"""MCP server (stdio) for agentic multiverse analyses of empirical papers.

    ama-mcp                      # or: python -m ama_mcp.server

Register it with Claude Code (``claude mcp add ama -- ama-mcp``) or with Codex
(``[mcp_servers.ama]`` in ``~/.codex/config.toml``). A call to ``analyze_paper`` scaffolds a
run directory and starts the orchestrator as a detached background process, so the analysis
keeps running after the calling session ends. The other tools read that directory.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import __version__, core_dir, runs
from .orchestrator import STAGES, STEPS

INSTRUCTIONS = """\
Agentic multiverse analysis (AMA) of one empirical paper. Given the paper's PDF, the tool
finds or rebuilds its replication package, reproduces the main estimate, has independent
agents list and audit the paper's analysis choices, runs the audited grid of alternative
specifications, and reports how much of that grid keeps the paper's directional claim.

How to use it:
1. check_backends: confirm the Claude Code or Codex CLI is installed and logged in.
2. analyze_paper(paper_pdf=..., backend="claude" or "codex"): starts the analysis in the
   background and returns a run_id. A run takes hours to more than a day.
3. run_status(run): progress, the current stage and the last log lines. Poll it sparingly.
4. run_results(run): when the status is completed or stopped, the headline (share supported
   at p < 0.10 first), the strict survival rate (p < 0.05), all measures and an excerpt of
   the report. read_run_file reads any other text file.
stop_run and resume_run stop a run and continue it from where it stopped.

Agent sessions run code from replication packages with broad permissions. Run this server
inside a container or virtual machine.
"""

EXPECT = (
    "The analysis runs in the background. The orchestrator launches each role as a fresh {backend} agent "
    "session (typically 15 to 30 sessions per paper), runs the gate scripts between them, and may stop early "
    "when a gate decides the paper cannot be rebuilt, reproduced or audited; a stop is a recorded result, "
    "not a crash. Expect several hours to more than a day of wall-clock time, mostly in the data build, the "
    "reproduction and the specification grid, and heavy model use (tens of millions of tokens or more, most "
    "of it cached context). Check progress with run_status every so often; when it reports 'completed' or "
    "'stopped', run_results gives the measures and the report."
)

DOCS = ("README.md", "PIPELINE.md", "gates.md", "rubric.md", "env.md", "data_sources_TEMPLATE.md")


def _fail(e: BaseException) -> ToolError:
    return ToolError(str(e) or type(e).__name__)


def _resolve(run: str) -> Path:
    try:
        return runs.resolve_run(run)
    except (ValueError, FileNotFoundError) as e:
        raise _fail(e) from None


def _reader(path: Path):
    def read() -> str:
        return path.read_text(encoding="utf-8")
    return read


def build_server() -> MCPServer:
    mcp = MCPServer(name="ama", title="Agentic multiverse analysis", instructions=INSTRUCTIONS,
                    version=__version__)

    # ------------------------------------------------------------------------ tools
    @mcp.tool(description="Report whether the Claude Code (claude) and Codex (codex) CLIs are installed and "
                          "logged in. Never returns credentials.")
    async def check_backends(
        probe: Annotated[bool, Field(description="allow one tiny model call when login cannot be read otherwise")] = False,
    ) -> dict[str, Any]:
        from .runners import check_backends as _check
        return await anyio.to_thread.run_sync(lambda: _check(probe=probe))

    @mcp.tool(description="Start an agentic multiverse analysis of one paper in the background. Returns the "
                          "run_id to pass to run_status / run_results. Takes hours to more than a day.")
    async def analyze_paper(
        paper_pdf: Annotated[str, Field(description="path to the paper's PDF on this machine")],
        backend: Annotated[str, Field(description="'claude' (Claude Code CLI) or 'codex' (Codex CLI)")] = "claude",
        citation: Annotated[str | None, Field(description="authors (year), title, journal: helps the agents identify "
                                                          "the paper")] = None,
        package_dir: Annotated[str | None, Field(description="the authors' replication package (directory or "
                                                             "archive), if you have it")] = None,
        data_sources_file: Annotated[str | None, Field(description="a text/markdown file describing the data you can "
                                                                   "access (databases, local copies, credentials "
                                                                   "setup); see the data_sources_TEMPLATE resource")] = None,
        headline_anchor_file: Annotated[str | None, Field(description="optional JSON fixing which outcomes count as the "
                                                                      "headline: {'ia_outcomes': [{'outcome', "
                                                                      "'location'}]}")] = None,
        g0_tol: Annotated[float, Field(description="reproduction gate tolerance (the study used 0.10)")] = 0.10,
        grid_workers: Annotated[int, Field(description="parallel worker processes for the specification grid")] = 4,
        grid_mem_gb: Annotated[int, Field(description="total memory budget for the grid, in GB")] = 16,
        forbidden_sources: Annotated[list[str] | None, Field(description="sources no agent may read (e.g. a "
                                                                         "published comment on the paper)")] = None,
        analysis_python: Annotated[str | None, Field(description="Python interpreter the agents use for analysis "
                                                                 "code (default: this server's)")] = None,
        models: Annotated[dict[str, str] | None, Field(description="per-step model override, e.g. {'4': 'opus'}; "
                                                                   "'*' sets every step")] = None,
        stop_after: Annotated[str | None, Field(description="pause after 'G2' (the audited fork space) or after a "
                                                            "stage: " + ", ".join(STAGES[:-1]))] = None,
        data_dirs: Annotated[list[str] | None, Field(description="directories holding data the agents may read")] = None,
        supplements: Annotated[list[str] | None, Field(description="internet appendix or other supplementary files")] = None,
        run_id: Annotated[str | None, Field(description="name for the run directory (default: from the PDF name)")] = None,
        effort: Annotated[str | None, Field(description="reasoning effort of every agent session: claude low, medium, "
                                                        "high, xhigh or max; codex minimal to ultra (model-dependent). "
                                                        "Default: the CLI's own setting. Codex's default is often "
                                                        "low; use 'high' or above for a full analysis")] = None,
    ) -> dict[str, Any]:
        backend = (backend or "claude").strip().lower()
        if backend == "fake" and os.environ.get("AMA_MCP_ALLOW_FAKE") != "1":
            raise ToolError("backend must be 'claude' or 'codex'")
        if backend not in ("claude", "codex", "fake"):
            raise ToolError("backend must be 'claude' or 'codex'")
        if backend != "fake":
            from .runners import check_backends as _check
            info = (await anyio.to_thread.run_sync(_check)).get(backend) or {}
            if not info.get("installed"):
                raise ToolError(f"the {backend} CLI is not installed or not on PATH: {info.get('detail', '')}")
            if info.get("logged_in") is False:
                raise ToolError(f"the {backend} CLI is not logged in: {info.get('detail', '')}")

        def create_and_start() -> tuple[Path, dict[str, Any]]:
            run = runs.create_run(
                paper_pdf, citation=citation, package_dir=package_dir, data_sources_file=data_sources_file,
                headline_anchor_file=headline_anchor_file, g0_tol=g0_tol, grid_workers=grid_workers,
                grid_mem_gb=grid_mem_gb, forbidden_sources=forbidden_sources or (),
                analysis_python=analysis_python or sys.executable, backend=backend, models=models,
                stop_after=stop_after, data_dirs=data_dirs or (), supplements=supplements or (), run_id=run_id,
                effort=effort)
            return run, runs.start_background(run)

        try:
            run, proc = await anyio.to_thread.run_sync(create_and_start)
        except (ValueError, FileNotFoundError, FileExistsError, RuntimeError, OSError) as e:
            raise _fail(e) from None
        return {"run_id": run.name, "run_dir": str(run), "pid": proc["pid"], "backend": backend,
                "status": "started", "log": proc["log"],
                "what_to_expect": EXPECT.format(backend={"claude": "Claude Code", "codex": "Codex"}.get(backend, backend)),
                "next": f"run_status(run='{run.name}')"}

    @mcp.tool(description="Progress of a run: status (created, running, paused, completed, stopped, error, "
                          "interrupted), current stage, gate outcomes so far and the last log lines.")
    async def run_status(run: Annotated[str, Field(description="run_id or run directory")]) -> dict[str, Any]:
        return runs.status(_resolve(run))

    @mcp.tool(description="Results of a run: the headline (share supported first: the measures of the finance "
                          "paper), the strict survival rate (p < 0.05), ama_result.json with every measure, "
                          "the report path and an excerpt of the report.")
    async def run_results(
        run: Annotated[str, Field(description="run_id or run directory")],
        excerpt_chars: Annotated[int, Field(description="characters of report.md to include")] = 4000,
    ) -> dict[str, Any]:
        rd = _resolve(run)
        return await anyio.to_thread.run_sync(lambda: runs.results(rd, excerpt_chars=max(0, int(excerpt_chars))))

    @mcp.tool(description="List the runs under the runs root ($AMA_RUNS_ROOT or ~/ama-runs), newest first.")
    async def list_runs() -> dict[str, Any]:
        root = runs.default_runs_root()
        return {"runs_root": str(root), "runs": runs.list_runs(root)}

    @mcp.tool(description="Stop a running analysis. The run can be continued later with resume_run.")
    async def stop_run(run: Annotated[str, Field(description="run_id or run directory")]) -> dict[str, Any]:
        rd = _resolve(run)
        return await anyio.to_thread.run_sync(lambda: runs.stop(rd))

    @mcp.tool(description="Continue a run that was stopped, interrupted, paused or hit an error, from where it "
                          "left off. A run that a gate stopped stays stopped unless from_stage is given.")
    async def resume_run(
        run: Annotated[str, Field(description="run_id or run directory")],
        stop_after: Annotated[str | None, Field(description="change the pause point: 'none', 'G2' or a stage")] = None,
        from_stage: Annotated[str | None, Field(description="run this stage and all later ones again with fresh "
                                                            "agents: " + ", ".join(STAGES))] = None,
        effort: Annotated[str | None, Field(description="change the reasoning effort for the remaining agent "
                                                        "sessions ('default' to clear)")] = None,
    ) -> dict[str, Any]:
        rd = _resolve(run)
        if from_stage is not None and from_stage not in STAGES:
            raise ToolError(f"from_stage must be one of {', '.join(STAGES)}")
        try:
            if stop_after is not None:
                runs.set_options(rd, stop_after=stop_after)
            if effort is not None:
                runs.set_options(rd, effort=effort)
            proc = runs.start_background(rd, from_stage=from_stage)
        except (ValueError, RuntimeError, OSError) as e:
            raise _fail(e) from None
        return {"run_id": rd.name, "pid": proc["pid"], "status": "started", "next": f"run_status(run='{rd.name}')"}

    @mcp.tool(description="Read a text file inside a run directory (or list a directory), size-capped. The "
                          "held-out benchmark/ directory is never readable.")
    async def read_run_file(
        run: Annotated[str, Field(description="run_id or run directory")],
        path: Annotated[str, Field(description="path relative to the run directory, e.g. 'report.md', "
                                               "'reproduction.md', 'audit.json', '.' for a listing")] = ".",
        max_bytes: Annotated[int, Field(description="maximum bytes to return (cap 1,000,000)")] = 100_000,
    ) -> dict[str, Any]:
        rd = _resolve(run)
        try:
            return runs.read_run_file(rd, path, max_bytes=max_bytes)
        except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
            raise _fail(e) from None

    # -------------------------------------------------------------------- resources
    core = core_dir()
    for name in DOCS:
        p = core / name
        if p.is_file():
            mcp.resource(f"ama://docs/{name}", name=f"doc_{Path(name).stem}", title=name,
                         description=f"AMA pipeline document: {name}", mime_type="text/markdown")(_reader(p))
    pdir = core / "prompts"
    if pdir.is_dir():
        for p in sorted(pdir.glob("step*.md")):
            mcp.resource(f"ama://prompts/{p.name}", name=f"prompt_{p.stem}", title=p.name,
                         description="role prompt template ({{PLACEHOLDER}} fields are filled per run by "
                                     "render_prompt.py)", mime_type="text/markdown")(_reader(p))

    # ---------------------------------------------------------------------- prompts
    @mcp.prompt(name="run_analysis", title="Run an agentic multiverse analysis",
                description="Step-by-step instructions for analysing one paper with this server.")
    def run_analysis(paper_pdf: str = "", backend: str = "claude") -> str:
        target = f"the paper at {paper_pdf}" if paper_pdf else "a paper (ask me for the path to its PDF)"
        return (
            f"Run an agentic multiverse analysis of {target} with the {backend} backend.\n\n"
            "1. Call check_backends and confirm that the chosen CLI is installed and logged in. If it is not, "
            "tell me what to fix and stop.\n"
            "2. Ask me, once, for anything optional I may have: a citation for the paper, the authors' "
            "replication package, a file describing the data I can access (the data_sources_TEMPLATE "
            "resource shows the format), and sources the agents must not read. Do not insist; all are optional.\n"
            "3. Call analyze_paper with what I gave you. Tell me the run_id, that the analysis runs in the "
            "background for hours to more than a day, and that the agent sessions execute code with broad "
            "permissions, so this should run in a container or VM.\n"
            "4. When I ask for progress, call run_status and summarise it in plain words: the stage, the gates "
            "passed so far, and whether anything stopped.\n"
            "5. When the status is completed or stopped, call run_results. Report the counts first: whether the "
            "main estimate was reproduced, how many specifications the audited grid has, and the share that keep "
            "the paper's directional claim. Then quote the report's own summary. Do not editorialise beyond "
            "the numbers; a stop at a gate is a result, explain which gate and why.\n"
            f"Pipeline steps for reference: {', '.join(STEPS)}."
        )

    return mcp


USAGE = """\
usage: ama-mcp [--help] [--version]

ama-mcp is an MCP server that speaks over stdin/stdout. It is started by an MCP client, not
run by hand. Register it once:

  Claude Code:  claude mcp add --scope user ama -e AMA_RUNS_ROOT="$HOME/ama-runs" -- {exe}
  Codex:        add to ~/.codex/config.toml
                  [mcp_servers.ama]
                  command = "{exe}"
                  env = {{ AMA_RUNS_ROOT = "/path/to/ama-runs" }}

Then ask the agent to use the ama tools (check_backends, analyze_paper, run_status, ...).
The same analyses can be run without MCP with the `ama` command (`ama --help`).
Agent sessions run code with broad permissions: run this inside a container or VM.
"""


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    if any(a in ("-h", "--help") for a in args):
        exe = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else Path("ama-mcp")
        print(USAGE.format(exe=exe if exe.name == "ama-mcp" else "ama-mcp"))
        return
    if any(a in ("-V", "--version") for a in args):
        print(f"ama-mcp {__version__}")
        return
    if args:
        print(f"ama-mcp: unknown arguments {' '.join(args)!r}; see `ama-mcp --help`", file=sys.stderr)
        raise SystemExit(2)
    if sys.stdin.isatty():
        print("ama-mcp: MCP server waiting for a client on stdin (Ctrl-C to quit; `ama-mcp --help` "
              "shows how to register it)", file=sys.stderr)
    build_server().run("stdio")


if __name__ == "__main__":
    main()
