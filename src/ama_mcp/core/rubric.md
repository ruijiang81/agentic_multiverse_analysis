# Fork-validity rubric

The fork auditor scores **every** brainstormed option against the three
conditions of Simonsohn, Simmons and Nelson (2020), *Specification curve
analysis*, Nature Human Behaviour. Each verdict carries a written reason.
Judge the paper's own choices with exactly the severity applied to any other.

## S1 — Construct consistency

The option estimates the same theoretical quantity the paper's headline claim
is about.

- **VALID** if the option is a defensible operationalization of the same
  estimand (e.g. a different but standard accruals model for "earnings
  management").
- **QUESTIONABLE** if the construct match is arguable. Retain and flag.
- **DISTINCT ESTIMAND** if the option answers a different question (e.g.
  per-unit vs. aggregate outcome). Retain, label, and **exclude from the
  survival denominator**; report separately.

## S2 — Statistical validity

The option's estimator and inference are appropriate to the design. Rate
**INVALID as a primary specification** (may be retained as a labelled
diagnostic) when the option:

- clusters below the level at which treatment is assigned
  (Abadie, Athey, Imbens and Wooldridge 2023);
- conditions on a post-treatment variable, or selects the sample on one;
- uses an already-treated unit as a comparison under staggered adoption
  (Goodman-Bacon 2021; Callaway and Sant'Anna 2021);
- applies analytic cluster-robust inference with few clusters, where a wild
  cluster bootstrap is required (Cameron, Gelbach and Miller 2008);
- ignores a known assignment mechanism that permits randomization inference
  (Young 2019) when the design is an actual randomization;
- estimates a quantity the shipped data cannot identify (record the cell with
  an `err=` row rather than approximating it).

## S3 — Non-redundancy

Options that are algebraic relabelings of one another are merged into one,
with the merge recorded. Two options are redundant if they must produce the
same estimate up to a deterministic monotone transform.

## Grid selection

After scoring, the auditor selects the grid:

- cross the VALID (and QUESTIONABLE, flagged) options of the **4–8 most
  consequential dimensions**, to a total of **100–250 cells** per
  identification strategy;
- every dimension **not** crossed gets a `held_fixed` entry recording
  `{dimension, option_id, why}`;
- cap by auditing marginal informativeness, not by truncating a list.

## Inference designation (binding on step 3)

Before any estimate exists, the auditor designates in writing the admissible
inference method for headline p-values, and the rule is binding on the
implementer. Typical designations:

| Design feature | Designation |
|---|---|
| Treatment assigned at a level above the observation | cluster at the assignment level |
| Few clusters (< ~42) | wild cluster bootstrap, 999+ reps, seeded |
| Actual randomization with a known assignment rule | randomization inference over the real assignment |
| Staggered adoption with heterogeneous effects | heterogeneity-robust estimator (CS / did2s / Sun-Abraham) |
| Serially correlated panel outcomes | cluster on the unit, not on unit-time |

## Mandatory companion diagnostics

The auditor also flags diagnostics that must be reported **with every headline
cell**, not as optional extras — e.g. pre-trend/placebo tests, covariate
balance, post-period reversal, and the density/continuity checks an RD needs.
