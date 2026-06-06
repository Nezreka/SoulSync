#!/usr/bin/env python3
"""One-off repair for source ids that enrichment wrongly shared across multiple
artists (the Kendrick/Jorja corruption — one Deezer/AudioDB/Qobuz/Tidal id
stamped onto several unrelated artists).

Dry-run by default — shows exactly what it would clear and writes nothing.

Usage:
    python scripts/dedupe_source_ids.py            # dry-run (review first)
    python scripts/dedupe_source_ids.py --apply    # actually clear them

After --apply, run metadata enrichment so the (now name-checked) workers
re-derive each artist's id correctly. Stop the app first so the DB isn't locked.
"""

import logging
import os
import sys

# Allow running directly (`python scripts/dedupe_source_ids.py`) — put the repo
# root on the path so `core` / `database` import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.maintenance.dedupe_source_ids import clear_corrupt_source_ids  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("dedupe_source_ids")


def main():
    apply = "--apply" in sys.argv[1:]
    db = MusicDatabase()
    report = clear_corrupt_source_ids(db, dry_run=not apply)

    mode = "APPLYING" if apply else "DRY-RUN (no changes written)"
    logger.info(f"=== Source-id corruption repair — {mode} ===")
    logger.info(
        f"Corrupt clusters: {report['cluster_count']}  |  "
        f"artists affected: {report['artist_count']}"
    )
    if report['by_source']:
        logger.info("By source: " + ", ".join(
            f"{s}={n}" for s, n in sorted(report['by_source'].items())
        ))
    for c in report['clusters']:
        logger.info(f"  [{c['source']}] id {c['source_id']} -> {', '.join(c['artists'])}")

    if not report['cluster_count']:
        logger.info("Nothing to clean — no shared source ids across differently-named artists.")
    elif apply:
        logger.info("Cleared. Now run metadata enrichment to re-derive these ids correctly.")
    else:
        logger.info("Re-run with --apply to clear these (then run enrichment to re-derive).")


if __name__ == "__main__":
    main()
