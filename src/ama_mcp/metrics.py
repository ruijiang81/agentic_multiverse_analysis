"""Per-paper measures of one agentic multiverse analysis (AMA) run directory.

    from ama_mcp.metrics import compute_paper_metrics
    m = compute_paper_metrics(Path("runs/my_paper"))
    m["share_supported"]["value"]            # e.g. 0.42
    m["share_supported"]["definition"]       # plain-language definition
    python -m ama_mcp.metrics runs/my_paper [--json] [--inputs measure_inputs.json] [--out paper_metrics.json]

Every entry of the returned dict is ``{"value", "paper_name", "definition", "group"}`` (plus ``"note"`` where the
value is missing or rests on an assumption). ``value`` is None where an input is missing. The measures are the ones
the paper reports for ONE paper's multiverse; statistics across papers (means, regressions, multiple-testing
corrections, split-half reliability) are out of scope. docs/CHANGES_FROM_STUDY.md (section Metrics) lists every measure, where its
definition comes from in the paper and what differs from the study code.

Inputs read from the run directory (all written by the pipeline): config.yaml, grid_results.csv, audit.json,
forks.json, reproduction.md, gates.log, implementation_audit*.md, companion_results.csv, companion_spec.json,
package_status.json, author_commentary.md.

Optional inputs that the pipeline does NOT write (in the study they came from coding steps outside the run
directory). Give them in ``<run>/measure_inputs.json`` or as ``inputs=`` (a dict; it overrides the file):

    {
      "claim": "directional" | "null" | "unclear",     # default: directional (assumed, noted)
      "reproduced_p": 0.031,                           # p of the reproduced main estimate under the PAPER's inference
      "reproduced_p_sided": "two" | "one",             # one-sided p is doubled (default two)
      "reproduced_t": -2.1, "reproduced_se": 0.014,     # used, in this order, when reproduced_p is absent
      "outcome_sd": 0.32,                              # SD of the main outcome (paper's summary statistics)
      "nonlinear": false,                              # main estimate from a nonlinear model (SD clause off)
      "fixes": {                                       # the study's measure audit for grids that need it
        "p_sidedness": "one_published_direction" | "two",
        "joint_p_column": "<grid column with the co-required test's p>" | null,
        "benchmark": 0.5 | null,                        # claim tested against a nonzero benchmark
        "units": [{"dimension": null | "<dim>", "option": "<opt>", "verdict": "factor" | "not_comparable",
                   "factor": 100.0}],                   # surv30h size rule only
        "unclear": false
      }
    }

Without reproduced_p/t/se the p-value is read heuristically from the reproduced cell of the main estimate's row in
reproduction.md (source "reproduction.md", see the note of `reproduced_p`). Without fixes the grid's p-values are
used as written; `measure_review_flags` lists the conditions under which the study audited a grid's p-values.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd
import yaml

SCHEMA = "ama-mcp/paper-metrics/1"
MEASURE_INPUTS_FILE = "measure_inputs.json"
SEED = 20261103            # seed of the study's permutation null
NULL_DRAWS = 1000          # permutation draws per crossed choice (study: 1,000)
P_CUT = 0.10               # share supported: published sign and designated two-sided p < 0.10
P_FLOOR = 1e-4             # median signed z: p floored at 1e-4 (|z| <= 3.89)
CUTS = (0.01, 0.05, 0.10, 0.20, 0.30, 0.50)
EPS = 1e-12
_ND = NormalDist()


# ================================================================================================ registry
# key -> (group, the paper's name for it, definition). Order = order of the output.
MEASURES: dict[str, tuple[str, str, str]] = {
    # ---- the three outcomes and the share with the published sign (paper section "Outcomes")
    "share_supported": (
        "outcome", "share supported",
        "Share of the paper's audited-grid specifications whose estimate has the published sign and whose two-sided "
        "p-value under the designated inference is below 0.10. Specifications whose estimation fails and those "
        "marked as a distinct estimand are left out."),
    "median_signed_z": (
        "outcome", "median signed z",
        "Median over the same specifications of z = sign(b) sign(published) Phi^-1(1 - max(p, 1e-4) / 2), p the "
        "designated two-sided p-value: the median t-statistic, positive in the published direction."),
    "share_published_sign": (
        "outcome", "share with the published sign",
        "Share of the same specifications whose estimate has the published sign, whatever its p-value."),
    "reproduced": (
        "outcome", "reproduced",
        "1 if our estimate of the main specification has the published sign, a two-sided p-value of at most 0.10 "
        "under the paper's own inference, and is close to the published estimate: within 25% of it, or (linear "
        "models) less than 0.10 standard deviations of the outcome away. The standard for directional claims."),
    "n_specifications": (
        "outcome", "specifications (N_i)",
        "Number of audited-grid specifications that enter the share supported and the median signed z."),
    "claim_type": (
        "outcome", "claim type",
        "directional, null or unclear (an input; the outcomes are read differently for null and unclear claims)."),
    # ---- specifications by sign and p-value (section "Sign and Significance across Specifications")
    "share_opposite_sign": (
        "specifications", "opposite sign",
        "Share of the specifications whose estimate has the opposite sign to the published estimate."),
    "share_published_sign_p_ge_010": (
        "specifications", "published sign, p >= 0.10",
        "Share of the specifications with the published sign and a designated p-value of 0.10 or more."),
    "share_supported_by_cutoff": (
        "specifications", "share with published sign and p below the cutoff",
        "Share of the specifications with the published sign and designated two-sided p below each cutoff "
        "(0.01, 0.05, 0.10, 0.20, 0.30, 0.50); the 0.10 entry is the share supported."),
    "n_failed_specifications": (
        "specifications", "specifications whose estimation fails",
        "Audited-grid specifications (one per combination of crossed options, distinct-estimand rows left out) "
        "with an error, or without an estimate or p-value."),
    "n_distinct_estimand_specifications": (
        "specifications", "distinct-estimand specifications",
        "Audited-grid rows the choice auditor marks as a distinct estimand (left out of every share)."),
    # ---- reproduction (section "Reproducing the Main Estimate")
    "g0_strict_match": (
        "reproduction", "strict match (G0)",
        "1 if the last G0 decision in gates.log is PASS: the reproduced estimate is within the run's G0 tolerance "
        "(the study used 10%) of the published one or within half a unit of its last printed decimal."),
    "published_estimate": (
        "reproduction", "published main estimate",
        "The gating main estimate's value in config.yaml; its sign is the published sign of every specification."),
    "reproduced_estimate": (
        "reproduction", "reproduced main estimate",
        "Our estimate of the main specification, read from reproduction.md with gate G0's own parser."),
    "relative_deviation": (
        "reproduction", "relative gap",
        "|reproduced - published| / |published|."),
    "same_sign": (
        "reproduction", "same sign", "1 if the reproduced and published main estimates have the same sign."),
    "reproduced_p": (
        "reproduction", "p-value under the paper's own inference",
        "Two-sided p-value of the reproduced main estimate under the inference the paper reports, applied to our "
        "data (one-sided p doubled; from a t or a standard error with the normal distribution when no p is given)."),
    "gap_sd": (
        "reproduction", "gap in standard deviations of the outcome",
        "|reproduced - published| / SD of the outcome; defined for linear models with an outcome SD."),
    "published_effect_sd": (
        "reproduction", "published effect in SD of the outcome", "|published| / SD of the outcome (linear models)."),
    "close": (
        "reproduction", "close",
        "Closeness condition of 'reproduced': relative gap <= 0.25, or gap < 0.10 SD of the outcome. Without an "
        "outcome SD only the 25% clause can hold (the study's convention)."),
    "reproduced_p020": (
        "reproduction", "reproduced with p < 0.20",
        "The same as 'reproduced' with a reproduced p below 0.20 in place of at most 0.10 (the variant the paper "
        "also reports)."),
    "reproduced_null_standard": (
        "reproduction", "reproduced (null claim)",
        "For a null claim: 1 if our estimate of the paper's own specification has a p-value of 0.10 or more under "
        "the paper's inference, as the paper reports. Computed for every claim; read it only for null claims."),
    "published_n": (
        "reproduction", "published number of observations",
        "Observations of the main estimate as config.yaml records them (n_obs)."),
    "reproduced_n": (
        "reproduction", "reproduced number of observations",
        "Observations of our reproduction, read from reproduction.md (the study's parsing rules)."),
    "n_gap": (
        "reproduction", "sample-size gap", "|reproduced N - published N| / published N."),
    "published_abs_t": (
        "reproduction", "published |t|",
        "|t| of the published main estimate from config.yaml: value / standard error; else a printed t; else a "
        "printed p converted to |z| (one-sided p doubled); else significance stars as a lower bound."),
    "materials": (
        "reproduction", "authors' materials",
        "What the package finder located (package_status.json finder.status): authors_data (data and code, or "
        "data only), code_only, or rebuilt (appendix only or nothing found)."),
    # ---- companion grid and sub-grid (section "The Authors' Own Choices")
    "has_companion": (
        "companion", "companion grid",
        "1 if the run has a companion grid: companion_spec.json restores at least one choice and "
        "companion_results.csv has a row without an error or distinct-estimand flag."),
    "companion_restored_choices": (
        "companion", "choices restored",
        "Choices in which the companion grid restores the paper's own option (companion_spec.json restored)."),
    "companion_share_supported": (
        "companion", "share supported, companion grid",
        "Share supported over the companion grid's specifications (same filter, inference and rule)."),
    "companion_share_supported_new": (
        "companion", "share supported, companion specifications not in the audited grid",
        "Share supported over the companion specifications that are not identical to an audited-grid "
        "specification (estimate rounded to 1e-10 and p to 1e-8)."),
    "companion_n_specifications": (
        "companion", "companion specifications", "Companion specifications that enter the share."),
    "subgrid_share_supported": (
        "companion", "share supported, sub-grid",
        "Share supported over the sub-grid: the companion specifications that take the paper's own option in every "
        "restored choice."),
    "subgrid_n_specifications": ("companion", "sub-grid specifications", "Specifications of the sub-grid."),
    "restored_exclusion_type": (
        "companion", "rating of the restored options",
        "How the audit had excluded the restored own options: invalid_only (all rated invalid), "
        "invalid_and_design (an invalid one and one excluded by grid design), design_only (all valid or "
        "questionable, excluded by grid design)."),
    # ---- choices (sections "Which Choices Matter" and "Distance from the Paper's Own Specification")
    "choice_ranges": (
        "choices", "range of each crossed choice",
        "For each crossed choice that takes two or more options among the specifications: the share supported "
        "under each option, and the range = largest minus smallest of those shares."),
    "top_choice_range": (
        "choices", "range of the most influential choice", "The largest range among the crossed choices."),
    "most_influential_choices": (
        "choices", "most influential choice",
        "The crossed choice(s) with the largest range (none when every range is zero)."),
    "choice_decides": (
        "choices", "a choice decides the result",
        "1 if some crossed choice has a share supported of at least 0.5 under one option and at most 0.1 under "
        "another."),
    "top_choice_range_null_p95": (
        "choices", "95th percentile of the largest range under permutation",
        "95th percentile of the largest range over permutations that shuffle one choice's option labels at a time "
        "among the specifications sharing all other crossed options (1,000 draws per choice)."),
    "top_choice_range_exceeds_null": (
        "choices", "largest range above its permutation 95th percentile",
        "1 if the largest range exceeds the 95th percentile of its own permutation distribution."),
    "own_specification_found": (
        "choices", "own specification identified",
        "1 if exactly one specification takes the paper's own option in every crossed choice (companion grid, or "
        "audited grid without one)."),
    "own_specification_supports": (
        "choices", "own specification keeps the claim",
        "1 if that specification (k = 0) has the published sign and designated p < 0.10."),
    "share_supported_by_choices_changed": (
        "choices", "share supported by choices changed (k)",
        "Share supported among the specifications whose options differ from the paper's own in k = 0, 1, 2, or 3 "
        "or more crossed choices; companion grid (audited grid without one) and audited grid."),
    "n_held_fixed_off_paper_option": (
        "choices", "choices held off the paper's option",
        "Held-fixed choices held at another listed option although the paper's option is listed, not restored by "
        "the companion grid and not an inference choice: the k = 0 specification then differs from the paper's."),
    # ---- the audited grid (sections "Choices, Options and the Grid", "Inference Fixed before Estimation")
    "n_choices": ("grid", "choices", "Choices (dimensions) the brainstormer lists in forks.json."),
    "n_options": ("grid", "options", "Options listed over all choices."),
    "n_options_valid": ("grid", "options rated valid", "Options the choice auditor rates valid."),
    "n_options_questionable": ("grid", "options rated questionable", "Options rated questionable."),
    "n_options_invalid": ("grid", "options rated invalid", "Options rated invalid."),
    "n_crossed_choices": ("grid", "crossed choices", "Choices the audited grid crosses."),
    "n_grid_specifications": (
        "grid", "audited grid size", "Specifications of the audited grid as the auditor counts them (audit.json)."),
    "n_held_fixed_choices": ("grid", "choices held fixed", "Choices the audited grid holds fixed at one option."),
    "held_fixed_at_paper_option_share": (
        "grid", "share of held-fixed choices at the paper's option",
        "Share of the held-fixed choices held at the paper's own option."),
    "holds_invalid_option_fixed": (
        "grid", "holds an invalid option fixed", "1 if a held-fixed choice is held at an option rated invalid."),
    "crossed_options_questionable_share": (
        "grid", "share of crossed options rated questionable",
        "Share of the options used in crossed choices that are rated questionable."),
    "specifications_with_questionable_share": (
        "grid", "share of specifications using a questionable option",
        "Share of the specifications that use at least one crossed option rated questionable."),
    "share_supported_all_valid": (
        "grid", "share supported, all-valid specifications",
        "Share supported over the specifications whose crossed options are all rated valid."),
    "designated_inference": (
        "grid", "designated inference",
        "The inference method the choice auditor designates (audit.json) and its family by the study's keyword "
        "rules: wild_bootstrap, randomization, analytic, placebo_or_simulated_null, other_resampling."),
    # ---- audits inside the pipeline (section "Audits and Open Findings", appendix "Roles and Gates")
    "pipeline_audits": (
        "pipeline", "audits and gate outcomes",
        "From gates.log (cut at the last G6 PASS) and implementation_audit*.md: implementation-audit rounds, "
        "blockers and major findings in the first and last round, reproduction-audit rounds and verdicts, sample "
        "review items fixable at the first and last review, and failures of GC, G2 and G6."),
    # ---- author commentary (section "What the Papers Say about Their Riskiest Choices")
    "commentary_classes": (
        "commentary", "classes of the riskiest choices",
        "Number of riskiest choices the author-commentary agent codes in each class (silent, partially "
        "anticipated, claim not supported in our rerun, anticipated and defended, disclosed but downplayed)."),
    # ---- earlier measures of the study (draft and per-run report)
    "surv30h": (
        "earlier", "surv30h",
        "Earlier robustness score: share of the specifications with the published sign, designated p < 0.30 and "
        "estimate / published >= 0.5 (size rule skipped where the measure audit ruled units not comparable)."),
    "surv30h_parts": (
        "earlier", "surv30h failure classes",
        "Shares of the specifications that fail on sign, on p (published sign, p >= 0.30), or only on size; with "
        "surv30h they sum to one."),
    "companion_surv30h": ("earlier", "companion surv30h", "surv30h over the companion grid's specifications."),
    "strict_survival": (
        "earlier", "survival rate (survival.py)",
        "The per-run report's survival rate: share of audited-grid rows (errors and distinct estimands left out) "
        "with the direction of config.yaml and p < 0.05, or the grid's joint 5% decision column when present."),
    "strict_sign_consistency": (
        "earlier", "sign-consistency rate (survival.py)", "Share of the same rows with the published direction."),
    "companion_strict_survival": (
        "earlier", "companion survival rate (survival.py --companion)",
        "The same survival rate over all companion_results.csv rows."),
    # ---- diagnostics
    "measure_review_flags": (
        "diagnostics", "grids flagged for the measure audit",
        "The study's criteria for auditing a grid's p-values and units by hand: one-sided inference labels, a "
        "joint decision column, estimators on different scales, and unit-scale outliers (U1-U5). A flagged grid "
        "may need `fixes` in measure_inputs.json."),
    "inputs_used": (
        "diagnostics", "inputs used", "Which optional inputs were given and where each input value came from."),
    "warnings": ("diagnostics", "warnings", "Problems met while computing the measures."),
}

GROUP_TITLES = {"outcome": "Outcomes", "specifications": "Specifications", "reproduction": "Reproduction",
                "companion": "Companion grid", "choices": "Choices", "grid": "Audited grid",
                "pipeline": "Audits in the pipeline", "commentary": "Author commentary",
                "earlier": "Earlier measures", "diagnostics": "Diagnostics"}


# ================================================================================================ small helpers
def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _clean(v: Any) -> Any:
    """JSON-safe plain Python values (numpy scalars converted, NaN -> None)."""
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_clean(x) for x in v]
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if v is pd.NA or v is pd.NaT:
        return None
    return v


def _ppf(q: float) -> float:
    return _ND.inv_cdf(q)


def _p_from_t(t: float) -> float:
    """Two-sided normal p of a t (or z) statistic."""
    return math.erfc(abs(t) / math.sqrt(2.0))


def signed_z(p, sign_ok) -> np.ndarray:
    """z = Phi^-1(1 - p/2), p clipped to [1e-4, 1]; positive where the estimate has the published sign."""
    p = np.clip(np.asarray(p, float), P_FLOOR, 1.0)
    z = np.array([_ppf(1 - x / 2) for x in p.ravel()], float).reshape(p.shape)
    return np.where(np.asarray(sign_ok, bool), z, -z)


def _read_json(p: Path) -> dict:
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _read_yaml(p: Path) -> dict:
    with open(p, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _gating_estimate(cfg: dict) -> dict | None:
    return next((e for e in (cfg.get("headline_estimates") or []) if e.get("gating", True)), None)


# ================================================================================================ copies of core code
# grid_mask and annotate: verbatim logic of core/scripts/survival.py; parse_number, parse_repro_table and
# match_target: verbatim logic of core/scripts/gate_g0.py. Copied so that this module imports nothing from the core
# scripts (which are run as scripts, not imported); tests/test_metrics.py checks them against the core files.
ALPHA = 0.05


def significance(g: pd.DataFrame, alpha: float, joint_col: str | None) -> tuple[pd.Series, str]:
    if joint_col and joint_col in g.columns:
        col = g[joint_col]
        if col.dtype == object:
            col = col.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "t"})
        return col.astype(bool), f"joint rule from column '{joint_col}'"
    return g["p_value"] < alpha, f"p_value < {alpha}"


def annotate(grid: pd.DataFrame, direction: str, alpha: float = ALPHA,
             joint_col: str | None = "sig_joint_5pct") -> pd.DataFrame:
    g = grid.copy()
    for col in ("estimate", "p_value"):
        if col not in g.columns:
            raise ValueError(f"grid has no '{col}' column")
        g[col] = pd.to_numeric(g[col], errors="coerce")
    g["err"] = g.get("err", pd.Series(np.nan, index=g.index))
    g["has_err"] = g["err"].notna() & (g["err"].astype(str).str.strip() != "")
    g["distinct_estimand"] = (
        g.get("distinct_estimand", pd.Series(False, index=g.index)).fillna(False).astype(bool))
    want = 1 if str(direction).lower().startswith("pos") else -1
    g["sign_ok"] = np.sign(g["estimate"]) == want
    g["sig"], rule = significance(g, alpha, joint_col)
    g["survives"] = g["sign_ok"] & g["sig"]
    g.attrs["sig_rule"] = rule
    return g


def grid_mask(g: pd.DataFrame, audit: dict) -> pd.Series:
    """Rows of the audited grid: every crossed choice at an option the audit lists, held-fixed choices at the held
    option (or missing), row_type 'grid'/'cell'/empty when the column exists."""
    m = pd.Series(True, index=g.index)
    for c in (audit.get("recommended_grid", {}) or {}).get("crossed_dimensions", []):
        d, ok = c["dimension"], set(map(str, c.get("option_ids", [])))
        if d in g.columns and ok:
            m &= g[d].astype(str).isin(ok)
    for h in (audit.get("recommended_grid", {}) or {}).get("held_fixed", []) or []:
        d, opt = h.get("dimension"), h.get("option_id")
        if d in g.columns and opt is not None and g[d].astype(str).eq(str(opt)).any():
            m &= g[d].astype(str).eq(str(opt)) | g[d].isna()
    if "row_type" in g.columns:
        m &= g["row_type"].astype(str).str.strip().str.lower().isin({"grid", "cell", "nan", ""})
    return m


def parse_number(s: str):
    s = s.strip().replace(",", "").replace("*", "").replace("−", "-").replace("–", "-")
    m = re.search(r"(?<![A-Za-z_])-?\d+\.?\d*(?:[eE]-?\d+)?", s)
    return float(m.group()) if m else None


def parse_repro_table(md: str) -> dict[str, dict]:
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
        unr = re.search(r"unrounded[^0-9\-−]{0,12}([-−]?\d*\.\d+(?:[eE]-?\d+)?)", line, re.I)
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


# ================================================================================================ choice families
# Keyword families of crossed-choice names (study code; first matching rule wins).
FAMILY_RULES = [
    ("inference", r"cluster|inference|bootstrap|(^|_)se($|_)|std_err"),
    ("outliers", r"outlier|winsor|trim|tails|bounds"),
    ("rdd_iv_design", r"bandwidth|kernel|donut|poly|running_var|^rank_|ranking_|instrument|^instr_|^iv_|inst_scale"),
    ("fixed_effects", r"(^|_)fe($|_)|fixed_effect|trends?$|time_trend"),
    ("comparison_group", r"control_group|control_pool|control_matching|control_exclusion|control_shock|comparison|"
                         r"counterfactual|contrast|match|caliper|pscore|ps_covariates|common_support|replacement_ties|"
                         r"bias_correction|^baseline$"),
    ("controls", r"control|ctrl|covariate|confounder"),
    ("estimator_design", r"estimator|design|specification|lag_length|johansen|slope_heterogeneity|het_form"),
    ("weighting", r"weighting$|weights?$"),
    ("functional_form", r"functional_form|transform|^spec$"),
    ("treatment_definition", r"treat|(^|_)post(_|$)|onset|adoption|_dates|timing|decimali|exposure|implicated|scandal|"
                             r"family_list|family_set|merger|closure|event_set|multiple_treatment|repeat|overlap|"
                             r"contamination|incorp|inc_state|reinc|prior_membership|group_split|cutoff|intensity|"
                             r"similarity|^change$|grouping|classification|confounding_laws|year_assignment|"
                             r"^during$|investor_class|insider|transaction_type"),
    ("window", r"window|period|horizon|^week$|^periods$|time_index|ea_day"),
    ("sample", r"sample|universe|filter|screen|exclu|balance|complete|delaware|state_|industry|exchange|share_code|"
               r"security|price|coverage|requirement|inclusion|existence|public|link|merge|data_quality|panel_rows|"
               r"issuer|deal|composition|unit_of_analysis|min_base|lowprc|after_hours|purpose_type|large_family|"
               r"currency|size_cut|excluded_days|lag_rule|threshold"),
    ("outcome_definition", r"outcome|measure|model|accrual|^da_|return|forecast|consensus|actual_eps|deflator|"
                           r"normali|scaling|aggregation|benchmark|market_index|adjustment|patent|green|count|"
                           r"rounding|fill|ratio_|breakpoint|portfolio|port_weight|rebalancing|sort_variable|"
                           r"breadth|liq|spread|vega|default_risk|style|unclassified|ddc|bond_def|carrier|"
                           r"asset_distance|attention|expcov|peer_performance|reference_date|formation_lag|"
                           r"definition|def$|source|construct|ownership|size_specification|sales|news|nl_|"
                           r"tie_|volfilter|jump"),
]
FAMILY_EXPLICIT = {"outcome_timing": "window", "industry_adjustment": "outcome_definition",
                   "week": "outcome_definition", "investor_class": "sample", "insider_group": "sample",
                   "insider_type": "sample", "transaction_type": "sample"}


def family_of(name: str) -> str:
    n = str(name).lower()
    if n in FAMILY_EXPLICIT:
        return FAMILY_EXPLICIT[n]
    for fam, rx in FAMILY_RULES:
        if re.search(rx, n):
            return fam
    return "other"


def classify_inference(method: str) -> str:
    """Family of the designated inference method from its description (the study's keyword rules)."""
    m = method.lower()
    if re.search(r'randomi[sz]ation|permutation|fisher', m) and not m.startswith(
            'wild cluster restricted (wcr) bootstrap-t, null imposed: two-way'):
        return 'randomization'
    if re.search(r'no bootstrap|analytic; no bootstrap|analytic, no bootstrap', m):
        return 'analytic'
    if 'wild' in m and re.search(r'only for cells with fewer than 42|when g < 42|only as the pre-specified fallback', m):
        return 'analytic'
    if re.search(r'\bwild\b|\bwcr\b|\bwre\b|wcu', m):
        return 'wild_bootstrap'
    if re.search(r'bootstrap', m):
        return 'other_resampling'
    if re.search(r'placebo|pseudo|non-event|simulated', m):
        return 'placebo_or_simulated_null'
    return 'analytic'


_FINDER_MAP = {"authors_data_and_code": "authors_data", "authors_data_only": "authors_data",
               "authors_code_only": "code_only", "appendix_only": "rebuilt", "none_found": "rebuilt"}


# ================================================================================================ the run
class _Run:
    """Lazy reader of one run directory."""

    def __init__(self, run_dir: Path):
        self.dir = Path(run_dir)
        self._cache: dict[str, Any] = {}

    def path(self, name: str) -> Path:
        return self.dir / name

    def exists(self, name: str) -> bool:
        return (self.dir / name).exists()

    def _get(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    @property
    def cfg(self) -> dict:
        return self._get("cfg", lambda: _read_yaml(self.path("config.yaml")) if self.exists("config.yaml") else {})

    @property
    def audit(self) -> dict:
        return self._get("audit", lambda: _read_json(self.path("audit.json")) if self.exists("audit.json") else {})

    @property
    def forks(self) -> dict:
        return self._get("forks", lambda: _read_json(self.path("forks.json")) if self.exists("forks.json") else {})

    @property
    def spec(self) -> dict | None:
        return self._get("spec", lambda: _read_json(self.path("companion_spec.json"))
                         if self.exists("companion_spec.json") else None)

    def text(self, name: str) -> str | None:
        return self._get("text:" + name, lambda: self.path(name).read_text(encoding="utf-8", errors="replace")
                         if self.exists(name) else None)

    def csv(self, name: str) -> pd.DataFrame | None:
        return self._get("csv:" + name, lambda: pd.read_csv(self.path(name)) if self.exists(name) else None)


def _gating_rows(g: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, bool]:
    """Keep only the gating main estimate's rows when a grid also holds rows of another estimate (headline_id)."""
    if "headline_id" not in g.columns or g["headline_id"].astype(str).nunique() < 2:
        return g, False
    hid = next((e.get("id") for e in (cfg.get("headline_estimates") or []) if e.get("gating", True)), "h1")
    return g[g["headline_id"].astype(str) == str(hid)].copy(), True


def _fix_arrays(g: pd.DataFrame, pub: float, rule: dict | None, warn: list, companion: bool = False) -> dict:
    """The study's measure-audit fixes, applied mechanically (surv30h_build.final / common._fix_arrays): unit factors
    and not-comparable cells (size rule only), a co-required test's p (the larger p), a one-sided p made two-sided,
    and the sign measured from a nonzero benchmark."""
    n = len(g)
    est = g.estimate.to_numpy(float)
    p = g.p_value.to_numpy(float)
    s = g.sign_ok.to_numpy(bool)
    out = dict(sign_raw=s.copy(), p_raw=p.copy(), ratio_raw=est / pub, sign_ok=s.copy(), p=p.copy(),
               ratio=est / pub, not_comparable=np.zeros(n, bool), joint_p=np.zeros(n, bool))
    if rule is None or rule.get("unclear"):
        return out
    factor = np.ones(n)
    nocomp = np.zeros(n, bool)
    for u in rule.get("units") or []:
        sel = np.ones(n, bool)
        if u.get("dimension"):
            if u["dimension"] not in g.columns:
                if not companion:
                    warn.append(f"fixes: unit rule names choice {u['dimension']!r}, not a grid column; skipped")
                continue
            sel = g[u["dimension"]].astype(str).to_numpy() == str(u.get("option"))
        if u.get("verdict") == "factor":
            factor = np.where(sel, factor * float(u["factor"]), factor)
        elif u.get("verdict") == "not_comparable":
            nocomp |= sel
    pp_ = p.copy()
    jflag = np.zeros(n, bool)
    jc = rule.get("joint_p_column")
    if jc:
        if jc in g.columns:
            jp = pd.to_numeric(g[jc], errors="coerce").to_numpy(float)
            jflag = np.nan_to_num(jp, nan=-np.inf) > p
            pp_ = np.fmax(pp_, jp)
        elif not companion:
            warn.append(f"fixes: joint_p_column {jc!r} is not a grid column; not applied")
    ss, rr = s.copy(), est / (pub * factor)
    b = rule.get("benchmark")
    if b is not None and not (isinstance(b, float) and math.isnan(b)):
        b = float(b)
        ss = np.sign(est - b) == np.sign(pub - b)
        rr = (est - b) / ((pub - b) * factor)
    if rule.get("p_sidedness") == "one_published_direction":
        pp_ = np.where(ss, np.minimum(1, 2 * pp_), np.minimum(1, 2 * (1 - pp_)))
    rr = np.where(nocomp, np.nan, rr)
    out.update(sign_ok=ss, p=pp_, ratio=rr, not_comparable=nocomp, joint_p=jflag)
    return out


class _Cells:
    """Scored specifications of the audited grid (or the companion grid)."""

    def __init__(self, g: pd.DataFrame, dims: list[str], f: dict):
        self.g = g.reset_index(drop=True)
        self.dims = dims
        self.f = f
        self.sign = np.asarray(f["sign_ok"], bool)
        self.p = np.asarray(f["p"], float)
        self.ratio = np.asarray(f["ratio"], float)
        self.sup = self.sign & (self.p < P_CUT)
        self.n = len(self.g)


def _audited_cells(run: _Run, pub: float, fixes: dict | None, warn: list) -> tuple[_Cells, pd.DataFrame]:
    """Specifications of the audited grid that enter the share (gating estimate's rows only, no error, not a distinct
    estimand, numeric estimate and p-value) and the masked rows before that filter."""
    grid = run.csv("grid_results.csv")
    if grid is None:
        raise FileNotFoundError("grid_results.csv missing")
    direction = "negative" if pub < 0 else "positive"
    g0 = annotate(grid[grid_mask(grid, run.audit)], direction)
    g0, filtered = _gating_rows(g0, run.cfg)
    if filtered:
        warn.append("grid_results.csv holds rows of several headline estimates; only the gating estimate's rows "
                    "are scored")
    g = g0[~g0.has_err & ~g0.distinct_estimand & g0.p_value.notna() & g0.estimate.notna()].copy()
    crossed = [c["dimension"] for c in (run.audit.get("recommended_grid", {}) or {}).get("crossed_dimensions", [])]
    dims = [d for d in crossed if d in g.columns and g[d].astype(str).nunique() >= 2]
    f = _fix_arrays(g, pub, fixes, warn)
    return _Cells(g, dims, f), g0


def _companion_frames(run: _Run, pub: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series] | None:
    """(raw gating rows, annotated gating rows, filter mask) of companion_results.csv, or None."""
    raw = run.csv("companion_results.csv")
    if raw is None:
        return None
    direction = "negative" if pub < 0 else "positive"
    raw_g, _ = _gating_rows(raw, run.cfg)
    cg = annotate(raw, direction)
    cg, _ = _gating_rows(cg, run.cfg)
    keep = ~cg.has_err & ~cg.distinct_estimand & cg.p_value.notna() & cg.estimate.notna()
    return raw_g, cg, keep


# ================================================================================================ reproduction
_P_PAT = re.compile(r"(?<![A-Za-z_])p(?:[- ]?val(?:ue)?)?\s*(?:\((?:two|one)[- ]sided\)\s*)?(?:=|<|≈|~|:|\s)\s*"
                    r"(\d*\.?\d+(?:[eE][-+]?\d+)?)", re.I)
_T_PAT = re.compile(r"(?<![A-Za-z_])(?:t|z|t-stat(?:istic)?|z-stat(?:istic)?)\s*(?:=|:)\s*([-+\u2212]?\d*\.?\d+)")
_SE_PAT = re.compile(r"(?<![A-Za-z_])(?:SE|s\.e\.|std\.? ?err(?:or)?)\s*(?:=|:)?\s*\(?\s*(\d*\.?\d+(?:[eE][-+]?\d+)?)",
                     re.I)
_PUB_PREFIX = re.compile(r"publish|paper['\u2019]s", re.I)


def _own_matches(txt: str, pat: re.Pattern):
    """Matches of pat whose sentence, up to the match, does not mention the published (paper's) value."""
    for m in pat.finditer(txt):
        st = max(txt.rfind(". ", 0, m.start()), txt.rfind(";", 0, m.start())) + 1
        if not _PUB_PREFIX.search(txt[st:m.start()]):
            yield m


def _parse_reproduced_inference(row: str, rep: float) -> tuple[float, str]:
    """Heuristic: the reproduced p (else t, else SE) printed in the reproduced cell of the main estimate's row of
    reproduction.md, then in the rest of the row (the published cell and any sentence that first mentions the
    published value are skipped). Returns (two-sided p, how)."""
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    parts = [cells[2]] + cells[3:] if len(cells) >= 3 else [row]
    for txt in parts:
        txt = txt.replace("\u2212", "-")
        one = bool(re.search(r"one[- ]sided", txt, re.I)) and not re.search(r"two[- ]sided", txt, re.I)
        for m in _own_matches(txt, _P_PAT):
            p = float(m.group(1))
            if 0 <= p <= 1:
                return (min(2 * p, 1.0) if one else p), "p"
        for m in _own_matches(txt, _T_PAT):
            return _p_from_t(float(m.group(1))), "t"
        for m in _own_matches(txt, _SE_PAT):
            if float(m.group(1)) > 0 and rep == rep:
                return _p_from_t(rep / float(m.group(1))), "se"
    return math.nan, ""


def published_abs_t(est: dict) -> tuple[float, str]:
    """|t| of the published main estimate from config.yaml (the study's pooled_panel.published_t)."""
    v, se = _num(est.get("value")), _num(est.get("std_error"))
    if v == v and se == se and se > 0:
        return abs(v / se), "se"
    s = " ".join(str(est.get(k) or "") for k in ("t_or_p", "std_error"))
    t = re.search(r"\b[tz](?:-stat(?:istic)?)?\s*=\s*([-+]?\d*\.?\d+)", s)
    if t:
        return abs(float(t.group(1))), "t"
    pm = re.search(r"\bp\s*([=<])\s*(0?\.\d+|1(?:\.0+)?)", s)
    if pm:
        pv = float(pm.group(2))
        if pv == 0:
            pv, bound = 0.5 * 10 ** -len(pm.group(2).split(".")[-1]), True
        else:
            bound = pm.group(1) == "<"
        one = re.search(r"one-sided", s, re.I) and not re.search(r"two-sided", s, re.I)
        pv = min(2 * pv, 1.0) if one else pv
        return float(_ppf(1 - pv / 2)), ("p_one_sided" if one else "p") + ("_bound" if bound else "")
    lv = re.search(r"significant at the (\d+)% level", s, re.I)
    if lv:
        return float(_ppf(1 - float(lv.group(1)) / 200)), "stars_bound"
    return math.nan, ""


# observations: the study's parsing rules for the published and reproduced N
_NUM = r"(?<![A-Za-z_\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?!\d)(?!\.\d)(?!,\d{3})"
_NLAB = re.compile(r"(?<![A-Za-z_])N\b[^0-9|\n]{0,40}?" + _NUM)
_XNUM = r"(?<![-–/])" + _NUM
_PAIRS = [re.compile(_XNUM + r"[^0-9()\n]{0,30}\((?:the\s+)?(?:paper|published)[^0-9)\n]{0,15}" + _NUM),
          re.compile(_XNUM + r"\s*\[" + _NUM + r"\]"),
          re.compile(_XNUM + r"[^0-9\n]{0,30}?\b(?:vs\.?|versus)\s*(?:the\s+)?(?:paper['’]?s?|published)?[^0-9\n]{0,10}"
                     + _NUM)]


def _to_int(s: str) -> int:
    return int(s.replace(",", ""))


def _first_int(s) -> int | None:
    m = re.search(_NUM, str(s))
    return _to_int(m.group(1)) if m else None


def published_n(est: dict) -> int | None:
    v = est.get("n_obs")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    s = str(v or "").strip()
    return _first_int(s) if re.match(r"^~?\s*\d", s) else None


def _cells_of(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_n_label(lab: str) -> bool:
    lab = lab.strip("*` ").lower()
    return (bool(re.match(r"^(h1[_ ]n(?![a-z])|n\b|n[_ (]|n\d|obs\b|observations|number of obs|sample size)", lab))
            and not re.search(r"publish|paper", lab))


def reproduced_n(md: str, est: dict, pn: int | None) -> int | None:
    got = match_target(est, parse_repro_table(md))
    if got:
        c = _cells_of(got["row"])
        if len(c) >= 3:
            m = re.search(r"(?<![A-Za-z_])[Nn]\s*(?:=|:)?\s*~?\s*" + _NUM, c[2])
            if m:
                return _to_int(m.group(1))
    if pn is None:
        return None
    lines = md.splitlines()
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        c = _cells_of(line)
        if len(c) < 3 or not _is_n_label(c[0]):
            continue
        nums = [_first_int(x) for x in c[1:4]]
        if sum(x is not None for x in nums) < 2:
            continue
        if nums.count(pn) >= 2:
            return pn
        if pn in nums:
            return next(x for x in nums if x is not None and x != pn)
    pn_pat = re.compile(r"(?<![\d,.])(" + re.escape(f"{pn:,}") + "|" + str(pn) + r")(?![\d,]\d)")
    for line in lines:
        for pat in _PAIRS:
            for m in pat.finditer(line):
                if _to_int(m.group(2)) == pn:
                    return _to_int(m.group(1))
    for line in lines:
        if re.search(r"match|exact|identical", line, re.I) and not re.search(r"\bnot\b|\bno\b|differ|mismatch|n't",
                                                                            line, re.I):
            if any(_to_int(m.group(1)) == pn for m in _NLAB.finditer(line)):
                return pn
    for line in lines:
        if not pn_pat.search(line):
            continue
        cand = [x for x in (_to_int(m.group(1)) for m in _NLAB.finditer(line)) if x != pn and x > 1]
        if cand:
            return cand[0]
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        c = _cells_of(line)
        if len(c) >= 2 and _is_n_label(c[0]):
            nums = [_first_int(x) for x in c[1:] if _first_int(x) is not None]
            if len(nums) == 1:
                return nums[0]
    return None


def _reproduction(run: _Run, est: dict | None, inp: dict, used: dict, warn: list) -> dict:
    out: dict[str, Any] = {}
    pub = _num(est.get("value")) if est else math.nan
    md = run.text("reproduction.md")
    rep, row = math.nan, None
    if est is not None and md is not None:
        got = match_target(est, parse_repro_table(md))
        if got:
            rep, row = float(got["reproduced"]), got["row"]
    if md is None:
        warn.append("reproduction.md missing")
    elif row is None:
        warn.append("reproduction.md: no table row matches the gating main estimate")
    out["published_estimate"] = pub
    out["reproduced_estimate"] = rep
    have = pub == pub and pub != 0 and rep == rep
    dev = abs(rep - pub) / abs(pub) if have else math.nan
    out["relative_deviation"] = dev
    out["same_sign"] = (bool(np.sign(rep) == np.sign(pub)) if have else None)
    # p-value under the paper's own inference
    rp, src = math.nan, None
    sided = str(inp.get("reproduced_p_sided") or "two").lower()
    if inp.get("reproduced_p") is not None:
        rp, src = float(inp["reproduced_p"]), "inputs: reproduced_p"
        if sided.startswith("one"):
            rp, src = min(2 * rp, 1.0), "inputs: reproduced_p (one-sided, doubled)"
    elif inp.get("reproduced_t") is not None:
        rp, src = _p_from_t(float(inp["reproduced_t"])), "inputs: reproduced_t (normal)"
    elif inp.get("reproduced_se") is not None and _num(inp["reproduced_se"]) > 0 and rep == rep:
        rp, src = _p_from_t(rep / float(inp["reproduced_se"])), "inputs: reproduced_se (normal)"
    elif row is not None:
        rp, how = _parse_reproduced_inference(row, rep)
        src = f"reproduction.md ({how}, heuristic)" if how else None
    used["reproduced_p"] = src
    out["reproduced_p"] = rp
    out["_reproduced_p_source"] = src
    # closeness
    sd = _num(inp.get("outcome_sd"))
    nonlinear = inp.get("nonlinear")
    nonlinear = bool(nonlinear) if nonlinear is not None and not (isinstance(nonlinear, float) and nonlinear != nonlinear) else False
    used["outcome_sd"] = "inputs" if sd == sd else None
    lin_sd = (not nonlinear) and sd == sd and sd > 0
    absdiff = abs(rep - pub) if have else math.nan
    out["gap_sd"] = absdiff / sd if lin_sd and have else math.nan
    out["published_effect_sd"] = abs(pub) / sd if lin_sd and pub == pub else math.nan
    close = None
    sd_known = lin_sd or nonlinear
    if have:
        if dev <= .25 or (lin_sd and out["gap_sd"] < .10):
            close = True
        elif sd_known:
            close = False      # neither clause holds (the SD clause does not apply to a nonlinear model)
    out["close"] = close
    out["_sd_clause"] = ("applied" if lin_sd else "not applicable (nonlinear model)" if nonlinear
                         else "not evaluated (no outcome_sd input)")
    for key, ok_p in (("reproduced", lambda x: x <= .10), ("reproduced_p020", lambda x: x < .20)):
        if not have or rp != rp:
            out[key] = None
        elif not (out["same_sign"] and ok_p(rp)):
            out[key] = 0                       # fails sign or p whatever the closeness
        else:
            out[key] = None if close is None else int(close)
    out["reproduced_null_standard"] = (int(rp >= .10) if rp == rp else None)
    # G0
    gl = run.text("gates.log") or ""
    g0 = re.findall(r"\tG0\t(PASS|FAIL)\t([^\n]*)", gl)
    if g0:
        out["g0_strict_match"] = int(g0[-1][0] == "PASS")
    else:   # no G0 decision: a finished run never passed G0 (the study's 0); a run in progress is undecided
        out["g0_strict_match"] = 0 if re.search(r"\tG6\tPASS", gl) else None
        out["_g0_note"] = "no G0 decision in gates.log"
    # N
    pn = published_n(est) if est else None
    rn = reproduced_n(md, est, pn) if (md is not None and est is not None) else None
    out["published_n"], out["reproduced_n"] = pn, rn
    out["n_gap"] = abs(rn - pn) / pn if (pn and rn is not None) else math.nan
    t, tsrc = published_abs_t(est) if est else (math.nan, "")
    if t != t and inp.get("published_abs_t") is not None:
        t, tsrc = abs(float(inp["published_abs_t"])), "inputs: published_abs_t"
    out["published_abs_t"] = t
    out["_published_abs_t_source"] = tsrc
    # materials
    ps = _read_json(run.path("package_status.json")) if run.exists("package_status.json") else {}
    finder = (ps.get("finder") or {}).get("status")
    out["materials"] = {"class": _FINDER_MAP.get(finder), "finder_status": finder} if finder else None
    return out


# ================================================================================================ options
def _options(run: _Run) -> pd.DataFrame:
    """Every listed option with its audit verdict and its place in the audited grid (the study's load_options)."""
    au = run.audit
    verd = {(v.get("dimension"), str(v.get("option_id"))): v for v in au.get("verdicts") or []}
    rg = au.get("recommended_grid") or {}
    crossed = {c["dimension"]: set(map(str, c.get("option_ids") or [])) for c in rg.get("crossed_dimensions") or []}
    held = {h.get("dimension"): str(h.get("option_id")) for h in rg.get("held_fixed") or []}
    rows = []
    for d in run.forks.get("dimensions") or []:
        did = d.get("id")
        status = "crossed" if did in crossed else ("held_fixed" if did in held else "neither")
        for o in d.get("options") or []:
            oid = str(o.get("id"))
            v = verd.get((did, oid)) or {}
            in_grid = (oid in crossed[did]) if status == "crossed" else (status == "held_fixed" and oid == held[did])
            rows.append(dict(dimension=did, option=oid, is_paper_choice=(oid == str(d.get("paper_choice"))),
                             verdict=(str(v.get("verdict")).upper() if v else None), dim_status=status,
                             in_grid=bool(in_grid)))
    return pd.DataFrame(rows, columns=["dimension", "option", "is_paper_choice", "verdict", "dim_status", "in_grid"])


def _grid_facts(run: _Run, opts: pd.DataFrame, cells: _Cells | None) -> dict:
    out: dict[str, Any] = {}
    fk_dims = run.forks.get("dimensions") or []
    vc = pd.Series([str(v.get("verdict")).upper() for v in run.audit.get("verdicts") or []]).value_counts()
    rg = run.audit.get("recommended_grid") or {}
    out["n_choices"] = len(fk_dims) if run.forks else None
    out["n_options"] = sum(len(d.get("options") or []) for d in fk_dims) if run.forks else None
    if run.audit:
        out["n_options_valid"] = int(vc.get("VALID", 0))
        out["n_options_questionable"] = int(vc.get("QUESTIONABLE", 0))
        out["n_options_invalid"] = int(vc.get("INVALID", 0))
        out["n_crossed_choices"] = len(rg.get("crossed_dimensions") or [])
        out["n_grid_specifications"] = (int(rg["n_cells"]) if rg.get("n_cells") is not None else None)
        method = str((run.audit.get("inference_designation") or {}).get("method", ""))
        out["designated_inference"] = {"family": classify_inference(method) if method else None, "method": method}
    if len(opts):
        H = opts[opts.dim_status == "held_fixed"]
        held = H[H.in_grid]
        hd = H.groupby("dimension", sort=False).agg(has_pc=("is_paper_choice", "any")).reset_index()
        hd = hd.merge(held[["dimension", "is_paper_choice", "verdict"]].rename(columns={"is_paper_choice": "held_pc"}),
                      on="dimension", how="left")
        hd["matched"] = hd.held_pc.notna()
        hd["held_pc"] = hd.held_pc.astype("boolean").fillna(False).astype(bool)
        out["n_held_fixed_choices"] = len(hd)
        out["held_fixed_at_paper_option_share"] = float(hd.held_pc.mean()) if len(hd) else math.nan
        out["holds_invalid_option_fixed"] = int(bool((hd.matched & (hd.verdict == "INVALID")).any()))
        X = opts[(opts.dim_status == "crossed") & opts.in_grid]
        out["crossed_options_questionable_share"] = float((X.verdict == "QUESTIONABLE").mean()) if len(X) else math.nan
        out["_held_table"] = hd
    if cells is not None and cells.n and cells.dims and len(opts):
        vmap = {(r.dimension, r.option): r.verdict for r in opts.itertuples()}
        V = np.array([[vmap.get((d, str(o))) for d, o in zip(cells.dims, row)]
                      for row in cells.g[cells.dims].astype(str).itertuples(index=False)], dtype=object)
        anyq = (V == "QUESTIONABLE").any(axis=1)
        allv = (V == "VALID").all(axis=1)
        out["specifications_with_questionable_share"] = float(anyq.mean())
        out["share_supported_all_valid"] = float(cells.sup[allv].mean()) if allv.any() else math.nan
        out["_n_all_valid"] = int(allv.sum())
    return out


# ================================================================================================ choices
def _choice_ranges(cells: _Cells) -> pd.DataFrame:
    rows = []
    for d in cells.dims:
        col = cells.g[d].astype(str).to_numpy()
        sh = pd.Series(cells.sup.astype(float)).groupby(col, sort=False).agg(["mean", "size"])
        rows.append(dict(dimension=d, family=family_of(d), best=float(sh["mean"].max()), worst=float(sh["mean"].min()),
                         n_options=len(sh), options={k: {"share_supported": float(v["mean"]), "n": int(v["size"])}
                                                     for k, v in sh.iterrows()}))
    t = pd.DataFrame(rows, columns=["dimension", "family", "best", "worst", "n_options", "options"])
    t["range"] = t.best - t.worst
    return t


def _permute_within(labels, strata, rng, R):
    labels = np.asarray(labels, np.int64)
    s = np.asarray(strata, np.int64).ravel()
    n = len(labels)
    base = np.argsort(s, kind="stable")
    keys = 4.0 * s[None, :] + rng.random((R, n))
    idx = np.argsort(keys, axis=1, kind="stable")
    out = np.empty((R, n), np.int64)
    out[:, base] = labels[idx]
    return out


def _option_shares(perm_labels, y, counts):
    R, n = perm_labels.shape
    m = len(counts)
    flat = (np.arange(R)[:, None] * m + perm_labels).ravel()
    sums = np.bincount(flat, weights=np.broadcast_to(y, (R, n)).ravel(), minlength=R * m).reshape(R, m)
    return sums / counts[None, :]


def _codes(values):
    codes, uniq = pd.factorize(pd.Series(list(values), dtype=object), sort=False)
    return np.asarray(codes, np.int64), len(uniq)


def top_range_null(cells: _Cells, R: int = NULL_DRAWS, seed: int = SEED, seed_index: int = 0) -> np.ndarray:
    """R draws of the largest range under the within-stratum permutation null (the study's rangenull_lib): each
    choice's option labels are permuted among the specifications that share the options of all other crossed
    choices; the draws of choice j use np.random.default_rng([seed, 0, seed_index, j])."""
    O = cells.g[cells.dims].astype(str).to_numpy()
    y = cells.sup.astype(float)
    codes = np.column_stack([_codes(O[:, j])[0] for j in range(len(cells.dims))])
    ranges = []
    for j in range(len(cells.dims)):
        lab, m = _codes(O[:, j])
        counts = np.bincount(lab, minlength=m).astype(float)
        if codes.shape[1] == 1:
            s = np.zeros(len(lab), np.int64)
        else:
            _, inv = np.unique(np.delete(codes, j, axis=1), axis=0, return_inverse=True)
            s = np.asarray(inv, np.int64).ravel()
        rng = np.random.default_rng([seed, 0, seed_index, j])
        sh = _option_shares(_permute_within(lab, s, rng, R), y, counts)
        ranges.append(sh.max(axis=1) - sh.min(axis=1))
    return np.vstack(ranges).max(axis=0)


# ---- own specification and choices changed (the study's s7_ownspec.match and b_paths)
_TOKEN_SPLIT = re.compile(r"__|[|;,=\s()\[\]{}:]+")
_INFDES_RX = re.compile(r"fixed by the (?:binding )?inference designation|part of the designated inference|"
                        r"is the designated (?:headline )?inference|^designated inference\b", re.I)


def _forks_info(forks: dict) -> tuple[dict, dict]:
    pc, o2d = {}, {}
    for d in forks.get("dimensions") or []:
        pc[d["id"]] = d.get("paper_choice")
        for o in d.get("options") or []:
            o2d.setdefault(str(o.get("id")), set()).add(d["id"])
    return pc, o2d


def _written_options(texts, o2d):
    out: dict[str, set] = {}
    for t in texts:
        for tok in _TOKEN_SPLIT.split(str(t)):
            dims = o2d.get(tok)
            if dims and len(dims) == 1:
                out.setdefault(next(iter(dims)), set()).add(tok)
    return out


def _tie_break(idx, texts, pc, o2d):
    rec = {i: _written_options(texts[i], o2d) for i in idx}
    dims = set().union(*[set(r) for r in rec.values()])
    varied = [d for d in sorted(dims) if len({frozenset(rec[i].get(d, set())) for i in idx}) > 1]
    keep = [i for i in idx if all(pc.get(d) is not None and rec[i].get(d) == {str(pc[d])} for d in varied)]
    return keep, varied


def _match_own(opt_table: pd.DataFrame, pc: dict, o2d: dict, texts: dict, ran_values: dict) -> dict:
    varied = [d for d in opt_table.columns if opt_table[d].nunique() >= 2]
    inf = [d for d in varied if family_of(d) == "inference"]
    if inf:
        raise RuntimeError(f"an inference choice varies across the specifications: {inf}")
    null = [d for d in varied if pc.get(d) is None]
    missing = [d for d in varied if pc.get(d) is not None and str(pc[d]) not in set(opt_table[d])]
    out = dict(i=None, reason=None, tie=False)
    if null:
        out["reason"] = "a varied choice has no paper option in forks.json"
        return out
    if missing:
        ran = all(str(pc[d]) in ran_values.get(d, set()) for d in missing)
        out["reason"] = ("every specification with the paper's option failed" if ran
                         else "the paper's option was never estimated")
        return out
    m = np.ones(len(opt_table), bool)
    for d in varied:
        m &= opt_table[d].to_numpy() == str(pc[d])
    idx = [int(x) for x in np.flatnonzero(m)]
    if not idx:
        out["reason"] = "no specification combines the paper's options"
    elif len(idx) == 1:
        out["i"] = idx[0]
    else:
        keep, _ = _tie_break(idx, texts, pc, o2d)
        if len(keep) == 1:
            out.update(i=keep[0], tie=True)
        else:
            out["reason"] = "several specifications match"
    return out


def _as_text(s: pd.Series) -> pd.Series:
    return s.map(lambda v: "<NA>" if pd.isna(v) else str(v))


def _record_dims(text) -> frozenset:
    """Choice names in one row's restored_dims record ('dim', 'dim=option' or 'dim:old->new', joined by ; | or ,)."""
    if not isinstance(text, str):
        return frozenset()
    names = set()
    for seg in re.split(r"[;|,]", re.sub(r"\([^()]*\)", "", text)):
        if seg.strip():
            m = re.match(r"^\s*([A-Za-z0-9_]+)\s*(?:[=:].*)?$", seg)
            if m is None:
                raise ValueError(f"unreadable restored_dims segment {seg!r}")
            names.add(m.group(1))
    return frozenset(names)


def _twin_flags(raw: pd.DataFrame, restored: list, choice: dict) -> dict:
    """Restored choices without a column: per row, True where the row keeps the audited option."""
    named = raw["restored_dims"].map(_record_dims) if "restored_dims" in raw.columns else \
        pd.Series([frozenset()] * len(raw), index=raw.index)
    cols = [d for d in choice if d in raw.columns]
    key = pd.concat([_as_text(raw[d]) for d in cols], axis=1).agg("|".join, axis=1) if cols else \
        pd.Series("", index=raw.index)
    out = {}
    for d in restored:
        if d in raw.columns:
            continue
        hit = named.map(lambda s, d=d: d in s).astype(bool)
        out[d] = ~(hit | ~key.isin(set(key[hit])))
    return out


def _subgrid_rows(run: _Run, raw: pd.DataFrame) -> pd.Series:
    """True for companion rows that take the paper's option in every restored choice (the study's subgrid_rows)."""
    spec = run.spec or {}
    choice = {d["id"]: d.get("paper_choice") for d in run.forks.get("dimensions") or []}
    restored = [e["dimension"] for e in spec.get("restored") or []]
    for e in spec.get("restored") or []:
        po, pc = str(e.get("paper_option")), choice.get(e["dimension"])
        if pc is not None and po != str(pc) and po.split(" ", 1)[0] == str(pc):
            po = str(pc)          # an option id followed by a description
        if pc is None or po != str(pc):
            raise RuntimeError(f"companion_spec.json paper_option of {e['dimension']} is not the forks.json "
                               "paper_choice")
    named = raw["restored_dims"].map(_record_dims) if "restored_dims" in raw.columns else \
        pd.Series([frozenset()] * len(raw), index=raw.index)
    stray = set().union(*named) - set(restored) if len(named) else set()
    if stray:
        raise RuntimeError(f"restored_dims names choices that companion_spec.json does not restore: {sorted(stray)}")
    cols = [d for d in choice if d in raw.columns]
    key = pd.concat([_as_text(raw[d]) for d in cols], axis=1).agg("|".join, axis=1) if cols else \
        pd.Series("", index=raw.index)
    sub = pd.Series(True, index=raw.index)
    for d in restored:
        if d in raw.columns:
            sub &= _as_text(raw[d]).eq(str(choice[d])).astype(bool)
        else:
            hit = named.map(lambda s, d=d: d in s).astype(bool)
            sub &= hit | ~key.isin(set(key[hit]))
    return sub


def _paths(run: _Run, cells: _Cells, g0: pd.DataFrame, comp: _Cells | None, comp_frames, has_companion: bool,
           held: pd.DataFrame | None) -> dict:
    """Own specification and the share supported by the number of choices changed (k)."""
    pc, o2d = _forks_info(run.forks)
    dims = cells.dims
    aud_table = pd.DataFrame({d: cells.g[d].astype(str).to_numpy() for d in dims})
    out: dict[str, Any] = {}
    use_comp = has_companion and comp is not None and comp.n > 0
    if use_comp:
        raw_g, cg, keep = comp_frames
        sc = cg[keep].reset_index(drop=True)
        dimcols = [c for c in raw_g.columns if c in pc]
        opt_table = pd.DataFrame({c: sc[c].astype(str).to_numpy() for c in dimcols})
        texts = {i: [sc.at[i, c] for c in ("cell_id", "restored_dims") if c in sc.columns] for i in range(len(sc))}
        res = _match_own(opt_table, pc, o2d, texts, {c: set(cg[c].astype(str)) for c in dimcols})
    else:
        texts = {i: [cells.g.at[i, "cell_id"]] if "cell_id" in cells.g.columns else [] for i in range(cells.n)}
        res = _match_own(aud_table, pc, o2d, texts, {d: set(g0[d].astype(str)) for d in dims if d in g0.columns})
    out["source"] = "companion grid" if use_comp else "audited grid"
    out["own_found"] = res["i"] is not None
    out["own_reason"] = res["reason"]
    if res["i"] is None:
        return out
    if any(pc.get(d) is None for d in dims):
        raise RuntimeError("a crossed choice has no paper option in forks.json")
    k_aud = np.zeros(cells.n, int) + sum((aud_table[d].to_numpy() != str(pc[d])).astype(int) for d in dims)
    if use_comp:
        varied = [d for d in dimcols if opt_table[d].nunique() >= 2]
        if any(pc.get(d) is None for d in varied):
            raise RuntimeError("a varied companion choice has no paper option")
        const = [d for d in dims if d not in varied]
        if set(dims) - set(dimcols) or any(opt_table[d].iloc[0] != str(pc[d]) for d in const):
            raise RuntimeError("a crossed choice is missing from, or fixed off the paper's option in, the companion grid")
        k_comp = np.zeros(len(sc), int) + sum((opt_table[d].to_numpy() != str(pc[d])).astype(int) for d in varied)
        restored = [e["dimension"] for e in (run.spec or {}).get("restored") or []]
        tw = _twin_flags(raw_g, restored, pc)
        for d, f in tw.items():
            t = f.loc[cg.index[keep]].to_numpy(bool)
            if t.any() and not t.all():
                k_comp += t.astype(int)
        sup_comp = comp.sup
    else:
        k_comp, sup_comp = k_aud, cells.sup
    if list(np.flatnonzero(k_comp == 0)) != [res["i"]]:
        raise RuntimeError("the own specification is not the only specification with k = 0")
    out["own_supports"] = bool(sup_comp[res["i"]])

    def bins(k, s):
        r = {}
        for b, f in (("k0", k == 0), ("k1", k == 1), ("k2", k == 2), ("k3plus", k >= 3)):
            r[b] = {"share_supported": float(s[f].mean()) if f.any() else None, "n": int(f.sum())}
        r["all"] = {"share_supported": float(s.mean()), "n": int(len(s))}
        return r
    out["by_k"] = {"companion_or_audited": bins(k_comp, sup_comp), "audited": bins(k_aud, cells.sup)}
    # held-fixed choices held off the paper's option (the k = 0 specification then differs from the paper's)
    if held is not None and len(held):
        restored = {e["dimension"] for e in (run.spec or {}).get("restored") or []} if use_comp else set()
        reason = {e.get("dimension"): str(e.get("why") or e.get("reason") or "")
                  for e in (run.audit.get("recommended_grid") or {}).get("held_fixed") or []}
        infdes = np.array([family_of(d) == "inference" or bool(_INFDES_RX.search(reason.get(d, "")))
                           for d in held.dimension], bool)
        rest = held.dimension.isin(restored).to_numpy()
        off = held.matched.to_numpy(bool) & held.has_pc.to_numpy(bool) & ~held.held_pc.to_numpy(bool) & ~rest & ~infdes
        out["held_off"] = int(off.sum())
    return out


# ================================================================================================ pipeline, commentary
_TALLY = re.compile(r"TALLY:\s*blockers\s*=\s*(\d+)\s*major\s*=\s*(\d+)\s*minor\s*=\s*(\d+)", re.I)
_FIXABLE_N = re.compile(r"FIXABLE n=(\d+)")


def _pipeline(run: _Run) -> dict | None:
    gl = run.text("gates.log")
    if gl is None:
        return None
    ev = []
    for line in gl.splitlines():
        if not line.strip() or line[0].isspace():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        gate, verdict = parts[1].strip(), parts[2].strip()
        reason = "\t".join(parts[3:]).strip() if len(parts) > 3 else ""
        if gate == "NOTE":
            reason, verdict = ((verdict + " " + reason).strip() if verdict != "orchestrator" else reason), "NOTE"
        ev.append((gate, verdict, reason))
    g6 = [i for i, e in enumerate(ev) if e[0] == "G6" and e[1] == "PASS"]
    finished = bool(g6)
    if g6:
        ev = ev[: g6[-1] + 1]
    by = lambda g: [e for e in ev if e[0] == g]  # noqa: E731
    gs, gc, g0a, g2, g6e = (by(g) for g in ("GS", "GC", "G0A", "G2", "G6"))
    items = lambda e: int(_FIXABLE_N.search(e[2]).group(1)) if e[1] == "FIXABLE" and _FIXABLE_N.search(e[2]) else 0  # noqa: E731
    files = sorted(run.dir.glob("implementation_audit*.md"), key=lambda p: (len(p.stem), p.stem))
    t = []
    for p in files:
        m = _TALLY.search(p.read_text(encoding="utf-8", errors="replace"))
        t.append(tuple(int(x) for x in m.groups()) if m else None)
    tt = [x for x in t if x is not None]
    return {
        "finished_g6": finished,
        "implementation_audit_rounds": len(t),
        "first_round_blockers": tt[0][0] if tt else None, "first_round_major": tt[0][1] if tt else None,
        "last_round_blockers": tt[-1][0] if tt else None, "last_round_major": tt[-1][1] if tt else None,
        "reproduction_audit_rounds": len(g0a), "reproduction_audit_verdicts": [e[1] for e in g0a],
        "sample_review_first_fixable_items": items(gs[0]) if gs else None,
        "sample_review_last_fixable_items": items(gs[-1]) if gs else None,
        "config_review_failures": sum(e[1] == "FAIL" for e in gc),
        "grid_gate_failures": sum(e[1] == "FAIL" for e in g2),
        "report_gate_failures": sum(e[1] == "FAIL" for e in g6e),
    }


COMMENTARY_CLASSES = ["silent", "partially-anticipated", "claim-contradicted-by-data", "anticipated-and-defended",
                      "disclosed-but-downplayed"]
_RX_CLASS = re.compile(r"Classification:\s*\**\s*`?([a-z\-]+)`?", re.I)
_EXTRA = re.compile(r"^## *(appendix|note)|other author claim|not selected|not among the (top|selected)|"
                    r"outside the (top|three|four|five|six|selected)|closing note|not a ranked fork|"
                    r"^## *\d*\.? *result in counts", re.I)
_FORK3 = re.compile(r"^### +(\d+(\.\d+)* +)?Fork \d", re.I)


def _commentary(run: _Run, warn: list) -> dict | None:
    txt = run.text("author_commentary.md")
    if txt is None:
        return None
    cnt = dict.fromkeys(COMMENTARY_CLASSES, 0)
    head, per_section = None, {}
    for i, line in enumerate(txt.splitlines()):
        if line.startswith("## ") or _FORK3.match(line):
            head = (i, line)
            continue
        m = _RX_CLASS.search(line)
        if m and m.group(1).lower() in cnt:
            if head is not None and _EXTRA.search(head[1]):
                continue
            cnt[m.group(1).lower()] += 1
            per_section[head] = per_section.get(head, 0) + 1
    if any(v != 1 for v in per_section.values()):
        warn.append("author_commentary.md: a section carries more than one classification label")
    cnt["total"] = sum(cnt[k] for k in COMMENTARY_CLASSES)
    return cnt


# ================================================================================================ flags
_ONE_SIDED = re.compile(r"one[- _]?sided|one[- _]?tail|1[- _]?sided|upper[- _]?tail|lower[- _]?tail|right[- _]?tail|"
                        r"left[- _]?tail|one[- _]?side\b", re.I)
_Z30 = _ND.inv_cdf(1 - .30 / 2)


def _flags(run: _Run, cells: _Cells | None, pub: float, rep: float, fixes: dict | None) -> dict:
    """The study's audit-list criteria (surv30h_build.audit_list) for one grid."""
    out: dict[str, Any] = {}
    grid = run.csv("grid_results.csv")
    if grid is not None and pub == pub and pub != 0:
        g = annotate(grid[grid_mask(grid, run.audit)], "negative" if pub < 0 else "positive")
        g = g[~g.has_err & ~g.distinct_estimand]
        est_cols = [c for c in g.columns if re.search(r"estimator|model|^est$", c, re.I)]
        out["U1_mixed_scale"] = bool(any(g[c].astype(str).str.contains(r"logit|probit|poisson|lpm", case=False).any()
                                         for c in est_cols))
    if cells is not None and cells.n:
        g = cells.g
        est, p, s = g.estimate.to_numpy(float), g.p_value.to_numpy(float), np.asarray(cells.f["sign_raw"], bool)
        z = signed_z(p, s)
        ratio = est / pub
        pass30 = z > _Z30
        u2 = float(np.median(ratio[pass30])) if pass30.sum() >= 5 else math.nan
        out["U2_median_ratio_off"] = bool(u2 == u2 and not (.2 <= u2 <= 5))
        opts = []
        for dn in cells.dims:
            col = g[dn].astype(str).to_numpy()
            med = pd.DataFrame({"o": col, "lest": np.log(np.abs(est) + 1e-300), "absz": np.abs(z)}).groupby("o").median()
            ref = (med["lest"] - np.log(abs(pub))).abs().idxmin()
            off = (med["lest"] - med["lest"][ref]).abs()
            ok = ((med["absz"] - med["absz"][ref]).abs() < 1) & (med["absz"] >= _Z30) & (med["absz"][ref] >= _Z30)
            opts += [f"{dn}={o}" for o in med.index[(off > np.log(3)) & ok]]
        out["U3_scale_options"] = opts
        labels = sorted(set(g.inference_method.astype(str))) if "inference_method" in g else []
        out["U5_one_sided_labels"] = [x for x in labels if _ONE_SIDED.search(x)]
        out["U5_joint_rule_column"] = "sig_joint_5pct" in g.columns
    rat = rep / pub if (pub == pub and pub != 0 and rep == rep) else math.nan
    lg = math.log10(rat) if rat > 0 else math.nan
    out["U4_reproduced_power_of_ten"] = bool(rat > 0 and not (.2 <= rat <= 5) and abs(lg - round(lg)) <= .15
                                             and round(lg) != 0)
    out["flagged"] = bool(out.get("U1_mixed_scale") or out.get("U2_median_ratio_off") or out.get("U3_scale_options")
                          or out.get("U4_reproduced_power_of_ten") or out.get("U5_one_sided_labels")
                          or out.get("U5_joint_rule_column"))
    out["affects_share_supported"] = bool(out.get("U5_one_sided_labels") or out.get("U5_joint_rule_column"))
    out["fixes_given"] = fixes is not None
    return out


# ================================================================================================ main entry
def load_measure_inputs(run_dir: Path) -> dict:
    p = Path(run_dir) / MEASURE_INPUTS_FILE
    return _read_json(p) if p.exists() else {}


def _entry(key: str, value: Any, note: str | None = None) -> dict:
    group, name, definition = MEASURES[key]
    e = {"value": _clean(value), "paper_name": name, "definition": definition, "group": group}
    if note:
        e["note"] = note
    return e


def compute_paper_metrics(run_dir: Path, *, inputs: dict | None = None, null_draws: int = NULL_DRAWS,
                          seed: int = SEED, null_seed_index: int = 0) -> dict:
    """The per-paper measures of one run directory (see the module docstring). `inputs` overrides
    <run_dir>/measure_inputs.json key by key. null_draws / seed / null_seed_index set the permutation null (the study
    used 1,000 draws, seed 20261103 and the paper's position in its sample as the index)."""
    run = _Run(Path(run_dir))
    warn: list[str] = []
    inp = {**load_measure_inputs(run.dir), **(inputs or {})}
    fixes = inp.get("fixes")
    used: dict[str, Any] = {"measure_inputs_file": (run.dir / MEASURE_INPUTS_FILE).exists(),
                            "fixes": fixes is not None}
    V: dict[str, Any] = {k: None for k in MEASURES}
    notes: dict[str, str] = {}

    claim = inp.get("claim")
    if claim not in ("directional", "null", "unclear"):
        if claim is not None:
            warn.append(f"inputs: claim {claim!r} is not directional, null or unclear; treated as directional")
        claim = "directional"
        notes["claim_type"] = "not given; assumed directional"
        used["claim"] = "assumed"
    else:
        used["claim"] = "inputs"
    V["claim_type"] = claim

    est = _gating_estimate(run.cfg) if run.cfg else None
    if not run.cfg:
        warn.append("config.yaml missing")
    elif est is None:
        warn.append("config.yaml lists no gating main estimate")
    pub = _num(est.get("value")) if est else math.nan

    # ---- reproduction
    try:
        R = _reproduction(run, est, inp, used, warn)
        for k in ("g0_strict_match", "published_estimate", "reproduced_estimate", "relative_deviation", "same_sign",
                  "reproduced_p", "gap_sd", "published_effect_sd", "close", "reproduced", "reproduced_p020",
                  "reproduced_null_standard", "published_n", "reproduced_n", "n_gap", "published_abs_t", "materials"):
            V[k] = R.get(k)
        if R.get("_reproduced_p_source"):
            notes["reproduced_p"] = "source: " + R["_reproduced_p_source"]
        else:
            notes["reproduced_p"] = "no reproduced_p/t/se input and none readable in reproduction.md"
        notes["close"] = "SD clause " + R["_sd_clause"] + (
            "; undetermined: the 25% clause fails and only outcome_sd (or nonlinear: true) can decide"
            if R.get("close") is None and R.get("relative_deviation") is not None
            and R.get("relative_deviation") == R.get("relative_deviation") else "")
        notes["reproduced"] = (f"p source: {R.get('_reproduced_p_source') or 'none'}; SD clause {R['_sd_clause']}"
                               + ("; for a null claim read reproduced_null_standard" if claim == "null" else "")
                               + ("; an unclear claim has no reproduction standard" if claim == "unclear" else ""))
        if R.get("_g0_note"):
            notes["g0_strict_match"] = R["_g0_note"]
        if R.get("_published_abs_t_source"):
            notes["published_abs_t"] = "from " + R["_published_abs_t_source"]
        rep = _num(R.get("reproduced_estimate"))
    except Exception as e:  # noqa: BLE001
        warn.append(f"reproduction measures: {type(e).__name__}: {e}")
        rep = math.nan

    # ---- audited grid
    cells, g0 = None, None
    if not (pub == pub and pub != 0):
        warn.append("no numeric nonzero published estimate: no grid outcome is defined")
    else:
        try:
            cells, g0 = _audited_cells(run, pub, fixes, warn)
        except Exception as e:  # noqa: BLE001
            warn.append(f"audited grid: {type(e).__name__}: {e}")
    if cells is not None:
        if fixes is None:
            notes["share_supported"] = "designated p-values as the grid writes them (no `fixes` input)"
        V["n_specifications"] = cells.n
        if cells.n:
            z = signed_z(cells.p, cells.sign)
            V["share_supported"] = float(cells.sup.mean())
            V["median_signed_z"] = float(np.median(z))
            V["share_published_sign"] = float(cells.sign.mean())
            V["share_opposite_sign"] = float((~cells.sign).mean())
            V["share_published_sign_p_ge_010"] = float((cells.sign & ~(cells.p < P_CUT)).mean())
            V["share_supported_by_cutoff"] = {f"{c:.2f}": float((cells.sign & (cells.p < c)).mean()) for c in CUTS}
            size_ok = np.where(np.isnan(cells.ratio), True, cells.ratio >= .5)
            p30 = cells.p < .30
            V["surv30h"] = float((cells.sign & p30 & size_ok).mean())
            V["surv30h_parts"] = {"opposite_sign": float((~cells.sign).mean()),
                                  "p_ge_030": float((cells.sign & ~p30).mean()),
                                  "size_only": float((cells.sign & p30 & ~size_ok).mean())}
        else:
            warn.append("the audited grid has no specification with an estimate and a p-value")
        # failed and distinct-estimand specifications (the study's count: one row per combination of crossed options)
        try:
            V["n_distinct_estimand_specifications"] = int(g0.distinct_estimand.sum())
            gg = g0[~g0.distinct_estimand]
            dd = [c["dimension"] for c in (run.audit.get("recommended_grid") or {}).get("crossed_dimensions", [])
                  if c["dimension"] in gg.columns]
            if dd:
                gg = gg[~gg[dd].astype(str).duplicated()]
            V["n_failed_specifications"] = int((gg.has_err | gg.estimate.isna() | gg.p_value.isna()).sum())
        except Exception as e:  # noqa: BLE001
            warn.append(f"failed specifications: {type(e).__name__}: {e}")

    # ---- options and grid facts
    opts = pd.DataFrame(columns=["dimension", "option", "is_paper_choice", "verdict", "dim_status", "in_grid"])
    held = None
    try:
        opts = _options(run)
        GF = _grid_facts(run, opts, cells)
        held = GF.pop("_held_table", None)
        nav = GF.pop("_n_all_valid", None)
        for k, v in GF.items():
            V[k] = v
        if nav is not None:
            notes["share_supported_all_valid"] = f"{nav} all-valid specifications"
    except Exception as e:  # noqa: BLE001
        warn.append(f"grid facts: {type(e).__name__}: {e}")

    # ---- companion grid
    comp, frames, has_comp, spec, n_rest = None, None, False, None, 0
    try:
        spec = run.spec
        n_rest = len((spec or {}).get("restored") or [])
        V["companion_restored_choices"] = n_rest
        if run.exists("companion_results.csv"):
            ca = annotate(run.csv("companion_results.csv"), "positive")   # no gating filter (study rule)
            has_comp = bool(n_rest) and bool((~ca.has_err & ~ca.distinct_estimand).any())
        V["has_companion"] = int(has_comp)
        # rating of the restored options
        if spec and n_rest and len(opts):
            idx = {(r.dimension, r.option): r for r in opts.itertuples()}
            cls = []
            for e in spec.get("restored") or []:
                po = str(e.get("paper_option"))
                if (e["dimension"], po) not in idx:
                    po = po.split(" ", 1)[0]
                o = idx.get((e["dimension"], po))
                if o is None:
                    warn.append(f"restored option {e['dimension']}={po} is not in forks.json")
                    continue
                if o.in_grid:
                    continue        # the audited grid already holds the paper's option: nothing is restored
                cls.append("INVALID" if o.verdict == "INVALID" else "DESIGN")
            if cls:
                V["restored_exclusion_type"] = ("invalid_only" if all(c == "INVALID" for c in cls) else
                                                "invalid_and_design" if "INVALID" in cls else "design_only")
    except Exception as e:  # noqa: BLE001
        warn.append(f"companion grid: {type(e).__name__}: {e}")
    if pub == pub and pub != 0:
        try:
            frames = _companion_frames(run, pub)
            if frames is not None:
                raw_g, cg, keep = frames
                sc = cg[keep]
                if len(sc):
                    f = _fix_arrays(sc, pub, fixes, warn, companion=True)
                    comp = _Cells(sc, [], f)
                    V["companion_n_specifications"] = comp.n
                    V["companion_share_supported"] = float(comp.sup.mean())
                    size_ok = np.where(np.isnan(comp.ratio), True, comp.ratio >= .5)
                    V["companion_surv30h"] = float((comp.sign & (comp.p < .30) & size_ok).mean())
                    if cells is not None:
                        key = set(zip(cells.g.estimate.round(10), cells.g.p_value.round(8)))
                        new = np.array([k not in key for k in zip(comp.g.estimate.round(10), comp.g.p_value.round(8))])
                        V["companion_share_supported_new"] = float(comp.sup[new].mean()) if new.any() else math.nan
                        notes["companion_share_supported_new"] = f"{int(new.sum())} new specifications"
                    try:
                        sub = _subgrid_rows(run, raw_g).loc[sc.index].to_numpy(bool)
                        V["subgrid_n_specifications"] = int(sub.sum())
                        V["subgrid_share_supported"] = float(comp.sup[sub].mean()) if sub.any() else math.nan
                    except Exception as e:  # noqa: BLE001
                        warn.append(f"sub-grid: {type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            warn.append(f"companion grid: {type(e).__name__}: {e}")

    # ---- choices
    if cells is not None and cells.n and cells.dims:
        try:
            T = _choice_ranges(cells)
            top = float(T["range"].max())
            V["choice_ranges"] = {r.dimension: {"range": float(r.range), "family": r.family, "options": r.options}
                                  for r in T.itertuples()}
            V["top_choice_range"] = top
            V["most_influential_choices"] = ([r.dimension for r in T.itertuples() if r.range >= top - EPS]
                                             if top > EPS else [])
            V["choice_decides"] = int(bool(((T.best >= .5 - EPS) & (T.worst <= .1 + EPS)).any()))
            if null_draws and null_draws > 0:
                tn = top_range_null(cells, R=int(null_draws), seed=seed, seed_index=null_seed_index)
                p95 = float(np.percentile(tn, 95))
                V["top_choice_range_null_p95"] = p95
                V["top_choice_range_exceeds_null"] = int(top > p95 + EPS)
                notes["top_choice_range_null_p95"] = f"{int(null_draws)} draws, seed [{seed}, 0, {null_seed_index}, j]"
        except Exception as e:  # noqa: BLE001
            warn.append(f"choice ranges: {type(e).__name__}: {e}")
        try:
            PT = _paths(run, cells, g0, comp, frames, has_comp, held)
            V["own_specification_found"] = int(PT["own_found"])
            if not PT["own_found"]:
                notes["own_specification_found"] = PT["own_reason"]
            else:
                V["own_specification_supports"] = int(PT["own_supports"])
                V["share_supported_by_choices_changed"] = PT["by_k"]
                notes["share_supported_by_choices_changed"] = ("companion_or_audited: " + PT["source"])
                if "held_off" in PT:
                    V["n_held_fixed_off_paper_option"] = PT["held_off"]
        except Exception as e:  # noqa: BLE001
            warn.append(f"own specification: {type(e).__name__}: {e}")
    elif cells is not None and cells.n:
        warn.append("no crossed choice takes two or more options among the specifications")

    # ---- strict survival (survival.py), whole grid file, direction of config.yaml
    try:
        direction = (run.cfg.get("published_conclusion") or {}).get("direction")
        grid = run.csv("grid_results.csv")
        if direction and grid is not None:
            g = annotate(grid[grid_mask(grid, run.audit)], direction)
            base = g[~g.has_err & ~g.distinct_estimand]
            if len(base):
                V["strict_survival"] = float(base.survives.mean())
                V["strict_sign_consistency"] = float(base.sign_ok.mean())
                notes["strict_survival"] = g.attrs.get("sig_rule", "")
            craw = run.csv("companion_results.csv")
            if craw is not None:
                cg = annotate(craw, direction)
                cb = cg[~cg.has_err & ~cg.distinct_estimand]
                if len(cb):
                    V["companion_strict_survival"] = float(cb.survives.mean())
    except Exception as e:  # noqa: BLE001
        warn.append(f"strict survival: {type(e).__name__}: {e}")

    # ---- pipeline audits, commentary, flags
    try:
        V["pipeline_audits"] = _pipeline(run)
    except Exception as e:  # noqa: BLE001
        warn.append(f"pipeline audits: {type(e).__name__}: {e}")
    try:
        V["commentary_classes"] = _commentary(run, warn)
    except Exception as e:  # noqa: BLE001
        warn.append(f"commentary: {type(e).__name__}: {e}")
    try:
        V["measure_review_flags"] = _flags(run, cells, pub, rep, fixes)
        fl = V["measure_review_flags"]
        if fl.get("affects_share_supported") and fixes is None:
            warn.append("grid flagged (one-sided labels or a joint decision column): check whether `fixes` are needed")
    except Exception as e:  # noqa: BLE001
        warn.append(f"flags: {type(e).__name__}: {e}")

    used["outcome_sd"] = used.get("outcome_sd")
    V["inputs_used"] = used
    V["warnings"] = warn
    return {k: _entry(k, V[k], notes.get(k)) for k in MEASURES}


# ================================================================================================ output helpers
def flat_values(metrics: dict) -> dict:
    """{measure: value} without the definitions."""
    return {k: v.get("value") for k, v in metrics.items()}


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.3f}"
    if isinstance(v, (dict, list)):
        s = json.dumps(v)
        return s if len(s) <= 160 else s[:157] + "..."
    return str(v)


def metrics_markdown(metrics: dict) -> str:
    """A readable table of the measures, grouped as in the paper."""
    lines = ["# Paper measures", ""]
    for grp, title in GROUP_TITLES.items():
        keys = [k for k, e in metrics.items() if e.get("group") == grp]
        if not keys:
            continue
        lines += [f"## {title}", "", "| measure | value | definition |", "|---|---|---|"]
        for k in keys:
            e = metrics[k]
            d = e["definition"] + (f" ({e['note']})" if e.get("note") else "")
            lines.append(f"| {e['paper_name']} | {_fmt(e['value']).replace('|', '/')} | {d.replace('|', '/')} |")
        lines.append("")
    return "\n".join(lines)


def write_paper_metrics(run_dir: Path, out: Path | None = None, **kw) -> Path:
    """Compute the measures and write them as JSON (default <run_dir>/paper_metrics.json)."""
    m = compute_paper_metrics(Path(run_dir), **kw)
    out = Path(out) if out else Path(run_dir) / "paper_metrics.json"
    out.write_text(json.dumps(m, indent=1), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Per-paper measures of one AMA run directory.")
    ap.add_argument("run_dir")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a markdown table")
    ap.add_argument("--inputs", default=None, help="JSON file of measure inputs (overrides measure_inputs.json)")
    ap.add_argument("--out", default=None, help="also write the JSON to this file")
    ap.add_argument("--null-draws", type=int, default=NULL_DRAWS)
    a = ap.parse_args(argv)
    inputs = _read_json(Path(a.inputs)) if a.inputs else None
    m = compute_paper_metrics(Path(a.run_dir), inputs=inputs, null_draws=a.null_draws)
    if a.out:
        Path(a.out).write_text(json.dumps(m, indent=1), encoding="utf-8")
    print(json.dumps(m, indent=1) if a.json else metrics_markdown(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
