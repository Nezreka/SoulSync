"""folder overrides: one table for music and video, longest prefix wins, the
user's override beats the inferred learning, and a stale one never hides a
file the learning would find."""

from __future__ import annotations

import sqlite3

import pytest

from core.library import path_mappings as pm
from core.library import path_resolver, server_paths

_REL = "Beck/Guero/01 - E-Pro.flac"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    pm.ensure_schema(c.cursor())
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _clean():
    pm.configure(None)
    server_paths.reset()
    yield
    pm.configure(None)
    server_paths.reset()


def _use(conn):
    """the resolver reads overrides through the configured connection"""
    class _NoClose:
        def __init__(self, c):
            self.c = c

        def execute(self, *a):
            return self.c.execute(*a)

        def close(self):
            pass
    pm.configure(lambda: _NoClose(conn))


# ---------------------------------------------------------------- schema
def test_schema_is_idempotent_and_media_type_generic(conn):
    pm.ensure_schema(conn.cursor())
    cols = {r[1] for r in conn.execute("PRAGMA table_info(media_path_mappings)")}
    assert {"media_type", "server_kind", "server_prefix", "local_prefix", "enabled",
            "verified_at", "verify_json"} <= cols
    pm.save_mapping(conn, {"server_prefix": "/data/tv", "local_prefix": "/tv",
                           "media_type": "video"})
    pm.save_mapping(conn, {"server_prefix": "/data/tv", "local_prefix": "/music-tv"})
    assert [m["media_type"] for m in pm.list_mappings(conn)] == ["music", "video"]
    assert len(pm.list_mappings(conn, "video")) == 1


def test_music_database_runs_the_migration():
    src = open("database/music_database.py", encoding="utf-8").read()
    assert "from core.library.path_mappings import ensure_schema" in src


def test_same_prefix_saves_over_itself(conn):
    a = pm.save_mapping(conn, {"server_prefix": "/data/music/", "local_prefix": "/a"})
    b = pm.save_mapping(conn, {"server_prefix": "/data/music", "local_prefix": "/b"})
    assert a == b
    [row] = pm.list_mappings(conn)
    assert row["local_prefix"] == "/b"


@pytest.mark.parametrize("data,words", [
    ({"server_prefix": "", "local_prefix": "/m"}, "media server"),
    ({"server_prefix": "/x", "local_prefix": ""}, "SoulSync sees"),
    ({"server_prefix": "/x", "local_prefix": "music"}, "full path"),
    ({"server_prefix": "/x", "local_prefix": "/m", "media_type": "books"}, "media_type"),
    ({"server_prefix": "/x", "local_prefix": "/m", "server_kind": "Plex Media"}, "server_kind"),
])
def test_bad_saves_are_refused(conn, data, words):
    with pytest.raises(pm.MappingError, match=words):
        pm.save_mapping(conn, data)


def test_verify_result_is_stored_and_a_save_clears_it(conn):
    mid = pm.save_mapping(conn, {"server_prefix": "/x", "local_prefix": "/m"})
    pm.record_verify(conn, mid, {"sampled": 10, "readable": 9})
    [row] = pm.list_mappings(conn)
    assert row["verify"] == {"sampled": 10, "readable": 9} and row["verified_at"]
    pm.save_mapping(conn, {"server_prefix": "/x", "local_prefix": "/n"}, mid)
    [row] = pm.list_mappings(conn)
    assert row["verify"] == {} and row["verified_at"] is None


# ---------------------------------------------------------------- prefixes
@pytest.mark.parametrize("raw,norm", [
    ("/data/music/", "/data/music"),
    ("/data//music", "/data/music"),
    ("/", "/"),
    ("C:\\Music\\", "C:/Music"),
    ("\\\\nas\\share\\Music", "//nas/share/Music"),
    ("//nas/share/", "//nas/share"),
])
def test_normalize_prefix(raw, norm):
    assert pm.normalize_prefix(raw) == norm


def test_prefix_matches_on_a_folder_boundary_only():
    assert pm.relative_to("/music/A/b.flac", "/music") == "A/b.flac"
    assert pm.relative_to("/musicvideos/A/b.mkv", "/music") is None
    assert pm.relative_to("/music", "/music/") == ""


def test_trailing_slashes_on_either_side_dont_matter():
    maps = [{"server_prefix": "/data/music/", "local_prefix": "/srv/music/"}]
    assert pm.translate("/data/music/" + _REL, maps) == ["/srv/music/" + _REL]


def test_posix_compares_with_case_windows_without():
    assert pm.relative_to("/Music/a.flac", "/music") is None
    assert pm.relative_to("d:\\MUSIC\\Beck\\a.flac", "D:\\Music") == "Beck/a.flac"
    assert pm.relative_to("\\\\NAS\\Share\\a.flac", "//nas/share") == "a.flac"


def test_windows_server_path_onto_a_posix_mount():
    maps = [{"server_prefix": "D:\\Media\\Music", "local_prefix": "/music"}]
    assert pm.translate("D:\\Media\\Music\\Beck\\Guero\\01.flac", maps) == ["/music/Beck/Guero/01.flac"]


def test_posix_server_path_onto_a_windows_folder_keeps_backslashes():
    maps = [{"server_prefix": "/music", "local_prefix": "E:\\Music"}]
    assert pm.translate("/music/Beck/01.flac", maps) == ["E:\\Music\\Beck\\01.flac"]


def test_overlapping_prefixes_longest_wins():
    maps = [{"server_prefix": "/data", "local_prefix": "/all"},
            {"server_prefix": "/data/music/lossless", "local_prefix": "/flac"},
            {"server_prefix": "/data/music", "local_prefix": "/music"}]
    assert pm.translate("/data/music/lossless/A/b.flac", maps) == [
        "/flac/A/b.flac", "/music/lossless/A/b.flac", "/all/music/lossless/A/b.flac"]


def test_a_servers_own_override_wins_a_tie_and_others_dont_apply():
    maps = [{"server_prefix": "/music", "local_prefix": "/any", "server_kind": ""},
            {"server_prefix": "/music", "local_prefix": "/plex", "server_kind": "plex"},
            {"server_prefix": "/music", "local_prefix": "/jf", "server_kind": "jellyfin"}]
    assert pm.translate("/music/a.flac", maps, server_kind="plex") == ["/plex/a.flac", "/any/a.flac"]


def test_disabled_overrides_are_skipped():
    maps = [{"server_prefix": "/music", "local_prefix": "/m", "enabled": False}]
    assert pm.translate("/music/a.flac", maps) == []


# ---------------------------------------------------------------- the resolver
@pytest.fixture
def two_mounts(tmp_path):
    """the same file name under two folders: the override picks one, the
    suffix walk would refuse (two physical matches)"""
    a, b = tmp_path / "a" / "Music", tmp_path / "b" / "Music"
    for root in (a, b):
        f = root / _REL
        f.parent.mkdir(parents=True)
        f.write_bytes(b"x" * 10)
    return a, b


class _Cfg:
    def __init__(self, paths):
        self.paths = paths

    def get(self, key, default=None):
        return self.paths if key == "library.music_paths" else default


def test_the_resolver_asks_the_override_first(conn, two_mounts):
    a, b = two_mounts
    cfg = _Cfg([str(a), str(b)])
    server = "/plexmedia/Music/" + _REL
    # with no override the two copies make the suffix walk refuse
    assert path_resolver.resolve_library_file_path(server, config_manager=cfg) is None
    pm.save_mapping(conn, {"server_prefix": "/plexmedia/Music", "local_prefix": str(b)})
    _use(conn)
    assert path_resolver.resolve_library_file_path(server, config_manager=cfg) == str(b / _REL)


def test_an_override_respects_the_library_root(conn, two_mounts):
    a, b = two_mounts
    pm.save_mapping(conn, {"server_prefix": "/plexmedia/Music", "local_prefix": str(b)})
    _use(conn)
    got = path_resolver.resolve_library_file_path(
        "/plexmedia/Music/" + _REL, config_manager=_Cfg([str(a)]), library_root=str(a))
    assert got == str(a / _REL)


def test_explicit_beats_inferred_in_server_paths(conn, two_mounts):
    a, b = two_mounts
    server_paths._learned["/plexmedia/Music"] = str(a)
    assert server_paths.local_path_for("/plexmedia/Music/" + _REL) == str(a / _REL)
    pm.save_mapping(conn, {"server_prefix": "/plexmedia/Music", "local_prefix": str(b)})
    _use(conn)
    assert server_paths.local_path_for("/plexmedia/Music/" + _REL) == str(b / _REL)
    assert server_paths.local_folder_for("/plexmedia/Music") == str(b)


def test_a_stale_override_falls_through_to_the_learning(conn, two_mounts, tmp_path):
    a, _b = two_mounts
    server_paths._learned["/plexmedia/Music"] = str(a)
    pm.save_mapping(conn, {"server_prefix": "/plexmedia/Music",
                           "local_prefix": str(tmp_path / "gone")})
    _use(conn)
    assert server_paths.local_path_for("/plexmedia/Music/" + _REL) == str(a / _REL)


def test_a_write_is_seen_at_once_not_after_the_cache(conn, two_mounts):
    _a, b = two_mounts
    _use(conn)
    assert pm.local_path("/plexmedia/Music/" + _REL) is None
    pm.save_mapping(conn, {"server_prefix": "/plexmedia/Music", "local_prefix": str(b)})
    assert pm.local_path("/plexmedia/Music/" + _REL) == str(b / _REL)


def test_unconfigured_means_no_overrides_and_no_database():
    pm.configure(None)
    assert pm._load("music") == []
    assert pm.active_mappings("music") == []
    assert pm.local_path("/music/a.flac") is None


def test_video_overrides_dont_reach_music(conn, two_mounts):
    _a, b = two_mounts
    pm.save_mapping(conn, {"server_prefix": "/plexmedia/Music", "local_prefix": str(b),
                           "media_type": "video"})
    _use(conn)
    assert pm.local_path("/plexmedia/Music/" + _REL) is None
    assert pm.local_path("/plexmedia/Music/" + _REL, media_type="video") == str(b / _REL)
