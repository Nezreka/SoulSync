"""server activity overhaul: plex and jellyfin sessions normalize to one rich
shape (source vs stream, why it converts, transcoder, connection, player, kinds).

the payloads are recorded shapes: plex /status/sessions + /library/metadata
xml and jellyfin /Sessions json (fields checked against jellyfin's 12.4
openapi), with made up names and addresses.
"""

from __future__ import annotations

import json

from core.server_activity import jellyfin as jf
from core.server_activity import plex as px
from core.server_activity.quality import summarize_playback
from tests.sa_helpers import book_tracks, jf_session, plex_meta, plex_session


def _plex(rk, **kw):
    src = plex_meta().get(rk) if kw.pop("with_source", True) else None
    return px.normalize(plex_session(rk), server_name="Basement", source=src, **kw)


# ── plex ────────────────────────────────────────────────────────────────────

def test_plex_transcode_shows_the_file_against_what_is_sent():
    s = _plex("5101")
    pb = s["playback"]
    assert pb["method"] == "transcode" and pb["method_label"] == "Transcode"
    assert pb["video"]["source"]["label"] == "4K HEVC Main 10 · HDR10"
    assert pb["video"]["stream"]["label"] == "1080p H.264 · SDR"
    assert pb["audio"]["source"]["label"] == "TrueHD Atmos 7.1 · English"
    assert pb["audio"]["stream"]["codec"] == "AAC" and pb["audio"]["stream"]["channel_layout"] == "5.1"
    assert pb["subtitles"]["source"]["label"] == "English (SRT)"
    assert pb["subtitles"]["stream"]["label"] == "Sent as text"
    assert pb["container"] == {"source": "MKV", "stream": "HLS", "changed": True}
    assert pb["bitrate_kbps"] == {"source": 62400, "stream": 11800}
    assert summarize_playback(pb, "movie") == {"quality": "4K HEVC", "change": "4K → 1080p"}


def test_plex_reason_is_worked_out_and_says_so():
    r = _plex("5101")["reason"]
    assert r["inferred"] is True
    assert r["codes"] == ["over_remote_limit", "hdr", "audio_codec"]
    assert r["text"].startswith("Converting because the file is bigger than the player's remote quality")
    assert "62.4 Mbps" in r["text"] and "11.8 Mbps" in r["text"]


def test_plex_transcoder_details():
    t = _plex("5101")["transcoder"]
    assert t["hw"] is True and t["hw_decode"] == "Intel Quick Sync" and t["hw_encode"] == "Intel Quick Sync"
    assert t["speed"] == 2.4 and t["throttled"] is True
    assert t["buffered_s"] == 220.0          # maxOffsetAvailable 4312 s, playing at 4092 s


def test_plex_connection_player_and_user():
    s = _plex("5101")
    assert s["connection"]["remote"] is True and s["connection"]["ip"] == "73.41.208.17"
    assert s["connection"]["secure"] is True and s["connection"]["relayed"] is False
    assert s["player"] == {"product": "Plex for Apple TV", "version": "8.41", "platform": "tvOS",
                           "platform_version": "18.1", "device": "Apple TV", "model": "J305AP",
                           "name": "Living room"}
    assert s["user"] == "maya" and s["user_avatar"] == "/api/server-activity/avatar?user=plex:7341"
    assert s["kind"] == "movie" and s["activity"] == "watching" and s["year"] == 2024
    assert s["controls"]["stop"] is True and s["controls"]["message"] is False


def test_plex_payload_carries_no_token_and_no_avatar_url():
    s = _plex("5101")
    public = {k: v for k, v in s.items() if not k.startswith("_")}
    text = json.dumps(public)
    assert "plex.tv/users" not in text and "Token" not in text


def test_plex_hires_music_direct_play():
    s = _plex("702", with_source=False)
    assert s["kind"] == "track" and s["activity"] == "listening"
    assert s["playback"]["method"] == "direct_play" and s["reason"] is None and s["transcoder"] is None
    a = s["playback"]["audio"]["source"]
    assert a["quality"] == "FLAC 24-bit · 96 kHz" and a["hires"] and a["lossless"]
    assert a["channel_layout"] == "Stereo"
    assert s["music"] == {"artist": "Radiohead", "album_artist": "Radiohead", "album": "Kid A", "track": 1, "disc": 1}
    assert s["subtitle"] == "Radiohead · Kid A" and s["connection"]["remote"] is False
    assert s["thumb"] == "/library/metadata/701/thumb/1700000002"      # the album cover


def test_plex_audiobook_has_chapter_and_whole_book_progress():
    book = px.book_progress(offset_ms=1_100_000, duration_ms=1_890_000, tracks=book_tracks(), current_key="814")
    s = _plex("814", audio_kind="audiobook", book=book, book_meta={"narrator": "Ray Porter"})
    assert s["kind"] == "audiobook" and s["state"] == "paused"
    assert s["title"] == "Project Hail Mary"
    assert s["subtitle"] == "Andy Weir · read by Ray Porter · Chapter 14 of 32"
    p = s["book"]["progress"]
    assert p["chapter"]["index"] == 14 and p["chapter"]["count"] == 32
    assert p["position_ms"] == 13 * 1_890_000 + 1_100_000
    assert p["percent"] == 42 and p["left_ms"] == 32 * 1_890_000 - p["position_ms"]
    assert s["playback"]["method"] == "direct_stream"
    assert s["playback"]["container"]["source"] == "M4B" and s["playback"]["container"]["stream"] == "MP4"
    assert s["connection"]["relayed"] is True


def test_plex_burned_image_subtitles_lead_the_reason():
    s = _plex("909")
    assert s["kind"] == "episode" and s["state"] == "buffering"
    assert s["subtitle"] == "Shōgun · S1 E9"
    assert s["show"] == {"title": "Shōgun", "season": 1, "episode": 9, "season_title": "Season 1"}
    assert s["playback"]["subtitles"]["decision"] == "burn"
    assert s["reason"]["codes"][0] == "subtitle_burn_image"
    assert summarize_playback(s["playback"], "episode")["change"] == "Subtitles burned in"


def test_plex_without_the_library_item_does_not_invent_the_source():
    s = _plex("5101", with_source=False)
    assert s["playback"]["video"]["source"]["codec"] == "HEVC"
    assert s["playback"]["video"]["source"]["resolution"] == ""
    assert s["playback"]["bitrate_kbps"]["source"] is None


def test_plex_stop_needs_plex_pass_and_says_so():
    s = _plex("5101", plex_pass=False)
    assert s["controls"]["stop"] is False
    assert "Plex Pass" in s["controls"]["stop_note"]


def test_a_bare_plex_element_never_raises():
    from xml.etree import ElementTree as ET
    s = px.normalize(ET.fromstring('<Video type="clip"/>'))
    assert s["title"] == "" and s["user"] == "Someone" and s["playback"]["method"] == "direct_play"


def test_book_progress_from_chapter_marks():
    chs = [{"start_ms": 0, "end_ms": 0, "title": "a"}, {"start_ms": 1000, "end_ms": 0, "title": "b"},
           {"start_ms": 5000, "end_ms": 0, "title": "c"}]
    b = px.book_progress(offset_ms=6000, duration_ms=10_000, chapters=chs)
    assert b["chapter"] == {"index": 3, "count": 3, "title": "c", "position_ms": 1000, "duration_ms": 5000}
    assert b["percent"] == 60 and b["left_ms"] == 4000
    assert px.book_progress(offset_ms=0, duration_ms=0) is None


# ── jellyfin ────────────────────────────────────────────────────────────────

def test_jellyfin_direct_play_episode():
    s = jf.normalize(jf_session("Cold Harbor"))
    assert s["id"] == "jellyfin:8f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e" and s["session_key"] == s["id"]
    assert s["kind"] == "episode" and s["subtitle"] == "Severance · S2 E10"
    assert s["playback"]["method"] == "direct_play" and s["reason"] is None
    assert s["playback"]["video"]["source"]["label"] == "1080p HEVC Main 10 · SDR"
    assert s["playback"]["audio"]["source"]["label"] == "EAC3 5.1 · eng"
    assert s["playback"]["subtitles"] is None
    assert s["connection"]["remote"] is False and s["connection"]["ip"] == "192.168.1.44"
    assert s["player"]["product"] == "Jellyfin Android TV" and s["player"]["version"] == "0.18.1"
    assert s["player"]["name"] == "Shield TV"
    assert s["thumb"] == "jf:5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e"          # the show's poster
    assert s["user_avatar"] == "/api/server-activity/avatar?user=jellyfin:0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f"
    assert s["controls"] == {"stop": True, "message": True, "stop_note": None}
    assert s["offset_ms"] == 3_271_000 and s["duration_ms"] == 4_540_000


def test_jellyfin_states_its_reasons():
    s = jf.normalize(jf_session("The Wild Robot"))
    assert s["playback"]["method"] == "transcode"
    assert s["reason"]["inferred"] is False
    assert s["reason"]["codes"] == ["over_remote_limit", "audio_codec"]
    assert s["playback"]["video"]["source"]["resolution"] == "1080p"
    assert s["playback"]["video"]["stream"]["resolution"] == "720p"
    assert s["playback"]["audio"]["source"]["codec"] == "DTS-HD MA"
    assert s["playback"]["audio"]["stream"]["codec"] == "AAC"
    t = s["transcoder"]
    assert t["hw"] is True and t["hw_encode"] == "NVIDIA NVENC" and t["speed"] == 2.4
    assert t["progress_pct"] == 52 and t["buffered_s"] is None and t["throttled"] is None
    assert s["bandwidth_kbps"] == 3200 and s["connection"]["remote"] is True


def test_jellyfin_remux_is_direct_stream():
    raw = jf_session("The Wild Robot")
    raw["TranscodingInfo"].update(IsVideoDirect=True, IsAudioDirect=True, TranscodeReasons=["ContainerNotSupported"])
    s = jf.normalize(raw)
    assert s["playback"]["method"] == "direct_stream"
    assert s["reason"]["codes"] == ["container"] and s["reason"]["text"].startswith("Repackaging because")


def test_jellyfin_audiobook_chapters():
    raw = jf_session("The Selfish Gene")
    chs = jf.chapters_of(raw["NowPlayingItem"])
    book = px.book_progress(offset_ms=5_900_000, duration_ms=60_000_000, chapters=chs)
    s = jf.normalize(raw, audio_kind="audiobook", book=book)
    assert s["kind"] == "audiobook" and s["state"] == "paused"
    assert s["subtitle"] == "Richard Dawkins · Chapter 3 of 4"
    assert s["book"]["progress"]["chapter"]["title"] == "The replicators"
    assert s["book"]["progress"]["percent"] == 10
    assert s["controls"]["message"] is False          # web client without DisplayMessage


def test_jellyfin_reason_map_covers_every_documented_reason():
    from core.server_activity.reasons import JELLYFIN_REASONS, REASONS
    documented = ["ContainerNotSupported", "VideoCodecNotSupported", "AudioCodecNotSupported",
                  "SubtitleCodecNotSupported", "AudioIsExternal", "SecondaryAudioNotSupported",
                  "VideoProfileNotSupported", "VideoLevelNotSupported", "VideoResolutionNotSupported",
                  "VideoBitDepthNotSupported", "VideoFramerateNotSupported", "RefFramesNotSupported",
                  "AnamorphicVideoNotSupported", "InterlacedVideoNotSupported", "AudioChannelsNotSupported",
                  "AudioProfileNotSupported", "AudioSampleRateNotSupported", "AudioBitDepthNotSupported",
                  "ContainerBitrateExceedsLimit", "VideoBitrateNotSupported", "AudioBitrateNotSupported",
                  "UnknownVideoStreamInfo", "UnknownAudioStreamInfo", "DirectPlayError",
                  "VideoRangeTypeNotSupported", "VideoCodecTagNotSupported", "StreamCountExceedsLimit",
                  "VideoRotationNotSupported"]
    for r in documented:
        assert JELLYFIN_REASONS[r] in REASONS, r
    from core.server_activity.reasons import from_jellyfin
    assert from_jellyfin("ContainerNotSupported,SomethingNew", remote=False) == ["container", "jf_something_new"]


def test_quality_labels():
    from core.server_activity import quality as q
    assert q.res_label("4k") == "4K" and q.res_label(None, 1600, 3840) == "4K"
    assert q.res_label("sd") == "SD" and q.res_label(None, 720, 1280) == "720p"
    assert q.hdr_label(range_type="DOVIWithHDR10") == "Dolby Vision"
    assert q.hdr_label("arib-std-b67") == "HLG" and q.hdr_label("bt709") == ""
    assert q.audio_side(codec="mp3", bitrate_kbps=128)["quality"] == "MP3 128 kbps"
    assert q.audio_side(codec="flac", bit_depth=16, sample_rate=44100)["quality"] == "FLAC 16-bit · 44.1 kHz"
    assert q.codec_label("dca", "ma") == "DTS-HD MA"


def test_plex_picks_the_audio_track_switched_on_not_the_first():
    """a plex Stream element has no children, so it is falsy: `a or b` on one
    silently skipped the selected track."""
    from xml.etree import ElementTree as ET
    el = ET.fromstring(
        '<Video type="movie" title="x" sessionKey="1"><Media audioCodec="aac"><Part selected="1">'
        '<Stream streamType="2" id="1" codec="ac3" channels="6" language="English"/>'
        '<Stream streamType="2" id="2" codec="aac" channels="2" language="Japanese" selected="1"/>'
        '</Part></Media><Player state="playing"/></Video>')
    assert px.normalize(el)["playback"]["audio"]["source"]["language"] == "Japanese"
