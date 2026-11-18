#!/usr/bin/env python3
"""Specification-curve figure: estimates sorted by value with the designated
inference's interval, over a fork-choice matrix whose rows are ordered by kill
power and whose cells mark the paper's own choices with a star.

Importable by a run's make_report.py:

    from spec_curve import spec_curve
    spec_curve(grid, dims, paper_choice={"outcome": "pmda"},
               published=-0.0095, out="figures/spec_curve.pdf")
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def spec_curve(grid: pd.DataFrame, dims: list[str], *, out: str | Path,
               paper_choice: dict[str, str] | None = None,
               published: float | None = None, alpha: float = 0.05,
               title: str = "", estimate: str = "estimate",
               ci_lo: str | None = None, ci_hi: str | None = None,
               pcol: str = "p_value", figsize=(9.5, 7.5)) -> Path:
    paper_choice = paper_choice or {}
    g = grid.dropna(subset=[estimate]).sort_values(estimate).reset_index(drop=True)
    x = np.arange(len(g))
    sig = g[pcol] < alpha if pcol in g.columns else pd.Series(False, index=g.index)

    rows = [(d, sorted(map(str, g[d].dropna().unique()))) for d in dims]
    n_opt = sum(len(o) for _, o in rows)

    fig, (ax, mx) = plt.subplots(
        2, 1, figsize=figsize, sharex=True,
        gridspec_kw={"height_ratios": [2.0, max(1.0, n_opt * 0.16)], "hspace": 0.05})

    if ci_lo and ci_hi and ci_lo in g.columns:
        ax.vlines(x, g[ci_lo], g[ci_hi], color="0.78", lw=0.8, zorder=1)
    ax.scatter(x[~sig], g.loc[~sig, estimate], s=9, c="0.55",
               label=f"p >= {alpha}", zorder=2)
    ax.scatter(x[sig], g.loc[sig, estimate], s=9, c="#b2182b",
               label=f"p < {alpha}", zorder=3)
    ax.axhline(0, color="k", lw=0.7)
    if published is not None:
        ax.axhline(published, color="#2166ac", lw=1.0, ls="--",
                   label="published estimate")
    ax.set_ylabel("estimate")
    ax.legend(frameon=False, fontsize=8, loc="best")
    if title:
        ax.set_title(title, fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)

    ylab, y = [], 0
    for dim, opts in rows:
        for opt in opts:
            hit = g[dim].astype(str).values == opt
            mx.scatter(x[hit], np.full(hit.sum(), y), s=5, marker="|",
                       c="#2166ac", linewidths=0.8)
            star = " *" if str(paper_choice.get(dim, "")) == opt else ""
            ylab.append(f"{dim}: {opt}{star}")
            y += 1
        y += 0.6
        ylab.append("")
    ylab = ylab[:-1]
    mx.set_yticks(range(len(ylab)))
    mx.set_yticklabels(ylab, fontsize=6)
    mx.set_ylim(-1, len(ylab))
    mx.invert_yaxis()
    mx.set_xlabel("specification, ordered by estimate")
    mx.spines[["top", "right", "left"]].set_visible(False)
    mx.tick_params(axis="y", length=0)

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", dpi=200)
    plt.close(fig)
    return out


def pvalue_hist(grid: pd.DataFrame, *, out: str | Path, pcol: str = "p_value",
                alpha: float = 0.05) -> Path:
    fig, ax = plt.subplots(figsize=(5, 3))
    ax.hist(grid[pcol].dropna(), bins=np.arange(0, 1.02, 0.02),
            color="0.6", edgecolor="white", linewidth=0.4)
    ax.axvline(alpha, color="#b2182b", lw=1.0, ls="--", label=f"p = {alpha}")
    ax.set_xlabel("p-value under the designated inference")
    ax.set_ylabel("specifications")
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", dpi=200)
    plt.close(fig)
    return out
