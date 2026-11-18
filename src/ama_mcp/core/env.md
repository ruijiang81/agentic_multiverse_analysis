# Compute environment

Read by the agents (prompts point here as `<core>/env.md`). The per-run
`config.yaml:compute` block and the run's `run_policy.json` (written by the
orchestrator, never by an agent) hold the machine-specific values; this file
holds the rules and the library notes that apply on any machine.

## Interpreters

| Tool | Where it is named | Notes |
|---|---|---|
| Python 3.12 | `run_policy.json:analysis_python`, repeated in the RUN ISOLATION block of every prompt and recorded as `config.yaml:compute.python` by the config drafter | Use it for every analysis script. Never use a system `python3` that differs from it |
| Stata | `config.yaml:compute.stata`, when the user has one | Optional. Batch: `stata-mp -q -b do file.do` (or `stata-se`); see the note below |
| R | `config.yaml:compute.r`, when the user has one | Optional |

Packages the study's analysis environment had (install them into the
analysis interpreter before a run): pandas, numpy, scipy, statsmodels,
linearmodels, pyfixest (0.60 in the study), rdrobust, wildboottest, pyreadstat,
pyarrow, duckdb, matplotlib, pyyaml, and `wrds` if the user has WRDS access.

Stata batch mode always writes `<dofile>.log` **into the current working
directory**, and returns exit code 0 even when the do-file errors. Always grep
the log for `r(` error codes.

## Resource caps

- Grids run `grid_workers` single-threaded processes within `grid_mem_gb`
  total (both in `run_policy.json`). Respect them: several papers may run at
  once on the same machine.
- Grid scripts append one CSV row per finished cell.
- Designated reps run in full, unless the parallel projection exceeds 3 h.
  Then use 999 reps (user cap, 2026-09-28), never lower, with the rule
  documented in the script header.

## Reproducibility rules

- Every random procedure (bootstrap, randomization inference, permutation)
  takes its seed from `config.yaml:compute.seed`.
- Package directories are read-only to agents. Grid code reads from
  `package/` and writes only under the run directory.

## Verified library notes (save the implementer a debugging round)

- `pyfixest.feols("y ~ d | firm + yr", data, vcov={"CRV1": "firm"})` works; the
  fitted object exposes `.coef()`, `.se()`, `.pvalue()` and `.wildboottest(
  param=..., reps=..., seed=...)`.
- `wildboottest.wildboottest(...)` takes the **statsmodels model**, not the
  fitted results: pass `sm.OLS(y, X)`, not `sm.OLS(y, X).fit()`. Passing the
  results object raises `AttributeError: 'OLSResults' object has no attribute
  'exog'`.
- `statsmodels` two-way clustering: `fit(cov_type="cluster",
  cov_kwds={"groups": np.column_stack([g1, g2]), "use_correction": True})`.
- `pyreadstat.read_dta` reads every `.dta` in these packages, including
  Stata 13+ files; pass `metadataonly=True` for a fast schema peek and use
  `usecols=` to column-select on wide files.
- WRDS: `wrds.Connection(wrds_username=...)` -- **pass the username
  explicitly** (the data-sources file of the run says which one), or the call
  prompts for it and dies on EOF in a non-interactive agent session.
  Credentials belong in `~/.pgpass`, never in a prompt or a run file.
- `wrds.Connection.raw_sql` fails with pandas 3 + SQLAlchemy 1.4. Query
  through the DBAPI cursor instead:
  `raw = db.engine.raw_connection(); cur = raw.cursor(); cur.execute(sql);
  pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])`,
  with `raw.rollback()` after an error. (Found by a package builder.)
- Stata's `winsor2 x, by(g)` clips at the 1st and 99th within-group
  percentiles by default; the small residual against a pandas
  `groupby().transform(clip to quantile)` is a percentile tie-break
  convention, not an error.

## Worker threads (2026-09-30)

pyfixest's Rust core (`pyfixest/core/_core_impl`) runs a rayon thread pool with one
thread per core in every process. Its idle threads spin-wait (`sched_yield`) and
ignore OMP/MKL/OpenBLAS/vecLib/numba limits. With 11 grid workers that pushed the
load average to ~250 on 24 cores. Every parallel grid or companion script must set
`RAYON_NUM_THREADS=1` alongside the other thread variables before importing
pyfixest.

## Data sources

The data a run may use beyond the package and public sources are described
in the file named by `run_policy.json:data_sources_file` (the user writes it;
see `data_sources_TEMPLATE.md`). Each prompt that needs it points at that file,
or says that no extra data source was declared.

## Missing data sources

Before declaring a paper infeasible, check every declared data source and the
public web (government statistics, author-posted data).

If a required input is still unavailable, record it in `package_status.json`
under `builder.missing_sources` as a list of:

```json
{"source": "e.g. SDC Platinum M&A", "provider": "LSEG / Refinitiv", "tables_or_fields": "deal attitude (hostile), date announced, target CUSIP", "needed_for": "h1 outcome | control X | robustness table Y", "coverage_needed": "1976-1995, US targets", "checked": ["live WRDS: tr_sdc_ma permission denied", "local copy: absent"], "how_to_obtain": "institutional SDC Platinum licence / export from library terminal / request from authors"}
```

They stay in `package_status.json`, so the run shows exactly what the paper would need.
