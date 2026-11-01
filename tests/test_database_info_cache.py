"""the dashboard's library card reads counts that walk every track. they're
kept a minute per server; the update worker reads them live."""

from database.music_database import MusicDatabase


def test_the_dashboard_card_is_counted_once_a_minute(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    MusicDatabase._db_info_cache.clear()
    calls = []
    real = db.get_statistics_for_server
    monkeypatch.setattr(db, "get_statistics_for_server", lambda s, *a, **k: calls.append(s) or real(s))

    first = db.get_database_info_for_server("plex")
    second = db.get_database_info_for_server("plex")
    db.get_database_info_for_server("jellyfin")

    assert first == second
    assert calls == ["plex", "jellyfin"]


def test_the_cache_expires(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    MusicDatabase._db_info_cache.clear()
    calls = []
    real = db.get_statistics_for_server
    monkeypatch.setattr(db, "get_statistics_for_server", lambda s, *a, **k: calls.append(s) or real(s))
    monkeypatch.setattr(MusicDatabase, "_DB_INFO_TTL", 0.0)

    db.get_database_info_for_server("plex")
    db.get_database_info_for_server("plex")

    assert calls == ["plex", "plex"]
