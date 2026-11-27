"""
ReplayGain analysis and tag writing for SoulSync.

Analysis is performed via FFmpeg's ebur128 filter (ReplayGain 2.0, -18 LUFS reference).
Tag writing uses mutagen directly to stay consistent with the rest of the codebase.

Supported formats: MP3, FLAC, OGG Vorbis, Opus, M4A/MP4
"""

import logging
import re
import subprocess
from typing import Optional, Tuple, Dict

logger = logging.getLogger(__name__)

# ReplayGain 2.0 reference level (EBU R128)
RG_REFERENCE_LUFS = -18.0


def get_target_lufs(config_manager=None) -> float:
    """#1060 — the loudness target gains are written against.

    Default is the ReplayGain 2.0 reference (-18 LUFS). Players that apply RG
    without their own pre-amp can leave libraries sounding too quiet; setting a
    hotter target (e.g. -14) bakes the offset into the written gains — the same
    thing rsgain/foobar2000 call "target loudness". Deliberately shared by
    EVERY gain writer (import pipeline, repair fix, per-track/album analyze
    endpoints) so the whole library stays consistent.

    Stored under the ReplayGain Filler job's settings
    (repair.jobs.replaygain_filler.target_lufs). Tolerates a positive "14" as
    meaning -14; junk/unset -> -18; clamped to [-30, -5]."""
    if config_manager is None:
        return RG_REFERENCE_LUFS
    # NOTE the '.settings.' segment: the job-settings UI saves into a nested
    # dict at repair.jobs.<id>.settings (see set_job_settings) — the flat
    # get_config_key path would silently miss every UI-saved value.
    raw = config_manager.get('repair.jobs.replaygain_filler.settings.target_lufs', RG_REFERENCE_LUFS)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return RG_REFERENCE_LUFS
    if value > 0:
        value = -value
    return max(-30.0, min(-5.0, value))

# Tag names used across all formats
_TAG_TRACK_GAIN = "REPLAYGAIN_TRACK_GAIN"
_TAG_TRACK_PEAK = "REPLAYGAIN_TRACK_PEAK"
_TAG_ALBUM_GAIN = "REPLAYGAIN_ALBUM_GAIN"
_TAG_ALBUM_PEAK = "REPLAYGAIN_ALBUM_PEAK"

_AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.mp4'}

# opus keeps its gain as R128_TRACK_GAIN / R128_ALBUM_GAIN (RFC 7845 5.2.1):
# a signed Q7.8 integer (dB * 256) relative to -23 LUFS, on top of the
# header's output gain. many opus players ignore REPLAYGAIN_* entirely.
# peaks have no R128 tag, so the REPLAYGAIN_*_PEAK comments stay.
_R128_TRACK = "R128_TRACK_GAIN"
_R128_ALBUM = "R128_ALBUM_GAIN"
R128_REFERENCE_LUFS = -23.0
# a ReplayGain gain sits 5 dB above the same file's R128 gain (-18 vs -23)
_R128_OFFSET_DB = RG_REFERENCE_LUFS - R128_REFERENCE_LUFS


def rg_gain_to_r128(gain_db: float) -> int:
    """a ReplayGain gain in dB as an R128 tag value (Q7.8, ref -23 LUFS).
    a target other than -18 (#1060) rides along: the offset is kept."""
    q = int(round((float(gain_db) - _R128_OFFSET_DB) * 256))
    return max(-32768, min(32767, q))


def r128_to_rg_gain(value) -> Optional[float]:
    """an R128 tag value (Q7.8 text) as a ReplayGain gain in dB, or None"""
    try:
        q = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not -32768 <= q <= 32767:
        return None
    return round(q / 256.0 + _R128_OFFSET_DB, 2)


def _is_opus(audio, ext: str) -> bool:
    """an opus stream, by extension or (an .ogg holding opus) by what mutagen opened"""
    if ext == '.opus':
        return True
    try:
        from mutagen.oggopus import OggOpus
        return isinstance(audio, OggOpus)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# FFmpeg availability
# ---------------------------------------------------------------------------

def is_ffmpeg_available() -> bool:
    """Return True if ffmpeg is on PATH."""
    try:
        subprocess.run(
            ['ffmpeg', '-version'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_track(file_path: str) -> Tuple[float, float]:
    """
    Analyze a single audio file and return (integrated_lufs, true_peak_dbfs).

    Uses FFmpeg's ebur128 filter with true peak measurement.
    Raises:
        FileNotFoundError: if ffmpeg is not on PATH
        RuntimeError: if ffmpeg fails or output cannot be parsed
    """
    cmd = [
        'ffmpeg', '-nostdin', '-v', 'info',
        '-i', file_path,
        '-filter:a', 'ebur128=peak=true',
        '-f', 'null', '-'
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
            timeout=120
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError("ffmpeg not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg timed out analyzing track") from exc

    parsed = _parse_ebur128(result.stderr)
    if parsed is None:
        raise RuntimeError(
            f"Could not parse ebur128 output for '{file_path}'. "
            f"FFmpeg exit code: {result.returncode}"
        )
    return parsed['integrated_lufs'], parsed['true_peak_db']


def _parse_ebur128(stderr: str) -> Optional[Dict[str, float]]:
    """the Summary block's integrated loudness, true peak and loudness range"""

    # ebur128 emits two kinds of output:
    #   (a) Per-window progress lines like
    #       "[Parsed_ebur128_0 @ ...] t: 0.5 ... I: -70.0 LUFS ..."
    #       — the "I:" here is the PARTIAL integrated loudness up to that
    #       window. The very first window of nearly any track is silent
    #       (intro fade-in / encoder padding) and reads ~-70 LUFS.
    #   (b) A final "Summary:" block like:
    #         Summary:
    #           Integrated loudness:
    #             I:         -14.3 LUFS
    #           ...
    #           True peak:
    #             Peak:        -0.4 dBFS
    #
    # The OLD code used ``re.search('I:\s+...')`` which returned the FIRST
    # match — i.e. the first per-window partial reading. For nearly every
    # track that came back as ~-70 LUFS, producing gain = -18 - (-70) =
    # +52.00 dB on EVERY track. (User report: "all tracks seem to get
    # the same track_gain ... +52.00 dB".)
    #
    # Fix: anchor parsing to the Summary block so we always read the
    # final integrated value, never a per-window partial.
    summary_idx = stderr.rfind('Summary:')
    if summary_idx >= 0:
        summary_block = stderr[summary_idx:]
        lufs_values = re.findall(r'I:\s+([-\d.]+)\s+LUFS', summary_block)
        peak_matches = re.findall(r'Peak:\s+([-\d.]+)\s+dBFS', summary_block)
    else:
        # No Summary block in the output (truncated / unexpected ffmpeg
        # version). Defensive fallback to the LAST per-window reading,
        # which is at least closer to the final integrated value than
        # the first window (which is always silence).
        lufs_values = re.findall(r'I:\s+([-\d.]+)\s+LUFS', stderr)
        peak_matches = re.findall(r'Peak:\s+([-\d.]+)\s+dBFS', stderr)

    if not lufs_values:
        return None

    integrated_lufs = float(lufs_values[-1])

    if peak_matches:
        true_peak_dbfs = max(float(v) for v in peak_matches)
    else:
        # Fall back to 0 dBFS peak if not available
        true_peak_dbfs = 0.0

    lra = None
    if summary_idx >= 0:
        lra_values = re.findall(r'LRA:\s+([-\d.]+)\s+LU\b', stderr[summary_idx:])
        if lra_values:
            lra = float(lra_values[0])
    return {'integrated_lufs': integrated_lufs, 'true_peak_db': true_peak_dbfs, 'lra': lra}


def _low_priority_prefix() -> list:
    """nice 19 and idle io where the box has them: a background measurement
    never competes with playback, a scan or an import"""
    import os
    import shutil
    if os.name != 'posix':
        return []
    prefix = []
    if shutil.which('ionice'):
        prefix += ['ionice', '-c3']
    if shutil.which('nice'):
        prefix += ['nice', '-n', '19']
    return prefix


def measure_loudness(file_path: str, *, audio_stream: Optional[int] = None,
                     timeout: float = 120, low_priority: bool = True) -> Dict[str, Optional[float]]:
    """ebur128 on one audio stream of a file, for the background measurers:
    one thread, no video decoded, low priority. ``audio_stream`` picks the
    nth audio stream (a video's main track is 0); None takes ffmpeg's pick.
    returns {integrated_lufs, true_peak_db, lra}. raises like analyze_track."""
    cmd = (_low_priority_prefix() if low_priority else []) + [
        'ffmpeg', '-nostdin', '-hide_banner', '-v', 'info', '-threads', '1',
        '-i', file_path, '-vn', '-sn', '-dn',
    ]
    if audio_stream is not None:
        cmd += ['-map', f'0:a:{int(audio_stream)}']
    cmd += ['-filter:a', 'ebur128=peak=true', '-f', 'null', '-']
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError("ffmpeg not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg timed out measuring loudness") from exc
    parsed = _parse_ebur128(result.stderr)
    if parsed is None:
        raise RuntimeError(f"ffmpeg couldn't measure this file (exit {result.returncode})")
    # silence reads -70 LUFS: no gain can level it, and +52 dB would be a disaster
    if parsed['integrated_lufs'] <= -69.0:
        raise RuntimeError("the audio is silent")
    return parsed


# ---------------------------------------------------------------------------
# Gain / peak formatting helpers
# ---------------------------------------------------------------------------

def format_gain(lufs: float, reference: float = RG_REFERENCE_LUFS) -> str:
    """Return a formatted gain string like '-2.50 dB'."""
    gain = reference - lufs
    return f"{gain:+.2f} dB"


def format_peak(true_peak_dbfs: float) -> str:
    """Convert a true peak in dBFS to a linear peak string like '0.987654'."""
    linear = 10 ** (true_peak_dbfs / 20.0)
    # Clamp to [0, 1] — values above 0 dBFS (>1.0) are kept as-is (clipping)
    return f"{linear:.6f}"


# ---------------------------------------------------------------------------
# Tag reading
# ---------------------------------------------------------------------------

def read_replaygain_tags(file_path: str) -> Dict[str, Optional[str]]:
    """
    Read existing ReplayGain tags from an audio file.

    Returns a dict with keys:
        track_gain, track_peak, album_gain, album_peak
    All values are strings (e.g. "-2.50 dB") or None if not present.
    """
    result = {
        'track_gain': None,
        'track_peak': None,
        'album_gain': None,
        'album_peak': None,
    }

    import os
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _AUDIO_EXTENSIONS:
        return result

    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=False)
        if audio is None:
            return result

        if ext == '.mp3':
            result['track_gain'] = _read_id3_txxx(audio, _TAG_TRACK_GAIN)
            result['track_peak'] = _read_id3_txxx(audio, _TAG_TRACK_PEAK)
            result['album_gain'] = _read_id3_txxx(audio, _TAG_ALBUM_GAIN)
            result['album_peak'] = _read_id3_txxx(audio, _TAG_ALBUM_PEAK)
        elif ext in ('.flac', '.ogg', '.oga', '.opus'):
            result['track_gain'] = _vorbis_first(audio, _TAG_TRACK_GAIN.lower())
            result['track_peak'] = _vorbis_first(audio, _TAG_TRACK_PEAK.lower())
            result['album_gain'] = _vorbis_first(audio, _TAG_ALBUM_GAIN.lower())
            result['album_peak'] = _vorbis_first(audio, _TAG_ALBUM_PEAK.lower())
            if _is_opus(audio, ext):
                # R128 is opus's own gain: it wins over a REPLAYGAIN_* an
                # older SoulSync (or another tool) left beside it
                for key, tag in (('track_gain', _R128_TRACK), ('album_gain', _R128_ALBUM)):
                    gain = r128_to_rg_gain(_vorbis_first(audio, tag.lower()))
                    if gain is not None:
                        result[key] = f"{gain:+.2f} dB"
        elif ext in ('.m4a', '.mp4'):
            result['track_gain'] = _mp4_rg(audio, _TAG_TRACK_GAIN)
            result['track_peak'] = _mp4_rg(audio, _TAG_TRACK_PEAK)
            result['album_gain'] = _mp4_rg(audio, _TAG_ALBUM_GAIN)
            result['album_peak'] = _mp4_rg(audio, _TAG_ALBUM_PEAK)
    except Exception as e:
        logger.debug("read replaygain tags failed: %s", e)

    return result


def _read_id3_txxx(audio, description: str) -> Optional[str]:
    """Read a TXXX frame value by description (case-insensitive)."""
    try:
        key = f"TXXX:{description}"
        if key in audio.tags:
            frame = audio.tags[key]
            return str(frame.text[0]) if frame.text else None
        # Also try uppercase/lowercase variants
        for frame_key in audio.tags.keys():
            if frame_key.upper() == key.upper():
                frame = audio.tags[frame_key]
                return str(frame.text[0]) if frame.text else None
    except Exception as e:
        logger.debug("read id3 txxx frame failed: %s", e)
    return None


def _vorbis_first(audio, key: str) -> Optional[str]:
    """Return the first value of a Vorbis comment key, or None."""
    try:
        vals = audio.get(key) or audio.get(key.upper())
        if vals:
            return str(vals[0])
    except Exception as e:
        logger.debug("read vorbis comment failed: %s", e)
    return None


def _mp4_rg(audio, tag_name: str) -> Optional[str]:
    """Read a ReplayGain freeform atom from MP4."""
    try:
        key = f"----:com.apple.iTunes:{tag_name}"
        if key in audio:
            raw = audio[key]
            if raw:
                val = raw[0]
                if hasattr(val, 'decode'):
                    return val.decode('utf-8')
                return str(val)
    except Exception as e:
        logger.debug("read mp4 replaygain atom failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# Tag writing
# ---------------------------------------------------------------------------

def write_replaygain_tags(
    file_path: str,
    track_gain_db: float,
    track_peak_dbfs: float,
    album_gain_db: Optional[float] = None,
    album_peak_dbfs: Optional[float] = None,
) -> bool:
    """
    Write ReplayGain tags to an audio file.

    Args:
        file_path: Path to the audio file.
        track_gain_db: Track gain in dB (gain = ref - lufs, signed).
        track_peak_dbfs: Track true peak in dBFS.
        album_gain_db: Album gain in dB, or None to skip writing album tags.
        album_peak_dbfs: Album true peak in dBFS, or None to skip album tags.

    Returns:
        True on success, False on failure.
    """
    import os
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _AUDIO_EXTENSIONS:
        return False

    track_gain_str = f"{track_gain_db:+.2f} dB"
    track_peak_str = format_peak(track_peak_dbfs)

    album_gain_str = f"{album_gain_db:+.2f} dB" if album_gain_db is not None else None
    album_peak_str = format_peak(album_peak_dbfs) if album_peak_dbfs is not None else None

    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=False)
        if audio is None:
            return False

        if ext == '.mp3':
            _write_id3_rg(audio, track_gain_str, track_peak_str, album_gain_str, album_peak_str)
        elif ext in ('.flac', '.ogg', '.oga', '.opus') and _is_opus(audio, ext):
            _write_opus_rg(audio, track_gain_db, track_peak_str, album_gain_db, album_peak_str)
        elif ext in ('.flac', '.ogg', '.oga', '.opus'):
            _write_vorbis_rg(audio, track_gain_str, track_peak_str, album_gain_str, album_peak_str)
        elif ext in ('.m4a', '.mp4'):
            _write_mp4_rg(audio, track_gain_str, track_peak_str, album_gain_str, album_peak_str)
        else:
            return False

        # Atomic + audio-integrity-verified save (#819/#1000): never rewrite the
        # library file in place; abort rather than risk damaging the audio.
        from core.metadata.common import save_audio_file, get_mutagen_symbols
        return bool(save_audio_file(audio, get_mutagen_symbols()))
    except Exception:
        return False


def _write_id3_rg(audio, track_gain: str, track_peak: str,
                  album_gain: Optional[str], album_peak: Optional[str]) -> None:
    """Write ReplayGain TXXX frames to an MP3 file's ID3 tags."""
    from mutagen.id3 import TXXX

    if audio.tags is None:
        audio.add_tags()

    def _set_txxx(desc: str, value: str) -> None:
        # Remove any existing frame with this description (case-insensitive)
        to_delete = [k for k in audio.tags.keys() if k.upper() == f"TXXX:{desc}".upper()]
        for k in to_delete:
            del audio.tags[k]
        audio.tags.add(TXXX(encoding=3, desc=desc, text=[value]))

    _set_txxx(_TAG_TRACK_GAIN, track_gain)
    _set_txxx(_TAG_TRACK_PEAK, track_peak)
    if album_gain is not None:
        _set_txxx(_TAG_ALBUM_GAIN, album_gain)
    if album_peak is not None:
        _set_txxx(_TAG_ALBUM_PEAK, album_peak)


def _write_vorbis_rg(audio, track_gain: str, track_peak: str,
                     album_gain: Optional[str], album_peak: Optional[str]) -> None:
    """Write ReplayGain Vorbis comments (FLAC, OGG, Opus)."""
    audio[_TAG_TRACK_GAIN.lower()] = [track_gain]
    audio[_TAG_TRACK_PEAK.lower()] = [track_peak]
    if album_gain is not None:
        audio[_TAG_ALBUM_GAIN.lower()] = [album_gain]
    if album_peak is not None:
        audio[_TAG_ALBUM_PEAK.lower()] = [album_peak]


def _write_opus_rg(audio, track_gain_db: float, track_peak: str,
                   album_gain_db: Optional[float], album_peak: Optional[str]) -> None:
    """opus gains as R128_* (RFC 7845), never REPLAYGAIN_*_GAIN: a player
    reading both would apply the gain twice, and the RFC says not to write
    them. an old REPLAYGAIN_*_GAIN SoulSync wrote before is taken off. the
    peaks stay as REPLAYGAIN_*_PEAK (R128 has none; clipping prevention
    needs them)."""
    if audio.tags is None:
        audio.add_tags()
    audio[_R128_TRACK.lower()] = [str(rg_gain_to_r128(track_gain_db))]
    audio[_TAG_TRACK_PEAK.lower()] = [track_peak]
    for stale in (_TAG_TRACK_GAIN, _TAG_ALBUM_GAIN if album_gain_db is not None else None):
        if stale:
            for key in (stale.lower(), stale):
                if key in audio:
                    del audio[key]
    if album_gain_db is not None:
        audio[_R128_ALBUM.lower()] = [str(rg_gain_to_r128(album_gain_db))]
    if album_peak is not None:
        audio[_TAG_ALBUM_PEAK.lower()] = [album_peak]


def _write_mp4_rg(audio, track_gain: str, track_peak: str,
                  album_gain: Optional[str], album_peak: Optional[str]) -> None:
    """Write ReplayGain freeform atoms to an MP4/M4A file."""
    from mutagen.mp4 import MP4FreeForm

    def _set_atom(name: str, value: str) -> None:
        key = f"----:com.apple.iTunes:{name}"
        audio[key] = [MP4FreeForm(value.encode('utf-8'))]

    _set_atom(_TAG_TRACK_GAIN, track_gain)
    _set_atom(_TAG_TRACK_PEAK, track_peak)
    if album_gain is not None:
        _set_atom(_TAG_ALBUM_GAIN, album_gain)
    if album_peak is not None:
        _set_atom(_TAG_ALBUM_PEAK, album_peak)
