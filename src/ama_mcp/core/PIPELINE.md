# AMA orchestration contract

Agentic Multiverse Analysis, as specified in the accompanying papers'
description of the pipeline. One run = one paper with a replication package.

> **Release note.** This is the contract as the study ran it, kept as close to
> verbatim as possible. In this package the orchestrator is
> `ama_mcp.orchestrator`, which launches every role as a fresh Claude Code or
> Codex session and runs the gate scripts itself. Where the package differs
> from the study, a note marked **[release]** says so; every change is listed
> in `docs/CHANGES_FROM_STUDY.md` of the repository. In dated notes such as
> "user rule, 2026-09-28", "the user" is the study's principal investigator and
> the date records when the rule was adopted.

## Roles, order, artifacts

The orchestrator (the interactive session) renders each role's fixed prompt
template from the run's `config.yaml`, launches the role as an **independent
subagent session**, and checks the gate artifacts between steps. Agents never
check their own work; the orchestrator never edits an agent's artifact.

| # | Role | Prompt | Reads | Writes | Gate after |
|---|---|---|---|---|---|
| C | Config drafter | `prompts/stepC_config_drafter.md` | `package/` | `config.yaml` | human review |
| 0 | Reproducer | `prompts/step0_reproducer.md` | `config.yaml`, `package/` | `reproduction.md`, `repro/` | **G0** |
| 1 | Fork brainstormer | `prompts/step1_brainstormer.md` | `config.yaml`, `package/`, `reproduction.md` | `forks.json` | — |
| 2 | Fork auditor | `prompts/step2_fork_auditor.md` | `forks.json`, `rubric.md`, `package/` | `audit.json` | **G2** |
| 3 | Implementer | `prompts/step3_implementer.md` | `audit.json`, `package/` | `run_grid.py`, `grid_results.csv` | — |
| 4 | Implementation auditor | `prompts/step4_implementation_auditor.md` | `run_grid.py`, `audit.json`, `package/` | `implementation_audit.md` | **G4** |
| 3F | Implementer (fix round) | `prompts/step3F_fix_round.md` | `implementation_audit.md` | `fix_log.md`, updated grid | loops to 4 |
| 5 | Author commentary | `prompts/step5_author_commentary.md` | `grid_survival.csv`, paper text, web | `author_commentary.md` | — |
| 6 | Reporter | `prompts/step6_reporter.md` | all verified artifacts (**[release]** and `paper_measures.json`) | `report.md`, `make_report.py`, figures | **G6** |

## Separation of duties (non-negotiable)

1. The brainstormer (1) never sees the auditor's verdicts; enumeration finishes
   before judging starts.
2. The auditor (2) is a different session from the brainstormer (1).
3. The implementation auditor (4) is a different session from the implementer
   (3) and does not edit files.
4. The fix round (3F) resumes the **implementer's** session, not the auditor's.
5. The inference method binding step 3 is designated in step 2, **before any
   estimate exists**.
6. No agent is shown a prior critique of the paper under audit. Where one
   exists in this repository it lives in the run's `benchmark/` directory,
   which is off-limits to every agent and is opened only by the orchestrator
   after G6, for the held-out comparison.

## Model assignment

Checking roles (1, 2, 4, 5) run on the frontier model; generating roles
(C, 0, 3, 6) run on the efficient model. Determinism lives in the seeded grid
script, not in the model.

**[release]** As the study actually ran: P, C, 0, 3, 3F and 6 on the efficient
model; every other role (including the package builder, whose errors every
later step inherits) on the frontier model.

## Run directory layout

```
runs/<run_id>/
  config.yaml              step C, human-reviewed before step 0 starts
  package/                 the replication package; agents must NOT modify it
  benchmark/               held-out prior critiques; NOT visible to agents
  repro/                   step 0 scratch + its own scripts
  reproduction.md          step 0
  forks.json               step 1
  audit.json               step 2
  run_grid.py              step 3
  grid_results.csv         step 3 (one row per cell, appended as it finishes)
  implementation_audit.md  step 4
  fix_log.md               step 3F
  grid_survival.csv        scripts/survival.py, from grid_results.csv
  author_commentary.md     step 5
  make_report.py           step 6
  report.md                step 6
  figures/                 step 6
  gates.log                orchestrator's gate decisions, append-only
```

## Artifacts are frozen at their gate

A gate PASS hashes the run's artifacts into `.frozen.json`. No agent may edit
an artifact after the gate that covered it has passed; if one does,
`scripts/freeze.py <run> --check` fails and the gate must be re-run before
anything downstream is trusted. An agent that finishes late and wants to
revise its output does not edit the file — it reports, and the orchestrator
decides whether to reopen the step.

## Gate ownership

Gate checking belongs to the orchestrator, not to the gated agent. An agent's
printed `GATE0: PASS` line is a convenience summary, never the decision. See
`gates.md`.

## Estimation discipline

Resampling procedures are executed, not derived. Every randomization draw and
bootstrap replication refits the model through the same path as the point
estimate; sufficient-statistic reductions, closed-form robust-variance
expressions and batched reformulations of the estimator are forbidden at
step 3 and are a BLOCKER at step 4. When the honest cost exceeds the budget,
reps come down deterministically and the rule is documented — the compute is
not bought back with algebra nobody will re-derive.

## Resource discipline

- Grid cells run in parallel worker processes (`run_policy.json`:
  `grid_workers`, `grid_mem_gb` total across workers; defaults 1 and 16),
  single-threaded each, with per-cell seeds derived from the run seed and the
  cell id so results do not depend on the worker count. Column-select on load.
- Grid scripts append one CSV row per finished cell so an interrupted run
  resumes without loss.
- The designated replication count is run in full. (The earlier 2-hour cap cut
  a designated 9,999-rep bootstrap to 999 on the BCL pilot; removed on the
  user's instruction, 2026-09-26.) Reps come down only if the parallel
  projection exceeds 3 h, and then to 999, the user's cap (2026-09-28); never
  below 999. The same count applies to every cell and is documented in the
  script header.

## Extension: papers without a shipped package (BCL study, 2026-09-26)

Most papers in the Reusing Natural Experiments database predate their
journal's data policy, so the pipeline gains three roles and two gates that
run **before** step C. The nine original roles and their prompts are
unchanged except where noted.

| # | Role | Prompt | Writes | Gate after |
|---|---|---|---|---|
| P | Package finder | `prompts/stepP_package_finder.md` | `package/paper/`, `package/authors_package/`, `package_status.json` | — |
| B | Package builder (only if P found no complete package) | `prompts/stepB_package_builder.md` | `package/{data,code}/`, `BUILD_LOG.md`, `data/VARIABLES.md` | **GP** (`scripts/gate_gp.py`) |
| C | Config drafter, now anchored | `prompts/stepC_config_drafter.md` (+ `{{HEADLINE_ANCHOR}}`) | `config.yaml` | — |
| CR | Config reviewer (independent of C) | `prompts/stepCR_config_reviewer.md` | `config_review.md` | **GC** (`scripts/gate_gc.py`) |

- **Headline anchor replaces the human config review.** The orchestrator
  writes `runs/<id>/headline_anchor.json` from Internet Appendix Table IA.II of
  Heath, Ringgenberg, Samadi and Werner (2023), which lists every recorded
  outcome of the paper with its table location. Step C must pick exactly one
  of them as the single gating target `h1`; GC checks the pick programmatically
  and requires the reviewer's `CONFIG_REVIEW: PASS`. One redraft is allowed.
  **[release]** The anchor is optional. Without it, step C picks `h1` under
  the same one-target rules (the outcome the paper presents as its main
  result), the reviewer checks the pick against the paper itself, and GC
  checks everything except the anchor location.
- **The builder may not estimate the treatment effect.** It calibrates only on
  sample sizes and descriptive statistics, so that G0 remains a real test of
  whether rebuilt data reproduce the paper.
- **Orchestrator policy file.** `runs/<id>/run_policy.json` (never written by an
  agent) sets the G0 tolerance (`g0_tol`; 0.10 for this study, per the user)
  and the forbidden sources. `render_prompt.py` appends a RUN ISOLATION block to
  every prompt when it exists: no reading other runs, no third-party critiques,
  and — for rebuilt packages — a step-0 rule against choosing between readings
  of the design by closeness to the target. Design quantities (N, clusters)
  are informational at G0 for rebuilt packages. **[release]** The policy file
  also names the analysis interpreter (`analysis_python`), the reproduction
  language (`repro_language`) and the user's description of the data they can
  access (`data_sources_file`), which replaces the study's own data catalog.
- **Held out.** Karpoff and Wittry (2018) sits in `benchmark/` for every BCL run
  but its own, exactly as BDLYY does for the Reg SHO runs.
- **Deviation from the contract.** The fix round (3F) is a fresh implementer
  session given the 3F prompt, not a resumed one; the workflow engine cannot
  resume a named session.
- Orchestration for the study: `bcl_study/setup_run.py` scaffolds a run from
  `bcl_study/manifest.json`; the multi-agent workflow renders each prompt,
  launches the role as a fresh agent that reads the rendered file verbatim,
  and has a mechanical executor run each gate script. **[release]** The
  package's orchestrator does the same with `scripts/new_run.py` and runs the
  gate scripts directly.

### When G0 fails (user policy, 2026-09-26)

A paper that misses the G0 tolerance goes to an independent **reproduction
auditor** (`prompts/stepG0A_reproduction_auditor.md`), which hunts for
implementation errors in the build and the reproduction. It must identify each
error by the paper's documented method or as an objective bug *before*
estimating its effect, and may never search for a specification that lands
closer to the target. `scripts/gate_g0a.py` reads its verdict:

- `CLEAN` → the paper continues to the multiverse with the gap recorded
  (config.yaml and reproduction.md frozen at G0);
- `ERRORS` → builder fix round (`prompts/stepBF_builder_fix.md`, package
  errors only), GP again, a fresh reproducer (which corrects reproduction
  errors the audit names), G0 again; at most two audit rounds;
- `UNRESOLVED` → stop.

### Sample fidelity (user directive, 2026-09-26)

Every run uses the samples the paper describes, as exactly as the data allow,
and records where it cannot.

- The builder must implement every documented sample rule and aim at every
  reported N (per table column, firms, treated/control), logging what it
  tried where a gap remains.
- An independent **sample reviewer** (`prompts/stepSR_sample_reviewer.md`)
  then extracts every sample description and reported N from the paper,
  checks each against the build, and writes `sample_reconciliation.md`
  (`sample item | paper | package | paper value | rebuilt value | exact? |
  why not | what was tried`). `scripts/gate_gs.py` reads its verdict:
  `BEST_ACHIEVABLE` → continue; `FIXABLE` → builder fix round
  (`stepBF` with `--findings sample_reconciliation.md`) and one re-review; items
  still fixable after two rounds are carried forward, recorded, not hidden.
- The reviewer may test whether a documented rule closes a gap in N; it may
  not run outcome-on-treatment regressions or pick rules by their effect.
- The implementer uses the paper's sample in every cell except along
  sample-varying dimensions, asserts the paper's-own cell reproduces the
  reproduction's N, and records `n_obs` per cell.
- The report carries a required **Sample fidelity** section (step 6, §1b).
- `MINOR` (added 2026-09-26): real
  implementation errors whose joint correction leaves the miss essentially
  unchanged. They are fixed (builder fix round + fresh reproducer) and the
  paper continues, following the user's rule to continue unless a wrong
  implementation may lead to the wrong match.

### Companion survival (user directive, 2026-09-27)

After G4, the **companion implementer** (`prompts/step3C_companion_implementer.md`)
re-runs the audited grid with the paper's own option **reinstated in every
dimension where the audit excluded it**. A held-fixed dimension is reset to
the paper's option; a crossed dimension gets the paper's option added. It then
takes the full cross-product, which contains the paper's own specification.
Undefined combinations become `err` rows. (v1, which restored held-fixed
dimensions only, was undefined whenever the audit had also dropped the
paper's option from a crossed dimension, e.g. the TWFE estimand. Voided
2026-09-27.)
Inference stays at the binding designation. It reuses `run_grid.py` and writes
`companion_spec.json` and `companion_results.csv`. An independent **companion
auditor** (`step4C`) and fix round (`step3CF`) loop up to 3 rounds.
`survival.py --companion` scores it into `grid_survival_companion*`, and the
report carries §4b next to the strict rates. **AMA's strict survival remains
the primary measure.** The companion shows what the held-fixed rulings cost
the paper. If nothing differs, `restored` is empty and the companion equals
the strict grid. **[release]** The audited (strict) grid remains the primary
basis, but the report now leads with the measures of *An AI-Powered Multiverse
Analysis of Empirical Finance Research* (share supported at p < 0.10 first),
which the orchestrator writes to `paper_measures.json` before step 6; strict
survival (p < 0.05) follows as the secondary measure.

### Karpoff–Wittry (2018) as a data source (user decision, 2026-09-27)

The data and reproduction roles (P, B, BF, SR, C, CR, 0, G0A) may read
Karpoff and Wittry (2018) and its internet appendix
(`papers/.../karpoff_wittry_2018_internet_appendix.pdf`) for law adoption
dates, coding and other data details, recording where a date comes from it.
The multiverse roles (1, 2, 3, 3F, 4, 3C, 4C, 5, 6) remain blind to it, so
that the held-out benchmark comparison of fork recall stays valid.
`render_prompt.py` applies this per step. A paper whose reproduction audit
had flagged the missing K–W dates was re-audited from G0 with the new access.
**[release]** The study hard-coded this source in `render_prompt.py`; the
package keeps only the generic form it later added for the same purpose:
list such a source under `run_policy.json:data_role_sources`
(`{cite, paths}`), and the data roles may read it while every other role is
told not to.

### Survival counts audited-grid cells only (2026-09-29)

`survival.py` scores only rows inside the audited recommended grid: every
crossed dimension's option must be one the audit lists, and `row_type`, when
present, must be `grid`. Rows outside it are excluded and counted in
`n_rows_off_grid_excluded`: reproduction anchors run with the paper's own
inference, mandatory diagnostics, and side-cells reported next to the grid.
One paper's only "surviving" cell had been such an anchor, run with analytic
SEs instead of the designated bootstrap. Its strict survival moved from 0.6%
to 0%.

### [release] Differences in this package

- The study's cluster rules (a Slurm block appended to every prompt when the
  run policy named one) are not shipped; grids run locally within
  `grid_workers` / `grid_mem_gb`.
- The study's data catalog (`data_sources.md`: its WRDS account and local
  WRDS mirror) is replaced by the user's own description of the data they can
  access (`data_sources_TEMPLATE.md`, `run_policy.json:data_sources_file`).
- The isolation rule that named one agent tool ("never use the Artifact
  tools") is backend-neutral: never publish run outputs through any external
  service or tool.
- `headline_anchor.json` is optional (above).
- The report and the per-run headline lead with the finance paper's measures
  (`paper_measures.json`, share supported first); strict survival is secondary
  (above).

See `docs/CHANGES_FROM_STUDY.md` (section Core) in the repository for the full list.
