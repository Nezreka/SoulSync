"""#1138 — a manual library match whose file was deleted must not strand the track.

carlosjfcasero: matched song B to a library file, deleted that file from disk,
reprocessed the playlist. The log said the match "cannot apply", and the song
was then neither downloaded nor wishlisted — it silently vanished from the run,
every run.

Why: the preview flagged `has_manual_match` from the mere EXISTENCE of a saved
match row, never asking whether that match still resolves. A dead match
therefore rendered as "already matched, not in the playlist yet", which is the
one state offering neither Find & Add nor a download. The lookup already knew
it had failed — it just had no way to say so.
"""

from __future__ import annotations

from core.sync.match_overrides import build_bulk_override_lookup


class _Db:
    """Bulk-capable stub: one saved manual match pointing at a dead library row."""

    def __init__(self, match=None, path_resolves=None):
        self._match = match
        self._path_resolves = path_resolves

    def read_sync_match_cache_bulk(self, ids, server_source):
        return {}

    def find_manual_library_matches_bulk(self, profile_id, ids, server_source):
        return {"s1": self._match} if self._match else {}

    def find_track_id_by_file_path(self, path):
        return self._path_resolves


SOURCE_TRACKS = [{"source_track_id": "s1", "name": "El Fin del Mundo"}]


def test_a_dead_match_is_reported_as_dead():
    db = _Db(match={"library_track_id": "17549816",
                    "library_file_path": "/music/gone.mp3"},
             path_resolves=None)          # the file was deleted
    lookup = build_bulk_override_lookup(db, 1, "jellyfin", {"9001"}, SOURCE_TRACKS)

    assert lookup("s1") is None, "an unresolvable match must not pair anything"
    assert "s1" in lookup.dead_match_source_ids, (
        "the caller has to be able to tell a dead match from a live one, or it "
        "renders the row as already-matched and strands the track")


def test_a_live_match_is_not_marked_dead():
    db = _Db(match={"library_track_id": "9001",
                    "library_file_path": "/music/here.mp3"})
    lookup = build_bulk_override_lookup(db, 1, "jellyfin", {"9001"}, SOURCE_TRACKS)

    assert lookup("s1") == "9001"
    assert lookup.dead_match_source_ids == set()


def test_a_match_self_healed_by_file_path_is_not_dead():
    """The stored path still resolves to a live library row — that is a HIT
    (and the existing self-heal rewrites the id), not a dead match."""
    db = _Db(match={"library_track_id": "old-id",
                    "library_file_path": "/music/moved.mp3"},
             path_resolves="9001")
    db.update_manual_library_match_track_id = lambda *a, **k: True
    lookup = build_bulk_override_lookup(db, 1, "jellyfin", {"9001"}, SOURCE_TRACKS)

    assert lookup("s1") == "9001"
    assert lookup.dead_match_source_ids == set()


def test_the_attribute_exists_on_the_per_row_fallback_too():
    """Stub DBs without the bulk readers take a different code path; callers
    read the attribute unconditionally, so it must always be there."""

    class _Minimal:
        def read_sync_match_cache(self, *_a):
            return None

    lookup = build_bulk_override_lookup(_Minimal(), 1, "plex", set(), SOURCE_TRACKS)
    assert lookup.dead_match_source_ids == set()
