from unittest.mock import MagicMock

from core.sync.match_overrides import record_manual_match, resolve_match_overrides


# ──────────────────────────────────────────────────────────────────────
# resolve_match_overrides — pre-pair source→server from cache
# ──────────────────────────────────────────────────────────────────────

def test_empty_inputs_return_empty_dict():
    assert resolve_match_overrides([], [], lambda _id: None) == {}
    assert resolve_match_overrides([{"source_track_id": "x"}], [], lambda _id: "y") == {}
    assert resolve_match_overrides([], [{"id": "y"}], lambda _id: None) == {}


def test_single_cache_hit_returns_pair():
    sources = [{"source_track_id": "spotify-iron-man", "name": "Iron Man - 2012 - Remaster"}]
    servers = [{"id": 5001, "title": "Iron Man"}]
    cache = {"spotify-iron-man": 5001}
    result = resolve_match_overrides(sources, servers, lambda sid: cache.get(sid))
    assert result == {0: 0}


def test_multiple_overrides_resolve_correctly():
    sources = [
        {"source_track_id": "iron"},
        {"source_track_id": "para"},
        {"source_track_id": "war"},
    ]
    servers = [
        {"id": 5001, "title": "Iron Man"},
        {"id": 5002, "title": "Paranoid"},
        {"id": 5003, "title": "War Pigs"},
    ]
    cache = {"iron": 5001, "para": 5002, "war": 5003}
    result = resolve_match_overrides(sources, servers, lambda sid: cache.get(sid))
    assert result == {0: 0, 1: 1, 2: 2}


def test_source_without_track_id_skipped():
    sources = [
        {"source_track_id": "iron", "name": "Iron Man"},
        {"name": "Paranoid"},  # no source_track_id (e.g. legacy / non-mirrored)
    ]
    servers = [{"id": 5001, "title": "Iron Man"}, {"id": 5002, "title": "Paranoid"}]
    cache = {"iron": 5001}
    result = resolve_match_overrides(sources, servers, lambda sid: cache.get(sid))
    assert result == {0: 0}


def test_cache_miss_skipped():
    sources = [{"source_track_id": "iron"}, {"source_track_id": "para"}]
    servers = [{"id": 5001, "title": "Iron Man"}, {"id": 5002, "title": "Paranoid"}]
    result = resolve_match_overrides(sources, servers, lambda sid: None)
    assert result == {}


def test_stale_cache_pointing_at_missing_server_track_skipped():
    # User cached a match → file got deleted from server → server_tracks
    # no longer has 5001 → don't pair, fall through to normal matching.
    sources = [{"source_track_id": "iron"}]
    servers = [{"id": 9999, "title": "Different Track"}]
    cache = {"iron": 5001}  # 5001 no longer exists
    result = resolve_match_overrides(sources, servers, lambda sid: cache.get(sid))
    assert result == {}


def test_server_id_str_int_coercion():
    # Cache might store ints, server_tracks might have str IDs (Plex
    # ratingKey is str). Helper coerces both sides to str.
    sources = [{"source_track_id": "iron"}]
    servers = [{"id": "5001", "title": "Iron Man"}]
    cache = {"iron": 5001}  # int from cache
    result = resolve_match_overrides(sources, servers, lambda sid: cache.get(sid))
    assert result == {0: 0}


def test_two_sources_pointing_at_same_server_track_only_first_wins():
    # Defensive — UNIQUE constraint prevents this in production but
    # cache_lookup is injectable so we verify the safety.
    sources = [{"source_track_id": "a"}, {"source_track_id": "b"}]
    servers = [{"id": 5001, "title": "Iron Man"}]
    cache = {"a": 5001, "b": 5001}
    result = resolve_match_overrides(sources, servers, lambda sid: cache.get(sid))
    assert result == {0: 0}


def test_cache_lookup_raising_treated_as_miss():
    sources = [{"source_track_id": "iron"}]
    servers = [{"id": 5001, "title": "Iron Man"}]
    def boom(_sid):
        raise RuntimeError("db down")
    result = resolve_match_overrides(sources, servers, boom)
    assert result == {}


def test_non_dict_source_or_server_skipped():
    sources = [None, "string", {"source_track_id": "iron"}]
    servers = [{"id": 5001, "title": "Iron Man"}]
    cache = {"iron": 5001}
    result = resolve_match_overrides(sources, servers, lambda sid: cache.get(sid))
    # source idx 2 → server idx 0
    assert result == {2: 0}


def test_server_without_id_skipped():
    sources = [{"source_track_id": "iron"}]
    servers = [{"title": "Iron Man"}]  # no id
    cache = {"iron": 5001}
    result = resolve_match_overrides(sources, servers, lambda sid: cache.get(sid))
    assert result == {}


def test_partial_cache_hits_only_pair_those():
    sources = [
        {"source_track_id": "iron"},
        {"source_track_id": "para"},
        {"source_track_id": "war"},
    ]
    servers = [
        {"id": 5001, "title": "Iron Man"},
        {"id": 5002, "title": "Paranoid"},
        {"id": 5003, "title": "War Pigs"},
    ]
    # Only iron + war cached, para falls through to normal matching
    cache = {"iron": 5001, "war": 5003}
    result = resolve_match_overrides(sources, servers, lambda sid: cache.get(sid))
    assert result == {0: 0, 2: 2}


# ──────────────────────────────────────────────────────────────────────
# record_manual_match — persist user-confirmed pair
# ──────────────────────────────────────────────────────────────────────

def test_record_persists_with_confidence_one():
    db = MagicMock()
    db.save_sync_match_cache.return_value = True
    ok = record_manual_match(
        db,
        source_track_id="spotify-iron-man",
        server_source="plex",
        server_track_id=5001,
        server_track_title="Iron Man",
        source_title="Iron Man - 2012 - Remaster",
        source_artist="Black Sabbath",
    )
    assert ok is True
    db.save_sync_match_cache.assert_called_once()
    kwargs = db.save_sync_match_cache.call_args.kwargs
    assert kwargs["spotify_track_id"] == "spotify-iron-man"
    assert kwargs["server_source"] == "plex"
    assert kwargs["server_track_id"] == 5001
    assert kwargs["server_track_title"] == "Iron Man"
    assert kwargs["confidence"] == 1.0
    assert kwargs["normalized_title"] == "iron man - 2012 - remaster"
    assert kwargs["normalized_artist"] == "black sabbath"


def test_record_returns_false_when_required_fields_missing():
    db = MagicMock()
    assert record_manual_match(db, source_track_id="", server_source="plex", server_track_id=1) is False
    assert record_manual_match(db, source_track_id="x", server_source="", server_track_id=1) is False
    assert record_manual_match(db, source_track_id="x", server_source="plex", server_track_id=None) is False
    db.save_sync_match_cache.assert_not_called()


def test_record_returns_false_when_db_save_returns_false():
    db = MagicMock()
    db.save_sync_match_cache.return_value = False
    assert record_manual_match(db, source_track_id="x", server_source="plex", server_track_id=1) is False


def test_record_swallows_db_exception():
    db = MagicMock()
    db.save_sync_match_cache.side_effect = RuntimeError("db boom")
    assert record_manual_match(db, source_track_id="x", server_source="plex", server_track_id=1) is False


def test_record_returns_false_when_db_lacks_method():
    class NoSaveDB:
        pass
    assert record_manual_match(NoSaveDB(), source_track_id="x", server_source="plex", server_track_id=1) is False


def test_record_handles_empty_optional_strings():
    db = MagicMock()
    db.save_sync_match_cache.return_value = True
    ok = record_manual_match(db, source_track_id="x", server_source="plex", server_track_id=1)
    assert ok is True
    kwargs = db.save_sync_match_cache.call_args.kwargs
    assert kwargs["normalized_title"] == ""
    assert kwargs["normalized_artist"] == ""
    assert kwargs["server_track_title"] == ""
