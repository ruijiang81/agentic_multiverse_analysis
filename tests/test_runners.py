"""Tests for ama_mcp.runners. No real agent session is started: fake ``claude`` and ``codex``
executables on PATH record their command line, stdin and environment and print canned
transcripts."""
from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
import time
from pathlib import Path

import pytest

try:
    from ama_mcp import runners as R
except ImportError:  # running from a source checkout without an install
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from ama_mcp import runners as R


# --------------------------------------------------------------------------------------
# fake CLIs
# --------------------------------------------------------------------------------------

FAKE_CLAUDE = r'''
import json, os, subprocess, sys, time
a = sys.argv[1:]
if a[:1] == ["--help"]:
    print("Usage: claude [options]\n  --strict-mcp-config\n" + ("" if os.environ.get("FAKE_NO_SAFE") else "  --safe-mode\n"))
    sys.exit(0)
if a[:1] == ["--version"]:
    print("9.9.9 (Claude Code)"); sys.exit(0)
if a[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                      "email": "someone@example.org", "orgId": "0f0f0f0f-1111-2222-3333-444444444444",
                      "orgName": "Some University", "subscriptionType": "max"}, indent=2))
    sys.exit(0)
rec = {"argv": a, "cwd": os.getcwd(), "stdin": sys.stdin.read(),
       "env": {k: os.environ.get(k) for k in ("CLAUDECODE", "CLAUDE_EFFORT", "CLAUDE_CODE_SESSION_ID",
                                               "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY",
                                               "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "AMA_EXTRA")}}
with open(os.environ["FAKE_RECORD"], "w") as f:
    json.dump(rec, f)
mode = os.environ.get("FAKE_MODE", "ok")
def emit(o): print(json.dumps(o), flush=True)
if mode == "ok":
    emit({"type": "system", "subtype": "init", "session_id": "s-1", "model": "claude-sonnet-x",
          "permissionMode": "bypassPermissions", "mcp_servers": [], "tools": ["Bash", "Read", "WebFetch"],
          "claude_code_version": "9.9.9"})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"},
                                                        {"type": "tool_use", "name": "Bash", "input": {}}]}})
    open("out.txt", "w").write("OK")
    emit({"type": "result", "subtype": "success", "is_error": False, "result": "All done.",
          "session_id": "s-1", "num_turns": 3, "total_cost_usd": 0.01,
          "usage": {"input_tokens": 10, "output_tokens": 5, "server_tool_use": {"web_search_requests": 0}}})
    sys.exit(0)
if mode == "ratelimit":
    emit({"type": "result", "subtype": "success", "is_error": True,
          "result": "Claude AI usage limit reached|1700000000", "session_id": "s-2"})
    sys.exit(1)
if mode == "root":
    print("--dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons",
          file=sys.stderr)
    sys.exit(1)
if mode == "noresult":
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "partial"}]}})
    sys.exit(0)
if mode == "hang":
    code = "import time; time.sleep(600)"
    c = subprocess.Popen([sys.executable, "-c", code])
    g = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)  # left the group
    with open(os.environ["FAKE_PIDS"], "w") as f:
        f.write(f"{os.getpid()} {c.pid} {g.pid}")
    time.sleep(600)
'''

FAKE_CODEX = r'''
import json, os, sys
a = sys.argv[1:]
if a[:1] == ["--version"]:
    print("codex-cli 9.9.9"); sys.exit(0)
if a[:2] == ["login", "status"]:
    print("Logged in using an API key - sk-proj-abcdef0123456789***wxyz"); sys.exit(0)
if "mcp" in a and "list" in a:
    if os.environ.get("FAKE_MCP_FAIL"):
        print("config error", file=sys.stderr); sys.exit(3)
    print(json.dumps([{"name": "ama", "enabled": True}, {"name": "off", "enabled": False},
                      {"name": "we.ird", "enabled": True}])); sys.exit(0)
if a[:1] == ["sandbox"]:
    print("bubblewrap is unavailable", file=sys.stderr); sys.exit(101)
assert a[0] == "exec", a
rec = {"argv": a, "cwd": os.getcwd(), "stdin": sys.stdin.read(),
       "env": {k: os.environ.get(k) for k in ("CLAUDECODE", "CODEX_THREAD_ID", "OPENAI_API_KEY")}}
with open(os.environ["FAKE_RECORD"], "w") as f:
    json.dump(rec, f)
out = a[a.index("-o") + 1]
def emit(o): print(json.dumps(o), flush=True)
emit({"type": "thread.started", "thread_id": "t-1"})
emit({"type": "turn.started"})
if os.environ.get("FAKE_MODE", "ok") == "ok":
    emit({"type": "item.completed", "item": {"id": "i0", "type": "command_execution", "command": "ls"}})
    emit({"type": "item.completed", "item": {"id": "i1", "type": "web_search", "query": "x"}})
    emit({"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "from events"}})
    emit({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 7}})
    open(out, "w").write("Final answer.\n")
    sys.exit(0)
emit({"type": "error", "message": "You've hit your usage limit. Try again later."})
emit({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again later."}})
sys.exit(1)
'''


def _install(bindir: Path, name: str, body: str) -> Path:
    p = bindir / name
    p.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


@pytest.fixture()
def fakes(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _install(bindir, "claude", FAKE_CLAUDE)
    _install(bindir, "codex", FAKE_CODEX)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    monkeypatch.setenv("FAKE_PIDS", str(tmp_path / "pids.txt"))
    R._HELP_CACHE.clear()
    work = tmp_path / "run"
    work.mkdir()
    yield {"bin": bindir, "record": record, "work": work, "tmp": tmp_path}
    R._HELP_CACHE.clear()


def _record(fakes) -> dict:
    return json.loads(fakes["record"].read_text())


def _flag_value(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, x in enumerate(argv) if x == flag and i + 1 < len(argv)]


# --------------------------------------------------------------------------------------
# model table and roles
# --------------------------------------------------------------------------------------


def test_default_models_match_the_study_assignment():
    assert set(R.ROLES) == {"P", "B", "SR", "BF", "C", "CR", "0", "G0A", "1", "2", "3", "3F", "4",
                            "3C", "4C", "3CF", "5", "6"}
    sonnet = {r for r, m in R.DEFAULT_MODELS["claude"].items() if m == "sonnet"}
    opus = {r for r, m in R.DEFAULT_MODELS["claude"].items() if m == "opus"}
    assert sonnet == {"P", "C", "0", "3", "3F", "6"}
    assert opus == set(R.ROLES) - sonnet
    assert set(R.DEFAULT_MODELS["codex"]) == set(R.ROLES)
    assert all(m is None for m in R.DEFAULT_MODELS["codex"].values())


@pytest.mark.parametrize("label,step", [("4", "4"), ("step4_r2", "4"), ("3F", "3F"), ("step3CF_r3", "3CF"),
                                        ("BF_g0_audit", "BF"), ("G0A", "G0A"), ("0 retry", "0")])
def test_base_role(label, step):
    assert R.base_role(label) == step


def test_default_model_lookup():
    assert R.default_model("claude", "step3F_r2") == "sonnet"
    assert R.default_model("claude", "4C") == "opus"
    assert R.default_model("claude", "unknown-role") == "opus"
    assert R.default_model("codex", "P") is None
    assert R.make_runner("claude").default_model("6") == "sonnet"


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def test_clean_env_strips_parent_session_markers_and_keeps_credentials():
    base = {"PATH": "/bin", "CLAUDECODE": "1", "CLAUDE_EFFORT": "xhigh", "CLAUDE_CODE_ENTRYPOINT": "cli",
            "CLAUDE_CODE_SESSION_ID": "x", "CLAUDE_CODE_MESSAGING_TOKEN": "t", "CLAUDE_AGENT_SDK_VERSION": "1",
            "CODEX_THREAD_ID": "t", "CODEX_SANDBOX": "seatbelt", "ANTHROPIC_API_KEY": "k",
            "CLAUDE_CODE_USE_BEDROCK": "1", "OPENAI_API_KEY": "k2", "CODEX_HOME": "/x"}
    env = R.clean_env(base, extra={"A": "b"})
    for k in ("CLAUDECODE", "CLAUDE_EFFORT", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
              "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_AGENT_SDK_VERSION", "CODEX_THREAD_ID", "CODEX_SANDBOX"):
        assert k not in env
    for k in ("PATH", "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "OPENAI_API_KEY", "CODEX_HOME"):
        assert k in env
    assert env["A"] == "b"


def test_redact():
    s = R.redact("a@b.com sk-proj-ABC_123***xyz 0f0f0f0f-1111-2222-3333-444444444444 "
                 "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc " + "x" * 40 + " codex-cli 0.152.1")
    assert "a@b.com" not in s and "ABC_123" not in s and "0f0f0f0f" not in s and "eyJhbGci" not in s
    assert "x" * 40 not in s
    assert "codex-cli 0.152.1" in s


# --------------------------------------------------------------------------------------
# FakeRunner / make_runner
# --------------------------------------------------------------------------------------


def test_make_runner_backends():
    assert isinstance(R.make_runner("claude"), R.ClaudeRunner)
    assert isinstance(R.make_runner("Codex"), R.CodexRunner)
    fr = R.make_runner("fake", handler=lambda *a: 0)
    assert isinstance(fr, R.FakeRunner) and fr.backend == "fake"
    with pytest.raises(ValueError):
        R.make_runner("gemini")


def test_fake_runner_calls_handler_and_records(tmp_path):
    seen = []

    def handler(role, prompt, workdir, log_path):
        seen.append((role, prompt, Path(workdir), Path(log_path)))
        (Path(workdir) / "config.yaml").write_text("x: 1\n")
        return 0

    fr = R.FakeRunner(handler)
    res = fr.run("PROMPT", workdir=tmp_path, role="C", label="run1:stepC", model="sonnet",
                 log_path=tmp_path / "logs" / "stepC.jsonl", timeout_s=5, extra_dirs=[tmp_path])
    assert isinstance(res, R.RoleResult)
    assert res.exit_code == 0 and not res.timed_out and res.error is None and res.ok
    assert seen == [("C", "PROMPT", tmp_path, tmp_path / "logs" / "stepC.jsonl")]
    assert Path(res.log_path).exists() and (tmp_path / "config.yaml").exists()
    assert fr.calls[0]["role"] == "C" and fr.calls[0]["model"] == "sonnet" and fr.calls[0]["timeout_s"] == 5


def test_fake_runner_tuple_failure_and_timeout(tmp_path):
    fr = R.FakeRunner(lambda role, *a: (3, "boom") if role == "0" else (_ for _ in ()).throw(TimeoutError()))
    r0 = fr.run("p", workdir=tmp_path, role="0", label="l", model=None, log_path=tmp_path / "a.log")
    assert (r0.exit_code, r0.last_message, r0.error, r0.ok) == (3, "boom", "agent_error", False)
    r1 = fr.run("p", workdir=tmp_path, role="3", label="l", model=None, log_path=tmp_path / "b.log")
    assert r1.timed_out and r1.error == "timeout" and r1.exit_code != 0


# --------------------------------------------------------------------------------------
# ClaudeRunner
# --------------------------------------------------------------------------------------


def test_claude_runner_command_env_and_parse(fakes, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_EFFORT", "xhigh")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key")
    extra = fakes["tmp"] / "data"
    extra.mkdir()
    log = fakes["work"] / "logs" / "step0.jsonl"
    kickoff = "Your complete instructions are in this file:\n\n/x/prompts_rendered/step0.md\n"
    res = R.ClaudeRunner(env={"AMA_EXTRA": "1"}).run(
        kickoff, workdir=fakes["work"], role="0", label="r:step0", model="sonnet", log_path=log,
        extra_dirs=[extra, fakes["tmp"] / "missing"])
    rec = _record(fakes)
    argv = rec["argv"]
    assert argv[0] == "-p"
    assert _flag_value(argv, "--output-format") == ["stream-json"] and "--verbose" in argv
    assert _flag_value(argv, "--permission-mode") == ["bypassPermissions"]
    assert "--strict-mcp-config" in argv and "--mcp-config" not in argv
    assert "--safe-mode" in argv
    assert _flag_value(argv, "--model") == ["sonnet"]
    assert _flag_value(argv, "--add-dir") == [str(extra.resolve())]
    denied = _flag_value(argv, "--disallowedTools")[0].split(",")
    assert {"SendMessage", "ListAgents", "PushNotification", "RemoteTrigger"} <= set(denied)
    assert not {"Bash", "Edit", "Write", "Read", "WebFetch", "WebSearch"} & set(denied)
    assert kickoff not in argv and rec["stdin"] == kickoff  # prompt on stdin
    assert Path(rec["cwd"]).resolve() == fakes["work"].resolve()
    env = rec["env"]
    assert env["CLAUDECODE"] is None and env["CLAUDE_EFFORT"] is None
    assert env["CLAUDE_CODE_SESSION_ID"] is None and env["CLAUDE_CODE_ENTRYPOINT"] is None
    assert env["ANTHROPIC_API_KEY"] == "dummy-key" and env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert env["AMA_EXTRA"] == "1"
    assert res.exit_code == 0 and res.ok and not res.timed_out
    assert res.last_message == "All done."
    assert res.log_path == str(log.resolve()) and log.read_text().count("\n") == 3
    assert res.meta["session_id"] == "s-1" and res.meta["num_turns"] == 3
    assert res.meta["init"]["mcp_servers"] == [] and res.meta["tool_calls"] == 1
    assert res.meta["skipped_extra_dirs"] == [str(fakes["tmp"] / "missing")]
    assert (fakes["work"] / "out.txt").read_text() == "OK"


def test_claude_runner_no_model_no_safe_mode_and_log_rotation(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_NO_SAFE", "1")
    log = fakes["work"] / "x.jsonl"
    log.write_text("earlier transcript\n")
    res = R.ClaudeRunner().run("go", workdir=fakes["work"], role="4", label="l", model=None, log_path=log)
    argv = _record(fakes)["argv"]
    assert "--model" not in argv and "--safe-mode" not in argv
    assert res.ok
    R.ClaudeRunner(disallowed_tools=(), safe_mode=True).run(
        "go", workdir=fakes["work"], role="4", label="l", model=None, log_path=fakes["work"] / "y.jsonl")
    argv = _record(fakes)["argv"]
    assert "--disallowedTools" not in argv and "--safe-mode" in argv
    assert (fakes["work"] / "x.jsonl.1").read_text() == "earlier transcript\n"


def test_claude_runner_non_bypass_mode_allows_tools(fakes):
    R.ClaudeRunner(permission_mode="acceptEdits", safe_mode=False).run(
        "go", workdir=fakes["work"], role="1", label="l", model="opus", log_path=fakes["work"] / "l.jsonl")
    argv = _record(fakes)["argv"]
    assert _flag_value(argv, "--permission-mode") == ["acceptEdits"]
    tools = _flag_value(argv, "--allowedTools")[0].split(",")
    assert {"Bash", "Edit", "Write", "WebFetch", "WebSearch"} <= set(tools)


@pytest.mark.parametrize("mode,error,code", [("ratelimit", "rate_limited", 1), ("root", "refused_root", 1),
                                             ("noresult", "no_result", 0)])
def test_claude_runner_error_classes(fakes, monkeypatch, mode, error, code):
    monkeypatch.setenv("FAKE_MODE", mode)
    res = R.ClaudeRunner().run("go", workdir=fakes["work"], role="2", label="l", model="opus",
                               log_path=fakes["work"] / f"{mode}.jsonl")
    assert res.exit_code == code and res.error == error and not res.ok and not res.timed_out


def test_claude_runner_not_installed(tmp_path):
    res = R.ClaudeRunner(executable="no-such-agent-cli-xyz").run(
        "go", workdir=tmp_path, role="P", label="l", model=None, log_path=tmp_path / "l.jsonl")
    assert res.exit_code == 127 and res.error == "not_installed" and Path(res.log_path).exists()


def test_missing_workdir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        R.ClaudeRunner().run("go", workdir=tmp_path / "nope", role="P", label="l", model=None,
                             log_path=tmp_path / "l.jsonl")


def _gone(pid: int, wait_s: float = 10.0) -> bool:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not R._alive(pid):
            return True
        time.sleep(0.2)
    return False


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_timeout_kills_process_group_and_detached_descendants(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    t0 = time.monotonic()
    res = R.ClaudeRunner(kill_grace_s=1).run("go", workdir=fakes["work"], role="3", label="l", model="sonnet",
                                             log_path=fakes["work"] / "hang.jsonl", timeout_s=3)
    assert res.timed_out and res.error == "timeout" and res.exit_code != 0
    assert time.monotonic() - t0 < 30
    pids = [int(x) for x in (fakes["tmp"] / "pids.txt").read_text().split()]
    assert len(pids) == 3
    for pid in pids:
        assert _gone(pid), f"pid {pid} survived the timeout"


# --------------------------------------------------------------------------------------
# CodexRunner
# --------------------------------------------------------------------------------------


def test_codex_runner_command_and_parse(fakes, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "parent")
    monkeypatch.setenv("CLAUDECODE", "1")
    extra = fakes["tmp"] / "data"
    extra.mkdir()
    log = fakes["work"] / "logs" / "stepP.jsonl"
    res = R.CodexRunner(reasoning_effort="low").run(
        "kickoff text", workdir=fakes["work"], role="P", label="r:stepP", model=None, log_path=log,
        extra_dirs=[extra])
    rec = _record(fakes)
    argv = rec["argv"]
    assert argv[0] == "exec" and "--json" in argv and "--skip-git-repo-check" in argv
    assert Path(_flag_value(argv, "--cd")[0]).resolve() == fakes["work"].resolve()
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    cfg = _flag_value(argv, "-c")
    assert 'web_search="live"' in cfg and 'model_reasoning_effort="low"' in cfg
    assert "mcp_servers.ama.enabled=false" in cfg
    assert not any("mcp_servers.off" in c for c in cfg)  # already disabled: left alone
    assert "features.memories=false" in cfg
    assert "-m" not in argv  # account default model
    assert _flag_value(argv, "--add-dir") == [str(extra.resolve())]
    assert argv[-1] == "-" and rec["stdin"] == "kickoff text"
    assert rec["env"]["CODEX_THREAD_ID"] is None and rec["env"]["CLAUDECODE"] is None
    assert res.ok and res.exit_code == 0
    assert res.last_message == "Final answer."
    assert res.meta["thread_id"] == "t-1" and res.meta["commands"] == 1 and res.meta["web_searches"] == 1
    assert res.meta["usage"]["output_tokens"] == 7
    assert res.meta["mcp_servers_disabled"] == ["ama"]
    assert res.meta["mcp_servers_not_disabled"] == ["we.ird"]


def test_codex_runner_records_a_failed_mcp_listing(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_MCP_FAIL", "1")
    res = R.CodexRunner().run("go", workdir=fakes["work"], role="P", label="l", model=None,
                              log_path=fakes["work"] / "l.jsonl")
    cfg = _flag_value(_record(fakes)["argv"], "-c")
    assert not any(c.startswith("mcp_servers.") for c in cfg)  # nothing could be turned off
    assert "exited 3" in res.meta["mcp_list_error"]
    assert "mcp_servers_disabled" not in res.meta


def test_codex_runner_model_and_workspace_write(fakes):
    R.CodexRunner(sandbox="workspace-write", web_search=None, profile="ama").run(
        "go", workdir=fakes["work"], role="4", label="l", model="gpt-x", log_path=fakes["work"] / "l.jsonl")
    argv = _record(fakes)["argv"]
    assert _flag_value(argv, "-m") == ["gpt-x"] and _flag_value(argv, "-p") == ["ama"]
    assert _flag_value(argv, "-s") == ["workspace-write"]
    cfg = _flag_value(argv, "-c")
    assert "sandbox_workspace_write.network_access=true" in cfg
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert not any(c.startswith("web_search") for c in cfg)
    with pytest.raises(ValueError):
        R.CodexRunner(sandbox="read-only")


def test_codex_runner_rate_limit(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    res = R.CodexRunner().run("go", workdir=fakes["work"], role="5", label="l", model=None,
                              log_path=fakes["work"] / "l.jsonl")
    assert res.exit_code == 1 and res.error == "rate_limited" and "usage limit" in res.last_message


# --------------------------------------------------------------------------------------
# check_backends
# --------------------------------------------------------------------------------------


def test_check_backends_redacts(fakes):
    out = R.check_backends()
    assert set(out) == {"claude", "codex"}
    c, x = out["claude"], out["codex"]
    for d in (c, x):
        assert {"installed", "version", "logged_in", "detail"} <= set(d)
    assert c["installed"] and c["logged_in"] is True and c["version"].startswith("9.9.9")
    assert "authMethod=claude.ai" in c["detail"]
    assert x["installed"] and x["logged_in"] is True and x["version"] == "codex-cli 9.9.9"
    blob = json.dumps(out)
    for secret in ("someone@example.org", "0f0f0f0f", "Some University", "abcdef0123456789"):
        assert secret not in blob
    if sys.platform.startswith("linux"):
        assert x["sandbox_available"] is False


def test_check_backends_not_installed(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    out = R.check_backends()
    assert out["claude"]["installed"] is False and out["claude"]["logged_in"] is False
    assert out["codex"]["installed"] is False


# --------------------------------------------------------------------------------------
# opt-in live check (spends a few cents of the account's usage):
#   AMA_LIVE_TESTS=claude,codex pytest tests/test_runners.py -k live
# --------------------------------------------------------------------------------------

LIVE_TASK = ("write the word OK into out.txt, then run python3 -c 'print(6*7)' and write the result into "
             "calc.txt, then fetch https://example.com with any tool you have and write its <title> into web.txt")


@pytest.mark.parametrize("backend", ["claude", "codex"])
def test_live_backend(backend, tmp_path):
    wanted = {b.strip() for b in os.environ.get("AMA_LIVE_TESTS", "").split(",") if b.strip()}
    if backend not in wanted:
        pytest.skip(f"set AMA_LIVE_TESTS={backend} to run a real {backend} session")
    work = tmp_path / "run"
    work.mkdir()
    (work / "instructions.md").write_text("# Instructions\n\n" + LIVE_TASK + "\n")
    kickoff = (f"Your complete instructions are in this file:\n\n{work / 'instructions.md'}\n\n"
               "Read the whole file before doing anything else, then carry out those instructions exactly "
               "as written. When you have finished, reply with a brief plain-text summary.")
    runner = (R.make_runner("claude") if backend == "claude"
              else R.make_runner("codex", reasoning_effort="low"))
    res = runner.run(kickoff, workdir=work, role="P", label="live", log_path=tmp_path / "live.jsonl",
                     model="haiku" if backend == "claude" else None, timeout_s=900)
    assert res.ok, (res.error, res.last_message)
    assert (work / "out.txt").read_text().strip() == "OK"
    assert (work / "calc.txt").read_text().strip() == "42"
    assert "Example Domain" in (work / "web.txt").read_text()
    assert res.last_message.strip()


def test_claude_usage_limit_structured_fields(tmp_path):
    """Claude Code 2.1.x reports a usage limit as api_error "usage_limit_reached" (HTTP 429) with the text
    "You've hit your 5-hour limit"; it must be classed rate_limited (so the orchestrator waits), not agent_error."""
    from ama_mcp import runners as R
    log = tmp_path / "s.log"
    log.write_text("\n".join(json.dumps(e) for e in [
        {"type": "system", "subtype": "init", "session_id": "s1", "model": "m", "tools": []},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "API Error"}]},
         "api_error_status": 429, "api_error_type": "rate_limit_error", "api_error_origin": "first_party"},
        {"type": "result", "subtype": "success", "is_error": True, "num_turns": 1, "terminal_reason": "api_error",
         "api_error_status": 429, "api_error_type": "rate_limit_error", "api_error": "usage_limit_reached",
         "result": "You've hit your 5-hour limit · resets 3:30pm (America/Chicago)"},
    ]) + "\n")
    info = R.parse_claude_stream(log)
    assert info["api_error"] == "usage_limit_reached" and info["api_error_status"] == 429
    assert R._classify(1, False, False, True, info["result_text"]) == "rate_limited"   # the text alone now matches
    assert R._classify(1, False, False, True, "API Error api_error=usage_limit_reached") == "rate_limited"
    assert R._classify(1, False, False, True, "something else failed") == "agent_error"
