#!/usr/bin/env python3
"""Freeze a run's artifacts when a gate passes, and detect later edits.

An upstream agent that finishes late can rewrite an artifact after its gate
has already passed and its consumers have already run. That happened once: a
brainstormer appended a 172nd option to forks.json hours after the fork
auditor had judged 171 of them and the grid had executed. G2 caught it only
because the coverage check happens to be re-runnable; a change that preserved
the option count would have gone unnoticed.

So each gate PASS records a hash of the artifacts in play, and any later step
can ask whether what it is reading is what was gated.

    python <core>/scripts/freeze.py runs/<id> --gate G2 --set
    python <core>/scripts/freeze.py runs/<id> --check
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

# A gate freezes what it and the gates before it have settled -- nothing
# downstream. Freezing the grid at G0 would flag every later fix round as
# tampering, and a check that cries wolf is a check nobody reads.
GATE_SCOPE = {
    "G0": ["config.yaml", "reproduction.md"],
    "G2": ["config.yaml", "reproduction.md", "forks.json", "audit.json"],
    "G4": ["config.yaml", "reproduction.md", "forks.json", "audit.json",
           "run_grid.py", "run_grid.R", "grid_results.csv",
           "implementation_audit.md", "fix_log.md"],
    # G4C: the companion audit cleared -- freeze the companion artifacts; a later G6 re-freezes all
    "G4C": ["config.yaml", "reproduction.md", "forks.json", "audit.json",
            "run_grid.py", "run_grid.R", "grid_results.csv",
            "implementation_audit.md", "fix_log.md",
            "companion_spec.json", "run_companion.py", "companion_results.csv"],
    "G6": ["config.yaml", "reproduction.md", "forks.json", "audit.json",
           "run_grid.py", "run_grid.R", "grid_results.csv",
           "implementation_audit.md", "fix_log.md", "report.md",
           "companion_spec.json", "run_companion.py", "companion_results.csv"],
}
ARTIFACTS = GATE_SCOPE["G6"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def load(run: Path) -> dict:
    f = run / ".frozen.json"
    return json.loads(f.read_text()) if f.exists() else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--gate", default="")
    ap.add_argument("--set", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    run = Path(args.run_dir).resolve()
    frozen = load(run)

    if args.set:
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        scope = GATE_SCOPE.get(args.gate, ARTIFACTS)
        # A re-gate re-freezes its own scope; entries from later gates are
        # dropped, since whatever they covered is now being redone.
        later = [n for n, rec in frozen.items()
                 if n not in scope and rec.get("gate", "") > args.gate]
        for n in later:
            frozen.pop(n)
        for name in scope:
            p = run / name
            if p.exists():
                frozen.setdefault(name, {})
                frozen[name] = {"sha256": sha(p), "gate": args.gate, "at": stamp}
        (run / ".frozen.json").write_text(json.dumps(frozen, indent=1))
        print(f"froze {len(frozen)} artifacts at {args.gate or 'no gate'}")
        return 0

    if not frozen:
        print("nothing frozen yet")
        return 0
    bad = []
    for name, rec in sorted(frozen.items()):
        now = sha(run / name)
        state = ("MISSING" if not now else
                 "unchanged" if now == rec["sha256"] else "CHANGED")
        print(f"  {name:26} {state:10} frozen at {rec['gate']} {rec['at'][:19]}")
        if state != "unchanged":
            bad.append(name)
    if bad:
        print(f"\n{len(bad)} artifact(s) changed after being gated: "
              f"{', '.join(bad)}", file=sys.stderr)
        print("Re-run the gate that covered them before trusting anything "
              "downstream.", file=sys.stderr)
        return 1
    print("\nall gated artifacts unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
