"""Read-only production-code reproduction with a disposable database.

Run from repository root: PYTHONPATH=. .venv/bin/python docs/reviews/2026-09-05/evidence/repro_listening_ids.py
"""
import json
import os
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="review-listening-") as tmp:
        os.environ["DATABASE_PATH"] = str(Path(tmp) / "music.db")
        os.environ["VIDEO_DATABASE_PATH"] = str(Path(tmp) / "video.db")
        os.environ["SOULSYNC_CONFIG_PATH"] = str(Path(tmp) / "config.json")
        from database.music_database import MusicDatabase
        from core.listening_import.lastfm import LastFMListeningImportWorker
        from core.stats.queries import get_recent_tracks, get_listening_events

        db = MusicDatabase(os.environ["DATABASE_PATH"])
        with db._get_connection() as conn:
            artist = conn.execute("INSERT INTO lib2_artists(name,name_key,genres) VALUES('Muse','muse','[\"Rock\"]')").lastrowid
            album = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title,origin,image_url) VALUES(?,'The Resistance','library','https://example.test/cover.jpg')", (artist,)).lastrowid
            track = conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Uprising')", (album,)).lastrowid
        worker = LastFMListeningImportWorker.__new__(LastFMListeningImportWorker)
        worker.db = db
        events = [{"title": "Uprising", "artist": "Muse", "album": "The Resistance", "track_id": "lastfm:1", "played_at": "2026-09-04T12:00:00", "duration_ms": 300000}]
        worker._resolve_db_track_ids(events)
        worker._insert_events_deduped(events)
        with db._get_connection() as conn:
            stored = dict(conn.execute("SELECT db_track_id,lib2_track_id FROM listening_history").fetchone())
        print("lastfm_stored", json.dumps(stored))
        print("lastfm_recent", json.dumps(get_recent_tracks(db, 10)))
        print("lastfm_genres", json.dumps(db.get_genre_breakdown("all")))
        assert stored == {"db_track_id": track, "lib2_track_id": None}
        assert get_recent_tracks(db, 10)[0]["image_url"] is None
        assert db.get_genre_breakdown("all") == []
        db.insert_listening_events([{"title": "Uprising", "artist": "Muse", "album": "The Resistance", "track_id": "plex:1", "played_at": "2026-09-05T12:00:00", "server_source": "plex", "lib2_track_id": track}])
        detail = get_listening_events(db, None, time_range="all", filter_type="date", date="2026-09-05")
        print("plex_chart_detail", json.dumps(detail))
        assert detail["items"][0]["image_url"] is None
        assert detail["items"][0]["db_track_id"] is None
        with db._get_connection() as conn:
            other = conn.execute("INSERT INTO lib2_tracks(album_id,title,legacy_track_id) VALUES(?,'Wrong legacy collision',?)", (album, track)).lastrowid
            db._add_listening_history_table(conn.cursor())
            after = dict(conn.execute("SELECT db_track_id,lib2_track_id FROM listening_history WHERE server_source='lastfm'").fetchone())
        print("lastfm_after_startup_backfill", json.dumps(after), "wrong_track", other)
        assert after["lib2_track_id"] == other
        print("CONFIRMED: Last.fm writes the obsolete ID column; chart details read it while native/server plays write lib2_track_id.")
        from types import SimpleNamespace
        from core.search.library_check import check_library_presence
        from core.stats.queries import resolve_track
        with db._get_connection() as conn:
            va = conn.execute("INSERT INTO lib2_artists(name,name_key) VALUES('Various Artists','various artists')").lastrowid
            compilation = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title,origin) VALUES(?,'Compilation','library')", (va,)).lastrowid
            song = conn.execute("INSERT INTO lib2_tracks(album_id,title,track_artist) VALUES(?,'Compilation Song','Muse')", (compilation,)).lastrowid
            conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id) VALUES(?,?)", (song, artist))
            conn.execute("INSERT INTO lib2_track_files(track_id,path,file_state) VALUES(?,'/synthetic/compilation.flac','active')", (song,))
        cfg = SimpleNamespace(get_active_media_server=lambda: 'plex', get_plex_config=lambda: {})
        presence = check_library_presence(db, None, cfg, 1, [], [{"name": "Compilation Song", "artist": "Muse"}])
        playback = resolve_track(db, lambda x: x, 'Compilation Song', 'Muse')
        print('compilation_presence', json.dumps(presence), 'stats_play_resolution', playback)
        assert presence['tracks'][0]['in_library'] is False and playback is None


if __name__ == "__main__":
    main()
