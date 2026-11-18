"""Release hygiene of the whole repository: no machine paths, e-mail addresses, tokens or build
products, and (optionally) none of the names on a maintainer's private deny-list.

The deny-list is never stored in the repository. To use it, put one entry per line in a file
outside the repository and point ``AMA_RELEASE_DENYLIST`` at it. An entry is matched as a
case-insensitive literal; an entry starting with ``re:`` is a regular expression. Lines starting
with ``#`` are comments. Without the variable, that check is skipped.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "build", "dist", "runs", "ama-runs"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".csv", ".txt", ".log", ".toml", ".cfg", ".ini",
                 ".js", ".sh", ".tex", ".html", ""}

# Built from fragments so that this file does not match itself.
GENERIC = {
    "machine path": re.compile("/" + "Users/|/" + "mfs/|/" + "home/|Box" + "-Box|Cloud" + "Storage|~/" + r"\.venvs"),
    "e-mail address": re.compile(r"[A-Za-z0-9._%+-]+" + "@" + r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "API token": re.compile("sk" + r"-[A-Za-z0-9_-]{20,}|gh" + r"[pousr]_[A-Za-z0-9]{20,}|xox" + r"[abpr]-[A-Za-z0-9-]{10,}"
                            "|AK" + r"IA[0-9A-Z]{16}"),
    "JSON web token": re.compile("ey" + r"J[A-Za-z0-9_-]{8,}\.ey" + r"J[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
}
# Invented values that the runner tests use to check that credentials are redacted.
ALLOWED = {"a" + "@b.com", "someone" + "@example.org", "sk" + "-proj-abcdef0123456789",
           "ey" + "JhbGciOiJIUzI1NiJ9.ey" + "JzdWIiOiIxIn0.abc"}
ALLOWED_EMAIL_DOMAINS = re.compile(r"@example\.(org|com|net)$")


def repo_files():
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix in TEXT_SUFFIXES or name in (".gitignore", "LICENSE"):
                yield p


def _scan(patterns: dict[str, re.Pattern]) -> list[str]:
    hits = []
    for f in repo_files():
        if f.resolve() == Path(__file__).resolve():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for what, pat in patterns.items():
            for m in pat.finditer(text):
                s = m.group(0)
                if s in ALLOWED or (what == "e-mail address" and ALLOWED_EMAIL_DOMAINS.search(s)):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{f.relative_to(REPO)}:{line}: {what}: {s!r}")
    return hits


def test_no_machine_paths_emails_or_tokens() -> None:
    hits = _scan(GENERIC)
    assert not hits, "\n".join(hits)


def _denylist() -> dict[str, re.Pattern]:
    path = os.environ.get("AMA_RELEASE_DENYLIST", "").strip()
    if not path:
        pytest.skip("AMA_RELEASE_DENYLIST is not set (a maintainer's private list of names to keep out)")
    pats = {}
    for i, line in enumerate(Path(path).expanduser().read_text(encoding="utf-8").splitlines(), 1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        rx = entry[3:] if entry.startswith("re:") else re.escape(entry)
        pats[f"deny-list entry {i}"] = re.compile(rx, re.I)
    return pats


def test_no_denylisted_names() -> None:
    pats = _denylist()
    hits = []
    for f in repo_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for what, pat in pats.items():
            for m in pat.finditer(text):
                hits.append(f"{f.relative_to(REPO)}:{text.count(chr(10), 0, m.start()) + 1}: {what}")
    assert not hits, "\n".join(hits)  # the matched text itself is not printed


def test_build_products_are_ignored() -> None:
    gi = (REPO / ".gitignore").read_text(encoding="utf-8").split()
    for pat in ("__pycache__/", "*.py[cod]", "*.egg-info/", "build/", "dist/", "runs/"):
        assert pat in gi, pat
    if shutil.which("git") is None or not (REPO / ".git").exists():
        return
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True).stdout
    bad = [f for f in out.splitlines() if re.search(r"(__pycache__/|\.py[cod]$|\.egg-info/)", f)]
    assert not bad, bad
