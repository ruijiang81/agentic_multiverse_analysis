#!/usr/bin/env python3
"""G0A -- reproduction-audit gate, run only after G0 has FAILED.

User policy for the BCL study (2026-09-26): a paper whose rebuilt data miss the
G0 tolerance continues to the multiverse only if an independent reproduction
auditor is confident that no implementation error explains the miss.  Reads the
verdict line of the latest g0_audit*.md:

  G0_AUDIT: CLEAN            exit 0  -- proceed; config.yaml + reproduction.md frozen at G0
  G0_AUDIT: MINOR n=<k>      exit 5  -- errors that do not explain the miss: fix, re-reproduce, continue
  G0_AUDIT: ERRORS n=<k>     exit 3  -- fix round, then re-reproduce and re-gate
  G0_AUDIT: UNRESOLVED - ... exit 4  -- stop
  (no audit / no verdict)    exit 1

    python <core>/scripts/gate_g0a.py runs/<run_id>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _gatelog import log_gate
from freeze import main as _freeze_main


def latest_audit(run: Path) -> Path | None:
    got = sorted(run.glob("g0_audit*.md"), key=lambda p: (len(p.stem), p.stem))
    return got[-1] if got else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    run = Path(ap.parse_args().run_dir).resolve()
    f = latest_audit(run)
    if f is None:
        log_gate(run, "G0A", "FAIL", "no g0_audit*.md")
        print("G0A: FAIL - no g0_audit*.md", file=sys.stderr)
        return 1
    line = next((l.strip() for l in reversed(f.read_text(encoding="utf-8").splitlines())
                 if re.search(r"G0_AUDIT:", l)), "")
    verdict = line.split("G0_AUDIT:", 1)[1].strip(" `*") if line else ""
    print(f"G0A reproduction-audit check -- {f.name}: {line or '(no verdict line)'}")
    if verdict.startswith("CLEAN"):
        argv = sys.argv
        sys.argv = ["freeze", str(run), "--gate", "G0", "--set"]
        try:
            _freeze_main()
        finally:
            sys.argv = argv
        log_gate(run, "G0A", "PASS", f"{f.name}: G0 missed tolerance; independent audit found no "
                                     "implementation error; proceeding per user policy 2026-09-26")
        print("G0A: PASS (CLEAN) -- proceed to the multiverse, reproduction gap recorded")
        return 0
    if verdict.startswith("MINOR"):
        log_gate(run, "G0A", "MINOR", f"{f.name}: {verdict[:300]} -- errors do not explain the miss; "
                                      "fix, re-reproduce, then continue (user policy: continue unless a "
                                      "wrong implementation may lead to the wrong match)")
        print(f"G0A: MINOR -- {verdict}")
        return 5
    if verdict.startswith("ERRORS"):
        log_gate(run, "G0A", "ERRORS", f"{f.name}: {verdict[:300]}")
        print(f"G0A: ERRORS -- {verdict}")
        return 3
    if verdict.startswith("UNRESOLVED"):
        log_gate(run, "G0A", "UNRESOLVED", f"{f.name}: {verdict[:400]}")
        print(f"G0A: UNRESOLVED -- {verdict}")
        return 4
    log_gate(run, "G0A", "FAIL", f"{f.name}: unparseable verdict {line[:200]!r}")
    print("G0A: FAIL - no parseable G0_AUDIT verdict", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
