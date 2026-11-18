# AMA core — Agentic Multiverse Analysis

A fixed multi-agent pipeline that measures how much of a published paper's
defensible specification universe agrees with its headline conclusion.

This directory is the pipeline itself: the role prompts, the gate scripts and
the documents the agents read. It is a port of the version used for the
accompanying papers' study, kept as close to verbatim as possible; every
change and the reason for it are listed in `docs/CHANGES_FROM_STUDY.md` of
the repository (section Core). In dated notes such as "user rule, 2026-09-28",
"the user" is the study's principal investigator and the date records when the
rule was adopted. The rest of the package (`ama_mcp.orchestrator`, the MCP server
and the CLI) only renders these prompts, launches each role as a fresh agent
session, and runs these scripts.

```
P find package -> B rebuild (if needed) -> SR sample review ── GS ── GP ──>
C draft config -> CR review config ── GC ──>
0 reproduce ── G0 ──> (miss: G0A reproduction audit -> BF fix -> 0 again)
  -> 1 brainstorm forks -> 2 validate forks ── G2 ──>
     3 implement grid -> 4 review implementation ── G4 ──> (3F fix, loop)
  -> 3C companion grid -> 4C companion audit (3CF fix, loop)
  -> survival -> 5 author commentary -> 6 report ── G6 ──> done
```

Three invariants make the output worth reading: **no agent checks its own
work**; **the fork space is judged before any estimate exists**; and **every
transition is gated on an artifact another agent or a program can check**.

## Layout

| Path | What |
|---|---|
| `PIPELINE.md` | The orchestration contract: roles, order, artifacts, separation of duties, and the extensions the study added (package finding and rebuilding, sample fidelity, reproduction audit, companion survival) |
| `rubric.md` | The fork-validity rubric (Simonsohn, Simmons and Nelson 2020: S1/S2/S3) |
| `gates.md` | G0/G2/G4/G6 pass conditions |
| `env.md` | Interpreters, resource caps, reproducibility rules, verified library notes, the `missing_sources` format |
| `data_sources_TEMPLATE.md` | How a user describes the data they can access (WRDS account, local copies, ...); the filled-in copy is the run's `data_sources_file` |
| `prompts/step*.md` | The 18 fixed role prompts, with `{{PLACEHOLDER}}` fields |
| `scripts/render_prompt.py` | Fills a prompt from a run's `config.yaml` and `run_policy.json`; saves what each agent was told |
| `scripts/new_run.py` | Scaffolds `runs/<id>/` |
| `scripts/gate_gp.py` | Package gate (programmatic) |
| `scripts/gate_gs.py` | Sample-fidelity gate (reads the sample reviewer's verdict) |
| `scripts/gate_gc.py` | Config gate (programmatic checks + the config reviewer's verdict) |
| `scripts/gate_g0.py` | Reproduction gate |
| `scripts/gate_g0a.py` | Reproduction-audit gate, after a G0 miss |
| `scripts/gate_g2.py` | Fork-coverage gate (fully programmatic) |
| `scripts/gate_g4.py` | Implementation gate: reads the auditor's tally, requires the fix loop to have closed |
| `scripts/freeze.py` | Hashes artifacts at each gate; `--check` detects a later edit |
| `scripts/gate_g6.py` | Report-integrity gate (re-runs the generator, diffs) |
| `scripts/survival.py` | The conclusion-survival metric and kill power (`--companion` for the companion grid) |
| `scripts/spec_curve.py` | Specification-curve and p-value figures (imported by a run's `make_report.py`) |

## Steps, prompts and gates

| Step | Role | Prompt | Writes | Gate after (exit codes) |
|---|---|---|---|---|
| P | Package finder | `stepP_package_finder.md` | `package/paper/`, `package/authors_package/`, `package_status.json` | — |
| B | Package builder (no complete authors' package) | `stepB_package_builder.md` | `package/{code,data}/`, `BUILD_LOG.md` | GP: 0 pass, 2 infeasible, else fail |
| SR | Sample reviewer | `stepSR_sample_reviewer.md` | `sample_reconciliation[_rN].md` | GS: 0 BEST_ACHIEVABLE, 3 FIXABLE, 1 fail |
| BF | Builder fix round | `stepBF_builder_fix.md` | `package/BUILD_LOG.md` fix section | GP again |
| C | Config drafter | `stepC_config_drafter.md` | `config.yaml` | — |
| CR | Config reviewer | `stepCR_config_reviewer.md` | `config_review.md` | GC: 0 pass |
| 0 | Reproducer | `step0_reproducer.md` | `reproduction.md`, `repro/` | G0: 0 pass, 1 fail, 2 infeasible |
| G0A | Reproduction auditor | `stepG0A_reproduction_auditor.md` | `g0_audit[_rN].md` | G0A: 0 CLEAN, 5 MINOR, 3 ERRORS, 4 UNRESOLVED, 1 fail |
| 1 | Fork brainstormer | `step1_brainstormer.md` | `forks.json` | — |
| 2 | Fork auditor | `step2_fork_auditor.md` | `audit.json` | G2: 0 pass |
| 3 | Implementer | `step3_implementer.md` | `run_grid.py`, `grid_results.csv` | — |
| 4 | Implementation auditor | `step4_implementation_auditor.md` | `implementation_audit[_rN].md` | G4: 0 pass |
| 3F | Implementer, fix round | `step3F_fix_round.md` | `fix_log.md`, updated grid | loops to 4 |
| 3C | Companion implementer | `step3C_companion_implementer.md` | `companion_spec.json`, `run_companion.py`, `companion_results.csv` | — |
| 4C | Companion auditor | `step4C_companion_auditor.md` | `companion_audit[_rN].md` | tally; `freeze.py --gate G4C --set` |
| 3CF | Companion fix round | `step3CF_companion_fix.md` | `companion_fix_log.md` | loops to 4C |
| 5 | Author commentary | `step5_author_commentary.md` | `author_commentary.md` | — |
| 6 | Reporter | `step6_reporter.md` | `make_report.py`, `report.md`, `figures/` | G6: 0 pass |

## The run policy

`runs/<id>/run_policy.json` is written by the orchestrator, never by an agent.
`render_prompt.py` and the gates read it:

| Key | Meaning |
|---|---|
| `g0_tol` | G0 relative tolerance (the study used 0.10; the gate's default is 0.15) |
| `grid_workers`, `grid_mem_gb` | Parallel grid workers and their total memory |
| `paper_pdf` | The paper PDF (`{{PAPER_PDF}}`) |
| `forbidden_sources` | Third-party critiques and other sources no role may read |
| `data_role_sources` | `[{cite, paths}]`: a third-party source the data/reproduction roles (P, B, BF, SR, C, CR, 0, G0A) may use for data details only; every other role is told not to read it |
| `restricted_package` | `{path, allowed_roles, terms}`: authors' files licensed for reproduction checking only (default roles 0 and G0A) |
| `local_materials` | `{supplements: [{path, what, source}], package: {path, source, contents, license}}`: public materials obtained in advance |
| `data_sources_file` | The user's description of the data they can access (`{{DATA_SOURCES}}`), or null |
| `analysis_python` | The interpreter agents use for analysis code (stated in every prompt) |
| `repro_language` | Language the reproducer re-implements in (`{{REPRO_LANGUAGE}}`; default Python) |

`headline_anchor.json` (optional): `{source, draft_used, ia_outcomes: [{outcome,
location}], resolution?}` — an external list of the paper's recorded outcomes;
when present, step C must pick `h1` from it and GC checks the pick.

## Running one paper by hand

The orchestrator does all of this; by hand it looks like this (`$CORE` is this
directory, `$PY` the analysis interpreter):

```bash
$PY $CORE/scripts/new_run.py my_paper --root runs
RUN=runs/my_paper
# put the paper in $RUN/package/paper/ and, if you have it, the replication
# package in $RUN/package/authors_package/; write $RUN/run_policy.json.
# Any prior critique of the paper goes in $RUN/benchmark/, which no agent may read.

# Each "render" writes $RUN/prompts_rendered/step<X>[_rN].md: hand that file to a
# FRESH agent session and wait for it to finish before running the next line.
$PY $CORE/scripts/render_prompt.py $RUN P
$PY $CORE/scripts/render_prompt.py $RUN B     # only if P found no complete package
$PY $CORE/scripts/render_prompt.py $RUN SR
$PY $CORE/scripts/gate_gs.py $RUN             # 3 FIXABLE -> render BF --findings sample_reconciliation.md, SR --round 2
$PY $CORE/scripts/gate_gp.py $RUN

$PY $CORE/scripts/render_prompt.py $RUN C
$PY $CORE/scripts/render_prompt.py $RUN CR
$PY $CORE/scripts/gate_gc.py $RUN

$PY $CORE/scripts/render_prompt.py $RUN 0
$PY $CORE/scripts/gate_g0.py $RUN
# G0 miss: render G0A, then gate_g0a.py; ERRORS/MINOR -> BF --findings g0_audit.md, GP, 0 --round 2, G0

$PY $CORE/scripts/render_prompt.py $RUN 1   # brainstorm
$PY $CORE/scripts/render_prompt.py $RUN 2   # validate
$PY $CORE/scripts/gate_g2.py $RUN

$PY $CORE/scripts/render_prompt.py $RUN 3   # implement + run grid
$PY $CORE/scripts/render_prompt.py $RUN 4   # independent implementation review
# findings -> render step 3F, then step 4 again with --round 2
$PY $CORE/scripts/gate_g4.py $RUN

$PY $CORE/scripts/render_prompt.py $RUN 3C  # companion grid, then 4C (3CF, --round N) until cleared
$PY $CORE/scripts/freeze.py $RUN --gate G4C --set

$PY $CORE/scripts/freeze.py $RUN --check    # before reading any result
$PY $CORE/scripts/survival.py $RUN
$PY $CORE/scripts/survival.py $RUN --companion
$PY -m ama_mcp.metrics $RUN --null-draws 0 --json --out $RUN/paper_measures.json   # [release] the report's headline
$PY $CORE/scripts/render_prompt.py $RUN 5   # author commentary
$PY $CORE/scripts/render_prompt.py $RUN 6   # report
$PY $CORE/scripts/gate_g6.py $RUN
```

Each rendered prompt is handed to an **independent agent session**, never
executed by the orchestrating session. The orchestrator renders, launches,
reads artifacts and decides gates; it does not do the analysis.

Agents run code from replication packages with broad permissions. Run the
pipeline inside a container or a virtual machine.

## What the metric means

**[release]** The report now leads with the per-paper measures of *An AI-Powered
Multiverse Analysis of Empirical Finance Research* (`paper_measures.json`, from
`ama_mcp.metrics`): the **share supported** (published sign and two-sided
`p < 0.10` under the designated inference), the median signed z, the share with
the published sign and whether the main estimate is reproduced. Strict survival,
below, is the survival rate of *When AI Analysts Disagree* and is reported second.

A specification **survives** iff its estimate carries the published sign and
`p < 0.05` under the inference the fork auditor designated before any estimate
existed. Reported per paper: the sign-consistency rate, the survival rate, and
each fork's **kill power** — the spread in survival across that fork's options.
A fork with high kill power is one a reader must accept on faith before the
conclusion follows.

The metric is descriptive. It does not correct for multiplicity, it counts
each specification once regardless of how closely it resembles its neighbours,
and it inherits the enumerated fork space: a specification nobody proposed
casts no vote.
