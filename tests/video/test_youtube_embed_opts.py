"""YouTube SponsorBlock + embedded-subtitle options (ytdl-sub parity).

media_embed_opts turns the organization settings into yt-dlp postprocessors;
ydl_download_opts splices them with the RIGHT ordering (chapter surgery before
the metadata embed, subtitle embed after) and process_youtube_download plumbs
them into every real download.
"""

from __future__ import annotations

from pathlib import Path

from core.video.organization import normalize
from core.video.youtube_download import media_embed_opts, ydl_download_opts

_ROOT = Path(__file__).resolve().parent.parent.parent
_YTD = (_ROOT / "core" / "video" / "youtube_download.py").read_text(encoding="utf-8")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


def _keys(pps):
    return [p["key"] for p in pps]


def test_defaults_add_nothing():
    assert media_embed_opts(normalize({})) == {}
    opts = ydl_download_opts({}, "/yt", "stem", extra_opts={})
    assert _keys(opts["postprocessors"]) == ["FFmpegMetadata", "FFmpegThumbnailsConvertor"]


def test_mark_mode_adds_chapters_only():
    e = media_embed_opts(normalize({"youtube_sponsorblock": "mark"}))
    assert _keys(e["postprocessors"]) == ["SponsorBlock", "ModifyChapters"]
    mc = e["postprocessors"][1]
    assert "remove_sponsor_segments" not in mc and "sponsorblock_chapter_title" in mc


def test_remove_mode_cuts_the_hard_sell_but_keeps_intros():
    e = media_embed_opts(normalize({"youtube_sponsorblock": "remove"}))
    mc = e["postprocessors"][1]
    assert set(mc["remove_sponsor_segments"]) == {"sponsor", "selfpromo", "interaction"}
    assert "intro" not in mc["remove_sponsor_segments"]      # chapters only, never cut
    sb = e["postprocessors"][0]
    assert "intro" in sb["categories"] and "outro" in sb["categories"]


def test_embed_subs_uses_the_shared_language_setting():
    e = media_embed_opts(normalize({"youtube_embed_subs": True, "subtitle_langs": "en, es"}))
    assert e["writesubtitles"] is True and e["subtitleslangs"] == ["en", "es"]
    assert _keys(e["postprocessors"]) == ["FFmpegEmbedSubtitle"]


def test_ordering_chapter_surgery_before_metadata_sub_embed_after():
    e = media_embed_opts(normalize({"youtube_sponsorblock": "remove",
                                    "youtube_embed_subs": True}))
    opts = ydl_download_opts({}, "/yt", "stem", extra_opts=e)
    assert _keys(opts["postprocessors"]) == [
        "SponsorBlock", "ModifyChapters",                    # cut BEFORE embedding metadata
        "FFmpegMetadata", "FFmpegThumbnailsConvertor",
        "FFmpegEmbedSubtitle"]                               # subs after the merge
    assert opts["writesubtitles"] is True                    # top-level keys merged too


def test_settings_gate_is_whitelisted():
    assert normalize({"youtube_sponsorblock": "nonsense"})["youtube_sponsorblock"] == "off"
    assert normalize({"youtube_sponsorblock": "REMOVE"})["youtube_sponsorblock"] == "remove"
    assert normalize({})["youtube_embed_subs"] is False


def test_worker_plumbs_the_settings_into_every_download():
    assert "extra_opts=media_embed_opts(settings)" in _YTD


def test_settings_ui_has_the_fields():
    for frag in ("vo-sponsorblock", "vo-yt-subs", "youtube_sponsorblock", "youtube_embed_subs"):
        assert frag in _SETTINGS_JS, frag
    assert 'id="vo-sponsorblock"' in _INDEX and 'id="vo-yt-subs"' in _INDEX
