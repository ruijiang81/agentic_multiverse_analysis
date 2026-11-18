"""Tests of the core pipeline files (src/ama_mcp/core): prompt rendering and every gate.

Everything runs on a copy of tests/fixtures/core_run, a fully synthetic run (invented paper,
invented numbers). Scripts are invoked exactly as the orchestrator invokes them:
``python <core>/scripts/<name>.py <run_dir> [args]``.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "src" / "ama_mcp" / "core"
SCRIPTS = CORE / "scripts"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "core_run"
STEPS = ["P", "B", "SR", "BF", "C", "CR", "0", "G0A", "1", "2", "3", "3F", "4", "3C", "4C", "3CF", "5", "6"]
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


# --------------------------------------------------------------------------- helpers

@pytest.fixture
def run(tmp_path: Path) -> Path:
    dst = tmp_path / "runs" / "core_run"
    shutil.copytree(FIXTURE, dst)
    for d in ("figures", "repro", "benchmark", "prompts_rendered"):
        (dst / d).mkdir(exist_ok=True)
    old = time.time() - 3600          # G4 refuses a grid file modified in the last 2 minutes
    for f in ("grid_results.csv", "companion_results.csv"):
        os.utime(dst / f, (old, old))
    return dst


def script(name: str, run_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / f"{name}.py"), str(run_dir), *args],
                          capture_output=True, text=True, env=ENV, timeout=600)


def render(run_dir: Path, step: str, *args: str) -> str:
    p = script("render_prompt", run_dir, step, *args)
    assert p.returncode == 0, p.stderr
    return p.stdout


def policy(run_dir: Path, **changes) -> None:
    f = run_dir / "run_policy.json"
    d = json.loads(f.read_text())
    d.update(changes)
    f.write_text(json.dumps(d, indent=1))


def gate_lines(run_dir: Path) -> list[list[str]]:
    return [l.split("\t") for l in (run_dir / "gates.log").read_text().splitlines()
            if l and not l.startswith("\t")]


def load_script_module(name: str):
    """Import a core script as a module without writing bytecode into the package."""
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.dont_write_bytecode = old


def frozen(run_dir: Path) -> dict:
    f = run_dir / ".frozen.json"
    return json.loads(f.read_text()) if f.exists() else {}


# --------------------------------------------------------------------------- render_prompt

@pytest.mark.parametrize("step", STEPS)
def test_render_every_step(run: Path, step: str) -> None:
    text = render(run, step)
    saved = run / "prompts_rendered" / f"step{step}.md"
    assert saved.read_text() + "\n" == text          # print() adds the final newline
    assert "{{" not in text and "}}" not in text
    assert "RUN ISOLATION" in text
    assert f"Do not read any other run directory under {run.resolve().parent}/." in text
    assert "Never publish, upload or share run" in text
    for gone in ("Artifact", "claude.ai", "Slurm", "slurm", "COMPUTE ON THIS SERVER", "shard"):
        assert gone not in text, gone
    assert "Invented, Critic (2021)" in text           # forbidden_sources reach every role


def test_render_core_relative_paths(run: Path) -> None:
    text = render(run, "2")
    assert f"`{CORE}/rubric.md`" in text
    assert (CORE / "rubric.md").is_file()
    text = render(run, "B")
    assert f"`{CORE}/env.md`" in text
    assert (CORE / "env.md").is_file()


def test_render_round_and_findings_suffixes(run: Path) -> None:
    render(run, "4", "--round", "2")
    assert "implementation_audit_r2.md" in (run / "prompts_rendered" / "step4_r2.md").read_text()
    render(run, "BF", "--findings", "sample_reconciliation.md")
    text = (run / "prompts_rendered" / "stepBF_sample_reconciliation.md").read_text()
    assert "sample_reconciliation.md" in text
    render(run, "BF", "--round", "2")
    assert "g0_audit_r2.md" in (run / "prompts_rendered" / "stepBF_r2.md").read_text()
    text = render(run, "C", "--paper-hint", "An invented hint")
    assert "PAPER (if known): An invented hint" in text


def test_render_needs_config_after_C(run: Path) -> None:
    (run / "config.yaml").unlink()
    for step in ("P", "B", "SR", "BF", "C"):
        assert script("render_prompt", run, step).returncode == 0
    p = script("render_prompt", run, "0")
    assert p.returncode != 0 and "run step C first" in p.stderr


def test_render_data_sources(run: Path, tmp_path: Path) -> None:
    for step in ("B", "BF", "SR", "G0A"):
        assert "none declared for this run" in render(run, step)
    ds = tmp_path / "my_data_sources.md"
    ds.write_text("# invented\n")
    policy(run, data_sources_file=str(ds))
    for step in ("B", "BF", "SR", "G0A"):
        text = render(run, step)
        assert f"read `{ds}` first" in text
        assert "none declared" not in text
        assert "data_sources.md" not in text.replace(ds.name, "")


def test_render_analysis_python_and_language(run: Path) -> None:
    assert "Analysis interpreter" not in render(run, "3")
    policy(run, analysis_python="/opt/invented/bin/python", repro_language="R")
    for step in STEPS:
        assert "Analysis interpreter for this run: /opt/invented/bin/python." in render(run, step)
    assert "headline estimation yourself in R" in render(run, "0")
    assert "yourself in Stata" in render(run, "0", "--repro-language", "Stata")


def test_render_with_and_without_anchor(run: Path) -> None:
    c = render(run, "C")
    assert "invented outcome (level)  --  Tab 2, Col 1" in c and "ia_location" in c
    cr = render(run, "CR")
    assert f"`{run.resolve()}/headline_anchor.json`" in cr and "anchor locations" in cr
    (run / "headline_anchor.json").unlink()
    c = render(run, "C")
    assert "none recorded for this run" in c and "exactly ONE gating coefficient target" in c
    assert "ia_location" not in c
    cr = render(run, "CR")
    assert "no `headline_anchor.json` was recorded" in cr and "ia_location" not in cr


def test_render_data_role_sources(run: Path) -> None:
    for step in ("P", "B", "BF", "SR", "C", "CR", "0", "G0A"):
        text = render(run, step)
        assert "You MAY read Invented, Coder (2019)" in text
    for step in ("1", "2", "3", "3F", "4", "3C", "4C", "3CF", "5", "6"):
        text = render(run, step)
        assert "You MAY read Invented, Coder" not in text
        assert "Invented, Coder (2019), 'Policy adoption dates, recoded' (/data/invented_dates.pdf)" in text


def test_render_restricted_package_and_local_materials(run: Path) -> None:
    policy(run, restricted_package={"path": "/data/authors_files", "terms": "replication checking only"},
           local_materials={"supplements": [{"path": "/data/ia.pdf", "what": "Internet Appendix",
                                             "source": "https://example.org/ia.pdf"}]})
    assert "You MAY read the authors' replication files at /data/authors_files" in render(run, "0")
    p = render(run, "P")
    assert "record `none_found`" in p and "/data/authors_files (the authors' replication files" in p
    assert "/data/authors_files (the authors' replication files" in render(run, "3")
    assert "Internet Appendix: /data/ia.pdf" in render(run, "5")


def test_render_ignores_study_cluster_block(run: Path) -> None:
    policy(run, slurm={"python": "/opt/invented/bin/python", "session_mem_gb": 1})
    for step in ("3", "3F", "3C"):
        text = render(run, step)
        assert "Slurm" not in text and "shard" not in text


def test_render_rebuilt_package_rule(run: Path) -> None:
    assert "REBUILT-PACKAGE RULE" in render(run, "0")
    st = json.loads((run / "package_status.json").read_text())
    st["finder"]["status"] = "authors_data_and_code"
    del st["builder"]
    (run / "package_status.json").write_text(json.dumps(st))
    assert "THIRD-PARTY REBUILD" not in render(run, "0")


def test_render_without_policy_has_no_footer(run: Path) -> None:
    (run / "run_policy.json").unlink()
    text = render(run, "1")
    assert "RUN ISOLATION" not in text and "{{" not in text


def test_every_prompt_placeholder_is_known() -> None:
    import re
    mod = load_script_module("render_prompt")
    assert sorted(mod.STEPS) == sorted(STEPS)
    for step, fn in mod.STEPS.items():
        assert (CORE / "prompts" / fn).is_file(), fn
    used = set()
    for f in (CORE / "prompts").glob("*.md"):
        used |= set(re.findall(r"\{\{([A-Z_]+)\}\}", f.read_text()))
    assert "CODEBASE" not in used
    assert {"DATA_SOURCES", "ANCHOR_REVIEW_READ", "ANCHOR_REVIEW_CHECK"} <= used


# --------------------------------------------------------------------------- new_run

def test_new_run(tmp_path: Path) -> None:
    p = subprocess.run([sys.executable, str(SCRIPTS / "new_run.py"), "my_paper", "--root", str(tmp_path / "runs")],
                       capture_output=True, text=True, env=ENV)
    assert p.returncode == 0, p.stderr
    r = tmp_path / "runs" / "my_paper"
    for d in ("package", "benchmark", "repro", "figures", "prompts_rendered"):
        assert (r / d).is_dir()
    assert (r / "gates.log").exists() and "NOT VISIBLE TO AGENTS" in (r / "benchmark" / "README.md").read_text()
    assert str(SCRIPTS / "render_prompt.py") in p.stdout


# --------------------------------------------------------------------------- GP

def test_gp(run: Path) -> None:
    p = script("gate_gp", run)
    assert p.returncode == 0 and "GP: PASS" in p.stdout
    assert gate_lines(run)[-1][1:3] == ["GP", "PASS"]
    (run / "package" / "PACKAGE_README.md").unlink()
    p = script("gate_gp", run)
    assert p.returncode == 1 and "missing package/PACKAGE_README.md" in p.stderr


def test_gp_infeasible(run: Path) -> None:
    st = json.loads((run / "package_status.json").read_text())
    st["builder"].update(feasible=False, infeasible_reason="needs invented confidential microdata")
    (run / "package_status.json").write_text(json.dumps(st))
    p = script("gate_gp", run)
    assert p.returncode == 2 and "GP: INFEASIBLE" in p.stdout


# --------------------------------------------------------------------------- GS

def test_gs(run: Path) -> None:
    p = script("gate_gs", run)
    assert p.returncode == 0 and "GS: PASS" in p.stdout
    (run / "sample_reconciliation_r2.md").write_text("table\n\nSAMPLE_REVIEW: FIXABLE n=1\n")
    p = script("gate_gs", run)                      # the latest round's file decides
    assert p.returncode == 3 and "GS: FIXABLE" in p.stdout
    for f in run.glob("sample_reconciliation*.md"):
        f.unlink()
    assert script("gate_gs", run).returncode == 1


# --------------------------------------------------------------------------- GC

def test_gc_with_anchor(run: Path) -> None:
    p = script("gate_gc", run)
    assert p.returncode == 0 and "GC: PASS" in p.stdout and "anchor: Tab 2, Col 1" in p.stdout
    cfg = yaml.safe_load((run / "config.yaml").read_text())
    cfg["headline_estimates"][0]["ia_location"] = "Tab 9, Col 9"
    (run / "config.yaml").write_text(yaml.safe_dump(cfg))
    p = script("gate_gc", run)
    assert p.returncode == 1 and "is not an anchor location" in p.stderr


def test_gc_without_anchor(run: Path) -> None:
    (run / "headline_anchor.json").unlink()
    p = script("gate_gc", run)
    assert p.returncode == 0 and "anchor check skipped" in p.stdout
    assert gate_lines(run)[-1][1:3] == ["GC", "PASS"] and "(no anchor)" in gate_lines(run)[-1][3]
    cfg = yaml.safe_load((run / "config.yaml").read_text())
    cfg["headline_estimates"][1]["gating"] = True        # two gating targets
    (run / "config.yaml").write_text(yaml.safe_dump(cfg))
    p = script("gate_gc", run)
    assert p.returncode == 1 and "2 gating targets" in p.stderr


def test_gc_reviewer_fail(run: Path) -> None:
    with (run / "config_review.md").open("a") as fh:
        fh.write("\nCONFIG_REVIEW: FAIL - invented transcription mismatch\n")
    p = script("gate_gc", run)
    assert p.returncode == 1 and "reviewer: CONFIG_REVIEW: FAIL" in p.stderr


# --------------------------------------------------------------------------- G0

def test_g0_pass_freezes(run: Path) -> None:
    p = script("gate_g0", run)
    assert p.returncode == 0 and "G0: PASS" in p.stdout
    assert "dev=2.47% ok" in p.stdout and "1 gating targets, 1 informational" in p.stdout
    fz = frozen(run)
    assert fz["config.yaml"]["gate"] == "G0" and fz["reproduction.md"]["gate"] == "G0"
    assert gate_lines(run)[-1][1:3] == ["G0", "PASS"]


def test_g0_fail_outside_tolerance(run: Path) -> None:
    p = script("gate_g0", run, "--tol", "0.01")
    assert p.returncode == 1 and "G0: FAIL" in p.stderr and "h1: deviation 2.47% > 1%" in p.stderr
    assert not frozen(run)


def test_g0_policy_tolerance(run: Path) -> None:
    policy(run, g0_tol=0.02)
    assert script("gate_g0", run).returncode == 1
    policy(run, g0_tol=0.03)
    assert script("gate_g0", run).returncode == 0


def test_g0_infeasible_and_missing(run: Path) -> None:
    md = (run / "reproduction.md").read_text().replace("| h1 | 0.050 | 0.0512345 |", "| h1 | 0.050 | not estimable |")
    (run / "reproduction.md").write_text(md)
    p = script("gate_g0", run)
    assert p.returncode == 2 and "G0: INFEASIBLE" in p.stdout
    (run / "reproduction.md").unlink()
    p = script("gate_g0", run)
    assert p.returncode == 1 and "reproduction.md missing" in p.stderr


# --------------------------------------------------------------------------- G0A

@pytest.mark.parametrize("verdict,code", [("CLEAN", 0), ("MINOR n=2", 5), ("ERRORS n=1", 3),
                                          ("UNRESOLVED - could not check invented dates", 4)])
def test_g0a_verdicts(run: Path, verdict: str, code: int) -> None:
    (run / "g0_audit_r2.md").write_text(f"findings\n\nG0_AUDIT: {verdict}\n")   # latest round decides
    p = script("gate_g0a", run)
    assert p.returncode == code, p.stdout + p.stderr
    if code == 0:
        assert frozen(run)["reproduction.md"]["gate"] == "G0"
        assert gate_lines(run)[-1][1:3] == ["G0A", "PASS"]


def test_g0a_no_verdict(run: Path) -> None:
    (run / "g0_audit.md").write_text("no verdict line\n")
    assert script("gate_g0a", run).returncode == 1
    (run / "g0_audit.md").unlink()
    p = script("gate_g0a", run)
    assert p.returncode == 1 and "no g0_audit" in p.stderr


# --------------------------------------------------------------------------- G2

def test_g2(run: Path) -> None:
    p = script("gate_g2", run)
    assert p.returncode == 0 and "G2: PASS" in p.stdout and "4  ->  100 cells" in p.stdout
    assert frozen(run)["audit.json"]["gate"] == "G2"


def test_g2_fail(run: Path) -> None:
    a = json.loads((run / "audit.json").read_text())
    a["verdicts"] = a["verdicts"][1:]
    a["recommended_grid"]["held_fixed"] = a["recommended_grid"]["held_fixed"][:1]
    (run / "audit.json").write_text(json.dumps(a))
    p = script("gate_g2", run)
    assert p.returncode == 1
    assert "1 options without a verdict" in p.stdout and "neither crossed nor held_fixed" in p.stdout


# --------------------------------------------------------------------------- G4

def test_g4(run: Path) -> None:
    p = script("gate_g4", run)
    assert p.returncode == 0 and "G4: PASS" in p.stdout and "grid rows 100 / designated cells 100" in p.stdout
    assert frozen(run)["grid_results.csv"]["gate"] == "G4"


def test_g4_blocker_and_unaudited_fixes(run: Path) -> None:
    (run / "implementation_audit.md").write_text("TALLY: blockers=1 major=0 minor=0\n")
    p = script("gate_g4", run)
    assert p.returncode == 1 and "unresolved blocker" in p.stdout
    (run / "implementation_audit.md").write_text("TALLY: blockers=0 major=2 minor=0\n")
    p = script("gate_g4", run)
    assert p.returncode == 1 and "no fix_log.md" in p.stdout
    (run / "fix_log.md").write_text("FIX ROUND 1 COMPLETE: fixed=2 rebutted=0 open=0\n")
    (run / "implementation_audit_r2.md").write_text("TALLY: blockers=0 major=0 minor=0\n")
    assert script("gate_g4", run).returncode == 0


def test_g4_incomplete_or_still_writing(run: Path) -> None:
    os.utime(run / "grid_results.csv", None)
    p = script("gate_g4", run)
    assert p.returncode == 1 and "still running?" in p.stdout
    rows = (run / "grid_results.csv").read_text().splitlines(True)
    (run / "grid_results.csv").write_text("".join(rows[:51]))
    old = time.time() - 3600
    os.utime(run / "grid_results.csv", (old, old))
    p = script("gate_g4", run)
    assert p.returncode == 1 and "grid incomplete: 50 of 100 cells" in p.stdout


# --------------------------------------------------------------------------- freeze

def test_freeze(run: Path) -> None:
    assert script("freeze", run, "--check").returncode == 0           # nothing frozen yet
    assert script("freeze", run, "--gate", "G2", "--set").returncode == 0
    assert set(frozen(run)) == {"config.yaml", "reproduction.md", "forks.json", "audit.json"}
    assert script("freeze", run, "--check").returncode == 0
    (run / "forks.json").write_text((run / "forks.json").read_text() + "\n")
    p = script("freeze", run, "--check")
    assert p.returncode == 1 and "CHANGED" in p.stdout and "forks.json" in p.stderr
    assert script("freeze", run, "--gate", "G4C", "--set").returncode == 0
    assert "companion_results.csv" in frozen(run)


# --------------------------------------------------------------------------- survival

def _expected(path: Path) -> tuple[int, int, float, float]:
    rows = list(csv.DictReader(path.open()))
    ok = [r for r in rows if not r["err"].strip()]
    pos = [r for r in ok if float(r["estimate"]) > 0]
    surv = [r for r in pos if float(r["p_value"]) < 0.05]
    return len(rows), len(rows) - len(ok), len(pos) / len(ok), len(surv) / len(ok)


def test_survival(run: Path) -> None:
    p = script("survival", run)
    assert p.returncode == 0, p.stderr
    h = json.loads((run / "grid_survival_headline.json").read_text())
    n, n_err, sign, surv = _expected(run / "grid_results.csv")
    assert h["n_cells_total"] == n and h["n_cells_error"] == n_err == 1
    assert h["n_cells_in_denominator"] == n - n_err and h["n_rows_off_grid_excluded"] == 0
    assert h["sign_consistency_rate"] == pytest.approx(sign)
    assert h["survival_rate"] == pytest.approx(surv)
    assert h["significance_rule"] == "p_value < 0.05"
    per = list(csv.DictReader((run / "grid_survival.csv").open()))
    assert {r["dimension"] for r in per} == {"outcome", "sample", "controls", "fixed_effects"}
    assert all(0 <= float(r["kill_power"]) <= 1 for r in per)


def test_survival_off_grid_rows_and_joint_rule(run: Path) -> None:
    rows = (run / "grid_results.csv").read_text().splitlines()
    rows.append("anchor,outcome_level,sample_full,ctrl_base,fe_paper_only,0.05,0.01,0.0001,analytic,1200,120,")
    (run / "grid_results.csv").write_text("\n".join(rows) + "\n")
    assert script("survival", run).returncode == 0
    h = json.loads((run / "grid_survival_headline.json").read_text())
    assert h["n_rows_off_grid_excluded"] == 1 and h["n_cells_total"] == 100


def test_survival_companion(run: Path) -> None:
    p = script("survival", run, "--companion")
    assert p.returncode == 0, p.stderr
    h = json.loads((run / "grid_survival_companion_headline.json").read_text())
    n, n_err, sign, surv = _expected(run / "companion_results.csv")
    assert h["survival_rate"] == pytest.approx(surv) and h["sign_consistency_rate"] == pytest.approx(sign)
    assert (run / "grid_survival_companion.csv").exists()


# --------------------------------------------------------------------------- G6

def test_g6(run: Path) -> None:
    p = script("gate_g6", run)
    assert p.returncode == 0 and "G6: PASS" in p.stdout
    assert frozen(run)["report.md"]["gate"] == "G6"


def test_g6_hand_typed_number_fails(run: Path) -> None:
    tampered = (run / "report.md").read_text() + "A hand-typed number: 0.123\n"
    (run / "report.md").write_text(tampered)
    p = script("gate_g6", run)
    assert p.returncode == 1 and "G6: FAIL - regenerated report differs" in p.stderr
    assert (run / "report.md").read_text() == tampered          # the committed report is restored
    assert not (run / ".report.md.g6bak").exists()


def test_g6_generator_error(run: Path) -> None:
    (run / "make_report.py").write_text("raise SystemExit(3)\n")
    p = script("gate_g6", run)
    assert p.returncode == 1 and "generator exited 3" in p.stderr


# --------------------------------------------------------------------------- spec_curve

def test_spec_curve(run: Path) -> None:
    import pandas as pd
    mod = load_script_module("spec_curve")
    g = pd.read_csv(run / "grid_results.csv")
    out = mod.spec_curve(g, ["outcome", "sample"], out=run / "figures" / "spec_curve.png",
                         paper_choice={"outcome": "outcome_level"}, published=0.05)
    assert out.exists() and out.stat().st_size > 0
    assert mod.pvalue_hist(g, out=run / "figures" / "p.png").exists()


# --------------------------------------------------------------------------- the full gate sequence

def test_full_sequence_on_fixture(run: Path) -> None:
    for name, args, code in [("gate_gp", (), 0), ("gate_gs", (), 0), ("gate_gc", (), 0), ("gate_g0", (), 0),
                             ("gate_g2", (), 0), ("gate_g4", (), 0), ("freeze", ("--gate", "G4C", "--set"), 0),
                             ("freeze", ("--check",), 0), ("survival", (), 0), ("survival", ("--companion",), 0),
                             ("gate_g6", (), 0), ("freeze", ("--check",), 0)]:
        p = script(name, run, *args)
        assert p.returncode == code, (name, p.stdout, p.stderr)
    verdicts = [(l[1], l[2]) for l in gate_lines(run)]
    assert verdicts == [("GP", "PASS"), ("GS", "PASS"), ("GC", "PASS"), ("G0", "PASS"), ("G2", "PASS"),
                        ("G4", "PASS"), ("G6", "PASS")]
