#!/usr/bin/env python3
"""G6 -- report-integrity gate.

Re-runs the committed generator and diffs its output against the committed
report.  A report the generator cannot reproduce contains hand-typed numbers
and fails.

    python <core>/scripts/gate_g6.py runs/<run_id>
"""
from __future__ import annotations

import argparse
import difflib
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from _gatelog import log_gate
from freeze import main as _freeze_main


def _freeze(run, gate):
    """Record artifact hashes so a later edit is detectable."""
    import sys as _s
    argv = _s.argv
    _s.argv = ['freeze', str(run), '--gate', gate, '--set']
    try:
        _freeze_main()
    finally:
        _s.argv = argv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--generator", default="make_report.py")
    ap.add_argument("--report", default="report.md")
    args = ap.parse_args()
    run = Path(args.run_dir).resolve()

    gen, rep = run / args.generator, run / args.report
    for p in (gen, rep):
        if not p.exists():
            return fail(run, f"{p.name} missing")

    cfg = yaml.safe_load((run / "config.yaml").read_text())
    # compute.python is agent-written and sometimes prose ("<venv>/bin/python -- pandas ...");
    # take its first token as a path, and fall back to this interpreter if that is not a file
    import os
    raw_py = str((cfg.get("compute", {}) or {}).get("python", "") or "").split()
    python = os.path.expanduser(raw_py[0]) if raw_py else sys.executable
    if not Path(python).is_file():
        python = sys.executable

    before = rep.read_text(encoding="utf-8")
    backup = run / f".{args.report}.g6bak"
    shutil.copy2(rep, backup)
    try:
        r = subprocess.run([python, str(gen)], cwd=run,
                           capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            return fail(run, f"generator exited {r.returncode}: "
                             f"{r.stderr.strip()[-400:]}")
        after = rep.read_text(encoding="utf-8")
    finally:
        pass

    if after == before:
        backup.unlink(missing_ok=True)
        _freeze(run, "G6")
        log_gate(run, "G6", "PASS", "generator reproduces report.md exactly")
        print("G6: PASS - report.md is byte-identical to the generator's output")
        return 0

    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                     "committed", "regenerated", lineterm="", n=1))
    print("\n".join(diff[:80]))
    shutil.copy2(backup, rep)
    backup.unlink(missing_ok=True)
    return fail(run, f"regenerated report differs on {len(diff)} diff lines")


def fail(run: Path, why: str) -> int:
    log_gate(run, "G6", "FAIL", why)
    print(f"\nG6: FAIL - {why}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
