"""Command line for agentic multiverse analyses.

    ama run PAPER.pdf --backend claude|codex [--effort LEVEL] [options]
                                                         start a run (in the background)
    ama status [RUN]                                     one run, or every run
    ama results RUN                                      measures, survival headline, report excerpt
    ama stop RUN                                         stop a running analysis
    ama resume RUN [--from-stage STAGE] [--effort LEVEL] continue a stopped / failed / paused run
    ama doctor                                           check the installation and the agent CLIs
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from . import __version__, core_dir, runs
from .orchestrator import STAGES, STEPS


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _kv(items: list[str], what: str) -> dict[str, str]:
    out = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"{what}: expected KEY=VALUE, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _resolve(args) -> Path:
    try:
        return runs.resolve_run(args.run, args.runs_root)
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(str(e)) from None


# ------------------------------------------------------------------------- commands

def cmd_run(args) -> int:
    backend = args.backend
    if backend == "fake" and os.environ.get("AMA_MCP_ALLOW_FAKE") != "1":
        raise SystemExit("backend must be claude or codex")
    if backend != "fake" and not args.no_check:
        from .runners import check_backends
        info = check_backends().get(backend) or {}
        if not info.get("installed"):
            raise SystemExit(f"the {backend} CLI is not installed or not on PATH ({info.get('detail', '')}); "
                             "see `ama doctor`")
        if info.get("logged_in") is False:
            raise SystemExit(f"the {backend} CLI is not logged in ({info.get('detail', '')}); see `ama doctor`")
    drs = []
    for item in args.data_role_source:
        cite, _, paths = item.partition("=")
        if not cite or not paths:
            raise SystemExit("--data-role-source: expected 'CITATION=PATH[,PATH...]'")
        drs.append({"cite": cite.strip(), "paths": [p.strip() for p in paths.split(",") if p.strip()]})
    try:
        run = runs.create_run(
            args.paper_pdf, runs_root=args.runs_root, run_id=args.run_id, citation=args.citation,
            package_dir=args.package_dir, data_sources_file=args.data_sources_file,
            headline_anchor_file=args.headline_anchor_file, g0_tol=args.g0_tol, grid_workers=args.grid_workers,
            grid_mem_gb=args.grid_mem_gb, forbidden_sources=args.forbidden_sources, data_role_sources=drs,
            analysis_python=args.analysis_python, backend=backend, models=_kv(args.model, "--model"),
            stop_after=args.stop_after, data_dirs=args.data_dirs, supplements=args.supplements,
            role_timeout_s=args.role_timeout, effort=args.effort)
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError) as e:
        raise SystemExit(str(e)) from None
    print(f"run {run.name}\n  directory {run}")
    if args.foreground:
        return _foreground(run)
    info = runs.start_background(run)
    print(f"  started in the background (pid {info['pid']}); log {info['log']}")
    print("  this takes hours to more than a day; check with:  ama status " + run.name)
    return 0


def _foreground(run: Path, from_stage: str | None = None) -> int:
    """Run attached to this terminal, then say how the run ended (the orchestrator prints its
    log lines only when stderr is a terminal)."""
    code = runs.run_foreground(run, from_stage=from_stage)
    s = runs.status(run, log_lines=0)
    line = f"{s['run_id']}: {s['status']}"
    if s.get("stopped_at") and s["stopped_at"] != "error":
        line += f" at {s['stopped_at']}: {str(s.get('reason') or '')[-400:]}"
    elif s.get("record", {}).get("paused"):
        line += f" ({s['record']['paused']}); continue with: ama resume {s['run_id']}"
    if s.get("error") and s["status"] in ("error", "interrupted"):
        line += f"\n  error: {s['error']}\n  log: {run / 'orchestrator.log'}\n  continue with: ama resume {s['run_id']}"
    elif s["status"] in ("completed", "stopped"):
        line += f"\n  results: ama results {s['run_id']}"
    print(line)
    return code


def _print_status(s: dict[str, Any]) -> None:
    head = f"{s['run_id']}: {s['status']}"
    if s.get("pid"):
        head += f" (pid {s['pid']})"
    print(head)
    done = s.get("completed_stages") or []
    print(f"  stages done: {len(done)}/{len(STAGES)}" + (f" ({', '.join(done)})" if done else ""))
    if s.get("stage") and s["status"] not in ("completed",):
        print(f"  current stage: {s['stage']}")
    line = _headline_line(s["headline"]) if s.get("headline") else ""
    if line:
        print("  headline: " + line)
    if s.get("current_session"):
        cs = s["current_session"]
        print(f"  agent session in progress: step {cs.get('step')}{cs.get('sfx', '')} since {cs.get('since')}")
    elif s.get("last_session"):
        ls = s["last_session"]
        print(f"  last agent session: step {ls.get('step')}{ls.get('sfx', '')} ended {ls.get('ended_at')} "
              f"exit {ls.get('exit_code')}" + (f" ({ls['error']})" if ls.get("error") else ""))
    for k, v in (s.get("record") or {}).items():
        print(f"  {k}: {v}")
    for k, v in (s.get("gates") or {}).items():
        print(f"  gate {k}: {v}")
    if s.get("stopped_at") and s["stopped_at"] != "error":
        print(f"  stopped at {s['stopped_at']}: {str(s.get('reason') or '')[-400:]}")
    if s.get("error") and s["status"] in ("error", "interrupted"):
        print(f"  error: {s['error']}  (continue with: ama resume {s['run_id']})")
    if s.get("log_tail"):
        print("  log:")
        for line in s["log_tail"]:
            print("    " + line)


_HEADLINE_LABELS = (
    ("share_supported", "share supported (published sign, p < 0.10)"),
    ("median_signed_z", "median signed z"),
    ("share_published_sign", "share with the published sign"),
    ("reproduced", "reproduced"),
    ("n_specifications", "specifications"),
    ("companion_share_supported", "companion share supported"),
    ("claim_type", "claim type"),
    ("reproduced_null_standard", "reproduced (null claim)"),
    ("strict_survival_rate_p05", "strict survival (p < 0.05)"),
)


def _shown(h: dict[str, Any], k: str) -> bool:
    """A headline value worth printing: not None, and the null-claim reproduction only for a null claim."""
    return h.get(k) is not None and (k != "reproduced_null_standard" or h.get("claim_type") == "null")


def _headline_line(h: dict[str, Any]) -> str:
    if h.get("measures_error"):
        head = f"measures unavailable ({h['measures_error']})"
        parts = [head] + [f"{lab} {_fmt(h[k])}" for k, lab in _HEADLINE_LABELS[-1:] if _shown(h, k)]
        return "; ".join(parts)
    return "; ".join(f"{lab} {_fmt(h[k])}" for k, lab in _HEADLINE_LABELS if _shown(h, k))


def cmd_status(args) -> int:
    if not args.run:
        return cmd_list(args)
    s = runs.status(_resolve(args))
    _print_json(s) if args.json else _print_status(s)
    return 0


def cmd_list(args) -> int:
    rows = runs.list_runs(args.runs_root)
    if getattr(args, "json", False):
        _print_json(rows)
        return 0
    if not rows:
        print(f"no runs under {runs.default_runs_root() if not args.runs_root else args.runs_root}")
        return 0
    for r in rows:
        stopped = r.get("stopped_at") if r.get("stopped_at") not in (None, "error") else None
        extra = f" at {stopped}" if stopped else (f" ({r['stage']})" if r.get("stage") else "")
        print(f"{r['run_id']:50} {r['status']}{extra}  {r.get('updated_at') or ''}")
    return 0


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def cmd_results(args) -> int:
    res = runs.results(_resolve(args), excerpt_chars=args.excerpt)
    if args.json:
        _print_json(res)
        return 0
    print(f"{res['run_id']}: {res['status']}")
    r = res.get("result") or {}
    if not res.get("result") and res["status"] not in ("completed", "stopped"):
        print("  no results yet (the run has not finished); measures below use whatever files exist")
    if r.get("stopped_at"):
        print(f"  stopped at {r['stopped_at']}: {str(r.get('reason') or '')[-400:]}")
    hl = res.get("headline") or {}
    if hl:
        print("  headline (the finance paper's measures, audited grid):")
        if hl.get("measures_error"):
            print(f"    measures unavailable: {hl['measures_error']}")
        for k, lab in _HEADLINE_LABELS[:-1]:
            if k in hl and (k != "reproduced_null_standard" or hl.get("claim_type") == "null"):
                print(f"    {lab}: {_fmt(hl[k])}")
    head = res.get("survival_headline")
    if isinstance(head, dict):
        print("  strict survival (survival.py: published sign and p < 0.05; secondary):")
        for k, v in head.items():
            print(f"    {k}: {_fmt(v)}")
    comp = res.get("companion_survival_headline")
    if isinstance(comp, dict):
        print("  companion grid (paper's held-fixed choices restored):")
        for k in ("n_cells_in_denominator", "sign_consistency_rate", "survival_rate"):
            if k in comp:
                print(f"    {k}: {_fmt(comp[k])}")
    m = res.get("metrics")
    if isinstance(m, dict) and m:
        print("  measures" + ("" if args.all else " (missing ones hidden; --all shows them)") + ":")
        for k, v in m.items():
            val = v.get("value") if isinstance(v, dict) and "value" in v else v
            if (val is None and not args.all) or k in hl:
                continue
            d = (v.get("definition") or v.get("description") or "") if isinstance(v, dict) else ""
            print(f"    {k}: {_fmt(val)}" + (f"  -- {d}" if d and args.definitions else ""))
    if res.get("report_path"):
        print(f"  report: {res['report_path']}")
    if res.get("report_excerpt") and args.excerpt:
        print("\n" + res["report_excerpt"])
    return 0


def cmd_stop(args) -> int:
    out = runs.stop(_resolve(args))
    if out.get("stopped"):
        print(f"stopped (pid {out['pid']}{', forced' if out.get('forced') else ''}); continue with: ama resume {args.run}")
        return 0
    print(out.get("reason", "not stopped"))
    return 1


def cmd_resume(args) -> int:
    run = _resolve(args)
    try:
        if args.stop_after is not None:
            runs.set_options(run, stop_after=args.stop_after)
        if args.effort is not None:
            runs.set_options(run, effort=args.effort)
        if args.foreground:
            return _foreground(run, from_stage=args.from_stage)
        info = runs.start_background(run, from_stage=args.from_stage)
    except (ValueError, RuntimeError) as e:
        raise SystemExit(str(e)) from None
    print(f"resumed {run.name} in the background (pid {info['pid']}); log {info['log']}")
    return 0


def _in_container() -> str:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return "yes"
    try:
        cg = Path("/proc/1/cgroup").read_text()
        if any(k in cg for k in ("docker", "containerd", "kubepods", "podman", "lxc")):
            return "yes"
    except OSError:
        pass
    return "not detected"


def cmd_doctor(args) -> int:
    ok = True
    print(f"ama-mcp {__version__} on Python {platform.python_version()} ({sys.executable})")
    if os.name != "posix":
        print("  ! the orchestrator needs a POSIX system (Linux or macOS)")
        ok = False
    core = core_dir()
    prompts = sorted((core / "prompts").glob("step*.md")) if (core / "prompts").is_dir() else []
    scripts = [n for n in ("render_prompt.py", "new_run.py", "gate_g0.py", "gate_g0a.py", "gate_gp.py",
                           "gate_gs.py", "gate_gc.py", "gate_g2.py", "gate_g4.py", "gate_g6.py", "freeze.py",
                           "survival.py") if (core / "scripts" / n).is_file()]
    print(f"core: {len(prompts)} role prompts, {len(scripts)}/12 scripts in {core}")
    if len(prompts) < len(STEPS) or len(scripts) < 12:
        print("  ! the core directory is incomplete")
        ok = False
    for mod in ("yaml", "pandas", "numpy", "scipy", "statsmodels", "matplotlib", "mcp"):
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            print(f"  ! cannot import {mod}: {e}")
            ok = False
    from .runners import check_backends
    backends = check_backends(probe=args.probe)
    usable = []
    for name, info in backends.items():
        state = ("installed" if info.get("installed") else "not installed")
        if info.get("installed"):
            li = info.get("logged_in")
            state += ", " + ("logged in" if li else "NOT logged in" if li is False else "login not verified")
            if li is not False:
                usable.append(name)
        print(f"backend {name}: {state}" + (f" ({info.get('version')})" if info.get("version") else "")
              + (f" -- {info.get('detail')}" if info.get("detail") else ""))
    if not usable:
        print("  ! no usable backend: install and log in to Claude Code (`claude`) or Codex (`codex login`)")
        ok = False
    root = Path(args.runs_root).expanduser() if args.runs_root else runs.default_runs_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".ama_write_test"
        probe.write_text("ok")
        probe.unlink()
        print(f"runs root: {root} (writable)")
    except OSError as e:
        print(f"  ! runs root {root} is not writable: {e}")
        ok = False
    print(f"container: {_in_container()}  (agent sessions run code with broad permissions: use a container or VM)")
    print("OK" if ok else "problems found")
    return 0 if ok else 1


# ---------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ama", description="Agentic multiverse analysis of empirical papers.")
    ap.add_argument("--version", action="version", version=f"ama-mcp {__version__}")
    ap.add_argument("--runs-root", default=None, help="where runs live (default: $AMA_RUNS_ROOT or ~/ama-runs)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    backends = ["claude", "codex"] + (["fake"] if os.environ.get("AMA_MCP_ALLOW_FAKE") == "1" else [])
    r = sub.add_parser("run", help="start an analysis of one paper")
    r.add_argument("paper_pdf", help="the paper's PDF")
    r.add_argument("--backend", choices=backends, default="claude", help="agent CLI that runs every role")
    r.add_argument("--citation", help="authors (year), title, journal")
    r.add_argument("--package", dest="package_dir", help="the authors' replication package (directory or archive)")
    r.add_argument("--data-sources", dest="data_sources_file", help="file describing the data you can access")
    r.add_argument("--headline-anchor", dest="headline_anchor_file",
                   help="JSON fixing the eligible headline outcomes ({'ia_outcomes': [{'outcome', 'location'}]})")
    r.add_argument("--g0-tol", type=float, default=0.10, help="reproduction gate tolerance (study: 0.10)")
    r.add_argument("--grid-workers", type=int, default=4)
    r.add_argument("--grid-mem-gb", type=int, default=16)
    r.add_argument("--forbid", dest="forbidden_sources", action="append", default=[], metavar="SOURCE",
                   help="a source no agent may read (repeatable)")
    r.add_argument("--data-role-source", action="append", default=[], metavar="CITATION=PATH[,PATH]",
                   help="a third-party source only the data and reproduction roles may read (repeatable)")
    r.add_argument("--analysis-python", default=sys.executable, help="interpreter the agents use for analysis")
    r.add_argument("--model", action="append", default=[], metavar="STEP=MODEL",
                   help="model for one step (STEP '*' for all), repeatable; steps: " + " ".join(STEPS))
    r.add_argument("--stop-after", default=None, help="pause after G2 or after a stage")
    r.add_argument("--data-dir", dest="data_dirs", action="append", default=[], help="data directory agents may read")
    r.add_argument("--supplement", dest="supplements", action="append", default=[],
                   help="internet appendix or other supplementary file (repeatable)")
    r.add_argument("--run-id", help="name of the run directory")
    r.add_argument("--role-timeout", type=int, default=None, help="seconds per agent session (default: none)")
    r.add_argument("--effort", default=None, metavar="LEVEL",
                   help="reasoning effort of every agent session: claude low|medium|high|xhigh|max; codex "
                        "minimal|low|medium|high|xhigh|max|ultra (model-dependent). Default: the CLI's own setting. "
                        "Codex's default is often low; use high or above for a full analysis")
    r.add_argument("--foreground", action="store_true", help="run attached to this terminal")
    r.add_argument("--no-check", action="store_true", help="skip the backend login check")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="status of one run, or a list of all runs")
    s.add_argument("run", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    ls = sub.add_parser("list", help="list runs")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)

    res = sub.add_parser("results", help="results of a finished run")
    res.add_argument("run")
    res.add_argument("--json", action="store_true")
    res.add_argument("--excerpt", type=int, default=1500, help="characters of the report to print")
    res.add_argument("--all", action="store_true", help="also list measures without a value")
    res.add_argument("--definitions", action="store_true", help="print each measure's definition")
    res.set_defaults(func=cmd_results)

    st = sub.add_parser("stop", help="stop a running analysis")
    st.add_argument("run")
    st.set_defaults(func=cmd_stop)

    rs = sub.add_parser("resume", help="continue a run from where it stopped")
    rs.add_argument("run")
    rs.add_argument("--from-stage", choices=STAGES, default=None, help="run this and every later stage again")
    rs.add_argument("--stop-after", default=None, help="new pause point ('none' to clear)")
    rs.add_argument("--effort", default=None, metavar="LEVEL",
                    help="change the reasoning effort for the remaining agent sessions ('default' to clear)")
    rs.add_argument("--foreground", action="store_true")
    rs.set_defaults(func=cmd_resume)

    d = sub.add_parser("doctor", help="check the installation and the agent CLIs")
    d.add_argument("--probe", action="store_true", help="allow one tiny model call to verify a login")
    d.set_defaults(func=cmd_doctor)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
