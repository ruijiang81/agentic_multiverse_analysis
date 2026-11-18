"""Agentic multiverse analysis (AMA) of one empirical paper, driven through agent CLIs.

The package ships the pipeline as it was run for the study (``ama_mcp/core``: role prompts,
gate scripts and the orchestration contract), an orchestrator that runs one paper through
it by launching every role as a fresh Claude Code (``claude -p``) or Codex (``codex exec``)
session, an MCP server (``ama-mcp``) and a command line (``ama``).
"""
from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"

__all__ = ["__version__", "core_dir"]


def core_dir() -> Path:
    """Directory holding the pipeline's prompts, gate scripts and documents.

    ``AMA_CORE_DIR`` overrides the packaged copy (for example to try edited prompts
    without touching the installed package).
    """
    env = os.environ.get("AMA_CORE_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent / "core"
