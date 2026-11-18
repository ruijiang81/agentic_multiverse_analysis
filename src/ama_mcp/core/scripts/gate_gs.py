#!/usr/bin/env python3
"""GS -- sample-fidelity gate.

Reads the verdict line of the latest sample_reconciliation*.md, written by the
independent sample reviewer (prompts/stepSR_sample_reviewer.md):

  SAMPLE_REVIEW: BEST_ACHIEVABLE   exit 0  -- every documented sample rule is built;
                                              remaining gaps are explained in the record
  SAMPLE_REVIEW: FIXABLE n=<k>     exit 3  -- builder fix round, then re-review
  (no review / no verdict)         exit 1

    python <core>/scripts/gate_gs.py runs/<run_id>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _gatelog import log_gate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    run = Path(ap.parse_args().run_dir).resolve()
    got = sorted(run.glob("sample_reconciliation*.md"), key=lambda p: (len(p.stem), p.stem))
    if not got:
        log_gate(run, "GS", "FAIL", "no sample_reconciliation*.md")
        print("GS: FAIL - no sample_reconciliation*.md", file=sys.stderr)
        return 1
    f = got[-1]
    line = next((l.strip() for l in reversed(f.read_text(encoding="utf-8").splitlines())
                 if re.search(r"SAMPLE_REVIEW:", l)), "")
    verdict = line.split("SAMPLE_REVIEW:", 1)[1].strip(" `*") if line else ""
    print(f"GS sample-fidelity check -- {f.name}: {line or '(no verdict line)'}")
    if verdict.startswith("BEST_ACHIEVABLE"):
        log_gate(run, "GS", "PASS", f"{f.name}: best achievable; gaps recorded")
        print("GS: PASS")
        return 0
    if verdict.startswith("FIXABLE"):
        log_gate(run, "GS", "FIXABLE", f"{f.name}: {verdict[:300]}")
        print(f"GS: FIXABLE -- {verdict}")
        return 3
    log_gate(run, "GS", "FAIL", f"{f.name}: unparseable verdict {line[:200]!r}")
    print("GS: FAIL - no parseable SAMPLE_REVIEW verdict", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
