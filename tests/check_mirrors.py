#!/usr/bin/env python
"""Enumerated mirror parity check for skills/k1-otd/reference/.

SKILL.md instructs agents to load the `reference/` copies as ground truth,
so every file there must be byte-identical to its source of truth in the
repository.

A prior adversarial review found `reference/proof-emitted.otd.yaml` stale by
several waves. The cause was not the sync itself but the check: parity was
verified against a hand-maintained list of two pairs while `reference/` held
seven files. A list cannot notice a file nobody added to it. This enumerates
the directory instead.

  python tests/check_mirrors.py          # verify; exit 1 on any mismatch
  python tests/check_mirrors.py --sync   # copy each source over its mirror
"""
import hashlib
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = REPO_ROOT / "skills/k1-otd/reference"
# `generated` is gitignored build output, not a source of truth. Without it,
# `proof-emitted.otd.yaml` resolved to two candidates -- the tracked canonical
# example and the untracked regenerated one -- and the checker correctly
# refused to guess which was authoritative.
SKIP_DIR_PARTS = {".git", "__pycache__", "reference", "generated"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_source(name: str):
    """Locate the single repository file a mirror copy shadows.

    Returns (path, note). A name that matches zero or several files is
    reported rather than guessed -- an ambiguous mirror is a governance
    problem, not something to resolve silently.
    """
    matches = []
    for candidate in REPO_ROOT.rglob(name):
        if not candidate.is_file():
            continue
        rel_parts = set(candidate.relative_to(REPO_ROOT).parts)
        if rel_parts & SKIP_DIR_PARTS:
            continue
        matches.append(candidate)
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        return None, "no source of truth found outside reference/"
    rels = ", ".join(str(m.relative_to(REPO_ROOT)) for m in sorted(matches))
    return None, f"ambiguous: {len(matches)} candidates ({rels})"


def main() -> int:
    do_sync = "--sync" in sys.argv
    if not REFERENCE_DIR.is_dir():
        print(f"ERROR: reference directory not found: {REFERENCE_DIR}")
        return 1

    mirrors = sorted(p for p in REFERENCE_DIR.iterdir() if p.is_file())
    print(f"=== Mirror Parity ({len(mirrors)} files enumerated in reference/) ===")
    if do_sync:
        print("(--sync: sources will be copied over their mirror copies)")

    problems = []
    for mirror in mirrors:
        source, note = find_source(mirror.name)
        if source is None:
            print(f"  UNRESOLVED  {mirror.name}  -- {note}")
            problems.append(mirror.name)
            continue

        rel_source = source.relative_to(REPO_ROOT)
        if do_sync and sha256(source) != sha256(mirror):
            shutil.copyfile(source, mirror)
            print(f"  SYNCED      {mirror.name}  <- {rel_source}")

        s_hash, m_hash = sha256(source), sha256(mirror)
        if s_hash == m_hash:
            print(f"  MATCH       {mirror.name}  {s_hash[:16]}  <- {rel_source}")
        else:
            print(f"  MISMATCH    {mirror.name}")
            print(f"                source {rel_source}  {s_hash[:16]}")
            print(f"                mirror                {m_hash[:16]}")
            problems.append(mirror.name)

    if problems:
        print(f"\n{len(problems)} mirror problem(s): {', '.join(problems)}")
        return 1
    print(f"\nAll {len(mirrors)} mirrors are byte-identical to their sources.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
