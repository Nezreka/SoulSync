"""the similar-artists worker asks about the artists you play before the
rest of the catalogue: they are the ones recommendations are seeded by."""

from __future__ import annotations

from core.library2.provider_attempts import ensure_provider_attempt_schema, record_attempt
from core.library2.schema import ensure_library_v2_schema
from core.similar_artists_worker import next_played_artist
from tests import lib2_seed


def _conn(tmp_path):
    conn = lib2_seed.row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(conn)
    ensure_provider_attempt_schema(conn.cursor())
    conn.execute("""CREATE TABLE IF NOT EXISTS listening_history (
        id INTEGER PRIMARY KEY, title TEXT, artist TEXT, album TEXT, played_at TIMESTAMP,
        lib2_track_id INTEGER)""")
    return conn


def _play(conn, track_id, days_ago, times=1):
    conn.executemany(
        "INSERT INTO listening_history (title, played_at, lib2_track_id) VALUES ('x', datetime('now', ?), ?)",
        [(f"-{days_ago} days", track_id)] * times)


def _with_id(conn, name, spotify_id):
    conn.execute("UPDATE lib2_artists SET spotify_id = ? WHERE name = ?", (spotify_id, name))


def test_the_most_played_artist_never_asked_about_comes_first(tmp_path):
    conn = _conn(tmp_path)
    miles = lib2_seed.track(conn, "Miles Davis", "Kind of Blue", "So What")
    run = lib2_seed.track(conn, "Run DMC", "Raising Hell", "Peter Piper")
    quiet = lib2_seed.track(conn, "Never Played", "An Album", "A Song")
    no_id = lib2_seed.track(conn, "No Provider Id", "Demo", "Tape")
    old = lib2_seed.track(conn, "Played Long Ago", "Then", "Was")
    for name, sid in (("Miles Davis", "sp-miles"), ("Run DMC", "sp-run"), ("Never Played", "sp-quiet"),
                      ("Played Long Ago", "sp-old")):
        _with_id(conn, name, sid)
    _play(conn, miles, 3, times=2)
    _play(conn, run, 5, times=9)
    _play(conn, no_id, 1, times=50)           # the table is keyed by a provider id: it can't be asked about
    _play(conn, old, 200, times=80)           # outside the window
    conn.commit()
    assert quiet

    first = next_played_artist(conn)
    assert first["name"] == "Run DMC" and first["plays"] == 9 and first["spotify_id"] == "sp-run"
    record_attempt(conn, entity_type="artist", entity_id=first["id"], service="similar_artists", status="matched")
    second = next_played_artist(conn)
    assert second["name"] == "Miles Davis"
    # an artist that errored is the queue's to retry on its schedule, not ours every pass
    record_attempt(conn, entity_type="artist", entity_id=second["id"], service="similar_artists", status="error")
    assert next_played_artist(conn) is None, "then the queue carries on through the rest of the catalogue"
    conn.close()


def test_no_listening_history_is_no_pick(tmp_path):
    conn = _conn(tmp_path)
    lib2_seed.track(conn, "Miles Davis", "Kind of Blue", "So What")
    _with_id(conn, "Miles Davis", "sp-miles")
    conn.commit()
    assert next_played_artist(conn) is None
    conn.close()
