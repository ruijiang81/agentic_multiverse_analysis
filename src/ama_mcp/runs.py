"""Create, inspect, start and stop AMA run directories.

A run directory holds everything about one paper: the inputs (``package/paper/`` and, when
supplied, ``package/authors_package/``), ``run_policy.json`` (read by the prompts and the
gates), ``ama_options.json`` (read by the orchestrator), every agent artifact, the agent
transcripts (``agent_logs/``), ``orchestrator_state.json`` / ``orchestrator.log`` and, at
the end, ``ama_result.json``.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import __version__
from . import core_dir as _default_core_dir
from .orchestrator import (AGENT_LOGS, DEFAULT_OPTIONS, HEADLINE_MEASURES, LOCK_FILE, LOG_FILE, OPTIONS_FILE,
                           PAPER_MEASURES_FILE, PHASES, PID_FILE, RESULT_FILE, STAGES, STATE_FILE, STEPS, _clean,
                           now_iso, read_json, write_json_atomic)

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
BACKENDS = ("claude", "codex", "fake")
STDOUT_LOG = "orchestrator.stdout.log"
TEXT_LIMIT = 1_000_000


# ------------------------------------------------------------------------ locations

def default_runs_root() -> Path:
    """``$AMA_RUNS_ROOT``, else ``~/ama-runs``."""
    env = os.environ.get("AMA_RUNS_ROOT", "").strip()
    return Path(env).expanduser().resolve() if env else (Path.home() / "ama-runs").resolve()


def make_run_id(paper_pdf: str | Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", Path(paper_pdf).stem.lower()).strip("_")[:40] or "paper"
    return f"{slug}_{time.strftime('%Y%m%d_%H%M%S')}"


def resolve_run(run: str | Path, runs_root: str | Path | None = None) -> Path:
    """A run id (looked up under the runs root) or a path to a run directory."""
    s = str(run).strip()
    if not s:
        raise ValueError("no run given")
    p = Path(s).expanduser()
    if (os.sep in s or s.startswith((".", "~"))) and p.is_dir():
        p = p.resolve()
    else:
        if not RUN_ID_RE.match(s):
            raise ValueError(f"not a valid run id: {s!r}")
        root = Path(runs_root).expanduser().resolve() if runs_root else default_runs_root()
        p = (root / s).resolve()
        if p.parent != root:
            raise ValueError(f"not a valid run id: {s!r}")
    if not p.is_dir():
        raise FileNotFoundError(f"no run directory {p}")
    if not ((p / "run_policy.json").exists() or (p / STATE_FILE).exists()):
        raise FileNotFoundError(f"{p} is not an AMA run directory (no run_policy.json)")
    return p


# --------------------------------------------------------------------------- create

def _validate_models(models: Mapping[str, Any] | None) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for k, v in (models or {}).items():
        k = str(k).strip()
        if k.lower().startswith("step"):
            k = k[4:]
        if k not in STEPS and k != "*":
            raise ValueError(f"models: unknown step {k!r}; steps are {', '.join(STEPS)} (or '*' for all)")
        out[k] = (str(v).strip() or None) if v is not None else None
    return out


# Effort levels each CLI accepts (claude --effort; codex model_reasoning_effort, model-dependent).
EFFORTS = {"claude": ("low", "medium", "high", "xhigh", "max"),
           "codex": ("minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
           "fake": ("low", "medium", "high", "xhigh", "max")}


def _validate_effort(effort: str | None, backend: str) -> str | None:
    if effort in (None, "", "default"):
        return None
    e = str(effort).strip().lower()
    ok = EFFORTS.get(backend, ())
    if e not in ok:
        raise ValueError(f"effort for {backend} must be one of {', '.join(ok)} (or omit it for the CLI's default)")
    return e


def _runner_options(backend: str, effort: str | None) -> dict[str, Any]:
    """The runner keyword for an effort level: ClaudeRunner(effort=...), CodexRunner(reasoning_effort=...)."""
    if not effort or backend == "fake":
        return {}
    return {"reasoning_effort": effort} if backend == "codex" else {"effort": effort}


def _validate_stop_after(stop_after: str | None) -> str | None:
    if stop_after in (None, "", "none", "None"):
        return None
    s = str(stop_after)
    if s.upper() == "G2":
        return "G2"
    if s.lower() in STAGES and s.lower() != "report":
        return s.lower()
    raise ValueError(f"stop_after must be 'G2' or one of {', '.join(STAGES[:-1])}")


def _data_role_sources(srcs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in srcs or ():
        if not isinstance(s, Mapping) or not s.get("cite"):
            raise ValueError("each data_role_sources entry needs 'cite' (and 'paths')")
        paths = s.get("paths") or []
        if isinstance(paths, (str, Path)):
            paths = [paths]
        out.append({"cite": str(s["cite"]), "paths": [str(Path(p).expanduser().resolve()) for p in paths]})
    return out


def create_run(paper_pdf: str | Path, *, runs_root: str | Path | None = None, run_id: str | None = None,
               citation: str | None = None, package_dir: str | Path | None = None,
               data_sources_file: str | Path | None = None, headline_anchor_file: str | Path | None = None,
               g0_tol: float = 0.10, grid_workers: int = 4, grid_mem_gb: int = 16,
               forbidden_sources: Sequence[str] = (), data_role_sources: Sequence[Mapping[str, Any]] = (),
               analysis_python: str = sys.executable, backend: str = "claude",
               models: Mapping[str, Any] | None = None, stop_after: str | None = None,
               data_dirs: Sequence[str | Path] = (), supplements: Sequence[str | Path] = (),
               restricted_package: Mapping[str, Any] | None = None, repro_language: str = "Python",
               role_timeout_s: int | None = None, core_dir: str | Path | None = None,
               effort: str | None = None) -> Path:
    """Scaffold a run directory for one paper and return its path. Nothing is launched.

    ``effort`` sets the reasoning effort of every agent session (Claude Code ``--effort``; Codex
    ``model_reasoning_effort``); ``None`` leaves the CLI's own default."""
    pdf = Path(paper_pdf).expanduser().resolve()
    if not pdf.is_file():
        raise FileNotFoundError(f"paper PDF not found: {pdf}")
    with pdf.open("rb") as fh:
        if fh.read(5) != b"%PDF-":
            raise ValueError(f"{pdf.name} is not a PDF file")
    backend = str(backend).strip().lower()
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {', '.join(BACKENDS[:2])}")
    models_ok = _validate_models(models)
    effort = _validate_effort(effort, backend)
    stop_after = _validate_stop_after(stop_after)
    g0_tol = float(g0_tol)
    if not 0 < g0_tol < 1:
        raise ValueError("g0_tol must be between 0 and 1 (the study used 0.10)")
    grid_workers, grid_mem_gb = int(grid_workers), int(grid_mem_gb)
    if grid_workers < 1 or grid_mem_gb < 1:
        raise ValueError("grid_workers and grid_mem_gb must be at least 1")
    pkg_src = Path(package_dir).expanduser().resolve() if package_dir else None
    if pkg_src is not None and not pkg_src.exists():
        raise FileNotFoundError(f"replication package not found: {pkg_src}")
    ds_src = Path(data_sources_file).expanduser().resolve() if data_sources_file else None
    if ds_src is not None and not ds_src.is_file():
        raise FileNotFoundError(f"data sources file not found: {ds_src}")
    anchor = None
    if headline_anchor_file:
        anchor = read_json(Path(headline_anchor_file).expanduser())
        if not isinstance(anchor, dict) or not isinstance(anchor.get("ia_outcomes"), list) or not all(
                isinstance(o, dict) and o.get("outcome") and o.get("location") for o in anchor["ia_outcomes"]):
            raise ValueError("headline anchor must be JSON with 'ia_outcomes': [{'outcome', 'location'}, ...]")
    sups = [Path(s).expanduser().resolve() for s in supplements or ()]
    for s in sups:
        if not s.is_file():
            raise FileNotFoundError(f"supplement not found: {s}")
    drs = _data_role_sources(data_role_sources)
    if restricted_package and not (isinstance(restricted_package, Mapping) and restricted_package.get("path")):
        raise ValueError("restricted_package needs at least 'path'")

    root = Path(runs_root).expanduser().resolve() if runs_root else default_runs_root()
    run_id = run_id or make_run_id(pdf)
    if not RUN_ID_RE.match(run_id):
        raise ValueError(f"run_id {run_id!r}: use letters, digits, '.', '_' or '-' (max 100)")
    root.mkdir(parents=True, exist_ok=True)
    run = root / run_id
    if run.exists():
        raise FileExistsError(f"run directory already exists: {run}")

    core = Path(core_dir).expanduser().resolve() if core_dir else _default_core_dir()
    p = subprocess.run([sys.executable, str(core / "scripts" / "new_run.py"), run_id, "--root", str(root)],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0 or not run.is_dir():
        raise RuntimeError(f"new_run.py failed: {(p.stderr or p.stdout)[-500:]}")

    paper_dir = run / "package" / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)
    pdf_dst = paper_dir / pdf.name
    shutil.copy2(pdf, pdf_dst)

    local_materials: dict[str, Any] = {}
    if pkg_src is not None:
        dst = run / "package" / "authors_package"
        dst.mkdir(parents=True, exist_ok=True)
        if pkg_src.is_dir():
            shutil.copytree(pkg_src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(pkg_src, dst / pkg_src.name)
        entries = sorted(x.name for x in dst.iterdir())
        local_materials["package"] = {
            "path": str(dst),
            "source": "supplied by the person who started this run",
            "contents": ", ".join(entries[:12]) + (" ..." if len(entries) > 12 else ""),
            "license": "as stated in the package",
        }
    if sups:
        inp = run / "inputs"
        inp.mkdir(exist_ok=True)
        local_materials["supplements"] = []
        for s in sups:
            shutil.copy2(s, inp / s.name)
            local_materials["supplements"].append({"what": f"supplementary material ({s.name})",
                                                   "path": str(inp / s.name),
                                                   "source": "supplied by the person who started this run"})
    ds_path = None
    if ds_src is not None:
        ds_path = run / "data_sources.md"
        shutil.copy2(ds_src, ds_path)
    if anchor is not None:
        write_json_atomic(run / "headline_anchor.json", anchor)

    policy: dict[str, Any] = {
        "created_by": f"ama-mcp {__version__}",
        "g0_tol": g0_tol,
        "grid_workers": grid_workers,
        "grid_mem_gb": grid_mem_gb,
        "paper_pdf": str(pdf_dst),
        "forbidden_sources": [str(f) for f in forbidden_sources or ()],
        "data_role_sources": drs,
        "data_sources_file": str(ds_path) if ds_path else None,
        "analysis_python": str(analysis_python),
        "repro_language": str(repro_language or "Python"),
    }
    if restricted_package:
        rp = dict(restricted_package)
        rp["path"] = str(Path(str(rp["path"])).expanduser().resolve())
        rp.setdefault("allowed_roles", ["0", "G0A"])
        rp.setdefault("terms", "replication checking only")
        policy["restricted_package"] = rp
    if local_materials:
        policy["local_materials"] = local_materials
    write_json_atomic(run / "run_policy.json", policy)

    opts = copy.deepcopy(DEFAULT_OPTIONS)
    opts.update({
        "backend": backend, "models": models_ok, "stop_after": stop_after,
        "citation": str(citation).strip() if citation else None,
        "data_dirs": [str(Path(d).expanduser().resolve()) for d in data_dirs or ()],
        "role_timeout_s": int(role_timeout_s) if role_timeout_s else None,
        "effort": effort, "runner_options": _runner_options(backend, effort),
        "created_at": now_iso(), "ama_mcp_version": __version__,
    })
    write_json_atomic(run / OPTIONS_FILE, opts)
    (run / AGENT_LOGS).mkdir(exist_ok=True)
    return run


def set_options(run_dir: Path, **changes: Any) -> dict[str, Any]:
    """Change orchestrator options of an existing run (e.g. ``stop_after`` before a resume)."""
    path = Path(run_dir) / OPTIONS_FILE
    opts = read_json(path) or copy.deepcopy(DEFAULT_OPTIONS)
    if "stop_after" in changes:
        changes["stop_after"] = _validate_stop_after(changes["stop_after"])
    if "models" in changes:
        changes["models"] = _validate_models(changes["models"])
    if "effort" in changes:
        backend = str(opts.get("backend") or "claude")
        changes["effort"] = _validate_effort(changes["effort"], backend)
        ro = {k: v for k, v in (opts.get("runner_options") or {}).items() if k not in ("effort", "reasoning_effort")}
        ro.update(_runner_options(backend, changes["effort"]))
        changes["runner_options"] = ro
    opts.update(changes)
    write_json_atomic(path, opts)
    return opts


# --------------------------------------------------------------------------- process

def _cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    except OSError:
        pass
    try:  # no /proc (macOS)
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        return out.split() if out else []
    except (OSError, subprocess.SubprocessError):
        return []


def _pid_is_ours(pid: int, run_dir: Path) -> bool:
    """True iff ``pid`` is alive and is this run's orchestrator (its command line names the run dir)."""
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, PermissionError, ValueError, OverflowError):
        return False
    cmd = _cmdline(int(pid))
    joined = " ".join(cmd)
    return bool(cmd) and "ama_mcp.orchestrator" in joined and str(Path(run_dir).resolve()) in cmd


def _lock_held(run_dir: Path) -> bool:
    if fcntl is None:  # pragma: no cover
        return False
    f = Path(run_dir) / LOCK_FILE
    if not f.exists():
        return False
    try:
        with open(f, "a+") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            except OSError:
                return False
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return False
    except OSError:
        return False


def running_pid(run_dir: str | Path) -> int | None:
    """The live orchestrator's pid for this run, or None."""
    info = read_json(Path(run_dir) / PID_FILE) or {}
    pid = info.get("pid")
    if pid and _pid_is_ours(int(pid), Path(run_dir)):
        return int(pid)
    return None


def is_running(run_dir: str | Path) -> bool:
    return running_pid(run_dir) is not None or _lock_held(Path(run_dir))


def _pythonpath_env() -> dict[str, str]:
    env = os.environ.copy()
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = pkg_parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def orchestrator_command(run_dir: Path, *, from_stage: str | None = None, python: str | None = None) -> list[str]:
    cmd = [python or sys.executable, "-m", "ama_mcp.orchestrator", "run", "--run-dir", str(Path(run_dir).resolve())]
    if from_stage:
        cmd += ["--from-stage", from_stage]
    return cmd


def start_background(run_dir: str | Path, *, from_stage: str | None = None, python: str | None = None) -> dict[str, Any]:
    """Start (or resume) the orchestrator as a detached process group; returns its pid."""
    run = Path(run_dir).resolve()
    if is_running(run):
        raise RuntimeError(f"an orchestrator is already running for {run.name} (pid {running_pid(run)})")
    cmd = orchestrator_command(run, from_stage=from_stage, python=python)
    with open(run / STDOUT_LOG, "ab") as out:
        proc = subprocess.Popen(cmd, cwd=str(run), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True, env=_pythonpath_env())
    write_json_atomic(run / PID_FILE, {"pid": proc.pid, "pgid": proc.pid, "started_at": now_iso(),
                                       "cmd": "python -m ama_mcp.orchestrator run --run-dir <run>"})
    # reap the child when it ends so a long-lived caller (the MCP server) keeps no zombie
    threading.Thread(target=proc.wait, daemon=True).start()
    return {"pid": proc.pid, "log": str(run / LOG_FILE), "stdout": str(run / STDOUT_LOG)}


def run_foreground(run_dir: str | Path, *, from_stage: str | None = None, python: str | None = None) -> int:
    """Run the orchestrator attached to this terminal; returns its exit code."""
    run = Path(run_dir).resolve()
    if is_running(run):
        raise RuntimeError(f"an orchestrator is already running for {run.name} (pid {running_pid(run)})")
    proc = subprocess.Popen(orchestrator_command(run, from_stage=from_stage, python=python), cwd=str(run),
                            env=_pythonpath_env())
    try:
        return proc.wait()
    except KeyboardInterrupt:  # Ctrl-C reaches the child too; let it record its state
        try:
            return proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait()


def _descendants(pid: int) -> list[int]:
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="], capture_output=True, text=True,
                             timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    kids: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [pid]
    while stack:
        for c in kids.get(stack.pop(), []):
            if c not in found and c != pid:
                found.append(c)
                stack.append(c)
    return found


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_bytes().split(b")")[-1].split()[0] != b"Z"
    except OSError:
        return True


def stop(run_dir: str | Path, *, grace_s: float = 30.0) -> dict[str, Any]:
    """Stop this run's orchestrator. The pid is signalled only after its command line has been
    checked to be this run's orchestrator; its process group only when it leads its own group."""
    run = Path(run_dir).resolve()
    info = read_json(run / PID_FILE) or {}
    pid = info.get("pid")
    if not pid or not _pid_is_ours(int(pid), run):
        return {"stopped": False, "reason": "no orchestrator process of this run is alive"}
    pid = int(pid)
    kids = _descendants(pid)
    try:
        own_group = os.getpgid(pid) == pid
    except OSError:
        own_group = False
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if own_group:
            os.killpg(pid, signal.SIGTERM)  # the orchestrator records "interrupted" and ends its session
        else:
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.2)
    forced = False
    if _alive(pid):
        forced = True
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if own_group:
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
    if forced:  # the orchestrator could not end its agent session itself (they run in their own groups)
        for k in kids:
            if _alive(k):
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(k, signal.SIGKILL)
    st = read_json(run / STATE_FILE)
    if isinstance(st, dict) and st.get("status") == "running":
        st["status"] = "interrupted"
        st["error"] = "stopped by the user"
        st.setdefault("history", []).append({"at": now_iso(), "event": "interrupted", "detail": "stopped by the user"})
        write_json_atomic(run / STATE_FILE, st)
    with contextlib.suppress(OSError):
        with (run / LOG_FILE).open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()}  {run.name}: stopped by the user{' (forced)' if forced else ''}\n")
    return {"stopped": True, "pid": pid, "forced": forced}


# ---------------------------------------------------------------------------- status

def _tail_lines(path: Path, n: int) -> list[str]:
    if n <= 0:
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 64_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
        return lines[-n:]
    except OSError:
        return []


def status(run_dir: str | Path, *, log_lines: int = 15) -> dict[str, Any]:
    run = Path(run_dir).resolve()
    st = read_json(run / STATE_FILE) or {}
    pid = running_pid(run)
    running = pid is not None or _lock_held(run)
    state = st.get("status") or "created"
    if state == "running" and not running:
        state = "interrupted"  # the process ended without recording why (killed, machine restart)
    if running:
        state = "running"
    rec = st.get("rec") or {}
    roles = st.get("roles") or []
    done = list(st.get("completed_stages") or [])
    cur = st.get("current_stage")
    last = roles[-1] if roles else None
    keep = ("finder_status", "sample", "g0_path", "tallies", "companion", "companion_restored", "paused")
    out = {
        "run_id": run.name,
        "run_dir": str(run),
        "status": state,
        "running": running,
        "pid": pid,
        "stage": cur if state not in ("completed",) else None,
        "stage_title": PHASES.get(cur or "", None),
        "completed_stages": done,
        "progress": f"{len(done)}/{len(STAGES)} stages ({', '.join(PHASES[s] for s in STAGES)})",
        "agent_sessions": len(roles),
        "current_session": st.get("inflight"),
        "last_session": {k: last.get(k) for k in ("step", "sfx", "attempt", "model", "started_at", "ended_at",
                                                   "exit_code", "error", "log_path")} if last else None,
        "stopped_at": rec.get("stopped_at"),
        "reason": (str(rec.get("reason"))[-600:] if rec.get("reason") else None),
        "error": st.get("error"),
        "record": {k: rec[k] for k in keep if k in rec},
        "gates": {k: str(v).strip().splitlines()[-1][:300] if str(v).strip() else ""
                  for k, v in (rec.get("gates") or {}).items()},
        "headline": headline(run),
        "result_available": (run / RESULT_FILE).exists(),
        "updated_at": st.get("updated_at"),
        "log_tail": _tail_lines(run / LOG_FILE, log_lines),
    }
    return out


def list_runs(runs_root: str | Path | None = None) -> list[dict[str, Any]]:
    root = Path(runs_root).expanduser().resolve() if runs_root else default_runs_root()
    if not root.is_dir():
        return []
    rows = []
    for d in root.iterdir():
        if not d.is_dir() or not ((d / "run_policy.json").exists() or (d / STATE_FILE).exists()):
            continue
        st = read_json(d / STATE_FILE) or {}
        pol = read_json(d / "run_policy.json") or {}
        opts = read_json(d / OPTIONS_FILE) or {}
        state = st.get("status") or "created"
        running = is_running(d)
        if running:
            state = "running"
        elif state == "running":
            state = "interrupted"
        rows.append({
            "run_id": d.name,
            "status": state,
            "stage": st.get("current_stage"),
            "completed_stages": len(st.get("completed_stages") or []),
            "stopped_at": (st.get("rec") or {}).get("stopped_at"),
            "paper": Path(pol.get("paper_pdf") or "").name or None,
            "backend": opts.get("backend"),
            "updated_at": st.get("updated_at") or opts.get("created_at"),
            "mtime": max((d / f).stat().st_mtime for f in (STATE_FILE, "run_policy.json") if (d / f).exists()),
        })
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    for r in rows:
        r.pop("mtime", None)
    return rows


# --------------------------------------------------------------------------- results

def headline(run_dir: str | Path, metrics: Any = None) -> dict[str, Any] | None:
    """The run's headline: the finance paper's measures (share supported first), then the strict survival rate of
    survival.py (published sign and p < 0.05). The measures come from the newer of ama_result.json (or `metrics`,
    which the caller read from it or computed) and paper_measures.json, so a report stage re-run after the last
    ama_result.json is not hidden; a source with an `error` is used only when no other source has values.
    None before any of these files exists."""
    run = Path(run_dir).resolve()

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return -1.0
    cands: list[tuple[float, dict[str, Any]]] = []
    if not isinstance(metrics, dict):
        res = read_json(run / RESULT_FILE)
        metrics = res.get("metrics") if isinstance(res, dict) else None
    if isinstance(metrics, dict):
        cands.append((mtime(run / RESULT_FILE), metrics))
    pm = read_json(run / PAPER_MEASURES_FILE)
    if isinstance(pm, dict):
        cands.append((mtime(run / PAPER_MEASURES_FILE), pm))
    cands.sort(key=lambda c: -c[0])
    m = next((c for _, c in cands if "error" not in c), cands[0][1] if cands else None)
    out: dict[str, Any] = {}
    if isinstance(m, dict) and "error" in m:
        out["measures_error"] = m["error"]
    elif isinstance(m, dict):
        for k in HEADLINE_MEASURES:
            if isinstance(m.get(k), dict) and "value" in m[k]:
                out[k] = m[k]["value"]
    surv = read_json(run / "grid_survival_headline.json")
    if isinstance(surv, dict):
        out["strict_survival_rate_p05"] = surv.get("survival_rate")
        out["strict_sign_consistency_rate"] = surv.get("sign_consistency_rate")
    return _clean(out) or None


def results(run_dir: str | Path, *, excerpt_chars: int = 4000) -> dict[str, Any]:
    run = Path(run_dir).resolve()
    res = read_json(run / RESULT_FILE)
    out: dict[str, Any] = {"run_id": run.name, "status": status(run, log_lines=0)["status"],
                           "headline": None, "result": res}
    out["survival_headline"] = read_json(run / "grid_survival_headline.json")
    out["companion_survival_headline"] = read_json(run / "grid_survival_companion_headline.json")
    metrics = (res or {}).get("metrics") if isinstance(res, dict) else None
    if metrics is None:
        try:
            from .metrics import compute_paper_metrics
            metrics = compute_paper_metrics(run)
        except Exception as e:  # noqa: BLE001
            metrics = {"error": f"metrics unavailable: {type(e).__name__}: {e}"}
    out["metrics"] = metrics
    out["headline"] = headline(run, metrics)
    rep = run / "report.md"
    out["report_path"] = str(rep) if rep.exists() else None
    if rep.exists():
        txt = rep.read_text(encoding="utf-8", errors="replace")
        out["report_excerpt"] = txt[:excerpt_chars] + (f"\n... ({len(txt) - excerpt_chars} more characters; "
                                                      "read_run_file('report.md') for the rest)"
                                                      if len(txt) > excerpt_chars else "")
    else:
        out["report_excerpt"] = None
    return out


def read_run_file(run_dir: str | Path, rel_path: str, *, max_bytes: int = 100_000) -> dict[str, Any]:
    """A text file (or a directory listing) inside the run directory. ``benchmark/`` is never
    readable: it holds held-out material no agent and no reader of the run may open."""
    run = Path(run_dir).resolve()
    rel = str(rel_path or ".").strip()
    if Path(rel).is_absolute():
        raise ValueError("give a path relative to the run directory")
    target = (run / rel).resolve()
    try:
        parts = target.relative_to(run).parts
    except ValueError:
        raise ValueError("path is outside the run directory") from None
    if parts and parts[0] == "benchmark":
        raise PermissionError("benchmark/ is held out and cannot be read")
    if not target.exists():
        raise FileNotFoundError(f"{rel} does not exist in the run directory")
    if target.is_dir():
        entries = []
        for p in sorted(target.iterdir()):
            if not parts and p.name == "benchmark":
                continue
            with contextlib.suppress(OSError):
                entries.append({"name": p.name + ("/" if p.is_dir() else ""),
                                "bytes": None if p.is_dir() else p.stat().st_size})
        return {"path": rel, "type": "directory", "entries": entries[:500], "truncated": len(entries) > 500}
    cap = max(1, min(int(max_bytes), TEXT_LIMIT))
    size = target.stat().st_size
    with target.open("rb") as fh:
        data = fh.read(cap)
    if b"\x00" in data[:8192]:
        raise ValueError(f"{rel} is a binary file")
    return {"path": rel, "type": "file", "bytes": size, "truncated": size > cap,
            "content": data.decode("utf-8", "replace")}
