#!/usr/bin/env python3
"""Scaffold a run directory for one paper.

    python <core>/scripts/new_run.py <run_id> --root <runs_root>

Creates runs/<id>/{package,benchmark,repro,figures,prompts_rendered} and an
empty gates.log.  `package/` is what the agents see; `benchmark/` holds any
prior critique of the paper and is off-limits to every agent -- it exists so
the orchestrator can compare the finished assessment against a held-out human
reanalysis after G6.

--root defaults to ./runs in the current directory (the study defaulted to the
runs/ folder of its own codebase).
"""
from __future__ import annotations

import argparse
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent
SUBDIRS = ["package", "benchmark", "repro", "figures", "prompts_rendered"]

BENCH_README = """\
# Held-out benchmark -- NOT VISIBLE TO AGENTS

Anything in this directory is a prior human critique, comment, reply or
reanalysis of the paper under assessment. Every AMA role prompt forbids
reading it. It is opened only by the orchestrator, after G6, so that the
assessment can be compared against a held-out human benchmark.

Putting a file here and then showing it to an agent destroys that comparison.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--root", default=str(Path.cwd() / "runs"))
    args = ap.parse_args()

    run = Path(args.root).resolve() / args.run_id
    for d in SUBDIRS:
        (run / d).mkdir(parents=True, exist_ok=True)
    (run / "benchmark" / "README.md").write_text(BENCH_README, encoding="utf-8")
    (run / "gates.log").touch()
    print(f"scaffolded {run}")
    for d in SUBDIRS:
        print(f"  {d}/")
    print("\nnext: put the replication package in package/, then run step C "
          f"(python {CORE / 'scripts' / 'render_prompt.py'} {run} C)")


if __name__ == "__main__":
    main()
