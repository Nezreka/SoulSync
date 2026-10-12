"""Turn a finished audiobook download into a library folder.

Layout
------
Every segment of the path template is a FOLDER, and the chapter files live
inside the last one:

    Author/Series/01 - Title/01 - Chapter One.mp3

That differs from the music and podcast organizers, where the final segment is a
filename, and it is deliberate. An audiobook is a directory of ordered chapter
files that belong together, not a track; Audiobookshelf, Plex and Jellyfin all
read a book as a folder. Even a single-file m4b gets its own folder, so a book
that later gains a cover, an NFO or a chapters file has somewhere to put them.

Ordering
--------
Chapter files arrive named however the uploader left them, and a book whose
files sort wrong plays wrong. They are sorted naturally (so "Chapter 2" comes
before "Chapter 10", which a plain string sort gets backwards) and optionally
renumbered with a zero-padded prefix that every player will order correctly.

One book, one release
---------------------
A book folder is written from exactly ONE release and is never added to from
another. Audible sells the same book read by different narrators as separate
editions, and a folder that took chapters 1-6 from Michael Kramer and 7-12 from
a full-cast adaptation is not a book — it is unlistenable, and the damage is
silent because every chapter file is individually fine.

Ranking picks one release, but nothing about ranking stops a RETRY, or an
"any narrator" fallback, from later organizing a second release into the same
folder. So the folder carries a marker naming the release that filled it, and
organizing a different one into it is refused.

Safety
------
Nothing here deletes a source file. Files are copied and the caller decides what
to do with the download afterwards, so a failed organize can be retried and a
half-moved book can never lose chapters.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("audiobook_organizer")

DEFAULT_TEMPLATE = "$author/$series/$seriespos - $title"

# Extensions that are part of the book itself.
AUDIO_EXTENSIONS = frozenset({
    ".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4",
})

# Extensions worth keeping alongside it.
SIDECAR_EXTENSIONS = frozenset({".cue", ".nfo", ".txt", ".jpg", ".jpeg", ".png", ".webp"})

# Names the release a book folder was filled from. Hidden, tiny, and read
# before anything is written, so a second release cannot be mixed in.
RELEASE_MARKER = ".soulsync-release"

_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NUMBER_RUN_RE = re.compile(r"(\d+)")

# A folder segment that is nothing but leftover punctuation and a separator once
# an empty variable was substituted away — "01 - " with no title, " - " alone.
_COLLAPSIBLE_SEGMENT_RE = re.compile(r"^[\s\-_.,;:()\[\]{}]*$")


def sanitize_segment(name: str) -> str:
    """Make one path segment safe on Windows and Linux.

    Empty input returns "" rather than a placeholder: the caller collapses empty
    segments out of the path entirely, which is what lets a book with no series
    lose its series folder instead of gaining one called "Unknown".
    """
    cleaned = _ILLEGAL_CHARS_RE.sub("_", str(name or ""))
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip("_. ")
    return cleaned[:180]


def natural_key(name: str) -> tuple:
    """Sort key that reads digit runs as numbers.

    "Chapter 10" sorts after "Chapter 2" here; a plain string sort puts it
    before, which silently reorders an entire book.
    """
    parts = _NUMBER_RUN_RE.split(str(name or ""))
    key: List[Any] = []
    for index, part in enumerate(parts):
        if index % 2:
            try:
                key.append((1, int(part)))
                continue
            except ValueError:
                pass
        key.append((0, part.lower()))
    return tuple(key)


def render_audiobook_path(
    template: str,
    book: Dict[str, Any],
) -> List[str]:
    """Render a path template into folder segments.

    Supported variables:
        $author / $artist  first author
        $authorletter      first letter of the author, for A-Z shelving
        $narrator          first narrator
        $title             book title
        $series            series title, empty when standalone
        $seriespos         zero-padded position in the series, empty when none
        $year              release year
        $asin              Audible id, for a strictly unique folder

    Segments left empty or reduced to punctuation are dropped, so the default
    template collapses from "Author/Series/01 - Title" to "Author/Title" for a
    standalone book rather than leaving an empty folder or a stray "01 - ".
    """
    authors = book.get("author_names") or []
    author = str(authors[0]).strip() if authors else ""
    narrators = book.get("narrator_names") or []
    narrator = str(narrators[0]).strip() if narrators else ""

    series_list = book.get("series") or []
    series_entry = series_list[0] if series_list else {}
    series_title = str((series_entry or {}).get("title") or "").strip()
    sequence_raw = str((series_entry or {}).get("sequence") or "").strip()

    sequence = ""
    if sequence_raw:
        try:
            # Zero-pad whole numbers so 2 sorts before 10; leave 2.5 alone.
            value = float(sequence_raw)
            sequence = f"{int(value):02d}" if value == int(value) else sequence_raw
        except ValueError:
            sequence = sequence_raw

    release_date = str(book.get("release_date") or "")
    year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""

    title = str(book.get("title") or "").strip() or "Unknown Title"
    author_letter = (author[:1].upper() if author else "") or "#"

    variables = [
        ("authorletter", author_letter),
        ("author", author),
        ("artist", author),
        ("narrator", narrator),
        ("seriespos", sequence),
        ("series", series_title),
        ("title", title),
        ("year", year),
        ("asin", str(book.get("asin") or "")),
    ]

    rendered = template or DEFAULT_TEMPLATE
    for name, value in variables:
        rendered = rendered.replace("${" + name + "}", value)
        rendered = rendered.replace("$" + name, value)

    segments: List[str] = []
    for raw in rendered.replace("\\", "/").split("/"):
        part = re.sub(r"\s*\[\s*\]", "", raw)
        part = re.sub(r"\s*\(\s*\)", "", part)
        part = re.sub(r"\s*\{\s*\}", "", part)
        part = re.sub(r"\s*-\s*$", "", part)
        part = re.sub(r"^\s*-\s*", "", part)
        part = re.sub(r"\s+", " ", part).strip()
        if not part or _COLLAPSIBLE_SEGMENT_RE.match(part):
            continue
        safe = sanitize_segment(part)
        if safe:
            segments.append(safe)

    # A template that renders to nothing at all still needs somewhere to go.
    if not segments:
        segments = [sanitize_segment(title) or "Unknown Title"]
    return segments


def read_release_marker(folder: Path) -> str:
    """Which release filled this book folder, or "" when it is unmarked.

    An unmarked folder is either empty or predates the marker; both are treated
    as "no claim", so an existing library is never made un-writable by shipping
    this.
    """
    marker = Path(folder) / RELEASE_MARKER
    try:
        return marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def write_release_marker(folder: Path, release_id: str) -> None:
    """Claim a book folder for one release. Best effort — a marker that cannot
    be written must not fail an otherwise good import."""
    if not release_id:
        return
    try:
        (Path(folder) / RELEASE_MARKER).write_text(str(release_id), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not mark %s with its release: %s", folder, exc)


def folder_accepts_release(folder: Path, release_id: str) -> bool:
    """True when this release may write into this book folder.

    Refuses only when the folder is CLAIMED BY A DIFFERENT release. An unmarked
    folder, an empty release id, or the same release again all pass — re-running
    an import must stay idempotent.
    """
    if not release_id:
        return True
    claimed = read_release_marker(folder)
    return not claimed or claimed == str(release_id)


def collect_audio_files(source: Path) -> List[Path]:
    """Every audio file under a finished download, in playing order.

    Recursive because uploads nest — a book often arrives as
    ``Release.Name/Disc 1/*.mp3``. Sorted by the full relative path so disc
    folders stay in order and chapters stay in order inside them.
    """
    if not source.exists():
        return []
    if source.is_file():
        return [source] if source.suffix.lower() in AUDIO_EXTENSIONS else []

    found = [
        path for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]
    found.sort(key=lambda p: tuple(natural_key(part) for part in p.relative_to(source).parts))
    return found


def chapter_filename(index: int, original: Path, total: int, renumber: bool = True) -> str:
    """The name one chapter file gets in the library.

    Renumbering keeps the uploader's own name and only puts an ordered prefix in
    front of it, because that name is usually the chapter title and throwing it
    away loses real information. The prefix is padded to the width of the book,
    so a 120-chapter release still sorts correctly.
    """
    if not renumber:
        return original.name
    stem = sanitize_segment(original.stem) or "Chapter"
    width = max(2, len(str(max(1, total))))
    # Strip a leading number the uploader already applied, or the file ends up
    # called "01 - 01 - Chapter One".
    stem = re.sub(r"^\s*\d{1,4}\s*[-_.)\]]*\s*", "", stem) or "Chapter"
    return f"{str(index).zfill(width)} - {stem}{original.suffix.lower()}"


def organize_download(
    source_dir: str,
    book: Dict[str, Any],
    library_root: str,
    template: Optional[str] = None,
    renumber: bool = True,
    copy_sidecars: bool = True,
    release_id: str = "",
) -> Dict[str, Any]:
    """Copy a finished download into the library, ordered and named.

    Returns ``{ok, path, files, skipped, error}``. Copies rather than moves so a
    failure is always retryable and a half-organized book cannot lose chapters;
    the caller decides whether to clean the download up afterwards.

    An existing destination file is left alone and reported as skipped, so
    re-running this over a book that is already organized is a no-op rather than
    a rewrite.

    ``release_id`` claims the folder. A folder already claimed by a DIFFERENT
    release is refused outright rather than merged into — see "One book, one
    release" above.
    """
    source = Path(source_dir)
    audio = collect_audio_files(source)
    if not audio:
        return {"ok": False, "error": f"No audio files found under {source_dir}",
                "path": "", "files": [], "skipped": []}

    segments = render_audiobook_path(template or DEFAULT_TEMPLATE, book)
    destination = Path(library_root).joinpath(*segments)

    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create the audiobook folder %s: %s", destination, exc)
        return {"ok": False, "error": f"Could not create {destination}: {exc}",
                "path": "", "files": [], "skipped": []}

    if not folder_accepts_release(destination, release_id):
        claimed = read_release_marker(destination)
        logger.warning("Refusing to mix releases in %s (holds %s, offered %s)",
                       destination, claimed, release_id)
        return {
            "ok": False,
            "error": (f"{destination.name} was already filled from a different release "
                      f"({claimed}). Mixing two editions would interleave narrators."),
            "path": str(destination), "files": [], "skipped": [],
        }

    written: List[str] = []
    skipped: List[str] = []
    for index, path in enumerate(audio, start=1):
        target = destination / chapter_filename(index, path, len(audio), renumber)
        if target.exists():
            skipped.append(str(target))
            continue
        try:
            shutil.copy2(path, target)
            written.append(str(target))
        except OSError as exc:
            logger.warning("Could not copy %s into the library: %s", path, exc)
            return {"ok": False, "error": f"Could not copy {path.name}: {exc}",
                    "path": str(destination), "files": written, "skipped": skipped}

    write_release_marker(destination, release_id)

    if copy_sidecars and source.is_dir():
        _copy_sidecars(source, destination)

    return {"ok": True, "path": str(destination), "files": written,
            "skipped": skipped, "error": ""}


# Staging: build the book where media servers can't see it, then reveal it.
#
# The music side cures partial-file visibility with atomic_copy_to_staging
# (copy to a temp name, rename into place). A book folder needs the same
# guarantee at the folder level: the M4B merge deletes its source chapters
# after folding them, so filing sources into the watched library first lets
# Plex ingest files that then vanish. Staging the whole folder and revealing
# it in one atomic operation means scanners only ever see complete books.

STAGING_DIR_NAME = ".soulsync-audiobook-staging"

# Staging dirs orphaned by a crashed import are swept lazily. Imports run
# sequentially in the download monitor's tick and a merge is time-bounded
# (the ffmpeg timeout), so a live staging dir is never 24h old; anything
# older is a crash leftover.
_STAGING_MAX_AGE_SECONDS = 24 * 3600


def staging_base(library_root: str) -> Optional[Path]:
    """Where staged book folders live: next to the library root.

    Outside the library (so Plex/Audiobookshelf/Jellyfin never watch it)
    and hidden (belt and suspenders). Docker top-level mounts (library at
    /audiobooks) with a non-root container user cannot create directories
    under / — then it falls back to inside the library root, which SoulSync
    can always write; dot-prefixed folders are skipped by media-server
    scanners, so it stays invisible there too. Returns None when neither
    works — the caller then files directly into the library, exactly like
    before.
    """
    lib = Path(library_root).expanduser().resolve()
    for base in (lib.parent / STAGING_DIR_NAME, lib / STAGING_DIR_NAME):
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        _sweep_stale_staging(base)
        return base
    logger.warning("Could not set up audiobook staging for %s", library_root)
    return None


def _sweep_stale_staging(base: Path) -> None:
    """Best-effort removal of staging dirs orphaned by a crashed import."""
    now = time.time()
    try:
        entries = list(base.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            # Only touch dirs this module creates (tempfile.mkdtemp
            # prefix="book-"): never sweep anything else that wandered in.
            if not entry.is_dir() or not entry.name.startswith("book-"):
                continue
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age < _STAGING_MAX_AGE_SECONDS:
            continue
        try:
            shutil.rmtree(entry, ignore_errors=True)
            logger.info("Swept stale audiobook staging dir %s", entry.name)
        except OSError:
            pass


def new_staging_dir(base: Path) -> Optional[Path]:
    """A fresh, uniquely-named staging dir for one import attempt."""
    try:
        return Path(tempfile.mkdtemp(prefix="book-", dir=str(base)))
    except OSError as exc:
        logger.warning("Could not create audiobook staging dir in %s: %s",
                       base, exc)
        return None


def reveal_staged_book(staged_book: Path, stage_root: Path,
                       library_root: str, release_id: str = "") -> Dict[str, Any]:
    """Move a staged book folder into the real library, Plex-clean.

    ``staged_book`` is the book folder organize_download built under
    ``stage_root``; it lands at the same relative location under
    ``library_root``. When the destination does not exist yet the whole
    folder is renamed in one atomic operation — media servers see the
    complete book appear at once. Otherwise (destination exists, or the
    rename crosses filesystems as with Docker -v / NAS mounts) files move
    over one by one, each appearing atomically: a rename where possible,
    otherwise a copy to a temp name inside the destination plus rename —
    the music side's atomic_copy_to_staging shape. Either way scanners never
    see a half-written file, and never see source files that are about to be
    deleted (the M4B case that started this).

    The one-book-one-release rule is enforced against the REAL destination:
    a different release already living there refuses the reveal, with the
    same message organizing into it would have produced.
    """
    try:
        rel = staged_book.resolve().relative_to(stage_root.resolve())
    except (OSError, ValueError) as exc:
        return {"ok": False, "path": "", "files": [],
                "error": f"Staged book is not under the staging root: {exc}"}
    if not rel.parts:
        # staged_book IS the staging root — revealing it would try to
        # replace the library root itself.
        return {"ok": False, "path": "", "files": [],
                "error": "Staged book cannot be the staging root itself"}
    dest = Path(library_root).expanduser() / rel

    if not staged_book.is_dir():
        return {"ok": False, "path": "", "files": [],
                "error": f"Staged book folder is missing: {staged_book}"}

    if release_id and not folder_accepts_release(dest, release_id):
        claimed = read_release_marker(dest)
        return {
            "ok": False, "path": "", "files": [],
            "error": (f"{dest.name} was already filled from a different release "
                      f"({claimed}). Mixing two editions would interleave narrators."),
        }
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "path": "", "files": [],
                "error": f"Could not create {dest.parent}: {exc}"}

    try:
        # The fast path: one atomic rename, the book appears whole.
        os.replace(staged_book, dest)
    except OSError:
        # Destination exists, or the rename crosses filesystems — move file
        # by file, each appearing atomically.
        error = _reveal_files(staged_book, dest)
        if error:
            return {"ok": False, "path": "", "files": [], "error": error}

    try:
        files = sorted(str(p) for p in dest.rglob("*") if p.is_file())
    except OSError as exc:
        return {"ok": False, "path": str(dest), "files": [],
                "error": f"Book revealed but could not be listed: {exc}"}
    return {"ok": True, "path": str(dest), "files": files, "error": ""}


def _reveal_files(staged_book: Path, dest: Path) -> Optional[str]:
    """Move every file from ``staged_book`` into ``dest``, each atomic.

    Rename where possible; on EXDEV (Docker -v / NAS mounts: staging and
    library on different filesystems) copy to a temp name inside ``dest``
    and rename into place, then drop the staged source. Returns None on
    success, or an error string — the first failure aborts, and a retry
    overwrites the partial arrivals idempotently.
    """
    try:
        sources = sorted(p for p in staged_book.rglob("*") if p.is_file())
    except OSError as exc:
        return f"Could not walk staged book: {exc}"
    for src in sources:
        target = dest / src.relative_to(staged_book)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"Could not create {target.parent}: {exc}"
        try:
            os.replace(src, target)
            continue
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                return f"Could not reveal {src.name}: {exc}"
        # Cross-filesystem: the music side's staging shape — copy to a temp
        # name (invisible: no media extension) then swap it into place.
        tmp = target.with_name(f"{target.name}.tmp.{uuid.uuid4().hex[:8]}")
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, target)
            src.unlink()
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return f"Could not reveal {src.name}: {exc}"
    return None


def _copy_sidecars(source: Path, destination: Path) -> None:
    """Bring the cover and any notes along, best effort.

    A missing cover is a cosmetic problem; failing the whole organize over one
    would turn it into a lost book.
    """
    for path in source.rglob("*"):
        if path.name == RELEASE_MARKER:
            continue
        if not path.is_file() or path.suffix.lower() not in SIDECAR_EXTENSIONS:
            continue
        target = destination / path.name
        if target.exists():
            continue
        try:
            shutil.copy2(path, target)
        except OSError as exc:
            logger.debug("Skipping sidecar %s: %s", path, exc)


def library_root() -> str:
    """Where organized audiobooks live, from settings."""
    try:
        from core.settings import config_manager
        # Both keys are filled from the SAME settings input, so the fallback
        # is the same folder, not a second one. "download_path" is a misnomer
        # kept for installs that only ever wrote that key: nothing downloads
        # there any more, in-progress books go to the universal download
        # folder like every other source (see audiobook_grab).
        configured = (
            config_manager.get("library.audiobooks_path", "")
            or config_manager.get("audiobooks.download_path", "")
        )
    except Exception:                                       # noqa: BLE001
        configured = ""
    return str(configured or "").strip() or os.path.join(".", "audiobooks")


def configured_template() -> str:
    """The audiobook path template, honouring the organization master switch.

    When custom organization is turned off the default layout is used rather
    than a flat dump, because a folder of 300 loose chapter files is not a
    library.
    """
    try:
        from core.settings import config_manager
        if config_manager.get("file_organization.enabled", True) is False:
            return DEFAULT_TEMPLATE
        return str(
            config_manager.get("file_organization.templates.audiobook_path", "")
            or DEFAULT_TEMPLATE
        )
    except Exception:                                       # noqa: BLE001
        return DEFAULT_TEMPLATE
