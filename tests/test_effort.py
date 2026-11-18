"""The reasoning-effort option: stored per run, mapped to each CLI's flag, changeable on resume."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import ama_mcp
from ama_mcp import runners as R
from ama_mcp import runs
from ama_mcp.cli import main


def _core() -> Path:
    core = Path(ama_mcp.__file__).resolve().parent / "core"
    if not (core / "scripts" / "new_run.py").exists():
        pytest.skip("core scripts not available")
    return core


def _run(tmp_path: Path, backend: str, effort, run_id: str = "r1") -> Path:
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    return runs.create_run(pdf, runs_root=tmp_path / "runs", run_id=run_id, backend=backend, effort=effort,
                           core_dir=_core())


def _opts(run: Path) -> dict:
    return json.loads((run / "ama_options.json").read_text())


@pytest.mark.parametrize("backend, effort, ro", [
    ("codex", "high", {"reasoning_effort": "high"}),
    ("codex", "ULTRA", {"reasoning_effort": "ultra"}),
    ("claude", "max", {"effort": "max"}),
    ("claude", None, {}),
])
def test_create_run_stores_effort(tmp_path, backend, effort, ro):
    o = _opts(_run(tmp_path, backend, effort))
    assert o["runner_options"] == ro
    assert o["effort"] == (effort.lower() if effort else None)


def test_invalid_effort_is_refused(tmp_path):
    with pytest.raises(ValueError, match="effort for claude"):
        _run(tmp_path, "claude", "ultra")
    with pytest.raises(ValueError, match="effort for codex"):
        _run(tmp_path, "codex", "fast", run_id="r2")


def test_set_options_changes_and_clears_effort(tmp_path):
    run = _run(tmp_path, "codex", "low")
    o = _opts(run)
    o["runner_options"]["web_search"] = "cached"          # other runner options survive
    (run / "ama_options.json").write_text(json.dumps(o))
    runs.set_options(run, effort="xhigh")
    assert _opts(run)["runner_options"] == {"web_search": "cached", "reasoning_effort": "xhigh"}
    runs.set_options(run, effort="default")
    assert _opts(run)["runner_options"] == {"web_search": "cached"} and _opts(run)["effort"] is None


def test_runner_command_lines_carry_the_effort(tmp_path):
    cmd, _ = R.CodexRunner(reasoning_effort="high").build_command(
        "codex", workdir=tmp_path, model=None, extra_dirs=(), last_message_path=tmp_path / "m.txt")
    assert 'model_reasoning_effort="high"' in cmd
    cmd = R.ClaudeRunner(effort="xhigh").build_command("claude", model=None, extra_dirs=(), safe_mode=False)
    assert cmd[cmd.index("--effort") + 1] == "xhigh"


def test_cli_effort_flag_reaches_the_run(tmp_path, monkeypatch):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("AMA_CORE_DIR", str(_core()))
    monkeypatch.setattr(runs, "start_background", lambda run, **kw: {"pid": 0, "log": str(run / "x.log")})
    assert main(["--runs-root", str(tmp_path / "runs"), "run", str(pdf), "--backend", "codex", "--no-check",
                 "--effort", "high", "--run-id", "c1"]) == 0
    assert _opts(tmp_path / "runs" / "c1")["runner_options"] == {"reasoning_effort": "high"}
    assert main(["--runs-root", str(tmp_path / "runs"), "resume", "c1", "--effort", "max"]) == 0
    assert _opts(tmp_path / "runs" / "c1")["runner_options"] == {"reasoning_effort": "max"}
