"""Kometa logo-pack installer — plan building + install, with injected I/O.

No network: list_folder/fetch are fakes, so we assert the mapping (curated +
re-slugged mirror), best-effort failure handling, and that files land in the
right pack folders where AssetStore.read_logo can find them.
"""

from __future__ import annotations

from core.video.overlays import logo_packs
from core.video.overlays.assets import AssetStore


def _fake_list(repo, path):
    # a couple of Kometa-style display-name files per mirror folder
    if path.endswith("streaming/color"):
        return [("Disney+.png", "u://disney"), ("Netflix.png", "u://netflix")]
    if path.endswith("network/color"):
        return [("HBO.png", "u://hbo"), ("A&E.png", "u://ae")]
    if path.endswith("studio/standard"):
        return [("20th Century Studios.png", "u://20th"), ("A24.png", "u://a24")]
    return []


def test_build_plan_covers_curated_and_reslugged_mirror():
    plan = logo_packs.build_plan(list_folder=_fake_list)
    by_field = {}
    for field, name, url in plan:
        by_field.setdefault(field, {})[name] = url

    # curated quality fields map our normalized values straight through
    assert by_field["resolution"]["4k"].endswith("resolution/4k.png")
    assert by_field["audio_codec"]["dts_hd"].endswith("audio_codec/standard/ma.png")
    assert by_field["source"]["bluray"].endswith("video_format/logos/bluray.png")
    assert by_field["hdr"]["dolby_vision"].endswith("resolution/dv.png")

    # mirror folders are re-slugged with OUR convention so render-time lookups match
    assert by_field["network"]["hbo"] == "u://hbo"          # 'HBO.png' -> 'hbo'
    assert by_field["network"]["a_e"] == "u://ae"           # 'A&E.png' -> 'a_e'
    assert by_field["studio"]["20th_century_studios"] == "u://20th"
    assert by_field["streaming"]["disney"] == "u://disney"  # 'Disney+.png' -> 'disney'


def test_mirror_listing_failure_is_skipped_not_fatal():
    def boom_on_studio(repo, path):
        if "studio" in path:
            raise RuntimeError("network down")
        return _fake_list(repo, path)
    plan = logo_packs.build_plan(list_folder=boom_on_studio)
    fields = {f for f, _, _ in plan}
    assert "studio" not in fields          # the one that threw is dropped
    assert "network" in fields and "resolution" in fields   # everything else survives


def test_reslug_collision_keeps_first():
    def dup(repo, path):
        if path.endswith("network/color"):
            return [("HBO.png", "u://a"), ("H.B.O.png", "u://b")]   # both slug to 'h_b_o'? no: 'hbo' vs 'h_b_o'
        return []
    plan = [j for j in logo_packs.build_plan(list_folder=dup) if j[0] == "network"]
    names = [n for _, n, _ in plan]
    assert names == ["hbo", "h_b_o"]       # distinct slugs, both kept

    def same(repo, path):
        if path.endswith("network/color"):
            return [("HBO.png", "u://first"), ("H B O.png", "u://second")]  # both -> 'h_b_o'? 'hbo' vs 'h_b_o'
        return []
    # 'HBO' -> 'hbo', 'H B O' -> 'h_b_o' are different; force a true collision:
    def collide(repo, path):
        if path.endswith("network/color"):
            return [("HBO.png", "u://first"), ("HBO.png", "u://second")]
        return []
    plan = [j for j in logo_packs.build_plan(list_folder=collide) if j[0] == "network"]
    assert plan == [("network", "hbo", "u://first")]   # first file wins


def test_install_writes_into_readable_packs(tmp_path):
    store = AssetStore(str(tmp_path))
    fetched = {}

    def fake_fetch(url):
        if url.endswith("aac.png"):
            return None                     # simulate one miss
        fetched[url] = True
        return b"\x89PNG\r\n\x1a\n" + url.encode()   # unique bytes per url

    res = logo_packs.install(store, list_folder=_fake_list, fetch=fake_fetch)
    assert res["total"] == res["installed"] + res["failed"]
    assert res["failed"] == 1               # the aac miss
    assert res["installed"] > 20            # curated + mirror

    # the written files are exactly where read_logo looks
    assert store.read_logo("resolution", "4k") is not None
    assert store.read_logo("network", "hbo") == b"\x89PNG\r\n\x1a\n" + b"u://hbo"
    assert store.read_logo("audio_codec", "aac") is None      # the miss wrote nothing
    counts = store.logo_pack_counts()
    assert counts["network"] == 2 and counts["resolution"] == 4


def test_progress_is_reported(tmp_path):
    store = AssetStore(str(tmp_path))
    seen = []
    logo_packs.install(store, list_folder=_fake_list, fetch=lambda u: b"x",
                       on_progress=lambda p: seen.append(p))
    assert seen and seen[-1]["done"] == seen[-1]["total"]
    assert all(set(p) >= {"done", "total", "ok", "failed", "field"} for p in seen)
