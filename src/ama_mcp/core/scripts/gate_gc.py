#!/usr/bin/env python3
"""GC -- config gate.  Stands in for the human review of config.yaml.

In the study the headline was fixed externally: runs/<id>/headline_anchor.json
(written by the orchestrator from the Reusing Natural Experiments database,
Internet Appendix Table IA.II) lists the outcomes and table locations the
literature records for this paper.  In this release the anchor is OPTIONAL.
Passes iff
  1. config.yaml has the required fields and a positive/negative direction;
  2. it carries exactly one gating coefficient target, h1, with a value, and --
     only when headline_anchor.json exists -- whose `ia_location` is copied
     verbatim from the anchor;
  3. the independent config reviewer's config_review.md ends in
     `CONFIG_REVIEW: PASS`.
Without an anchor, the choice of h1 is checked by the reviewer alone (its
prompt then asks it to check the pick against the paper itself).

    python <core>/scripts/gate_gc.py runs/<run_id>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from _gatelog import log_gate

REQUIRED = ["run_id", "paper", "research_question", "published_conclusion",
            "headline_estimates", "design", "authors_fork_choices", "data", "compute"]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    run = Path(ap.parse_args().run_dir).resolve()
    problems: list[str] = []

    try:
        cfg = yaml.safe_load((run / "config.yaml").read_text()) or {}
    except Exception as e:  # noqa: BLE001
        return fail(run, f"config.yaml unreadable: {e}")
    anchor_path = run / "headline_anchor.json"
    anchor = json.loads(anchor_path.read_text()) if anchor_path.exists() else None
    locs = {norm(o["location"]): o for o in anchor.get("ia_outcomes", [])} if anchor is not None else {}

    for k in REQUIRED:
        if not cfg.get(k):
            problems.append(f"config.yaml missing {k}")
    direction = str((cfg.get("published_conclusion") or {}).get("direction", "")).lower()
    if direction not in ("positive", "negative"):
        problems.append(f"direction is {direction!r}, not positive/negative")

    ests = cfg.get("headline_estimates") or []
    gating = [e for e in ests if e.get("gating", True)]
    if len(gating) != 1:
        problems.append(f"{len(gating)} gating targets; exactly one (h1) required")
    else:
        h1 = gating[0]
        if str(h1.get("id")) != "h1":
            problems.append(f"gating target id is {h1.get('id')!r}, not h1")
        if anchor is not None:
            got = norm(h1.get("ia_location", ""))
            # exact match, or the anchor location embedded verbatim (e.g. "Capex -- Tab 4, Col 2")
            if got not in locs and not any(got.endswith(k) for k in locs):
                problems.append(f"h1.ia_location {h1.get('ia_location')!r} is not an anchor location")
        if h1.get("value") is None:
            problems.append("h1 has no value")

    rv = run / "config_review.md"
    verdict = ""
    if rv.exists():
        verdict = next((l.strip() for l in reversed(rv.read_text().splitlines())
                        if "CONFIG_REVIEW:" in l), "")
    if not verdict:
        problems.append("config_review.md missing or has no CONFIG_REVIEW line")
    elif "PASS" not in verdict.split("CONFIG_REVIEW:", 1)[1][:8]:
        problems.append(f"reviewer: {verdict[:300]}")

    print("GC config check")
    if gating:
        anc = (f"anchor: {gating[0].get('ia_location')}" if anchor is not None
               else "no headline_anchor.json: anchor check skipped")
        print(f"  h1: {gating[0].get('quantity')} at {gating[0].get('location')} "
              f"({anc}) = {gating[0].get('value')}")
    print(f"  reviewer: {verdict or '(none)'}")
    if problems:
        return fail(run, "; ".join(problems))
    if anchor is not None:
        log_gate(run, "GC", "PASS", f"h1 at {gating[0].get('ia_location')}; reviewer PASS")
    else:
        log_gate(run, "GC", "PASS", f"h1 at {gating[0].get('location')} (no anchor); reviewer PASS")
    print("GC: PASS")
    return 0


def fail(run: Path, why: str) -> int:
    log_gate(run, "GC", "FAIL", why[:500])
    print(f"GC: FAIL - {why}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
