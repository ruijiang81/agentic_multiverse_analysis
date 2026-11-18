# Data sources available to every role -- TEMPLATE

Copy this file, fill it in for your machine, and give its path to the tool as
the run's data-sources description (`run_policy.json:data_sources_file`). Only
the package builder, its fix round, the sample reviewer and the reproduction
auditor (B, BF, SR, G0A) are pointed at this file; they are told to read it
**before concluding that a data source is unavailable**. If you give no file,
their prompts say that no extra data source was declared, and the agents work
from the paper, the replication package (if any) and public sources only.

Write it for an agent that has never seen your machine: exact paths, exact
schema/table names, and how to read them. Delete the sections you do not need.

**Never put a password, token or API key in this file.** Agents read it and
rendered prompts are saved inside the run directory. Use your tools' own
credential stores (`~/.pgpass` for WRDS, environment variables, keychains) and
say here only that they are configured.

---

## 1. Databases you can query live

For each one: how to connect, which account to use, what is licensed, and
what is known **not** to be licensed.

Example (WRDS):

- Connect with `wrds.Connection(wrds_username="<your WRDS username>")`; the
  password is in `~/.pgpass`. (Always pass the username explicitly: a
  non-interactive agent session cannot answer a prompt.) See `env.md` for the
  `raw_sql` workaround.
- Licensed: Compustat (North America, annual and quarterly), CRSP (stock,
  CCM link), I/B/E/S, ExecuComp, ... (list what your subscription covers).
- Known **not** licensed: e.g. `tr_sdc_ma` returns "permission denied".

## 2. Local copies of data

For each local dataset: location, format, coverage and snapshot date, and how
to look things up.

Example:

- **Location:** `/data/wrds_mirror/` (Parquet, mirrors the WRDS SAS tree)
- **Snapshot:** 2026-01-15
- **Catalog:** `/data/wrds_mirror/INVENTORY.md` lists every database; read it
  before concluding a source is missing.
- **How to read:** always column-select and filter, e.g. with `duckdb`:
  `SELECT gvkey, fyear, at FROM '/data/wrds_mirror/comp/funda.parquet' WHERE fyear BETWEEN 1976 AND 1995`,
  or `pyarrow.parquet.read_table(path, columns=[...], filters=[...])`.
- Never copy a whole large table into a package. Record the path and the
  snapshot date in the package's provenance table.
- Where a table is both live and local, say which to prefer.

## 3. Public or hand-collected datasets you already hold

Paths, source URL, vintage, coverage and merge keys of anything else the agents
may use (government statistics, law adoption dates, author-posted data, ...).

## 4. Known absent

Datasets you know you **cannot** provide (e.g. confidential Census
microdata, a database your institution does not license). Listing them saves
the agents a search and makes an infeasible paper fail fast and honestly.

---

When a required input is still unavailable, the package builder records it in
`package_status.json` under `builder.missing_sources` (format in `env.md`,
"Missing data sources"), so you can see exactly what a paper would need.
