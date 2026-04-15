"""
ReplayGain analysis and tag writing for SoulSync.

Analysis is performed via FFmpeg's ebur128 filter (ReplayGain 2.0, -18 LUFS reference).
Tag writing uses mutagen directly to stay consistent with the rest of the codebase.

Supported formats: MP3, FLAC, OGG Vorbis, Opus, M4A/MP4
"""

import re
import subprocess
from typing import Optional, Tuple, Dict

# ReplayGain 2.0 reference level (EBU R128)
RG_REFERENCE_LUFS = -18.0

# Tag names used across all formats
_TAG_TRACK_GAIN = "REPLAYGAIN_TRACK_GAIN"
_TAG_TRACK_PEAK = "REPLAYGAIN_TRACK_PEAK"
_TAG_ALBUM_GAIN = "REPLAYGAIN_ALBUM_GAIN"
_TAG_ALBUM_PEAK = "REPLAYGAIN_ALBUM_PEAK"

_AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.mp4'}


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
            text=True,
            timeout=120
        )
    except FileNotFoundError:
        raise FileNotFoundError("ffmpeg not found on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg timed out analyzing track")

    stderr = result.stderr

    # Parse integrated loudness: "    I:         -18.3 LUFS"
    lufs_match = re.search(r'I:\s+([-\d.]+)\s+LUFS', stderr)
    # Parse true peak: "    Peak:\s+([-\d.]+) dBFS" (may appear per-channel; take max)
    peak_matches = re.findall(r'Peak:\s+([-\d.]+)\s+dBFS', stderr)

    if not lufs_match:
        raise RuntimeError(
            f"Could not parse ebur128 output for '{file_path}'. "
            f"FFmpeg exit code: {result.returncode}"
        )

    integrated_lufs = float(lufs_match.group(1))

    if peak_matches:
        true_peak_dbfs = max(float(v) for v in peak_matches)
    else:
        # Fall back to 0 dBFS peak if not available
        true_peak_dbfs = 0.0

    return integrated_lufs, true_peak_dbfs


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
        elif ext in ('.m4a', '.mp4'):
            result['track_gain'] = _mp4_rg(audio, _TAG_TRACK_GAIN)
            result['track_peak'] = _mp4_rg(audio, _TAG_TRACK_PEAK)
            result['album_gain'] = _mp4_rg(audio, _TAG_ALBUM_GAIN)
            result['album_peak'] = _mp4_rg(audio, _TAG_ALBUM_PEAK)
    except Exception:
        pass

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
    except Exception:
        pass
    return None


def _vorbis_first(audio, key: str) -> Optional[str]:
    """Return the first value of a Vorbis comment key, or None."""
    try:
        vals = audio.get(key) or audio.get(key.upper())
        if vals:
            return str(vals[0])
    except Exception:
        pass
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
    except Exception:
        pass
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
        elif ext in ('.flac', '.ogg', '.oga', '.opus'):
            _write_vorbis_rg(audio, track_gain_str, track_peak_str, album_gain_str, album_peak_str)
        elif ext in ('.m4a', '.mp4'):
            _write_mp4_rg(audio, track_gain_str, track_peak_str, album_gain_str, album_peak_str)
        else:
            return False

        audio.save()
        return True
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
