"""Release hygiene of the core part: the shipped files exist and none of the study's cluster or
tool-specific rules is shipped in core/.

Machine paths, e-mail addresses, tokens and a maintainer's private deny-list are checked over
the whole repository by tests/test_release.py.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "src" / "ama_mcp" / "core"
# The study's cluster rules and the tool-specific publishing rule must not be shipped in core/
# (the change log and the tests may name them, to say they were dropped).
# Built from fragments so that this file does not match itself.
CORE_ONLY = [re.compile("slurm" + "_run", re.I), re.compile("AMA_" + "SHARD"), re.compile("claude" + r"\.ai"),
             re.compile("Artifact" + "Comments"), re.compile("COMPUTE ON " + "THIS SERVER")]
TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".csv", ".txt", ".log", ".toml"}


def files():
    for p in CORE.rglob("*"):
        if p.is_file() and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts:
            yield p


def test_core_files_exist() -> None:
    assert len(list((CORE / "prompts").glob("step*.md"))) == 18
    for name in ("render_prompt", "new_run", "_gatelog", "freeze", "survival", "spec_curve", "gate_g0",
                 "gate_g0a", "gate_gp", "gate_gs", "gate_gc", "gate_g2", "gate_g4", "gate_g6"):
        assert (CORE / "scripts" / f"{name}.py").is_file(), name
    for doc in ("PIPELINE.md", "gates.md", "rubric.md", "env.md", "data_sources_TEMPLATE.md", "README.md"):
        assert (CORE / doc).is_file(), doc
    assert not (CORE / "data_sources.md").exists()


def test_no_cluster_or_publishing_rules_in_core() -> None:
    hits = []
    for f in files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for pat in CORE_ONLY:
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{f.relative_to(REPO)}:{line}: {m.group(0)!r}")
    assert not hits, "\n".join(hits)
