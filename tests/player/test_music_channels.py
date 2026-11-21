"""the guide's music channels: genres you have plenty of, and vibes from moods."""

import json

from core.player import music_channels as mc


def _row(tid, genre=None, artist_genre=None, mood=None, seconds=200):
    return (tid, f"t{tid}", "A", seconds * 1000, 1,
            json.dumps([genre]) if genre else "[]",
            json.dumps([artist_genre]) if artist_genre else "[]", mood)


def test_genres_need_enough_tracks_and_use_the_album_then_the_artist():
    rows = [_row(i, genre="Rock") for i in range(30)]
    rows += [_row(100 + i, artist_genre="jazz") for i in range(25)]
    rows += [_row(200 + i, genre="Polka") for i in range(5)]
    out = {c["id"]: c for c in mc.build(rows)}
    assert set(out) == {"music-genre-rock", "music-genre-jazz"}
    assert out["music-genre-rock"]["name"] == "Rock"
    assert out["music-genre-rock"]["count"] == 30
    assert out["music-genre-rock"]["tracks"][0][3] == 200


def test_moods_fold_into_vibes_most_specific_first():
    rows = [_row(i, mood="Relaxed") for i in range(12)] + [_row(50 + i, mood="dreamy, odd") for i in range(10)]
    rows += [_row(90 + i, mood="Unknown") for i in range(30)]
    out = {c["id"]: c for c in mc.build(rows)}
    assert list(out) == ["music-vibe-chill"]
    assert out["music-vibe-chill"]["count"] == 22


def test_short_and_untimed_tracks_never_air():
    rows = [_row(i, genre="Rock", seconds=30) for i in range(40)]
    assert mc.build(rows) == []


def test_the_sample_is_capped_and_stable():
    rows = [_row(i, genre="Rock") for i in range(500)]
    a = mc.build(rows, per_channel=50)[0]["tracks"]
    b = mc.build(list(reversed(rows)), per_channel=50)[0]["tracks"]
    assert len(a) == 50 and a == b


def test_vibe_lookup():
    assert mc.vibe_for("Energetic") == "High energy"
    assert mc.vibe_for("odd / Happy") == "Feel good"
    assert mc.vibe_for(None) is None


def test_read_runs_on_the_real_schema_and_only_airs_owned_tracks(tmp_path):
    from core.library2.schema import ensure_library_v2_schema
    from tests import lib2_seed

    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    ensure_library_v2_schema(conn)
    for i in range(25):
        lib2_seed.track(conn, "Miles Davis", "Kind of Blue", f"take {i}", duration=300_000,
                        album_cols={"genres": json.dumps(["Jazz"]), "mood": "Relaxed"})
    for i in range(25):
        lib2_seed.track(conn, "Miles Davis", "Kind of Blue", f"wanted {i}", owned=False,
                        duration=300_000)
    conn.commit()
    out = {c["id"]: c for c in mc.read(conn)}
    assert set(out) == {"music-genre-jazz", "music-vibe-chill"}
    assert out["music-genre-jazz"]["count"] == 25


def test_spellings_of_one_genre_share_a_channel():
    rows = [_row(i, genre="Rap/Hip Hop") for i in range(15)]
    rows += [_row(100 + i, genre="Hip-Hop/Rap") for i in range(10)]
    out = mc.build(rows)
    assert [c["id"] for c in out] == ["music-genre-hip-hop-rap"]
    assert out[0]["name"] == "Rap/Hip Hop" and out[0]["count"] == 25
    assert mc.genre_key("R&B") == "b r"
