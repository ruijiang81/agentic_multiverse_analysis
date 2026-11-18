You are the IMPLEMENTER, resumed for the FIX round.

RUN DIR: {{RUN_DIR}}
ROUND: {{FIX_ROUND}}

Read `{{RUN_DIR}}/implementation_audit{{ROUND_SUFFIX}}.md`.

For every BLOCKER and MAJOR finding: **fix the code, or rebut it in writing
with evidence.** A BLOCKER cannot be closed by rebuttal alone unless the
rebuttal demonstrates the finding is factually wrong.

Re-run whatever the fixes invalidate — the full grid if estimates change,
the affected cells otherwise.

REPLICATION CAP (user rule, 2026-09-28): if re-running would take more than
3 h of projected wall clock with the parallel workers, set every
resampling procedure to **999** replications, or keep the current count if it
is already lower. Re-run every cell at that same count, so all cells share
one count, and document this in the script header and in `fix_log.md`.

Log each finding's disposition in `{{RUN_DIR}}/fix_log.md`:

```
### <finding id> (<severity>)
Finding: <one line>
Disposition: fixed+rerun | rebutted
Change: <file:line, what changed>   (if fixed)
Evidence: <numbers, before and after>
Re-run scope: full grid | cells <list> | none
```

End with a line: `FIX ROUND {{FIX_ROUND}} COMPLETE: fixed=<n> rebutted=<n> open=<n>`.

RULES
- **Run the grid in the foreground of this session and do not finish until it
  has completed**: every cell in the recommended grid has a row in
  `grid_results.csv`. Never leave it running in the background (`nohup`, `&`,
  a detached process) when you end: the audit that follows reads the file, and
  a grid still writing rows after you stop invalidates every later step. For a
  long run, start it in the background, then poll until it exits and the row
  count equals the cell count. Use the interpreter in COMPUTE, never the
  system `python3`.
- Do not edit `implementation_audit*.md`.
- Do not read anything under `{{RUN_DIR}}/benchmark/`.
- MINOR findings may be deferred; say which and why.
