"""Start the MCP server over stdio with the MCP client and exercise its surface."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import anyio
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mcp import ClientSession, StdioServerParameters, stdio_client  # noqa: E402

import ama_mcp  # noqa: E402
from ama_mcp import runs  # noqa: E402

TOOLS = {"check_backends", "analyze_paper", "run_status", "run_results", "list_runs", "stop_run",
         "resume_run", "read_run_file"}


def _core() -> Path | None:
    env = os.environ.get("AMA_TEST_CORE_DIR")
    core = Path(env).resolve() if env else Path(ama_mcp.__file__).resolve().parent / "core"
    ok = (core / "scripts" / "new_run.py").exists() and (core / "prompts").is_dir()
    return core if ok else None


def _params(tmp_path: Path, *, allow_fake: bool = True) -> StdioServerParameters:
    env = {"PYTHONPATH": str(SRC), "AMA_RUNS_ROOT": str(tmp_path / "runs"), "PYTHONDONTWRITEBYTECODE": "1"}
    if allow_fake:
        env["AMA_MCP_ALLOW_FAKE"] = "1"
    core = _core()
    if core is not None and os.environ.get("AMA_TEST_CORE_DIR"):
        env["AMA_CORE_DIR"] = str(core)
    return StdioServerParameters(command=sys.executable, args=["-m", "ama_mcp.server"], env=env, cwd=str(tmp_path))


def _payload(result) -> dict:
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


async def _session(tmp_path, fn, **kw):
    async with stdio_client(_params(tmp_path, **kw)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await fn(session)


def test_lists_tools_resources_and_prompt(tmp_path):
    async def body(s):
        tools = {t.name for t in (await s.list_tools()).tools}
        resources = {str(r.uri) for r in (await s.list_resources()).resources}
        prompts = {p.name for p in (await s.list_prompts()).prompts}
        prompt = await s.get_prompt("run_analysis", {"paper_pdf": "paper.pdf", "backend": "codex"})
        listing = _payload(await s.call_tool("list_runs", {}))
        doc = None
        if "ama://docs/PIPELINE.md" in resources:
            doc = (await s.read_resource("ama://docs/PIPELINE.md")).contents[0].text
        return tools, resources, prompts, prompt, listing, doc

    tools, resources, prompts, prompt, listing, doc = anyio.run(_session, tmp_path, body)
    assert tools == TOOLS
    assert prompts == {"run_analysis"}
    assert "codex" in prompt.messages[0].content.text
    assert listing["runs"] == [] and listing["runs_root"] == str((tmp_path / "runs").resolve())
    if _core() is not None:
        assert "ama://docs/PIPELINE.md" in resources
        assert "ama://prompts/step0_reproducer.md" in resources
        assert sum(u.startswith("ama://prompts/") for u in resources) == 18
        assert doc and "orchestrat" in doc.lower()


def test_read_run_file_and_status_through_the_server(tmp_path):
    core = _core()
    if core is None:
        pytest.skip("core scripts not available")
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    run = runs.create_run(pdf, runs_root=tmp_path / "runs", run_id="r1", backend="claude", core_dir=core)
    (run / "report.md").write_text("# Report\n\nhello\n")

    async def body(s):
        ok = _payload(await s.call_tool("read_run_file", {"run": "r1", "path": "report.md"}))
        held = await s.call_tool("read_run_file", {"run": "r1", "path": "benchmark/README.md"})
        escape = await s.call_tool("read_run_file", {"run": "r1", "path": "../../x"})
        st = _payload(await s.call_tool("run_status", {"run": "r1"}))
        res = _payload(await s.call_tool("run_results", {"run": "r1"}))
        missing = await s.call_tool("run_status", {"run": "nope"})
        stopped = _payload(await s.call_tool("stop_run", {"run": "r1"}))
        return ok, held, escape, st, res, missing, stopped

    ok, held, escape, st, res, missing, stopped = anyio.run(_session, tmp_path, body)
    assert ok["content"].startswith("# Report")
    assert held.is_error and "benchmark" in held.content[0].text
    assert escape.is_error
    assert st["status"] == "created" and st["run_id"] == "r1"
    assert res["report_excerpt"].startswith("# Report")
    assert missing.is_error
    assert stopped["stopped"] is False


def test_fake_backend_is_refused_unless_allowed(tmp_path):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    async def body(s):
        return await s.call_tool("analyze_paper", {"paper_pdf": str(pdf), "backend": "fake"})

    out = anyio.run(lambda: _session(tmp_path, body, allow_fake=False))
    assert out.is_error and "claude" in out.content[0].text
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_analyze_paper_starts_a_background_run(tmp_path):
    if _core() is None:
        pytest.skip("core scripts not available")
    pdf = tmp_path / "Some Paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    async def body(s):
        return _payload(await s.call_tool("analyze_paper", {"paper_pdf": str(pdf), "backend": "fake",
                                                            "run_id": "bg1", "g0_tol": 0.1}))

    started = anyio.run(_session, tmp_path, body)
    assert started["run_id"] == "bg1" and started["pid"] > 0
    assert "background" in started["what_to_expect"]
    run = Path(started["run_dir"])
    # the fake backend's default agent writes nothing, so the run ends at the first role's guard;
    # what matters here is that the detached orchestrator started, rendered, launched and recorded
    deadline = time.time() + 120
    st = runs.status(run)
    while time.time() < deadline and st["status"] in ("created", "running"):
        time.sleep(1)
        st = runs.status(run)
    assert st["status"] == "error", st
    assert "package_status.json" in (st["error"] or "")
    assert (run / "prompts_rendered" / "stepP.md").exists()
    assert len(list((run / "agent_logs").glob("*.log"))) == 2
    assert json.loads((run / "run_policy.json").read_text())["g0_tol"] == 0.1


def test_server_help_and_version(tmp_path):
    import subprocess
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "ama_mcp.server", *a], capture_output=True, text=True,
                                    env=env, timeout=60, stdin=subprocess.PIPE)
    h = run("--help")
    assert h.returncode == 0 and "claude mcp add" in h.stdout and "[mcp_servers.ama]" in h.stdout
    v = run("--version")
    assert v.returncode == 0 and v.stdout.strip() == f"ama-mcp {ama_mcp.__version__}"
    bad = run("--hepl")
    assert bad.returncode == 2 and "ama-mcp --help" in bad.stderr


def test_cli_foreground_reports_how_the_run_ended(tmp_path):
    import subprocess
    if _core() is None:
        pytest.skip("core scripts not available")
    pdf = tmp_path / "invented_paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% an invented paper\n")
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1", "AMA_MCP_ALLOW_FAKE": "1"}
    if os.environ.get("AMA_TEST_CORE_DIR"):
        env["AMA_CORE_DIR"] = str(_core())
    p = subprocess.run([sys.executable, "-m", "ama_mcp.cli", "--runs-root", str(tmp_path / "runs"), "run", str(pdf),
                        "--backend", "fake", "--run-id", "cli_fg", "--foreground"],
                       capture_output=True, text=True, env=env, timeout=600)
    # the fake backend's default agent writes nothing: the package finder fails twice
    assert p.returncode == 1
    assert "cli_fg: error" in p.stdout and "produced no package_status.json twice" in p.stdout
    assert "ama resume cli_fg" in p.stdout
