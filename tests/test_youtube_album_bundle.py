"""YouTube album-bundle flow: resolve a YouTube Music album to its playlist and
stage its tracks.

Why it exists: some albums list every song as unavailable in the YouTube Music
song catalogue (no videoId), so the per-track song search never finds them, yet
the album's own playlist is readable by yt-dlp. These tests pin the matching
rules (artist AND title must agree), the playlist lookup, and the staging
contract the master worker relies on. Nothing touches the network.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import youtube_album as ya
from core.downloads import album_bundle_dispatch as dispatch
from core.downloads.master import _resolve_album_bundle_sources

ARTIST = "Lisa & Fiona"
ALBUM = "Folge 16: Das Verschwinden der Nachtaugen"
PLAYLIST = "OLAK5uy_m44U6pExBarSPZPUuZgWaMRMTpPH6LMBM"


# --- matching -------------------------------------------------------------

@pytest.mark.parametrize("wanted,candidate,expected", [
    (ALBUM, ALBUM, True),
    (ALBUM, "Folge 16 - Das Verschwinden der Nachtaugen", True),   # punctuation
    (ALBUM, "folge 16: das verschwinden der nachtaugen", True),    # case
    ("Café Del Mar", "Cafe del Mar", True),                        # accents
    (ALBUM, "Folge 15: Der mysteriöse neue Schüler", False),       # other volume
    (ALBUM, "Superfolge: Der magische Begleiter von Lisa & Fiona", False),
    (ALBUM, "", False),
    ("", ALBUM, False),
])
def test_title_matches(wanted, candidate, expected):
    assert ya.title_matches(wanted, candidate) is expected


def test_artist_must_be_one_of_the_credited_artists():
    assert ya.artist_matches(ARTIST, [{"name": "Lisa & Fiona"}])
    assert ya.artist_matches("lisa fiona", [{"name": "Lisa & Fiona"}])
    assert ya.artist_matches(ARTIST, [{"name": "Bobby & Max"}, {"name": "Lisa & Fiona"}])
    assert not ya.artist_matches(ARTIST, [{"name": "Lisa Loeb"}])
    assert not ya.artist_matches(ARTIST, [])
    assert not ya.artist_matches("", [{"name": "Lisa & Fiona"}])


class _FakeYT:
    def __init__(self, hits=None, album=None, raw=None, boom=False):
        self.hits, self.album, self.raw, self.boom = hits or [], album or {}, raw, boom
        self.queries = []

    def search(self, query, filter=None, limit=None):
        if self.boom:
            raise RuntimeError("network down")
        self.queries.append((query, filter))
        return self.hits

    def get_album(self, browse_id):
        return self.album

    def _send_request(self, endpoint, body):
        if self.raw is None:
            raise RuntimeError("unavailable")
        return self.raw


def _hit(title=ALBUM, artist="Lisa & Fiona", browse_id="MPREb_x"):
    return {"browseId": browse_id, "title": title, "artists": [{"name": artist}]}


def test_find_album_requires_artist_and_title_to_agree():
    yt = _FakeYT(hits=[
        _hit(artist="Lisa Loeb"),                         # right title, wrong artist
        _hit(title="Folge 15: Der mysteriöse neue Schüler"),  # right artist, wrong title
        _hit(browse_id="MPREb_right"),
    ])
    found = ya.find_album(ARTIST, ALBUM, yt)
    assert found["browse_id"] == "MPREb_right"
    assert yt.queries == [(f"{ARTIST} {ALBUM}", "albums")]


def test_find_album_prefers_nothing_over_a_near_miss():
    yt = _FakeYT(hits=[_hit(artist="Lisa Loeb"), _hit(title="Folge 15: Der mysteriöse neue Schüler")])
    assert ya.find_album(ARTIST, ALBUM, yt) is None


def test_find_album_never_raises():
    assert ya.find_album(ARTIST, ALBUM, _FakeYT(boom=True)) is None
    assert ya.find_album("", ALBUM, _FakeYT()) is None
    assert ya.find_album(ARTIST, "", _FakeYT()) is None


# --- playlist id ----------------------------------------------------------

def test_playlist_id_from_get_album():
    assert ya.album_playlist_id("MPREb_x", _FakeYT(album={"audioPlaylistId": PLAYLIST})) == PLAYLIST


def test_playlist_id_falls_back_to_the_canonical_url_of_the_page():
    """get_album leaves audioPlaylistId empty for albums whose songs are marked
    unavailable; the raw page still names the playlist."""
    raw = {"microformat": {"microformatDataRenderer": {
        "urlCanonical": f"https://music.youtube.com/playlist?list={PLAYLIST}"}}}
    assert ya.album_playlist_id("MPREb_x", _FakeYT(album={"audioPlaylistId": None}, raw=raw)) == PLAYLIST


def test_playlist_id_is_none_when_the_page_has_none():
    assert ya.album_playlist_id("MPREb_x", _FakeYT(album={}, raw={"microformat": {}})) is None
    assert ya.album_playlist_id("MPREb_x", _FakeYT(album={}, raw=None)) is None
    assert ya.album_playlist_id("", _FakeYT()) is None


# --- playlist entries -----------------------------------------------------

def test_playlist_entries_keep_order_and_skip_entries_without_an_id():
    info = {"entries": [
        {"id": "a1", "title": "Intro", "duration": 27},
        None,
        {"title": "No id"},
        {"id": "a2", "title": " Teil 01 ", "duration": 92},
    ]}
    seen = {}

    def extractor(url, opts):
        seen["url"], seen["opts"] = url, opts
        return info

    entries = ya.playlist_entries(PLAYLIST, {"cookiefile": "/x"}, extractor=extractor)
    assert [e["id"] for e in entries] == ["a1", "a2"]
    assert entries[1] == {"id": "a2", "title": "Teil 01", "duration": 92}
    assert seen["url"].endswith(PLAYLIST)
    assert seen["opts"]["extract_flat"] is True and seen["opts"]["cookiefile"] == "/x"


def test_playlist_entries_never_raise():
    def boom(url, opts):
        raise RuntimeError("bot check")

    assert ya.playlist_entries(PLAYLIST, extractor=boom) == []
    assert ya.playlist_entries("", extractor=boom) == []


def test_check_entries():
    ok = [{"id": "a", "duration": 600}, {"id": "b", "duration": 600}]
    assert ya.check_entries(ok, 1200)[0] is True
    assert ya.check_entries(ok, 1400)[0] is True            # 1200 >= 0.8 * 1400
    assert ya.check_entries(ok, 3000)[0] is False           # far shorter than asked
    assert ya.check_entries(ok, None)[0] is True            # nothing to compare
    assert ya.check_entries([], 1200)[0] is False
    unknown = [{"id": "a", "duration": None}]
    assert ya.check_entries(unknown, 3000)[0] is True       # unknown proves nothing


# --- the opt-in gate ------------------------------------------------------

def test_youtube_is_only_bundle_capable_when_the_setting_is_on():
    assert "youtube" not in dispatch.album_bundle_sources(lambda k, d=None: False)
    assert "youtube" in dispatch.album_bundle_sources(lambda k, d=None: True)
    assert {"torrent", "usenet", "soulseek"} <= dispatch.album_bundle_sources(lambda k, d=None: False)


def test_eligibility_for_youtube_needs_the_flag():
    common = dict(mode="youtube", is_album=True, album_name=ALBUM, artist_name=ARTIST)
    assert dispatch.is_eligible(**common) is False
    assert dispatch.is_eligible(**common, youtube_album_bundle=True) is True


def _config(values):
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    return cfg


def test_hybrid_chain_prefix_includes_youtube_only_when_enabled():
    base = {"download_source.mode": "hybrid",
            "download_source.hybrid_order": ["usenet", "soulseek", "youtube"]}
    assert _resolve_album_bundle_sources(_config(base)) == ["usenet", "soulseek"]
    on = dict(base, **{"download_source.youtube_album_bundle": True})
    assert _resolve_album_bundle_sources(_config(on)) == ["usenet", "soulseek", "youtube"]


def test_youtube_single_source_mode_with_the_flag():
    values = {"download_source.mode": "youtube", "download_source.youtube_album_bundle": True}
    assert _resolve_album_bundle_sources(_config(values)) == ["youtube"]
    assert _resolve_album_bundle_sources(_config({"download_source.mode": "youtube"})) == []


# --- the client method ----------------------------------------------------

ENTRIES = [
    {"id": "v1", "title": "Das Verschwinden der Nachtaugen (Einleitung)", "duration": 27},
    {"id": "v2", "title": "Lisa & Fiona Titelsong", "duration": 66},
    {"id": "v3", "title": "Das Verschwinden der Nachtaugen (Teil 01)", "duration": 92},
]


@pytest.fixture
def client(tmp_path):
    from core.youtube_client import YouTubeClient
    instance = YouTubeClient(download_path=str(tmp_path / "dl"))
    instance._download_delay = 0
    return instance


def _enable(monkeypatch, value=True):
    from core.settings import config_manager
    real_get = config_manager.get
    monkeypatch.setattr(
        config_manager, "get",
        lambda key, default=None: value if key == "download_source.youtube_album_bundle"
        else real_get(key, default),
    )


def _patch_flow(entries=ENTRIES, album=True, playlist=PLAYLIST, downloads=None, tmp_path=None):
    """Patch every network step; ``downloads`` maps entry id -> file name or None."""
    downloads = downloads or {}

    def fake_download(self, url, title):
        vid = url.rsplit("=", 1)[-1]
        name = downloads.get(vid, f"{vid}.mp3")
        if name is None:
            return None
        path = tmp_path / name
        path.write_bytes(b"audio")
        return str(path)

    return [
        patch("ytmusicapi.YTMusic", MagicMock()),
        patch("core.youtube_album.find_album",
              return_value={"browse_id": "MPREb_x", "title": ALBUM, "artists": [ARTIST]} if album else None),
        patch("core.youtube_album.album_playlist_id", return_value=playlist),
        patch("core.youtube_album.playlist_entries", return_value=list(entries)),
        patch("core.youtube_client.YouTubeClient._download_sync", fake_download),
    ]


def _run(client, tmp_path, patches, **kwargs):
    from contextlib import ExitStack
    staging = tmp_path / "staging"
    events = []
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        outcome = client.download_album_to_staging(
            ALBUM, ARTIST, str(staging), events.append, **kwargs)
    return outcome, staging, events


def test_disabled_by_default_is_a_fallback(client, tmp_path):
    outcome, staging, _ = _run(client, tmp_path, _patch_flow(tmp_path=tmp_path))
    assert outcome["success"] is False and outcome["fallback"] is True
    assert not staging.exists()


def test_stages_every_track_in_order(client, tmp_path, monkeypatch):
    _enable(monkeypatch)
    outcome, staging, events = _run(client, tmp_path, _patch_flow(tmp_path=tmp_path))
    assert outcome["success"] is True and outcome["fallback"] is False
    assert outcome["partial"] is False
    assert outcome["expected_count"] == 3 and outcome["completed_count"] == 3
    names = sorted(p.name for p in staging.iterdir())
    assert names == [
        "01 - Das Verschwinden der Nachtaugen (Einleitung).mp3",
        "02 - Lisa & Fiona Titelsong.mp3",
        "03 - Das Verschwinden der Nachtaugen (Teil 01).mp3",
    ]
    assert outcome["files"] == [str(staging / n) for n in names]
    assert [e["state"] for e in events][0] == "searching" and events[-1]["state"] == "staged"
    # the YouTube download folder is left clean
    assert not any(p.suffix == ".mp3" for p in tmp_path.iterdir() if p.is_file())


def test_a_failed_track_makes_the_result_partial_not_a_failure(client, tmp_path, monkeypatch):
    _enable(monkeypatch)
    outcome, staging, _ = _run(client, tmp_path, _patch_flow(tmp_path=tmp_path, downloads={"v2": None}))
    assert outcome["success"] is True and outcome["partial"] is True
    assert outcome["expected_count"] == 3 and outcome["completed_count"] == 2
    assert len(list(staging.iterdir())) == 2


def test_nothing_downloaded_falls_back_to_per_track(client, tmp_path, monkeypatch):
    _enable(monkeypatch)
    outcome, _, _ = _run(client, tmp_path, _patch_flow(
        tmp_path=tmp_path, downloads={"v1": None, "v2": None, "v3": None}))
    assert outcome["success"] is False and outcome["fallback"] is True
    assert outcome["files"] == []


@pytest.mark.parametrize("flow", [
    dict(album=False),                    # no album with that artist and title
    dict(playlist=None),                  # album page without a playlist
    dict(entries=[]),                     # playlist unreadable
])
def test_every_miss_is_fallback_eligible(client, tmp_path, monkeypatch, flow):
    _enable(monkeypatch)
    outcome, staging, _ = _run(client, tmp_path, _patch_flow(tmp_path=tmp_path, **flow))
    assert outcome["success"] is False and outcome["fallback"] is True
    assert outcome["error"]
    assert not staging.exists()


def test_a_much_shorter_playlist_is_refused_before_anything_downloads(client, tmp_path, monkeypatch):
    _enable(monkeypatch)
    downloaded = []
    patches = _patch_flow(tmp_path=tmp_path)
    outcome, staging, _ = _run(client, tmp_path, patches, expected_duration_seconds=10_000)
    assert outcome["success"] is False and outcome["fallback"] is True
    assert "min" in outcome["error"]
    assert not staging.exists() and downloaded == []


def test_an_album_that_will_not_download_is_abandoned_after_two_misses(client, tmp_path, monkeypatch):
    """Readable but not playable for this account (YouTube Music Premium only):
    the first tracks fail, and the rest would fail the same way."""
    _enable(monkeypatch)
    attempts = []
    patches = _patch_flow(entries=ENTRIES * 5, tmp_path=tmp_path,
                          downloads={"v1": None, "v2": None, "v3": None})
    from core import youtube_client
    real = youtube_client.YouTubeClient._download_sync

    def counting(self, url, title):
        attempts.append(url)
        return None

    patches[-1] = patch("core.youtube_client.YouTubeClient._download_sync", counting)
    outcome, _, _ = _run(client, tmp_path, patches)
    assert outcome["success"] is False and outcome["fallback"] is True
    assert len(attempts) == 2


def test_early_failures_do_not_stop_an_album_that_has_started(client, tmp_path, monkeypatch):
    _enable(monkeypatch)
    entries = ENTRIES + [{"id": "v4", "title": "Teil 02", "duration": 90}, {"id": "v5", "title": "Teil 03", "duration": 90}]
    outcome, _, _ = _run(client, tmp_path, _patch_flow(
        entries=entries, tmp_path=tmp_path, downloads={"v2": None, "v3": None}))
    # v1 succeeded first, so two later misses are just misses
    assert outcome["success"] is True and outcome["completed_count"] == 3


def test_shutdown_stops_the_loop(client, tmp_path, monkeypatch):
    _enable(monkeypatch)
    client.shutdown_check = lambda: True
    outcome, _, _ = _run(client, tmp_path, _patch_flow(tmp_path=tmp_path))
    assert outcome["success"] is False and outcome["fallback"] is True


def test_staged_names_never_overwrite(tmp_path):
    from core.youtube_client import _staged_album_path
    first = _staged_album_path(tmp_path, 1, 'A/B: "C"?', "x.m4a")
    assert first.name == "01 - A B C.m4a" or first.name.startswith("01 - A B")
    first.write_bytes(b"x")
    second = _staged_album_path(tmp_path, 1, 'A/B: "C"?', "y.m4a")
    assert second != first


def _silent_mp3(path: Path) -> None:
    # 40 MPEG-1 Layer III frames (128 kbit/s, 44.1 kHz, 417 bytes each)
    path.write_bytes((b"\xff\xfb\x90\x00" + b"\x00" * 413) * 40)


def test_tags_are_written_for_the_staging_matcher(tmp_path):
    import mutagen
    from core.youtube_client import _tag_staged_track
    target = tmp_path / "03 - Teil 01.mp3"
    _silent_mp3(target)
    _tag_staged_track(target, "Teil 01", ARTIST, ALBUM, 3, 47)
    tags = mutagen.File(str(target), easy=True)
    assert tags["title"] == ["Teil 01"]
    assert tags["artist"] == [ARTIST] and tags["album"] == [ALBUM]
    assert tags["tracknumber"] == ["3/47"]


def test_tagging_an_unreadable_file_is_not_fatal(tmp_path):
    from core.youtube_client import _tag_staged_track
    broken = tmp_path / "x.mp3"
    broken.write_bytes(b"not audio")
    _tag_staged_track(broken, "t", "a", "b", 1, 1)
