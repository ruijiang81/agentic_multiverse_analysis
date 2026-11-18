#!/usr/bin/env python3
"""Strict conclusion survival: the per-run report's secondary measure (the survival
rate of "When AI Analysts Disagree").

The headline measures (share supported, at p < 0.10, and the others) are those of
"An AI-Powered Multiverse Analysis of Empirical Finance Research"; they come from
ama_mcp.metrics, and the orchestrator writes them to paper_measures.json before the
report.

A specification SUPPORTS the published conclusion iff its estimate carries the
published sign AND p < 0.05 under the assessment-designated inference.

  sign-consistency rate  share of valid specifications with the published sign
  survival rate          share supporting the conclusion
  kill power             per dimension, max minus min survival across its
                         options -- the invalidation rate; a large value means
                         that fork alone decides whether the conclusion holds

Cells flagged as a distinct estimand are excluded from the denominator and
reported separately.  Cells with an `err` are excluded and counted.

    python <core>/scripts/survival.py runs/<run_id>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ALPHA = 0.05
GRID_FILE = "grid_results.csv"


def load(run: Path) -> tuple[pd.DataFrame, dict, dict]:
    cfg = yaml.safe_load((run / "config.yaml").read_text())
    grid = pd.read_csv(run / GRID_FILE)
    audit_path = run / "audit.json"
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else {}
    return grid, cfg, audit


def significance(g: pd.DataFrame, alpha: float, joint_col: str | None) -> tuple[pd.Series, str]:
    """Decide which cells are significant, honouring a co-required test.

    A fork auditor may designate more than one test and require BOTH to clear
    the threshold -- e.g. randomization inference AND a wild cluster bootstrap.
    Reading `p_value < alpha` alone would silently apply the weaker of the two
    and overstate survival. When the grid carries an explicit joint-decision
    column, that column is the rule.
    """
    if joint_col and joint_col in g.columns:
        col = g[joint_col]
        if col.dtype == object:
            col = col.astype(str).str.strip().str.lower().isin(
                {"true", "1", "yes", "t"})
        return col.astype(bool), f"joint rule from column '{joint_col}'"
    return g["p_value"] < alpha, f"p_value < {alpha}"


def annotate(grid: pd.DataFrame, direction: str, alpha: float = ALPHA,
             joint_col: str | None = "sig_joint_5pct") -> pd.DataFrame:
    g = grid.copy()
    for col in ("estimate", "p_value"):
        if col not in g.columns:
            raise SystemExit(f"grid_results.csv has no '{col}' column")
    g["err"] = g.get("err", pd.Series(np.nan, index=g.index))
    g["has_err"] = g["err"].notna() & (g["err"].astype(str).str.strip() != "")
    g["distinct_estimand"] = (
        g.get("distinct_estimand", pd.Series(False, index=g.index))
        .fillna(False).astype(bool))

    want = 1 if str(direction).lower().startswith("pos") else -1
    g["sign_ok"] = np.sign(g["estimate"]) == want
    g["sig"], rule = significance(g, alpha, joint_col)
    g["survives"] = g["sign_ok"] & g["sig"]
    g.attrs["sig_rule"] = rule
    return g


def grid_mask(g: pd.DataFrame, audit: dict) -> pd.Series:
    """True for rows that belong to the audited recommended grid.

    Grid files sometimes carry extra rows: a reproduction anchor run with the
    paper's own inference, mandatory diagnostics, or side-cells reported next
    to the grid. None of them is a cell of the audited fork space, so none may
    vote in survival (added 2026-09-29 after a reproduction anchor run with
    analytic SEs was counted as the only surviving cell of one paper).
    A row belongs to the grid iff every crossed dimension's option is one the
    audit lists for that dimension, and its `row_type` (when the column
    exists) is 'grid' (or 'cell', the label one run's run_grid.py used).
    """
    m = pd.Series(True, index=g.index)
    for c in (audit.get("recommended_grid", {}) or {}).get("crossed_dimensions", []):
        d, ok = c["dimension"], set(map(str, c.get("option_ids", [])))
        if d in g.columns and ok:
            m &= g[d].astype(str).isin(ok)
    for h in (audit.get("recommended_grid", {}) or {}).get("held_fixed", []) or []:
        d, opt = h.get("dimension"), h.get("option_id")
        if d in g.columns and opt is not None and g[d].astype(str).eq(str(opt)).any():
            m &= g[d].astype(str).eq(str(opt)) | g[d].isna()   # one-at-a-time rows vary a held-fixed dim
    if "row_type" in g.columns:
        m &= g["row_type"].astype(str).str.strip().str.lower().isin({"grid", "cell", "nan", ""})  # "cell": one run's label
    return m


def dimension_columns(g: pd.DataFrame, audit: dict) -> list[str]:
    crossed = [c["dimension"] for c in
               (audit.get("recommended_grid", {}) or {}).get("crossed_dimensions", [])]
    cols = [c for c in crossed if c in g.columns]
    if cols:
        return cols
    reserved = {"cell_id", "estimate", "std_error", "p_value", "inference_method",
                "n_obs", "n_clusters", "err", "has_err", "distinct_estimand",
                "sign_ok", "sig", "survives"}
    return [c for c in g.columns
            if c not in reserved and g[c].dtype == object and g[c].nunique() <= 12]


def summarize(g: pd.DataFrame, dims: list[str]) -> tuple[dict, pd.DataFrame]:
    base = g[~g.has_err & ~g.distinct_estimand]
    n = len(base)
    head = {
        "n_cells_total": len(g),
        "n_cells_error": int(g.has_err.sum()),
        "n_cells_distinct_estimand": int(g.distinct_estimand.sum()),
        "n_cells_in_denominator": n,
        "sign_consistency_rate": float(base.sign_ok.mean()) if n else float("nan"),
        "survival_rate": float(base.survives.mean()) if n else float("nan"),
        "significance_rule": None,
        "median_estimate": float(base.estimate.median()) if n else float("nan"),
        "iqr_estimate": (float(base.estimate.quantile(.75) -
                               base.estimate.quantile(.25)) if n else float("nan")),
    }

    rows = []
    for d in dims:
        for opt, sub in base.groupby(d, dropna=False):
            rows.append({
                "dimension": d, "option": opt, "n": len(sub),
                "survival_rate": sub.survives.mean(),
                "sign_rate": sub.sign_ok.mean(),
                "flip_rate": 1 - sub.sign_ok.mean(),
                "median_estimate": sub.estimate.median(),
            })
    per_opt = pd.DataFrame(rows)
    if not per_opt.empty:
        kp = (per_opt.groupby("dimension")["survival_rate"]
              .agg(lambda s: s.max() - s.min()).rename("kill_power"))
        per_opt = per_opt.merge(kp, on="dimension")
        per_opt = per_opt.sort_values(["kill_power", "dimension", "survival_rate"],
                                      ascending=[False, True, True])
    return head, per_opt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--direction", default=None,
                    help="override config.yaml published_conclusion.direction")
    ap.add_argument("--joint-col", default="sig_joint_5pct",
                    help="grid column holding a co-required significance "
                         "decision; used instead of p_value < alpha when present")
    ap.add_argument("--alpha", type=float, default=ALPHA)
    ap.add_argument("--companion", action="store_true",
                    help="score companion_results.csv (paper's held-fixed choices restored)")
    args = ap.parse_args()
    run = Path(args.run_dir).resolve()
    global GRID_FILE
    suffix = ""
    if args.companion:
        GRID_FILE, suffix = "companion_results.csv", "_companion"

    grid, cfg, audit = load(run)
    direction = args.direction or (cfg.get("published_conclusion", {}) or {}).get("direction")
    if not direction:
        raise SystemExit("published_conclusion.direction missing from config.yaml")

    if not args.companion:
        mask = grid_mask(grid, audit)
        n_off = int((~mask).sum())
        grid = grid[mask].copy()
    else:
        n_off = 0   # companion cells are, by construction, outside the audited option sets
    g = annotate(grid, direction, alpha=args.alpha, joint_col=args.joint_col)
    dims = dimension_columns(g, audit)
    head, per_opt = summarize(g, dims)
    head["significance_rule"] = g.attrs["sig_rule"]
    head["n_rows_off_grid_excluded"] = n_off

    out = run / f"grid_survival{suffix}.csv"
    per_opt.to_csv(out, index=False)
    (run / f"grid_survival{suffix}_headline.json").write_text(
        json.dumps(head, indent=2), encoding="utf-8")

    print(f"published direction: {direction}")
    print(f"significance rule:   {g.attrs['sig_rule']}")
    for k, v in head.items():
        print(f"  {k:28} {v:.4g}" if isinstance(v, float) else f"  {k:28} {v}")
    if not per_opt.empty:
        print("\nkill power by dimension (max - min survival across options):")
        kp = per_opt.drop_duplicates("dimension")[["dimension", "kill_power"]]
        for _, r in kp.iterrows():
            print(f"  {r.dimension:28} {r.kill_power:.3f}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
