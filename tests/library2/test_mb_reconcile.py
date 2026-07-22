"""§62.6 Stufe 3: MusicBrainz release-group reconcile.

Two lib2 rows that are really the SAME release group (JP library pressing +
EN provider listing) get the RG MBID assigned and are folded together —
automatically only when the losing row is pristine, otherwise as a review
finding. No network: the MB client is a stub.
"""

from __future__ import annotations

import json

import pytest

from core.library2 import mb_reconcile as R


SAWANO_RG = "f17d521f-f8e9-41d8-9b0e-e270d5d905ed"


class FakeMBClient:
    def __init__(self, groups):
        self.groups = list(groups)
        self.calls = []

    def browse_artist_release_groups(self, artist_mbid, release_types=None,
                                     limit=100, offset=0):
        self.calls.append((artist_mbid, offset))
        return self.groups[offset:offset + limit]


def _artist_id(conn) -> int:
    return conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]


def _set_artist_mbid(conn, artist_id, mbid="60d2ea34-1912-425f-bf9c-fc544e4448cd"):
    conn.execute("UPDATE lib2_artists SET musicbrainz_id=? WHERE id=?", (mbid, artist_id))
    conn.commit()
    return mbid


def _seed_album(conn, artist_id, *, title, origin="library", release_date=None,
                expected_track_count=None, external_ids="{}", monitored=0,
                album_type="album"):
    cur = conn.execute(
        """INSERT INTO lib2_albums(primary_artist_id, title, album_type, origin,
               release_date, expected_track_count, external_ids, monitored)
           VALUES(?,?,?,?,?,?,?,?)""",
        (artist_id, title, album_type, origin, release_date,
         expected_track_count, external_ids, monitored))
    conn.execute(
        "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
        "VALUES(?,?, 'primary')", (cur.lastrowid, artist_id))
    conn.commit()
    return cur.lastrowid


def _sawano_groups():
    return [{
        "id": SAWANO_RG,
        "title": "TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        "primary-type": "Album",
        "first-release-date": "2017-06-07",
    }]


def test_no_mbid_skips_without_client_calls(legacy_db, imported_conn):
    client = FakeMBClient(_sawano_groups())
    stats = R.reconcile_artist_release_groups(
        legacy_db, _artist_id(imported_conn), client=client)
    assert stats["skipped"] == "no_mbid"
    assert client.calls == []


def test_title_match_assigns_release_group_mbid(legacy_db, imported_conn):
    aid = _artist_id(imported_conn)
    _set_artist_mbid(imported_conn, aid)
    album_id = _seed_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        release_date="2017-06-07", expected_track_count=33)

    stats = R.reconcile_artist_release_groups(
        legacy_db, aid, client=FakeMBClient(_sawano_groups()))

    assert stats["assigned"] == 1
    row = imported_conn.execute(
        "SELECT musicbrainz_id FROM lib2_albums WHERE id=?", (album_id,)).fetchone()
    assert row["musicbrainz_id"] == SAWANO_RG


def test_cross_language_duplicate_is_folded_into_library_row(legacy_db, imported_conn):
    """The full Sawano remediation: JP library row + EN pristine discography
    row → one row, RG MBID set, the EN row's provider id kept as edition."""
    aid = _artist_id(imported_conn)
    _set_artist_mbid(imported_conn, aid)
    jp_id = _seed_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        release_date="2017-06-07", expected_track_count=33,
        external_ids=json.dumps({"deezer": "196470602"}))
    en_id = _seed_album(
        imported_conn, aid,
        title='TV Anime "Attack on Titan Season 2" (Original Soundtrack)',
        origin="discography", release_date="2017-06-07",
        expected_track_count=33,
        external_ids=json.dumps({"deezer": "42695001"}))

    stats = R.reconcile_artist_release_groups(
        legacy_db, aid, client=FakeMBClient(_sawano_groups()))

    assert stats["merged"] == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE id=?", (en_id,)).fetchone()["c"] == 0
    survivor = imported_conn.execute(
        "SELECT musicbrainz_id, external_ids FROM lib2_albums WHERE id=?",
        (jp_id,)).fetchone()
    assert survivor["musicbrainz_id"] == SAWANO_RG
    # The survivor keeps its own deezer id; the alternative lives as edition.
    assert json.loads(survivor["external_ids"])["deezer"] == "196470602"
    alt = imported_conn.execute(
        "SELECT external_ids FROM lib2_release_editions "
        "WHERE release_group_id=? AND is_default=0", (jp_id,)).fetchall()
    assert any(json.loads(r["external_ids"]).get("deezer") == "42695001" for r in alt)


def test_non_pristine_duplicate_becomes_review_finding(legacy_db, imported_conn):
    aid = _artist_id(imported_conn)
    _set_artist_mbid(imported_conn, aid)
    jp_id = _seed_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        release_date="2017-06-07", expected_track_count=33)
    en_id = _seed_album(
        imported_conn, aid,
        title='TV Anime "Attack on Titan Season 2" (Original Soundtrack)',
        origin="discography", release_date="2017-06-07",
        expected_track_count=33, monitored=1)   # user intent → never auto-delete

    stats = R.reconcile_artist_release_groups(
        legacy_db, aid, client=FakeMBClient(_sawano_groups()))

    assert stats["merged"] == 0
    assert stats["review"] == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE id IN (?,?)",
        (jp_id, en_id)).fetchone()["c"] == 2
    finding = imported_conn.execute(
        "SELECT album_id, other_album_id, release_group_mbid, reason "
        "FROM lib2_release_group_review").fetchone()
    assert finding["release_group_mbid"] == SAWANO_RG
    assert {finding["album_id"], finding["other_album_id"]} == {jp_id, en_id}


def test_reconcile_is_idempotent(legacy_db, imported_conn):
    aid = _artist_id(imported_conn)
    _set_artist_mbid(imported_conn, aid)
    _seed_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        release_date="2017-06-07", expected_track_count=33,
        external_ids=json.dumps({"deezer": "196470602"}))
    _seed_album(
        imported_conn, aid,
        title='TV Anime "Attack on Titan Season 2" (Original Soundtrack)',
        origin="discography", release_date="2017-06-07",
        expected_track_count=33,
        external_ids=json.dumps({"deezer": "42695001"}))

    R.reconcile_artist_release_groups(legacy_db, aid, client=FakeMBClient(_sawano_groups()))
    stats2 = R.reconcile_artist_release_groups(
        legacy_db, aid, client=FakeMBClient(_sawano_groups()))

    assert stats2["assigned"] == 0
    assert stats2["merged"] == 0
    count = imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title LIKE '%Season 2%'"
    ).fetchone()["c"]
    assert count == 1


def test_date_fallback_requires_unique_release_group(legacy_db, imported_conn):
    """Two RGs sharing 2017-06-07 → no date-based assignment for a
    title-mismatched album (ambiguity guard)."""
    aid = _artist_id(imported_conn)
    _set_artist_mbid(imported_conn, aid)
    _seed_album(
        imported_conn, aid, title="Some Other Same-Day Album",
        origin="discography", release_date="2017-06-07", expected_track_count=33)

    groups = _sawano_groups() + [{
        "id": "11111111-2222-3333-4444-555555555555",
        "title": "Re:CREATORS (Original Soundtrack)",
        "primary-type": "Album",
        "first-release-date": "2017-06-07",
    }]
    stats = R.reconcile_artist_release_groups(
        legacy_db, aid, client=FakeMBClient(groups))

    assert stats["assigned"] == 0


def test_mismatched_track_counts_never_share_a_release_group(legacy_db, imported_conn):
    """A same-day 12-track release is NOT the 33-track album — the holder-
    count guard stops the date fallback from even assigning the RG, so no
    merge and no review noise."""
    aid = _artist_id(imported_conn)
    _set_artist_mbid(imported_conn, aid)
    _seed_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        release_date="2017-06-07", expected_track_count=33)
    other_id = _seed_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナル・サウンドトラック",
        origin="discography", release_date="2017-06-07",
        expected_track_count=12)   # clearly a different edition/album

    stats = R.reconcile_artist_release_groups(
        legacy_db, aid, client=FakeMBClient(_sawano_groups()))

    assert stats["merged"] == 0
    assert stats["review"] == 0
    assert imported_conn.execute(
        "SELECT musicbrainz_id FROM lib2_albums WHERE id=?",
        (other_id,)).fetchone()["musicbrainz_id"] is None


def test_default_client_unwraps_registry_search_adapter(
        legacy_db, imported_conn, monkeypatch):
    """The registry's MusicBrainz client is the SEARCH adapter wrapping the
    raw client — reconcile must reach browse_artist_release_groups through
    the wrapper (live-verify catch, §62)."""
    aid = _artist_id(imported_conn)
    _set_artist_mbid(imported_conn, aid)
    album_id = _seed_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        release_date="2017-06-07", expected_track_count=33)

    class Wrapper:  # shaped like MusicBrainzSearchClient
        def __init__(self):
            self._client = FakeMBClient(_sawano_groups())

    monkeypatch.setattr(
        "core.metadata.registry.get_musicbrainz_client", lambda: Wrapper())

    stats = R.reconcile_artist_release_groups(legacy_db, aid)

    assert stats["assigned"] == 1
    row = imported_conn.execute(
        "SELECT musicbrainz_id FROM lib2_albums WHERE id=?", (album_id,)).fetchone()
    assert row["musicbrainz_id"] == SAWANO_RG


def test_machine_auto_monitored_trackless_duplicate_still_folds(
        legacy_db, imported_conn):
    """Real-DB catch (§62): the EN duplicate was auto-monitored by the
    monitor-new-items policy (provenance 'new_release') — machine intent,
    not user intent. A trackless provider-only row whose only monitoring is
    machine-made must still fold; only user_explicit/wishlist_import block."""
    from core.library2.monitor_rules import record_rule

    aid = _artist_id(imported_conn)
    _set_artist_mbid(imported_conn, aid)
    jp_id = _seed_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        release_date="2017-06-07", expected_track_count=33)
    en_id = _seed_album(
        imported_conn, aid,
        title='TV Anime "Attack on Titan Season 2" (Original Soundtrack)',
        origin="discography", release_date="2017-06-07",
        expected_track_count=33, monitored=1)
    record_rule(imported_conn, "album", en_id, True, "new_release")
    imported_conn.commit()

    stats = R.reconcile_artist_release_groups(
        legacy_db, aid, client=FakeMBClient(_sawano_groups()))

    assert stats["merged"] == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE id=?", (en_id,)).fetchone()["c"] == 0
    assert imported_conn.execute(
        "SELECT musicbrainz_id FROM lib2_albums WHERE id=?",
        (jp_id,)).fetchone()["musicbrainz_id"] == SAWANO_RG


def test_date_fallback_respects_title_matched_holder_track_count(
        legacy_db, imported_conn):
    """Real-DB catch (§62): LOSTandFOUND (7 tracks, EP) shared its release
    day with the Hathaway OST (14 tracks) whose RG title-matched — the
    date-unique fallback must not hand the EP that RG."""
    aid = _artist_id(imported_conn)
    _set_artist_mbid(imported_conn, aid)
    ost_id = _seed_album(
        imported_conn, aid,
        title="MOBILE SUIT GUNDAM HATHAWAY Circe Original Motion Picture Soundtrack",
        origin="discography", release_date="2026-02-04",
        expected_track_count=14)
    ep_id = _seed_album(
        imported_conn, aid, title="LOSTandFOUND", album_type="ep",
        origin="discography", release_date="2026-02-04",
        expected_track_count=7)

    groups = [{
        "id": "6a07de2c-3e09-458c-8018-d23018f66050",
        "title": "MOBILE SUIT GUNDAM HATHAWAY Circe Original Motion Picture Soundtrack",
        "primary-type": "Album",
        "first-release-date": "2026-02-04",
    }]
    stats = R.reconcile_artist_release_groups(
        legacy_db, aid, client=FakeMBClient(groups))

    assert stats["assigned"] == 1     # only the OST, via title
    assert imported_conn.execute(
        "SELECT musicbrainz_id FROM lib2_albums WHERE id=?",
        (ep_id,)).fetchone()["musicbrainz_id"] is None
    assert stats["review"] == 0


def test_auto_monitored_duplicate_with_placeholder_tracks_folds_and_unmirrors(
        legacy_db, imported_conn):
    """Real-DB catch #2 (§62): the EN duplicate had 33 FILELESS placeholder
    tracks (materialized tracklist, '0/33') and machine monitoring. Files,
    not track rows, are what makes a row worth protecting — the fold must
    remove the placeholders and enqueue wishlist un-mirrors so the dead
    row's wanted tracks stop re-downloading."""
    from core.library2.monitor_rules import record_rule

    aid = _artist_id(imported_conn)
    _set_artist_mbid(imported_conn, aid)
    jp_id = _seed_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        release_date="2017-06-07", expected_track_count=33)
    en_id = _seed_album(
        imported_conn, aid,
        title='TV Anime "Attack on Titan Season 2" (Original Soundtrack)',
        origin="discography", release_date="2017-06-07",
        expected_track_count=33, monitored=1,
        external_ids=json.dumps({"deezer": "42695001"}))
    for n in (1, 2):
        imported_conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, monitored, "
            "spotify_id) VALUES(?,?,?,1,?)", (en_id, f"Cut {n}", n, f"sp-cut-{n}"))
    record_rule(imported_conn, "album", en_id, True, "new_release")
    imported_conn.commit()

    stats = R.reconcile_artist_release_groups(
        legacy_db, aid, client=FakeMBClient(_sawano_groups()))

    assert stats["merged"] == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE id=?", (en_id,)).fetchone()["c"] == 0
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_tracks WHERE album_id=?",
        (en_id,)).fetchone()["c"] == 0
    # The survivor had no deezer id → the duplicate's id fills the gap.
    survivor = imported_conn.execute(
        "SELECT external_ids FROM lib2_albums WHERE id=?", (jp_id,)).fetchone()
    assert json.loads(survivor["external_ids"]).get("deezer") == "42695001"
    # Un-mirror ops queued for the dead placeholders.
    ops = imported_conn.execute(
        "SELECT op FROM lib2_mirror_outbox").fetchall()
    assert any(r["op"] == "wishlist_remove" for r in ops)
