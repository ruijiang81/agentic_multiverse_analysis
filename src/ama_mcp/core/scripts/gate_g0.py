#!/usr/bin/env python3
"""G0 -- reproduction gate.

Compares the reproducer's table in reproduction.md against the targets in
config.yaml.  The reproducer's own 'GATE0: PASS' line is a convenience
summary; this script is the decision.

reproduction.md must contain a markdown table whose header includes the
columns `target`, `published` (or `target value`) and `reproduced`.

    python <core>/scripts/gate_g0.py runs/<run_id>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

from _gatelog import log_gate
from freeze import main as _freeze_main

TOL = 0.15  # 15 % translation tolerance; see <core>/gates.md
# A study may set a stricter band in the orchestrator-owned runs/<id>/run_policy.json
# ("g0_tol"); --tol overrides both.  Agents never write run_policy.json.


def printed_precision(raw: str, est_id: str) -> float | None:
    """Half a unit in the last decimal the config prints for this target.

    A relative tolerance is meaningless on a coefficient printed as 0.0001 or
    0.000: 15 % of it is smaller than the rounding the journal already applied.
    So a target also passes if it lands within half a unit of the last printed
    digit.  Read from the raw YAML text, because yaml.safe_load turns "0.000"
    into 0.0 and loses the precision.
    """
    m = re.search(rf"^\s*-?\s*id:\s*{re.escape(est_id)}\s*$(.*?)(?=^\s*-\s+id:|\Z)",
                  raw, re.M | re.S)
    if not m:
        return None
    v = re.search(r"^\s*value:\s*(-?[\d.]+)\s*$", m.group(1), re.M)
    if not v:
        return None
    frac = v.group(1).partition(".")[2]
    return 0.5 * 10 ** -len(frac) if frac else 0.5


def parse_number(s: str):
    # Unicode minus (U+2212) and en dash used as a minus sign read as "-" (a reproduced "−0.0009" was read as +0.0009)
    s = s.strip().replace(",", "").replace("*", "").replace("\u2212", "-").replace("\u2013", "-")
    # skip digits glued to a letter (the "2" in "R2", the "1" in "h1")
    m = re.search(r"(?<![A-Za-z_])-?\d+\.?\d*(?:[eE]-?\d+)?", s)
    return float(m.group()) if m else None


def parse_repro_table(md: str) -> dict[str, dict]:
    """Return {target_id: {'published': float, 'reproduced': float, 'row': str}}."""
    rows: dict[str, dict] = {}
    for line in md.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or set("".join(cells)) <= set("-: "):
            continue
        head = cells[0].lower()
        if head in ("target", "id"):
            continue
        key = re.sub(r"[^a-z0-9]", "", cells[0].lower())
        pub, rep = parse_number(cells[1]), parse_number(cells[2])
        # a reproducer who rounded the cell but wrote "unrounded <x>" in the row: use x (in one run a rounded
        # cell and its unrounded value fell on opposite sides of the tolerance)
        unr = re.search(r"unrounded[^0-9\-\u2212]{0,12}([-\u2212]?\d*\.\d+(?:[eE]-?\d+)?)", line, re.I)
        if unr and rep is not None:
            u = parse_number(unr.group(1))
            if u is not None and abs(u - rep) <= 0.05 * max(abs(rep), 1e-12):
                rep = u
        if pub is None or rep is None:
            continue
        rows[key] = {"published": pub, "reproduced": rep, "row": line.strip()}
    return rows


def match_target(est: dict, table: dict) -> dict | None:
    for cand in (est.get("id"), est.get("quantity"), est.get("location")):
        if not cand:
            continue
        k = re.sub(r"[^a-z0-9]", "", str(cand).lower())
        if k in table:
            return table[k]
        for tk, tv in table.items():
            if k and (k in tk or tk in k):
                return tv
    return None


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
    ap.add_argument("--tol", type=float, default=None)
    args = ap.parse_args()
    run = Path(args.run_dir).resolve()
    global TOL
    pol = run / "run_policy.json"
    if args.tol is not None:
        TOL = args.tol
    elif pol.exists():
        import json
        TOL = float(json.loads(pol.read_text()).get("g0_tol", TOL))

    cfg = yaml.safe_load((run / "config.yaml").read_text())
    md_path = run / "reproduction.md"
    if not md_path.exists():
        return fail(run, "reproduction.md missing")

    md_text = md_path.read_text(encoding="utf-8")
    gating_ids = {str(e.get("id", "")).lower() for e in (cfg.get("headline_estimates") or [])
                  if e.get("gating", True)}
    for line in md_text.splitlines():
        cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
        # only a GATING target that cannot be estimated makes the run infeasible;
        # a non-gating (informational) target that is not estimable is just reported
        if (line.strip().startswith("|") and len(cells) >= 3 and "not estimable" in cells[2]
                and cells[0] in gating_ids):
            log_gate(run, "G0", "INFEASIBLE", f"target not estimable: {line.strip()[:300]}")
            print(f"G0: INFEASIBLE - target not estimable from the package: {line.strip()[:300]}")
            return 2
    table = parse_repro_table(md_text)
    targets = cfg.get("headline_estimates") or []
    if not targets:
        return fail(run, "config.yaml lists no headline_estimates")

    raw = (run / "config.yaml").read_text(encoding="utf-8")
    problems, lines = [], []
    for est in targets:
        eid = str(est.get("id", "?"))
        gating = est.get("gating", True)
        got = match_target(est, table)
        if got is None:
            msg = f"{eid}: no matching row in reproduction.md"
            lines.append(f"  {eid:4} {str(est.get('location',''))[:44]:44} "
                         f"{'MISSING':>34}  {'(informational)' if not gating else 'FAIL'}")
            if gating:
                problems.append(msg)
            continue
        try:
            pub = float(est["value"])
        except (TypeError, ValueError):
            msg = f"{eid}: config value {str(est.get('value'))[:60]!r} is not a number"
            lines.append(f"  {eid:4} {msg}  {'(informational, skipped)' if not gating else 'FAIL'}")
            if gating:
                problems.append(msg)
            continue
        rep = got["reproduced"]
        dev = abs(rep - pub) / (abs(pub) if pub else 1.0)
        eps = printed_precision(raw, eid)
        within_print = eps is not None and abs(rep - pub) <= eps
        ok = dev <= TOL or within_print
        flag = "ok" if ok else "FAIL"
        if not gating:
            flag = "ok" if ok else "off-target (informational)"
        elif ok and dev > TOL:
            flag = "ok (within printed precision)"
        lines.append(f"  {eid:4} {str(est.get('location',''))[:44]:44} "
                     f"target={pub:+.6g} repro={rep:+.6g} dev={dev:.2%} {flag}")
        if not ok and gating:
            problems.append(f"{eid}: deviation {dev:.2%} > {TOL:.0%} "
                            f"and outside printed precision")

    n_gate = sum(1 for e in targets if e.get("gating", True))
    print(f"G0 reproduction check -- {n_gate} gating targets, "
          f"{len(targets) - n_gate} informational")
    print("\n".join(lines))

    agent_line = next((l for l in md_path.read_text().splitlines()
                       if re.match(r"[*_`\s>#-]*GATE0", l)), "(no verdict line)")
    print(f"  reproducer's own verdict: {agent_line.strip()}")
    print("  NOTE: design quantities (N, cluster counts, windows) must match "
          "exactly; verify them by reading reproduction.md.")

    if problems:
        return fail(run, "; ".join(problems))
    _freeze(run, "G0")
    log_gate(run, "G0", "PASS", f"{n_gate} gating targets within {TOL:.0%} or printed precision")
    print("\nG0: PASS")
    return 0


def fail(run: Path, why: str) -> int:
    log_gate(run, "G0", "FAIL", why)
    print(f"\nG0: FAIL - {why}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
