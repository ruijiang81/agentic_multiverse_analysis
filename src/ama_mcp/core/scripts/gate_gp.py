#!/usr/bin/env python3
"""GP -- package gate.  Fully programmatic.

Passes iff the package a later role will read actually exists:
  1. package/paper/ holds the paper PDF;
  2. package_status.json records the finder's search (with evidence) and, when
     no complete authors' package was found, a builder block with feasible=true
     and the headline outcome available;
  3. package/ holds at least one data file and a PACKAGE_README.md; a rebuilt
     package also carries BUILD_LOG.md, data/VARIABLES.md and build code.

Exit code 2 (not 1) when the builder declared the paper infeasible -- a
legitimate terminal outcome, not a pipeline failure.

    python <core>/scripts/gate_gp.py runs/<run_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _gatelog import log_gate

DATA_EXT = {".dta", ".csv", ".parquet", ".sas7bdat", ".feather", ".pkl", ".xlsx", ".txt", ".zip"}
COMPLETE = {"authors_data_and_code"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    run = Path(ap.parse_args().run_dir).resolve()
    pkg = run / "package"
    problems: list[str] = []

    st_path = run / "package_status.json"
    st = json.loads(st_path.read_text()) if st_path.exists() else {}
    finder = st.get("finder") or {}
    builder = st.get("builder") or {}

    if not list((pkg / "paper").glob("*.pdf")):
        problems.append("no paper PDF in package/paper/")
    if not finder.get("status"):
        problems.append("package_status.json has no finder.status")
    elif not finder.get("evidence"):
        problems.append("finder recorded no search evidence")

    if builder and builder.get("feasible") is False:
        why = str(builder.get("infeasible_reason"))[:400]
        log_gate(run, "GP", "INFEASIBLE", why)
        print(f"GP: INFEASIBLE - {why}")
        return 2

    if finder.get("status") not in COMPLETE:
        if not builder:
            problems.append("no complete authors' package and no builder block")
        else:
            if not builder.get("headline_outcome_available"):
                problems.append("builder: headline outcome not available")
            for f in ("BUILD_LOG.md", "data/VARIABLES.md"):
                if not (pkg / f).exists():
                    problems.append(f"missing package/{f}")
            if not any((pkg / "code").glob("*")) if (pkg / "code").exists() else True:
                problems.append("no build code in package/code/")

    data = [p for p in pkg.rglob("*") if p.is_file() and p.suffix.lower() in DATA_EXT
            and "paper" not in p.relative_to(pkg).parts[:1]]
    if not data:
        problems.append("no data files in package/")
    if not (pkg / "PACKAGE_README.md").exists():
        problems.append("missing package/PACKAGE_README.md")

    print(f"GP package check -- finder={finder.get('status')} "
          f"builder={builder.get('mode', '-')} data_files={len(data)}")
    if problems:
        log_gate(run, "GP", "FAIL", "; ".join(problems)[:500])
        print("GP: FAIL - " + "; ".join(problems), file=sys.stderr)
        return 1
    log_gate(run, "GP", "PASS", f"finder={finder.get('status')} builder={builder.get('mode', '-')} "
                                f"data_files={len(data)}")
    print("GP: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
