"""Run one paper through the AMA pipeline.

    python -m ama_mcp.orchestrator run --run-dir RUN [--from-stage STAGE]

This is a port of the per-paper control flow of the workflow script that ran the study
(Package -> Config -> Reproduce -> Forks -> Grid -> Companion -> Report). Every role is a
fresh agent session launched through the user's Claude Code or Codex CLI (``runners.py``);
gates and the other core scripts run here as plain subprocesses. Each role is told only
where its rendered prompt is; the prompt files and the gate scripts live in ``core/``.

Resumability. Every decision point is recorded in ``RUN/orchestrator_state.json`` the
moment it is taken: role sessions that finished, gate exit codes and outputs, file moves,
and the outcome of every check that steers the control flow (finder status, audit tallies,
grid completeness after a wait). Running again on the same directory replays the recorded
decisions without repeating any work and continues at the first step that has not finished:
a role interrupted mid-session is launched again with a fresh session, and an implementer
interrupted after its grid finished on disk is not relaunched. Completed stages are skipped
outright. A run that a gate stopped stays stopped (``--from-stage`` redoes stages).
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import csv
import datetime as _dt
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__
from . import core_dir as _default_core_dir

try:  # POSIX only; the orchestrator falls back to the pid check without it
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

STAGES: tuple[str, ...] = ("package", "config", "reproduce", "forks", "grid", "companion", "report")
PHASES = {"package": "Package", "config": "Config", "reproduce": "Reproduce", "forks": "Forks",
          "grid": "Grid", "companion": "Companion", "report": "Report"}
STEPS: tuple[str, ...] = ("P", "B", "SR", "BF", "C", "CR", "0", "G0A", "1", "2", "3", "3F", "4",
                          "3C", "4C", "3CF", "5", "6")

OPTIONS_FILE = "ama_options.json"
STATE_FILE = "orchestrator_state.json"
LOG_FILE = "orchestrator.log"
RESULT_FILE = "ama_result.json"
PID_FILE = "orchestrator.pid"
PAPER_MEASURES_FILE = "paper_measures.json"   # the paper's headline measures, written before the reporter
# the measures of the finance paper ("An AI-Powered Multiverse Analysis of Empirical Finance Research") that the
# report leads with (ama_mcp.metrics keys), in report order
HEADLINE_MEASURES = ("share_supported", "median_signed_z", "share_published_sign", "reproduced",
                     "n_specifications", "companion_share_supported", "claim_type", "reproduced_null_standard")
LOCK_FILE = ".orchestrator.lock"
AGENT_LOGS = "agent_logs"

# Orchestrator options (RUN/ama_options.json, written by runs.create_run).
DEFAULT_OPTIONS: dict[str, Any] = {
    "backend": "claude",          # "claude" | "codex" | "fake" (tests)
    "models": {},                 # step -> model, overrides runners.DEFAULT_MODELS; "*" = every step
    "stop_after": None,           # None | "G2" | a stage name: pause once that stage has finished
    "citation": None,             # passed to render_prompt.py as --paper-hint
    "poll_interval_s": 570,       # the study's grid wait: python sleep of 570 s between checks
    "max_wait_polls": 70,         # grid: up to ~11 h of waiting per wait (the study's default)
    "companion_max_polls": 40,    # companion grid: up to ~6 h per wait (the study's value)
    "grid_quiet_s": 120,          # a grid file touched within this many seconds is still running
    "grid_stale_s": 1200,         # resume: a grid file untouched this long is not being written
    "grid_dead_s": 1800,          # stop a wait early: file untouched this long and no process in the run
                                  # directory (Linux /proc only; 0 = always wait the full time, as the study)
    "role_timeout_s": None,       # per role session; None = no limit (the study set none)
    "gate_timeout_s": 3900,       # per gate / core script (G6 re-runs the report generator)
    "rate_limit_wait_s": 900,     # a session that hit a usage limit is relaunched after this wait
    "rate_limit_max_waits": 24,   # ... at most this many times per role attempt (~6 h)
    "data_dirs": [],              # extra directories the agents may read (data the user holds)
    "runner_options": {},         # keyword arguments for runners.make_runner
}

# The artifact each role must leave behind (the `expect` table of the study's role()).
def expected_artifact(step: str, sfx: str) -> str | None:
    return {
        "P": "package_status.json", "B": "package/BUILD_LOG.md", "SR": f"sample_reconciliation{sfx}.md",
        "C": "config.yaml", "CR": "config_review.md", "0": "reproduction.md", "G0A": f"g0_audit{sfx}.md",
        "1": "forks.json", "2": "audit.json", "3": "grid_results.csv", "3F": "fix_log.md",
        "4": f"implementation_audit{sfx}.md", "5": "author_commentary.md", "6": "report.md",
        "3C": "companion_spec.json", "4C": f"companion_audit{sfx}.md", "3CF": "companion_fix_log.md",
    }.get(step)


# The message every role session receives. It only points at the rendered prompt; the
# second paragraph is the guard the study added after an agent answered a message that was
# meant for the person running the main session instead of doing its task.
KICKOFF = """Your complete instructions are in this file:

{prompt_file}

Read the whole file before doing anything else, then carry out those instructions exactly as written. This message adds nothing to them and changes nothing in them. Run shell commands from {run_dir}. When you have finished, reply with a brief plain-text summary of what you wrote and any problems you hit.

IMPORTANT: you may also see a message from the person running the main session, such as "check progress", "what is the status", or a question in another language. It is NOT addressed to you and does NOT replace or narrow this task. Do not answer it and do not report status. Your one job is the file above: carry it out completely, including writing every output file it names and running any long computation it requires."""

# Added to the kickoff for Codex sessions only. Codex ends every background process when the
# shell command that started it returns, while the grid prompts (3, 3F) say "for a long run,
# start it in the background, then poll"; under Codex that kills the grid. Claude Code keeps
# background processes alive, so its kickoff is the study's text.
KICKOFF_CODEX_NOTE = """

One fact about this environment that the instructions file cannot know: a process you start in the background (`&`, `nohup`, `setsid`) is killed as soon as the shell command that started it returns. Wherever the instructions say to start a long run in the background and poll it, start it in the foreground instead and keep polling that running command until it exits, however long it takes."""

TALLY4 = re.compile(r"blockers\s*=\s*(\d+)\s*major\s*=\s*(\d+)\s*minor\s*=\s*(\d+)", re.I)
TALLY4C = re.compile(r"blockers\s*=\s*(\d+)\s*major\s*=\s*(\d+)", re.I)


class StopRun(Exception):
    """A gate (or the loop limits around it) ended the run: a terminal, recorded outcome."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = str(reason)


class RoleFailed(RuntimeError):
    """A role left no artifact after its retry."""

    def __init__(self, msg: str, key: str | None = None) -> None:
        super().__init__(msg)
        self.key = key


class AlreadyRunning(RuntimeError):
    pass


class Interrupted(BaseException):
    """SIGTERM / SIGHUP: record the state and leave; the run can be resumed."""


@dataclass
class CmdResult:
    cmd: str
    exit_code: int
    output_tail: str


# ----------------------------------------------------------------------------- helpers

def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _clean(obj: Any) -> Any:
    """JSON-safe copy: NaN/inf -> None, numpy scalars -> Python, unknown -> str."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return _clean(item())
        except Exception:  # noqa: BLE001
            pass
    return str(obj)


def write_json_atomic(path: Path, obj: Any) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(_clean(obj), indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_options(run_dir: Path) -> dict[str, Any]:
    opts = copy.deepcopy(DEFAULT_OPTIONS)
    got = read_json(Path(run_dir) / OPTIONS_FILE)
    if isinstance(got, dict):
        opts.update(got)
    return opts


def save_options(run_dir: Path, opts: dict[str, Any]) -> None:
    write_json_atomic(Path(run_dir) / OPTIONS_FILE, opts)


def new_state(run_id: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "run_id": run_id,
        "status": "created",
        "current_stage": None,
        "completed_stages": [],
        "rec": {"run": run_id, "gates": {}, "completed": False},
        "snapshots": {},
        "journal": {},
        "roles": [],
        "gates": [],
        "inflight": None,
        "role_seq": 0,
        "error": None,
        "history": [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def _ancestors() -> set[int]:
    """This process and every process above it (Linux /proc)."""
    pids, pid = {os.getpid(), os.getppid()}, os.getppid()
    while pid > 1:
        try:
            status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
            pid = int(next(l.split()[1] for l in status.splitlines() if l.startswith("PPid:")))
        except (OSError, ValueError, StopIteration, IndexError):
            break
        if pid in pids:
            break
        pids.add(pid)
    return pids


def _pause_target(stop_after: Any) -> str | None:
    """The stage after which ``stop_after`` pauses the run ("G2" = after the forks stage)."""
    if not stop_after:
        return None
    return "forks" if str(stop_after).upper() == "G2" else str(stop_after).lower()


def _artifact_present(run_dir: Path, rel: str) -> bool:
    """`test -s`: exists and is not empty."""
    p = run_dir / rel
    try:
        return p.exists() and (p.is_dir() or p.stat().st_size > 0)
    except OSError:
        return False


# ------------------------------------------------------------------------- orchestrator

class Orchestrator:
    """Runs (or resumes) one paper in one run directory."""

    def __init__(self, run_dir: str | Path, *, runner: Any = None, core_dir: str | Path | None = None,
                 python: str | None = None, options: dict[str, Any] | None = None,
                 sleep: Callable[[float], None] | None = None, echo: bool | None = None) -> None:
        self.run_dir = Path(run_dir).expanduser().resolve()
        if not self.run_dir.is_dir():
            raise FileNotFoundError(f"run directory not found: {self.run_dir}")
        self.run_id = self.run_dir.name
        self.core = Path(core_dir).expanduser().resolve() if core_dir else _default_core_dir()
        self.scripts = self.core / "scripts"
        self.py = python or sys.executable
        self.opts = load_options(self.run_dir)
        if options:
            self.opts.update(options)
        self._runner = runner
        self.backend = str(getattr(runner, "backend", "") or self.opts.get("backend") or "claude")
        self._sleep = sleep or time.sleep
        self.echo = sys.stderr.isatty() if echo is None else echo
        self.state = self._load_state()
        self.rec: dict[str, Any] = self.state["rec"]
        self.stage: str | None = None
        self._occ: dict[str, int] = {}
        self._resumed_inflight: str | None = None
        self._mcp_warned = False
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- state and logging
    def _load_state(self) -> dict[str, Any]:
        got = read_json(self.run_dir / STATE_FILE)
        if not isinstance(got, dict):
            return new_state(self.run_id)
        base = new_state(self.run_id)
        base.update(got)
        return base

    def _save(self) -> None:
        with self._lock:
            self.state["updated_at"] = now_iso()
            write_json_atomic(self.run_dir / STATE_FILE, self.state)

    def log(self, msg: str) -> None:
        line = f"{now_iso()}  {msg}"
        with contextlib.suppress(OSError):
            with (self.run_dir / LOG_FILE).open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        if self.echo:
            print(line, file=sys.stderr, flush=True)

    def _event(self, event: str, detail: str = "") -> None:
        self.state["history"].append({"at": now_iso(), "event": event, "detail": detail[:500]})

    @property
    def runner(self) -> Any:
        if self._runner is None:
            from .runners import make_runner  # lazy: keeps imports light for status/list calls
            self._runner = make_runner(self.backend, **(self.opts.get("runner_options") or {}))
        return self._runner

    def model_for(self, step: str) -> str | None:
        models = self.opts.get("models") or {}
        if step in models:
            return models[step] or None
        if "*" in models:
            return models["*"] or None
        try:
            from .runners import DEFAULT_MODELS
        except Exception:  # noqa: BLE001
            return None
        return (DEFAULT_MODELS.get(self.backend) or {}).get(step)

    def _extra_dirs(self) -> list[Path]:
        """Directories outside the run directory that a role session is given (``--add-dir``).
        The core directory is deliberately not among them: agents only read it (by absolute
        path), and in a sandboxed CLI mode an added directory is writable, which would let an
        agent edit the gate scripts."""
        dirs: list[Path] = [Path(d).expanduser() for d in (self.opts.get("data_dirs") or [])]
        pol = read_json(self.run_dir / "run_policy.json") or {}
        for src in pol.get("data_role_sources") or []:
            for p in (src or {}).get("paths", []) or []:
                pp = Path(p).expanduser()
                dirs.append(pp if pp.is_dir() else pp.parent)
        rp = (pol.get("restricted_package") or {}).get("path")
        if rp:
            dirs.append(Path(rp).expanduser())
        out: list[Path] = []
        for d in dirs:
            with contextlib.suppress(OSError):
                d = d.resolve()
                if d.is_dir() and d not in out and d != self.run_dir:
                    out.append(d)
        return out

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    # --------------------------------------------------------------------- the journal
    def _key(self, kind: str, name: str) -> str:
        base = f"{self.stage}/{kind}:{name}"
        n = self._occ.get(base, 0) + 1
        self._occ[base] = n
        return f"{base}#{n}"

    def _journal(self, key: str, value: Any) -> None:
        self.state["journal"][key] = value
        self._save()

    def memo(self, name: str, fn: Callable[[], Any], kind: str = "check") -> Any:
        """Run ``fn`` once; a resumed run replays its recorded value."""
        key = self._key(kind, name)
        hit = self.state["journal"].get(key)
        if hit is not None:
            return hit.get("value")
        value = fn()
        self._journal(key, {"value": value, "at": now_iso()})
        return value

    # ------------------------------------------------------------------ core scripts
    def _exec(self, script: str, *args: str, timeout: float | None = None) -> CmdResult:
        cmd = [self.py, str(self.scripts / script), str(self.run_dir), *map(str, args)]
        shown = " ".join(shlex.quote(c) for c in [script, *map(str, args)])
        timeout = timeout or float(self.opts.get("gate_timeout_s") or 3900)
        try:
            p = subprocess.run(cmd, cwd=self.run_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=timeout, env=self._env(), stdin=subprocess.DEVNULL)
            out, code = p.stdout.decode("utf-8", "replace"), p.returncode
        except subprocess.TimeoutExpired as e:
            raw = e.stdout or e.output or b""
            out = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw))
            out += f"\n[{script} timed out after {int(timeout)} s]"
            code = 124
        except OSError as e:
            out, code = f"cannot run {script}: {e}", 127
        return CmdResult(shown, code, out[-3000:])

    def gate(self, name: str, script: str, *args: str) -> CmdResult:
        """Run a gate (or freeze / survival) once and record its result; replayed on resume."""
        key = self._key("gate", name)
        hit = self.state["journal"].get(key)
        if hit is not None:
            return CmdResult(**hit["result"])
        res = self._exec(script, *args)
        last = (res.output_tail.strip().splitlines() or [""])[-1][:200]
        self.log(f"{self.run_id}: {name}: exit {res.exit_code} -- {last}")
        self.state["gates"].append({"stage": self.stage, "name": name, "cmd": res.cmd,
                                    "exit_code": res.exit_code, "output_tail": res.output_tail[-1500:],
                                    "at": now_iso()})
        self._journal(key, {"result": asdict(res), "at": now_iso()})
        return res

    def _render(self, step: str, rflag: list[str], sfx: str) -> Path:
        args = [step, *rflag]
        if self.opts.get("citation"):
            args += ["--paper-hint", str(self.opts["citation"])]
        cmd = [self.py, str(self.scripts / "render_prompt.py"), str(self.run_dir), *args]
        p = subprocess.run(cmd, cwd=self.run_dir, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           text=True, errors="replace", timeout=600, env=self._env(),
                           stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise RuntimeError(f"render {step} failed: {p.stderr[-300:]}")
        f = self.run_dir / "prompts_rendered" / f"step{step}{sfx}.md"
        if not f.is_file():
            raise RuntimeError(f"render {step}{sfx}: {f.name} was not written")
        return f

    # -------------------------------------------------------------------------- roles
    def role(self, step: str, round: int = 1, findings: str = "") -> dict[str, Any]:
        """Render the step's prompt and run it as a fresh agent session (the study's role())."""
        rflag = ["--round", str(round)] if round > 1 else []
        sfx = f"_r{round}" if round > 1 else ""
        if findings:  # fix round acting on a named findings file; rendered prompt saved under its stem
            rflag += ["--findings", findings]
            sfx = "_" + re.sub(r"\.md$", "", findings)
        key = self._key("role", f"{step}{sfx}")
        hit = self.state["journal"].get(key)
        if hit is not None:
            if hit.get("failed"):
                raise RoleFailed(hit.get("error", f"step {step}{sfx} failed"))
            return {**hit, "key": key}
        try:
            return {**self._run_role(step, rflag, sfx, key), "key": key}
        except Exception as e:
            # lets a caller that tolerates the failure (the fix round) record it under this key
            if not isinstance(e, RoleFailed):
                with contextlib.suppress(Exception):
                    e.role_key = key  # type: ignore[attr-defined]
            raise

    def _run_role(self, step: str, rflag: list[str], sfx: str, key: str) -> dict[str, Any]:
        if step == "3" and key == self._resumed_inflight and self._grid_on_disk_after_interruption():
            entry = {"ok": True, "attempts": 0, "resumed_from_files": True, "at": now_iso()}
            self._journal(key, entry)
            return entry
        expect = expected_artifact(step, sfx)
        prompt_file = self._render(step, rflag, sfx)
        model = self.model_for(step)
        self.state["inflight"] = {"key": key, "step": step, "sfx": sfx, "since": now_iso()}
        self._save()
        res: Any = None
        attempt = 0
        for attempt in (1, 2):
            res = self._launch(step, sfx, prompt_file, model, attempt)
            if not expect:
                break
            # guard: the role must leave its artifact behind (an agent once answered an unrelated
            # message instead of doing its task)
            if _artifact_present(self.run_dir, expect):
                break
            self.log(f"{self.run_id}: step {step}{sfx} left no {expect}"
                     + (" -- retrying once" if attempt == 1 else ""))
            if attempt == 2:
                msg = f"step {step}{sfx} produced no {expect} twice"
                err = getattr(res, "error", None)
                if err:
                    msg += f" (last session: {err}, exit {getattr(res, 'exit_code', '?')})"
                self.state["inflight"] = None
                self._journal(key, {"failed": True, "error": msg, "at": now_iso()})
                raise RoleFailed(msg, key=key)
        if not expect and res is not None and getattr(res, "exit_code", 0) != 0:
            self.log(f"{self.run_id}: step {step}{sfx} ended with exit {getattr(res, 'exit_code', '?')} "
                     "(this step has no required artifact; the gate after it decides)")
        self.state["inflight"] = None
        entry = {"ok": True, "attempts": attempt, "prompt": f"prompts_rendered/{prompt_file.name}",
                 "at": now_iso()}
        self._journal(key, entry)
        return entry

    def _launch(self, step: str, sfx: str, prompt_file: Path, model: str | None, attempt: int) -> Any:
        label = f"{self.run_id}:step{step}{sfx}" + (" retry" if attempt > 1 else "")
        message = KICKOFF.format(prompt_file=prompt_file, run_dir=self.run_dir)
        if self.backend == "codex":
            message += KICKOFF_CODEX_NOTE
        waits = 0
        while True:
            self.state["role_seq"] = int(self.state.get("role_seq") or 0) + 1
            seq = self.state["role_seq"]
            log_rel = f"{AGENT_LOGS}/{seq:03d}_step{step}{sfx}{'_retry' if attempt > 1 else ''}.log"
            (self.run_dir / AGENT_LOGS).mkdir(exist_ok=True)
            started = now_iso()
            self.log(f"{self.run_id}: [{PHASES.get(self.stage or '', '')}] step {step}{sfx}"
                     f"{' (retry)' if attempt > 1 else ''} -> {self.backend}"
                     f"{' model ' + model if model else ''}; transcript {log_rel}")
            t0 = time.monotonic()
            res = self.runner.run(message, workdir=self.run_dir, role=step, label=label, model=model,
                                  log_path=self.run_dir / log_rel, timeout_s=self.opts.get("role_timeout_s"),
                                  extra_dirs=self._extra_dirs())
            err = getattr(res, "error", None)
            entry = {
                "seq": seq, "stage": self.stage, "step": step, "sfx": sfx, "attempt": attempt,
                "label": label, "backend": self.backend, "model": model, "log_path": log_rel,
                "started_at": started, "ended_at": now_iso(),
                "duration_s": round(float(getattr(res, "duration_s", time.monotonic() - t0) or 0), 1),
                "exit_code": getattr(res, "exit_code", None), "timed_out": bool(getattr(res, "timed_out", False)),
                "error": err, "last_message": str(getattr(res, "last_message", "") or "")[-500:],
            }
            self.state["roles"].append(entry)
            self._save()
            self.log(f"{self.run_id}: step {step}{sfx} ended: exit {entry['exit_code']}"
                     f"{' (' + str(err) + ')' if err else ''} after {entry['duration_s']} s")
            self._warn_mcp(getattr(res, "meta", None))
            if err == "rate_limited" and waits < int(self.opts.get("rate_limit_max_waits") or 0):
                waits += 1
                wait_s = float(self.opts.get("rate_limit_wait_s") or 900)
                self.log(f"{self.run_id}: usage limit reached; relaunching step {step}{sfx} in {int(wait_s)} s "
                         f"(wait {waits})")
                self._sleep(wait_s)
                continue
            return res

    def _warn_mcp(self, meta: Any) -> None:
        """Once per invocation: say when a session may have kept the user's MCP servers (Codex turns
        them off one by name; a name it cannot address, or a failed listing, leaves them on)."""
        if self._mcp_warned or not isinstance(meta, dict):
            return
        left = [str(s) for s in meta.get("mcp_servers_not_disabled") or []]
        failed = meta.get("mcp_list_error")
        if not (left or failed):
            return
        self._mcp_warned = True
        what = (f"these MCP servers stayed enabled (a name with characters other than letters, digits, '-' "
                f"and '_' cannot be turned off from the command line): {', '.join(left)}" if left else
                f"the configured MCP servers could not be listed ({failed}), so none was turned off")
        self.log(f"{self.run_id}: warning: in this {self.backend} session {what}. Role sessions should not load "
                 "other MCP servers, in particular not this tool's own server; remove them from the CLI "
                 "configuration the run uses.")

    # ------------------------------------------------------------------ grid helpers
    def _quiet(self) -> float:
        return float(self.opts.get("grid_quiet_s", 120))

    def _grid_status(self) -> tuple[bool, str]:
        try:
            n = int(read_json(self.run_dir / "audit.json")["recommended_grid"]["n_cells"])
            f = self.run_dir / "grid_results.csv"
            r, age = 0, 0.0
            if f.exists():
                with f.open(newline="", encoding="utf-8", errors="replace") as fh:
                    r = len({row.get("cell_id") for row in csv.DictReader(fh)})
                age = time.time() - f.stat().st_mtime
            return (r >= n and age > self._quiet()), f"distinct cells {r} designated {n} age {int(age)}"
        except Exception as e:  # noqa: BLE001  (a missing or malformed file reads as incomplete)
            return False, f"cannot read the grid: {type(e).__name__}: {e}"

    def _grid_done(self, label: str) -> bool:
        ok, detail = self._grid_status()
        self.log(f"{self.run_id}: grid complete? {label}: {detail} -> {'COMPLETE' if ok else 'INCOMPLETE'}")
        return ok

    def _wait_grid(self, label: str) -> bool:
        """Wait until the grid is complete (the study: up to ~11 h, a check every ~10 min)."""
        t0 = time.time()
        for i in range(int(self.opts.get("max_wait_polls", 70))):
            if self._grid_done(f"{label} poll {i}"):
                return True
            if self._abandoned(self.run_dir / "grid_results.csv", f"grid {label}", since=t0):
                return False
            self._sleep(float(self.opts.get("poll_interval_s", 570)))
        return False

    def _process_in_run_dir(self) -> bool | None:
        """Is any other process working in the run directory: its working directory is inside it,
        or its command line names a path inside it? None when it cannot be told (no /proc). The
        orchestrator itself and the processes above it (the MCP server, a shell or a `timeout`
        wrapper whose command line names the run) are not counted. Processes of other users
        (working directory unreadable) and processes in another PID namespace (another
        container) are seen only through their command line, or not at all."""
        proc = Path("/proc")
        if not (proc / "self" / "cwd").exists():
            return None
        me = _ancestors()
        root = str(self.run_dir)

        def inside(s: str) -> bool:
            return s == root or s.endswith(root) or (root + os.sep) in s

        for d in proc.iterdir():
            if not d.name.isdigit() or int(d.name) in me:
                continue
            try:
                if inside(os.readlink(d / "cwd")):
                    return True
            except OSError:
                pass
            try:
                args = (d / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            if any(inside(a.decode("utf-8", "replace")) for a in args if a):
                return True
        return False

    def _abandoned(self, f: Path, what: str, since: float | None = None) -> bool:
        """Stop waiting early only when nothing can still be writing the file: it is incomplete,
        untouched for ``grid_dead_s`` and no process works in the run directory (e.g. a
        background grid that the agent CLI killed when its session ended). A file that does not
        exist yet counts as untouched since ``since`` (the start of the wait), so ``grid_dead_s``
        always applies."""
        dead_s = float(self.opts.get("grid_dead_s") or 0)
        if dead_s <= 0:
            return False
        try:
            age = time.time() - f.stat().st_mtime
            state = "untouched"
        except OSError:
            age = time.time() - since if since is not None else 0.0
            state = "not written"
        if age < dead_s or self._process_in_run_dir() is not False:
            return False
        self.log(f"{self.run_id}: {what}: incomplete, {state} for {int(age)} s and no process is "
                 "working in the run directory -- not waiting further")
        return True

    def _grid_ready(self, label: str) -> bool:
        return bool(self.memo(f"grid ready {label}", lambda: self._grid_done(label) or self._wait_grid(label)))

    def _grid_on_disk_after_interruption(self) -> bool:
        """Resume: the implementer was interrupted; if its grid is complete (or still being
        written and then completes), keep it instead of launching a new implementer."""
        gr = self.run_dir / "grid_results.csv"
        if not (gr.exists() and any((self.run_dir / f"run_grid.{e}").exists() for e in ("py", "R", "do"))):
            return False
        self.log(f"{self.run_id}: the implementer was interrupted; checking the grid it left on disk")
        if self._grid_done("resume after interruption"):
            return True
        if time.time() - gr.stat().st_mtime < float(self.opts.get("grid_stale_s", 1200)):
            return self._wait_grid("resume after interruption")
        return False

    def _companion_status(self) -> tuple[bool, str]:
        try:
            spec = read_json(self.run_dir / "companion_spec.json")
            n = spec.get("n_cells") or read_json(self.run_dir / "audit.json")["recommended_grid"]["n_cells"]
            n = int(n)
            f = self.run_dir / "companion_results.csv"
            r, age = 0, 0.0
            if f.exists():
                with f.open(newline="", encoding="utf-8", errors="replace") as fh:
                    r = sum(1 for _ in csv.reader(fh)) - 1
                age = time.time() - f.stat().st_mtime
            return (r >= n and age > self._quiet()), f"rows {r} cells {n} age {int(age)}"
        except Exception as e:  # noqa: BLE001
            return False, f"cannot read the companion grid: {type(e).__name__}: {e}"

    def _companion_done(self, label: str) -> bool:
        ok, detail = self._companion_status()
        self.log(f"{self.run_id}: companion complete? {label}: {detail} -> {'COMPLETE' if ok else 'INCOMPLETE'}")
        return ok

    def _companion_wait(self, label: str) -> bool:
        """The study's companion wait: sleep first, then check, up to 40 times."""
        t0 = time.time()
        for i in range(int(self.opts.get("companion_max_polls", 40))):
            if self._abandoned(self.run_dir / "companion_results.csv", f"companion {label}", since=t0):
                return False
            self._sleep(float(self.opts.get("poll_interval_s", 570)))
            if self._companion_done(f"{label} poll {i}"):
                return True
        return False

    # -------------------------------------------------------------- reads that steer flow
    def _finder_status(self) -> str:
        try:
            return str(read_json(self.run_dir / "package_status.json")["finder"]["status"])
        except Exception as e:  # noqa: BLE001
            return f"unreadable: {type(e).__name__}: {e}"

    def _builder_feasible(self) -> str:
        try:
            st = read_json(self.run_dir / "package_status.json")
            return str((st.get("builder") or {}).get("feasible"))
        except Exception as e:  # noqa: BLE001
            return f"unreadable: {type(e).__name__}: {e}"

    def _last_tally_line(self, rel: str) -> str:
        """`grep -E "TALLY:" FILE | tail -1`"""
        try:
            lines = [l for l in (self.run_dir / rel).read_text(encoding="utf-8", errors="replace").splitlines()
                     if "TALLY:" in l]
        except OSError as e:
            return f"grep: {rel}: {e.strerror}"
        return lines[-1].strip() if lines else ""

    def _restored_count(self) -> int | None:
        try:
            d = read_json(self.run_dir / "companion_spec.json")
            return len(d.get("restored") or [])
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------------- stages
    def _stage_package(self) -> None:
        rec = self.rec
        self.role("P")
        rec["finder_status"] = self.memo("finder status", self._finder_status)
        # only a complete authors' package skips the builder; partial data (e.g. one posted
        # variable) still needs it
        authors_data = rec["finder_status"] == "authors_data_and_code"
        if not authors_data:
            self.role("B")
            feasible = self.memo("builder feasible", self._builder_feasible)
            if "False" in str(feasible):
                gpi = self.gate("GP", "gate_gp.py")
                raise StopRun("GP", "INFEASIBLE " + gpi.output_tail)
        # sample fidelity: independent reviewer; documented-but-missed rules go back to the builder
        for k in (1, 2):
            self.role("SR", k)
            gs = self.gate(f"GS r{k}", "gate_gs.py")
            rec["gates"][f"GS_r{k}"] = gs.output_tail[-600:]
            if gs.exit_code == 0:
                rec["sample"] = f"best_achievable_r{k}"
                break
            if gs.exit_code != 3:
                raise StopRun("GS", gs.output_tail)
            if k == 2 or authors_data:
                rec["sample"] = "fixable_items_remain_recorded"
                self.log(f"{self.run_id}: sample items remain FIXABLE; recorded")
                break
            self.role("BF", k, f"sample_reconciliation{'_r' + str(k) if k > 1 else ''}.md")
        gp = self.gate("GP", "gate_gp.py")
        rec["gates"]["GP"] = gp.output_tail[-600:]
        if gp.exit_code == 2:
            raise StopRun("GP", "INFEASIBLE " + gp.output_tail)
        if gp.exit_code != 0:
            raise StopRun("GP", gp.output_tail)
        self.log(f"{self.run_id}: package ready ({rec.get('finder_status')})")

    def _archive_config_review(self) -> str:
        n = 1
        while (self.run_dir / f"config_review_r{n}.md").exists():
            n += 1
        src = self.run_dir / "config_review.md"
        if src.exists():
            os.replace(src, self.run_dir / f"config_review_r{n}.md")
            return f"archived r{n}"
        return "nothing to archive"

    def _stage_config(self) -> None:
        rec = self.rec
        gc_ok = False
        attempt = 0
        for attempt in (1, 2):
            # C reads any existing config_review.md (a rejected earlier draft); it is then archived
            self.role("C")
            self.memo(f"archive review {attempt}", self._archive_config_review, kind="action")
            self.role("CR")
            gc = self.gate(f"GC try{attempt}", "gate_gc.py")
            rec["gates"]["GC"] = gc.output_tail[-800:]
            gc_ok = gc.exit_code == 0
            if gc_ok:
                break
        if not gc_ok:
            raise StopRun("GC", rec["gates"]["GC"])

    def _archive_reproduction(self, k: int) -> str:
        """The study's ``cp reproduction.md reproduction_r<k>.md 2>&1 && echo archived``, whose
        result was not checked: a failed copy is recorded and the fresh reproducer still runs."""
        import shutil
        try:
            shutil.copy2(self.run_dir / "reproduction.md", self.run_dir / f"reproduction_r{k}.md")
        except OSError as e:
            self.log(f"{self.run_id}: archive repro r{k}: cp failed ({e}); continuing")
            return f"cp failed: {e}"
        return "archived"

    def _stage_reproduce(self) -> None:
        rec = self.rec
        self.role("0")
        g0 = self.gate("G0", "gate_g0.py")
        if g0.exit_code != 0 and re.search(r"no matching row|reproduction\.md missing", g0.output_tail):
            # procedural failure only (table not parseable): one fresh reproducer
            self.role("0", 2)
            g0 = self.gate("G0 retry", "gate_g0.py")
        rec["gates"]["G0"] = rec["gates"].get("G0") or g0.output_tail[-1500:]
        if g0.exit_code == 2:
            raise StopRun("G0", "INFEASIBLE (headline not estimable) " + g0.output_tail)
        if g0.exit_code == 0:
            rec["g0_path"] = "pass"
        else:
            # an independent auditor looks for implementation errors.
            # CLEAN -> continue with the gap recorded; ERRORS -> fix, re-reproduce, re-gate;
            # MINOR -> fix, re-reproduce, continue; UNRESOLVED -> stop.
            resolved = False
            for k in (1, 2):
                self.role("G0A", k)
                ga = self.gate(f"G0A r{k}", "gate_g0a.py")
                rec["gates"][f"G0A_r{k}"] = ga.output_tail[-800:]
                if ga.exit_code == 0:
                    resolved = True
                    rec["g0_path"] = f"audit_clean_r{k}"
                    break
                if ga.exit_code not in (3, 5):
                    raise StopRun("G0A", ga.output_tail)
                minor = ga.exit_code == 5
                if k == 2 and not minor:
                    raise StopRun("G0A", "implementation errors remain after 2 audit rounds: " + ga.output_tail)
                self.role("BF", k, f"g0_audit{'_r' + str(k) if k > 1 else ''}.md")
                gpf = self.gate(f"GP after fix r{k}", "gate_gp.py")
                if gpf.exit_code != 0:
                    raise StopRun("GP-fix", gpf.output_tail)
                self.memo(f"archive repro r{k}", lambda k=k: self._archive_reproduction(k), kind="action")
                self.role("0", k + 1)
                g0 = self.gate(f"G0 after fix r{k}", "gate_g0.py")
                rec["gates"][f"G0_after_fix_r{k}"] = g0.output_tail[-1500:]
                if g0.exit_code == 0:
                    resolved = True
                    rec["g0_path"] = f"pass_after_fix_r{k}"
                    break
                if minor:
                    resolved = True
                    rec["g0_path"] = f"minor_errors_fixed_r{k}"
                    break
            if not resolved:
                raise StopRun("G0A", "unresolved")
        self.log(f"{self.run_id}: reproduction accepted ({rec.get('g0_path')})")

    def _stage_forks(self) -> None:
        rec = self.rec
        self.role("1")
        self.role("2")
        g2 = self.gate("G2", "gate_g2.py")
        if g2.exit_code != 0:
            self.role("2")
            g2 = self.gate("G2 retry", "gate_g2.py")
        rec["gates"]["G2"] = g2.output_tail[-800:]
        if g2.exit_code != 0:
            raise StopRun("G2", g2.output_tail)
        self.log(f"{self.run_id}: G2 PASS")

    def _fix3f(self, rnd: int) -> bool:
        """A fix round may leave its (long) grid rerun running in the background and end
        without fix_log.md: wait for the grid, then have a fresh 3F session record the fixes."""
        try:
            self.role("3F", rnd)
        except Exception as e:  # noqa: BLE001 -- the study caught every error of this session
            # (an interruption is a BaseException and still ends the run)
            self.state["inflight"] = None
            key = getattr(e, "key", None) or getattr(e, "role_key", None)
            if key and key not in self.state["journal"]:
                # recorded as failed, so a resume replays this decision instead of launching it again
                self._journal(key, {"failed": True, "error": f"{type(e).__name__}: {e}"[-500:], "at": now_iso()})
            self.log(f"{self.run_id}: 3F r{rnd}: {e} -- waiting for the grid")
        if not self._grid_ready(f"after 3F r{rnd}"):
            return False
        present = self.memo(f"fix_log present r{rnd}", lambda: _artifact_present(self.run_dir, "fix_log.md"))
        if not present:
            self.role("3F", rnd)
            if not self._grid_ready(f"after 3F r{rnd} record"):
                return False
        return True

    def _stage_grid(self) -> None:
        rec = self.rec
        self.role("3")
        # a grid still writing (or finished < 2 min ago) is not a failure: wait before
        # re-running the implementer
        if not self._grid_ready("after step3"):
            self.log(f"{self.run_id}: grid incomplete after step 3 -- re-running implementer once")
            self.role("3")
            if not self.memo("grid complete after step3 retry", lambda: self._grid_done("after step3 retry")):
                raise StopRun("G3", "grid incomplete after two implementer sessions")
        rnd = 1
        tallies: list[str] = []
        while True:
            self.role("4", rnd)
            sfx = f"_r{rnd}" if rnd > 1 else ""
            line = self.memo(f"tally r{rnd}", lambda sfx=sfx: self._last_tally_line(f"implementation_audit{sfx}.md"))
            m = TALLY4.search(line or "")
            b = int(m.group(1)) if m else 99
            mj = int(m.group(2)) if m else 99
            tallies.append(f"r{rnd}: B={b} M={mj} m={m.group(3)}" if m else f"r{rnd}: no TALLY")
            rec["tallies"] = list(tallies)
            if m and b == 0 and (rnd > 1 or mj == 0):
                break
            if rnd >= 4:
                raise StopRun("G4", f"audit loop did not close after {rnd} rounds: {'; '.join(tallies)}")
            if not self._fix3f(rnd):
                raise StopRun("G3", f"grid incomplete after fix round {rnd}")
            rnd += 1
        rec["tallies"] = tallies
        g4 = self.gate("G4", "gate_g4.py")
        rec["gates"]["G4"] = g4.output_tail[-600:]
        if g4.exit_code != 0:
            raise StopRun("G4", g4.output_tail)
        self.log(f"{self.run_id}: G4 PASS ({'; '.join(tallies)})")

    def _stage_companion(self) -> None:
        rec = self.rec
        r3c = self.role("3C")

        def restored_count() -> int:
            n = self._restored_count()
            if n is None:
                # the study threw here (stopped_at "error"). Raised inside the check so that nothing
                # is recorded, and with the implementer's key so that a resume launches a fresh one.
                raise RoleFailed("companion_spec.json unreadable", key=r3c.get("key"))
            return n

        restored = self.memo("companion spec", restored_count)
        rec["companion_restored"] = int(restored)
        if restored == 0:
            rec["companion"] = "none: no held-fixed choice differs from the paper"
            self.log(f"{self.run_id}: companion {rec['companion']}")
            return
        if not self.memo("companion ready after 3C", lambda: self._companion_done("after 3C")
                         or self._companion_wait("after 3C")):
            # a companion grid may still be finishing in the background: waited above;
            # re-run the implementer once
            self.role("3C")
            if not self.memo("companion complete after 3C retry", lambda: self._companion_done("after 3C retry")):
                rec["companion"] = "incomplete"
                self.log(f"{self.run_id}: companion incomplete")
                return
        tallies: list[str] = []
        for rnd in (1, 2, 3):
            self.role("4C", rnd)
            sfx = f"_r{rnd}" if rnd > 1 else ""
            line = self.memo(f"companion tally r{rnd}",
                             lambda sfx=sfx: self._last_tally_line(f"companion_audit{sfx}.md"))
            mm = TALLY4C.search(line or "")
            b = int(mm.group(1)) if mm else 99
            mj = int(mm.group(2)) if mm else 99
            tallies.append(f"r{rnd}: B={b} M={mj}")
            if mm and b == 0 and (rnd > 1 or mj == 0):
                rec["companion"] = f"audited ({'; '.join(tallies)})"
                self.gate("freeze G4C", "freeze.py", "--gate", "G4C", "--set")
                self.log(f"{self.run_id}: companion {rec['companion']}")
                return
            if rnd == 3:
                rec["companion"] = f"NOT CLEARED ({'; '.join(tallies)}) -- reported as unaudited"
                self.log(f"{self.run_id}: companion {rec['companion']}")
                return
            self.role("3CF", rnd)
            # the re-run may still be finishing in the background
            if not self.memo(f"companion ready after 3CF r{rnd}",
                             lambda rnd=rnd: self._companion_done(f"after 3CF r{rnd}")
                             or self._companion_wait(f"fix r{rnd}")):
                rec["companion"] = "incomplete after fix"
                self.log(f"{self.run_id}: companion {rec['companion']}")
                return

    def _stage_report(self) -> None:
        rec = self.rec
        fz = self.gate("freeze check", "freeze.py", "--check")
        sv = self.gate("survival", "survival.py")
        rec["freeze"] = fz.output_tail[-600:]
        if fz.exit_code != 0:
            raise StopRun("freeze", fz.output_tail)
        rec["survival_stdout"] = sv.output_tail[-2000:]
        if sv.exit_code != 0:
            raise StopRun("survival", sv.output_tail)
        if int(rec.get("companion_restored") or 0) > 0 and "incomplete" not in str(rec.get("companion") or ""):
            svc = self.gate("companion survival", "survival.py", "--companion")
            rec["companion_survival"] = svc.output_tail[-800:]
        self._write_paper_measures()
        self.role("5")
        self.role("6")
        self._write_paper_measures()   # rewritten before each G6, so an agent's edit of the file cannot pass G6
        g6 = self.gate("G6", "gate_g6.py")
        if g6.exit_code != 0:
            self.role("6")
            self._write_paper_measures()
            g6 = self.gate("G6 retry", "gate_g6.py")
        rec["gates"]["G6"] = g6.output_tail[-600:]
        if g6.exit_code != 0:
            raise StopRun("G6", g6.output_tail)
        head = read_json(self.run_dir / "grid_survival_headline.json")
        if head is None:
            with contextlib.suppress(OSError):
                head = (self.run_dir / "grid_survival_headline.json").read_text(encoding="utf-8")[-3000:]
        rec["survival_headline"] = head

    def _write_paper_measures(self) -> None:
        """Write paper_measures.json: the finance paper's measures (share supported first), computed by
        ama_mcp.metrics from the audited grid. It is written before the reporter runs, so the report leads with
        these values and G6 can regenerate it from files. Deterministic, so a resume rewrites the same file.
        It is written again before each G6, so an agent's edit of it cannot pass G6. The companion value is
        withheld when the companion grid is incomplete and flagged when its auditor did not clear it.
        A failure is written into the file and logged; it does not stop the run."""
        try:
            from .metrics import compute_paper_metrics  # lazy; another module owns the measures
            m = compute_paper_metrics(self.run_dir, null_draws=0)
            out: dict[str, Any] = {k: dict(m[k]) for k in HEADLINE_MEASURES if k in m}
            comp = str(self.rec.get("companion") or "")
            ce = out.get("companion_share_supported")
            if ce is not None:
                if "incomplete" in comp:
                    ce.update(value=None, note=f"companion grid {comp}; not reported")
                elif comp.startswith("NOT CLEARED"):
                    ce["note"] = "the companion auditor did not clear the companion grid; report it as unaudited"
                elif ce.get("value") is None and not ce.get("note"):
                    ce["note"] = "no companion grid" + (f" ({comp})" if comp else "")
        except Exception as e:  # noqa: BLE001
            out = {"error": f"measures unavailable: {type(e).__name__}: {e}"}
            self.log(f"{self.run_id}: paper measures unavailable: {type(e).__name__}: {e}")
        write_json_atomic(self.run_dir / PAPER_MEASURES_FILE, out)

    # ------------------------------------------------------------------------ driving
    def _base_rec(self) -> dict[str, Any]:
        done = self.state["completed_stages"]
        if done and done[-1] in self.state["snapshots"]:
            return copy.deepcopy(self.state["snapshots"][done[-1]])
        return {"run": self.run_id, "gates": {}, "completed": False}

    def _pause_after(self, stage: str) -> str | None:
        sa = self.opts.get("stop_after")
        if _pause_target(sa) != stage:
            return None
        return "after G2" if str(sa).upper() == "G2" else f"after {stage}"

    def reset_from(self, stage: str) -> None:
        """Forget the record of ``stage`` and every later stage, so that they run again with
        fresh agents. Artifacts already on disk stay where they are."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; one of {', '.join(STAGES)}")
        idx = STAGES.index(stage)
        done = self.state["completed_stages"]
        if idx > len(done):
            raise ValueError(f"cannot redo from {stage}: stage {STAGES[len(done)]} has not finished")
        drop = set(STAGES[idx:])
        self.state["completed_stages"] = [s for s in done if s not in drop]
        self.state["journal"] = {k: v for k, v in self.state["journal"].items() if k.split("/", 1)[0] not in drop}
        for s in drop:
            self.state["snapshots"].pop(s, None)
        self.state["inflight"] = None
        self.state["status"] = "created"
        self._event("redo", f"from stage {stage}")
        self._save()

    def _write_pid(self) -> None:
        with contextlib.suppress(OSError):
            write_json_atomic(self.run_dir / PID_FILE, {
                "pid": os.getpid(), "pgid": os.getpgid(0), "started_at": now_iso(),
                "cmd": "python -m ama_mcp.orchestrator run --run-dir <run>"})

    @contextlib.contextmanager
    def _exclusive(self):
        if fcntl is None:  # pragma: no cover
            yield
            return
        fh = open(self.run_dir / LOCK_FILE, "a+")  # noqa: SIM115
        try:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AlreadyRunning(f"an orchestrator is already running for {self.run_id}") from None
            except OSError:
                pass  # this filesystem has no flock; the pid check in runs.py still guards
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def _install_signals(self) -> dict[int, Any] | None:
        if threading.current_thread() is not threading.main_thread():
            return None

        def handler(signum, frame):  # noqa: ARG001
            raise Interrupted(f"received signal {signum}")

        prev = {}
        for s in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
            if s is not None:
                prev[s] = signal.signal(s, handler)
        return prev

    def run(self, *, from_stage: str | None = None, install_signal_handlers: bool = True) -> dict[str, Any]:
        """Run the paper, or resume it. Returns the run record (the study's ``rec``)."""
        with self._exclusive():
            prev = self._install_signals() if install_signal_handlers else None
            try:
                return self._run(from_stage)
            finally:
                for s, h in (prev or {}).items():
                    with contextlib.suppress(Exception):
                        signal.signal(s, h)

    def _run(self, from_stage: str | None) -> dict[str, Any]:
        st = self.state
        if from_stage:
            self.reset_from(from_stage)
        elif st["status"] in ("completed", "stopped"):
            self.log(f"{self.run_id}: already {st['status']}"
                     + (f" at {self.rec.get('stopped_at')}" if st["status"] == "stopped" else "")
                     + "; nothing to do (use --from-stage to run stages again)")
            return st["rec"]
        if not self.scripts.is_dir():
            raise FileNotFoundError(f"core scripts not found in {self.core}")
        if st["status"] == "paused":
            self.log(f"{self.run_id}: continuing past the pause ({st['rec'].get('paused')})")
            target = _pause_target(self.opts.get("stop_after"))
            if target is not None and target not in st["completed_stages"]:
                # a new pause point set before this resume (`ama resume --stop-after`, resume_run)
                self.log(f"{self.run_id}: will pause again after {target}")
            elif target is not None:
                # the pause point that was honoured (its stage is done, so it cannot fire again)
                self.opts["stop_after"] = None
                disk = load_options(self.run_dir)
                disk["stop_after"] = None
                save_options(self.run_dir, disk)
        resumed = bool(st["completed_stages"] or st["journal"])
        inflight = st.get("inflight") or {}
        self._resumed_inflight = inflight.get("key")
        st["inflight"] = None
        st.update(status="running", pid=os.getpid(), error=None, error_traceback=None, started_at=now_iso())
        self.rec = self._base_rec()
        st["rec"] = self.rec
        self._event("resume" if resumed else "start",
                    f"backend {self.backend}" + (f"; interrupted step {inflight.get('step')}{inflight.get('sfx', '')}"
                                                 if inflight else ""))
        self._write_pid()
        self._save()
        self.log(f"{self.run_id}: {'resuming' if resumed else 'starting'} (backend {self.backend}, "
                 f"ama-mcp {__version__})")
        try:
            for stage in STAGES:
                if stage in st["completed_stages"]:
                    continue
                self.stage = stage
                self._occ = {}
                st["current_stage"] = stage
                self._save()
                self.log(f"{self.run_id}: --- {PHASES[stage]} ---")
                getattr(self, f"_stage_{stage}")()
                st["completed_stages"].append(stage)
                st["snapshots"][stage] = copy.deepcopy(self.rec)
                self._save()
                paused = self._pause_after(stage)
                if paused:
                    # held here (the study held the pilots after G2 until the grid half was validated)
                    self.rec["paused"] = paused
                    st["status"] = "paused"
                    st["current_stage"] = None
                    self._event("paused", paused)
                    self.log(f"{self.run_id}: paused {paused}; `ama resume` continues")
                    return self.rec
            self.rec["completed"] = True
            st["status"] = "completed"
            st["current_stage"] = None
            self._event("completed")
            self.log(f"{self.run_id}: COMPLETE")
        except StopRun as s:
            self.rec["stopped_at"] = s.stage
            self.rec["reason"] = s.reason[-1500:]
            st["status"] = "stopped"
            self._event("stopped", f"{s.stage}: {s.reason[-300:]}")
            self.log(f"{self.run_id}: stopped at {s.stage}")
        except Exception as e:  # noqa: BLE001
            st["status"] = "error"
            st["error"] = f"{type(e).__name__}: {e}"[-1500:]
            st["error_traceback"] = traceback.format_exc()[-4000:]
            self.rec["stopped_at"] = "error"
            self.rec["reason"] = str(e)[-1500:]
            if isinstance(e, RoleFailed) and e.key:
                st["journal"].pop(e.key, None)  # a resume retries the role
            self._event("error", st["error"])
            self.log(f"{self.run_id}: error {st['error']} -- `ama resume` retries from here")
        except BaseException as e:
            st["status"] = "interrupted"
            st["error"] = f"interrupted: {type(e).__name__}: {e}"[-500:]
            self._event("interrupted", st["error"])
            self.log(f"{self.run_id}: {st['error']} -- `ama resume` continues from here")
            self._save()
            raise
        finally:
            st["finished_at"] = now_iso()
            self._save()
        if st["status"] in ("completed", "stopped"):
            self.write_result()
        return self.rec

    def write_result(self) -> dict[str, Any]:
        """RUN/ama_result.json: the record, the survival headline(s) and the paper's measures."""
        try:
            from .metrics import compute_paper_metrics  # lazy; another module owns the measures
            metrics: Any = compute_paper_metrics(self.run_dir)
        except Exception as e:  # noqa: BLE001
            metrics = {"error": f"metrics unavailable: {type(e).__name__}: {e}"}
        result = {
            "run_id": self.run_id,
            "status": self.state["status"],
            "completed": bool(self.rec.get("completed")),
            "stopped_at": self.rec.get("stopped_at"),
            "reason": self.rec.get("reason"),
            "record": self.rec,
            "survival_headline": read_json(self.run_dir / "grid_survival_headline.json"),
            "companion_survival_headline": read_json(self.run_dir / "grid_survival_companion_headline.json"),
            "report": "report.md" if (self.run_dir / "report.md").exists() else None,
            "metrics": metrics,
            "backend": self.backend,
            "models": {s: self.model_for(s) for s in STEPS},
            "agent_sessions": len(self.state.get("roles") or []),
            "ama_mcp_version": __version__,
            "written_at": now_iso(),
        }
        write_json_atomic(self.run_dir / RESULT_FILE, result)
        return result


# ------------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ama_mcp.orchestrator",
                                 description="Run or resume one AMA run directory.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run or resume the paper in RUN_DIR")
    r.add_argument("--run-dir", required=True)
    r.add_argument("--from-stage", choices=STAGES, default=None,
                   help="run this stage and every later stage again with fresh agents")
    a = ap.parse_args(argv)
    try:
        orch = Orchestrator(a.run_dir)
        orch.run(from_stage=a.from_stage)
    except AlreadyRunning as e:
        print(str(e), file=sys.stderr)
        return 3
    except (Interrupted, KeyboardInterrupt):
        return 130
    except (FileNotFoundError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    status = orch.state["status"]
    if not orch.echo:  # the log lines were not shown; say how it ended
        detail = orch.state.get("error") if status == "error" else orch.rec.get("stopped_at") or orch.rec.get("paused")
        print(f"{orch.run_id}: {status}" + (f" ({detail})" if detail else "") + f"; log {orch.run_dir / LOG_FILE}",
              file=sys.stderr)
    return 0 if status in ("completed", "stopped", "paused") else 1


if __name__ == "__main__":
    raise SystemExit(main())
