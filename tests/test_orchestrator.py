"""Orchestrator scenarios with a fake agent backend.

Every role is played by ``FakePaper``, which writes the smallest artifacts the real gate
scripts accept (or reject, where a scenario needs a failure). The gates, render_prompt,
freeze and survival scripts run for real from the core directory, so these tests exercise
the same command lines and exit codes as a real run.

The core directory defaults to the packaged ``ama_mcp/core``; ``AMA_TEST_CORE_DIR`` points
the tests at another copy.
"""
from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import ama_mcp  # noqa: E402
from ama_mcp import runs  # noqa: E402
from ama_mcp.orchestrator import STATE_FILE, Orchestrator, read_json  # noqa: E402

try:
    from ama_mcp.runners import FakeRunner  # noqa: E402
except Exception:  # pragma: no cover - runners not importable: a stand-in with the same contract
    from types import SimpleNamespace

    class FakeRunner:  # type: ignore[no-redef]
        backend = "fake"

        def __init__(self, handler):
            self.handler = handler

        def run(self, prompt, *, workdir, role, label, model, log_path, timeout_s=None, extra_dirs=()):
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            code = self.handler(role, prompt, Path(workdir), Path(log_path))
            Path(log_path).write_text(f"fake {role}\n")
            return SimpleNamespace(exit_code=code, last_message="", log_path=str(log_path), duration_s=0.0,
                                   timed_out=False, error=None)


NEEDED = ["render_prompt.py", "new_run.py", "gate_g0.py", "gate_g0a.py", "gate_gp.py", "gate_gs.py",
          "gate_gc.py", "gate_g2.py", "gate_g4.py", "gate_g6.py", "freeze.py", "survival.py", "_gatelog.py"]


def _core() -> Path:
    env = os.environ.get("AMA_TEST_CORE_DIR")
    core = Path(env).resolve() if env else Path(ama_mcp.__file__).resolve().parent / "core"
    missing = [n for n in NEEDED if not (core / "scripts" / n).exists()]
    if missing or not (core / "prompts").is_dir():
        pytest.skip(f"core scripts not available in {core}: {missing}")
    return core


@pytest.fixture(scope="module")
def core() -> Path:
    return _core()


class SimulatedCrash(BaseException):
    """Stands in for the orchestrator process dying (kill -9, machine restart)."""


PUBLISHED = 0.500
ANCHOR = {"source": "test fixture", "draft_used": "2000",
          "ia_outcomes": [{"outcome": "y", "location": "Table 2, Col 1"}]}

CONFIG = """\
run_id: {run_id}
paper:
  title: A test paper
  authors: A. Author
  venue: Test Journal
  year: 2000
  doi_or_url: none
research_question: Does x raise y?
published_conclusion:
  statement: x raises y
  direction: positive
  significance: p < 0.05, two-sided
headline_estimates:
  - id: h1
    location: Table 2, col. 1
    ia_location: Table 2, Col 1
    quantity: effect of x on y
    value: 0.500
    std_error: 0.100
    t_or_p: 5.0
    n_obs: 1000
    target_type: published
design: firm-year panel; OLS with firm and year fixed effects; standard errors clustered by firm
authors_fork_choices:
  - dimension: d1
    option: d1_a
    where: Table 2
data:
  paths:
    - path: package/data/panel.csv
      what: the panel
      quirks: none
  forbidden: [benchmark/]
compute:
  python: python
  mem_cap_gb: 4
  seed: 20260923
notes: test fixture
"""

MAKE_REPORT = """\
import json, pathlib
h = json.loads(pathlib.Path("grid_survival_headline.json").read_text())
pathlib.Path("report.md").write_text("# Report\\n\\nsurvival rate: %.3f\\n" % h["survival_rate"])
"""

DIMS = [("d1", 5), ("d2", 5), ("d3", 2), ("d4", 2)]


def _old(path: Path, age: float = 900) -> None:
    t = time.time() - age
    os.utime(path, (t, t))


class FakePaper:
    """Plays every role of one paper. Per-step lists give the outcome of each successive call."""

    def __init__(self, run: Path, **cfg):
        self.run = Path(run)
        self.cfg = {
            "finder": "none_found", "feasible": True, "sr": ["BEST_ACHIEVABLE"], "cr": ["PASS"],
            "repro": [0.501], "g0a": ["CLEAN"], "audit_valid": [True], "tallies": [(0, 0, 1)],
            "restored": 1, "ctallies": [(0, 0, 0)], "report_ok": [True], "grid_rows": [None],
            "no_artifact": {}, "crash_before": None, "crash_after": None,
        }
        self.cfg.update(cfg)
        self.calls: list[str] = []
        self.n = collections.Counter()

    @staticmethod
    def _pick(seq, i):
        return seq[min(i, len(seq) - 1)]

    def __call__(self, role, prompt, workdir, log_path):
        name = re.search(r"prompts_rendered/(step\S+?\.md)", prompt).group(1)
        assert Path(workdir).resolve() == self.run.resolve()
        assert "is NOT addressed to you" in prompt
        sfx = name[4 + len(role):-3]
        rnd = int(sfx[2:]) if re.fullmatch(r"_r\d+", sfx) else 1
        i = self.n[role]
        self.n[role] += 1
        self.calls.append(f"{role}{sfx}")
        if self.cfg["crash_before"] == (role, i):
            raise SimulatedCrash(f"crash before {role}{sfx}")
        if i < self.cfg["no_artifact"].get(role, 0):
            return 0
        getattr(self, "do_zero" if role == "0" else "do_" + role)(sfx, rnd, i)
        if self.cfg["crash_after"] == (role, i):
            raise SimulatedCrash(f"crash after {role}{sfx}")
        return 0

    # -------------------------------------------------------------------- Package
    def _status(self):
        return json.loads((self.run / "package_status.json").read_text())

    def do_P(self, sfx, rnd, i):
        pkg = self.run / "package"
        (self.run / "package_status.json").write_text(json.dumps({"run_id": self.run.name, "finder": {
            "status": self.cfg["finder"], "downloaded": [],
            "evidence": [{"where": "fake search", "result": "nothing usable"}], "web_search_available": False}}))
        (pkg / "PACKAGE_README.md").write_text("| path | what it is | origin URL | retrieved |\n")
        if self.cfg["finder"] == "authors_data_and_code":
            (pkg / "authors_package").mkdir(exist_ok=True)
            (pkg / "authors_package" / "data.csv").write_text("a,b\n1,2\n")

    def do_B(self, sfx, rnd, i):
        pkg = self.run / "package"
        for d in ("data", "code"):
            (pkg / d).mkdir(exist_ok=True)
        (pkg / "BUILD_LOG.md").write_text("# Build log\n")
        (pkg / "data" / "VARIABLES.md").write_text("y, x\n")
        (pkg / "data" / "panel.csv").write_text("firm,year,y,x\n1,2000,1,0\n")
        (pkg / "code" / "build.py").write_text("# build\n")
        st = self._status()
        ok = self.cfg["feasible"]
        st["builder"] = {"feasible": ok, "infeasible_reason": None if ok else "headline outcome not available",
                         "headline_outcome_available": ok, "mode": "rebuilt"}
        (self.run / "package_status.json").write_text(json.dumps(st))

    def do_SR(self, sfx, rnd, i):
        v = self._pick(self.cfg["sr"], i)
        v = "FIXABLE n=1" if v == "FIXABLE" else v
        (self.run / f"sample_reconciliation{sfx}.md").write_text(f"| item | paper | package |\n\nSAMPLE_REVIEW: {v}\n")

    def do_BF(self, sfx, rnd, i):
        with (self.run / "package" / "BUILD_LOG.md").open("a") as fh:
            fh.write(f"\n## Fix round {rnd} ({sfx[1:]}.md)\n")

    # --------------------------------------------------------------------- Config
    def do_C(self, sfx, rnd, i):
        (self.run / "config.yaml").write_text(CONFIG.format(run_id=self.run.name))

    def do_CR(self, sfx, rnd, i):
        v = self._pick(self.cfg["cr"], i)
        (self.run / "config_review.md").write_text(
            "checks\n\nCONFIG_REVIEW: " + ("PASS" if v == "PASS" else "FAIL - value mistranscribed") + "\n")

    # ------------------------------------------------------------------ Reproduce
    def do_zero(self, sfx, rnd, i):
        v = self._pick(self.cfg["repro"], i)
        row = (f"| h9 | {PUBLISHED:.3f} | 0.5 | 0% | wrong id |" if v == "missing" else
               f"| h1 | {PUBLISHED:.3f} | not estimable | - | outcome missing |" if v == "not estimable" else
               f"| h1 | {PUBLISHED:.3f} | {v:.6f} | {abs(v - PUBLISHED) / PUBLISHED:.1%} | |")
        (self.run / "reproduction.md").write_text(
            "# Reproduction\n\n| target | published | reproduced | deviation | note |\n|---|---|---|---|---|\n"
            f"{row}\n\nGATE0: see table\n")

    def do_G0A(self, sfx, rnd, i):
        v = self._pick(self.cfg["g0a"], i)
        v = {"MINOR": "MINOR n=1", "ERRORS": "ERRORS n=2", "UNRESOLVED": "UNRESOLVED - could not check"}.get(v, v)
        (self.run / f"g0_audit{sfx}.md").write_text(f"checks\n\nG0_AUDIT: {v}\n")

    # ---------------------------------------------------------------------- Forks
    def _dims(self):
        return DIMS + ([("d5", 2)] if self.cfg["restored"] else [])

    def do_1(self, sfx, rnd, i):
        dims = [{"id": d, "paper_choice": f"{d}_a",
                 "options": [{"id": f"{d}_{chr(97 + k)}", "label": f"{d} option {k}"} for k in range(n)]}
                for d, n in self._dims()]
        (self.run / "forks.json").write_text(json.dumps({"dimensions": dims}))

    def do_2(self, sfx, rnd, i):
        valid = self._pick(self.cfg["audit_valid"], i)
        opts = [f"{d}_{chr(97 + k)}" for d, n in self._dims() for k in range(n)]
        crossed = [{"dimension": d, "option_ids": [f"{d}_{chr(97 + k)}" for k in range(n)]}
                   for d, n in (DIMS if valid else DIMS[:3])]
        held = [{"dimension": "d5", "option_id": "d5_b", "why": "the paper's option is ruled out"}] \
            if self.cfg["restored"] else []
        n_cells = 1
        for c in crossed:
            n_cells *= len(c["option_ids"])
        (self.run / "audit.json").write_text(json.dumps({
            "verdicts": [{"option_id": o, "verdict": "VALID", "reason": "fits the rubric"} for o in opts],
            "recommended_grid": {"crossed_dimensions": crossed, "held_fixed": held, "n_cells": n_cells},
            "inference_designation": {"method": "cluster-robust by firm", "reason": "the paper's clustering"}}))

    # ----------------------------------------------------------------------- Grid
    def _grid_rows(self, extra_dim=None):
        import itertools
        dims = DIMS + ([extra_dim] if extra_dim else [])
        levels = [[f"{d}_{chr(97 + k)}" for k in range(n)] for d, n in dims]
        rows = []
        for j, combo in enumerate(itertools.product(*levels)):
            est = 0.4 + 0.001 * j if j % 4 else -0.1
            rows.append([f"c{j:03d}", *combo, f"{est:.4f}", "0.1", "0.01" if j % 3 else "0.4",
                         "cluster", "1000", "50", ""])
        header = ["cell_id", *[d for d, _ in dims], "estimate", "std_error", "p_value", "inference_method",
                  "n_obs", "n_clusters", "err"]
        return header, rows

    def do_3(self, sfx, rnd, i):
        (self.run / "run_grid.py").write_text("# grid script\n")
        header, rows = self._grid_rows()
        rows = rows[:self._pick(self.cfg["grid_rows"], i)]
        f = self.run / "grid_results.csv"
        f.write_text("\n".join(",".join(r) for r in [header, *rows]) + "\n")
        _old(f)

    def do_4(self, sfx, rnd, i):
        b, mj, mn = self._pick(self.cfg["tallies"], i)
        (self.run / f"implementation_audit{sfx}.md").write_text(
            f"findings\n\nTALLY: blockers={b} major={mj} minor={mn}\n")

    def do_3F(self, sfx, rnd, i):
        with (self.run / "fix_log.md").open("a") as fh:
            fh.write(f"### F{rnd}\nDisposition: fixed+rerun\n\nFIX ROUND {rnd} COMPLETE: fixed=1 rebutted=0 open=0\n")

    # ------------------------------------------------------------------ Companion
    def do_3C(self, sfx, rnd, i):
        if not self.cfg["restored"]:
            (self.run / "companion_spec.json").write_text(json.dumps({"restored": [], "n_cells": 100}))
            return
        (self.run / "companion_spec.json").write_text(json.dumps({
            "restored": [{"dimension": "d5", "kind": "held_fixed", "audit_option(s)": "d5_b",
                          "paper_option": "d5_a", "audit_verdict": "VALID", "audit_reason": "test"}],
            "n_cells": 200}))
        (self.run / "run_companion.py").write_text("# companion\n")
        header, rows = self._grid_rows(("d5", 2))
        f = self.run / "companion_results.csv"
        f.write_text("\n".join(",".join(r) for r in [header, *rows]) + "\n")
        _old(f)

    def do_4C(self, sfx, rnd, i):
        b, mj, mn = self._pick(self.cfg["ctallies"], i)
        (self.run / f"companion_audit{sfx}.md").write_text(f"findings\n\nTALLY: blockers={b} major={mj} minor={mn}\n")

    def do_3CF(self, sfx, rnd, i):
        with (self.run / "companion_fix_log.md").open("a") as fh:
            fh.write(f"COMPANION FIX ROUND {rnd} COMPLETE: fixed=1 rebutted=0 open=0\n")
        _old(self.run / "companion_results.csv")

    # --------------------------------------------------------------------- Report
    def do_5(self, sfx, rnd, i):
        (self.run / "author_commentary.md").write_text("# Author commentary\n")

    def do_6(self, sfx, rnd, i):
        (self.run / "make_report.py").write_text(MAKE_REPORT)
        h = json.loads((self.run / "grid_survival_headline.json").read_text())
        good = f"# Report\n\nsurvival rate: {h['survival_rate']:.3f}\n"
        ok = self._pick(self.cfg["report_ok"], i)
        (self.run / "report.md").write_text(good if ok else "# Report\n\nsurvival rate: 0.999 (typed by hand)\n")


# ------------------------------------------------------------------------- harness

def make_run(tmp_path: Path, core: Path, **kw) -> Path:
    pdf = tmp_path / "test_paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% a fake paper\n")
    anchor = tmp_path / "anchor.json"
    anchor.write_text(json.dumps(ANCHOR))
    kw.setdefault("headline_anchor_file", anchor)
    return runs.create_run(pdf, runs_root=tmp_path / "runs", run_id=kw.pop("run_id", "paper1"), backend="fake",
                           core_dir=core, **kw)


def orch(run: Path, core: Path, paper: FakePaper, sleep=None, **opts) -> Orchestrator:
    o = {"poll_interval_s": 0, "max_wait_polls": 2, "companion_max_polls": 2, **opts}
    return Orchestrator(run, runner=FakeRunner(paper), core_dir=core, options=o, sleep=sleep or (lambda s: None),
                        echo=False)


def gates_log(run: Path) -> str:
    return (run / "gates.log").read_text()


FULL_PATH = ["P", "B", "SR", "C", "CR", "0", "1", "2", "3", "4", "3C", "4C", "5", "6"]


# --------------------------------------------------------------------------- tests

def test_full_pass_path(tmp_path, core):
    run = make_run(tmp_path, core, citation="A. Author (2000), A test paper, Test Journal")
    paper = FakePaper(run)
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True, rec
    assert paper.calls == FULL_PATH
    assert rec["finder_status"] == "none_found"
    assert rec["sample"] == "best_achievable_r1"
    assert rec["g0_path"] == "pass"
    assert rec["tallies"] == ["r1: B=0 M=0 m=1"]
    assert rec["companion_restored"] == 1
    assert rec["companion"] == "audited (r1: B=0 M=0)"
    assert "companion_survival" in rec
    for g in ("GS_r1", "GP", "GC", "G0", "G2", "G4", "G6"):
        assert g in rec["gates"], g
    assert isinstance(rec["survival_headline"], dict) and "survival_rate" in rec["survival_headline"]
    st = read_json(run / STATE_FILE)
    assert st["status"] == "completed" and st["completed_stages"][-1] == "report"
    assert len(st["roles"]) == len(FULL_PATH)
    assert all((run / r["log_path"]).exists() for r in st["roles"])
    res = read_json(run / "ama_result.json")
    assert res["completed"] is True and res["survival_headline"]["n_cells_in_denominator"] > 0
    assert "metrics" in res
    # the finance paper's measures were written before the reporter and lead the headline
    pm = read_json(run / "paper_measures.json")
    assert list(pm)[0] == "share_supported" and "value" in pm["share_supported"], pm
    hl = runs.headline(run)
    assert list(hl)[0] == "share_supported" and "strict_survival_rate_p05" in hl, hl
    assert runs.status(run)["headline"] == hl and runs.results(run)["headline"] == hl
    log = gates_log(run)
    for g in ("GS\tPASS", "GP\tPASS", "GC\tPASS", "G0\tPASS", "G2\tPASS", "G4\tPASS", "G6\tPASS"):
        assert g in log, g
    # the citation reached the config drafter through --paper-hint
    assert "A. Author (2000), A test paper" in (run / "prompts_rendered" / "stepC.md").read_text()
    # a second run on a finished directory does nothing
    again = FakePaper(run)
    orch(run, core, again).run()
    assert again.calls == []


def test_g0_fail_minor_continues(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, repro=[0.8, 0.79], g0a=["MINOR"])
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True, rec
    assert rec["g0_path"] == "minor_errors_fixed_r1"
    assert paper.calls[:12] == ["P", "B", "SR", "C", "CR", "0", "G0A", "BF_g0_audit", "0_r2", "1", "2", "3"]
    assert (run / "reproduction_r1.md").exists()
    assert (run / "prompts_rendered" / "stepBF_g0_audit.md").exists()
    assert "G0A_r1" in rec["gates"] and "G0_after_fix_r1" in rec["gates"]


def test_g0a_errors_twice_stops(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, repro=[0.8], g0a=["ERRORS", "ERRORS"])
    rec = orch(run, core, paper).run()
    assert rec["completed"] is False
    assert rec["stopped_at"] == "G0A"
    assert "implementation errors remain after 2 audit rounds" in rec["reason"]
    assert paper.calls[5:] == ["0", "G0A", "BF_g0_audit", "0_r2", "G0A_r2"]
    assert read_json(run / STATE_FILE)["status"] == "stopped"
    assert read_json(run / "ama_result.json")["stopped_at"] == "G0A"


def test_g0a_errors_then_pass_after_fix(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, repro=[0.8, 0.502], g0a=["ERRORS"])
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True and rec["g0_path"] == "pass_after_fix_r1"


def test_g0a_unresolved_stops(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, repro=[0.8], g0a=["UNRESOLVED"])
    rec = orch(run, core, paper).run()
    assert rec["stopped_at"] == "G0A" and "UNRESOLVED" in rec["reason"]
    assert paper.calls[-1] == "G0A" and "BF_g0_audit" not in paper.calls


def test_g0_infeasible_and_procedural_retry(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, repro=["missing", "not estimable"])
    rec = orch(run, core, paper).run()
    assert paper.calls[5:] == ["0", "0_r2"]  # one fresh reproducer after the procedural failure
    assert rec["stopped_at"] == "G0" and rec["reason"].startswith("INFEASIBLE")


def test_g2_retry(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, audit_valid=[False, True])
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True
    assert paper.calls.count("2") == 2
    assert "G2\tFAIL" in gates_log(run) and "G2\tPASS" in gates_log(run)


def test_g4_loop_closes_in_round_2(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, tallies=[(1, 2, 0), (0, 1, 3)])
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True, rec
    assert rec["tallies"] == ["r1: B=1 M=2 m=0", "r2: B=0 M=1 m=3"]
    i = paper.calls.index("3")
    assert paper.calls[i:i + 4] == ["3", "4", "3F", "4_r2"]
    assert (run / "implementation_audit_r2.md").exists() and (run / "fix_log.md").exists()


def test_g4_loop_not_closing_stops(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, tallies=[(1, 0, 0)])
    rec = orch(run, core, paper).run()
    assert rec["stopped_at"] == "G4"
    assert "did not close after 4 rounds" in rec["reason"]
    assert paper.calls.count("3F") + paper.calls.count("3F_r2") + paper.calls.count("3F_r3") == 3
    assert paper.calls[-1] == "4_r4"
    assert len(rec["tallies"]) == 4


def test_major_only_in_round_one_needs_a_fix_round(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, tallies=[(0, 1, 0), (0, 0, 0)])
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True and rec["tallies"] == ["r1: B=0 M=1 m=0", "r2: B=0 M=0 m=0"]


def test_incomplete_grid_reruns_implementer_once(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, grid_rows=[50, None])
    slept = []
    rec = orch(run, core, paper, sleep=slept.append, grid_dead_s=0).run()
    assert rec["completed"] is True
    assert paper.calls.count("3") == 2 and len(slept) == 2  # waited the full (test-sized) time first


def test_abandoned_grid_is_not_waited_for(tmp_path, core):
    if not Path("/proc/self/cwd").exists():
        pytest.skip("needs /proc to tell that no process is writing the grid")
    run = make_run(tmp_path, core)
    paper = FakePaper(run, grid_rows=[50, None])
    slept = []
    rec = orch(run, core, paper, sleep=slept.append, grid_dead_s=60).run()
    assert rec["completed"] is True
    assert paper.calls.count("3") == 2 and slept == []


def test_grid_incomplete_after_two_implementers_stops(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, grid_rows=[50])
    rec = orch(run, core, paper, grid_dead_s=0).run()
    assert rec["stopped_at"] == "G3" and "two implementer sessions" in rec["reason"]


def test_companion_none(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, restored=0)
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True
    assert rec["companion"].startswith("none")
    assert "4C" not in paper.calls and "companion_survival" not in rec
    ce = read_json(run / "paper_measures.json")["companion_share_supported"]
    assert ce["value"] is None and ce["note"].startswith("no companion grid")


def test_companion_not_cleared_is_recorded(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, ctallies=[(1, 0, 0)])
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True
    assert rec["companion"].startswith("NOT CLEARED (r1: B=1 M=0; r2: B=1 M=0; r3: B=1 M=0)")
    assert [c for c in paper.calls if c.startswith(("4C", "3CF"))] == ["4C", "3CF", "4C_r2", "3CF_r2", "4C_r3"]
    ce = read_json(run / "paper_measures.json")["companion_share_supported"]
    assert ce["value"] is not None and "did not clear" in ce["note"]


def test_agent_edit_of_paper_measures_is_undone_before_g6(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run)
    first = paper.do_6

    def do_6(sfx, rnd, i):
        first(sfx, rnd, i)
        (run / "paper_measures.json").write_text(json.dumps({"share_supported": {"value": 0.987654}}))
    paper.do_6 = do_6
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True
    pm = read_json(run / "paper_measures.json")
    assert pm["share_supported"]["value"] != 0.987654 and "claim_type" in pm


def test_g6_retry(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, report_ok=[False, True])
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True
    assert paper.calls[-3:] == ["5", "6", "6"]
    assert "G6\tFAIL" in gates_log(run)


def test_builder_infeasible_stops_at_gp(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, feasible=False)
    rec = orch(run, core, paper).run()
    assert rec["stopped_at"] == "GP" and rec["reason"].startswith("INFEASIBLE")
    assert paper.calls == ["P", "B"]


def test_authors_package_skips_builder_and_sample_fix(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, finder="authors_data_and_code", sr=["FIXABLE"])
    rec = orch(run, core, paper).run()
    assert "B" not in paper.calls and "BF_sample_reconciliation" not in paper.calls
    assert rec["sample"] == "fixable_items_remain_recorded"


def test_sample_fix_round(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, sr=["FIXABLE", "BEST_ACHIEVABLE"])
    rec = orch(run, core, paper).run()
    assert paper.calls[:6] == ["P", "B", "SR", "BF_sample_reconciliation", "SR_r2", "C"]
    assert rec["sample"] == "best_achievable_r2"


def test_config_redraft_archives_review(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, cr=["FAIL", "PASS"])
    rec = orch(run, core, paper).run(install_signal_handlers=False)
    assert rec["completed"] is True
    assert paper.calls[3:7] == ["C", "CR", "C", "CR"]
    assert (run / "config_review_r1.md").exists() and "FAIL" in (run / "config_review_r1.md").read_text()


def test_missing_artifact_retried_once_then_error_then_resume(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, no_artifact={"1": 2})
    rec = orch(run, core, paper).run()
    st = read_json(run / STATE_FILE)
    assert st["status"] == "error" and "produced no forks.json twice" in st["error"]
    assert paper.calls[-2:] == ["1", "1"]
    # resume: completed work is replayed, the failed role is launched again
    paper2 = FakePaper(run)
    rec = orch(run, core, paper2).run()
    assert rec["completed"] is True
    assert paper2.calls[0] == "1" and "0" not in paper2.calls and "P" not in paper2.calls


def test_resume_after_crash_mid_grid(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, tallies=[(1, 0, 0), (0, 0, 0)], crash_before=("4", 1))
    with pytest.raises(SimulatedCrash):
        orch(run, core, paper).run()
    st = read_json(run / STATE_FILE)
    assert st["status"] == "interrupted" and st["current_stage"] == "grid"
    assert st["inflight"]["step"] == "4" and st["inflight"]["sfx"] == "_r2"
    # a fresh orchestrator resumes at audit round 2: nothing before it runs again
    paper2 = FakePaper(run, tallies=[(0, 0, 0)])
    rec = orch(run, core, paper2).run()
    assert rec["completed"] is True, rec
    assert paper2.calls[0] == "4_r2"
    assert rec["tallies"] == ["r1: B=1 M=0 m=0", "r2: B=0 M=0 m=0"]
    assert "3" not in paper2.calls and "3F" not in paper2.calls


def test_resume_keeps_a_finished_grid_of_an_interrupted_implementer(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, crash_after=("3", 0))
    with pytest.raises(SimulatedCrash):
        orch(run, core, paper).run()
    paper2 = FakePaper(run)
    rec = orch(run, core, paper2).run()
    assert rec["completed"] is True
    assert paper2.calls[0] == "4"  # the implementer is not relaunched: its grid is complete on disk
    st = read_json(run / STATE_FILE)
    assert any(v.get("resumed_from_files") for v in st["journal"].values() if isinstance(v, dict))


def test_stop_after_g2_pauses_then_resumes(tmp_path, core):
    run = make_run(tmp_path, core, stop_after="G2")
    paper = FakePaper(run)
    rec = orch(run, core, paper).run()
    assert rec.get("paused") == "after G2"
    assert paper.calls[-1] == "2"
    assert runs.status(run)["status"] == "paused"
    paper2 = FakePaper(run)
    rec = orch(run, core, paper2).run()
    assert rec["completed"] is True and paper2.calls[0] == "3"
    assert "paused" not in rec


def test_from_stage_redoes_report(tmp_path, core):
    run = make_run(tmp_path, core)
    orch(run, core, FakePaper(run)).run()
    paper2 = FakePaper(run)
    rec = orch(run, core, paper2).run(from_stage="report")
    assert rec["completed"] is True and paper2.calls == ["5", "6"]


def test_paper_measures_failure_does_not_stop_the_run(tmp_path, core, monkeypatch):
    import ama_mcp.metrics as metrics_mod

    def boom(*a, **k):
        raise RuntimeError("no grid")
    monkeypatch.setattr(metrics_mod, "compute_paper_metrics", boom)
    run = make_run(tmp_path, core)
    rec = orch(run, core, FakePaper(run)).run()
    assert rec["completed"] is True, rec
    pm = read_json(run / "paper_measures.json")
    assert pm == {"error": "measures unavailable: RuntimeError: no grid"}
    assert "paper measures unavailable" in (run / "orchestrator.log").read_text()
    hl = runs.headline(run, {"error": "x"})
    assert hl["measures_error"] and "strict_survival_rate_p05" in hl


def test_rate_limited_session_is_relaunched(tmp_path, core):
    from ama_mcp import runners
    if not hasattr(runners, "RoleResult"):
        pytest.skip("runners.RoleResult not available")
    run = make_run(tmp_path, core)
    paper = FakePaper(run)
    limited = {"n": 0}

    def handler(role, prompt, workdir, log_path):
        if role == "P" and limited["n"] == 0:
            limited["n"] += 1
            return runners.RoleResult(1, "usage limit", str(log_path), 0.0, False, "rate_limited")
        return paper(role, prompt, workdir, log_path)

    slept = []
    o = Orchestrator(run, runner=FakeRunner(handler), core_dir=core, sleep=slept.append, echo=False,
                     options={"poll_interval_s": 0, "max_wait_polls": 2, "rate_limit_wait_s": 5})
    rec = o.run()
    assert rec["completed"] is True and slept[0] == 5
    roles = read_json(run / STATE_FILE)["roles"]
    assert roles[0]["error"] == "rate_limited" and roles[1]["step"] == "P"


def test_models_and_extra_dirs_reach_the_runner(tmp_path, core):
    data = tmp_path / "mydata"
    data.mkdir()
    run = make_run(tmp_path, core, models={"4": "model-x", "*": "model-y"}, data_dirs=[data], stop_after="config")
    paper = FakePaper(run)
    fr = FakeRunner(paper)
    Orchestrator(run, runner=fr, core_dir=core, sleep=lambda s: None, echo=False).run()
    calls = getattr(fr, "calls", None)
    if calls is None:
        pytest.skip("this FakeRunner does not record calls")
    assert {c["model"] for c in calls} == {"model-y"}
    assert str(data.resolve()) in calls[0]["extra_dirs"]
    assert str(core) not in calls[0]["extra_dirs"]  # read-only for agents: never an added (writable) dir


def test_create_run_policy_and_status(tmp_path, core):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "readme.txt").write_text("authors' package\n")
    ds = tmp_path / "my_data.md"
    ds.write_text("I can read Compustat.\n")
    run = make_run(tmp_path, core, package_dir=pkg, data_sources_file=ds, g0_tol=0.1, grid_workers=3,
                   grid_mem_gb=8, forbidden_sources=["a comment on this paper"],
                   data_role_sources=[{"cite": "Someone (2020)", "paths": [str(ds)]}])
    pol = read_json(run / "run_policy.json")
    assert pol["g0_tol"] == 0.1 and pol["grid_workers"] == 3 and pol["grid_mem_gb"] == 8
    assert Path(pol["paper_pdf"]).exists() and Path(pol["paper_pdf"]).parent.name == "paper"
    assert pol["forbidden_sources"] == ["a comment on this paper"]
    assert pol["data_sources_file"].endswith("data_sources.md") and Path(pol["data_sources_file"]).exists()
    assert pol["repro_language"] == "Python" and pol["analysis_python"]
    assert pol["local_materials"]["package"]["path"].endswith("authors_package")
    assert (run / "package" / "authors_package" / "readme.txt").exists()
    assert (run / "benchmark").is_dir() and (run / "gates.log").exists()
    s = runs.status(run)
    assert s["status"] == "created" and s["running"] is False and s["headline"] is None
    assert [r["run_id"] for r in runs.list_runs(tmp_path / "runs")] == ["paper1"]
    assert runs.resolve_run("paper1", tmp_path / "runs") == run
    with pytest.raises(FileExistsError):
        make_run(tmp_path, core)
    with pytest.raises(ValueError):
        make_run(tmp_path, core, run_id="x2", models={"Z": "m"})


def test_read_run_file_guards(tmp_path, core):
    run = make_run(tmp_path, core)
    (run / "notes.md").write_text("hello\n")
    assert runs.read_run_file(run, "notes.md")["content"] == "hello\n"
    assert runs.read_run_file(run, "notes.md", max_bytes=2)["truncated"] is True
    listing = runs.read_run_file(run, ".")
    assert "benchmark/" not in [e["name"] for e in listing["entries"]]
    with pytest.raises(PermissionError):
        runs.read_run_file(run, "benchmark/README.md")
    with pytest.raises(PermissionError):
        runs.read_run_file(run, "package/../benchmark/README.md")
    with pytest.raises(ValueError):
        runs.read_run_file(run, "../../etc/hostname")
    with pytest.raises(ValueError):
        runs.read_run_file(run, "/etc/hostname")
    (run / "blob.bin").write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError):
        runs.read_run_file(run, "blob.bin")


def test_stop_only_signals_this_runs_orchestrator(tmp_path, core):
    run = make_run(tmp_path, core)
    # a stand-in process whose command line looks like this run's orchestrator
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "ama_mcp.orchestrator",
                          str(run)], start_new_session=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
    try:
        (run / "orchestrator.pid").write_text(json.dumps({"pid": other.pid}))
        assert runs.stop(run)["stopped"] is False and other.poll() is None
        (run / "orchestrator.pid").write_text(json.dumps({"pid": p.pid}))
        assert runs.running_pid(run) == p.pid
        out = runs.stop(run, grace_s=10)
        assert out["stopped"] is True
        p.wait(timeout=10)
        assert runs.running_pid(run) is None
    finally:
        for proc in (p, other):
            if proc.poll() is None:
                proc.kill()
                proc.wait()


# ------------------------------------------------- fixes after the release review

def test_resume_with_a_new_pause_point(tmp_path, core):
    run = make_run(tmp_path, core, stop_after="G2")
    rec = orch(run, core, FakePaper(run)).run()
    assert rec.get("paused") == "after G2"
    runs.set_options(run, stop_after="grid")  # what `ama resume --stop-after grid` / resume_run do
    paper2 = FakePaper(run)
    rec = orch(run, core, paper2).run()
    assert rec.get("paused") == "after grid" and rec["completed"] is False
    assert paper2.calls[0] == "3" and paper2.calls[-1] == "4"
    assert read_json(run / "ama_options.json")["stop_after"] == "grid"
    paper3 = FakePaper(run)
    rec = orch(run, core, paper3).run()
    assert rec["completed"] is True and paper3.calls[0] == "3C"
    assert read_json(run / "ama_options.json")["stop_after"] is None  # the honoured pause point


class _BrokenSpecOnce(FakePaper):
    def do_3C(self, sfx, rnd, i):
        super().do_3C(sfx, rnd, i)
        if i == 0:
            (self.run / "companion_spec.json").write_text("{ not json")


def test_unreadable_companion_spec_is_retried_on_resume(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = _BrokenSpecOnce(run)
    rec = orch(run, core, paper).run()
    st = read_json(run / STATE_FILE)
    assert st["status"] == "error" and rec["stopped_at"] == "error"
    assert "companion_spec.json unreadable" in st["error"] and paper.calls[-1] == "3C"
    assert not any("companion spec" in k for k in st["journal"])  # nothing recorded for the check
    paper2 = FakePaper(run)
    rec = orch(run, core, paper2).run()
    assert rec["completed"] is True, rec
    assert paper2.calls[0] == "3C" and "4" not in paper2.calls  # a fresh companion implementer


class _BuilderFixRemovesReproduction(FakePaper):
    def do_BF(self, sfx, rnd, i):
        super().do_BF(sfx, rnd, i)
        (self.run / "reproduction.md").unlink(missing_ok=True)


def test_failed_reproduction_archive_does_not_stop_the_run(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = _BuilderFixRemovesReproduction(run, repro=[0.8, 0.502], g0a=["ERRORS"])
    rec = orch(run, core, paper).run()
    assert rec["completed"] is True, rec
    assert rec["g0_path"] == "pass_after_fix_r1"
    assert paper.calls[5:9] == ["0", "G0A", "BF_g0_audit", "0_r2"]
    assert not (run / "reproduction_r1.md").exists()
    assert "archive repro r1: cp failed" in (run / "orchestrator.log").read_text()


def test_fix_round_session_error_waits_for_the_grid(tmp_path, core):
    run = make_run(tmp_path, core)
    paper = FakePaper(run, tallies=[(1, 0, 0), (0, 0, 0)])
    seen = {"n": 0}

    def handler(role, prompt, workdir, log_path):
        if role == "3F" and seen["n"] == 0:
            seen["n"] += 1
            raise OSError("could not start the agent CLI")
        return paper(role, prompt, workdir, log_path)

    o = Orchestrator(run, runner=FakeRunner(handler), core_dir=core, sleep=lambda s: None, echo=False,
                     options={"poll_interval_s": 0, "max_wait_polls": 2})
    rec = o.run()
    assert rec["completed"] is True, rec
    i = paper.calls.index("4")
    assert paper.calls[i:i + 3] == ["4", "3F", "4_r2"]  # the grid wait, then a fresh 3F records the fixes
    st = read_json(run / STATE_FILE)
    failed = [k for k, v in st["journal"].items() if "role:3F#1" in k and v.get("failed")]
    assert failed and st["inflight"] is None
    assert "3F r1: could not start the agent CLI -- waiting for the grid" in (run / "orchestrator.log").read_text()


def test_missing_grid_file_is_waited_for(tmp_path, core):
    if not Path("/proc/self/cwd").exists():
        pytest.skip("needs /proc")
    run = make_run(tmp_path, core)
    o = orch(run, core, FakePaper(run), grid_dead_s=1800)
    f = run / "grid_results.csv"
    assert not f.exists()
    assert o._abandoned(f, "grid") is False  # no reference time: never cut short
    assert o._abandoned(f, "grid", since=time.time() - 60) is False  # the wait just started
    assert o._abandoned(f, "grid", since=time.time() - 4000) is True  # nothing written for > grid_dead_s


def test_grid_writer_named_on_its_command_line_counts_as_running(tmp_path, core):
    if not Path("/proc/self/cwd").exists():
        pytest.skip("needs /proc")
    run = make_run(tmp_path, core)
    o = orch(run, core, FakePaper(run), grid_dead_s=60)
    f = run / "grid_results.csv"
    f.write_text("cell_id\n")
    _old(f, 4000)
    assert o._abandoned(f, "grid") is True
    # a grid process started from outside the run directory, naming its script by path
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(run / "run_grid.py")],
                         cwd=str(tmp_path))
    try:
        time.sleep(0.5)
        assert o._process_in_run_dir() is True
        assert o._abandoned(f, "grid") is False
    finally:
        p.kill()
        p.wait()


def test_codex_sessions_get_the_background_note(tmp_path, core):
    from ama_mcp.orchestrator import KICKOFF_CODEX_NOTE
    for backend in ("codex", "fake"):
        run = make_run(tmp_path, core, run_id=f"note_{backend}", stop_after="package")
        fr = FakeRunner(FakePaper(run))
        fr.backend = backend
        Orchestrator(run, runner=fr, core_dir=core, sleep=lambda s: None, echo=False).run()
        calls = getattr(fr, "calls", None)
        if calls is None:
            pytest.skip("this FakeRunner does not record calls")
        assert all((KICKOFF_CODEX_NOTE in c["prompt"]) == (backend == "codex") for c in calls), backend


def test_mcp_servers_left_enabled_are_logged_once(tmp_path, core):
    from ama_mcp import runners
    if not hasattr(runners, "RoleResult"):
        pytest.skip("runners.RoleResult not available")
    run = make_run(tmp_path, core, stop_after="package")
    paper = FakePaper(run)

    def handler(role, prompt, workdir, log_path):
        code = paper(role, prompt, workdir, log_path)
        return runners.RoleResult(code, "done", str(log_path), 0.0, False, None,
                                  {"mcp_list_error": "`codex mcp list --json` exited 1"})

    Orchestrator(run, runner=FakeRunner(handler), core_dir=core, sleep=lambda s: None, echo=False).run()
    log = (run / "orchestrator.log").read_text()
    assert log.count("could not be listed") == 1


def test_orchestrator_entry_point_says_how_the_run_ended(tmp_path, core):
    run = make_run(tmp_path, core)
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1", "AMA_CORE_DIR": str(core)}
    p = subprocess.run([sys.executable, "-m", "ama_mcp.orchestrator", "run", "--run-dir", str(run)],
                       capture_output=True, text=True, env=env, timeout=600)
    # the fake backend's default agent writes nothing, so the package finder fails twice
    assert p.returncode == 1
    assert "paper1: error (RoleFailed: step P produced no package_status.json twice" in p.stderr
