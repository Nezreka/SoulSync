"""the guide's music channels: genres you have plenty of, and vibes from moods."""

import json

from core.player import music_channels as mc


def _album(aid, genre=None, artist_genre=None, mood=None, tracks=10):
    return (aid, json.dumps([genre]) if genre else "[]",
            json.dumps([artist_genre]) if artist_genre else "[]", mood, tracks)


def test_genres_need_enough_tracks_and_use_the_album_then_the_artist():
    albums = [_album(i, genre="Rock") for i in range(3)]            # 30 tracks
    albums += [_album(10 + i, artist_genre="jazz") for i in range(3)]  # 30 tracks
    albums += [_album(20, genre="Polka", tracks=5)]
    out = {c["id"]: c for c in mc.plan(albums)}
    assert set(out) == {"music-genre-rock", "music-genre-jazz"}
    assert out["music-genre-rock"]["name"] == "Rock"
    assert out["music-genre-rock"]["count"] == 30
    assert sorted(out["music-genre-rock"]["albums"]) == [0, 1, 2]


def test_moods_fold_into_vibes():
    albums = [_album(1, mood="Relaxed", tracks=12), _album(2, mood="dreamy, odd", tracks=10),
              _album(3, mood="Unknown", tracks=30)]
    out = {c["id"]: c for c in mc.plan(albums)}
    assert list(out) == ["music-vibe-chill"]
    assert out["music-vibe-chill"]["count"] == 22


def test_spellings_of_one_genre_share_a_channel():
    albums = [_album(1, genre="Rap/Hip Hop", tracks=15), _album(2, genre="Hip-Hop/Rap", tracks=10)]
    out = mc.plan(albums)
    assert [c["id"] for c in out] == ["music-genre-hip-hop-rap"]
    assert out[0]["name"] == "Rap/Hip Hop" and out[0]["count"] == 25
    assert mc.genre_key("R&B") == "b r"


def test_up_to_thirty_genres():
    albums = [_album(i, genre=f"Genre {i}", tracks=25) for i in range(40)]
    assert len(mc.plan(albums)) == 30


def test_fill_takes_a_stable_capped_sample_of_the_albums_tracks():
    plan = [{"id": "music-genre-rock", "name": "Rock", "kind": "genre", "count": 500, "albums": [1, 2]}]
    tracks = {1: [[i, f"t{i}", "A", 200, 1] for i in range(300)],
              2: [[1000 + i, f"u{i}", "B", 200, 2] for i in range(300)]}
    a = mc.fill(plan, tracks, per_channel=50)[0]["tracks"]
    b = mc.fill(plan, {k: list(reversed(v)) for k, v in tracks.items()}, per_channel=50)[0]["tracks"]
    assert len(a) == 50 and a == b
    assert mc.fill(plan, {}) == []


def test_a_tracks_own_mood_comes_before_its_album():
    albums = [_album(1, mood="Relaxed", tracks=30)]
    # the album says relaxed, but its tracks say energetic
    out = {c["id"]: c for c in mc.plan(albums, [(1, "Energetic", 25), (1, "odd", 5)])}
    assert list(out) == ["music-vibe-high-energy"]
    assert out["music-vibe-high-energy"]["count"] == 25
    planned = [{"id": "v", "name": "High energy", "kind": "vibe", "count": 2, "albums": [1]}]
    tracks = {1: [[1, "a", "A", 200, 1], [2, "b", "A", 200, 1], [3, "c", "A", 200, 1]]}
    filled = mc.fill(planned, tracks, track_vibe={1: "High energy", 2: "Chill"})
    # a chill track stays off the high energy channel; one with no mood rides along
    assert [t[0] for t in filled[0]["tracks"]] == sorted([1, 3], key=mc._spread)


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
    # too short to air
    lib2_seed.track(conn, "Miles Davis", "Kind of Blue", "intro", duration=20_000)
    for i in range(25):
        lib2_seed.track(conn, "Miles Davis", "Kind of Blue", f"wanted {i}", owned=False,
                        duration=300_000)
    # moods only on the tracks (no genre, no album mood): they still make a vibe
    for i in range(22):
        lib2_seed.track(conn, "Run DMC", "Raising Hell", f"cut {i}", duration=200_000,
                        mood="Energetic")
    conn.commit()
    out = {c["id"]: c for c in mc.read(conn)}
    assert set(out) == {"music-genre-jazz", "music-vibe-chill", "music-vibe-high-energy"}
    assert len(out["music-vibe-high-energy"]["tracks"]) == 22
    assert out["music-genre-jazz"]["count"] == 26
    assert len(out["music-genre-jazz"]["tracks"]) == 25
