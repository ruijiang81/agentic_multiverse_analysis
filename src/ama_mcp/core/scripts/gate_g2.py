#!/usr/bin/env python3
"""G2 -- fork-coverage gate.  Fully programmatic.

Passes iff
  1. every option in forks.json carries a verdict, with a stated reason;
  2. the recommended grid is within bounds and every uncrossed dimension has a
     held_fixed entry with a reason;
  3. the admissible inference method is designated in writing.

    python <core>/scripts/gate_g2.py runs/<run_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from math import prod
from pathlib import Path

from _gatelog import log_gate
from freeze import main as _freeze_main

MIN_CELLS, MAX_CELLS = 100, 250
VALID_VERDICTS = {"VALID", "QUESTIONABLE", "INVALID"}


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
    ap.add_argument("--min-cells", type=int, default=MIN_CELLS)
    ap.add_argument("--max-cells", type=int, default=MAX_CELLS)
    args = ap.parse_args()
    run = Path(args.run_dir).resolve()

    forks = json.loads((run / "forks.json").read_text())
    audit = json.loads((run / "audit.json").read_text())
    problems: list[str] = []

    # --- 1. coverage -------------------------------------------------------
    dims = {d["id"]: d for d in forks["dimensions"]}
    all_opts = {o["id"]: d["id"] for d in forks["dimensions"] for o in d["options"]}
    verdicts = {v["option_id"]: v for v in audit.get("verdicts", [])}

    missing = sorted(set(all_opts) - set(verdicts))
    extra = sorted(set(verdicts) - set(all_opts))
    if missing:
        problems.append(f"{len(missing)} options without a verdict: "
                        f"{missing[:8]}{' ...' if len(missing) > 8 else ''}")
    if extra:
        problems.append(f"{len(extra)} verdicts for unknown options: {extra[:8]}")
    for oid, v in verdicts.items():
        if v.get("verdict") not in VALID_VERDICTS:
            problems.append(f"{oid}: verdict {v.get('verdict')!r} not in {VALID_VERDICTS}")
        if not str(v.get("reason", "")).strip():
            problems.append(f"{oid}: verdict carries no reason")

    # --- 2. grid bounds ----------------------------------------------------
    grid = audit.get("recommended_grid", {}) or {}
    crossed = grid.get("crossed_dimensions", []) or []
    held = {h["dimension"] for h in (grid.get("held_fixed", []) or [])}
    crossed_dims = {c["dimension"] for c in crossed}

    if not crossed:
        problems.append("recommended_grid.crossed_dimensions is empty")
    if not 4 <= len(crossed) <= 8:
        problems.append(f"{len(crossed)} crossed dimensions (rubric says 4-8)")

    n_cells = prod(len(c.get("option_ids", [])) for c in crossed) if crossed else 0
    if not args.min_cells <= n_cells <= args.max_cells:
        problems.append(f"grid is {n_cells} cells "
                        f"(bounds {args.min_cells}-{args.max_cells})")
    if grid.get("n_cells") not in (None, n_cells):
        problems.append(f"audit.json reports n_cells={grid['n_cells']} "
                        f"but the cross-product is {n_cells}")

    for c in crossed:
        for oid in c.get("option_ids", []):
            if oid not in all_opts:
                problems.append(f"grid uses unknown option {oid}")
            elif verdicts.get(oid, {}).get("verdict") == "INVALID":
                problems.append(f"grid crosses an INVALID option: {oid}")

    uncovered = sorted(set(dims) - crossed_dims - held)
    if uncovered:
        problems.append(f"{len(uncovered)} dimensions neither crossed nor "
                        f"held_fixed: {uncovered[:8]}")
    for h in (grid.get("held_fixed", []) or []):
        if not str(h.get("why", "")).strip():
            problems.append(f"held_fixed {h.get('dimension')} has no reason")

    # --- 3. inference designation -----------------------------------------
    inf = audit.get("inference_designation") or {}
    if not str(inf.get("method", "")).strip():
        problems.append("no inference method designated")
    if not str(inf.get("reason", "")).strip():
        problems.append("inference designation carries no reason")

    # --- report ------------------------------------------------------------
    print("G2 fork-coverage check")
    print(f"  dimensions        {len(dims)}")
    print(f"  options           {len(all_opts)}")
    counts = {v: sum(1 for x in verdicts.values() if x.get('verdict') == v)
              for v in sorted(VALID_VERDICTS)}
    print(f"  verdicts          {counts}")
    print(f"  crossed dims      {len(crossed)}  ->  {n_cells} cells")
    print(f"  held fixed        {len(held)}")
    print(f"  inference         {inf.get('method', '(none)')}")

    if problems:
        for p in problems:
            print(f"  ! {p}")
        log_gate(run, "G2", "FAIL", "; ".join(problems)[:500])
        print("\nG2: FAIL", file=sys.stderr)
        return 1
    _freeze(run, "G2")
    log_gate(run, "G2", "PASS",
             f"{len(all_opts)} options judged, {n_cells} cells, "
             f"inference={inf.get('method')}")
    print("\nG2: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
