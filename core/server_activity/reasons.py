"""why a stream is being converted, in plain words.

one table for both servers. jellyfin states its reasons (TranscodeReasons);
plex doesn't, so plex reasons are worked out from the stream decisions and the
payload says so (``inferred``).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# code -> (short label for lists and stats, clause for "converting because ...")
REASONS: Dict[str, tuple] = {
    "over_remote_limit": ("Over the player's remote quality",
                          "the file is bigger than the player's remote quality setting"),
    "over_quality_limit": ("Over the player's quality setting",
                           "the file is bigger than the player's quality setting"),
    "subtitle_burn_image": ("Image subtitles burned in",
                            "the subtitles are pictures the player can't draw, so they are burned into the video"),
    "subtitle_burn_styled": ("Styled subtitles burned in",
                             "the styled subtitles can't be shown by the player, so they are burned into the video"),
    "subtitle_burn": ("Subtitles burned in", "the subtitles are burned into the video"),
    "subtitle_codec": ("Subtitle format the player can't show",
                       "the player can't show this subtitle format"),
    "hdr": ("HDR on a screen without HDR", "the player can't show HDR, so the picture is mapped to SDR"),
    "video_codec": ("Video format the player can't play", "the player can't play this video format"),
    "video_profile": ("Video profile the player can't play", "the player can't play this video profile"),
    "video_level": ("Video level the player can't play", "the player can't play this video level"),
    "video_resolution": ("Picture bigger than the player takes", "the picture is bigger than the player takes"),
    "video_bit_depth": ("Video bit depth the player can't play", "the player can't play this video bit depth"),
    "video_framerate": ("Frame rate the player can't play", "the player can't play this frame rate"),
    "video_ref_frames": ("Video the player can't decode", "the player can't decode this video's reference frames"),
    "video_anamorphic": ("Anamorphic video", "the player can't play anamorphic video"),
    "video_interlaced": ("Interlaced video", "the player can't play interlaced video"),
    "video_codec_tag": ("Video tag the player can't play", "the player can't play this video's codec tag"),
    "video_rotation": ("Rotated video", "the player can't rotate this video"),
    "video_bitrate": ("Video bitrate over the player's limit", "the video bitrate is over the player's limit"),
    "audio_codec": ("Audio format the player can't play", "the player can't play this audio format"),
    "audio_channels": ("More audio channels than the player takes",
                       "the audio has more channels than the player takes"),
    "audio_profile": ("Audio profile the player can't play", "the player can't play this audio profile"),
    "audio_sample_rate": ("Sample rate the player can't play", "the player can't play this sample rate"),
    "audio_bit_depth": ("Audio bit depth the player can't play", "the player can't play this audio bit depth"),
    "audio_bitrate": ("Audio bitrate over the player's limit", "the audio bitrate is over the player's limit"),
    "audio_external": ("External audio track", "the audio track is a separate file"),
    "audio_secondary": ("Second audio track", "the player can't switch to a second audio track"),
    "container": ("File type the player can't open", "the player can't open this file type"),
    "stream_count": ("Too many tracks in the file", "the file has more tracks than the player takes"),
    "unknown_stream": ("Stream details unknown", "the server couldn't read the stream details"),
    "direct_play_error": ("Playing as is failed", "playing the file as is failed"),
    "video_other": ("Video the player couldn't take as is", "the player couldn't take the video as is"),
    "audio_other": ("Audio the player couldn't take as is", "the player couldn't take the audio as is"),
}

JELLYFIN_REASONS = {
    "ContainerNotSupported": "container",
    "VideoCodecNotSupported": "video_codec",
    "AudioCodecNotSupported": "audio_codec",
    "SubtitleCodecNotSupported": "subtitle_codec",
    "AudioIsExternal": "audio_external",
    "SecondaryAudioNotSupported": "audio_secondary",
    "VideoProfileNotSupported": "video_profile",
    "VideoLevelNotSupported": "video_level",
    "VideoResolutionNotSupported": "video_resolution",
    "VideoBitDepthNotSupported": "video_bit_depth",
    "VideoFramerateNotSupported": "video_framerate",
    "RefFramesNotSupported": "video_ref_frames",
    "AnamorphicVideoNotSupported": "video_anamorphic",
    "InterlacedVideoNotSupported": "video_interlaced",
    "AudioChannelsNotSupported": "audio_channels",
    "AudioProfileNotSupported": "audio_profile",
    "AudioSampleRateNotSupported": "audio_sample_rate",
    "AudioBitDepthNotSupported": "audio_bit_depth",
    "ContainerBitrateExceedsLimit": "over_quality_limit",
    "VideoBitrateNotSupported": "video_bitrate",
    "AudioBitrateNotSupported": "audio_bitrate",
    "UnknownVideoStreamInfo": "unknown_stream",
    "UnknownAudioStreamInfo": "unknown_stream",
    "DirectPlayError": "direct_play_error",
    "VideoRangeTypeNotSupported": "hdr",
    "VideoCodecTagNotSupported": "video_codec_tag",
    "StreamCountExceedsLimit": "stream_count",
    "VideoRotationNotSupported": "video_rotation",
}


def label(code: str) -> str:
    return REASONS.get(code, (code.replace("_", " ").capitalize(), ""))[0]


def _dedupe(codes: Iterable[str]) -> List[str]:
    out: List[str] = []
    for c in codes:
        if c and c not in out:
            out.append(c)
    return out


def from_jellyfin(raw: Any, *, remote: bool) -> List[str]:
    """jellyfin's TranscodeReasons (a list, or a comma string on older servers)
    -> our codes. an unknown reason keeps its name so nothing is dropped."""
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",")]
    out = []
    for r in raw or []:
        code = JELLYFIN_REASONS.get(str(r), "")
        if code == "over_quality_limit" and remote:
            code = "over_remote_limit"
        if not code:
            code = "jf_" + "".join("_" + ch.lower() if ch.isupper() else ch for ch in str(r)).strip("_")
        out.append(code)
    return _dedupe(out)


def infer_plex(*, method: str, video_decision: str, audio_decision: str, subtitle_decision: str,
               subtitle_codec: str, src_height: Optional[int], out_height: Optional[int],
               src_video_kbps: Optional[int], out_video_kbps: Optional[int], src_hdr: str,
               out_hdr: str, src_vcodec: str, out_vcodec: str, src_acodec: str, out_acodec: str,
               src_channels: Optional[int], out_channels: Optional[int], src_container: str,
               out_container: str, remote: bool) -> List[str]:
    """plex gives no reason. read one off what changed, most telling first."""
    codes: List[str] = []
    vd, ad, sd = (video_decision or "").lower(), (audio_decision or "").lower(), (subtitle_decision or "").lower()
    if sd == "burn":
        low = (subtitle_codec or "").lower()
        from core.server_activity.quality import IMAGE_SUBTITLES, STYLED_SUBTITLES
        codes.append("subtitle_burn_image" if low in IMAGE_SUBTITLES
                     else "subtitle_burn_styled" if low in STYLED_SUBTITLES else "subtitle_burn")
    if vd == "transcode":
        smaller = bool(src_height and out_height and out_height < src_height * 0.9)
        thinner = bool(src_video_kbps and out_video_kbps and out_video_kbps < src_video_kbps * 0.8)
        if smaller or thinner:
            codes.append("over_remote_limit" if remote else "over_quality_limit")
        if src_hdr and not out_hdr:
            codes.append("hdr")
        if not codes and src_vcodec and out_vcodec and src_vcodec != out_vcodec:
            codes.append("video_codec")
        if not codes:
            codes.append("video_other")
    if ad == "transcode":
        if src_channels and out_channels and out_channels < src_channels and src_acodec == out_acodec:
            codes.append("audio_channels")
        elif src_acodec and out_acodec and src_acodec != out_acodec:
            codes.append("audio_codec")
        elif vd != "transcode":
            codes.append("audio_other")
    if method == "direct_stream" and not codes:
        if src_container and out_container and src_container != out_container:
            codes.append("container")
    return _dedupe(codes)


def summary(codes: List[str], *, method: str, inferred: bool, src_kbps: Optional[int] = None,
            out_kbps: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """the reason block for a card: codes, labels, one plain sentence."""
    if not codes or method == "direct_play":
        return None
    clauses = [REASONS[c][1] for c in codes if c in REASONS and REASONS[c][1]]
    if not clauses:
        clauses = [label(c).lower() for c in codes]
    verb = "Converting" if method == "transcode" else "Repackaging"
    text = f"{verb} because " + (clauses[0] if len(clauses) == 1
                                  else ", ".join(clauses[:-1]) + " and " + clauses[-1]) + "."
    if codes[0] in ("over_remote_limit", "over_quality_limit") and src_kbps and out_kbps:
        from core.server_activity.quality import kbps_label
        text += f" The source runs at {kbps_label(src_kbps)} and {kbps_label(out_kbps)} is sent."
    return {"codes": codes, "items": [{"code": c, "label": label(c)} for c in codes],
            "inferred": inferred, "text": text}
