#!/usr/bin/env python3
"""G4 -- implementation gate.

Passes when the latest implementation audit records zero unresolved BLOCKER
findings after the fix-and-re-audit loop, and every fix has itself been
re-audited. A MAJOR may be closed by a written rebuttal with evidence; a
BLOCKER may not.

The decision belongs to the independent auditor, not to the implementer: this
script reads the auditor's own tally, checks the loop actually closed, and
records the outcome.

    python <core>/scripts/gate_g4.py runs/<run_id>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _gatelog import log_gate
from freeze import main as _freeze_main

TALLY = re.compile(r"TALLY:\s*blockers\s*=\s*(\d+)\s*major\s*=\s*(\d+)\s*minor\s*=\s*(\d+)",
                   re.I)


def audits(run: Path) -> list[Path]:
    got = sorted(run.glob("implementation_audit*.md"))
    return sorted(got, key=lambda p: (len(p.stem), p.stem))


def _freeze(run: Path, gate: str) -> None:
    argv = sys.argv
    sys.argv = ["freeze", str(run), "--gate", gate, "--set"]
    try:
        _freeze_main()
    finally:
        sys.argv = argv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    args = ap.parse_args()
    run = Path(args.run_dir).resolve()

    found = audits(run)
    if not found:
        return fail(run, "no implementation_audit*.md")

    print("G4 implementation check")
    tallies = []
    for p in found:
        m = TALLY.search(p.read_text(encoding="utf-8"))
        if not m:
            print(f"  {p.name:34} no TALLY line")
            tallies.append(None)
            continue
        b, mj, mn = (int(x) for x in m.groups())
        tallies.append((b, mj, mn))
        print(f"  {p.name:34} blockers={b} major={mj} minor={mn}")

    latest = tallies[-1]
    if latest is None:
        return fail(run, f"{found[-1].name} has no TALLY line")

    problems = []
    if latest[0] > 0:
        problems.append(f"{latest[0]} unresolved blocker(s) in {found[-1].name}")

    fix_log = run / "fix_log.md"
    first = tallies[0]
    if first and (first[0] or first[1]):
        if not fix_log.exists():
            problems.append("round 1 raised blockers/majors but no fix_log.md")
        elif len(found) < 2:
            problems.append("fixes were made but never re-audited "
                            "(only one implementation_audit file)")
        else:
            print(f"  fix_log.md present, {len(found)} audit rounds -- "
                  "fixes were re-audited")

    # the grid must be complete and no longer growing (a background run once
    # kept appending after the audit had passed)
    import json as _json
    try:
        n_cells = int(_json.loads((run / "audit.json").read_text())
                      .get("recommended_grid", {}).get("n_cells", 0))
    except Exception:  # noqa: BLE001
        n_cells = 0
    gr = run / "grid_results.csv"
    if not gr.exists():
        problems.append("grid_results.csv missing")
    elif n_cells:
        import csv as _csv
        with gr.open() as fh:
            rows = sum(1 for _ in _csv.reader(fh)) - 1
        print(f"  grid rows {rows} / designated cells {n_cells}")
        if rows < n_cells:
            problems.append(f"grid incomplete: {rows} of {n_cells} cells")
        import time as _time
        if _time.time() - gr.stat().st_mtime < 120:
            problems.append("grid_results.csv modified within the last 2 minutes -- still running?")

    if problems:
        for p in problems:
            print(f"  ! {p}")
        log_gate(run, "G4", "FAIL", "; ".join(problems)[:500])
        print("\nG4: FAIL", file=sys.stderr)
        return 1

    _freeze(run, "G4")
    log_gate(run, "G4", "PASS",
             f"{len(found)} audit round(s), final tally blockers={latest[0]} "
             f"major={latest[1]} minor={latest[2]}")
    print("\nG4: PASS")
    return 0


def fail(run: Path, why: str) -> int:
    log_gate(run, "G4", "FAIL", why)
    print(f"\nG4: FAIL - {why}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
