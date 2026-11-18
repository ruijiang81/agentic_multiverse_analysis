"""Agent runners: launch one AMA role as a fresh, non-interactive CLI agent session.

Every role of the pipeline (package finder, builder, config drafter, reproducer, fork
brainstormer, auditors, implementer, reporter, ...) runs as an independent agent session
started through the user's own command-line login:

* ``claude`` -- Claude Code CLI, ``claude -p`` (print mode), transcript as stream-json;
* ``codex``  -- OpenAI Codex CLI, ``codex exec --json``;
* ``fake``   -- an in-process stand-in for tests (a Python callable plays the agent).

Each session starts in ``workdir`` with no memory of any other session, can read and write
files, run shell commands and reach the web, and has its full transcript written to
``log_path``. The orchestrator (not this module) renders the role prompt, decides what the
role must leave behind, and runs the gate scripts.

SECURITY. Role sessions run code from third-party replication packages with broad
permissions (the Claude runner uses ``--permission-mode bypassPermissions`` and the Codex
runner ``--dangerously-bypass-approvals-and-sandbox`` by default). Run the whole tool inside
a container or virtual machine that holds nothing you would not hand to such code.

Isolation of each role session from the caller's own agent setup:

* no MCP servers at all (Claude: ``--strict-mcp-config`` with no ``--mcp-config``; Codex:
  every server in the user's config is switched off with ``-c mcp_servers.<name>.enabled=false``),
  so a role never loads this MCP server again (no recursion) nor the user's other servers;
* no cross-session memory (Claude: ``CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`` and, when the CLI
  supports it, ``--safe-mode``, which also drops user CLAUDE.md files, hooks, skills and
  plugins; Codex: ``features.memories=false``, ``features.apps=false``, ``features.plugins=false``);
* the environment variables that mark a parent Claude Code / Codex session are removed, so a
  nested call works when the MCP server itself runs inside Claude Code or Codex.

Timeouts kill the session's whole process group plus every descendant found at that moment
(processes that left the group with setsid included) and return ``timed_out=True``. A session
that ends on its own is NOT cleaned up after: a role may deliberately leave a long grid
computation running in the background for the orchestrator to wait on, as the study did.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "ROLES",
    "DEFAULT_MODELS",
    "ERROR_CODES",
    "RoleResult",
    "AgentRunner",
    "ClaudeRunner",
    "CodexRunner",
    "FakeRunner",
    "make_runner",
    "check_backends",
    "default_model",
    "base_role",
    "clean_env",
    "redact",
    "parse_claude_stream",
    "parse_codex_events",
]

# --------------------------------------------------------------------------------------
# Roles and model assignment
# --------------------------------------------------------------------------------------

#: Every role (step) of the pipeline, in pipeline order.
ROLES: tuple[str, ...] = (
    "P", "B", "SR", "BF", "C", "CR", "0", "G0A", "1", "2", "3", "3F", "4", "3C", "4C", "3CF", "5", "6",
)

# The study ran these roles on the efficient model and every other role on the frontier
# model (the workflow's MODEL table: P, C, 0, 3, 3F, 6 -> 'sonnet'; default 'opus').
_EFFICIENT_ROLES = frozenset({"P", "C", "0", "3", "3F", "6"})

#: backend -> role -> model. ``None`` means "do not pass a model flag" (account default).
DEFAULT_MODELS: dict[str, dict[str, str | None]] = {
    "claude": {r: ("sonnet" if r in _EFFICIENT_ROLES else "opus") for r in ROLES},
    "codex": {r: None for r in ROLES},
    "fake": {r: None for r in ROLES},
}

_FALLBACK_MODEL: dict[str, str | None] = {"claude": "opus", "codex": None, "fake": None}


def base_role(role: str) -> str:
    """Strip a label down to its step: ``'step4_r2'`` -> ``'4'``, ``'BF_g0_audit'`` -> ``'BF'``."""
    r = str(role).strip()
    if r.lower().startswith("step"):
        r = r[4:]
    return r.split("_", 1)[0].split(" ", 1)[0]


def default_model(backend: str, role: str) -> str | None:
    """The study's model for ``role`` on ``backend`` (``None`` = the account's default model)."""
    table = DEFAULT_MODELS.get(backend, {})
    step = base_role(role)
    if step in table:
        return table[step]
    return _FALLBACK_MODEL.get(backend)


# --------------------------------------------------------------------------------------
# Result type
# --------------------------------------------------------------------------------------

#: Values of ``RoleResult.error`` (``None`` when the session ended normally with exit 0).
ERROR_CODES: dict[str, str] = {
    "not_installed": "the CLI executable was not found on PATH (exit_code 127)",
    "timeout": "the session hit timeout_s and its process group was killed",
    "rate_limited": "the account hit a usage or rate limit; retry after the reset time",
    "auth": "the CLI is not logged in or its credentials were rejected",
    "refused_root": "Claude Code refused bypassPermissions as root outside a recognised sandbox",
    "no_result": "the session ended without a final result event in its transcript",
    "agent_error": "the session ended with an error or a non-zero exit code",
}


@dataclass
class RoleResult:
    """Outcome of one role session.

    exit_code: the CLI's exit code (negative = killed by that signal; 127 = CLI not found).
    last_message: the agent's final reply text ("" if none).
    log_path: where the transcript was written (JSON lines for claude/codex).
    duration_s: wall-clock seconds.
    timed_out: True when the session was killed at ``timeout_s``.
    error: ``None`` for a normal end, else one of :data:`ERROR_CODES`.
    meta: backend details for provenance (session id, model reported by the CLI, token
        usage, cost when the CLI reports it, number of tool calls, skipped extra dirs ...).
    """

    exit_code: int
    last_message: str
    log_path: str
    duration_s: float
    timed_out: bool
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None


# --------------------------------------------------------------------------------------
# Environment and redaction helpers
# --------------------------------------------------------------------------------------

# Variables a parent Claude Code / Codex / Agent SDK session sets for its own children. Left
# in place they make a nested CLI believe it is a sub-process of that session (older Claude
# Code versions refuse to start; CLAUDE_EFFORT would silently carry the parent's effort).
_PARENT_SESSION_VARS = frozenset({
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SAFE_MODE",
    "CLAUDE_CODE_SIMPLE",
    "CLAUDE_CODE_RESTRICTED",
    "CLAUDE_CODE_ACTION",
    "CLAUDE_CODE_AGENT",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "CODEX_THREAD_ID",
    "CODEX_CI",
})
_PARENT_SESSION_PREFIXES = (
    "CLAUDE_CODE_SESSION",
    "CLAUDE_CODE_MESSAGING",
    "CLAUDE_CODE_SSE",
    "CLAUDE_CODE_IDE",
    "CLAUDE_CODE_BRIDGE",
    "CLAUDE_AGENT_SDK",
)


def clean_env(base: Mapping[str, str] | None = None, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy of the environment without parent-session markers, plus ``extra``.

    Credentials and provider settings the user configured (ANTHROPIC_API_KEY,
    CLAUDE_CODE_USE_BEDROCK, OPENAI_API_KEY, CODEX_HOME, proxies ...) are kept.
    """
    env = dict(os.environ if base is None else base)
    for k in list(env):
        if k in _PARENT_SESSION_VARS or k.startswith(_PARENT_SESSION_PREFIXES):
            del env[k]
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"\bsk-[A-Za-z0-9_*.\-]+"), "sk-<redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*"), "<token>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<id>"),
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "<token>"),
)


def redact(text: str) -> str:
    """Remove e-mail addresses, API keys, JWTs, UUIDs and other long tokens from ``text``."""
    out = str(text)
    for pat, rep in _REDACTIONS:
        out = pat.sub(rep, out)
    return out


# --------------------------------------------------------------------------------------
# Process handling
# --------------------------------------------------------------------------------------


def _descendants(pid: int) -> list[int]:
    """All descendant pids of ``pid`` right now (``ps`` snapshot; [] if ps is unavailable)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="], capture_output=True,
                             text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            c, p = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(p, []).append(c)
    found: list[int] = []
    stack = [pid]
    while stack:
        for c in children.get(stack.pop(), []):
            if c not in found and c != pid:
                found.append(c)
                stack.append(c)
    return found


def _signal_all(pgid: int, pids: Sequence[int], sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    for p in pids:
        try:
            os.kill(p, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _kill_tree(proc: subprocess.Popen, grace_s: float) -> None:
    """SIGTERM the session's process group and descendants, then SIGKILL what is left."""
    if os.name != "posix":  # pragma: no cover - Windows fallback
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            proc.kill()
        return
    pgid = proc.pid  # the session leader: started with start_new_session=True
    pids = _descendants(proc.pid)
    _signal_all(pgid, pids, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace_s)
    while time.monotonic() < deadline:
        if proc.poll() is not None and not any(_alive(p) for p in pids):
            break
        time.sleep(0.2)
    pids = sorted(set(pids) | set(_descendants(proc.pid)))
    _signal_all(pgid, pids, signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie still answers signal 0; treat it as gone
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().split(b")")[-1].split()[0] != b"Z"
    except OSError:
        return True


def _run_process(cmd: Sequence[str], *, cwd: Path, env: Mapping[str, str], stdin_text: str,
                 stdout_path: Path, stderr_path: Path, timeout_s: float | None,
                 kill_grace_s: float) -> tuple[int, bool, float]:
    """Run ``cmd`` in its own session with stdin from ``stdin_text``; stdout/stderr to files."""
    start = time.monotonic()
    timed_out = False
    kw: dict[str, Any] = {}
    if os.name == "posix":
        kw["start_new_session"] = True
    else:  # pragma: no cover
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err, tempfile.TemporaryFile() as fin:
        fin.write(stdin_text.encode("utf-8"))
        fin.flush()
        fin.seek(0)
        proc = subprocess.Popen(list(cmd), cwd=str(cwd), env=dict(env), stdin=fin, stdout=out,
                                stderr=err, **kw)
        try:
            rc = proc.wait(timeout=timeout_s if timeout_s and timeout_s > 0 else None)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc, kill_grace_s)
            rc = proc.wait()
        except BaseException:
            # the orchestrator is going down (Ctrl-C, cancelled task): take the session with it
            _kill_tree(proc, min(kill_grace_s, 5.0))
            proc.wait()
            raise
    return rc, timed_out, time.monotonic() - start


def _rotate(path: Path) -> None:
    """Never overwrite an earlier transcript: move ``path`` to ``path.<n>`` first."""
    if not path.exists():
        return
    n = 1
    while path.with_name(f"{path.name}.{n}").exists():
        n += 1
    path.rename(path.with_name(f"{path.name}.{n}"))


def _tail(path: Path, n: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _existing_dirs(dirs: Sequence[Path | str]) -> tuple[list[Path], list[str]]:
    keep, skipped = [], []
    for d in dirs or ():
        p = Path(d).expanduser()
        if p.is_dir():
            p = p.resolve()
            if p not in keep:
                keep.append(p)
        else:
            skipped.append(str(d))
    return keep, skipped


_RATE_RE = re.compile(r"usage[ _-]limit|rate[ _-]?limit|hit your [\w -]{0,20}limit|limit[ _-]reached|"
                      r"too many requests|quota exceeded", re.I)
_AUTH_RE = re.compile(r"not logged in|please run /login|run `?claude (auth )?login|invalid api key|"
                      r"authentication[_ ]error|oauth token (has )?expired|unauthorized|"
                      r"codex login|log in again|credentials? (are )?(missing|invalid|expired)", re.I)
_ROOT_RE = re.compile(r"cannot be used with root", re.I)


def _classify(rc: int, timed_out: bool, ended_ok: bool, has_result: bool, text: str) -> str | None:
    if timed_out:
        return "timeout"
    if rc == 0 and ended_ok:
        return None
    if _ROOT_RE.search(text):
        return "refused_root"
    if _RATE_RE.search(text):
        return "rate_limited"
    if _AUTH_RE.search(text):
        return "auth"
    if not has_result:
        return "no_result"
    return "agent_error"


# --------------------------------------------------------------------------------------
# Transcript parsers
# --------------------------------------------------------------------------------------


def _json_lines(path: Path):
    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_claude_stream(path: Path) -> dict[str, Any]:
    """Summarise a ``claude -p --output-format stream-json`` transcript."""
    info: dict[str, Any] = {"has_result": False, "is_error": False, "result_text": None,
                            "last_assistant_text": "", "tool_calls": 0, "init": {}}
    for ev in _json_lines(path):
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            info["init"] = {
                "model": ev.get("model"),
                "permission_mode": ev.get("permissionMode"),
                "cli_version": ev.get("claude_code_version"),
                "mcp_servers": [s.get("name") for s in ev.get("mcp_servers") or [] if isinstance(s, dict)],
                "n_tools": len(ev.get("tools") or []),
                "web_tools": sorted(x for x in ev.get("tools") or [] if x in ("WebFetch", "WebSearch")),
            }
            info["session_id"] = ev.get("session_id")
        elif t == "assistant":
            content = (ev.get("message") or {}).get("content") or []
            texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            info["tool_calls"] += sum(1 for c in content if isinstance(c, dict) and c.get("type") == "tool_use")
            if any(x.strip() for x in texts):
                info["last_assistant_text"] = "\n".join(x for x in texts if x.strip())
        elif t == "result":
            info["has_result"] = True
            info["is_error"] = bool(ev.get("is_error"))
            info["subtype"] = ev.get("subtype")
            res = ev.get("result")
            info["result_text"] = res if isinstance(res, str) else None
            if isinstance(ev.get("errors"), list) and ev["errors"]:
                info["errors"] = [str(e)[:500] for e in ev["errors"]][:5]
            for k in ("session_id", "num_turns", "total_cost_usd", "duration_ms", "duration_api_ms"):
                if ev.get(k) is not None:
                    info[k] = ev[k]
            if isinstance(ev.get("usage"), dict):
                info["usage"] = {k: v for k, v in ev["usage"].items() if isinstance(v, (int, float))}
        # Claude Code marks API failures with structured fields (e.g. api_error "usage_limit_reached",
        # api_error_type "rate_limit_error", api_error_status 429) on the result event and on the error message
        # before it; the human-readable text ("You've hit your 5-hour limit") changes between CLI versions.
        for k in ("api_error", "api_error_type", "api_error_status"):
            if ev.get(k) is not None:
                info[k] = ev[k]
    return info


def parse_codex_events(path: Path) -> dict[str, Any]:
    """Summarise a ``codex exec --json`` event log."""
    info: dict[str, Any] = {"has_result": False, "failed": False, "last_agent_message": "",
                            "errors": [], "commands": 0, "web_searches": 0, "usage": {}}
    for ev in _json_lines(path):
        t = ev.get("type")
        if t == "thread.started":
            info["thread_id"] = ev.get("thread_id")
        elif t == "item.completed":
            item = ev.get("item") or {}
            it = item.get("type")
            if it == "agent_message" and isinstance(item.get("text"), str):
                info["last_agent_message"] = item["text"]
            elif it == "command_execution":
                info["commands"] += 1
            elif it == "web_search":
                info["web_searches"] += 1
        elif t == "turn.completed":
            info["has_result"] = True
            for k, v in (ev.get("usage") or {}).items():
                if isinstance(v, (int, float)):
                    info["usage"][k] = info["usage"].get(k, 0) + v
        elif t == "turn.failed":
            info["failed"] = True
            msg = (ev.get("error") or {}).get("message")
            if msg:
                info["errors"].append(str(msg)[:1000])
        elif t == "error":
            if ev.get("message"):
                info["errors"].append(str(ev["message"])[:1000])
    return info


# --------------------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------------------


class AgentRunner:
    """Launches one role as a fresh, non-interactive agent session."""

    backend: str = ""

    def run(self, prompt: str, *, workdir: Path, role: str, label: str, model: str | None,
            log_path: Path, timeout_s: int | None = None, extra_dirs: Sequence[Path] = ()) -> RoleResult:
        raise NotImplementedError

    def default_model(self, role: str) -> str | None:
        return default_model(self.backend, role)

    def describe(self) -> dict[str, Any]:
        """Settings worth recording with a run (no secrets)."""
        return {"backend": self.backend}

    # shared plumbing for the CLI runners ------------------------------------------------
    def _prepare(self, workdir: Path, log_path: Path) -> tuple[Path, Path, Path]:
        wd = Path(workdir).expanduser().resolve()
        if not wd.is_dir():
            raise FileNotFoundError(f"workdir does not exist: {wd}")
        lp = Path(log_path).expanduser()
        if not lp.is_absolute():
            lp = (Path.cwd() / lp)
        lp.parent.mkdir(parents=True, exist_ok=True)
        err = lp.with_name(lp.name + ".stderr")
        _rotate(lp)
        _rotate(err)
        return wd, lp, err

    def _not_installed(self, exe: str, lp: Path, start: float) -> RoleResult:
        msg = f"{self.backend}: executable {exe!r} not found on PATH"
        lp.write_text(json.dumps({"type": "runner_error", "error": "not_installed", "message": msg}) + "\n")
        return RoleResult(127, "", str(lp), time.monotonic() - start, False, "not_installed",
                          {"backend": self.backend, "message": msg})


_HELP_CACHE: dict[str, str] = {}
_HELP_LOCK = threading.Lock()


def _cli_help(exe: str, env: Mapping[str, str]) -> str:
    with _HELP_LOCK:
        if exe not in _HELP_CACHE:
            try:
                r = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=60,
                                   env=dict(env), stdin=subprocess.DEVNULL)
                _HELP_CACHE[exe] = (r.stdout or "") + (r.stderr or "")
            except (OSError, subprocess.SubprocessError):
                _HELP_CACHE[exe] = ""
        return _HELP_CACHE[exe]


class ClaudeRunner(AgentRunner):
    """``claude -p`` (Claude Code print mode), one fresh session per role.

    permission_mode: "bypassPermissions" (default: edits and shell commands never prompt) or
        any other Claude Code mode; with a non-bypass mode the session gets
        ``--allowedTools`` = ``allowed_tools`` (default: shell, file and web tools).
    safe_mode: pass ``--safe-mode`` (no user CLAUDE.md, hooks, skills, plugins, MCP servers,
        auto-memory). ``None`` = use it when the installed CLI lists it in ``--help``.
    effort: ``--effort`` level, or ``None`` for the CLI's default.
    disallowed_tools: ``--disallowedTools``; default :attr:`DEFAULT_DISALLOWED_TOOLS`, the
        tools that reach outside the session (message other local sessions, push
        notifications to the user's devices, schedule cron or remote triggers, publish).
        Names the installed CLI does not have are harmless. ``()`` passes none.
    extra_args: appended verbatim to every command line.
    env: extra environment variables for the sessions.
    """

    backend = "claude"
    DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = (
        "Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep", "LS",
        "WebFetch", "WebSearch", "TodoWrite",
    )
    DEFAULT_DISALLOWED_TOOLS: tuple[str, ...] = (
        "SendMessage", "ListAgents", "PushNotification", "RemoteTrigger", "CronCreate", "CronDelete",
        "CronList", "ScheduleWakeup", "ShareOnboardingGuide", "PublishPlugin", "DesignSync",
    )

    def __init__(self, executable: str = "claude", *, permission_mode: str = "bypassPermissions",
                 safe_mode: bool | None = None, effort: str | None = None,
                 allowed_tools: Sequence[str] | None = None,
                 disallowed_tools: Sequence[str] | None = None, extra_args: Sequence[str] = (),
                 env: Mapping[str, str] | None = None, kill_grace_s: float = 10.0) -> None:
        self.executable = executable
        self.permission_mode = permission_mode
        self.safe_mode = safe_mode
        self.effort = effort
        self.allowed_tools = tuple(allowed_tools) if allowed_tools is not None else None
        self.disallowed_tools = (tuple(disallowed_tools) if disallowed_tools is not None
                                 else self.DEFAULT_DISALLOWED_TOOLS)
        self.extra_args = tuple(extra_args)
        self.env = dict(env or {})
        self.kill_grace_s = kill_grace_s

    def build_env(self) -> dict[str, str]:
        # roles share the run directory as their working directory; Claude Code's auto-memory
        # is keyed by that directory, so without this one role could read another's notes
        extra = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
        extra.update(self.env)
        return clean_env(extra=extra)

    def _use_safe_mode(self, exe: str, env: Mapping[str, str]) -> bool:
        if self.safe_mode is not None:
            return self.safe_mode
        return "--safe-mode" in _cli_help(exe, env)

    def build_command(self, exe: str, *, model: str | None, extra_dirs: Sequence[Path],
                      safe_mode: bool) -> list[str]:
        cmd = [exe, "-p", "--output-format", "stream-json", "--verbose",
               "--permission-mode", self.permission_mode, "--strict-mcp-config"]
        if self.permission_mode != "bypassPermissions":
            tools = self.allowed_tools if self.allowed_tools is not None else self.DEFAULT_ALLOWED_TOOLS
            if tools:
                cmd += ["--allowedTools", ",".join(tools)]
        if self.disallowed_tools:
            cmd += ["--disallowedTools", ",".join(self.disallowed_tools)]
        if safe_mode:
            cmd.append("--safe-mode")
        if model:
            cmd += ["--model", model]
        if self.effort:
            cmd += ["--effort", self.effort]
        for d in extra_dirs:
            cmd += ["--add-dir", str(d)]
        cmd += list(self.extra_args)
        return cmd  # the prompt goes in on stdin (never as a positional after --add-dir)

    def run(self, prompt: str, *, workdir: Path, role: str, label: str, model: str | None,
            log_path: Path, timeout_s: int | None = None, extra_dirs: Sequence[Path] = ()) -> RoleResult:
        start = time.monotonic()
        wd, lp, err = self._prepare(workdir, log_path)
        env = self.build_env()
        exe = shutil.which(self.executable, path=env.get("PATH"))
        if exe is None:
            return self._not_installed(self.executable, lp, start)
        dirs, skipped = _existing_dirs(extra_dirs)
        safe = self._use_safe_mode(exe, env)
        cmd = self.build_command(exe, model=model, extra_dirs=dirs, safe_mode=safe)
        rc, timed_out, dur = _run_process(cmd, cwd=wd, env=env, stdin_text=prompt, stdout_path=lp,
                                          stderr_path=err, timeout_s=timeout_s,
                                          kill_grace_s=self.kill_grace_s)
        info = parse_claude_stream(lp)
        last = info["result_text"] if info["result_text"] is not None else info["last_assistant_text"]
        stderr_tail = _tail(err)
        api = " ".join(f"{k}={info[k]}" for k in ("api_error", "api_error_type") if info.get(k))
        if info.get("api_error_status") == 429:
            api += " rate_limit (HTTP 429)"
        text = " ".join([last or "", stderr_tail, " ".join(info.get("errors") or []), api])
        error = _classify(rc, timed_out, info["has_result"] and not info["is_error"], info["has_result"], text)
        meta: dict[str, Any] = {
            "backend": self.backend, "role": role, "label": label, "model_requested": model,
            "safe_mode": safe, "permission_mode": self.permission_mode,
            "stderr_path": str(err), "argv": [Path(cmd[0]).name] + cmd[1:],
        }
        for k in ("session_id", "num_turns", "total_cost_usd", "duration_ms", "usage", "subtype",
                  "tool_calls", "init", "errors"):
            if info.get(k) not in (None, {}, []):
                meta[k] = info[k]
        if skipped:
            meta["skipped_extra_dirs"] = skipped
        if error and not last:
            last = stderr_tail.strip()[-2000:]
        return RoleResult(rc, last or "", str(lp), dur, timed_out, error, meta)

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "executable": self.executable,
                "permission_mode": self.permission_mode, "safe_mode": self.safe_mode,
                "effort": self.effort, "disallowed_tools": list(self.disallowed_tools),
                "extra_args": list(self.extra_args)}


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class CodexRunner(AgentRunner):
    """``codex exec`` (OpenAI Codex CLI), one fresh session per role.

    sandbox: "danger-full-access" (default; ``--dangerously-bypass-approvals-and-sandbox``:
        no approval prompts, no Codex sandbox, full network; meant for an externally
        sandboxed container/VM) or "workspace-write" (Codex's own sandbox: writes limited to
        workdir, extra_dirs and temp; network switched on with
        ``sandbox_workspace_write.network_access=true``; on Linux this needs bubblewrap).
    web_search: value for Codex's top-level ``web_search`` setting ("live", "cached",
        "indexed", "disabled"); ``None`` leaves the user's setting.
    reasoning_effort: ``model_reasoning_effort`` override, or ``None``.
    profile: a Codex config profile (``-p``), or ``None``.
    disable_user_mcp: switch off every MCP server in the user's Codex config for the session.
    config_overrides: extra ``key=value`` strings passed as ``-c``.
    """

    backend = "codex"

    def __init__(self, executable: str = "codex", *, sandbox: str = "danger-full-access",
                 web_search: str | None = "live", reasoning_effort: str | None = None,
                 profile: str | None = None, disable_user_mcp: bool = True,
                 config_overrides: Sequence[str] = (), extra_args: Sequence[str] = (),
                 env: Mapping[str, str] | None = None, kill_grace_s: float = 10.0) -> None:
        if sandbox not in ("danger-full-access", "workspace-write"):
            raise ValueError("sandbox must be 'danger-full-access' or 'workspace-write'")
        self.executable = executable
        self.sandbox = sandbox
        self.web_search = web_search
        self.reasoning_effort = reasoning_effort
        self.profile = profile
        self.disable_user_mcp = disable_user_mcp
        self.config_overrides = tuple(config_overrides)
        self.extra_args = tuple(extra_args)
        self.env = dict(env or {})
        self.kill_grace_s = kill_grace_s
        self.mcp_list_error: str | None = None

    def build_env(self) -> dict[str, str]:
        return clean_env(extra=self.env)

    def user_mcp_servers(self, exe: str, env: Mapping[str, str]) -> list[str]:
        """Names of the enabled MCP servers in the user's Codex configuration."""
        cmd = [exe, "mcp", "list", "--json"]
        if self.profile:
            cmd[1:1] = ["-p", self.profile]
        self.mcp_list_error = None  # set when the listing fails: then no server is turned off
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=dict(env),
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            self.mcp_list_error = f"`codex mcp list --json` could not run: {type(e).__name__}"
            return []
        if r.returncode != 0:
            self.mcp_list_error = f"`codex mcp list --json` exited {r.returncode}"
            return []
        try:
            data = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            self.mcp_list_error = "`codex mcp list --json` did not print JSON"
            return []
        if isinstance(data, dict):
            data = data.get("servers") or data.get("mcp_servers") or []
        names = []
        for s in data if isinstance(data, list) else []:
            if isinstance(s, dict) and s.get("name") and s.get("enabled", True):
                names.append(str(s["name"]))
        return names

    def build_command(self, exe: str, *, workdir: Path, model: str | None, extra_dirs: Sequence[Path],
                      last_message_path: Path, mcp_servers: Sequence[str] = ()) -> tuple[list[str], list[str]]:
        cmd = [exe, "exec", "--json", "--color", "never", "--skip-git-repo-check",
               "--cd", str(workdir), "-o", str(last_message_path)]
        if self.sandbox == "danger-full-access":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd += ["-s", "workspace-write", "-c", "sandbox_workspace_write.network_access=true",
                    "-c", 'approval_policy="never"']
        if self.web_search:
            cmd += ["-c", f'web_search="{self.web_search}"']
        if self.reasoning_effort:
            cmd += ["-c", f'model_reasoning_effort="{self.reasoning_effort}"']
        # role independence: no cross-session memories, no ChatGPT apps/connectors, no plugins
        cmd += ["-c", "features.memories=false", "-c", "features.apps=false", "-c", "features.plugins=false"]
        not_disabled = []
        for name in mcp_servers:
            if _SAFE_NAME.match(name):
                cmd += ["-c", f"mcp_servers.{name}.enabled=false"]
            else:
                not_disabled.append(name)
        if self.profile:
            cmd += ["-p", self.profile]
        if model:
            cmd += ["-m", model]
        for d in extra_dirs:
            cmd += ["--add-dir", str(d)]
        for kv in self.config_overrides:
            cmd += ["-c", kv]
        cmd += list(self.extra_args)
        cmd.append("-")  # read the prompt from stdin
        return cmd, not_disabled

    def run(self, prompt: str, *, workdir: Path, role: str, label: str, model: str | None,
            log_path: Path, timeout_s: int | None = None, extra_dirs: Sequence[Path] = ()) -> RoleResult:
        start = time.monotonic()
        wd, lp, err = self._prepare(workdir, log_path)
        last_path = lp.with_name(lp.name + ".last_message.txt")
        _rotate(last_path)
        env = self.build_env()
        exe = shutil.which(self.executable, path=env.get("PATH"))
        if exe is None:
            return self._not_installed(self.executable, lp, start)
        dirs, skipped = _existing_dirs(extra_dirs)
        servers = self.user_mcp_servers(exe, env) if self.disable_user_mcp else []
        cmd, not_disabled = self.build_command(exe, workdir=wd, model=model, extra_dirs=dirs,
                                               last_message_path=last_path, mcp_servers=servers)
        rc, timed_out, dur = _run_process(cmd, cwd=wd, env=env, stdin_text=prompt, stdout_path=lp,
                                          stderr_path=err, timeout_s=timeout_s,
                                          kill_grace_s=self.kill_grace_s)
        info = parse_codex_events(lp)
        try:
            last = last_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            last = ""
        if not last:
            last = info["last_agent_message"]
        stderr_tail = _tail(err)
        text = " ".join([" ".join(info["errors"]), stderr_tail])
        ended_ok = info["has_result"] and not info["failed"]
        error = _classify(rc, timed_out, ended_ok, info["has_result"] or bool(last), text)
        meta: dict[str, Any] = {
            "backend": self.backend, "role": role, "label": label, "model_requested": model,
            "sandbox": self.sandbox, "web_search": self.web_search,
            "stderr_path": str(err), "last_message_path": str(last_path),
            "argv": [Path(cmd[0]).name] + cmd[1:],
            "thread_id": info.get("thread_id"), "usage": info["usage"],
            "commands": info["commands"], "web_searches": info["web_searches"],
        }
        if info["errors"]:
            meta["errors"] = info["errors"][-5:]
        if servers:
            meta["mcp_servers_disabled"] = [s for s in servers if s not in not_disabled]
        if not_disabled:
            meta["mcp_servers_not_disabled"] = not_disabled
        if self.disable_user_mcp and self.mcp_list_error:
            meta["mcp_list_error"] = self.mcp_list_error
        if skipped:
            meta["skipped_extra_dirs"] = skipped
        if error and not last:
            last = (" ".join(info["errors"]) or stderr_tail).strip()[-2000:]
        return RoleResult(rc, last or "", str(lp), dur, timed_out, error, meta)

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "executable": self.executable, "sandbox": self.sandbox,
                "web_search": self.web_search, "reasoning_effort": self.reasoning_effort,
                "profile": self.profile, "config_overrides": list(self.config_overrides),
                "extra_args": list(self.extra_args)}


Handler = Callable[[str, str, Path, Path], Any]


class FakeRunner(AgentRunner):
    """Test double: ``handler(role, prompt, workdir, log_path)`` plays the agent.

    The handler returns an int exit code (contract), or ``(exit_code, last_message)``, or a
    complete :class:`RoleResult`. Raising ``TimeoutError`` simulates a timed-out session.
    Every call is recorded in ``self.calls``.
    """

    backend = "fake"

    def __init__(self, handler: Handler | None = None) -> None:
        self.handler: Handler = handler or (lambda role, prompt, workdir, log_path: 0)
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def run(self, prompt: str, *, workdir: Path, role: str, label: str, model: str | None,
            log_path: Path, timeout_s: int | None = None, extra_dirs: Sequence[Path] = ()) -> RoleResult:
        start = time.monotonic()
        wd = Path(workdir)
        lp = Path(log_path)
        lp.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.calls.append({"role": role, "label": label, "model": model, "prompt": prompt,
                               "workdir": str(wd), "log_path": str(lp), "timeout_s": timeout_s,
                               "extra_dirs": [str(d) for d in extra_dirs]})
        timed_out = False
        last = ""
        try:
            out = self.handler(role, prompt, wd, lp)
        except TimeoutError:
            out, timed_out = -int(signal.SIGKILL), True
        if isinstance(out, RoleResult):
            return out
        if isinstance(out, tuple):
            code, last = int(out[0]), str(out[1])
        else:
            code = int(out) if out is not None else 0
        if not lp.exists():
            lp.write_text(json.dumps({"type": "fake_session", "role": role, "label": label,
                                      "model": model, "exit_code": code, "timed_out": timed_out}) + "\n")
        if not last:
            last = f"fake {role} session ended with exit code {code}"
        error = "timeout" if timed_out else (None if code == 0 else "agent_error")
        return RoleResult(code, last, str(lp), time.monotonic() - start, timed_out, error,
                          {"backend": self.backend, "role": role, "label": label, "model_requested": model})


def make_runner(backend: str, **kw: Any) -> AgentRunner:
    """``"claude"`` | ``"codex"`` | ``"fake"`` -> a runner; ``kw`` go to its constructor."""
    b = str(backend).strip().lower()
    if b == "claude":
        return ClaudeRunner(**kw)
    if b == "codex":
        return CodexRunner(**kw)
    if b == "fake":
        return FakeRunner(**kw)
    raise ValueError(f"unknown backend {backend!r}: expected 'claude', 'codex' or 'fake'")


# --------------------------------------------------------------------------------------
# Backend check
# --------------------------------------------------------------------------------------


def _quick(cmd: Sequence[str], env: Mapping[str, str], timeout: float = 60,
           stdin_text: str | None = None) -> tuple[int | None, str]:
    try:
        r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, env=dict(env),
                           input=stdin_text, stdin=None if stdin_text is not None else subprocess.DEVNULL)
        return r.returncode, (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    except subprocess.TimeoutExpired:
        return None, "timed out"
    except OSError as e:
        return None, f"could not run: {e.__class__.__name__}"


def _first_line(s: str) -> str:
    for line in s.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _check_claude(executable: str, probe: bool, timeout: float) -> dict[str, Any]:
    env = clean_env(extra={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
    exe = shutil.which(executable, path=env.get("PATH"))
    res: dict[str, Any] = {"installed": exe is not None, "version": None, "logged_in": False, "detail": ""}
    if exe is None:
        res["detail"] = f"'{executable}' not found on PATH; install Claude Code and run 'claude' once to log in"
        return res
    rc, out = _quick([exe, "--version"], env, timeout)
    res["version"] = redact(_first_line(out)) if rc == 0 else None
    rc, out = _quick([exe, "auth", "status", "--json"], env, timeout)
    status = None
    try:
        start = out.index("{")
        status = json.loads(out[start:out.rindex("}") + 1])
    except ValueError:
        status = None
    if isinstance(status, dict) and "loggedIn" in status:
        res["logged_in"] = bool(status.get("loggedIn"))
        bits = [f"{k}={status[k]}" for k in ("authMethod", "apiProvider", "subscriptionType")
                if isinstance(status.get(k), str) and status.get(k)]
        res["detail"] = redact("auth status: " + (", ".join(bits) if bits else "no details"))
    elif probe:
        rc, out = _quick([exe, "-p", "--output-format", "json", "--model", "haiku", "--strict-mcp-config"],
                         env, max(timeout, 120), stdin_text="Reply with the single word OK.")
        ok = rc == 0 and '"is_error":false' in out.replace(" ", "")
        res["logged_in"] = ok
        res["detail"] = "probe session " + ("succeeded" if ok else "failed: " + redact(_first_line(out))[:300])
    else:
        res["logged_in"] = None
        res["detail"] = "this claude version has no 'auth status'; call check_backends(probe=True) to test with one tiny session"
    help_text = _cli_help(exe, env)
    res["safe_mode_supported"] = "--safe-mode" in help_text
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        res["detail"] += ("; running as root: Claude Code refuses bypassPermissions as root unless it "
                          "detects a container (/.dockerenv) or IS_SANDBOX=1 is set")
    return res


def _check_codex(executable: str, probe: bool, timeout: float) -> dict[str, Any]:
    env = clean_env()
    exe = shutil.which(executable, path=env.get("PATH"))
    res: dict[str, Any] = {"installed": exe is not None, "version": None, "logged_in": False, "detail": ""}
    if exe is None:
        res["detail"] = f"'{executable}' not found on PATH; install the Codex CLI and run 'codex login'"
        return res
    rc, out = _quick([exe, "--version"], env, timeout)
    res["version"] = redact(_first_line(out)) if rc == 0 else None
    rc, out = _quick([exe, "login", "status"], env, timeout)
    line = redact(_first_line(out))
    if rc == 0:
        res["logged_in"] = True
    elif rc is not None and re.search(r"not logged in", out, re.I):
        res["logged_in"] = False
    else:
        res["logged_in"] = None
    res["detail"] = f"login status: {line}" if line else "login status: no output"
    if probe and res["logged_in"] is None:
        rc, out = _quick([exe, "exec", "--json", "--skip-git-repo-check", "--color", "never", "-"],
                         env, max(timeout, 180), stdin_text="Reply with the single word OK.")
        res["logged_in"] = rc == 0 and "turn.completed" in out
        res["detail"] += "; probe session " + ("succeeded" if res["logged_in"] else "failed")
    if sys.platform.startswith("linux"):
        rc, out = _quick([exe, "sandbox", "--", "true"], env, 30)
        res["sandbox_available"] = rc == 0
        if rc != 0:
            res["detail"] += ("; Codex's own Linux sandbox is unavailable here (bubblewrap missing or blocked): "
                              "only sandbox='danger-full-access' (the default) works")
    return res


def check_backends(*, probe: bool = False, timeout_s: float = 60,
                   claude_executable: str = "claude", codex_executable: str = "codex") -> dict[str, dict[str, Any]]:
    """Which agent CLIs are installed and logged in.

    Returns ``{"claude": {...}, "codex": {...}}``, each with ``installed`` (bool), ``version``
    (str | None), ``logged_in`` (bool, or None when it cannot be told without a session) and
    ``detail`` (str). Tokens, e-mail addresses and account or organisation ids are never
    returned. ``probe=True`` allows one tiny model call when login cannot be read otherwise.
    """
    return {
        "claude": _check_claude(claude_executable, probe, timeout_s),
        "codex": _check_codex(codex_executable, probe, timeout_s),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(check_backends(), indent=2))
