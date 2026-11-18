# Changes from the study version

This repository packages the agentic multiverse analysis (AMA) pipeline that was run for the
accompanying paper. The prompts, gate scripts and per-paper control flow are kept as close to
what the study ran as a public user's machine allows. This document lists every deviation and
the reason for it, one section per part of the package, followed by the fixes made after the
release review.

- [Core pipeline](#core-pipeline-prompts-gate-scripts-documents-srcama_mcpcore): the 18 role
  prompts, the gate scripts and the documents the agents read
- [Role sessions](#role-sessions-srcama_mcprunnerspy): how each role is launched as a Claude Code
  or Codex session
- [Orchestrator, runs, MCP server, CLI](#orchestrator-runs-mcp-server-cli-orchestratorpy-runspy-serverpy-clipy):
  the per-paper control flow, resuming, the server and the command line
- [Metrics](#metrics-srcama_mcpmetricspy): the per-paper measures the paper reports
- [Fixes after the release review](#fixes-after-the-release-review)

## Core pipeline: prompts, gate scripts, documents (`src/ama_mcp/core/`)

`src/ama_mcp/core/` is the AMA pipeline as the study ran it: the 18 role
prompts, the gate scripts, and the documents the agents read. The release rule
for this directory is to keep the prompts and the gate logic as close to
verbatim as possible, and to change only what a public user's machine needs:
paths, credentials, the study's compute cluster, and study-specific data
sources. This section lists every deviation from the study version and the
reason for it.

### Copied verbatim

- `rubric.md`, `gates.md`
- `scripts/_gatelog.py`, `scripts/spec_curve.py`
- 10 of the 18 prompts: `step0_reproducer.md`, `step1_brainstormer.md`,
  `step2_fork_auditor.md`, `step3C_companion_implementer.md`,
  `step3CF_companion_fix.md`, `step4_implementation_auditor.md`,
  `step4C_companion_auditor.md`, `step5_author_commentary.md`,
  `step6_reporter.md`, `stepC_config_drafter.md` (its anchor block is filled
  by `render_prompt.py`; see below)

The gate logic of `gate_g0.py`, `gate_g0a.py`, `gate_gp.py`, `gate_gs.py`,
`gate_g2.py`, `gate_g4.py`, `gate_g6.py`, `freeze.py` and `survival.py` is
unchanged: only docstrings and comments differ (listed below). Their command
lines, exit codes, printed verdict lines and `gates.log` lines are the
study's. Gate output texts that cite the study's policy dates (for example
G0A's "proceeding per user policy 2026-09-26") are kept, because they are part
of the logged verdict.

### Not shipped

| Study file | Why |
|---|---|
| `data_sources.md` | The study's own catalog: its WRDS account, its local WRDS mirror and their paths. Replaced by `data_sources_TEMPLATE.md` (below). |
| `BENCHMARK_PROTOCOL.md` | The study's held-out benchmark comparison, not part of running a paper. |
| `workflows/*.js` | The study's Claude Code workflow scripts. The per-paper control flow is ported in `ama_mcp/orchestrator.py` (orchestrator part). |
| `tools/slurm_run.sh`, `tools/merge_shards.py` (referenced by the compute footer) | The study's cluster. |

### Prompts

| Prompt | Change | Why |
|---|---|---|
| `stepP_package_finder.md` | Removed the two lines pointing at `{{CODEBASE}}/candidates/replication_packages.json` ("an earlier survey for some papers; treat it as a lead"). | That file was the study's own survey of its sample; a public user has no such file. A package the user already holds reaches the finder through the existing `local_materials` mechanism (rendered into the RUN ISOLATION block, with the P-specific "treat it as found" rule). |
| `stepB_package_builder.md` | `WRDS: see env.md (always pass wrds_username explicitly)` became `ENVIRONMENT: see env.md (on WRDS, always pass wrds_username explicitly)`. `DATA SOURCES: read data_sources.md first — live WRDS, a local 332 GB WRDS mirror ...` became `DATA SOURCES: {{DATA_SOURCES}}`. "Check live WRDS **and** the local mirror in `data_sources.md` first" became "Check every declared data source (DATA SOURCES above) **and** the public web first". The `missing_sources` comment points at `env.md`, "Missing data sources" instead of `data_sources.md §3`. | The study's WRDS account and mirror do not exist on a user's machine; the user describes their own sources. The "and the public web" clause carries over the rule of the study's `data_sources.md §3` ("check both sources above and the public web"), which no longer exists as a separate file; that section's `missing_sources` format moved to `env.md`. |
| `stepBF_builder_fix.md` | Same `ENVIRONMENT` and `DATA SOURCES: {{DATA_SOURCES}}` lines. | Same. |
| `stepSR_sample_reviewer.md` | "DATA SOURCES: data_sources.md — before calling a gap "source not available here", check both live WRDS and the local mirror it lists" became "DATA SOURCES: {{DATA_SOURCES}}." followed by "Before calling a gap "source not available here", check every declared data source and the public web." | Same. |
| `stepG0A_reproduction_auditor.md` | "Before calling anything "a source not available here", check both sources in data_sources.md (live WRDS and the local mirror)" became "DATA SOURCES: {{DATA_SOURCES}}." followed by "Before calling anything "a source not available here", check every data source declared for this run and the public web." | Same. |
| `stepCR_config_reviewer.md` | The READ bullet about `headline_anchor.json` and the body of check 1 became `{{ANCHOR_REVIEW_READ}}` and `{{ANCHOR_REVIEW_CHECK}}`. When the run has a `headline_anchor.json` they render to the study's text exactly. | The anchor is optional in the public tool (interface contract item 3). Without it, the reviewer is told that no anchor exists and checks the pick against the paper itself (its abstract, introduction and conclusion; by convention the first column of its first main-results table). |
| `step6_reporter.md` | READ starts with `paper_measures.json`. A new block, "THE PAPER'S MEASURES (the report leads with these)", tells the reporter to read the share supported, median signed z, share with the published sign, reproduced, the number of specifications, the companion share supported and the claim type from that file, not to recompute them, to report a null as not determined with its note, to use the null-claim reproduction for a null claim, and to report the strict rates only if the file is missing. "SURVIVAL ANALYSIS" became "STRICT SURVIVAL (secondary)". Section 4 (Robustness) gives the paper's measures first, then the strict survival rates (p < 0.05), the kill-power table and the specification curve; §4b adds the companion share supported. | The study's reports led with strict survival (p < 0.05), the measure of *When AI Analysts Disagree*. *An AI-Powered Multiverse Analysis of Empirical Finance Research* uses share supported (p < 0.10) as its main measure, and the tool's report now leads with it (Deviation 21). |
| `step3_implementer.md`, `step3F_fix_round.md` | "WITH the parallel workers on all shard nodes exceeds 3 h" became "WITH the parallel workers exceeds 3 h" (3F: "with the parallel workers, set every ..."). | "Shard nodes" referred to the study's cluster footer, which is not shipped. The 3-hour rule and the 999-replication cap are unchanged. |

"User rule, 2026-09-28" and similar dated provenance notes in the prompts are
kept: none of them names a person or a place.

### `scripts/render_prompt.py`

- **Paths.** The core directory (`{{AMA_DIR}}`) is the directory of the script's
  parent, as before; it now resolves inside the installed package. The
  isolation rule "Do not read any other run directory under X/" uses the run's
  own parent directory instead of `<codebase>/runs`. The `{{CODEBASE}}`
  placeholder is gone (its only use was the stepP line above).
- **Slurm compute footer dropped.** The study appended a "COMPUTE ON THIS
  SERVER" block to every prompt of a run whose `run_policy.json` had a `slurm`
  key: a shared 100 GB session limit, grids submitted as sharded Slurm jobs
  through `tools/slurm_run.sh`, `AMA_SHARD_INDEX`/`AMA_SHARD_COUNT` handling,
  preemption and requeue rules. It described one cluster. The function
  `compute_footer` is removed; a `slurm` key in a policy file is ignored. Grids
  run locally within `grid_workers` / `grid_mem_gb`, exactly as the step 3
  prompt already specifies (the study's pre-cluster behaviour).
- **Publishing rule made backend-neutral.** "Edit files with the Edit and Write
  tools (or a script). Never use the Artifact, ArtifactComments or ArtifactData
  tools: they publish to claude.ai, and run outputs stay on this machine."
  became "Edit files with your file-editing tools (or a script). Never publish,
  upload or share run outputs through any external service or tool: run
  outputs stay on this machine." The rule was added after an agent edited its
  own output through a publishing tool; the new wording keeps the rule and
  applies it to both Claude Code and Codex. The comment that named the run is
  rewritten generically.
- **Hard-coded Karpoff–Wittry block removed.** For one study, the data roles
  (P, B, BF, SR, C, CR, 0, G0A) were told they MAY read Karpoff and Wittry
  (2018) and its internet appendix at two machine paths, and the entry was
  dropped from their forbidden list. The study later generalised this into
  `run_policy.json:data_role_sources` (`[{cite, paths}]`), which is kept
  unchanged: data roles get the same "You MAY read ... as a data source only"
  text, every other role gets the source on its forbidden list. A user who
  wants that study's behaviour lists the source under `data_role_sources`
  rather than `forbidden_sources`. Consequence on the study's own run
  directories: for those runs, the data roles now see that source only on the
  forbidden list.
- **New placeholder `{{DATA_SOURCES}}`** (B, BF, SR, G0A), rendered from
  `run_policy.json:data_sources_file`: "read `<file>` first — the databases,
  local data copies and access notes declared for this run, and what they
  lack", or, when unset or null, "none declared for this run (no database
  access or local data was described; work from the package, the paper and
  public sources)". The file's path is rendered, not its content, as the study
  pointed at its catalog file.
- **New placeholders `{{ANCHOR_REVIEW_READ}}`, `{{ANCHOR_REVIEW_CHECK}}`** (CR),
  see the prompt table above.
- **No-anchor branch of `{{HEADLINE_ANCHOR}}`** (stepC). The study's text for a
  run without `headline_anchor.json` was a single line, "(none recorded --
  choose the headline by the rule below)"; every run of the multi-paper study
  had an anchor, and the two earliest runs without one used a human config
  review. The public tool makes the anchor optional, so the no-anchor text now
  carries the same one-target rules as the anchored text (exactly one gating
  coefficient `h1` for the outcome the paper presents as its main result, by
  convention the first column of its first main-results table; N and
  R-squared as `gating: false`; `published_conclusion` is the claim about h1),
  minus the `ia_location` field. GC's "exactly one gating target, h1" check
  therefore stays meaningful without an anchor. With an anchor the text is the
  study's.
- **Analysis interpreter.** When `run_policy.json:analysis_python` is set, the
  RUN ISOLATION block gains one line: "Analysis interpreter for this run: X.
  Run every Python script with it (it is `compute.python` in config.yaml);
  never use another Python for analysis." The study named its interpreter in
  `env.md` (a machine path), which is now generic; the line is absent when the
  key is absent, so study policy files render as before.
- **Reproduction language.** `--repro-language` now defaults to
  `run_policy.json:repro_language`, else "Python" (the study's default). The
  command-line flag still overrides.
- Comments that named people (the authors of a cited source), a run, or the
  study's machines are rewritten without them; dated decision notes that name
  no one are kept. The rules they explain are unchanged.

Every other part of the RUN ISOLATION block is the study's text: no other run
directories, the warning about other papers' processes, `void_*`/`_archive*`
directories, no third-party critiques, the restricted-package rules, the
pre-downloaded `local_materials`, the forbidden-source list, and the
rebuilt-package rules for step 0.

### `scripts/gate_gc.py`

`headline_anchor.json` is optional (interface contract item 3). With the file
present the gate is the study's, byte for byte in output and log. Without it,
the study's script crashed (FileNotFoundError); the port skips only the anchor
check (`h1.ia_location` must be an anchor location) and keeps every other check:
required fields, a positive/negative direction, exactly one gating target with
id `h1` and a value, and the reviewer's `CONFIG_REVIEW: PASS`. Its output says
"no headline_anchor.json: anchor check skipped" and its log line reads
"h1 at <location> (no anchor); reviewer PASS".

### Other scripts: docstrings and comments only

| File | Change |
|---|---|
| all gate scripts, `freeze.py`, `survival.py` | Usage examples `python ama/scripts/<x>.py runs/<a sample run>` became `python <core>/scripts/<x>.py runs/<run_id>`. |
| `gate_g0.py` | `see ama/gates.md` became `see <core>/gates.md`; the comment explaining the "unrounded" rule no longer names the run and its deviations. |
| `gate_g6.py` | The comment example of a prose `compute.python` no longer shows a home-directory path. |
| `survival.py` | The docstring's first line, "The conclusion-survival metric (ms.tex section 4.4)", now reads "Strict conclusion survival: the per-run report's secondary measure", names the two papers and points to `paper_measures.json` (Deviation 21); two comments naming the run that labelled its grid rows `cell` say "one run" instead. |
| `new_run.py` | `--root` defaults to `./runs` in the current directory instead of the `runs/` folder of the study's codebase (which would have been inside the installed package); the "next:" hint prints the real path of `render_prompt.py`. Scaffolding is unchanged. |

### Documents

- **`env.md`** rewritten for any machine. Kept: Stata's batch-mode log
  warning, the resource caps (`grid_workers`/`grid_mem_gb`, append one row per
  cell, the 3-hour/999-replication rule), the reproducibility rules, every
  verified library note (pyfixest, wildboottest, statsmodels two-way
  clustering, pyreadstat, the `wrds` `raw_sql` workaround, Stata `winsor2`),
  and the worker-thread section (`RAYON_NUM_THREADS=1`). Dropped: the study's
  machines (interpreter, Stata and R paths, cores, memory, the cluster and its
  job tool), its WRDS username and its account's licensing note, and the
  pointers to its local WRDS mirror. Added: where the analysis interpreter is
  named (`run_policy.json:analysis_python`), the list of packages the study's
  environment had, the "pass `wrds_username` explicitly" rule as a generic
  note, and the `missing_sources` format that used to live in
  `data_sources.md §3` (prompts now point there).
- **`env.md`, "Missing data sources"**: the study's closing sentence "The
  orchestrator compiles these into the study's data-requirements register"
  became "They stay in `package_status.json`, so the run shows exactly what the
  paper would need." The public orchestrator compiles no register.
- **`data_sources_TEMPLATE.md`** (new) replaces `data_sources.md`. It tells a
  user how to describe the data they can access — live databases (account,
  licensed and unlicensed products), local copies (paths, format, snapshot,
  how to read), other datasets they hold, and what is known to be absent — and
  warns never to put a password or token in it. The filled-in copy is the
  run's `data_sources_file`.
- **`data_sources_TEMPLATE.md`** says which roles are pointed at the file:
  only B, BF, SR and G0A (`{{DATA_SOURCES}}`), as `render_prompt.py` does.
- **`PIPELINE.md`** kept as the study's contract, with notes marked
  **[release]** where the package differs (orchestrator, optional anchor, new
  policy keys, generic `data_role_sources`, the actual model assignment) and a
  closing "Differences in this package" section. Two sentences that named a
  sample paper together with its reproduction outcome are rewritten without
  the name. The pointer to the study's LaTeX source files (`ms.tex`/`ec.tex`)
  became "the accompanying papers' description of the pipeline". The release
  note at the top (and the core `README.md`) adds that in dated notes such as
  "user rule, 2026-09-28", "the user" is the study's principal investigator and
  the date records when the rule was adopted. Three **[release]** notes (the step 6 row, the companion paragraph and
  "Differences in this package") say that the report leads with
  `paper_measures.json` and that strict survival is secondary (Deviation 21).
- **`README.md`** (core) updated: the study's README listed nine prompts and
  four gates; the port lists all 18 prompts, every gate with its exit codes,
  the run-policy keys, and a by-hand walk-through with generic paths instead
  of a sample run id and a home-directory interpreter. It states that agents
  run package code with broad permissions and that the pipeline belongs in a
  container or virtual machine.

### New files

- `core/data_sources_TEMPLATE.md` (above). `core/` stays a plain data
  directory (no `__init__.py`), shipped as package data; the scripts run as
  `python <core>/scripts/<name>.py`, as in the study.

### Policy keys

Read by `render_prompt.py` and the gates: `g0_tol`, `grid_workers`,
`grid_mem_gb`, `paper_pdf`, `forbidden_sources`, `data_role_sources`,
`restricted_package`, `local_materials` (all as in the study), and the new
`data_sources_file`, `analysis_python`, `repro_language`. Ignored: the study's
`slurm`, `grid_shards`, `shard_cpus`, `shard_mem_gb` and bookkeeping keys.

### How the port was checked

The study's scripts and the ported scripts were run side by side on two
identical scratch copies of the small text artifacts (config, reproduction,
audits, forks, grid results, reports; package directories mirrored as empty
files) of 20 real runs and 9 mutated copies of one run. The runs were chosen
from their `gates.log` to cover G0 pass, fail and infeasible; G0A CLEAN,
MINOR, ERRORS, UNRESOLVED and unparseable; GS pass and FIXABLE; GP pass, fail
and infeasible; GC pass and reviewer fail; G2, G4 and G6 pass and fail; the
companion grid; restricted packages, `data_role_sources`, `local_materials`
and cluster policy blocks; and a run without an anchor. The mutations forced a
truncated grid, a grid still being written, a tampered report, a dropped fork
verdict, a failing config review, a missing and a mismatched anchor, a tampered
freeze record and a tight G0 tolerance.

- 377 script invocations (pre-check `freeze --check`, GP, GS, GC, G0, G0A, G2,
  G4, `freeze --check`, `survival`, `survival --companion`, G6, and a final
  `freeze --check`, per copy): identical exit codes in 376, identical output
  (after normalising paths and timestamps) in 375; identical `gates.log`
  lines, `.frozen.json` hashes, survival outputs and regenerated reports. The
  differences are the two runs without `headline_anchor.json`, where the
  study's GC crashed and the port decides the gate without the anchor check
  (intended).
- Rendered prompts, 24 per run for every step including round-2 and
  findings-file variants: every difference is one of the intended changes
  above (the publishing rule, the dropped cluster footer, the hard-coded
  data-role source, the `DATA_SOURCES` lines, the stepP lead file, the
  shard-node wording, and the no-anchor C/CR text); 17 distinct difference
  hunks in all.

`tests/test_core.py` repeats these checks on a synthetic fixture run
(`tests/fixtures/core_run/`, every name and number invented): every step
renders with no placeholder left, each policy feature renders as specified,
and every gate reaches each of its exit codes. `tests/test_core_release.py`
checks that the dropped cluster and publishing rules are not shipped in
`core/`; `tests/test_release.py` scans the whole repository for machine paths,
e-mail addresses and tokens (and, when a maintainer points
`AMA_RELEASE_DENYLIST` at a private list kept outside the repository, for the
names on it).

## Role sessions (`src/ama_mcp/runners.py`)

This section lists every way the public tool launches a role differently from the study, and why.
The role prompts, their order and the gates are covered in the other sections.

### How the study ran a role

- Each role was an `agent()` call inside one long-running Claude Code workflow script (the
  per-paper `paper()` function). That made each role a fresh subagent with its own context.
- The orchestrator rendered the role's prompt to `prompts_rendered/step<step><sfx>.md` and gave
  the agent a short kickoff message pointing at that file.
- Models: `sonnet` for P, C, 0, 3, 3F, 6. Every other role ran on the workflow's default
  model, `opus`.
- Agents had the orchestrating session's permissions. Runs were unattended, so they never
  stopped to ask about a file edit or a shell command. They had Claude Code's shell, file and web
  tools (WebSearch, WebFetch).
- Gate scripts and file checks were run by a "mechanical executor" agent that ran the commands
  and reported exit codes. Per-role transcripts were kept in the workflow engine's own store.
- Roles had no time limit. When a grid was still being written after a role ended, the
  orchestrator waited by polling the grid.

### What the public tool does instead

| # | Study | Public tool | Why |
|---|---|---|---|
| 1 | Workflow subagent | A new CLI process for every role: `claude -p` (Claude Code) or `codex exec` (OpenAI Codex CLI). Each runs in `workdir` and gets the kickoff text on stdin. | The workflow engine is not available to the public. The user's own CLI login is. |
| 2 | Claude only | `backend` is `"claude"`, `"codex"` or `"fake"` (a Python stand-in used by tests) | The user asked for support for both Claude and Codex accounts. |
| 3 | `sonnet` / `opus` per role | Same table for Claude (`DEFAULT_MODELS`). Codex roles use the account's default model (no `-m`). | Same assignment as the study. The aliases resolve to the newest model of each family when the role runs, and the model the CLI reports is recorded in `RoleResult.meta["init"]["model"]`. Codex has no matching frontier/efficient pair, so its roles use the account default unless the user overrides it. |
| 4 | Permission prompts never shown | Claude: `--permission-mode bypassPermissions`. Codex: `--dangerously-bypass-approvals-and-sandbox`. | A role must edit files and run commands without asking, as in the study. Codex's own Linux sandbox needs bubblewrap. Where bubblewrap is missing, every shell command fails, which we saw on a test host. The README requires a container or VM anyway. Two opt-in alternatives exist: `CodexRunner(sandbox="workspace-write")` (Codex sandbox with `sandbox_workspace_write.network_access=true`), and `ClaudeRunner(permission_mode=...)` with any other mode plus `--allowedTools` for the shell, file and web tools. |
| 5 | (no MCP setup) | Claude: `--strict-mcp-config` with no `--mcp-config`. Codex: each enabled server from `codex mcp list --json` is turned off with `-c mcp_servers.<name>.enabled=false`. A name with characters other than letters, digits, `-` and `_` cannot be addressed this way and stays enabled (`RoleResult.meta["mcp_servers_not_disabled"]`); when the listing fails (cannot run, non-zero exit, no JSON) no server is turned off and `meta["mcp_list_error"]` says why. The orchestrator logs a warning once per invocation in either case. | A role must never load this MCP server again (that would recurse), and must not load the user's other servers. `-c mcp_servers={}` does **not** clear servers set in the user's Codex config (we checked); turning each one off by name does. |
| 6 | Subagents had no channel to each other except the run directory | Claude: `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. `--safe-mode` is added when the installed CLI lists it, which turns off the user's CLAUDE.md, hooks, skills, plugins and MCP. `--disallowedTools` blocks SendMessage, ListAgents, PushNotification, RemoteTrigger, Cron*, ScheduleWakeup, ShareOnboardingGuide, PublishPlugin and DesignSync. Codex: `features.memories=false`, `features.apps=false`, `features.plugins=false`; the user's `AGENTS.md` files still load (Codex's `project_doc_max_bytes` could switch project files off, but this was not verified live and is not used). | Keeps the separation of duties in PIPELINE.md. A standalone CLI session has channels a workflow subagent lacked. Auto-memory is keyed by the working directory, so every role of a run would share it. User instruction files and hooks also leak in, and so can messages to other local sessions. Each switch can be turned off (`safe_mode=False`, `disallowed_tools=()`, `extra_args`, `config_overrides`). |
| 7 | (ran inside the orchestrating session) | The child environment drops the parent's session variables: `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_SESSION*`, `CLAUDE_CODE_MESSAGING*`, `CLAUDE_EFFORT`, `CLAUDE_PID`, `CODEX_THREAD_ID`, `CODEX_SANDBOX*`, and similar. Credentials and provider settings are kept. | The MCP server itself may run inside Claude Code or Codex, so each role is a nested call. Without this, older Claude Code versions refuse to start, and the parent's effort level would carry over to the role without anyone noticing. |
| 8 | WebSearch / WebFetch | Claude: unchanged (in Claude Code 2.1.325, WebFetch is a deferred tool loaded through ToolSearch). Codex: `-c web_search="live"`. Its shell has network access because no sandbox is used. | The package finder (P) and the author-commentary role (5) need the web, and the data roles may need a database login. `exec` in Codex CLI 0.152.1 has no `--search` flag. The top-level `web_search` setting (`live`/`cached`/`indexed`/`disabled`) is what turns the tool on. |
| 9 | No per-role time limit | Optional `timeout_s`. When it expires, the session's process group and every process descended from it get SIGTERM, then SIGKILL after a grace period (default 10 s), and the result has `timed_out=True`. This includes processes that left the group with `setsid`. | The user's machine has no workflow engine watching the roles. The default is still no limit (`None`), as in the study. A session that ends by itself is not cleaned up after (see 12). |
| 10 | Transcripts in the engine's store | The JSON-lines transcript goes to `log_path` (Claude `stream-json`, Codex `--json`), stderr to `<log>.stderr`, and Codex's final message to `<log>.last_message.txt`. A file already at the same path is renamed `.1`, `.2`, … rather than overwritten. | Keeps a lasting audit record for each role, including retries. |
| 11 | The executor agent's report | `RoleResult(exit_code, last_message, log_path, duration_s, timed_out)` as the interface contract requires, plus two optional fields: `error` (one of `ERROR_CODES`: `not_installed`, `timeout`, `rate_limited`, `auth`, `refused_root`, `no_result`, `agent_error`) and `meta` (session or thread id, model, usage, cost when the CLI reports it, tool and command counts). | The orchestrator can tell a usage limit or a missing login apart from a role that failed. `meta` records the model and usage for each run. |
| 12 | Background processes left by a role were waited on by the orchestrator | Unchanged for Claude: background processes outlive the session. Codex itself kills them; see below. | The study prompts (3, 3F) say not to end a role with the grid still running, and the orchestrator also waits on the grid. The runner keeps this behaviour. |
| 13 | Fix round (3F) as a fresh session | Same | The runner cannot resume a named session either. This is the study's own documented deviation, unchanged. |

### Live check (2026-11-15; Claude Code 2.1.325, Codex CLI 0.152.1)

These are the results from a scratch directory, using the kickoff style the orchestrator uses ("Your
complete instructions are in this file: …"). Claude ran on `haiku`. Codex ran on the account
default model with `model_reasoning_effort="low"`. The Claude call was nested inside a Claude Code
session.

- **Main task.** Both backends exited 0 and wrote `out.txt` = `OK`, `calc.txt` = `42` and
  `web.txt` = `Example Domain`, with a sensible `last_message`. Claude reported no MCP servers in
  its init event. Neither CLI wrote anything to stderr. Both fetched the page with `curl`.
- **Built-in web search.** Claude called WebSearch, and Codex logged a `web_search` item. Both
  found the right URL.
- **Background processes.** Claude: a job started with `nohup … &` kept running into the next
  command and after the session exited. Codex: the job was killed as soon as the command that
  started it returned. This affects the step 3 / 3F / 3C prompts, which say "for a long run,
  start it in the background, then poll". Under Codex that kills the grid. Codex lets a long
  foreground command run and polls it itself, so a Codex role should run the grid in the
  foreground. The runner does not change it; the orchestrator adds a note to the kickoff of
  Codex sessions (Orchestrator section, deviation 6).
- **Timeout** (`timeout_s=85`, with the role inside a 300 s foreground command). Both returned
  `timed_out=True` and `error="timeout"`. Exit codes were 143 for Claude (it handled SIGTERM) and
  -15 for Codex. No process from either session survived.
- **No flags had to be changed after the first live run.** One addition came after it:
  `--disallowedTools`. The first run's init event listed SendMessage, ListAgents,
  PushNotification and similar tools in the role session.

### Notes for users

- Run the tool in a container or VM. As root, Claude Code refuses `bypassPermissions` unless it
  detects a container (`/.dockerenv`) or `IS_SANDBOX=1` is set. The runner does not set it;
  `check_backends()` mentions it when it runs as root.
- `check_backends()` reads `claude auth status --json` and `codex login status`. It returns only
  the auth method, provider and plan type. E-mail addresses, organisation names and ids, and keys
  are never returned (`redact()`).

## Orchestrator, runs, MCP server, CLI (`orchestrator.py`, `runs.py`, `server.py`, `cli.py`)

This section lists every way the public orchestrator differs from the one the study ran, and why.
The prompts and gate scripts are covered in the Core section, the agent sessions in Role
sessions, the measures in Metrics.

### What the study ran

The study drove each paper with a Claude Code workflow script. Its per-paper function ran the
stages Package, Config, Reproduce, Forks, Grid, Companion and Report. For each role it rendered
the role's prompt with `render_prompt.py`, launched a fresh subagent with a short kickoff
message that pointed at the rendered file, and checked that the role left its artifact behind.
Gate scripts, file checks, file moves and waits were run by a "mechanical executor" agent that
executed shell commands and reported their exit codes and the last 3000 characters of output.
Several papers ran in parallel inside one workflow.

### Control flow: unchanged

`Orchestrator` ports that per-paper function branch by branch:

- **Package**: P; B unless `finder.status` is `authors_data_and_code`; a builder that declares
  the paper infeasible goes to GP and the run stops; the sample review (SR, GS) runs up to two
  rounds, with a builder fix round (BF) on `FIXABLE` (none for a complete authors' package);
  items still fixable are recorded and the run continues; GP (exit 2 = infeasible).
- **Config**: C, the archive of `config_review.md` to `config_review_r<n>.md`, CR, GC; two
  attempts.
- **Reproduce**: 0, G0; one fresh reproducer (round 2) on a procedural failure only ("no
  matching row" / "reproduction.md missing"); G0 exit 2 stops the run as infeasible; on a miss,
  the G0A loop for k = 1, 2: CLEAN continues; MINOR or ERRORS run BF on `g0_audit[_rk].md`, GP,
  the archive of `reproduction.md` to `reproduction_r<k>.md` (the study's `cp ... && echo
  archived`, whose result was not checked: a failed copy is logged and the run goes on), a fresh
  reproducer (round k+1) and G0; a pass continues; MINOR continues; ERRORS at k = 2 and
  UNRESOLVED stop.
- **Forks**: 1, 2, G2; one more fork-auditor session and G2 on failure; optional pause after G2.
- **Grid**: 3; the completeness wait (distinct `cell_id` count at least
  `audit.json recommended_grid.n_cells` and the file untouched for 120 s, a check about every
  10 minutes, up to 70 checks); one more implementer session if still incomplete; the audit loop
  (4, TALLY, break when blockers = 0 and (round > 1 or major = 0), else 3F with its wait and
  "record the fixes" session, up to round 4); G4. As in the study, any error of the first 3F
  session (not only a missing `fix_log.md`) is logged and the orchestrator goes on to the grid
  wait; the failure is recorded, so a resume replays it instead of launching that session again.
- **Companion**: 3C; the restored count from `companion_spec.json`; none restored ends the
  stage; the completeness wait (rows at least `n_cells`, up to 40 checks); 4C and 3CF for up to
  three rounds; `freeze.py --gate G4C --set` when the audit clears. The stage never stops a run.
- **Report**: `freeze.py --check`, `survival.py`, `survival.py --companion` when choices were
  restored and the companion grid is complete; 5; 6; G6 with one more reporter session.

The artifact guard (`test -s` on the role's expected file, one retry, then failure), the
expected-file table, the rendered-prompt file names, the gate exit-code handling, the TALLY
regular expressions and the keys of the run record (`rec`: `gates`, `finder_status`, `sample`,
`g0_path`, `tallies`, `companion`, `companion_restored`, `freeze`, `survival_stdout`,
`companion_survival`, `survival_headline`, `stopped_at`, `reason`, `paused`, `completed`) are
the study's. Three values differ in form, with no effect on any decision: `tallies` is also
recorded when the run stops at G3 inside the audit loop (the study recorded it only before G4 or
on a G4 stop); `survival_headline` holds the parsed `grid_survival_headline.json` (the study kept
the raw output of `cat`, which the port keeps only when the file is not valid JSON); and an
unreadable `package_status.json` gives `finder_status: "unreadable: <error>"` (the study kept the
last line of the executor's output, a Python traceback line). Neither value equals
`authors_data_and_code`, so the builder runs in both.

### Deviations

| # | Study | Public tool | Why |
|---|---|---|---|
| 1 | A JavaScript workflow script in the Claude Code workflow engine | A Python module, `python -m ama_mcp.orchestrator run --run-dir RUN`, one process per paper | The workflow engine is not available to the public. Several papers run as several processes. |
| 2 | A "mechanical executor" agent ran every gate, file check, file move and wait | The orchestrator runs them directly as subprocesses or in Python: `python <CORE>/scripts/<name>.py <run> [args]`, stdout and stderr merged, the last 3000 characters kept | No agent is needed to run a fixed command. The decisions come from the same scripts with the same exit codes, without the cost and the failure modes of an agent relaying them (the study had to make every executor command change directory itself after two executors ran a check from the wrong directory). |
| 3 | Gates ran with the study's analysis interpreter | Gates and core scripts run with the orchestrator's own interpreter (the `ama-mcp` environment, which has their dependencies). `gate_g6.py` still runs `make_report.py` with `config.yaml compute.python` when that is a file | The user's analysis environment may lack PyYAML or pandas. |
| 4 | Waits were `python -c "time.sleep(570)"` run by the executor | `time.sleep(poll_interval_s)` in the orchestrator, default 570 s, and the same number of checks (70 for the grid, 40 for the companion grid) | Same timing without an agent. All waits are options in `ama_options.json`. |
| 5 | (none) | A wait ends early when the grid file is incomplete, untouched for `grid_dead_s` (default 1800 s) **and** no process other than the orchestrator and the processes above it (the MCP server, a shell or a `timeout` wrapper) works in the run directory: none has its working directory inside it and none names a path inside it on its command line (Linux `/proc` only; without `/proc` the wait is never cut short). A results file that does not exist yet counts as untouched since the start of the wait, so `grid_dead_s` always applies. Assumption: a grid that is still running is visible this way. A grid process that neither works in the run directory nor names it, or that runs in another PID namespace (another container), is not seen, and the wait may end while it runs. `grid_dead_s = 0` restores the study's behaviour | Codex kills background jobs when the command that started them returns (see Role sessions), so a grid an agent started with `&` dies with its session and the study's loop would wait about 11 hours for nothing. The decision after the wait is the study's (re-run the implementer once, or stop at G3); only the waiting is cut short. |
| 6 | Kickoff: "Read the whole file with the Read tool ...", "Run shell commands from" the study's codebase root | "Read the whole file ...", "Run shell commands from" the run directory. Everything else, including the paragraph telling the agent that a message from the person running the main session is not addressed to it, is verbatim. Codex sessions only: one more paragraph (`KICKOFF_CODEX_NOTE`) says that a background process is killed when the command that started it returns, and that where the instructions say to start a long run in the background and poll it, the agent should run it in the foreground and poll that command | Backend-neutral wording (Codex has no tool named Read). The study's codebase root does not exist on a user's machine; every prompt uses absolute paths and the session's working directory is the run directory. The Codex note: the grid prompts (3, 3F) say "for a long run, start it in the background, then poll", which under Codex kills the grid (Role sessions, live check). The prompts stay verbatim; Claude sessions get the study's kickoff unchanged. Not verified with a live grid run. |
| 7 | Models from the workflow's table (Sonnet for P, C, 0, 3, 3F, 6; the workflow's default, Opus, for every other role) | `runners.DEFAULT_MODELS[backend][step]`, overridden per step by the run's `models` option (`"*"` for every step) | Same assignment for Claude; Codex uses the account default (see Role sessions). |
| 8 | Resume by hand: a `start` mode per paper (`samplefix`, `config`, `g0reaudit`, `rerepro`, `g0audit`, `forks`, `forkaudit`, `audit`, `resumegrid`, `fixround`, `auditfrom`, `companion`, `companionaudit`, `report`, `commentary`, ...) chosen by the study team after looking at the run | Automatic resume. `orchestrator_state.json` records every finished role, every gate result, every file move and every check that steers the flow (finder status, builder feasibility, audit tallies, the restored count, the outcome of each completeness wait) the moment it happens. Running again replays these decisions without repeating work and continues at the first unfinished step; completed stages are skipped. `--from-stage STAGE` forgets a stage and the later ones so they run again with fresh agents | A public user cannot be expected to pick a restart mode. Replaying recorded decisions resumes the audit/fix loop at the right round, the G0A loop at the right k, and so on. The study's special modes that added rounds beyond the fixed loop limits (an extra reproduction round after a procedural error, a re-audit after a new data source appeared) were case-by-case decisions of the study team and are not part of the automatic flow; the paper reports how many runs received them. |
| 9 | A role interrupted by a crash was re-run by hand | On resume, an interrupted role is launched again with a fresh session. An interrupted implementer (step 3) whose grid is complete on disk (or still being written and then completes) is not launched again | Re-running a finished multi-hour grid wastes compute and could leave two grid processes writing one file. |
| 10 | A run that hit an exception was recorded as `stopped_at: "error"` and re-run by hand | Same record, plus `status: "error"` in the state file; the failed role is not recorded as done, so `ama resume` retries it. An unreadable `companion_spec.json` (the study threw here) is such an error: nothing is recorded for the check and the companion implementer's session is forgotten, so a resume launches a fresh one, as the study team's restart from the companion stage did. A gate's stop (`status: "stopped"`) is final unless `--from-stage` is given | Errors (a missing login, a full disk, a role that left no artifact twice) are retryable; gate decisions are results. |
| 11 | Usage limits were handled outside the workflow | A session whose runner reports `error == "rate_limited"` is relaunched after `rate_limit_wait_s` (900 s), up to `rate_limit_max_waits` (24) times per attempt; these relaunches do not count as the role's retry | Subscription plans hit usage limits during long runs; without this, the run would fail and need a manual resume. |
| 12 | Transcripts lived in the workflow engine | `RUN/agent_logs/NNN_step<step><sfx>[_retry].log` (NNN orders the sessions); every session's step, model, exit code, error, duration and the tail of its last message are in `orchestrator_state.json` `roles` | A lasting record per role on the user's machine. |
| 13 | `rec` was returned by the workflow | `rec` is kept in `orchestrator_state.json` while running and written to `RUN/ama_result.json` with the survival headline(s), `metrics.compute_paper_metrics(run)` (its failure is tolerated and recorded), the models and the session count. Written on completion **and** when a gate stops the run | The MCP server and CLI read results from the run directory. A stopped run's measures (e.g. the reproduction outcome) are still useful. |
| 14 | The workflow's `log()` | `RUN/orchestrator.log` | Same purpose, on disk. |
| 15 | `stop_after: "G2"` held the pilot papers after G2 | `stop_after` is `"G2"` (recorded as `paused: "after G2"`, as in the study) or the name of a stage; `ama resume` continues past the pause. A pause point set before the resume (`ama resume --stop-after`, `resume_run(stop_after=...)`) that names a stage not yet done pauses the run again after that stage; a pause point whose stage is done (the one just honoured) is cleared from `ama_options.json` | Lets a user inspect the audited choice space, or any stage, before the next one runs. |
| 16 | `render_prompt.py` was never given `--paper-hint` | When the user gives a citation, every render gets `--paper-hint "<citation>"` (it fills `{{PAPER_HINT}}`, which only the config drafter's prompt uses) | The study's config drafter identified the paper from the PDF alone; a user who knows the citation can say so. Without a citation the prompt is rendered exactly as in the study. |
| 17 | Per-study setup scripts wrote `run_policy.json`, `headline_anchor.json` (from an external database) and held-out benchmarks, and added a cluster compute block | `runs.create_run` writes `run_policy.json` from the user's inputs (contract item 2), copies the PDF to `package/paper/`, a user package to `package/authors_package/` (announced to the prompts through `local_materials.package`, the mechanism the study used for packages downloaded in advance), supplements to `inputs/` (`local_materials.supplements`), the data sources file to `data_sources.md`, and an optional headline anchor; no cluster block, no benchmark files | The public tool has no study manifest, no external database and no cluster. |
| 18 | The workflow ran in the foreground of the study's session | `runs.start_background` starts the orchestrator as a detached process group (pid in `RUN/orchestrator.pid`); `runs.stop` signals it only after checking that the process's command line is this run's orchestrator, and signals the group only when the process leads its own group; a file lock (`RUN/.orchestrator.lock`) prevents two orchestrators on one run | The MCP server returns at once while the analysis runs for hours, and the user can stop it safely. |
| 19 | Subagents shared the study session's file access | Each session's working directory is the run directory; the user's `data_dirs`, the paths of `data_role_sources` and a `restricted_package` are passed to the CLI as added directories. The core directory is not: agents read it by absolute path | In a sandboxed CLI mode an added directory is writable; agents must not be able to edit the prompts or gate scripts. |
| 20 | The per-command executor timeout was 10 minutes | Gates and core scripts get `gate_timeout_s` (default 3900 s); role sessions have no time limit unless `role_timeout_s` is set | G6 re-runs the report generator, which the gate itself allows an hour; no gate should be cut short. The study set no limit on roles. |
| 21 | The report and the per-run headline led with strict survival (`survival.py`: published sign and p < 0.05), the measure of *When AI Analysts Disagree* | After the grid is scored (and the companion grid, where there is one), the orchestrator writes `RUN/paper_measures.json`: the headline measures of *An AI-Powered Multiverse Analysis of Empirical Finance Research* from `metrics.compute_paper_metrics(run, null_draws=0)` (share supported at p < 0.10, median signed z, share with the published sign, reproduced, the number of specifications, the companion share supported, the claim type and the null-claim reproduction). It is written again before each G6, so an agent's edit of it cannot pass G6; the companion value is withheld when the companion grid is incomplete and flagged when its auditor did not clear it. The reporter reads it and leads with it; strict survival follows as the secondary measure. `ama status`, `run_status`, `ama results` and `run_results` show the same headline first. A failure to compute is written into the file (`error`) and logged; it does not stop the run | Both papers use the pipeline; the finance paper's measures are its main results, so the tool's headline matches them. Strict survival is kept, unchanged, as the secondary measure. The values are deterministic (no null draws), so the report does not depend on a random seed |

### Interfaces added beyond the contract

- `runs.create_run` also takes `data_dirs`, `supplements`, `restricted_package`,
  `repro_language`, `role_timeout_s` and `core_dir`; `runs` also provides `resolve_run`,
  `set_options`, `results`, `read_run_file`, `running_pid`, `is_running`, `run_foreground`.
- `RUN/ama_options.json` holds the orchestrator options (backend, models, stop_after,
  citation, waits, timeouts, data_dirs, runner_options); `run_policy.json` stays the file the
  prompts and gates read.
- Environment variables: `AMA_RUNS_ROOT` (runs directory, default `~/ama-runs`),
  `AMA_CORE_DIR` (use another copy of the core directory), `AMA_MCP_ALLOW_FAKE=1` (lets the
  server and CLI accept the test backend `fake`).
- `read_run_file` (MCP) never reads `benchmark/` and returns text files only, capped at 1 MB.
- `ama-mcp --help` prints how to register the server; `ama-mcp --version` prints the version;
  other arguments are refused; on a terminal the server says on stderr that it waits for an MCP
  client.
- `ama run --foreground` and `ama resume --foreground` print how the run ended (status, the
  gate that stopped it or the error, the log path). `python -m ama_mcp.orchestrator run`
  prints the same line to stderr when its log lines were not shown (stderr not a terminal).
- The orchestrator logs a warning when a Codex session may have kept MCP servers enabled
  (Role sessions, row 5).

## Metrics (`src/ama_mcp/metrics.py`)

`metrics.py` is new. The study computed these measures in its cross-paper analysis code, which also reads files
that sit outside the run directories. That code is not released. This module recomputes, from a single run
directory, the per-paper measures that the paper reports. Statistics across papers are out of scope: means,
regressions, multiple-testing corrections, split-half reliability and the coder panels.

### Interface

```python
from ama_mcp.metrics import compute_paper_metrics, write_paper_metrics
m = compute_paper_metrics(run_dir)          # dict: measure key -> entry
write_paper_metrics(run_dir)                # writes <run_dir>/paper_metrics.json
```

```
python -m ama_mcp.metrics <run_dir> [--json] [--inputs FILE] [--out FILE] [--null-draws N]
```

- Each entry is `{"value", "paper_name", "definition", "group"}`. Some entries also carry a `"note"`, added when
  the value is missing or rests on an assumption.
- `value` is None whenever an input it needs is missing. Values are plain JSON types.
- Keys and their order follow `MEASURES` in the module.
- `metrics_markdown(m)` renders the dict as a Markdown table. `flat_values(m)` returns `{key: value}`.

Additions to the contract signature `compute_paper_metrics(run_dir: Path) -> dict` are all keyword-only and
optional:

- `inputs=None` is a dict that overrides `<run_dir>/measure_inputs.json` key by key.
- `null_draws=1000`, `seed=20261103` and `null_seed_index=0` set the permutation null.

Called with only `run_dir`, it behaves as the contract says.

The module only reads the run directory. It never writes there, except through `write_paper_metrics` or `--out`.

#### Files read from the run directory

All of these are written by the pipeline:

- `config.yaml`, `grid_results.csv`, `audit.json`, `forks.json`
- `reproduction.md`, `gates.log`, `implementation_audit*.md`
- `companion_spec.json`, `companion_results.csv`
- `package_status.json`, `author_commentary.md`

A missing file sets the measures that depend on it to None and adds a warning.

#### `measure_inputs.json` (optional; the pipeline does not write it)

In the study these inputs came from coding steps outside the run directory.

| key | meaning | used by | default when absent |
|---|---|---|---|
| `claim` | `directional`, `null` or `unclear` | `claim_type`; notes on the outcomes | `directional` (noted as assumed) |
| `reproduced_p` | p of the reproduced main estimate under the paper's own inference | reproduction measures | then `reproduced_t`, then `reproduced_se`, then the heuristic read of `reproduction.md` (see deviation 6) |
| `reproduced_p_sided` | `two` or `one`; a one-sided p is doubled | `reproduced_p` | `two` |
| `reproduced_t`, `reproduced_se` | turned into a two-sided normal p | `reproduced_p` | — |
| `outcome_sd` | SD of the main outcome | `gap_sd`, `published_effect_sd`, the 0.10-SD clause of `close` | None (see deviation 7) |
| `nonlinear` | main estimate comes from a nonlinear model, which switches the SD clause off | `close` | false |
| `published_abs_t` | published \|t\|, used only when `config.yaml` gives neither SE, t nor p | `published_abs_t` | None |
| `fixes.p_sidedness` | `one_published_direction`: the grid's p are one-sided in the published direction and are made two-sided | every share and z | p used as written |
| `fixes.joint_p_column` | the grid column holding the p of a co-required test; each specification uses the larger of the two p | every share and z | — |
| `fixes.benchmark` | the claim is tested against a nonzero benchmark; sign = sign(estimate − benchmark) | every share and z | 0 |
| `fixes.units` | list of `{dimension, option, verdict: factor\|not_comparable, factor}` | the surv30h size rule only | — |
| `fixes.unclear` | the measure audit could not settle the grid; fixes are not applied | — | false |

`fixes` replays the study's measure audit, which was a hand review of grids that met the criteria listed in
`measure_review_flags`. The output does not apply fixes on its own. When a grid is flagged and no `fixes` are
given, a warning says so.

### Measures and where they come from in the paper

Section names refer to the paper. G0 to G6, GS and GC are the pipeline gates; their logic is in `core/`.

| key(s) | paper name | where the paper defines it |
|---|---|---|
| `share_supported`, `median_signed_z`, `share_published_sign`, `n_specifications` | share supported, median signed z, share with the published sign, N_i | The Measures → Outcomes; The Share with the Published Sign |
| `reproduced`, `reproduced_p020` | reproduced (p ≤ 0.10 standard; the p < 0.20 variant) | Outcomes; Reproducing the Main Estimate |
| `reproduced_null_standard`, `claim_type` | null-claim standard, claim type | The Main Claim; Null and Unclear Claims |
| `share_opposite_sign`, `share_published_sign_p_ge_010`, `share_supported_by_cutoff` | sign and p-value classes, cutoff curve | Sign and Significance across Specifications |
| `n_failed_specifications`, `n_distinct_estimand_specifications` | specifications that fail; distinct estimands | Choices, Options and the Grid; Implementation, Audits and the Report |
| `g0_strict_match`, `published_estimate`, `reproduced_estimate`, `relative_deviation`, `same_sign`, `reproduced_p`, `gap_sd`, `published_effect_sd`, `close`, `published_n`, `reproduced_n`, `n_gap` | reproduction table columns | Reproducing the Main Estimate; Rebuilding the Data and Reproducing the Main Estimate |
| `published_abs_t` | published \|t\| | The Strength of the Published Result |
| `materials` | authors' materials | Authors' Materials |
| `has_companion`, `companion_*`, `subgrid_*`, `restored_exclusion_type` | companion grid, sub-grid, rating of restored options | The Authors' Own Choices |
| `choice_ranges`, `top_choice_range`, `most_influential_choices`, `choice_decides`, `top_choice_range_null_p95`, `top_choice_range_exceeds_null` | range of a choice, most influential choice, "a choice decides", permutation benchmark | Which Choices Matter |
| `own_specification_found`, `own_specification_supports`, `share_supported_by_choices_changed`, `n_held_fixed_off_paper_option` | own specification, share by choices changed (k) | Distance from the Paper's Own Specification; Choices Changed: Table Details |
| `n_choices` … `share_supported_all_valid` | grid facts (choices, options, ratings, held-fixed choices, questionable options) | Choices, Options and the Grid; Inside the Grids |
| `designated_inference` | designated inference and its family | Inference Fixed before Estimation; Designated Inference by Experiment |
| `pipeline_audits` | audits and open findings | Audits and Open Findings; Roles and Gates |
| `commentary_classes` | what the papers say about their riskiest choices | What the Papers Say about Their Riskiest Choices |
| `surv30h`, `surv30h_parts`, `companion_surv30h` | earlier robustness score | earlier drafts of the study; kept for continuity |
| `strict_survival`, `strict_sign_consistency`, `companion_strict_survival` | the per-run report's survival rate | `core/scripts/survival.py` (the report generator) |
| `measure_review_flags`, `inputs_used`, `warnings` | diagnostics | criteria of the study's measure audit |

Several rules are shared by the measures above:

- **Specifications.** These are the rows of `grid_results.csv` inside the audited grid, using the same mask as
  `survival.py`: a row is kept when every crossed choice takes an audited option and every held-fixed choice
  takes its held option. If `headline_id` is present, only rows of the gating headline are kept. Rows are then
  dropped if they have an error, a distinct-estimand flag, or a missing estimate or p-value.
- **Published sign.** The sign is that of the gating headline's value in `config.yaml`.
- **Median signed z.** p is floored at 1e-4.
- **`share_supported_by_choices_changed`.** This uses the companion grid when the run has one, and the audited
  grid otherwise. Both versions are reported.
- **Permutation null.** Each draw shuffles one crossed choice's option labels within the strata formed by the
  other crossed choices. Choice j uses `numpy.random.default_rng([seed, 0, seed_index, j])`, with 1,000 draws.
- **Copied code.** `significance`, `annotate`, `grid_mask`, `parse_repro_table` and `match_target` are copies of
  `core/scripts/survival.py` and `core/scripts/gate_g0.py`. The tests check that they still agree with the
  shipped core scripts.

### Differences from the study code

1. **No hand overrides.** The study's analysis overrode some values by hand for individual runs: the inference
   family of a few runs, the choice family of a few choices, and the commentary class of a few unlabeled
   sections. Those per-run tables name sample papers and are not released. The module keeps only the keyword
   rules, so on a new paper every value comes from rules.
2. **Rows of the gating headline are filtered automatically.** Some grids hold rows of several headline
   estimates (a `headline_id` column). The study kept only the gating headline's rows for a hand-checked list of
   such grids, and stopped with an error on any other one. The module keeps only the gating headline's rows of
   every such grid and adds a warning. `strict_survival` follows `survival.py` and uses every row of the grid
   file.
3. **Numeric coercion in `annotate`.** `estimate` and `p_value` are coerced with `pd.to_numeric`, so a stray
   string such as `"NA"` becomes NaN instead of raising an error. Results on numeric columns are unchanged.
4. **`fixes.benchmark` is general.** The study had one hard-coded rule for one run: a share tested against 0.5.
   The module accepts any benchmark value instead.
5. **Inputs are optional.** In the study, claim type, reproduced p, outcome SD, nonlinearity and the measure-audit
   fixes always came from external files. Here they are optional, and every measure that does not need them is
   still computed.
6. **Heuristic reproduced p.** With no p, t or SE given as an input, the module reads the reproduced cell of the
   main estimate's row in `reproduction.md`:
   - It looks for `p`, then `t`/`z`, then `SE`, in the reproduced cell first and then in the rest of the row.
   - A match is skipped when its sentence refers to the published value.
   - The `t` pattern is case-sensitive, so a sample size written `T=` is not read as a t.
   - The note on `reproduced_p` gives the source.
   The study used an agent extraction for this value. In validation, the heuristic agrees with that extraction on
   which side of 0.10 the p falls for 95% of runs (counts below).
7. **Undetermined closeness.** `close` (and so `reproduced`) is None when two things hold: the 25% clause fails,
   and only the SD clause could still decide, but neither `outcome_sd` nor `nonlinear` is given. The study had an
   SD for every run, so this case never arose there.
8. **G0 without a G0 line.** If `gates.log` has no G0 decision, `g0_strict_match` is 0 when the run finished (a
   G6 PASS exists), as in the study. Otherwise it is None, because the run is still in progress.
9. **Normal quantiles from the standard library.** The module uses `statistics.NormalDist` (and `math.erfc`)
   instead of scipy. The values agree to machine precision.
10. **Permutation seed index.** It defaults to 0. The study used each paper's position in its own list as the
    index; that position can be passed as `null_seed_index`.
11. **Reproduced value without a numeric published value.** If the gating headline has no numeric published
    value, the module still parses `reproduced_estimate` (the study reported NaN). Every outcome is still None,
    and `own_specification_found` is None, not 0, because no specification can be scored.

### Left out

The module computes per-paper values only. The following are not computed:

- Cross-paper tests, regressions, panels and the hold-out.
- Split-half reliability across fork splits.
- Coder panels and the critique study.
- Compute and cost measures. These come from session logs, not the run directory.
- Paper characteristics: data class, order of the effect, journal tier, authors and institutions.
- The measure audit itself. This was a hand review; `measure_review_flags` reports when a grid needs one.

### Validation (study runs, read in place)

The module was run on all 321 runs that the paper's analysis uses. Each value was compared with the per-paper
value computed by the paper's own analysis code. Two passes:

- **Pass A** gives the study's external inputs: claim type, reproduced p, outcome SD, nonlinear, measure-audit
  fixes and the null seed index.
- **Pass B** gives no inputs, which is what a public user gets.

That makes 99 compared quantities. Two of them are compared per choice (1,887 choices); the rest per run.

**Pass A.** 93 of the 99 quantities match on 100% of runs. Every one of the six that do not is explained:

- `published_abs_t` matches 306 of 321. The other 15 runs took \|t\| from a separate extraction; given as the
  `published_abs_t` input, all 15 match.
- Inference family matches 318 of 321. The other 3 were hand overrides (deviation 1).
- Commentary "silent" count matches 315 of 317. The other 2 were hand-coded (deviation 1).
- Choice family matches 1,885 of 1,887 choices. The other 2 were hand-mapped (deviation 1).
- `reproduced_estimate` and `own_specification_found` each differ in 1 run, the run with no numeric published
  value (deviation 11).

**Pass B.**

- Grid-derived measures (share supported, median signed z, the other shares, the ranges, the companion and k
  shares) match on 97.8% to 99.7% of runs. surv30h and its size class match on 87% to 89%, because they also
  carry the unit fixes. Every mismatch is in a run that the study's measure audit fixed: 6 runs for share
  supported, 42 for surv30h.
- `reproduced` (p ≤ 0.10 standard) matches the study in 246 of 321 runs. Of the 75 that differ:
  - 62 are undetermined because no outcome SD was given.
  - 12 are undetermined because the heuristic found no p.
  - 1 has the heuristic p on the other side of 0.10.
  Where pass B determines `reproduced`, it matches the study in 243 of 244 runs.
- Heuristic reproduced p, over the 318 runs where the study has a p:
  - found in 306;
  - within 0.005 of the study's value in 292;
  - on the same side of 0.10 in 303.
- The grid-fact, pipeline-audit, companion-count, strict-survival and G0 measures do not depend on inputs, and
  they match as in pass A.

The per-run comparison names papers, so it is kept outside the repository.

## Fixes after the release review

Three independent reviews (fidelity to the study's workflow script, release hygiene, and
installation and integration) checked the package before release. Each finding was reproduced
from the code first. The sections above already describe the resulting behaviour; this list
records what changed.

### Fidelity to the study's control flow (`orchestrator.py`)

| # | Finding | Fix |
|---|---|---|
| F1 | Resuming a paused run with a new pause point (`ama resume --stop-after`, `resume_run(stop_after=...)`) ran to the end and erased the option: every resume of a paused run cleared `stop_after`. | `stop_after` is cleared only when its stage is already done (the pause point just honoured, which cannot fire again). A new pause point at a later stage pauses the run there. Test: `test_resume_with_a_new_pause_point`. |
| F2 | An unreadable `companion_spec.json` recorded `None` for the check before the error, and the companion implementer stayed recorded as done, so every `ama resume` failed again at once without launching an agent, although the log said resume retries. | The error is raised inside the recorded check (nothing is recorded) as a role failure carrying the implementer's key, which is then forgotten; a resume launches a fresh companion implementer. Same record as the study (`stopped_at: "error"`). Test: `test_unreadable_companion_spec_is_retried_on_resume`. |
| F3 | A failed copy of `reproduction.md` to `reproduction_r<k>.md` in the G0A loop ended the run with an error; the study's `cp ... && echo archived` was not checked and the fresh reproducer still ran. | The copy's error is logged and recorded, and the run goes on. Test: `test_failed_reproduction_archive_does_not_stop_the_run`. |
| F4 | The first 3F session of a fix round caught only a missing artifact; the study caught every error there (e.g. the agent CLI failing to start) and went on to the grid wait. | Every `Exception` is caught (an interruption still ends the run); the failure is recorded under the session's key so a resume replays it. Test: `test_fix_round_session_error_waits_for_the_grid`. |
| F5 | The early end of a grid wait fired at the first check when the results file did not exist yet (its age read as infinite), guarded only by the scan for processes working in the run directory; the change log did not describe this or the scan's assumption. | A missing file counts as untouched since the start of the wait. The scan also counts a process whose command line names a path inside the run directory (e.g. `python RUN/run_grid.py` started from elsewhere), and no longer counts any process above the orchestrator (only its parent was excluded before), since a wrapper such as `timeout ... --run-dir RUN` names the run but writes no grid. The assumption is documented (Orchestrator section, deviation 5). Tests: `test_missing_grid_file_is_waited_for`, `test_grid_writer_named_on_its_command_line_counts_as_running`. |

### Release hygiene

| # | Finding | Fix |
|---|---|---|
| R1 | The release test stored the study team's identifiers as string fragments that joined back into the real values. | Removed. `tests/test_release.py` scans the whole repository for machine paths, e-mail addresses and tokens (with an explicit list of the invented values the runner tests use), and checks a maintainer's private deny-list only when `AMA_RELEASE_DENYLIST` points at a file kept outside the repository; matches are reported by file and line, never by the matched text. `tests/test_core_release.py` keeps the core-only checks. |
| R2 | No `.gitignore`; bytecode files containing this machine's absolute paths and the `egg-info` build directory sat in the untracked tree. | `.gitignore` added (bytecode, build products, caches, virtual environments, `runs/`); the generated files were deleted; `tests/test_release.py` checks the ignore rules and, when the directory is a git work tree, that no such file is tracked. |
| R3 | The README overstated the agents' isolation: Codex sessions still load `AGENTS.md` files, a Codex MCP server with an unusual name (or every server, when listing fails) stays enabled, `benchmark/` is protected only by the prompts, and agents can edit the installed package. | README Safety rewritten to say exactly that, with the advice to install the package where the analysis user cannot write. The runner records a failed `codex mcp list` (`mcp_list_error`) and the orchestrator logs a warning when MCP servers may have stayed enabled. `project_doc_max_bytes` was not added: it could not be verified without a live session. Tests: `test_codex_runner_records_a_failed_mcp_listing`, `test_mcp_servers_left_enabled_are_logged_once`. |
| R4 | The README did not tell Codex users that Codex kills background jobs, which the grid prompts rely on. | A Codex-only note in the kickoff message (Orchestrator section, deviation 6). The prompts are unchanged. Test: `test_codex_sessions_get_the_background_note`. |
| R5 | `data_sources_TEMPLATE.md` said that roles other than B, BF, SR and G0A see the data-sources pointer. | Corrected: only those four are pointed at the file. |
| R6 | `env.md` kept the study-only sentence about compiling missing sources into "the study's data-requirements register". | Replaced (Core section, Documents). |
| R7 | Dated notes ("user rule, 2026-09-28") read as if "the user" were the person running the tool. | One sentence in the PIPELINE.md release note and in the core README explains them. The notes stay, as part of what was run. |
| R8 | No LICENSE file; the README's License and Citation sections are placeholders. | Not changed: the license and the citation are the authors' decision. Both must be filled in before the repository is made public. |

### Installation and integration

| # | Finding | Fix |
|---|---|---|
| I1 | `ama-mcp --help` and `--version` started the stdio server silently. | `ama-mcp --help` prints how to register the server with Claude Code and Codex; `--version` prints the version; other arguments are refused; on a terminal the server says on stderr that it waits for a client. Test: `test_server_help_and_version`. |
| I2 | `ama run --foreground` exited 1 without a reason when stderr was not a terminal. | `ama run/resume --foreground` print the final status, the stop or error and the log path; the orchestrator's own entry point prints a final line to stderr when its log lines were not shown. While fixing this, `runs.status(..., log_lines=0)` returned the whole log tail instead of none; fixed. Tests: `test_cli_foreground_reports_how_the_run_ended`, `test_orchestrator_entry_point_says_how_the_run_ended`. |
| I3 | No `.gitignore` (same as R2). | See R2. |
