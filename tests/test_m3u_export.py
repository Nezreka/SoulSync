"""Unit tests for the pure library M3U builder."""

from __future__ import annotations

from core.library.m3u_export import build_m3u


def test_empty_yields_just_the_header():
    assert build_m3u([]) == "#EXTM3U\n"


def test_full_entry_extinf_and_path():
    out = build_m3u([
        {"path": "/music/K/DAMN/01 BLOOD.flac", "title": "BLOOD.", "artist": "Kendrick Lamar",
         "duration": 178},
    ])
    assert out == (
        "#EXTM3U\n"
        "#EXTINF:178,Kendrick Lamar - BLOOD.\n"
        "/music/K/DAMN/01 BLOOD.flac\n"
    )


def test_preserves_order_and_multiple_tracks():
    out = build_m3u([
        {"path": "/a.flac", "title": "A", "artist": "Art", "duration": 100},
        {"path": "/b.flac", "title": "B", "artist": "Art", "duration": 200},
    ])
    assert out.splitlines() == [
        "#EXTM3U",
        "#EXTINF:100,Art - A", "/a.flac",
        "#EXTINF:200,Art - B", "/b.flac",
    ]


def test_skips_entries_without_a_path():
    out = build_m3u([
        {"path": "", "title": "no path"},
        {"path": None, "title": "also none"},
        {"title": "missing key"},
        {"path": "/real.flac", "title": "Real", "artist": "Art", "duration": 5},
    ])
    assert out == "#EXTM3U\n#EXTINF:5,Art - Real\n/real.flac\n"


def test_unknown_or_bad_duration_is_minus_one():
    for bad in (None, 0, -3, "", "abc"):
        out = build_m3u([{"path": "/x.flac", "title": "T", "artist": "A", "duration": bad}])
        assert "#EXTINF:-1,A - T" in out


def test_label_falls_back_title_then_filename():
    # title only (no artist)
    assert "#EXTINF:9,Just Title" in build_m3u(
        [{"path": "/x.flac", "title": "Just Title", "duration": 9}])
    # neither -> basename of the path
    assert "#EXTINF:-1,10 - Song.flac" in build_m3u(
        [{"path": "/m/10 - Song.flac"}])


def test_none_entry_is_tolerated():
    assert build_m3u([None, {"path": "/x.flac", "title": "T"}]) == "#EXTM3U\n#EXTINF:-1,T\n/x.flac\n"


# ── write_library_m3u (I/O sibling) ──────────────────────────────────────────

def test_write_library_m3u_writes_file(tmp_path):
    from core.library.m3u_export import write_library_m3u
    folder = tmp_path / "Transfer"
    p = write_library_m3u([{"path": "/a.flac", "title": "A", "artist": "Art", "duration": 100}], str(folder))
    assert p == str(folder / "soulsync_library.m3u")
    assert (folder / "soulsync_library.m3u").read_text() == "#EXTM3U\n#EXTINF:100,Art - A\n/a.flac\n"


def test_write_library_m3u_creates_missing_folders(tmp_path):
    import os
    from core.library.m3u_export import write_library_m3u
    folder = tmp_path / "new" / "nested"
    p = write_library_m3u([{"path": "/x.flac", "title": "T"}], str(folder))
    assert p and os.path.exists(p)


def test_write_library_m3u_empty_folder_returns_none():
    from core.library.m3u_export import write_library_m3u
    assert write_library_m3u([{"path": "/x.flac"}], "") is None


def test_entry_base_path_is_prepended():
    out = build_m3u([{"path": "Artist/Album/01.flac", "title": "T", "artist": "A", "duration": 3}],
                    entry_base_path="/mnt/music")
    assert out == "#EXTM3U\n#EXTINF:3,A - T\n/mnt/music/Artist/Album/01.flac\n"


def test_entry_base_path_empty_leaves_paths_untouched():
    out = build_m3u([{"path": "/abs/x.flac", "title": "T", "duration": 3}], entry_base_path="")
    assert "/abs/x.flac\n" in out and "//abs" not in out


def test_entry_base_path_trailing_slash_normalized():
    out = build_m3u([{"path": "a.flac", "title": "T"}], entry_base_path="/mnt/music/")
    assert "/mnt/music/a.flac\n" in out
