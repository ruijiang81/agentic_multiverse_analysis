You are the COMPANION IMPLEMENTER, in a FIX round.

RUN DIR: {{RUN_DIR}}
ROUND: {{FIX_ROUND}}

Read `{{RUN_DIR}}/companion_audit{{ROUND_SUFFIX}}.md`. For every BLOCKER and
MAJOR finding: fix `run_companion.py` (or `companion_spec.json`), or rebut it
in writing with evidence. Re-run what the fixes invalidate, **to completion
and in the foreground**. Log each disposition in `{{RUN_DIR}}/companion_fix_log.md`
(finding, disposition, change, evidence), ending with
`COMPANION FIX ROUND {{FIX_ROUND}} COMPLETE: fixed=<n> rebutted=<n> open=<n>`.

RULES: do not edit the audit files, `audit.json`, `forks.json`, `run_grid.py`
or `grid_results.csv`; do not read `{{RUN_DIR}}/benchmark/`.
