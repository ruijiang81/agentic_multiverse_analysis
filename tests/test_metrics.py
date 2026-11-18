"""Tests of ama_mcp.metrics on small invented run directories (no real run data)."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest
import yaml

from ama_mcp import metrics as M

CORE_SCRIPTS = Path(M.__file__).resolve().parent / "core" / "scripts"
ND = NormalDist()

# ------------------------------------------------------------------------------------------------ invented run
# Crossed choices: est (a, b) and win (w1, w2, w3, w4); held fixed: ctrl at c1 (the paper's own option is c0).
# Published estimate 0.5 (positive). The six scored specifications:
#   a w1  0.6  p .01   supports
#   a w2  0.4  p .08   supports
#   a w3  0.2  p .30   published sign, p >= .10
#   b w1 -0.1  p .50   opposite sign
#   b w2  0.3  p .05   supports
#   b w3  0.1  p .20   published sign, p >= .10
GOOD = [("a", "w1", 0.6, 0.01), ("a", "w2", 0.4, 0.08), ("a", "w3", 0.2, 0.30),
        ("b", "w1", -0.1, 0.50), ("b", "w2", 0.3, 0.05), ("b", "w3", 0.1, 0.20)]


def _grid_rows():
    rows = [dict(cell_id=f"{e}|{w}", est=e, win=w, ctrl="c1", estimate=x, std_error=0.1, p_value=p,
                 inference_method="wild_cluster_bootstrap", err="", distinct_estimand=False, row_type="grid")
            for e, w, x, p in GOOD]
    rows += [
        dict(cell_id="a|w4", est="a", win="w4", ctrl="c1", estimate=None, std_error=None, p_value=None,
             inference_method="wild_cluster_bootstrap", err="did not converge", distinct_estimand=False, row_type="grid"),
        dict(cell_id="b|w4", est="b", win="w4", ctrl="c1", estimate=0.9, std_error=0.1, p_value=0.001,
             inference_method="wild_cluster_bootstrap", err="", distinct_estimand=True, row_type="grid"),
        # rows outside the audited grid: an option the audit does not list, another held-fixed option, a diagnostic
        dict(cell_id="z|w1", est="z", win="w1", ctrl="c1", estimate=-5.0, std_error=0.1, p_value=0.001,
             inference_method="analytic", err="", distinct_estimand=False, row_type="grid"),
        dict(cell_id="a|w1|c2", est="a", win="w1", ctrl="c2", estimate=-5.0, std_error=0.1, p_value=0.001,
             inference_method="analytic", err="", distinct_estimand=False, row_type="grid"),
        dict(cell_id="anchor", est="a", win="w1", ctrl="c1", estimate=-5.0, std_error=0.1, p_value=0.001,
             inference_method="analytic", err="", distinct_estimand=False, row_type="reproduction_anchor"),
    ]
    return rows


CONFIG = {
    "run_id": "invented",
    "published_conclusion": {"statement": "x raises y", "direction": "positive", "significance": "p < 0.05"},
    "headline_estimates": [{"id": "h1", "location": "Table 2, col 1", "quantity": "coefficient on treat",
                            "value": 0.5, "std_error": 0.2, "n_obs": 1000, "gating": True}],
}
FORKS = {"dimensions": [
    {"id": "est", "paper_choice": "a", "options": [{"id": "a"}, {"id": "b"}, {"id": "z"}]},
    {"id": "win", "paper_choice": "w1", "options": [{"id": "w1"}, {"id": "w2"}, {"id": "w3"}, {"id": "w4"}]},
    {"id": "ctrl", "paper_choice": "c0", "options": [{"id": "c0"}, {"id": "c1"}, {"id": "c2"}]},
]}
VERDICTS = {("est", "a"): "VALID", ("est", "b"): "QUESTIONABLE", ("est", "z"): "INVALID",
            ("win", "w1"): "VALID", ("win", "w2"): "VALID", ("win", "w3"): "VALID", ("win", "w4"): "VALID",
            ("ctrl", "c0"): "INVALID", ("ctrl", "c1"): "VALID", ("ctrl", "c2"): "QUESTIONABLE"}
AUDIT = {
    "verdicts": [dict(dimension=d, option_id=o, verdict=v, distinct_estimand=False) for (d, o), v in VERDICTS.items()],
    "recommended_grid": {"crossed_dimensions": [{"dimension": "est", "option_ids": ["a", "b"]},
                                                {"dimension": "win", "option_ids": ["w1", "w2", "w3", "w4"]}],
                         "held_fixed": [{"dimension": "ctrl", "option_id": "c1", "why": "post-treatment controls"}],
                         "n_cells": 8},
    "inference_designation": {"method": "Wild cluster restricted bootstrap-t clustered by state, Webb weights"},
}
REPRO = """# Reproduction

| target | published | reproduced | deviation | note |
|---|---|---|---|---|
| h1 | 0.5 (SE 0.2, N=1,000) | 0.45 (SE 0.15, N=1,000) | -10% | same sign |
"""
GATES = "\n".join([
    "2026-01-01T00:00:00\tGS\tFIXABLE\tsample_reconciliation.md: FIXABLE n=2",
    "2026-01-01T00:00:01\tGS\tPASS\tbest achievable",
    "2026-01-01T00:00:02\tGC\tFAIL\twrong pick",
    "2026-01-01T00:00:03\tGC\tPASS\th1 ok",
    "2026-01-01T00:00:04\tG0\tFAIL\th1: deviation 12% > 10%",
    "2026-01-01T00:00:05\tG0A\tERRORS\tg0_audit.md: ERRORS n=1",
    "  artifact hashes continue here",
    "2026-01-01T00:00:06\tG0\tPASS\t1 gating targets within 10% or printed precision",
    "2026-01-01T00:00:07\tG2\tPASS\t10 options judged",
    "2026-01-01T00:00:08\tG4\tPASS\t2 audit round(s), final tally blockers=0 major=1 minor=0",
    "2026-01-01T00:00:09\tG6\tFAIL\treport differs",
    "2026-01-01T00:00:10\tG6\tPASS\tgenerator reproduces report.md exactly",
]) + "\n"
COMMENTARY = """# Author commentary

## Summary
Classification counts below.

## 1. win
What the authors say: nothing.
Classification: `silent`

## 2. est
**Classification:** anticipated-and-defended

## Appendix: other claims
Classification: silent
"""


def make_run(root: Path, *, grid_rows=None, config=None, companion=None, spec=None, inputs=None) -> Path:
    run = root / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "config.yaml").write_text(yaml.safe_dump(config or CONFIG, sort_keys=False))
    pd.DataFrame(grid_rows if grid_rows is not None else _grid_rows()).to_csv(run / "grid_results.csv", index=False)
    (run / "audit.json").write_text(json.dumps(AUDIT))
    (run / "forks.json").write_text(json.dumps(FORKS))
    (run / "reproduction.md").write_text(REPRO)
    (run / "gates.log").write_text(GATES)
    (run / "implementation_audit.md").write_text("...\nTALLY: blockers=1 major=2 minor=3\n")
    (run / "implementation_audit_r2.md").write_text("...\nTALLY: blockers=0 major=1 minor=0\n")
    (run / "package_status.json").write_text(json.dumps({"finder": {"status": "none_found"}}))
    (run / "author_commentary.md").write_text(COMMENTARY)
    if companion is not None:
        pd.DataFrame(companion).to_csv(run / "companion_results.csv", index=False)
    if spec is not None:
        (run / "companion_spec.json").write_text(json.dumps(spec))
    if inputs is not None:
        (run / M.MEASURE_INPUTS_FILE).write_text(json.dumps(inputs))
    return run


def val(m, key):
    return m[key]["value"]


def z_of(p, sign):
    z = ND.inv_cdf(1 - max(p, 1e-4) / 2)
    return z if sign else -z


@pytest.fixture()
def run(tmp_path):
    return make_run(tmp_path)


# ------------------------------------------------------------------------------------------------ the outcomes
def test_outcomes_on_invented_grid(run):
    m = M.compute_paper_metrics(run, null_draws=50)
    assert val(m, "n_specifications") == 6
    assert val(m, "share_supported") == pytest.approx(3 / 6)
    assert val(m, "share_published_sign") == pytest.approx(5 / 6)
    assert val(m, "share_opposite_sign") == pytest.approx(1 / 6)
    assert val(m, "share_published_sign_p_ge_010") == pytest.approx(2 / 6)
    zs = [z_of(p, x > 0) for _, _, x, p in GOOD]
    assert val(m, "median_signed_z") == pytest.approx(float(np.median(zs)), abs=1e-12)
    cut = val(m, "share_supported_by_cutoff")
    assert cut == pytest.approx({"0.01": 0, "0.05": 1 / 6, "0.10": 3 / 6, "0.20": 3 / 6, "0.30": 4 / 6, "0.50": 5 / 6})
    assert val(m, "n_failed_specifications") == 1          # the error row; the distinct-estimand row is not counted
    assert val(m, "n_distinct_estimand_specifications") == 1
    assert val(m, "claim_type") == "directional"
    assert "assumed" in m["claim_type"]["note"]


def test_every_measure_has_value_and_definition_and_is_json(run):
    m = M.compute_paper_metrics(run, null_draws=20)
    assert list(m) == list(M.MEASURES)
    for k, e in m.items():
        assert set(e) >= {"value", "paper_name", "definition", "group"}, k
        assert e["definition"] and e["paper_name"], k
    json.dumps(m)                                          # serializable as is
    assert M.metrics_markdown(m).startswith("# Paper measures")


def test_surv30h_and_strict_survival(run):
    m = M.compute_paper_metrics(run, null_draws=0)
    # surv30h: sign, p < .30 and estimate / published >= .5 -> a w1, a w2, b w2
    assert val(m, "surv30h") == pytest.approx(3 / 6)
    assert val(m, "surv30h_parts") == pytest.approx({"opposite_sign": 1 / 6, "p_ge_030": 1 / 6, "size_only": 1 / 6})
    # survival.py: direction of config.yaml, p < .05 (strict), errors and distinct estimands out, NaN p kept
    assert val(m, "strict_survival") == pytest.approx(1 / 6)
    assert val(m, "strict_sign_consistency") == pytest.approx(5 / 6)


# ------------------------------------------------------------------------------------------------ filters
def test_gating_rows_only(tmp_path):
    rows = [dict(r, headline_id="h1") for r in _grid_rows()]
    rows += [dict(r, headline_id="h2", estimate=-1.0, p_value=0.001) for r in _grid_rows()[:6]]
    m = M.compute_paper_metrics(make_run(tmp_path, grid_rows=rows), null_draws=0)
    assert val(m, "n_specifications") == 6
    assert val(m, "share_supported") == pytest.approx(0.5)
    assert any("gating" in w for w in val(m, "warnings"))


def test_negative_published_estimate_flips_direction(tmp_path):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["headline_estimates"][0]["value"] = -0.5
    cfg["published_conclusion"]["direction"] = "negative"
    rows = [dict(r, estimate=(-r["estimate"] if isinstance(r["estimate"], float) else r["estimate"]))
            for r in _grid_rows()]
    m = M.compute_paper_metrics(make_run(tmp_path, grid_rows=rows, config=cfg), null_draws=0)
    assert val(m, "share_supported") == pytest.approx(0.5)
    assert val(m, "share_published_sign") == pytest.approx(5 / 6)


def test_no_numeric_published_estimate(tmp_path):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["headline_estimates"][0]["value"] = "not reported"
    m = M.compute_paper_metrics(make_run(tmp_path, config=cfg), null_draws=0)
    assert val(m, "share_supported") is None and val(m, "median_signed_z") is None
    assert val(m, "reproduced") is None
    assert any("published estimate" in w for w in val(m, "warnings"))
    assert val(m, "n_options") == 10                         # facts that need no estimate are still reported


# ------------------------------------------------------------------------------------------------ fixes
def test_one_sided_p_made_two_sided(run):
    m = M.compute_paper_metrics(run, inputs={"fixes": {"p_sidedness": "one_published_direction"}}, null_draws=0)
    # doubled: a w1 .02 (supports), a w2 .16, b w2 .10 (not < .10) -> 1 of 6
    assert val(m, "share_supported") == pytest.approx(1 / 6)
    assert val(m, "inputs_used")["fixes"] is True


def test_joint_p_column_takes_the_larger_p(tmp_path):
    rows = _grid_rows()
    for r in rows:
        r["p_ri"] = 0.5 if (r["est"], r["win"]) == ("a", "w2") else 0.0
    m0 = M.compute_paper_metrics(make_run(tmp_path, grid_rows=rows), null_draws=0)
    assert val(m0, "share_supported") == pytest.approx(3 / 6)
    m1 = M.compute_paper_metrics(make_run(tmp_path, grid_rows=rows), inputs={"fixes": {"joint_p_column": "p_ri"}},
                                 null_draws=0)
    assert val(m1, "share_supported") == pytest.approx(2 / 6)


def test_benchmark_sign(tmp_path):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["headline_estimates"][0]["value"] = 0.7                  # a share tested against one half
    rows = _grid_rows()
    for r, x in zip(rows, [0.8, 0.6, 0.4, 0.55, 0.45, 0.9]):
        r["estimate"], r["p_value"] = x, 0.01
    m = M.compute_paper_metrics(make_run(tmp_path, grid_rows=rows, config=cfg),
                                inputs={"fixes": {"benchmark": 0.5}}, null_draws=0)
    assert val(m, "share_supported") == pytest.approx(4 / 6)     # 0.8, 0.6, 0.55, 0.9 lie above one half


def test_unit_rule_only_moves_surv30h(run):
    fx = {"units": [{"dimension": "est", "option": "b", "verdict": "not_comparable"}]}
    m = M.compute_paper_metrics(run, inputs={"fixes": fx}, null_draws=0)
    assert val(m, "share_supported") == pytest.approx(3 / 6)
    assert val(m, "surv30h") == pytest.approx(4 / 6)             # b w3 (size only) passes once its size rule is off
    m2 = M.compute_paper_metrics(run, inputs={"fixes": {"units": [{"dimension": None, "option": None,
                                                                    "verdict": "factor", "factor": 0.1}]}},
                                 null_draws=0)
    assert val(m2, "surv30h") == pytest.approx(4 / 6)            # every ratio x10: only sign and p remain binding


# ------------------------------------------------------------------------------------------------ reproduction
def test_reproduction_with_inputs(run):
    m = M.compute_paper_metrics(run, inputs={"claim": "directional", "reproduced_p": 0.04}, null_draws=0)
    assert val(m, "published_estimate") == 0.5 and val(m, "reproduced_estimate") == 0.45
    assert val(m, "relative_deviation") == pytest.approx(0.1)
    assert val(m, "same_sign") is True and val(m, "close") is True
    assert val(m, "reproduced") == 1 and val(m, "reproduced_p020") == 1
    assert val(m, "reproduced_null_standard") == 0
    assert val(m, "g0_strict_match") == 1                       # the last G0 decision is PASS
    assert val(m, "published_n") == 1000 and val(m, "reproduced_n") == 1000 and val(m, "n_gap") == 0
    assert val(m, "published_abs_t") == pytest.approx(2.5)
    assert val(m, "materials") == {"class": "rebuilt", "finder_status": "none_found"}
    m = M.compute_paper_metrics(run, inputs={"reproduced_p": 0.15}, null_draws=0)
    assert val(m, "reproduced") == 0 and val(m, "reproduced_p020") == 1 and val(m, "reproduced_null_standard") == 1
    m = M.compute_paper_metrics(run, inputs={"reproduced_p": 0.06, "reproduced_p_sided": "one"}, null_draws=0)
    assert val(m, "reproduced_p") == pytest.approx(0.12) and val(m, "reproduced") == 0


def test_reproduced_p_read_from_reproduction_md(run):
    m = M.compute_paper_metrics(run, null_draws=0)
    assert val(m, "reproduced_p") == pytest.approx(math.erfc(3 / math.sqrt(2)))   # from SE 0.15: t = 3
    assert "heuristic" in m["reproduced_p"]["note"]
    assert val(m, "reproduced") == 1


def test_heuristic_skips_published_values_and_sample_sizes():
    row = ("| h1 | 0.82 | 0.557 | -32% | Published: se 0.29, t=2.83, p<0.01, N=5,544. Reproduced: se 0.594, "
           "t=0.94, p=0.35, N=5,761 |")
    assert M._parse_reproduced_inference(row, 0.557) == (pytest.approx(0.35), "p")
    row = "| h1 | 0.002 | -0.0013 | -156% | Newey-West (T=456 months): SE=0.0067, t=-0.2007 |"
    p, how = M._parse_reproduced_inference(row, -0.0013)
    assert how == "t" and p == pytest.approx(math.erfc(0.2007 / math.sqrt(2)))
    row = "| h1 | -0.007 | -0.0056 | 20% | Wald chi2 = 0.98 (p 0.3216) vs published 1.409 |"
    assert M._parse_reproduced_inference(row, -0.0056) == (pytest.approx(0.3216), "p")


def test_sd_clause(tmp_path):
    md = REPRO.replace("0.45 (SE 0.15, N=1,000)", "0.3 (N=1,000)")      # 40% below the published 0.5
    run = make_run(tmp_path)
    (run / "reproduction.md").write_text(md)
    base = {"reproduced_p": 0.01}
    m = M.compute_paper_metrics(run, inputs=base, null_draws=0)
    assert val(m, "close") is None and val(m, "reproduced") is None       # only the SD clause could decide
    m = M.compute_paper_metrics(run, inputs={**base, "outcome_sd": 3.0}, null_draws=0)
    assert val(m, "gap_sd") == pytest.approx(0.2 / 3) and val(m, "reproduced") == 1
    m = M.compute_paper_metrics(run, inputs={**base, "outcome_sd": 1.0}, null_draws=0)
    assert val(m, "reproduced") == 0
    m = M.compute_paper_metrics(run, inputs={**base, "outcome_sd": 3.0, "nonlinear": True}, null_draws=0)
    assert val(m, "gap_sd") is None and val(m, "reproduced") == 0
    m = M.compute_paper_metrics(run, inputs={"reproduced_p": 0.5}, null_draws=0)
    assert val(m, "reproduced") == 0                                       # fails p whatever the closeness


def test_g0_without_decision(tmp_path):
    run = make_run(tmp_path)
    (run / "gates.log").write_text("2026-01-01T00:00:00\tGS\tPASS\tok\n")
    assert val(M.compute_paper_metrics(run, null_draws=0), "g0_strict_match") is None      # run in progress
    (run / "gates.log").write_text("2026-01-01T00:00:00\tGS\tPASS\tok\n2026-01-01T00:00:01\tG6\tPASS\tok\n")
    assert val(M.compute_paper_metrics(run, null_draws=0), "g0_strict_match") == 0         # finished, never passed


# ------------------------------------------------------------------------------------------------ choices
def test_choice_ranges_and_null(run):
    m = M.compute_paper_metrics(run, null_draws=200, null_seed_index=3)
    cr = val(m, "choice_ranges")
    assert cr["est"]["range"] == pytest.approx(2 / 3 - 1 / 3)
    assert cr["win"]["range"] == pytest.approx(1.0)
    assert cr["win"]["options"]["w2"] == {"share_supported": 1.0, "n": 2}
    assert val(m, "top_choice_range") == pytest.approx(1.0)
    assert val(m, "most_influential_choices") == ["win"]
    assert val(m, "choice_decides") == 1
    p95 = val(m, "top_choice_range_null_p95")
    assert 0 <= p95 <= 1
    again = M.compute_paper_metrics(run, null_draws=200, null_seed_index=3)
    assert val(again, "top_choice_range_null_p95") == p95                 # the draws are seeded
    assert val(m, "top_choice_range_exceeds_null") == int(1.0 > p95 + 1e-12)


def test_own_specification_and_choices_changed_without_companion(run):
    m = M.compute_paper_metrics(run, null_draws=0)
    assert val(m, "own_specification_found") == 1
    assert val(m, "own_specification_supports") == 1                      # a w1
    byk = val(m, "share_supported_by_choices_changed")["companion_or_audited"]
    assert byk["k0"] == {"share_supported": 1.0, "n": 1}
    assert byk["k1"] == {"share_supported": pytest.approx(1 / 3), "n": 3}  # a w2, a w3, b w1
    assert byk["k2"] == {"share_supported": pytest.approx(1 / 2), "n": 2}  # b w2, b w3
    assert byk["k3plus"] == {"share_supported": None, "n": 0}
    assert val(m, "n_held_fixed_off_paper_option") == 1                   # ctrl held at c1, paper's option c0


def test_grid_facts(run):
    m = M.compute_paper_metrics(run, null_draws=0)
    assert val(m, "n_choices") == 3 and val(m, "n_options") == 10
    assert (val(m, "n_options_valid"), val(m, "n_options_questionable"), val(m, "n_options_invalid")) == (6, 2, 2)
    assert val(m, "n_crossed_choices") == 2 and val(m, "n_grid_specifications") == 8
    assert val(m, "n_held_fixed_choices") == 1
    assert val(m, "held_fixed_at_paper_option_share") == 0.0
    assert val(m, "holds_invalid_option_fixed") == 0
    assert val(m, "crossed_options_questionable_share") == pytest.approx(1 / 6)
    assert val(m, "specifications_with_questionable_share") == pytest.approx(0.5)
    assert val(m, "share_supported_all_valid") == pytest.approx(2 / 3)     # est a only: a w1, a w2 of 3
    assert val(m, "designated_inference")["family"] == "wild_bootstrap"


def test_companion_and_subgrid(tmp_path):
    comp = [dict(cell_id=f"{e}|{w}|c0", est=e, win=w, ctrl="c0", estimate=x + 0.2, std_error=0.1,
                 p_value=min(p, 0.09), err="", distinct_estimand=False, restored_dims="ctrl")
            for e, w, x, p in GOOD]
    spec = {"restored": [{"dimension": "ctrl", "kind": "held_fixed", "paper_option": "c0", "audit_verdict": "INVALID"}]}
    m = M.compute_paper_metrics(make_run(tmp_path, companion=comp, spec=spec), null_draws=0)
    assert val(m, "has_companion") == 1 and val(m, "companion_restored_choices") == 1
    assert val(m, "companion_n_specifications") == 6
    # +0.2 moves b w1 to 0.1 (published sign); every p < .10 -> all six support
    assert val(m, "companion_share_supported") == pytest.approx(1.0)
    assert val(m, "companion_share_supported_new") == pytest.approx(1.0)
    assert val(m, "subgrid_n_specifications") == 6 and val(m, "subgrid_share_supported") == pytest.approx(1.0)
    assert val(m, "restored_exclusion_type") == "invalid_only"
    assert val(m, "companion_strict_survival") == pytest.approx(1 / 6)      # only p .01 is below .05
    # own specification now from the companion grid (ctrl restored to the paper's c0)
    assert val(m, "own_specification_found") == 1
    assert val(m, "n_held_fixed_off_paper_option") == 0
    assert "companion" in m["share_supported_by_choices_changed"]["note"]


# ------------------------------------------------------------------------------------------------ pipeline, commentary
def test_pipeline_audits_and_commentary(run):
    m = M.compute_paper_metrics(run, null_draws=0)
    pa = val(m, "pipeline_audits")
    assert pa["implementation_audit_rounds"] == 2
    assert (pa["first_round_blockers"], pa["first_round_major"]) == (1, 2)
    assert (pa["last_round_blockers"], pa["last_round_major"]) == (0, 1)
    assert pa["reproduction_audit_rounds"] == 1 and pa["reproduction_audit_verdicts"] == ["ERRORS"]
    assert pa["sample_review_first_fixable_items"] == 2 and pa["sample_review_last_fixable_items"] == 0
    assert (pa["config_review_failures"], pa["grid_gate_failures"], pa["report_gate_failures"]) == (1, 0, 1)
    cc = val(m, "commentary_classes")
    assert cc["silent"] == 1 and cc["anticipated-and-defended"] == 1 and cc["total"] == 2   # appendix label ignored


def test_measure_inputs_file_and_override(tmp_path):
    run = make_run(tmp_path, inputs={"claim": "null", "reproduced_p": 0.5})
    m = M.compute_paper_metrics(run, null_draws=0)
    assert val(m, "claim_type") == "null" and val(m, "reproduced_null_standard") == 1
    assert val(m, "inputs_used")["measure_inputs_file"] is True
    m = M.compute_paper_metrics(run, inputs={"reproduced_p": 0.01}, null_draws=0)
    assert val(m, "claim_type") == "null" and val(m, "reproduced_null_standard") == 0


def test_flags(tmp_path):
    rows = _grid_rows()
    for r in rows:
        r["inference_method"] = "one-sided wild bootstrap"
        r["sig_joint_5pct"] = True
    m = M.compute_paper_metrics(make_run(tmp_path, grid_rows=rows), null_draws=0)
    fl = val(m, "measure_review_flags")
    assert fl["U5_one_sided_labels"] and fl["U5_joint_rule_column"] and fl["affects_share_supported"]
    assert any("fixes" in w for w in val(m, "warnings"))


def test_cli_json(run, capsys):
    assert M.main([str(run), "--json", "--null-draws", "0"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["share_supported"]["value"] == pytest.approx(0.5)


# ------------------------------------------------------------------------------------------------ copies of core code
def _load_core(name):
    path = CORE_SCRIPTS / f"{name}.py"
    if not path.exists():
        pytest.skip(f"core/scripts/{name}.py not present")
    sys.path.insert(0, str(CORE_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"_core_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(CORE_SCRIPTS))
    return mod


def test_grid_mask_and_annotate_match_core_survival():
    sv = _load_core("survival")
    g = pd.DataFrame(_grid_rows())
    assert sv.grid_mask(g, AUDIT).tolist() == M.grid_mask(g, AUDIT).tolist()
    a, b = sv.annotate(g, "positive"), M.annotate(g, "positive")
    for col in ("has_err", "distinct_estimand", "sign_ok", "sig", "survives"):
        assert a[col].tolist() == b[col].tolist(), col


def test_repro_parser_matches_core_gate_g0():
    g0 = _load_core("gate_g0")
    t1, t2 = g0.parse_repro_table(REPRO), M.parse_repro_table(REPRO)
    assert t1 == t2
    est = CONFIG["headline_estimates"][0]
    assert g0.match_target(est, t1) == M.match_target(est, t2)


def test_no_absolute_paths_in_module():
    import re
    src = Path(M.__file__).read_text(encoding="utf-8")
    hits = re.findall(r"""["'](?:~?/[A-Za-z0-9_.-]+){2,}""", src)      # absolute path literals of 2+ parts
    assert not hits, hits
