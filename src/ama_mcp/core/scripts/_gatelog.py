"""Append-only gate decision log shared by the gate scripts."""
from __future__ import annotations

import datetime as _dt
import hashlib
from pathlib import Path

ARTIFACTS = ["config.yaml", "reproduction.md", "forks.json", "audit.json",
             "run_grid.py", "run_grid.R", "grid_results.csv",
             "implementation_audit.md", "fix_log.md", "report.md"]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.exists() else "-"


def log_gate(run: Path, gate: str, verdict: str, reason: str) -> None:
    ts = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    hashes = " ".join(f"{n}:{_sha(run / n)}" for n in ARTIFACTS
                      if (run / n).exists())
    with (run / "gates.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{ts}\t{gate}\t{verdict}\t{reason}\n")
        fh.write(f"\tartifacts\t{hashes}\n")
