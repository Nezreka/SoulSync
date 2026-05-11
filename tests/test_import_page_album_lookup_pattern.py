"""Pin the import-page album-lookup cache pattern in
``webui/static/stats-automations.js`` — github issue #524 regression
guard at the source-text level.

Why a structural test instead of a behavioral JS test:

``stats-automations.js`` is a ~7k-line file with a lot of global state
+ inline DOM rendering. Loading it into a sandboxed Node `vm` context
(the pattern used in `tests/static/test_discover_section_controller.mjs`)
would require stubbing dozens of unrelated dependencies. The file
needs to be modularized before behavioral tests are practical for
arbitrary functions in it.

Until then, this test fails the suite if the critical pattern from
the #524 fix gets removed:

1. The album cache (``_albumLookup`` field on ``importPageState``)
2. Card renderers populating the cache before emitting the onclick
3. The match-POST builder reading source/name/artist from the cache

If anyone deletes the cache, the click handler, or the cache writes,
this test catches it before the regression ships.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = _REPO_ROOT / "webui" / "static" / "stats-automations.js"


@pytest.fixture(scope="module")
def js_source() -> str:
    return _SOURCE.read_text(encoding="utf-8")


def test_album_lookup_cache_field_exists_on_state(js_source: str):
    """importPageState must have an `_albumLookup` field. Without it,
    card renderers have nowhere to stash source/name/artist for the
    click handler to read."""
    assert "_albumLookup:" in js_source, (
        "importPageState._albumLookup field missing — the album cache "
        "that backs the source-routing fix for issue #524 has been "
        "removed. The click handler will fall back to passing only "
        "album_id and the backend will silently misroute lookups again."
    )


def test_select_album_handler_reads_cache(js_source: str):
    """importPageSelectAlbum must read source / name / artist from
    the cache and include them in the match POST body. The whole
    point of the fix."""
    # Find the function body
    match = re.search(
        r"async function importPageSelectAlbum\([^)]*\) \{(.*?)^\}",
        js_source, re.DOTALL | re.MULTILINE,
    )
    assert match, "importPageSelectAlbum function not found"
    body = match.group(1)

    # Must read from the lookup cache
    assert "_albumLookup[" in body, (
        "importPageSelectAlbum no longer reads from "
        "importPageState._albumLookup — match POST will drop source "
        "again, see issue #524."
    )

    # Must build a matchBody that includes source + album_name + album_artist
    for required_field in ("source:", "album_name:", "album_artist:"):
        assert required_field in body, (
            f"matchBody missing required field {required_field!r}. "
            "Backend's get_artist_album_tracks needs source to route "
            "the lookup to the correct metadata client. Without it, "
            "cross-source album_ids fall through to the failure-fallback "
            "dict (Unknown Artist / album_id-as-title / 0 tracks). "
            "See issue #524 for the original symptom."
        )


def test_card_renderers_populate_cache_before_onclick(js_source: str):
    """Both renderers (suggestion card + search-result card) must write
    to ``_albumLookup`` before emitting the onclick — otherwise the
    click handler reads an empty cache for newly-displayed albums."""
    cache_writes = re.findall(
        r"_albumLookup\[a\.id\]\s*=\s*\{",
        js_source,
    )
    assert len(cache_writes) >= 2, (
        f"Expected >=2 _albumLookup writes (one per card renderer - "
        f"suggestions + search results), found {len(cache_writes)}. "
        "Adding a new card-rendering site without populating the cache "
        "regresses issue #524 for that path."
    )


def test_cache_entry_carries_source_field(js_source: str):
    """The cache must store `source:` per entry — not just id/name/artist."""
    write_blocks = re.findall(
        r"_albumLookup\[a\.id\]\s*=\s*\{[^}]*\}",
        js_source,
    )
    assert write_blocks, "no _albumLookup writes found"
    assert any("source:" in block for block in write_blocks), (
        "_albumLookup cache entries must include `source` — that's the "
        "field the click handler forwards to /api/import/album/match "
        "to route the lookup to the correct provider."
    )
