"""Audiobook M4B merge — fold a multi-file book into one chaptered .m4b.

Some listeners keep multi-file releases enabled because they are easier to
find, but they still want a single-file library: one .m4b per book, with a
chapter marker per original file, instead of ninety loose mp3s.

What this does, when ``audiobooks.merge_m4b`` is on:
  - Takes the folder the organizer just filed (files already ordered and
    renamed) and probes every audio file's duration and codec with ffprobe.
  - Concatenates them with ffmpeg into ``<Title>.m4b`` in the same folder,
    writing one [CHAPTER] per source file into an ffmetadata sidecar. Chapter
    titles come from the file stems with the organizer's ``01 - `` style
    prefix stripped.
  - All-AAC-in-mp4 input with uniform codec parameters (sample rate,
    channels, AAC profile) is stream-copied (fast, lossless); anything else —
    including AAC inputs with mixed parameters, which the concat demuxer
    would otherwise play back at the wrong speed — is re-encoded to AAC
    128k, so the output is always a proper .m4b and never mp3 wearing an
    .m4b extension.
  - Verifies the result (exists, non-empty, duration within 2% of the inputs,
    one chapter per input), then deletes ONLY the source audio files. The
    encode goes to a hidden temp file in the same folder and is renamed into
    place only after verification, so media-server watchers never see a
    half-written .m4b. Sidecars and the folder stay; the post-processor then
    tags the single file exactly as it would have tagged the folder.

Fail-open everywhere: the switch off, one file, no ffmpeg, an unprobable
file, a failed encode, or a failed verify all log and hand the original file
list back untouched. The merge is an import-time convenience — it is never
worth losing a book over, and the worst case is the book stays multi-file
exactly as it arrived.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

from core.audiobook_organizer import natural_key, sanitize_segment

logger = get_logger("audiobook_m4b_merge")

# Containers that already hold AAC the way an .m4b wants it. Only these are
# eligible for a stream copy; anything else is re-encoded to AAC.
_MP4_FAMILY = frozenset({".m4a", ".m4b", ".mp4"})

_FFPROBE_TIMEOUT = 30
_FFMPEG_TIMEOUT = 3600

# One leading track-number prefix: "001 ", "01 - ", "01_", "12.". Strips
# exactly one — the organizer already renumbered these files to "01 - <name>",
# so the prefix carries no information and what follows is usually the chapter
# name. Mirrors the organizer's own strip (just as aggressive about a bare
# "1984"), so the two stay consistent.
_TRACK_PREFIX_RE = re.compile(r"^\d{1,4}\s*[-_.)\]]*\s*")


def merge_enabled() -> bool:
    """The opt-in switch, carrying its own default (no deep merge exists)."""
    try:
        from core.settings import config_manager
        return bool(config_manager.get("audiobooks.merge_m4b", False))
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read audiobooks.merge_m4b: %s", exc)
        return False


def _resolve_ffmpeg() -> Optional[str]:
    """ffmpeg on PATH, else the bundled fallback the import pipeline uses."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = Path(__file__).resolve().parent / "imports" / "tools" / "ffmpeg"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return None


def _resolve_ffprobe(ffmpeg_bin: str) -> Optional[str]:
    """ffprobe on PATH, else the binary sitting next to the resolved ffmpeg."""
    found = shutil.which("ffprobe")
    if found:
        return found
    sibling = Path(ffmpeg_bin).parent / "ffprobe"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return None


def _probe(path: Path, ffprobe: str) -> Optional[Tuple[float, str, str, str, str]]:
    """(duration_seconds, codec_name, sample_rate, channels, profile) for the
    first audio stream, or None when the file will not do.

    One ffprobe call returns both the stream parameters and the container
    duration as JSON, parsed by field name: ffprobe's csv output prints
    fields in schema order regardless of the requested order, so positional
    csv parsing would silently mislabel sample_rate/profile/channels.

    ``-select_streams a:0`` means a file with no audio stream yields an empty
    ``streams`` list, which counts as a probe failure — merging a cover.jpg
    that slipped into the file list would silently drop it otherwise. The
    extra stream parameters exist for the stream-copy gate: the concat demuxer
    with ``-c copy`` needs uniform codec parameters across inputs, and two AAC
    files at different sample rates (or LC vs HE-AAC) would otherwise merge
    into a file that plays later chapters at the wrong speed.
    """
    try:
        probed = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries",
             "stream=codec_name,sample_rate,channels,profile:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not probe %s: %s", path.name, exc)
        return None
    if probed.returncode != 0:
        logger.warning("ffprobe failed on %s", path.name)
        return None
    try:
        payload = json.loads(probed.stdout)
        streams = payload.get("streams") or []
        first = streams[0] if streams else {}
        duration_raw = (payload.get("format") or {}).get("duration")
    except (ValueError, TypeError, AttributeError, KeyError, IndexError):
        logger.warning("ffprobe gave unreadable data for %s", path.name)
        return None
    codec_name = str(first.get("codec_name") or "")
    sample_rate = str(first.get("sample_rate") or "")
    channels = str(first.get("channels") or "")
    profile = str(first.get("profile") or "")
    try:
        seconds = float(duration_raw)
    except (TypeError, ValueError):
        logger.warning("ffprobe gave no duration for %s", path.name)
        return None
    if not codec_name or seconds <= 0:
        logger.warning("ffprobe found no usable audio in %s", path.name)
        return None
    return seconds, codec_name, sample_rate, channels, profile


def chapter_title(path: Path, index: int) -> str:
    """The human title for one chapter marker.

    Strips one leading track-number prefix ("001 ", "01 - ", "01_", "12.") and
    keeps the rest — usually the real chapter name the uploader gave the file.
    A bare number or an empty stem falls back to "Chapter N" rather than an
    empty marker.
    """
    stem = _TRACK_PREFIX_RE.sub("", path.stem).strip(" -_.")
    return stem or f"Chapter {index}"


def _escape_ffmetadata(value: str) -> str:
    """Escape a value for the ffmetadata format.

    Backslash first, then the structural characters (= ; #). A literal newline
    becomes backslash-newline, which the ffmetadata demuxer reads as a line
    continuation rather than the end of the entry.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace("=", "\\=").replace(";", "\\;").replace("#", "\\#")
    return value.replace("\n", "\\\n").replace("\r", "")


def build_ffmetadata(chapters: List[Tuple[int, int, str]]) -> str:
    """Render the ffmetadata chapter block for [(start_ms, end_ms, title)].

    TIMEBASE=1/1000 keeps every timestamp an integer number of milliseconds —
    no float drift between the chapter math here and what ffmpeg writes.
    """
    lines = [";FFMETADATA1"]
    for start_ms, end_ms, title in chapters:
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={int(start_ms)}")
        lines.append(f"END={int(end_ms)}")
        lines.append(f"title={_escape_ffmetadata(title)}")
    return "\n".join(lines) + "\n"


def build_concat_list(files: List[Path]) -> str:
    """Render the ffmpeg concat demuxer file.

    Paths go single-quoted and absolute; a single quote inside a path becomes
    '\\'', the POSIX shell-style quoting the demuxer expects.
    """
    lines = []
    for path in files:
        escaped = str(path).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def _output_path(folder: Path, book: Dict[str, Any]) -> Path:
    """Where the merged book goes. Never overwrites an existing file."""
    title = str(book.get("title") or "").strip() if isinstance(book, dict) else ""
    stem = sanitize_segment(title) or sanitize_segment(folder.name) or "audiobook"
    candidate = folder / f"{stem}.m4b"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({counter}).m4b"
        counter += 1
    return candidate


def _remove_quietly(path: Path) -> None:
    """Best-effort unlink of a partial/failed output. Never raises."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove %s: %s", path.name, exc)


def sweep_unverifiable_m4b(folder: Any, known_files: Any) -> List[str]:
    """Rename aside any .m4b in the folder that is not a known input and does
    not probe as playable audio.

    A crash mid-encode (SIGKILL, power loss) strands a partial ``<Title>.m4b``
    with no usable moov atom; the retry path would otherwise keep it forever —
    tagged, ingested by media servers, and squatting the name the next merge
    wants. A file that probes fine is left strictly alone, even if it is
    unexpected: it might be the listener's own file.

    Renames, never deletes — a misidentified file is recoverable. Returns the
    new paths. Never raises.
    """
    renamed: List[str] = []
    try:
        target = Path(folder).resolve() if folder else None
        if target is None or not target.is_dir():
            return renamed
        ffmpeg = _resolve_ffmpeg()
        ffprobe = _resolve_ffprobe(ffmpeg) if ffmpeg else None
        if not ffprobe:
            return renamed
        known = set()
        for f in known_files or []:
            try:
                known.add(Path(f).resolve())
            except OSError:
                continue
        for path in sorted(target.iterdir()):
            if not (path.is_file() and path.suffix.lower() == ".m4b"):
                continue
            try:
                if path.resolve() in known:
                    continue
            except OSError:
                continue
            if _probe(path, ffprobe) is not None:
                continue
            dest = path.with_name(path.name + ".unverified-partial")
            counter = 2
            while dest.exists():
                dest = path.with_name(f"{path.name}.unverified-partial ({counter})")
                counter += 1
            try:
                path.rename(dest)
            except OSError as exc:
                logger.warning("Could not move aside %s: %s", path.name, exc)
                continue
            renamed.append(str(dest))
            logger.warning(
                "Moved an unplayable .m4b aside as %s — likely a crashed "
                "merge; it was not deleted", dest.name)
    except Exception as exc:                                # noqa: BLE001
        logger.warning("M4B sweep failed for %s: %s", folder, exc)
    return renamed


def _verify_output(output: Path, ffprobe: str, expected_seconds: float,
                   expected_chapters: int) -> bool:
    """The merged file exists, is non-empty, is as long as its inputs, and
    carries one chapter marker per input — the feature's entire point. Anything
    less and the sources stay put.
    """
    try:
        if not output.is_file() or output.stat().st_size == 0:
            logger.warning("Merge produced an empty or missing file: %s", output.name)
            return False
    except OSError as exc:
        logger.warning("Could not stat the merged file %s: %s", output.name, exc)
        return False
    probed = _probe(output, ffprobe)
    if probed is None:
        return False
    actual = probed[0]
    tolerance = max(5.0, expected_seconds * 0.02)
    if abs(actual - expected_seconds) > tolerance:
        logger.warning(
            "Merged file is %.1fs but the inputs totalled %.1fs — refusing it",
            actual, expected_seconds,
        )
        return False
    try:
        chapters = subprocess.run(
            # chapter=id only: one integer per line, immune to multi-line
            # titles or tag formatting in the chapter metadata.
            [ffprobe, "-v", "error", "-show_entries", "chapter=id",
             "-of", "csv=p=0", str(output)],
            capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not read chapters of %s: %s", output.name, exc)
        return False
    if chapters.returncode != 0:
        logger.warning("Could not read chapters of %s", output.name)
        return False
    found = sum(1 for line in chapters.stdout.splitlines() if line.strip())
    if found != expected_chapters:
        logger.warning(
            "Merged file has %d chapters but %d were expected — refusing it",
            found, expected_chapters,
        )
        return False
    return True


def merge_book_files(folder: Any, files: Any, book: Any) -> List[str]:
    """Fold a filed multi-file book into one chaptered .m4b.

    Returns ``[merged_path]`` on success, or the original file list unchanged
    on any skip or failure. Never raises: every step is guarded, and the book
    on disk is never worse off for the attempt.
    """
    originals = [str(f) for f in (files or []) if str(f or "").strip()]
    try:
        if not merge_enabled():
            return originals
        target = Path(folder).resolve() if folder else None
        if target is None or not target.is_dir():
            return originals
        ordered = sorted(
            # Absolute: the concat demuxer resolves list entries relative to
            # the list file, which lives in a temp dir — relative paths here
            # would point ffmpeg at files that do not exist.
            # Deduplicated: a doubled entry would double a chapter and still
            # verify, so collapse repeats rather than merge them twice.
            {Path(f).resolve() for f in originals if Path(f).is_file()},
            key=lambda p: natural_key(p.name),
        )
        if len(ordered) < 2:
            # Nothing to merge — a single .m4b stays exactly as it is.
            return originals

        ffmpeg = _resolve_ffmpeg()
        if not ffmpeg:
            logger.info("ffmpeg not found — leaving %s as %d files",
                        target.name, len(ordered))
            return originals
        ffprobe = _resolve_ffprobe(ffmpeg)
        if not ffprobe:
            logger.info("ffprobe not found — leaving %s as %d files",
                        target.name, len(ordered))
            return originals

        # Stale temp files from a crashed encode are ours — hidden, clearly
        # marked, never a real book — so unlike the sweep's rename-aside,
        # these are safe to remove outright.
        for stale in target.glob(".*.m4b.partial"):
            _remove_quietly(stale)
        # A crashed encode strands a partial .m4b with no usable moov atom.
        # Move those aside (never delete) before deciding the folder already
        # holds a finished book.
        sweep_unverifiable_m4b(target, ordered)
        input_set = set(ordered)
        if any(p.is_file() and p.suffix.lower() == ".m4b" and p.resolve() not in input_set
               for p in target.iterdir()):
            # A playable .m4b is already filed here that is NOT one of the
            # inputs — a retry after a crash past the verify step, or a
            # hand-placed file. Never stack a second merge on top of it; the
            # book is already in its final shape.
            logger.info("A .m4b is already filed in %s — leaving it alone",
                        target.name)
            return originals

        probed: List[Tuple[Path, float, str, str, str, str]] = []
        for path in ordered:
            info = _probe(path, ffprobe)
            if info is None:
                logger.warning("Skipping the M4B merge for %s: %s would not probe",
                               target.name, path.name)
                return originals
            probed.append((path, *info))

        total_seconds = sum(seconds for _, seconds, _, _, _, _ in probed)
        # Stream-copy needs uniform codec PARAMETERS, not just the codec name:
        # mixed sample rates or AAC profiles across chapters would otherwise
        # merge into a file that plays later chapters at the wrong speed —
        # corruption the duration check cannot catch.
        first_params = (probed[0][2].lower(), probed[0][3], probed[0][4], probed[0][5])
        copy_ok = (
            probed[0][2].lower() == "aac"
            and all(path.suffix.lower() in _MP4_FAMILY for path, _, _, _, _, _ in probed)
            and all((codec.lower(), sr, ch, prof) == first_params
                    for _, _, codec, sr, ch, prof in probed)
        )
        if copy_ok:
            codec_args = ["-c", "copy"]
        else:
            codec_args = ["-c:a", "aac", "-b:a", "128k"]

        chapters: List[Tuple[int, int, str]] = []
        cursor_ms = 0
        for index, (path, seconds, _, _, _, _) in enumerate(probed, start=1):
            start_ms = cursor_ms
            cursor_ms += int(round(seconds * 1000))
            chapters.append((start_ms, cursor_ms, chapter_title(path, index)))

        output = _output_path(target, book if isinstance(book, dict) else {})
        # Encode to a hidden temp file in the same folder and rename it into
        # place only after verification: a mid-encode .m4b sitting in the
        # library would be picked up by media-server watchers (Audiobookshelf,
        # Plex) before the moov atom exists, and a hard kill would strand it
        # past the point the except handlers below can clean up. Same
        # directory keeps the final rename atomic.
        tmp_output = output.with_name(f".{output.name}.partial")

        with tempfile.TemporaryDirectory(prefix="soulsync-m4b-") as tmp:
            tmpdir = Path(tmp)
            list_file = tmpdir / "concat.txt"
            meta_file = tmpdir / "chapters.ffmeta"
            list_file.write_text(
                build_concat_list([path for path, _, _, _, _, _ in probed]), encoding="utf-8")
            meta_file.write_text(build_ffmetadata(chapters), encoding="utf-8")
            # First audio stream only: a chapter file with a second audio
            # stream (commentary, another language) must not smuggle a
            # non-AAC stream into the .m4b on the copy path, and audiobooks
            # have no use for it on the re-encode path either. Attached
            # pictures ride on the video stream and are dropped here too —
            # post_process_book re-embeds cover.jpg afterwards.
            cmd = [ffmpeg, "-y",
                   "-f", "concat", "-safe", "0", "-i", str(list_file),
                   "-i", str(meta_file),
                   "-map", "0:a:0", "-map_metadata", "1",
                   *codec_args,
                   "-movflags", "+faststart",
                   # The temp file's .partial suffix gives ffmpeg no usable
                   # extension, so name the muxer explicitly.
                   "-f", "mp4",
                   str(tmp_output)]
            logger.info("Merging %d files into %s (%s)",
                        len(probed), output.name,
                        "stream copy" if copy_ok else "AAC re-encode")
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("ffmpeg would not run for %s: %s", target.name, exc)
                _remove_quietly(tmp_output)
                return originals
            if result.returncode != 0:
                stderr = result.stderr or ""
                logger.warning("ffmpeg merge failed for %s: %s",
                               target.name, stderr[-500:])
                _remove_quietly(tmp_output)
                return originals

        if not _verify_output(tmp_output, ffprobe, total_seconds, len(probed)):
            logger.warning("Merged file failed verification for %s; removing it",
                           target.name)
            _remove_quietly(tmp_output)
            return originals

        try:
            tmp_output.replace(output)
        except OSError as exc:
            logger.warning("Could not finalize %s: %s", output.name, exc)
            _remove_quietly(tmp_output)
            return originals

        # The merge is verified — only now do the sources go. Each unlink is
        # guarded on its own: a stray undeleted chapter is untidy, not fatal.
        for path, _, _, _, _, _ in probed:
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("Could not remove merged source %s: %s", path.name, exc)
        logger.info("Merged %d files into %s", len(probed), output.name)
        return [str(output)]
    except Exception as exc:                                # noqa: BLE001
        logger.warning("M4B merge failed for %s, keeping the originals: %s",
                       folder, exc)
        return originals
