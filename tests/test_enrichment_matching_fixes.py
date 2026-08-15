"""Regression coverage for corruption-class enrichment matching bugs.

These tests deliberately seed only the authoritative Library-v2 catalogue. They
pin the upstream matching fixes at the same boundary the production workers now
use: native entity ids, provider ids in ``external_ids``/promoted columns, and
attempt state in ``lib2_provider_attempts``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.library2.match_status import set_library_v2_match
from core.library2.provider_attempts import attempt_state, record_attempt
from core.library2.worker_support import stored_provider_id
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _artist(db, server_id: str, name: str, **provider_ids: str) -> int:
    with db._get_connection() as conn:
        artist_id = seed_artist(
            conn, server_id=server_id, name=name, server_source="test"
        )
        for service, provider_id in provider_ids.items():
            set_library_v2_match(
                conn, "artist", artist_id, service, provider_id, actor="test"
            )
        # Owned, or the enrichment queue will not offer it: ownership in lib2 is
        # a live file row, which legacy's `artists` table implied by existing.
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,?)",
            (artist_id, f"{name} LP")).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)",
            (album, name)).lastrowid
        conn.execute(
            "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
            "VALUES(?,?,1,'active')", (track, f"/music/{track}.flac"))
        conn.commit()
    return artist_id


def _album(db, server_id: str, title: str, artist_id: int, **provider_ids: str) -> int:
    with db._get_connection() as conn:
        album_id = seed_album(
            conn,
            server_id=server_id,
            title=title,
            artist_id=artist_id,
            server_source="test",
        )
        for service, provider_id in provider_ids.items():
            set_library_v2_match(
                conn, "album", album_id, service, provider_id, actor="test"
            )
        conn.commit()
    return album_id


def _track(db, server_id: str, title: str, album_id: int, artist_id: int,
           **provider_ids: str) -> int:
    with db._get_connection() as conn:
        track_id = seed_track(
            conn,
            server_id=server_id,
            title=title,
            album_id=album_id,
            artist_id=artist_id,
            server_source="test",
        )
        for service, provider_id in provider_ids.items():
            set_library_v2_match(
                conn, "track", track_id, service, provider_id, actor="test"
            )
        conn.commit()
    return track_id


def _provider_id(db, entity_type: str, entity_id: int, service: str):
    with db._get_connection() as conn:
        return stored_provider_id(conn, entity_type, entity_id, service)


def _worker(db, service: str):
    if service == "tidal":
        from core.tidal_worker import TidalWorker
        return TidalWorker(database=db)
    if service == "qobuz":
        from core.qobuz_worker import QobuzWorker
        return QobuzWorker(database=db)
    if service == "audiodb":
        from core.audiodb_worker import AudioDBWorker
        return AudioDBWorker(database=db)
    raise AssertionError(service)


def _smear_case(db, service: str):
    parent_id = _artist(db, "parent", "Parent Artist", **{service: "100"})
    album_id = _album(db, "album", "Some Album", parent_id)
    return parent_id, {
        "type": "album",
        "id": album_id,
        "name": "Some Album",
        "artist": "Parent Artist",
        f"artist_{service}_id": "100",
    }


def _verify(worker, service: str, item: dict, result_id: str, result_name):
    if service == "audiodb":
        return worker._verify_artist_id(
            item, {"idArtist": result_id, "strArtist": result_name or ""}
        )
    return worker._verify_artist_id(item, result_id, result_name)


@pytest.mark.parametrize("service", ["tidal", "qobuz", "audiodb"])
def test_missing_result_name_never_corrects(db, service):
    parent_id, item = _smear_case(db, service)
    _verify(_worker(db, service), service, item, "999", None)
    assert _provider_id(db, "artist", parent_id, service) == "100"


@pytest.mark.parametrize("service", ["tidal", "qobuz", "audiodb"])
def test_mismatched_result_name_never_corrects(db, service):
    parent_id, item = _smear_case(db, service)
    _verify(_worker(db, service), service, item, "999", "Someone Else Entirely")
    assert _provider_id(db, "artist", parent_id, service) == "100"


@pytest.mark.parametrize("service", ["tidal", "qobuz", "audiodb"])
def test_matching_result_name_still_corrects(db, service):
    parent_id, item = _smear_case(db, service)
    _verify(_worker(db, service), service, item, "999", "Parent Artist")
    assert _provider_id(db, "artist", parent_id, service) == "999"


@pytest.mark.parametrize("service", ["tidal", "qobuz", "audiodb"])
def test_correction_refuses_id_held_by_differently_named_artist(db, service):
    parent_id, item = _smear_case(db, service)
    other_id = _artist(db, "other", "Totally Different Band", **{service: "999"})
    _verify(_worker(db, service), service, item, "999", "Parent Artist")
    assert _provider_id(db, "artist", parent_id, service) == "100"
    assert _provider_id(db, "artist", other_id, service) == "999"


@pytest.mark.parametrize("service", ["tidal", "qobuz", "audiodb"])
def test_same_named_holder_still_allows_correction(db, service):
    parent_id, item = _smear_case(db, service)
    _artist(db, "twin", "Parent Artist", **{service: "999"})
    _verify(_worker(db, service), service, item, "999", "Parent Artist")
    assert _provider_id(db, "artist", parent_id, service) == "999"


def test_existing_id_paths_stamp_matched(db):
    from core.amazon_worker import AmazonWorker
    from core.deezer_worker import DeezerWorker
    from core.itunes_worker import iTunesWorker

    deezer_id = _artist(db, "deezer", "Dz Artist", deezer="1")
    amazon_id = _artist(db, "amazon", "Am Artist", amazon="B1")
    itunes_id = _artist(db, "itunes", "It Artist", itunes="7")

    DeezerWorker(database=db)._process_artist(deezer_id, "Dz Artist")
    AmazonWorker(database=db)._process_artist(amazon_id, "Am Artist")
    iTunesWorker(database=db)._process_artist(
        {"type": "artist", "id": itunes_id, "name": "It Artist"}
    )

    with db._get_connection() as conn:
        assert attempt_state(conn, entity_type="artist", entity_id=deezer_id)["deezer"]["status"] == "matched"
        assert attempt_state(conn, entity_type="artist", entity_id=amazon_id)["amazon"]["status"] == "matched"
        assert attempt_state(conn, entity_type="artist", entity_id=itunes_id)["itunes"]["status"] == "matched"


def test_error_rows_requeue_after_retry_window(db):
    from core.tidal_worker import TidalWorker

    artist_id = _artist(db, "retry", "Old Error")
    stale = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db._get_connection() as conn:
        record_attempt(
            conn,
            entity_type="artist",
            entity_id=artist_id,
            service="tidal",
            status="error",
            attempted_at=stale,
        )
        # The artist's owned album and track are unattempted work, and
        # unattempted work legitimately comes before an expired retry. Settle
        # them so this test is about the retry window and nothing else.
        for row in conn.execute(
                "SELECT id FROM lib2_albums WHERE primary_artist_id=?",
                (artist_id,)).fetchall():
            record_attempt(conn, entity_type="album", entity_id=int(row[0]),
                           service="tidal", status="matched")
        for row in conn.execute(
                "SELECT t.id FROM lib2_tracks t "
                "JOIN lib2_albums al ON al.id = t.album_id "
                "WHERE al.primary_artist_id=?", (artist_id,)).fetchall():
            record_attempt(conn, entity_type="track", entity_id=int(row[0]),
                           service="tidal", status="matched")
        conn.commit()

    worker = TidalWorker(database=db)
    item = worker._get_next_item()
    assert item is not None and item["id"] == artist_id

    with db._get_connection() as conn:
        conn.execute(
            "UPDATE lib2_provider_attempts SET last_attempted_at=? "
            "WHERE entity_type='artist' AND entity_id=? AND service='tidal'",
            (fresh, artist_id),
        )
        conn.commit()
    assert worker._get_next_item() is None


def test_native_worker_queue_retries_not_found_and_error():
    from core.library2.worker_queue import _RETRYABLE

    assert _RETRYABLE == ("not_found", "error")


def _all_name_matchers(db):
    from core.amazon_worker import AmazonWorker
    from core.audiodb_worker import AudioDBWorker
    from core.bandcamp_worker import BandcampWorker
    from core.deezer_worker import DeezerWorker
    from core.discogs_worker import DiscogsWorker
    from core.genius_worker import GeniusWorker
    from core.itunes_worker import iTunesWorker
    from core.jiosaavn_worker import JioSaavnWorker
    from core.lastfm_worker import LastFMWorker
    from core.qobuz_worker import QobuzWorker
    from core.tidal_worker import TidalWorker

    return [
        DeezerWorker(database=db),
        QobuzWorker(database=db),
        TidalWorker(database=db),
        iTunesWorker(database=db),
        LastFMWorker(database=db),
        AudioDBWorker(database=db),
        DiscogsWorker(database=db),
        GeniusWorker(database=db),
        AmazonWorker(database=db),
        BandcampWorker(database=db),
        JioSaavnWorker(database=db),
    ]


def test_empty_normalized_titles_no_longer_match_everything(db):
    for worker in _all_name_matchers(db):
        label = type(worker).__name__
        assert not worker._name_matches("(Intro)", "(Skit)"), label
        assert not worker._name_matches("!!!", "???"), label
        assert worker._name_matches("!!!", "!!!"), label
        assert worker._name_matches("Kyougen", "Kyougen"), label
        assert not worker._name_matches("Kyougen", "Something Else"), label


def test_spotify_similarity_empty_guard(db):
    from core.spotify_worker import SpotifyWorker

    worker = SpotifyWorker(database=db)
    assert worker._name_similarity("!!!", "???") == 0.0
    assert worker._name_similarity("!!!", "!!!") == 1.0
    assert worker._name_similarity("(Intro)", "(Skit)") == 0.0
    assert worker._name_similarity("Kyougen", "Kyougen") == 1.0


class _RecordingAmazonClient:
    def __init__(self):
        self.searched = []

    def get_album(self, asin, include_tracks=False):
        return None

    def get_track_details(self, asin):
        return None

    def search_albums(self, query, limit=10):
        self.searched.append(query)
        return []

    def search_tracks(self, query, limit=10):
        self.searched.append(query)
        return []


def test_amazon_preserves_stored_match_on_refresh_miss(db):
    from core.amazon_worker import AmazonWorker

    artist_id = _artist(db, "artist", "Artist")
    album_id = _album(db, "album", "Album", artist_id, amazon="B-MANUAL")
    track_id = _track(
        db, "track", "Track", album_id, artist_id, amazon="B-MANUAL-T"
    )
    worker = AmazonWorker(database=db)
    worker.client = _RecordingAmazonClient()
    worker._process_album(
        album_id,
        "Album",
        "Artist",
        {"type": "album", "id": album_id, "name": "Album", "artist": "Artist"},
    )
    worker._process_track(
        track_id,
        "Track",
        "Artist",
        {"type": "track", "id": track_id, "name": "Track", "artist": "Artist"},
    )
    assert worker.client.searched == []
    assert _provider_id(db, "album", album_id, "amazon") == "B-MANUAL"
    assert _provider_id(db, "track", track_id, "amazon") == "B-MANUAL-T"


def test_jiosaavn_preserves_stored_match_on_refresh_miss(db):
    from core.jiosaavn_worker import JioSaavnWorker

    class _Client:
        def __init__(self):
            self.searched = []

        def get_album(self, provider_id):
            return None

        def get_track_details(self, provider_id):
            return None

        def search_albums(self, query, limit=5):
            self.searched.append(query)
            return []

        def search_tracks(self, query, limit=5):
            self.searched.append(query)
            return []

    artist_id = _artist(db, "artist", "Artist")
    album_id = _album(db, "album", "Album", artist_id, jiosaavn="J-MANUAL")
    track_id = _track(
        db, "track", "Track", album_id, artist_id, jiosaavn="J-MANUAL-T"
    )
    worker = JioSaavnWorker(database=db)
    worker._client = _Client()
    worker._process_album(album_id, "Album", "Artist")
    worker._process_track(track_id, "Track", "Artist")
    assert worker._client.searched == []
    assert _provider_id(db, "album", album_id, "jiosaavn") == "J-MANUAL"
    assert _provider_id(db, "track", track_id, "jiosaavn") == "J-MANUAL-T"


def test_mb_release_title_floor(db):
    from core.musicbrainz_service import MusicBrainzService

    service = MusicBrainzService(db)
    query, bad_title = "Night Visions", "Visions of the Night People"
    similarity = service._calculate_similarity(query, bad_title)
    assert 0.30 <= similarity < 0.6
    service.mb_client.search_release = lambda name, artist, limit=5: [{
        "id": "mbid-bad",
        "title": bad_title,
        "score": 100,
        "artist-credit": [{"artist": {"name": "Imagine Dragons"}}],
    }]
    assert service.match_release(query, "Imagine Dragons") is None

    service.mb_client.search_release = lambda name, artist, limit=5: [{
        "id": "mbid-good",
        "title": "Evolve",
        "score": 100,
        "artist-credit": [{"artist": {"name": "Imagine Dragons"}}],
    }]
    good = service.match_release("Evolve", "Imagine Dragons")
    assert good and good["mbid"] == "mbid-good"


def test_track_reset_clears_native_source_id_and_attempt(db):
    artist_id = _artist(db, "artist", "Artist")
    album_id = _album(db, "album", "Album", artist_id)
    track_id = _track(db, "track", "Track", album_id, artist_id, spotify="wrong")
    with db._get_connection() as conn:
        record_attempt(
            conn,
            entity_type="track",
            entity_id=track_id,
            service="spotify",
            status="matched",
        )
        conn.commit()

    assert db.reset_enrichment("spotify", "track", "item", entity_id=track_id) == 1
    assert _provider_id(db, "track", track_id, "spotify") is None
    with db._get_connection() as conn:
        assert "spotify" not in attempt_state(
            conn, entity_type="track", entity_id=track_id
        )
