#!/usr/bin/env python3
"""What did upstream build in code this branch deleted?

The Library-v2 rewrite deleted whole families of files — the legacy
artist-detail and library UI, the retired repair jobs — and rebuilt their
behaviour elsewhere. A merge resolves those as "keep the deletion", which is
correct and also completely silent: upstream can add a feature to
``release-card.tsx`` every month and every sync will keep dropping it without
one line of output. That is how this branch ended up without album and artist
play buttons, and with a music-video shelf that shipped unreachable.

This script makes that gap loud. It lists the upstream commits that touched
paths this branch no longer has, so each one is a decision someone made rather
than a deletion nobody saw.

    python3 scripts/deleted_path_upstream_audit.py                  # since the merge base
    python3 scripts/deleted_path_upstream_audit.py --since 3.3.1    # since a tag
    python3 scripts/deleted_path_upstream_audit.py --upstream upstream/dev

Decisions are recorded in ``scripts/deleted_path_reviewed.json`` so the list
only ever shows work nobody has looked at yet. A tool that reprints the same
seven commits every sync is a tool people stop reading. Record one with::

    python3 scripts/deleted_path_upstream_audit.py --reviewed <sha> "why"

Exit code is 1 when anything unreviewed is listed, so it can gate a sync.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import subprocess
import sys

# Commit subjects that never carry a feature — no point asking about them.
NOISE = ("Merge pull request", "Merge branch", "style(", "oxfmt", "docs(", "chore(tests)")

LEDGER = pathlib.Path(__file__).with_name("deleted_path_reviewed.json")


def load_ledger() -> dict[str, str]:
    if not LEDGER.exists():
        return {}
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def record(sha: str, note: str) -> int:
    """Mark one commit as decided, so it stops being reported."""
    full = git("rev-parse", sha)
    ledger = load_ledger()
    ledger[full[:12]] = note
    LEDGER.write_text(
        json.dumps(dict(sorted(ledger.items())), indent=2) + "\n", encoding="utf-8")
    print(f"recorded {full[:12]}: {note}")
    return 0


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def deleted_paths(base: str, head: str) -> set[str]:
    """Files that existed at the merge base and are gone on this branch."""
    out = git("diff", "--diff-filter=D", "--name-only", base, head)
    return {line for line in out.split("\n") if line}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default="upstream/dev")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument(
        "--since",
        default=None,
        help="Start from this ref instead of the merge base (e.g. a release tag).",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Only report deleted files under this prefix (repeatable).",
    )
    parser.add_argument(
        "--reviewed",
        nargs=2,
        metavar=("SHA", "NOTE"),
        help="Record a decision about one commit and exit.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Report everything, including commits already decided.",
    )
    args = parser.parse_args()

    if args.reviewed:
        return record(*args.reviewed)

    base = args.since or git("merge-base", args.head, args.upstream)
    gone = deleted_paths(base, args.head)
    if args.path:
        gone = {p for p in gone if any(p.startswith(prefix) for prefix in args.path)}
    if not gone:
        print("No deleted paths to audit.")
        return 0

    # One `git log` over all of them, then group — asking per file is O(files)
    # subprocesses and this list runs to the hundreds.
    log = git(
        "log", "--format=%H\x1f%h\x1f%ad\x1f%s", "--date=short", "--name-only",
        f"{base}..{args.upstream}", "--", *sorted(gone),
    )

    commits: dict[str, tuple[str, str, str]] = {}
    touched: dict[str, list[str]] = collections.defaultdict(list)
    current = None
    for line in log.split("\n"):
        if "\x1f" in line:
            full, short, date, subject = line.split("\x1f", 3)
            current = full
            commits[full] = (short, date, subject)
        elif line.strip() and current:
            if line in gone:
                touched[current].append(line)

    ledger = {} if args.all else load_ledger()
    interesting = [
        (full, meta) for full, meta in commits.items()
        if touched.get(full)
        and not any(n in meta[2] for n in NOISE)
        and full[:12] not in ledger
    ]
    if not interesting:
        decided = len(ledger)
        note = f", {decided} already decided" if decided else ""
        print(f"{len(gone)} deleted path(s) audited — nothing new{note}.")
        return 0

    print(f"Upstream work in {len(gone)} path(s) this branch deleted, since {base[:12]}:\n")
    for full, (short, date, subject) in sorted(interesting, key=lambda x: x[1][1]):
        print(f"  {short}  {date}  {subject}")
        for path in sorted(touched[full])[:6]:
            print(f"      {path}")
        extra = len(touched[full]) - 6
        if extra > 0:
            print(f"      … and {extra} more")
        print()

    print(f"{len(interesting)} commit(s) to review. Each is either a feature to port")
    print("into the Library-v2 surface that replaced it, or a deliberate decline.")
    print("Record one with:  --reviewed <sha> \"what was decided\"")
    return 1


if __name__ == "__main__":
    sys.exit(main())
