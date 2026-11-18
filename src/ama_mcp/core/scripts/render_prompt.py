#!/usr/bin/env python3
"""Render a fixed AMA role prompt from a run's config.yaml.

Prompts are fixed text templates with {{PLACEHOLDER}} fields; this is the only
place a per-paper value is allowed to enter a prompt.  Every rendered prompt is
also written to runs/<id>/prompts_rendered/ so the run carries its own audit
trail of exactly what each agent was told.

    python <core>/scripts/render_prompt.py runs/<run_id> 1
    python <core>/scripts/render_prompt.py runs/<run_id> 4 --round 2

Every path is relative to this core directory or taken from the run itself
(run_policy.json, written by the orchestrator); nothing points at a particular
machine.  See docs/CHANGES_FROM_STUDY.md for how this differs from the study version.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

AMA = Path(__file__).resolve().parent.parent
PROMPTS = AMA / "prompts"

STEPS = {
    "P": "stepP_package_finder.md",
    "B": "stepB_package_builder.md",
    "C": "stepC_config_drafter.md",
    "CR": "stepCR_config_reviewer.md",
    "G0A": "stepG0A_reproduction_auditor.md",
    "BF": "stepBF_builder_fix.md",
    "SR": "stepSR_sample_reviewer.md",
    "3C": "step3C_companion_implementer.md",
    "4C": "step4C_companion_auditor.md",
    "3CF": "step3CF_companion_fix.md",
    "0": "step0_reproducer.md",
    "1": "step1_brainstormer.md",
    "2": "step2_fork_auditor.md",
    "3": "step3_implementer.md",
    "3F": "step3F_fix_round.md",
    "4": "step4_implementation_auditor.md",
    "5": "step5_author_commentary.md",
    "6": "step6_reporter.md",
}


def fmt_estimates(cfg: dict) -> str:
    out = []
    for e in cfg.get("headline_estimates", []) or []:
        bits = [f"  - [{e.get('id','?')}] {e.get('location','?')}",
                f"    quantity : {e.get('quantity','?')}",
                f"    value    : {e.get('value','?')}"]
        if e.get("std_error") is not None:
            bits.append(f"    std.err. : {e['std_error']}")
        if e.get("t_or_p") is not None:
            bits.append(f"    t or p   : {e['t_or_p']}")
        if e.get("n_obs") is not None:
            bits.append(f"    N        : {e['n_obs']}")
        bits.append(f"    target   : {e.get('target_type','?')}")
        out.append("\n".join(bits))
    return "\n".join(out) or "  (none recorded)"


def fmt_compute(cfg: dict) -> str:
    c = cfg.get("compute", {}) or {}
    rows = [f"  {k:12} {v}" for k, v in c.items()]
    return "\n".join(rows) or "  (none recorded)"


def fmt_conclusion(cfg: dict) -> str:
    p = cfg.get("published_conclusion", {}) or {}
    return (f"{p.get('statement','?')}\n"
            f"  direction    : {p.get('direction','?')}\n"
            f"  significance : {p.get('significance','?')}")


def repro_summary(run: Path) -> str:
    f = run / "reproduction.md"
    if not f.exists():
        return "(reproduction.md not written yet)"
    lines = f.read_text(encoding="utf-8").splitlines()
    verdict = next((l for l in lines if l.strip().startswith("GATE0")), "")
    head = "\n".join(lines[:40])
    return f"{head}\n...\n{verdict}"


def load_json(path: Path) -> dict:
    import json
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def fmt_anchor(run: Path) -> str:
    """Headline anchor written by the orchestrator (runs/<id>/headline_anchor.json)."""
    a = load_json(run / "headline_anchor.json")
    if not a:
        # Public release: the anchor is optional (the study wrote one per paper from an external
        # database).  Without it the drafter picks the headline itself, under the same one-target rules.
        return (
            "(none recorded for this run -- choose the headline yourself by the rules below)\n"
            "Binding rules for this run:\n"
            "- Record exactly ONE gating coefficient target, `h1`: the treatment\n"
            "  coefficient for the ONE outcome the paper presents as its main result\n"
            "  (by convention, the first column of its first main-results table).\n"
            "  Say in `notes` why this is the paper's main result.\n"
            "- Record N, R-squared and similar quantities inside h1 or as\n"
            "  `gating: false` targets; add no further coefficient targets.\n"
            "- `published_conclusion` is the paper's claim about h1.")
    rows = "\n".join(f"  - {o['outcome']}  --  {o['location']}"
                      for o in a.get("ia_outcomes", []))
    return (
        f"{a.get('source', 'External database')} records these outcomes for this\n"
        f"paper, located in the draft it used (dated {a.get('draft_used', '?')}):\n"
        f"{rows}\n"
        "Binding rules for this study:\n"
        "- Record exactly ONE gating coefficient target, `h1`: the treatment\n"
        "  coefficient for the ONE anchored outcome the paper presents as its main\n"
        "  result (by convention, the anchored outcome in its first main-results\n"
        "  table). Give h1 the extra field `ia_location:`, copied verbatim from\n"
        "  the list above.\n"
        "- `location` is where that same estimate sits in the paper version in\n"
        "  the package. If that version numbers tables differently from the\n"
        "  anchor, say so in `notes`.\n"
        "- Record N, R-squared and similar quantities inside h1 or as\n"
        "  `gating: false` targets; add no further coefficient targets.\n"
        "- `published_conclusion` is the paper's claim about h1."
        + (f"\nResolution by the study owner (binding; overrides the location\n"
           f"mapping above where they conflict):\n  {a['resolution']}" if a.get("resolution") else ""))


def anchor_review(run: Path) -> tuple[str, str]:
    """Config-reviewer text for the headline check: the study's anchor wording when
    runs/<id>/headline_anchor.json exists, else a check against the paper itself."""
    if load_json(run / "headline_anchor.json"):
        return (f"- `{run}/headline_anchor.json` — the outcomes and table locations an\n"
                "  external database records for this paper; the headline must be one of them",
                "Its\n"
                "   `ia_location` is one of the anchor locations, and among the anchored\n"
                "   outcomes it is the one the paper presents as its main result. If the\n"
                "   paper version here numbers tables differently from the anchor, `location`\n"
                "   must point at the same outcome's estimate in this version.")
    return ("- no `headline_anchor.json` was recorded for this run: the headline is the\n"
            "  drafter's own pick, which you check against the paper itself (check 1)",
            "No\n"
            "   external anchor was recorded for this run, so check the pick against the\n"
            "   paper itself: h1 is the treatment coefficient for the ONE outcome the paper\n"
            "   presents as its main result (its abstract, introduction and conclusion; by\n"
            "   convention the first column of its first main-results table).")


def data_sources(policy: dict) -> str:
    """Where the data sources this run may use are described (run_policy.json:
    data_sources_file, written by the orchestrator from the user's description)."""
    f = (policy or {}).get("data_sources_file")
    if f:
        return (f"read `{f}` first — the databases, local data copies and access notes "
                "declared for this run, and what they lack")
    return ("none declared for this run (no database access or local data was described; "
            "work from the package, the paper and public sources)")


def rebuilt(run: Path) -> bool:
    b = (load_json(run / "package_status.json").get("builder") or {})
    return bool(b) and str(b.get("mode", "")).startswith(("rebuilt", "authors_data_aug"))


def isolation_footer(run: Path, step: str, policy: dict) -> str:
    """Study-level isolation rules, appended to every prompt when the run
    carries an orchestrator policy file (runs/<id>/run_policy.json)."""
    if not policy:
        return ""
    lines = ["", "RUN ISOLATION -- orchestrator policy for this study, binding on this role",
             f"- Do not read any other run directory under {run.parent}/.",
             "- Several papers run at once on this machine. Never kill, signal or restart a process you",
             "  did not start in this session unless you have confirmed it belongs to THIS run: check",
             "  `lsof -a -d cwd -p <pid>` (its cwd must be this run directory) before acting. Python worker",
             "  processes of other papers look identical (run_grid.py, multiprocessing spawn_main).",
             "- Do not read any `void_*` or `_archive*` directory inside the run directory:",
             "  it holds the voided output of an earlier, invalidated attempt at a stage.",
             "- Do not read, search for, or rely on any third-party critique, comment,",
             "  reply, replication or reanalysis of this paper."]
    # An agent once edited its own output through a tool that publishes files to an external service.
    # Backend-neutral form of the study's rule (which named that tool): run outputs stay on this machine.
    lines += ["- Edit files with your file-editing tools (or a script). Never publish, upload or share run",
              "  outputs through any external service or tool: run outputs stay on this machine."]
    if policy.get("analysis_python"):
        lines += [f"- Analysis interpreter for this run: {policy['analysis_python']}. Run every Python script",
                  "  with it (it is `compute.python` in config.yaml); never use another Python for analysis."]
    forb = policy.get("forbidden_sources", [])
    # Generic data-role exception (user decision 2026-10-12): a third-party reassessment the DATA roles may
    # use for law dates and data details only; every other role is blind to it (it may serve as a benchmark).
    # (The study also hard-coded one such source for one study; the release keeps only this generic form.)
    DATA_ROLES = {"P", "B", "BF", "SR", "C", "CR", "0", "G0A"}
    for src in policy.get("data_role_sources", []) or []:
        if step in DATA_ROLES:
            lines += [f"- You MAY read {src['cite']}, for the law adoption dates and coding and other data details",
                      "  needed to build or reproduce this paper:"] + [f"    * {p}" for p in src.get("paths", [])] + [
                      "  Use it as a data source only: do not use, record or repeat its findings or its assessment of",
                      "  this or any other paper. Record in BUILD_LOG.md / your output whenever a date or coding comes",
                      "  from it rather than from the paper itself."]
        else:
            forb = forb + [f"{src['cite']} ({'; '.join(src.get('paths', []))}): a third-party reassessment of these papers"]
    # User decision 2026-10-07 (option A): authors' files licensed "only for verifying replicability"
    # (e.g. INFORMS Management Science terms) may be read by the reproduction roles only.
    rp = policy.get("restricted_package") or {}
    if rp.get("path"):
        if step in set(rp.get("allowed_roles", ["0", "G0A"])):
            lines += [f"- You MAY read the authors' replication files at {rp['path']} (read its TERMS.md first),",
                      "  ONLY to verify and diagnose this run's reproduction of the headline: e.g. run the authors'",
                      "  code on their data to confirm the published h1, and compare their sample, variable",
                      "  definitions and code with the rebuilt package to explain a gap. Report any difference as a",
                      "  finding. The reproduced h1 that the gate checks stays the rebuilt package's own estimate.",
                      "  Never copy their data or code into the package or the run directory, and never use them",
                      f"  for any other estimate. License: {rp.get('terms', 'replication checking only')}."]
        else:
            forb = forb + [f"{rp['path']} (the authors' replication files: licensed for reproduction checking only and "
                           "reserved for the reproducer and the reproduction auditor; build and analyze from the "
                           "study's own rebuilt data)"]
            if step == "P":
                lines += ["- The study owner already holds this paper's authors' replication files under terms that",
                          "  restrict them to reproduction checking (see the forbidden list). Do not download them",
                          "  again or place them in the package. finder.status describes what THIS run may use, and",
                          "  those files are not available to it: record `none_found`, and say in the evidence and in",
                          "  PACKAGE_README.md that a restricted authors' package exists and is held for the",
                          "  reproduction roles only. The builder then rebuilds the data from original sources."]
    # Public materials downloaded in advance (2026-10-13; e.g. when the machine running the agents cannot reach
    # publisher sites): the paper's own internet appendix / supplements and the authors' PUBLIC package. They
    # are part of the paper (not third-party sources).
    lm = policy.get("local_materials") or {}
    if lm.get("supplements") or lm.get("package"):
        lines += ["- Downloaded in advance for this paper (public sources; use these local copies, and still search",
                  "  for anything missing):"]
        lines += [f"    * {x.get('what', 'supplement')}: {x['path']}  (source: {x.get('source', '?')})" for x in lm.get("supplements", [])]
        if lm.get("package"):
            pk = lm["package"]
            lines += [f"    * the authors' public replication package: {pk['path']}  (source: {pk.get('source', '?')};"
                      f" {pk.get('contents', '')}; license: {pk.get('license', 'see the source page')})"]
            if step == "P":
                lines += ["  This is the authors' own public package: treat it as found (finder.status per its contents) and",
                          "  copy what the run needs into the package directory as usual, recording the source URL."]
    if forb:
        lines.append("- Do not read or search for any of these:")
        lines += [f"    * {f}" for f in forb]
    if rebuilt(run):
        lines += ["- The package is a THIRD-PARTY REBUILD from original sources, not the",
                  "  authors' own data (see package/BUILD_LOG.md). Design quantities",
                  "  (N, cluster counts) are not expected to match the paper exactly:",
                  "  report them beside the paper's values with the deviation; for this",
                  "  study they are informational at G0, and only the coefficient gates."]
        if step == "0":
            lines += ["- REBUILT-PACKAGE RULE: implement the configured design once, as the",
                      "  paper and config.yaml document it. If you find a genuine",
                      "  implementation error, fix it and log the fix with its reason. Never",
                      "  choose between defensible readings of the design by which one lands",
                      "  closer to the target. List every headline estimate you computed, in",
                      "  order, under a heading `Estimation log` in reproduction.md."]
    return "\n".join(lines) + "\n"


def build_context(run: Path, cfg: dict, args) -> dict:
    paper = cfg.get("paper", {}) or {}
    cite = (f"{paper.get('authors','?')} ({paper.get('year','?')}), "
            f"\"{paper.get('title','?')}\", {paper.get('venue','?')}")
    rnd = args.round
    policy = load_json(run / "run_policy.json")
    tol = float(policy.get("g0_tol", 0.15))
    review_read, review_check = anchor_review(run)
    return {
        "PAPER_PDF": policy.get("paper_pdf", "(see package/paper/)"),
        "HEADLINE_ANCHOR": fmt_anchor(run),
        "ANCHOR_REVIEW_READ": review_read,
        "ANCHOR_REVIEW_CHECK": review_check,
        "DATA_SOURCES": data_sources(policy),
        "G0_TOL_PCT": f"{tol * 100:g}",
        "GRID_WORKERS": str(policy.get("grid_workers", 1)),
        "GRID_MEM_GB": str(policy.get("grid_mem_gb",
                                      (cfg.get("compute", {}) or {}).get("mem_cap_gb", 16))),
        "RUN_DIR": str(run),
        "PKG_DIR": str(run / "package"),
        "AMA_DIR": str(AMA),
        "COMPUTE": fmt_compute(cfg),
        "SEED": str((cfg.get("compute", {}) or {}).get("seed", 20260923)),
        "MEM_CAP_GB": str((cfg.get("compute", {}) or {}).get("mem_cap_gb", 16)),
        "PAPER_HINT": args.paper_hint or cite,
        "PAPER_CITATION": cite,
        "RESEARCH_QUESTION": cfg.get("research_question", "?"),
        "PUBLISHED_CONCLUSION": fmt_conclusion(cfg),
        "DESIGN": cfg.get("design", "?"),
        "HEADLINE_ESTIMATES": fmt_estimates(cfg),
        "REPRO_SUMMARY": repro_summary(run),
        "REPRO_LANGUAGE": args.repro_language or policy.get("repro_language") or "Python",
        "GRID_EXT": args.grid_ext,
        "AUDIT_ROUND": str(rnd),
        "FIX_ROUND": str(rnd),
        "ROUND_SUFFIX": "" if rnd == 1 else f"_r{rnd}",
        "FINDINGS_FILE": args.findings or ("g0_audit" + ("" if rnd == 1 else f"_r{rnd}") + ".md"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("step", choices=list(STEPS))
    ap.add_argument("--round", type=int, default=1,
                    help="audit / fix round (steps 4 and 3F)")
    ap.add_argument("--repro-language", default=None,
                    help="language the reproducer must re-implement in "
                         "(default: run_policy.json repro_language, else Python)")
    ap.add_argument("--grid-ext", default="py", choices=["py", "R", "do"])
    ap.add_argument("--paper-hint", default="")
    ap.add_argument("--findings", default="",
                    help="findings file the builder fix round (BF) acts on; "
                         "default g0_audit[_rN].md")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    run = Path(args.run_dir).resolve()
    cfg_path = run / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    if not cfg and args.step not in ("P", "B", "SR", "BF", "C"):
        sys.exit(f"{cfg_path} missing -- run step C first")

    text = (PROMPTS / STEPS[args.step]).read_text(encoding="utf-8")
    ctx = build_context(run, cfg, args)
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", str(v))

    text += isolation_footer(run, args.step, load_json(run / "run_policy.json"))

    left = set(re.findall(r"\{\{([A-Z_]+)\}\}", text))
    if left:
        sys.exit(f"unfilled placeholders: {sorted(left)}")

    if not args.no_save:
        out = run / "prompts_rendered"
        out.mkdir(parents=True, exist_ok=True)
        suffix = "" if args.round == 1 else f"_r{args.round}"
        if args.findings:  # a fix round acting on a named findings file
            suffix = f"_{Path(args.findings).stem}"
        (out / f"step{args.step}{suffix}.md").write_text(text, encoding="utf-8")

    print(text)


if __name__ == "__main__":
    main()
