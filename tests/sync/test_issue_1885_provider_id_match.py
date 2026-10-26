"""#1885: playlist sync cleared valid library links when the source artist
name used Latin characters and the local library used Cyrillic.

For every affected track the Spotify IDs and titles matched exactly and
the durations differed by less than a second, but the sync matchers only
matched on title/artist/album — the transliterated artist scored as a
different artist, the track was reported missing, and
``_record_library_membership`` wrote ``in_library=False`` /
``library_track_id=None`` over the saved link.

the fix checks the spotify id before the name-based matching. the id on a
library row is stamped by the enrichment worker from a name search, so it
isn't proof alone: the row also has to agree on title, duration, and artist
(loosely, through the cyrillic folds). artists in two different scripts
can't be compared, so there the id, title and a known duration carry it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.sync.provider_match import provider_id_library_match
from database.music_database import MusicDatabase


# ---------------------------------------------------------------------------
# provider_id_library_match — the shared corroboration helper
# ---------------------------------------------------------------------------

class _FakeDb:
    """A db with only explicit provider-ID surface — the singular getter
    plus the real title cleaner (no MagicMock auto-attrs, so a missing
    method or a stub-shaped row can't invent a match)."""

    def __init__(self, row):
        self.row = row
        self.calls = []

    def get_track_by_spotify_id(self, spotify_id, server_source=None):
        self.calls.append((spotify_id, server_source))
        return self.row

    # The real db's title cleaner, so the cleaned-title corroboration
    # route is exercised the way production runs it.
    def _clean_track_title_for_comparison(self, title):
        return MusicDatabase._clean_track_title_cached(title)


def _track(**kw):
    base = dict(name="Pesnya", artists=["Latin Name"], id="sp1885",
                duration_ms=200_000, album="Albom")
    base.update(kw)
    return SimpleNamespace(**base)


def _row(**kw):
    base = dict(id="lib-1", title="Pesnya", duration=200_800,
                artist_name="Кириллица", album_title="Albom",
                spotify_track_id="sp1885")
    base.update(kw)
    return SimpleNamespace(**base)


def test_exact_id_title_and_duration_match_despite_cyrillic_artist():
    db = _FakeDb(_row())
    row = provider_id_library_match(db, _track(), "soulsync")
    assert row is not None and row.id == "lib-1"
    assert db.calls == [("sp1885", "soulsync")]


def test_source_track_id_is_tried_when_catalogue_id_misses():
    db = _FakeDb(None)
    seen = []

    def getter(spotify_id, server_source=None):
        seen.append(spotify_id)
        return _row(spotify_track_id="mirror-9") if spotify_id == "mirror-9" else None

    db.get_track_by_spotify_id = getter
    track = _track(id="sp1885", source_track_id="mirror-9")
    row = provider_id_library_match(db, track, "soulsync")
    assert row is not None
    assert seen == ["sp1885", "mirror-9"]


def test_title_mismatch_rejects_the_row():
    # An ID stamped on a DIFFERENT song (bad provenance) must not pin it.
    db = _FakeDb(_row(title="Completely Different Song"))
    assert provider_id_library_match(db, _track(), "soulsync") is None


def test_duration_mismatch_rejects_the_row():
    # Same title, same stamped ID, but a 40s-longer recording: a
    # different edit, not the <1s difference the report describes.
    db = _FakeDb(_row(duration=240_000))
    assert provider_id_library_match(db, _track(), "soulsync") is None


def test_unknown_duration_on_either_side_does_not_block():
    # artists agree here, so an unknown duration is no evidence either way
    db = _FakeDb(_row(duration=None, artist_name="Latin Name"))
    assert provider_id_library_match(db, _track(), "soulsync") is not None
    db2 = _FakeDb(_row(artist_name="Latin Name"))
    assert provider_id_library_match(db2, _track(duration_ms=0), "soulsync") is not None


def test_cross_script_artist_needs_both_durations():
    # latin source, cyrillic library artist: no name evidence at all, so the
    # duration has to really be there on both sides
    assert provider_id_library_match(_FakeDb(_row()), _track(), "soulsync") is not None
    assert provider_id_library_match(_FakeDb(_row(duration=None)), _track(), "soulsync") is None
    assert provider_id_library_match(_FakeDb(_row()), _track(duration_ms=0), "soulsync") is None


def test_row_carrying_a_different_spotify_id_is_rejected():
    db = _FakeDb(_row(spotify_track_id="someone-else"))
    assert provider_id_library_match(db, _track(), "soulsync") is None


def test_wing_it_stub_id_is_never_looked_up():
    db = _FakeDb(_row())
    track = _track(id="wing_it_1234", source_track_id=None)
    assert provider_id_library_match(db, track, "soulsync") is None
    assert db.calls == []


def test_missing_getter_and_stub_shaped_rows_are_misses():
    assert provider_id_library_match(object(), _track(), "soulsync") is None
    # A MagicMock row has no real (str/int) id — it proves nothing.
    db = _FakeDb(MagicMock())
    assert provider_id_library_match(db, _track(), "soulsync") is None
    db2 = _FakeDb(_row(id=None))
    assert provider_id_library_match(db2, _track(), "soulsync") is None


def test_getter_error_is_a_miss_not_a_crash():
    db = _FakeDb(None)

    def boom(spotify_id, server_source=None):
        raise RuntimeError("db down")

    db.get_track_by_spotify_id = boom
    assert provider_id_library_match(db, _track(), "soulsync") is None


def test_cyrillic_title_matches_its_spotify_romanization():
    # Library title in Cyrillic, source title romanized the Spotify way
    # (я→"ya"); Unidecode alone keys them "pesnia" vs "pesnya".
    db = _FakeDb(_row(title="Песня"))
    row = provider_id_library_match(db, _track(name="Pesnya"), "soulsync")
    assert row is not None and row.id == "lib-1"


def test_single_letter_kha_cyrillic_folds():
    # х→"h" ("Horosho", not only "Khorosho") — the H fold table.
    db = _FakeDb(_row(title="Хорошо"))
    assert provider_id_library_match(db, _track(name="Horosho"), "soulsync") is not None
    db2 = _FakeDb(_row(title="Хелло"))
    assert provider_id_library_match(db2, _track(name="Hello"), "soulsync") is not None


def test_cyrillic_titles_never_agree_through_the_raw_unidecode_key():
    # Production normalize_key uses Unidecode, which romanizes both
    # й and и to "i" — the raw keys of Мой and Мои are identical.
    # Cyrillic titles are therefore keyed only through the fold
    # tables, which keep й ("y") and и ("i") apart, while a Latin
    # title still meets each Cyrillic title's fold in Latin space.
    from core.sync.provider_match import provider_titles_agree
    assert not provider_titles_agree(None, "Мой", "Мои")
    assert provider_titles_agree(None, "Мой", "Moy")
    assert provider_titles_agree(None, "Мои", "Moi")
    db = _FakeDb(_row(title="Мои"))
    assert provider_id_library_match(db, _track(name="Мой"), "soulsync") is None


def test_plural_getter_returning_a_single_dict_row_still_counts():
    class _DictPluralDb:
        def get_tracks_by_spotify_id(self, sid, server_source=None):
            return {"id": "lib-9", "title": "Pesnya", "duration": 200_500,
                    "spotify_track_id": "sp1885", "artist_name": "Latin Name"}

    row = provider_id_library_match(_DictPluralDb(), _track(), "soulsync")
    assert row is not None and row["id"] == "lib-9"


def test_folds_never_merge_distinct_cyrillic_titles():
    # A fold that maps two different letters to the same Latin string
    # would make different titles "agree" and defeat corroboration:
    # г/х must never both land on "h" (Голод hunger vs Холод cold),
    # and щ must never land on ш's "sh" (Щина vs Шина).
    from core.sync.provider_match import provider_titles_agree
    assert not provider_titles_agree(None, "Голод", "Холод")
    assert not provider_titles_agree(None, "Щина", "Шина")
    db = _FakeDb(_row(title="Холод"))
    assert provider_id_library_match(db, _track(name="Голод"), "soulsync") is None


def test_mixed_russian_romanization_with_single_letter_kha():
    # и→"i" with х→"h": Михаил is "Mihail" — the main table spells it
    # "Mikhail", the loose table "Myhayl"; neither key agrees.
    db = _FakeDb(_row(title="Михаил"))
    assert provider_id_library_match(db, _track(name="Mihail"), "soulsync") is not None
    db2 = _FakeDb(_row(title="Хи"))
    assert provider_id_library_match(db2, _track(name="Hi"), "soulsync") is not None


def test_yo_letter_matches_both_romanizations():
    # ё is romanized "yo" (Yolka) and "e" (Elka) in the wild — both folds
    # are keyed, so either source spelling corroborates.
    db = _FakeDb(_row(title="Ёлка"))
    assert provider_id_library_match(db, _track(name="Yolka"), "soulsync") is not None
    db2 = _FakeDb(_row(title="Ёлка"))
    assert provider_id_library_match(db2, _track(name="Elka"), "soulsync") is not None


def test_feat_and_remaster_suffixes_do_not_block_corroboration():
    db = _FakeDb(_row(title="Pesnya"))
    track = _track(name="Pesnya (feat. Someone)")
    assert provider_id_library_match(db, track, "soulsync") is not None
    db2 = _FakeDb(_row(title="Pesnya - Remastered 2020"))
    assert provider_id_library_match(db2, _track(name="Pesnya"), "soulsync") is not None


def test_duration_tolerance_boundary():
    db = _FakeDb(_row(duration=202_000))  # exactly 2s over — within tolerance
    assert provider_id_library_match(db, _track(), "soulsync") is not None
    db2 = _FakeDb(_row(duration=202_001))  # 1ms past it — a different edit
    assert provider_id_library_match(db2, _track(), "soulsync") is None


def test_dict_rows_are_corroborated_including_duration():
    good = {"id": "lib-9", "title": "Pesnya", "duration": 200_500,
            "spotify_track_id": "sp1885", "artist_name": "Latin Name"}
    db = _FakeDb(good)
    row = provider_id_library_match(db, _track(), "soulsync")
    assert row is not None and row["id"] == "lib-9"
    bad = dict(good, duration=999_000)  # dict shape must not skip the check
    db2 = _FakeDb(bad)
    assert provider_id_library_match(db2, _track(), "soulsync") is None


def test_first_lookup_id_failing_corroboration_falls_to_the_second():
    rows = {"sp1885": _row(title="Wrong Song", spotify_track_id="sp1885"),
            "mirror-9": _row(id="lib-2", spotify_track_id="mirror-9")}
    db = _FakeDb(None)
    db.get_track_by_spotify_id = lambda sid, server_source=None: rows.get(sid)
    track = _track(id="sp1885", source_track_id="mirror-9")
    row = provider_id_library_match(db, track, "soulsync")
    assert row is not None and row.id == "lib-2"


def test_duplicate_rows_second_one_can_corroborate():
    # Same Spotify ID on two rows: a poison-stamped one (wrong title) and
    # the real one. The plural getter's rows are considered in order.
    class _PluralDb:
        def get_tracks_by_spotify_id(self, sid, server_source=None):
            return [_row(id="lib-bad", title="Wrong Song"),
                    _row(id="lib-good", title="Pesnya")]

    row = provider_id_library_match(_PluralDb(), _track(), "soulsync")
    assert row is not None and row.id == "lib-good"


def test_plural_generator_consumed_and_plural_failure_falls_back_to_singular():
    class _GenDb:
        def get_tracks_by_spotify_id(self, sid, server_source=None):
            return (r for r in [_row()])

    assert provider_id_library_match(_GenDb(), _track(), "soulsync") is not None

    class _RaisingPluralDb:
        def get_tracks_by_spotify_id(self, sid, server_source=None):
            raise RuntimeError("broken plural")

        def get_track_by_spotify_id(self, sid, server_source=None):
            return _row()

    assert provider_id_library_match(_RaisingPluralDb(), _track(), "soulsync") is not None


def test_matches_returns_every_corroborated_row_in_order():
    from core.sync.provider_match import provider_id_library_matches

    class _PluralDb:
        def get_tracks_by_spotify_id(self, sid, server_source=None):
            return [_row(id="lib-bad", title="Wrong Song"),
                    _row(id="lib-1"), _row(id="lib-2")]

    rows = provider_id_library_matches(_PluralDb(), _track(), "soulsync")
    assert [r.id for r in rows] == ["lib-1", "lib-2"]


def test_getters_without_the_server_source_kwarg_are_still_asked():
    # A foreign/stub getter whose signature has no server_source must be
    # called with the ID alone, not written off as a miss.
    class _SingularNoKwargDb:
        def get_track_by_spotify_id(self, sid):
            return _row()

    assert provider_id_library_match(_SingularNoKwargDb(), _track(), "soulsync") is not None

    class _PluralNoKwargDb:
        def get_tracks_by_spotify_id(self, sid):
            return [_row()]

    assert provider_id_library_match(_PluralNoKwargDb(), _track(), "soulsync") is not None


# ---------------------------------------------------------------------------
# Database-only sync matcher (core/discovery/sync.py)
# ---------------------------------------------------------------------------

import core.discovery.sync as discovery_sync  # noqa: E402


def _run_db_only(track, fake_db):
    fake_cm = MagicMock()
    fake_cm.get_active_media_server.return_value = "soulsync"
    with patch("database.music_database.MusicDatabase", return_value=fake_db), \
         patch("core.settings.config_manager", fake_cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1):
        return asyncio.run(discovery_sync._database_only_find_track(track, candidate_pool={}))


def _db_only_fake(provider_row):
    db = MagicMock()
    db.read_sync_match_cache.return_value = None
    db.find_manual_library_match_by_source_track_id.return_value = None
    db.check_track_exists.return_value = (None, 0.0)  # fuzzy fails: Cyrillic artist
    db.get_tracks_by_spotify_id.return_value = [provider_row] if provider_row else []
    db.get_server_match_rejections_strict.return_value = set()
    return db


def test_durations_agree_ms_semantics_are_pinned():
    from core.sync.provider_match import durations_agree_ms
    assert durations_agree_ms(200_000, 201_999)
    assert not durations_agree_ms(200_000, 202_001)
    # A numeric string counts as its number.
    assert durations_agree_ms("200500", 200_000)
    # Anything not a positive finite number is unknown == agrees:
    # None, bool, non-numeric, zero/negative, NaN, inf.
    for unknown in (None, True, "abc", 0, -5, float("nan"), float("inf")):
        assert durations_agree_ms(unknown, 200_000), unknown
        assert durations_agree_ms(200_000, unknown), unknown


def test_provider_match_rejected_with_only_the_lenient_getter():
    # A db without the strict getter cannot prove "no rejections" —
    # a lenient empty set is not evidence. (An explicit class, not a
    # MagicMock: auto-attrs would invent the strict getter.)
    from core.sync.provider_match import provider_match_rejected

    class _LenientOnlyDb:
        def get_server_match_rejections(self, profile_id, server_source, ids):
            return set()

    assert provider_match_rejected(_LenientOnlyDb(), 1, "soulsync", _track(), "lib-1") is True


def test_provider_match_rejected_without_profile_or_ids_is_unknown():
    from core.sync.provider_match import provider_match_rejected
    db = _db_only_fake(_row())
    assert provider_match_rejected(db, None, "soulsync", _track(), "lib-1") is True
    assert provider_match_rejected(db, 0, "soulsync", _track(), "lib-1") is True
    no_ids = _track(id=None, source_track_id=None)
    assert provider_match_rejected(db, 1, "soulsync", no_ids, "lib-1") is True


def test_db_only_matcher_honors_provider_id_when_fuzzy_fails():
    track = _track()
    row = _row(id="t77", title="Pesnya")
    db = _db_only_fake(row)
    match, conf = _run_db_only(track, db)
    assert conf == 1.0 and match.id == "t77"
    # The returned object carries the provider row's artist/album, so
    # downstream consumers reading .artist/.album see the library's.
    assert match.artist == "Кириллица" and match.album == "Albom"
    # No sync_match_cache write: a 1.0 row reads as user-confirmed in the
    # compare view and the #1721 stale gate would drop it every run.
    db.save_sync_match_cache.assert_not_called()


def test_db_only_matcher_falls_through_when_provider_row_fails_corroboration():
    track = _track()
    row = _row(id="t77", title="Not The Same Song")
    assert _run_db_only(track, _db_only_fake(row)) == (None, 0.0)


def test_db_only_matcher_does_not_re_pin_an_unmatched_pairing():
    track = _track()
    row = _row(id="t77", title="Pesnya")
    db = _db_only_fake(row)
    db.get_server_match_rejections_strict.return_value = {("sp1885", "t77")}
    assert _run_db_only(track, db) == (None, 0.0)
    db.save_sync_match_cache.assert_not_called()


def test_db_only_matcher_honors_cyrillic_title_end_to_end():
    track = _track(name="Pesnya")
    row = _row(id="t78", title="Песня")
    match, conf = _run_db_only(track, _db_only_fake(row))
    assert conf == 1.0 and match.id == "t78"


def test_db_only_matcher_rejection_check_failure_fails_closed():
    track = _track()
    row = _row(id="t77", title="Pesnya")
    db = _db_only_fake(row)
    fake_cm = MagicMock()
    fake_cm.get_active_media_server.return_value = "soulsync"
    with patch("database.music_database.MusicDatabase", return_value=db), \
         patch("core.settings.config_manager", fake_cm), \
         patch("core.artists.map.get_current_profile_id", side_effect=RuntimeError("no profile")):
        result = asyncio.run(discovery_sync._database_only_find_track(track, candidate_pool={}))
    assert result == (None, 0.0)


def test_db_only_matcher_tries_the_next_row_when_the_first_is_unmatched():
    # Same Spotify ID on two rows: the user unmatched the first, the
    # second is the live match — iteration must reach it.
    track = _track()
    first = _row(id="t77", title="Pesnya")
    second = _row(id="t78", title="Pesnya")
    db = _db_only_fake(first)
    db.get_tracks_by_spotify_id.return_value = [first, second]
    db.get_server_match_rejections_strict.return_value = {("sp1885", "t77")}
    match, conf = _run_db_only(track, db)
    assert conf == 1.0 and match.id == "t78"


def test_db_only_matcher_rejection_check_uses_the_syncs_own_profile():
    # The matcher runs patched onto sync_service, whose syncs carry
    # their profile in a contextvar: that profile — not the ambient
    # one — is whose rejections count.
    from services.sync_service import _sync_profile_id
    track = _track()
    row = _row(id="t77", title="Pesnya")
    db = _db_only_fake(row)
    token = _sync_profile_id.set(42)
    try:
        match, conf = _run_db_only(track, db)  # ambient profile patched to 1
    finally:
        _sync_profile_id.reset(token)
    assert conf == 1.0 and match.id == "t77"
    assert db.get_server_match_rejections_strict.call_args[0][0] == 42


def test_db_only_matcher_rejection_query_failure_fails_closed():
    # The lenient is_rejected_match swallows this error and returns
    # False; the provider guard must not — unknown rejection state
    # means the pairing is not honored.
    track = _track()
    row = _row(id="t77", title="Pesnya")
    db = _db_only_fake(row)
    db.get_server_match_rejections_strict.side_effect = RuntimeError("db locked")
    assert _run_db_only(track, db) == (None, 0.0)


def test_db_only_cache_hit_still_wins_over_provider_match():
    # Precedence pin: sync_match_cache (fast-path) > provider-ID > fuzzy.
    track = _track()
    cache_dt = _row(id="t-cache", title="Pesnya")
    provider_dt = _row(id="t-prov", title="Pesnya")
    db = _db_only_fake(provider_dt)
    db.read_sync_match_cache.return_value = {
        "server_track_id": "t-cache", "confidence": 0.9}
    db.get_track_by_id.side_effect = lambda i: cache_dt if str(i) == "t-cache" else provider_dt
    db.check_track_exists.side_effect = (
        lambda *a, **k: (cache_dt, 0.95) if k.get("candidate_tracks") else (None, 0.0))
    match, conf = _run_db_only(track, db)
    assert match.id == "t-cache" and conf == 0.9


def test_db_only_manual_match_still_wins_over_provider_match():
    # Precedence pin: durable manual match (fast-path) > provider-ID.
    track = _track()
    manual_dt = _row(id="t-manual", title="Pesnya")
    provider_dt = _row(id="t-prov", title="Pesnya")
    db = _db_only_fake(provider_dt)
    db.find_manual_library_match_by_source_track_id.return_value = {
        "library_track_id": "t-manual", "library_file_path": "/m/x.flac"}
    db.get_track_by_id.side_effect = lambda i: manual_dt if str(i) == "t-manual" else provider_dt
    match, conf = _run_db_only(track, db)
    assert match.id == "t-manual" and conf == 1.0


# ---------------------------------------------------------------------------
# Media-server sync matcher (services/sync_service.py)
# ---------------------------------------------------------------------------

from services.sync_service import PlaylistSyncService  # noqa: E402


def _service():
    svc = PlaylistSyncService(spotify_client=MagicMock(), download_orchestrator=MagicMock(),
                              media_server_engine=MagicMock())
    client = MagicMock(); client.is_connected.return_value = True
    svc._get_active_media_client = lambda: (client, "jellyfin")
    svc._cancelled = False
    return svc


def _media_fake(provider_row):
    db = MagicMock()
    db.read_sync_match_cache.return_value = None
    db.find_manual_library_match_by_source_track_id.return_value = None
    db.check_track_exists.return_value = (None, 0.0)  # fuzzy fails: Cyrillic artist
    db.get_tracks_by_spotify_id.return_value = [provider_row] if provider_row else []
    db.get_server_match_rejections_strict.return_value = set()
    return db


def _run_media(track, db):
    cm = MagicMock(); cm.get_active_media_server.return_value = "jellyfin"
    with patch("database.music_database.MusicDatabase", return_value=db), \
         patch("core.settings.config_manager", cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1):
        return asyncio.run(_service()._find_track_in_media_server(track, candidate_pool={}))


def test_media_matcher_honors_provider_id_when_fuzzy_fails():
    track = _track()
    dt = _row(id="t88", title="Pesnya")
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    match, conf = _run_media(track, db)
    assert conf == 1.0 and match.id == "t88"


def test_media_matcher_does_not_re_pin_an_unmatched_pairing():
    track = _track()
    dt = _row(id="t88", title="Pesnya")
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    db.get_server_match_rejections_strict.return_value = {("sp1885", "t88")}
    assert _run_media(track, db) == (None, 0.0)


def test_media_matcher_rejection_query_failure_fails_closed():
    track = _track()
    dt = _row(id="t88", title="Pesnya")
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    db.get_server_match_rejections_strict.side_effect = RuntimeError("db locked")
    assert _run_media(track, db) == (None, 0.0)


def test_media_matcher_rejection_check_uses_the_syncs_own_profile():
    from services.sync_service import _sync_profile_id
    track = _track()
    dt = _row(id="t88", title="Pesnya")
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    token = _sync_profile_id.set(42)
    try:
        match, conf = _run_media(track, db)  # ambient profile patched to 1
    finally:
        _sync_profile_id.reset(token)
    assert conf == 1.0 and match.id == "t88"
    assert db.get_server_match_rejections_strict.call_args[0][0] == 42


def test_media_matcher_tries_the_next_row_when_the_first_is_unmatched():
    track = _track()
    first = _row(id="t88", title="Pesnya")
    second = _row(id="t89", title="Pesnya")
    db = _media_fake(first)
    db.get_tracks_by_spotify_id.return_value = [first, second]
    db.get_track_by_id.side_effect = (
        lambda i: {"t88": first, "t89": second}.get(str(i)))
    db.get_server_match_rejections_strict.return_value = {("sp1885", "t88")}
    match, conf = _run_media(track, db)
    assert conf == 1.0 and match.id == "t89"


def test_media_matcher_tries_the_next_row_when_the_first_cannot_materialize():
    track = _track()
    first = _row(id="t88", title="Pesnya")
    second = _row(id="t89", title="Pesnya")
    db = _media_fake(first)
    db.get_tracks_by_spotify_id.return_value = [first, second]
    db.get_track_by_id.side_effect = (
        lambda i: second if str(i) == "t89" else None)
    match, conf = _run_media(track, db)
    assert conf == 1.0 and match.id == "t89"


def test_media_matcher_cancelled_sync_never_reaches_the_provider_path():
    track = _track()
    dt = _row(id="t88", title="Pesnya")
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    svc = _service()
    svc._cancelled = True
    cm = MagicMock(); cm.get_active_media_server.return_value = "jellyfin"
    with patch("database.music_database.MusicDatabase", return_value=db), \
         patch("core.settings.config_manager", cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1):
        result = asyncio.run(svc._find_track_in_media_server(track, candidate_pool={}))
    assert result == (None, 0.0)
    db.get_tracks_by_spotify_id.assert_not_called()


def test_media_manual_match_still_wins_over_provider_match():
    # Precedence pin: durable manual match (fast-path) > provider-ID.
    track = _track()
    manual_dt = _row(id="t-manual", title="Pesnya")
    provider_dt = _row(id="t-prov", title="Pesnya")
    db = _media_fake(provider_dt)
    db.find_manual_library_match_by_source_track_id.return_value = {
        "library_track_id": "t-manual", "library_file_path": "/m/x.flac"}
    db.get_track_by_id.side_effect = (
        lambda i: manual_dt if str(i) == "t-manual" else provider_dt)
    match, conf = _run_media(track, db)
    assert match.id == "t-manual" and conf == 1.0


def _plex_service():
    svc = PlaylistSyncService(spotify_client=MagicMock(), download_orchestrator=MagicMock(),
                              media_server_engine=MagicMock())
    client = MagicMock(); client.is_connected.return_value = True
    svc._get_active_media_client = lambda: (client, "plex")
    svc._cancelled = False
    return svc


def _run_media_plex(track, db, rescued):
    cm = MagicMock(); cm.get_active_media_server.return_value = "plex"
    with patch("database.music_database.MusicDatabase", return_value=db), \
         patch("core.settings.config_manager", cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1), \
         patch("services.sync_service.rescue_stale_plex_track", return_value=rescued):
        return asyncio.run(_plex_service()._find_track_in_media_server(track, candidate_pool={}))


def test_media_plex_rescue_honored_only_when_it_corroborates():
    track = _track()
    dt = _row(id="t88", title="Pesnya")  # int("t88") fails in _materialize → rescue path
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    good = SimpleNamespace(ratingKey=999, title="Pesnya", duration=200_500)
    match, conf = _run_media_plex(track, db, good)
    assert conf == 1.0 and match.ratingKey == 999
    # A blind top search hit with a different title must NOT ride on the
    # provider ID's authority.
    wrong = SimpleNamespace(ratingKey=1000, title="Totally Other Song", duration=200_500)
    assert _run_media_plex(track, db, wrong) == (None, 0.0)
    # Nor one with a contradicting duration.
    long_cut = SimpleNamespace(ratingKey=1001, title="Pesnya", duration=400_000)
    assert _run_media_plex(track, db, long_cut) == (None, 0.0)


def test_media_plex_rescue_not_honored_when_rescued_key_was_unmatched():
    track = _track()
    dt = _row(id="t88", title="Pesnya")
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    db.get_server_match_rejections_strict.return_value = {("sp1885", "999")}
    rescued = SimpleNamespace(ratingKey=999, title="Pesnya", duration=200_500)
    assert _run_media_plex(track, db, rescued) == (None, 0.0)


def test_media_plex_rescue_not_honored_for_a_different_artist():
    # Same title, plausible duration — but the live track is another
    # artist's namesake song. It must not ride on the provider ID.
    track = _track()
    dt = _row(id="t88", title="Pesnya")  # artist_name "Кириллица"
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    namesake = SimpleNamespace(ratingKey=999, title="Pesnya", duration=200_500,
                               grandparentTitle="Someone Else")
    assert _run_media_plex(track, db, namesake) == (None, 0.0)
    same_artist = SimpleNamespace(ratingKey=999, title="Pesnya", duration=200_500,
                                  grandparentTitle="Кириллица")
    match, conf = _run_media_plex(track, db, same_artist)
    assert conf == 1.0 and match.ratingKey == 999


def test_media_plex_rescue_unknown_duration_is_no_evidence():
    # A Plex track with no duration cannot contradict the source's —
    # unknown agrees, the same rule the provider helper applies.
    track = _track()
    dt = _row(id="t88", title="Pesnya")
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    rescued = SimpleNamespace(ratingKey=999, title="Pesnya", duration=None)
    match, conf = _run_media_plex(track, db, rescued)
    assert conf == 1.0 and match.ratingKey == 999


def test_media_plex_rescue_honors_a_dict_provider_row():
    # Dict-shaped provider rows (the helper tolerates them) must reach
    # the rescue with their title/artist intact, not as empty strings.
    track = _track()
    dt = {"id": "t88", "title": "Pesnya", "duration": 200_800,
          "spotify_track_id": "sp1885", "artist_name": "Кириллица"}
    db = _media_fake(dt)
    db.get_track_by_id.return_value = None  # stale key: cannot materialize
    rescued = SimpleNamespace(ratingKey=999, title="Pesnya", duration=200_500,
                              grandparentTitle="Кириллица")
    match, conf = _run_media_plex(track, db, rescued)
    assert conf == 1.0 and match.ratingKey == 999


def test_media_plex_rescue_plexapi_artist_method_is_no_evidence():
    # Shaped like a real plexapi Track for this check: no
    # grandparentTitle string, and .artist is a METHOD — that shape
    # must not read as a disagreeing artist name and kill a rescue.
    class _PlexLikeTrack:
        ratingKey = 999
        title = "Pesnya"
        duration = 200_500

        def artist(self):
            return "method, not a name"

    track = _track()
    dt = _row(id="t88", title="Pesnya")
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    match, conf = _run_media_plex(track, db, _PlexLikeTrack())
    assert conf == 1.0 and match.ratingKey == 999


def test_media_plex_rescue_duration_checked_against_the_provider_row():
    # The source track's duration is unknown here, so the provider
    # row's own duration is the evidence the rescue must agree with.
    track = _track(duration_ms=0)
    dt = _row(id="t88", title="Pesnya", duration=200_800, artist_name="Latin Name")
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    wrong_cut = SimpleNamespace(ratingKey=999, title="Pesnya", duration=400_000)
    assert _run_media_plex(track, db, wrong_cut) == (None, 0.0)
    right_cut = SimpleNamespace(ratingKey=999, title="Pesnya", duration=201_000)
    match, conf = _run_media_plex(track, db, right_cut)
    assert conf == 1.0 and match.ratingKey == 999


def test_media_plex_rescue_various_artists_is_no_evidence():
    # grandparentTitle is the ALBUM artist: on a compilation it reads
    # "Various Artists" while the row carries the track artist. VA is
    # not an identity, so it cannot disagree.
    track = _track()
    dt = _row(id="t88", title="Pesnya")
    db = _media_fake(dt)
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t88" else None
    rescued = SimpleNamespace(ratingKey=999, title="Pesnya", duration=200_500,
                              grandparentTitle="Various Artists")
    match, conf = _run_media_plex(track, db, rescued)
    assert conf == 1.0 and match.ratingKey == 999


def test_rescue_prefers_the_provider_rows_exact_file():
    # The provider row's file_path (now carried by the getter) lets the
    # rescue pick the exact file over the search's top hit.
    from services.sync_service import rescue_stale_plex_track

    def _live(rating_key, file):
        return SimpleNamespace(
            ratingKey=rating_key,
            media=[SimpleNamespace(parts=[SimpleNamespace(file=file)])])

    wrong = SimpleNamespace(_original_plex_track=_live(111, "/music/other.flac"))
    right = SimpleNamespace(_original_plex_track=_live(222, "/music/lib/p.flac"))
    client = MagicMock()
    client.search_tracks.return_value = [wrong, right]
    db_track = SimpleNamespace(title="Pesnya", file_path="/music/lib/p.flac")
    rescued = rescue_stale_plex_track(client, db_track, "Кириллица")
    assert rescued is not None and rescued.ratingKey == 222


def test_provider_titles_agree_survives_a_normalize_key_failure():
    from core.sync.provider_match import provider_titles_agree
    with patch("core.text.normalize.normalize_key", side_effect=RuntimeError("no unidecode")):
        assert provider_titles_agree(None, "Pesnya", "Pesnya") is True
        assert provider_titles_agree(None, "Pesnya", "Other Song") is False


def test_media_jellyfin_unmaterializable_row_falls_through_to_fuzzy():
    # Provider row exists but the library object can't be materialized
    # and there's no Plex rescue — the matcher must not invent a hit;
    # it falls through (here to an empty fuzzy result).
    track = _track()
    dt = _row(id="t88", title="Pesnya")
    db = _media_fake(dt)
    db.get_track_by_id.return_value = None
    assert _run_media(track, db) == (None, 0.0)


# ---------------------------------------------------------------------------
# MusicDatabase.get_track_by_spotify_id — the real lookup
# ---------------------------------------------------------------------------

@pytest.fixture
def real_db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _seed_library_track(db, *, spotify_id="sp1885", server_source="soulsync"):
    with db._get_connection() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Кириллица')")
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (1, 1, 'Albom')")
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, duration, file_path,"
            " server_source, spotify_track_id)"
            " VALUES ('lib-1', 1, 1, 'Pesnya', 200800, '/m/p.flac', ?, ?)",
            (server_source, spotify_id))


def test_get_track_by_spotify_id_finds_cyrillic_filed_track(real_db):
    _seed_library_track(real_db)
    row = real_db.get_track_by_spotify_id("sp1885", server_source="soulsync")
    assert row is not None
    assert row.id == "lib-1" and row.title == "Pesnya"
    assert row.artist_name == "Кириллица" and row.duration == 200800
    assert row.spotify_track_id == "sp1885"


def test_get_track_by_spotify_id_scopes_to_server_and_misses_cleanly(real_db):
    _seed_library_track(real_db)
    assert real_db.get_track_by_spotify_id("sp1885", server_source="plex") is None
    assert real_db.get_track_by_spotify_id("nope") is None
    assert real_db.get_track_by_spotify_id("") is None
    assert real_db.get_track_by_spotify_id(None) is None


def test_provider_match_rejected_against_the_real_rejection_table(real_db):
    # Against the real table and the real strict getter — this is the
    # path that must distinguish "no rejection" from "query failed".
    from core.sync.provider_match import provider_match_rejected
    _seed_library_track(real_db)
    track = _track()
    assert provider_match_rejected(real_db, 1, "soulsync", track, "lib-1") is False
    assert real_db.add_server_match_rejection(1, "soulsync", "sp1885", "lib-1")
    assert provider_match_rejected(real_db, 1, "soulsync", track, "lib-1") is True


def test_strict_rejection_getter_raises_when_the_database_fails(real_db, monkeypatch):
    # The whole point of the strict twin: a failed query is an error the
    # caller can see, not an empty set that reads as "no rejections" —
    # and provider_match_rejected turns that error into "do not honor".
    from core.sync.provider_match import provider_match_rejected
    _seed_library_track(real_db)
    monkeypatch.setattr(real_db, "_get_connection",
                        MagicMock(side_effect=RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        real_db.get_server_match_rejections_strict(1, "soulsync", ["sp1885"])
    assert provider_match_rejected(real_db, 1, "soulsync", _track(), "lib-1") is True


def test_real_getter_carries_the_file_path(real_db):
    _seed_library_track(real_db)
    row = real_db.get_track_by_spotify_id("sp1885", server_source="soulsync")
    assert row is not None and row.file_path == "/m/p.flac"


def test_media_matcher_end_to_end_against_a_real_database(real_db):
    _seed_library_track(real_db, server_source="jellyfin")
    track = _track(name="Pesnya", artists=["Latin Name"], duration_ms=200_000)
    svc = _service()
    fake_cm = MagicMock()
    fake_cm.get_active_media_server.return_value = "jellyfin"
    with patch("database.music_database.MusicDatabase", return_value=real_db), \
         patch("core.settings.config_manager", fake_cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1):
        match, conf = asyncio.run(svc._find_track_in_media_server(track, candidate_pool={}))
    assert conf == 1.0 and str(match.ratingKey) == "lib-1"


def test_db_only_matcher_end_to_end_against_a_real_database(real_db):
    # The full sync path — real scope SQL, real getters, real rejection
    # table — not a MagicMock db. This is the #1885 scenario verbatim:
    # Latin source artist, Cyrillic library artist, exact Spotify ID.
    _seed_library_track(real_db)
    track = _track(name="Pesnya", artists=["Latin Name"], duration_ms=200_000)
    fake_cm = MagicMock()
    fake_cm.get_active_media_server.return_value = "soulsync"
    with patch("database.music_database.MusicDatabase", return_value=real_db), \
         patch("core.settings.config_manager", fake_cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1):
        match, conf = asyncio.run(
            discovery_sync._database_only_find_track(track, candidate_pool={}))
    assert conf == 1.0 and match.id == "lib-1"


def test_duplicate_spotify_id_rows_all_surface_in_id_order(real_db):
    _seed_library_track(real_db)
    with real_db._get_connection() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, duration, file_path,"
            " server_source, spotify_track_id)"
            " VALUES ('lib-0', 1, 1, 'Wrong Song', 200800, '/m/p0.flac', 'soulsync', 'sp1885')")
    rows = real_db.get_tracks_by_spotify_id("sp1885", server_source="soulsync")
    assert [r.id for r in rows] == ["lib-0", "lib-1"]
    assert all(r.spotify_track_id == "sp1885" for r in rows)
    # The helper picks the corroborating duplicate (lib-1), not the
    # poison-stamped first row carrying the same ID.
    track = _track(name="Pesnya", duration_ms=200_800)
    assert provider_id_library_match(real_db, track, "soulsync").id == "lib-1"


# ---------------------------------------------------------------------------
# artist gate: the stamped id is a name guess, not provenance
# ---------------------------------------------------------------------------

def test_artists_agree_through_the_cyrillic_folds():
    from core.sync.provider_match import artists_agree
    # unidecode spells й as "i", spotify as "y". the folds bridge it
    assert artists_agree(["Mumiy Troll"], ["Мумий Тролль"]) == "close"
    assert artists_agree(["Kino"], ["Кино"]) == "close"
    assert artists_agree(["Beyonce"], ["Beyoncé"]) == "close"
    # latin vs cyrillic with nothing in common still can't be compared
    assert artists_agree(["Boombox"], ["Бумбокс"]) in ("close", "cross_script")
    assert artists_agree(["Drake"], ["Metallica"]) is None
    # various artists is no evidence, and neither is a missing name
    assert artists_agree(["Drake"], ["Various Artists"]) is None
    assert artists_agree(["Drake"], [None, ""]) is None


def test_same_script_different_artist_is_not_honored():
    # the enrichment worker stamps spotify ids by title similarity with no
    # artist check, so an obscure "Intro" can carry another band's "Intro" id.
    # same title, same length, exact id, wrong band: must not count
    row = _row(title="Intro", artist_name="Metallica", duration=200_000)
    track = _track(name="Intro", artists=["The xx"], duration_ms=200_000)
    assert provider_id_library_match(_FakeDb(row), track, "soulsync") is None


def test_track_artist_counts_on_a_compilation_row():
    row = _row(artist_name="Various Artists", track_artist="Latin Name")
    assert provider_id_library_match(_FakeDb(row), _track(), "soulsync") is not None


def _seed(db, *, artist, title, spotify_id, duration, server="jellyfin", tid="lib-1"):
    with db._get_connection() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
                     (tid, artist, server))
        conn.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, 'Album', ?)",
                     (tid, tid, server))
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, duration, server_source,"
            " spotify_track_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, tid, tid, title, duration, server, spotify_id))


def _run_media_real(real_db, track):
    fake_cm = MagicMock()
    fake_cm.get_active_media_server.return_value = "jellyfin"
    with patch("database.music_database.MusicDatabase", return_value=real_db), \
         patch("core.settings.config_manager", fake_cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1):
        return asyncio.run(_service()._find_track_in_media_server(track, candidate_pool={}))


def test_issue_1885_real_fuzzy_miss_is_caught_by_the_id(real_db):
    """#1885 (glebosotov): latin source artist, cyrillic library artist. the
    real check_track_exists misses "Mumiy Troll" against "Мумий Тролль"
    (unidecode says "mumii"), so before the fix the sync called it missing
    and cleared the saved link. nothing is mocked on the matching side."""
    _seed(real_db, artist="Мумий Тролль", title="Utekay", spotify_id="sp-utekay",
          duration=220_400)
    miss, conf = real_db.check_track_exists("Utekay", "Mumiy Troll",
                                            confidence_threshold=0.7, server_source="jellyfin")
    assert miss is None  # the fuzzy matcher really can't see it
    track = _track(name="Utekay", artists=["Mumiy Troll"], id="sp-utekay", duration_ms=220_000)
    match, conf = _run_media_real(real_db, track)
    assert conf == 1.0 and str(match.ratingKey) == "lib-1"


def test_wrong_stamped_id_never_pins_another_bands_song(real_db):
    _seed(real_db, artist="Metallica", title="Intro", spotify_id="sp-intro", duration=120_000)
    track = _track(name="Intro", artists=["The xx"], id="sp-intro", duration_ms=120_000)
    assert _run_media_real(real_db, track) == (None, 0.0)

