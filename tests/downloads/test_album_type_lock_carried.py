"""every place the download pipeline rebuilds an album context carries the
artist page's section lock.

a release downloaded from the artist page is locked to the section it sat in
(album / ep / single), so it files where the user saw it. the pipeline
rebuilds the album dict from a fixed set of keys in several places; one that
forgets the lock silently hands the path builder a guessable type again.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REBUILDS = {
    "core/downloads/candidates.py": 2,
    "core/downloads/staging.py": 2,
    "core/downloads/master.py": 1,
    "core/wishlist/album_grouping.py": 1,
}
# rebuilds written with double quotes
REBUILDS_DQ = {
    "core/wishlist/routes.py": 1,
}


def test_each_album_context_rebuild_carries_the_lock():
    for rel, expected in REBUILDS.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        rebuilds = len(re.findall(r"'album_type': \w+\.get\('album_type'", src))
        locks = src.count("'album_type_locked': bool(")
        assert rebuilds == expected, f"{rel}: expected {expected} album rebuilds, found {rebuilds} (update this test)"
        assert locks >= rebuilds, f"{rel}: {rebuilds} album rebuilds but only {locks} carry album_type_locked"


def test_the_wishlist_add_endpoint_keeps_the_lock():
    for rel, expected in REBUILDS_DQ.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        rebuilds = len(re.findall(r'"album_type": album\.get\("album_type"', src))
        assert rebuilds == expected, f"{rel}: expected {expected} album rebuilds, found {rebuilds}"
        assert src.count('"album_type_locked": bool(') >= rebuilds, rel
