"""Whole-album quality upgrades (asked for on Discord by hrk_hrk).

A quality profile with ``upgrade_whole_albums`` on upgrades an album as one
release instead of track by track: when any wanted track of an album is below
the profile's cutoff, the complete release is looked for on the album sources
(a Soulseek folder from one user, a torrent / usenet / Lidarr release, a
Qobuz / Tidal / Deezer album), downloaded into private staging, checked, and
only then swapped in for the old files in one go.

The rules, in the order they bite:

* a candidate has to be the whole album: one file for every track of the
  library's tracklist, nothing left over, lengths within tolerance
  (:func:`check_complete`);
* every file has to decode, reach the profile's cutoff, never be a step down
  from the file it replaces, and share one format (:func:`validate_files`);
* the swap is all or nothing (:func:`swap_album`): new files are copied next
  to the old ones under temporary names, the old ones are set aside, the new
  ones renamed in, the library rows repointed in one transaction, and only
  then do the old files go to the deleted-files folder. A failure at any step
  puts every file back where it was;
* nothing passes: the album stays exactly as it is and the reason is recorded
  (:func:`record_attempt`). Tracks of such an album never go to the per-track
  wishlist (``track_wishlist_payload`` keeps them off), so there is no silent
  fallback to single-track downloads.

Staging lives under ``<library>/.soulsync_atomic_staging/.album-upgrade-<id>``:
inside the library (same disk), hidden from media servers and SoulSync's own
scans, and dot-prefixed so the atomic-publish startup recovery never treats it
as an album batch to publish.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.album_upgrade")

STAGING_PREFIX = ".album-upgrade-"
TEMP_SUFFIX = ".soulsync-upgrade"
BACKUP_SUFFIX = ".soulsync-replaced"
JOURNAL_NAME = ".soulsync_album_swap.json"
# a file may run this much longer or shorter than the library's track
DURATION_TOLERANCE_S = 5.0
DURATION_TOLERANCE_RATIO = 0.02
# a failed album is not looked for again for this long
RETRY_AFTER_DAYS = 7
AUDIO_EXTS = frozenset({'.flac', '.mp3', '.m4a', '.mp4', '.ogg', '.oga', '.opus',
                        '.wav', '.aiff', '.aif', '.wma', '.alac', '.ape', '.wv', '.dsf'})

OUTCOME_UPGRADED = "upgraded"
OUTCOME_NO_RELEASE = "no_complete_release"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FAILED = "failed"

_RUN_LOCK = threading.Lock()
_IN_FLIGHT: set = set()
_IN_FLIGHT_LOCK = threading.Lock()


def upgrades_whole_albums(profile: Optional[Dict[str, Any]]) -> bool:
    """Whether a quality profile asks for whole-album upgrades."""
    if not profile:
        return False
    value = profile.get("upgrade_whole_albums")
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


# ── the album as the library has it ──────────────────────────────────────────

@dataclass
class LibraryTrack:
    track_id: int
    title: str
    track_number: Optional[int]
    disc_number: int
    duration_ms: Optional[int]
    file_row: Optional[Dict[str, Any]] = None
    local_path: Optional[str] = None
    wanted: bool = True
    profile: Optional[Dict[str, Any]] = None

    @property
    def has_file(self) -> bool:
        return bool(self.file_row and self.local_path)

    @property
    def label(self) -> str:
        number = f"{self.track_number:02d} " if self.track_number else ""
        return f"{number}{self.title}".strip() or f"track {self.track_id}"


@dataclass
class AlbumUnit:
    album_id: int
    title: str
    artist: str
    year: Optional[str]
    tracks: List[LibraryTrack]
    provider_ids: Dict[str, str] = field(default_factory=dict)
    profile_id: int = 1
    library_root: Optional[str] = None

    @property
    def owned(self) -> List[LibraryTrack]:
        return [t for t in self.tracks if t.has_file]

    @property
    def to_replace(self) -> List[LibraryTrack]:
        """The tracks whose files the swap replaces: owned and wanted. An owned
        track someone unmonitored keeps its file (the monitoring rules)."""
        return [t for t in self.owned if t.wanted]

    @property
    def quality_profile(self) -> Optional[Dict[str, Any]]:
        for track in self.to_replace or self.owned:
            if track.profile:
                return track.profile
        return None

    @property
    def name(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    def expected_dicts(self) -> List[Dict[str, Any]]:
        """The tracklist in the shape the download sources' matchers read."""
        return [{
            "name": t.title, "title": t.title,
            "track_number": t.track_number, "disc_number": t.disc_number,
            "duration_ms": t.duration_ms or 0,
            "artists": [{"name": self.artist}] if self.artist else [],
            "album": self.title,
        } for t in self.tracks]


def _int(value: Any) -> Optional[int]:
    try:
        number = int(str(value).split("/")[0])
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _resolve(path: Any) -> Optional[str]:
    if not path:
        return None
    text = str(path)
    if os.path.isfile(text):
        return text
    try:
        from core.library2.paths import resolve_lib2_path
        return resolve_lib2_path(text)
    except Exception:  # noqa: BLE001 - unresolvable is "not on disk here"
        return None


def library_root_for(profile_id: int, paths: Iterable[str]) -> Optional[str]:
    """The library folder the album's files are in (own library or shared)."""
    roots: List[str] = []
    try:
        from core.imports.paths import library_root_for_profile, shared_transfer_root
        own = library_root_for_profile(profile_id, announce=False) if profile_id != 1 else None
        roots = [r for r in (own, shared_transfer_root()) if r]
    except Exception:  # noqa: BLE001
        roots = []
    for root in roots:
        norm = os.path.normcase(os.path.abspath(root))
        for path in paths:
            try:
                if os.path.normcase(os.path.abspath(path)).startswith(norm + os.sep):
                    return root
            except (TypeError, ValueError):
                continue
    return roots[-1] if roots else None


def load_album_unit(conn, album_id: int, *, profile_id: int = 1,
                    resolve: Callable[[Any], Optional[str]] = _resolve) -> Optional[AlbumUnit]:
    """The album, its tracklist and the file each owned track has now."""
    from core.library2.provider_ids import provider_only, source_ids_from_values
    from core.library2.quality_eval import effective_track_profile
    from core.library2.track_files import primary_file_row
    from core.library2.wanted import track_wanted_states
    from core.library_scope import library_scope, library_scope_for_profile

    album = conn.execute(
        """SELECT al.id, al.title, al.year, al.release_date, al.spotify_id,
                  al.musicbrainz_id, al.external_ids, ar.name AS artist
             FROM lib2_albums al LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE al.id = ?""", (int(album_id),)).fetchone()
    if album is None:
        return None
    rows = conn.execute(
        """SELECT id, title, track_number, COALESCE(disc_number, 1) AS disc_number, duration
             FROM lib2_tracks WHERE album_id = ? AND canonical_track_id IS NULL
            ORDER BY COALESCE(disc_number, 1), track_number, id""",
        (int(album_id),)).fetchall()
    ids = [int(r["id"]) for r in rows]
    wanted = track_wanted_states(conn, ids, profile_id=profile_id) if ids else {}
    tracks: List[LibraryTrack] = []
    profiles: Dict[int, Dict[str, Any]] = {}
    with library_scope(library_scope_for_profile(profile_id)):
        for row in rows:
            tid = int(row["id"])
            file_row = primary_file_row(conn, tid, scoped=True)
            if file_row and (file_row.get("file_state") or "active") != "active":
                file_row = None
            try:
                profile = effective_track_profile(conn, tid)
                profile = profiles.setdefault(int(profile["id"]), profile)
            except (LookupError, TypeError, ValueError):
                profile = None
            tracks.append(LibraryTrack(
                track_id=tid, title=str(row["title"] or "").strip(),
                track_number=_int(row["track_number"]),
                disc_number=_int(row["disc_number"]) or 1,
                duration_ms=_int(row["duration"]),
                file_row=file_row,
                local_path=resolve(file_row.get("path")) if file_row else None,
                wanted=bool(wanted.get(tid, False)),
                profile=profile,
            ))
    year = str(album["year"] or "").strip() or str(album["release_date"] or "")[:4] or None
    unit = AlbumUnit(
        album_id=int(album["id"]), title=str(album["title"] or "").strip(),
        artist=str(album["artist"] or "").strip(), year=year, tracks=tracks,
        provider_ids=provider_only(source_ids_from_values(
            spotify_id=album["spotify_id"], musicbrainz_id=album["musicbrainz_id"],
            external_ids=album["external_ids"])),
        profile_id=int(profile_id),
    )
    unit.library_root = library_root_for(profile_id, [t.local_path for t in unit.owned])
    return unit


def unit_blocker(conn, database, unit: AlbumUnit) -> Optional[str]:
    """Why this album must be left alone as a whole, or None.

    A whole-album swap touches every wanted file of the album, so anything that
    protects one file protects the album: an upgrades lock, a hand-tagged file,
    an acknowledged quality override."""
    if not unit.to_replace:
        return "none of its tracks has a file to upgrade"
    for track in unit.tracks:
        if track.file_row and not track.local_path:
            # a swap that skipped it would leave the album half upgraded
            return f"{track.label} is not reachable on disk"
    if not unit.title:
        return "the album has no title to search for"
    try:
        from core.library2 import locks
        held = locks.lock_map(conn)
        if "upgrades" in held.get(("album", int(unit.album_id)), ()):
            return "upgrades are locked on this album"
    except Exception as exc:  # noqa: BLE001 - no locks table: nothing is locked
        logger.debug("[Album upgrade] lock lookup failed: %s", exc)
    try:
        from core.library2.manual_skips import active_skip_paths
        protected = active_skip_paths(conn, ("quality", "bit_depth"), profile_id=unit.profile_id)
    except Exception:  # noqa: BLE001
        protected = set()
    try:
        from core.repair_jobs.base import hand_tagged_path_keys, is_hand_tagged_path
        hand = hand_tagged_path_keys(database)
    except Exception:  # noqa: BLE001
        hand, is_hand_tagged_path = set(), None
    for track in unit.to_replace:
        stored = str(track.file_row.get("path") or "")
        if stored in protected:
            return f"{track.label} is kept at its quality on purpose"
        if is_hand_tagged_path and (is_hand_tagged_path(stored, hand)
                                    or is_hand_tagged_path(track.local_path, hand)):
            return f"{track.label} is hand tagged"
    return None


# ── is a candidate the whole album? ──────────────────────────────────────────

def _audio_length(path: str) -> Optional[float]:
    try:
        from mutagen import File as MutagenFile
        info = getattr(MutagenFile(path), "info", None)
        length = float(getattr(info, "length", 0) or 0)
        if length > 0:
            return length
    except Exception as exc:  # noqa: BLE001 - fall through to a decode
        logger.debug("[Album upgrade] header length unreadable for %s: %s", path, exc)
    try:
        from core.imports.file_integrity import probe_decoded_duration
        length = float(probe_decoded_duration(path) or 0)
        return length if length > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _read_tags(path: str) -> Dict[str, Any]:
    try:
        from core.tag_writer import read_file_tags
        return read_file_tags(path) or {}
    except Exception:  # noqa: BLE001 - untagged: the filename speaks instead
        return {}


def staged_audio_files(root: str) -> List[str]:
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def inventory(paths: Sequence[str], root: Optional[str] = None, *,
              read_tags: Callable[[str], Dict[str, Any]] = _read_tags,
              length: Callable[[str], Optional[float]] = _audio_length) -> List[Dict[str, Any]]:
    """What each staged file says it is, in the shape ``match_bundle`` reads."""
    items = []
    for path in paths:
        tags = read_tags(path) or {}
        rel = os.path.relpath(path, root) if root else os.path.basename(path)
        items.append({
            "path": path,
            "relative_path": rel.replace(os.sep, "/"),
            "title": str(tags.get("title") or "").strip() or None,
            "track_number": _int(tags.get("track_number")),
            "disc_number": _int(tags.get("disc_number")),
            "duration_seconds": length(path),
        })
    return items


def _tolerance(seconds: float) -> float:
    return max(DURATION_TOLERANCE_S, seconds * DURATION_TOLERANCE_RATIO)


def _expected_seconds(track: LibraryTrack) -> Optional[float]:
    if track.duration_ms:
        return track.duration_ms / 1000.0
    return None


def _plain_rejection(item: Dict[str, Any], unit: AlbumUnit) -> str:
    code = item.get("code")
    if code == "missing_expected_track":
        title = item.get("expected_title") or "a track"
        return f"it has no file for {title}"
    if code == "unmatched_file":
        return f"it has a file yours doesn't ({item.get('relative_path') or item.get('title')})"
    if code == "low_confidence":
        return f"{item.get('relative_path')} is not clearly one of your tracks"
    if code in ("ambiguous_position", "ambiguous_title"):
        return "two of its files could be the same track"
    if code == "no_expected_tracklist":
        return "the album has no tracklist"
    return str(code or "it does not match")


def check_complete(unit: AlbumUnit, files: Sequence[Dict[str, Any]]) -> Tuple[bool, str, Dict[int, Dict[str, Any]]]:
    """One file per track of the album, nothing over, lengths that fit.

    Returns ``(ok, reason, {track_id: file})``. Works on what is known before a
    download (a folder listing, a streaming tracklist) and on staged files."""
    from core.acquisition.bundle_matching import ExpectedTrack, match_bundle

    expected = tuple(ExpectedTrack(
        release_track_id=None, recording_id=None, track_id=t.track_id,
        disc_number=t.disc_number, track_number=t.track_number, title=t.title,
        duration_seconds=_expected_seconds(t),
    ) for t in unit.tracks)
    count = len(unit.tracks)
    if len(files) != count:
        return False, (f"it has {len(files)} track{'s' if len(files) != 1 else ''}, "
                       f"your album has {count}"), {}
    report = match_bundle(expected, list(files))
    if not report.import_ready:
        # a track that matched on position but plays another length says so
        for match in report.matches:
            got = match.file.get("duration_seconds")
            want = match.expected.duration_seconds
            if got and want and abs(float(got) - want) > _tolerance(want):
                return False, (f"{match.expected.title} runs {_mmss(got)} there and "
                               f"{_mmss(want)} in your library"), {}
        first = report.rejections[0] if report.rejections else {}
        return False, _plain_rejection(first, unit), {}
    by_track = {t.track_id: t for t in unit.tracks}
    pairs: Dict[int, Dict[str, Any]] = {}
    for match in report.matches:
        tid = match.expected.track_id
        if tid is None or tid in pairs:
            return False, "its files do not line up with your tracks one to one", {}
        pairs[tid] = dict(match.file)
    if set(pairs) != set(by_track):
        return False, "its files do not line up with your tracks one to one", {}
    for tid, item in pairs.items():
        track = by_track[tid]
        want = _expected_seconds(track)
        got = item.get("duration_seconds")
        if want is None or not got:
            continue
        if abs(float(got) - want) > _tolerance(want):
            return False, (f"{track.label} runs {_mmss(got)} there and {_mmss(want)} in your "
                           f"library"), {}
    return True, "", pairs


def _mmss(seconds: Any) -> str:
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "?"
    return f"{total // 60}:{total % 60:02d}"


# ── are the staged files good enough? ───────────────────────────────────────

def _probe(path: str):
    from core.imports.file_ops import probe_audio_quality
    return probe_audio_quality(path)


def _integrity(path: str, expected_ms: Optional[int]):
    from core.imports.file_integrity import check_audio_integrity
    return check_audio_integrity(path, expected_ms or None, verify_flac_decode=True)


def _rank(quality, profile: Dict[str, Any]) -> Tuple[int, int, str, int]:
    """(rank, target count, policy, cutoff) of a quality under a profile."""
    from core.library2.quality_eval import profile_upgrade_settings
    from core.quality.model import rank_candidate

    targets, _fallback, policy, cutoff = profile_upgrade_settings(profile)
    if not targets:
        return 0, 0, policy, cutoff
    rank, _score = rank_candidate(quality, targets)
    return rank, len(targets), policy, cutoff


def _cutoff_label(profile: Dict[str, Any]) -> str:
    from core.library2.quality_eval import profile_upgrade_settings
    targets, _fallback, policy, cutoff = profile_upgrade_settings(profile)
    if not targets:
        return "your profile"
    if policy == "acceptable":
        return "any of your profile's targets"
    index = 0 if policy == "until_top" else max(0, min(int(cutoff or 0), len(targets) - 1))
    return targets[index].label or str(targets[index].format).upper()


def validate_files(unit: AlbumUnit, pairs: Dict[int, Dict[str, Any]], *,
                   probe: Callable[[str], Any] = _probe,
                   integrity: Callable[[str, Optional[int]], Any] = _integrity) -> Tuple[bool, str, Dict[int, Any]]:
    """Every file decodes, reaches the cutoff, is no step down, one format.

    Returns ``(ok, reason, {track_id: AudioQuality})``."""
    from core.library2.quality_eval import audio_quality_from_file, upgrade_complete

    qualities: Dict[int, Any] = {}
    formats = set()
    for track in unit.tracks:
        item = pairs.get(track.track_id)
        if item is None:
            return False, f"there is no file for {track.label}", {}
        path = item["path"]
        check = integrity(path, track.duration_ms)
        if not getattr(check, "ok", False):
            return False, f"{track.label} failed the decode check ({getattr(check, 'reason', '')})", {}
        quality = probe(path)
        if quality is None:
            return False, f"the quality of {track.label} could not be read", {}
        qualities[track.track_id] = quality
        formats.add(str(quality.format or "").lower())
        profile = track.profile or unit.quality_profile
        if not profile:
            return False, f"{track.label} has no quality profile", {}
        rank, count, policy, cutoff = _rank(quality, profile)
        if count and not upgrade_complete(rank, count, policy, cutoff):
            return False, (f"{track.label} is {quality.label()}, which doesn't reach "
                           f"{_cutoff_label(profile)}"), {}
        if track.has_file:
            old = audio_quality_from_file(track.file_row)
            if old is not None and count:
                old_rank, _c, _p, _cut = _rank(old, profile)
                if rank > old_rank:
                    return False, (f"{track.label} would go from {old.label()} down to "
                                   f"{quality.label()}"), {}
    if len(formats) > 1:
        return False, "its files are not all the same format (" + ", ".join(sorted(formats)) + ")", {}
    return True, "", qualities


# ── the swap ────────────────────────────────────────────────────────────────

@dataclass
class SwapItem:
    track_id: int
    file_id: int
    staged: str
    old_local: str
    new_local: str
    old_stored: str
    new_stored: str
    temp: str = ""
    backup: str = ""
    quality: Any = None
    size: int = 0


def _swap_basename(stored: str, basename: str) -> str:
    """The stored path with its file name changed, in the stored spelling."""
    cut = max(stored.rfind("/"), stored.rfind("\\"))
    return stored[:cut + 1] + basename if cut >= 0 else basename


def plan_swap(unit: AlbumUnit, pairs: Dict[int, Dict[str, Any]],
              qualities: Dict[int, Any]) -> Tuple[Optional[List[SwapItem]], str]:
    """Where each new file goes: beside the old one, the old name with the new
    file's extension, so the library keeps its own naming and folders."""
    items: List[SwapItem] = []
    taken = set()
    for track in unit.to_replace:
        staged = pairs[track.track_id]["path"]
        old_local = track.local_path
        ext = os.path.splitext(staged)[1].lower()
        stem = os.path.splitext(os.path.basename(old_local))[0]
        new_local = os.path.join(os.path.dirname(old_local), stem + ext)
        same = os.path.normcase(os.path.abspath(new_local)) == os.path.normcase(os.path.abspath(old_local))
        if not same and os.path.exists(new_local):
            return None, f"{os.path.basename(new_local)} is already in the album folder"
        key = os.path.normcase(os.path.abspath(new_local))
        if key in taken:
            return None, f"two tracks would both be named {os.path.basename(new_local)}"
        taken.add(key)
        old_stored = str(track.file_row.get("path") or old_local)
        items.append(SwapItem(
            track_id=track.track_id, file_id=int(track.file_row["id"]), staged=staged,
            old_local=old_local, new_local=new_local, old_stored=old_stored,
            new_stored=_swap_basename(old_stored, os.path.basename(new_local)),
            temp=new_local + TEMP_SUFFIX, backup=old_local + BACKUP_SUFFIX,
            quality=qualities.get(track.track_id),
        ))
    return items, ""


def _write_journal(path: str, album_id: int, phase: str, items: List[SwapItem]) -> None:
    data = {"album_id": album_id, "phase": phase, "items": [{
        "old": i.old_local, "new": i.new_local, "temp": i.temp, "backup": i.backup,
        "file_id": i.file_id, "new_stored": i.new_stored,
    } for i in items]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def _undo(done: List[SwapItem]) -> List[str]:
    """Put the old files back. Returns what could not be put back."""
    stuck = []
    for item in reversed(done):
        try:
            if os.path.exists(item.backup):
                if os.path.normcase(item.new_local) != os.path.normcase(item.old_local) \
                        and os.path.exists(item.new_local):
                    os.remove(item.new_local)
                os.replace(item.backup, item.old_local)
        except OSError as exc:
            logger.error("[Album upgrade] could not restore %s: %s", item.old_local, exc)
            stuck.append(item.old_local)
    return stuck


def _clean_temps(items: List[SwapItem]) -> None:
    for item in items:
        try:
            if item.temp and os.path.exists(item.temp):
                os.remove(item.temp)
        except OSError:
            pass


def retire_to_deleted(backup: str, original: str, library_root: Optional[str]) -> None:
    """The replaced file goes to the library's deleted-files folder under its
    own name, restorable until retention clears it, like every upgrade."""
    from core.library import own_writes
    from core.library.deleted_quarantine import record_deleted_entry
    from core.repair_jobs.base import deleted_quarantine_root

    root = library_root or os.path.dirname(original)
    deleted_root = deleted_quarantine_root(root)
    try:
        rel = os.path.relpath(original, root)
    except ValueError:
        rel = os.path.basename(original)
    if rel.startswith("..") or os.path.isabs(rel):
        rel = os.path.basename(original)
    dest = os.path.join(deleted_root, rel)
    base, ext = os.path.splitext(dest)
    n = 1
    while os.path.exists(dest):
        dest = f"{base}_{n}{ext}"
        n += 1
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    own_writes.note(backup, dest)
    shutil.move(backup, dest)
    record_deleted_entry(deleted_root, dest, original, "album_upgrade")


def _free_gb(path: str) -> Optional[float]:
    try:
        from core.disk_guard import free_gb
        return free_gb(path)
    except Exception:  # noqa: BLE001
        return None


def swap_album(items: List[SwapItem], *, journal_dir: str, album_id: int,
               apply_db: Callable[[List[SwapItem]], None],
               retire: Callable[[str, str], None],
               copy: Callable[[str, str], Any] = shutil.copy2) -> Tuple[bool, str, List[str]]:
    """Replace every old file with its new one, or none of them.

    Returns ``(ok, reason, warnings)``. ``apply_db`` repoints the library rows
    in one transaction and raises on any failure; ``retire(backup, original)``
    moves a replaced file to the deleted-files folder (a failure there only
    warns: the new album is in and the old file is still on disk)."""
    os.makedirs(journal_dir, exist_ok=True)
    journal = os.path.join(journal_dir, JOURNAL_NAME)
    for item in items:
        if not os.path.isfile(item.old_local):
            return False, f"{os.path.basename(item.old_local)} is no longer on disk", []
    need = sum(os.path.getsize(i.staged) for i in items) / 1024 ** 3
    free = _free_gb(os.path.dirname(items[0].old_local)) if items else None
    try:
        from core.disk_guard import floor_gb
        floor = floor_gb()
    except Exception:  # noqa: BLE001
        floor = 0.0
    if free is not None and free < need + floor:
        return False, f"not enough free space in the album folder ({free:.1f} GB free)", []
    # 1. copy every new file next to its old one under a temporary name
    try:
        for item in items:
            copy(item.staged, item.temp)
            item.size = os.path.getsize(item.temp)
            if item.size != os.path.getsize(item.staged):
                raise OSError(f"copy of {os.path.basename(item.staged)} is incomplete")
    except Exception as exc:  # noqa: BLE001 - nothing of the album was touched yet
        _clean_temps(items)
        return False, f"could not copy the new files into the album folder ({exc})", []
    try:
        _write_journal(journal, album_id, "swapping", items)
    except OSError as exc:
        _clean_temps(items)
        return False, f"could not write the swap journal ({exc})", []
    # 2. set every old file aside and rename the new one in
    done: List[SwapItem] = []
    try:
        for item in items:
            os.replace(item.old_local, item.backup)
            done.append(item)
            os.replace(item.temp, item.new_local)
    except Exception as exc:  # noqa: BLE001
        stuck = _undo(done)
        _clean_temps(items)
        if not stuck:
            os.remove(journal)
        return False, f"could not swap the files ({exc}); your album was put back", []
    # 3. the library follows, all rows or none
    try:
        apply_db(items)
    except Exception as exc:  # noqa: BLE001
        stuck = _undo(done)
        if not stuck:
            os.remove(journal)
        return False, f"could not update the library ({exc}); your album was put back", []
    try:
        _write_journal(journal, album_id, "committed", items)
    except OSError as exc:
        # the swap is done: a journal still saying "swapping" would have a
        # recovery undo it under a library that already points at the new files
        logger.warning("[Album upgrade] journal update failed: %s", exc)
        try:
            os.remove(journal)
        except OSError:
            pass
    # 4. the old files go to the deleted-files folder
    warnings = []
    for item in items:
        try:
            retire(item.backup, item.old_local)
        except Exception as exc:  # noqa: BLE001 - the upgrade itself is done
            logger.warning("[Album upgrade] could not move %s to deleted files: %s", item.backup, exc)
            warnings.append(f"{os.path.basename(item.old_local)} stayed beside the new file")
    try:
        os.remove(journal)
    except OSError:
        pass
    return True, "", warnings


def db_applier(database) -> Callable[[List[SwapItem]], None]:
    """Repoint each file row at its new file, with the new file's facts."""
    def _apply(items: List[SwapItem]) -> None:
        from core.library2.status import quality_tier
        conn = database._get_connection()
        try:
            for item in items:
                q = item.quality
                fmt = str(getattr(q, "format", "") or os.path.splitext(item.new_local)[1].lstrip(".")).lower()
                bitrate = getattr(q, "bitrate", None)
                bit_depth = getattr(q, "bit_depth", None)
                cur = conn.execute(
                    """UPDATE lib2_track_files
                          SET path=?, format=?, bitrate=?, sample_rate=?, bit_depth=?, size=?,
                              quality_tier=?, acquired_quality_json=NULL, retention_json=NULL,
                              profile_rank=NULL, file_state='active', missing_since=NULL,
                              missing_scan_count=0, updated_at=CURRENT_TIMESTAMP
                        WHERE id=? AND track_id=?""",
                    (item.new_stored, fmt, bitrate, getattr(q, "sample_rate", None), bit_depth,
                     item.size or None, quality_tier(fmt, bitrate, bit_depth),
                     item.file_id, item.track_id))
                if cur.rowcount != 1:
                    raise RuntimeError(f"no library row for {os.path.basename(item.old_local)}")
            try:
                from core.library2.track_files import rank_files_by_profile
                rank_files_by_profile(conn, [i.track_id for i in items])
            except Exception as exc:  # noqa: BLE001 - ranking is a tidy-up
                logger.debug("[Album upgrade] file ranking skipped: %s", exc)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return _apply


def _library_already_moved(database, items: List[Dict[str, Any]]) -> bool:
    """Did the library rows reach the new files before the process died?"""
    if database is None or not items:
        return False
    conn = database._get_connection()
    try:
        for item in items:
            row = conn.execute("SELECT path FROM lib2_track_files WHERE id=?",
                               (int(item.get("file_id") or 0),)).fetchone()
            if row is None or row[0] != item.get("new_stored"):
                return False
        return True
    except Exception:  # noqa: BLE001 - cannot tell: the files go back
        return False
    finally:
        conn.close()


def recover_interrupted_swaps(library_roots: Iterable[str], database=None) -> int:
    """A swap a restart cut short: before the library was updated every old
    file goes back; after it, the old files go on to deleted files. Then the
    staging is removed. Returns how many albums were seen to."""
    from core.downloads.atomic_album_publish import STAGING_DIRNAME
    handled = 0
    for root in library_roots:
        parent = os.path.join(str(root or ""), STAGING_DIRNAME)
        try:
            names = os.listdir(parent)
        except OSError:
            continue
        for name in names:
            if not name.startswith(STAGING_PREFIX):
                continue
            staging = os.path.join(parent, name)
            journal = os.path.join(staging, JOURNAL_NAME)
            if os.path.isfile(journal):
                try:
                    with open(journal, encoding="utf-8") as handle:
                        data = json.load(handle)
                    items = [SwapItem(0, 0, "", i["old"], i["new"], "", "", i["temp"], i["backup"])
                             for i in data.get("items") or []]
                    if data.get("phase") == "committed" or _library_already_moved(
                            database, data.get("items") or []):
                        for item in items:
                            if os.path.exists(item.backup):
                                retire_to_deleted(item.backup, item.old_local, root)
                    else:
                        _undo(items)
                        _clean_temps(items)
                    logger.warning("[Album upgrade] finished an interrupted swap for album %s",
                                   data.get("album_id"))
                except Exception as exc:  # noqa: BLE001 - leave it for a person
                    logger.error("[Album upgrade] could not recover %s: %s", staging, exc)
                    continue
            shutil.rmtree(staging, ignore_errors=True)
            handled += 1
    return handled


# ── tags ────────────────────────────────────────────────────────────────────

def write_library_tags(database, unit: AlbumUnit, items: List[SwapItem]) -> Optional[str]:
    """The library's metadata and cover go onto the staged files before the
    swap, so the new album carries the same tags the old one did."""
    from core.library2 import retag
    from core.tag_writer import write_tags_to_file

    conn = database._get_connection()
    try:
        contexts = {int(c["id"]): c for c in retag.track_contexts(
            conn, [i.track_id for i in items], lyrics=False)}
    finally:
        conn.close()
    try:
        cover = retag._album_cover_data(database, unit.album_id)
    except Exception:  # noqa: BLE001 - no cover is not a reason to stop
        cover = None
    for item in items:
        context = contexts.get(item.track_id)
        if not context:
            return f"no metadata for track {item.track_id}"
        result = write_tags_to_file(item.staged, context["db_data"],
                                    embed_cover=bool(cover), cover_data=cover)
        if not result.get("success"):
            return f"could not tag {os.path.basename(item.staged)} ({result.get('error')})"
    return None


# ── the record ──────────────────────────────────────────────────────────────

ATTEMPTS_DDL = """
CREATE TABLE IF NOT EXISTS album_upgrade_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id INTEGER NOT NULL,
    profile_id INTEGER NOT NULL DEFAULT 1,
    album_title TEXT,
    artist_name TEXT,
    outcome TEXT NOT NULL,
    source TEXT,
    release TEXT,
    reason TEXT,
    tried_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""
ATTEMPTS_INDEX = ("CREATE INDEX IF NOT EXISTS idx_album_upgrade_attempts_album "
                  "ON album_upgrade_attempts(album_id, created_at)")


def ensure_attempts_schema(conn) -> None:
    conn.execute(ATTEMPTS_DDL)
    conn.execute(ATTEMPTS_INDEX)


def record_attempt(database, unit_or_id, *, profile_id: int, outcome: str, reason: str = "",
                   source: str = "", release: str = "", tried: Sequence[Dict[str, Any]] = ()) -> None:
    unit = unit_or_id if isinstance(unit_or_id, AlbumUnit) else None
    album_id = unit.album_id if unit else int(unit_or_id)
    conn = database._get_connection()
    try:
        ensure_attempts_schema(conn)
        conn.execute(
            """INSERT INTO album_upgrade_attempts
                   (album_id, profile_id, album_title, artist_name, outcome, source, release,
                    reason, tried_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (album_id, int(profile_id), unit.title if unit else None,
             unit.artist if unit else None, outcome, source or None, release or None,
             reason or None, json.dumps(list(tried))))
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - the record never sinks the upgrade
        logger.warning("[Album upgrade] could not record the attempt: %s", exc)
    finally:
        conn.close()


def recent_attempts(database, *, profile_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    conn = database._get_connection()
    try:
        ensure_attempts_schema(conn)
        sql = "SELECT * FROM album_upgrade_attempts"
        params: tuple = ()
        if profile_id is not None:
            sql += " WHERE profile_id=?"
            params = (int(profile_id),)
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, int(limit))).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["tried"] = json.loads(item.pop("tried_json") or "[]")
            except (TypeError, ValueError):
                item["tried"] = []
            out.append(item)
        return out
    finally:
        conn.close()


def _recently_failed(conn, profile_id: int, days: int) -> set:
    try:
        ensure_attempts_schema(conn)
        rows = conn.execute(
            """SELECT album_id FROM album_upgrade_attempts
                WHERE profile_id=? AND outcome<>? AND created_at > datetime('now', ?)""",
            (int(profile_id), OUTCOME_UPGRADED, f"-{int(days)} days")).fetchall()
        return {int(r[0]) for r in rows}
    except Exception:  # noqa: BLE001
        return set()


# ── which albums ────────────────────────────────────────────────────────────

def _any_whole_album_profile(conn) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM quality_profiles WHERE upgrade_whole_albums=1 LIMIT 1").fetchone() is not None
    except Exception:  # noqa: BLE001 - an older table has no such column
        return False


def pending_album_ids(conn, *, profile_id: int = 1, limit: int = 5,
                      retry_after_days: int = RETRY_AFTER_DAYS) -> List[int]:
    """Albums with a wanted track below its cutoff under a whole-album profile.

    The same candidates the per-track wishlist would queue (the wanted
    projection, locks, hand-tagged files and quality overrides already applied
    by ``upgrade_candidate_track_ids``), grouped by album. An album that failed
    lately waits ``retry_after_days`` before it is looked for again."""
    from core.library2.wishlist_mirror import upgrade_candidate_track_ids

    if not _any_whole_album_profile(conn):
        return []
    candidates = upgrade_candidate_track_ids(conn, profile_id=profile_id, definite_only=True,
                                             whole_albums=True)
    if not candidates:
        return []
    recent = _recently_failed(conn, profile_id, retry_after_days)
    albums = conn.execute(
        "SELECT DISTINCT album_id FROM lib2_tracks WHERE id IN (SELECT value FROM json_each(?))"
        " AND album_id IS NOT NULL ORDER BY album_id",
        (json.dumps(candidates),)).fetchall()
    seen: List[int] = []
    for (album_id,) in albums:
        album_id = int(album_id)
        if album_id in recent or album_id in seen:
            continue
        with _IN_FLIGHT_LOCK:
            if album_id in _IN_FLIGHT:
                continue
        seen.append(album_id)
        if len(seen) >= max(1, int(limit)):
            break
    return seen


# ── one album ───────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    album_id: int
    outcome: str
    reason: str = ""
    source: str = ""
    release: str = ""
    name: str = ""
    tried: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def upgraded(self) -> bool:
        return self.outcome == OUTCOME_UPGRADED

    def log_line(self) -> str:
        if self.upgraded:
            line = f"Upgraded {self.name} as a whole album from {self.source}"
            return line + (f" ({self.release})" if self.release else "")
        return f"Left {self.name or f'album {self.album_id}'} as it is: {self.reason}"


def staging_dir_for(unit: AlbumUnit, attempt: int) -> str:
    from core.downloads.atomic_album_publish import STAGING_DIRNAME
    root = unit.library_root or os.path.dirname(os.path.dirname(unit.owned[0].local_path))
    return os.path.join(root, STAGING_DIRNAME, f"{STAGING_PREFIX}{unit.album_id}-{attempt}")


def _summarize(tried: List[Dict[str, Any]]) -> str:
    if not tried:
        return "no album source is set up"
    parts = []
    for entry in tried[:4]:
        label = entry.get("source") or "?"
        parts.append(f"{label}: {entry.get('reason')}")
    more = len(tried) - 4
    return "no complete release passed (" + "; ".join(parts) + (f"; {more} more" if more > 0 else "") + ")"


def upgrade_album(database, album_id: int, *, profile_id: int = 1,
                  sources: Optional[Sequence[str]] = None,
                  fetchers: Optional[Dict[str, Callable]] = None,
                  config_get: Optional[Callable[..., Any]] = None,
                  emit: Optional[Callable[[str], None]] = None,
                  swap: Callable[..., Tuple[bool, str, List[str]]] = swap_album,
                  tagger: Optional[Callable[[Any, AlbumUnit, List[SwapItem]], Optional[str]]] = None,
                  validators: Optional[Dict[str, Callable]] = None) -> Outcome:
    """Find, check and swap in the complete release of one album, or leave it.

    Never queues a per-track download, whatever happens."""
    from core.library2 import album_upgrade_sources as _sources

    with _IN_FLIGHT_LOCK:
        if album_id in _IN_FLIGHT:
            return Outcome(album_id, OUTCOME_SKIPPED, "this album is already being upgraded")
        _IN_FLIGHT.add(album_id)
    try:
        return _upgrade_album(database, int(album_id), profile_id=int(profile_id),
                              sources=sources, fetchers=fetchers or _sources.FETCHERS,
                              config_get=config_get or _config_get(), emit=emit or (lambda _m: None),
                              swap=swap, tagger=tagger or write_library_tags,
                              validators=validators or {})
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(album_id)


def _config_get():
    from core.settings import config_manager
    return config_manager.get


def _upgrade_album(database, album_id, *, profile_id, sources, fetchers, config_get, emit,
                   swap, tagger, validators) -> Outcome:
    from core.library2 import album_upgrade_sources as _sources

    conn = database._get_connection()
    try:
        unit = load_album_unit(conn, album_id, profile_id=profile_id)
        if unit is None:
            return Outcome(album_id, OUTCOME_SKIPPED, "the album is gone")
        blocker = unit_blocker(conn, database, unit)
    finally:
        conn.close()
    result = Outcome(album_id, OUTCOME_SKIPPED, name=unit.name)
    if blocker:
        result.reason = blocker
        record_attempt(database, unit, profile_id=profile_id, outcome=OUTCOME_SKIPPED, reason=blocker)
        return result
    if not upgrades_whole_albums(unit.quality_profile):
        result.reason = "its quality profile upgrades track by track"
        return result
    try:
        from core.disk_guard import music_has_room
        ok, free, floor = music_has_room()
    except Exception:  # noqa: BLE001 - never wedge on a probe
        ok, free, floor = True, None, 0
    if not ok:
        # not recorded: a full disk is no reason to wait a week once it has room
        result.reason = f"the download disk is below its free-space floor ({free:.1f} of {floor:.0f} GB)"
        return result

    check = validators.get("complete", check_complete)
    validate = validators.get("files", validate_files)
    read = validators.get("inventory", inventory)
    order = list(sources) if sources is not None else _sources.enabled_sources(config_get)
    tried: List[Dict[str, Any]] = []
    attempt_no = 0
    context = _sources.FetchContext(database=database, unit=unit, config_get=config_get,
                                    emit=emit, check=check)
    for source in order:
        fetch = fetchers.get(source)
        if fetch is None:
            continue
        emit(f"{unit.name}: looking on {source}")
        try:
            candidates = fetch(context, source)
            for candidate in candidates:
                if candidate.rejected:
                    tried.append({"source": source, "release": candidate.label,
                                  "reason": candidate.rejected})
                    continue
                attempt_no += 1
                staging = staging_dir_for(unit, attempt_no)
                shutil.rmtree(staging, ignore_errors=True)
                os.makedirs(staging, exist_ok=True)
                try:
                    verdict = _try_candidate(database, unit, candidate, staging, read=read,
                                             check=check, validate=validate, swap=swap,
                                             tagger=tagger, emit=emit)
                finally:
                    if os.path.isdir(staging) and not os.path.exists(os.path.join(staging, JOURNAL_NAME)):
                        shutil.rmtree(staging, ignore_errors=True)
                if verdict.ok:
                    result.outcome = OUTCOME_UPGRADED
                    result.source = source
                    result.release = candidate.label
                    result.warnings = verdict.warnings
                    result.tried = tried
                    record_attempt(database, unit, profile_id=profile_id, outcome=OUTCOME_UPGRADED,
                                   source=source, release=candidate.label,
                                   reason="; ".join(verdict.warnings), tried=tried)
                    return result
                tried.append({"source": source, "release": candidate.label, "reason": verdict.reason})
                if verdict.stop:
                    result.outcome = OUTCOME_FAILED
                    result.reason = verdict.reason
                    result.tried = tried
                    record_attempt(database, unit, profile_id=profile_id, outcome=OUTCOME_FAILED,
                                   reason=verdict.reason, tried=tried)
                    return result
        except Exception as exc:  # noqa: BLE001 - one source failing is a miss, not a crash
            logger.exception("[Album upgrade] %s failed for %s", source, unit.name)
            tried.append({"source": source, "reason": f"the search failed ({exc})"})
    result.outcome = OUTCOME_NO_RELEASE
    result.reason = _summarize(tried)
    result.tried = tried
    record_attempt(database, unit, profile_id=profile_id, outcome=OUTCOME_NO_RELEASE,
                   reason=result.reason, tried=tried)
    return result


@dataclass
class _Verdict:
    ok: bool
    reason: str = ""
    warnings: List[str] = field(default_factory=list)
    # a swap that could not be undone: stop trying other releases
    stop: bool = False


def _try_candidate(database, unit, candidate, staging, *, read, check, validate, swap,
                   tagger, emit) -> _Verdict:
    emit(f"{unit.name}: downloading {candidate.label}")
    fetched = candidate.fetch(staging)
    if fetched.error:
        return _Verdict(False, fetched.error)
    files = staged_audio_files(staging)
    if not files:
        return _Verdict(False, "nothing was downloaded")
    emit(f"{unit.name}: checking {len(files)} files")
    ok, reason, pairs = check(unit, read(files, staging))
    if not ok:
        return _Verdict(False, reason)
    ok, reason, qualities = validate(unit, pairs)
    if not ok:
        return _Verdict(False, reason)
    items, reason = plan_swap(unit, pairs, qualities)
    if items is None:
        return _Verdict(False, reason)
    problem = tagger(database, unit, items)
    if problem:
        return _Verdict(False, problem)
    emit(f"{unit.name}: swapping in {len(items)} files")
    ok, reason, warnings = swap(
        items, journal_dir=staging, album_id=unit.album_id, apply_db=db_applier(database),
        retire=lambda backup, original: retire_to_deleted(backup, original, unit.library_root))
    if not ok:
        # a journal left behind means some file could not be put back
        stuck = os.path.exists(os.path.join(staging, JOURNAL_NAME))
        return _Verdict(False, reason, stop=stuck)
    return _Verdict(True, warnings=warnings)


# ── many albums ─────────────────────────────────────────────────────────────

def run_album_upgrades(database, *, profile_id: int = 1, limit: int = 5,
                       album_ids: Optional[Sequence[int]] = None,
                       progress: Optional[Callable[[str], None]] = None,
                       on_upgraded: Optional[Callable[[Outcome], None]] = None,
                       **kwargs) -> List[Outcome]:
    """Upgrade the pending whole-album upgrades (or the given albums), one at
    a time. A second run while one is going waits for it."""
    say = progress or (lambda _m: None)
    with _RUN_LOCK:
        try:
            recover_interrupted_swaps(_library_roots(profile_id), database)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Album upgrade] swap recovery skipped: %s", exc)
        if album_ids is None:
            conn = database._get_connection()
            try:
                album_ids = pending_album_ids(conn, profile_id=profile_id, limit=limit)
            finally:
                conn.close()
        outcomes = []
        for album_id in album_ids:
            outcome = upgrade_album(database, int(album_id), profile_id=profile_id,
                                    emit=say, **kwargs)
            outcomes.append(outcome)
            _post_activity(outcome)
            if outcome.upgraded and on_upgraded:
                try:
                    on_upgraded(outcome)
                except Exception:  # noqa: BLE001
                    logger.debug("[Album upgrade] after-upgrade hook failed", exc_info=True)
        return outcomes


def _post_activity(outcome: Outcome) -> None:
    """Each album the upgrade looked at shows in the activity feed."""
    if outcome.outcome == OUTCOME_SKIPPED and not outcome.name:
        return
    try:
        from core.runtime_state import add_activity_item
        title = "Album Upgraded" if outcome.upgraded else "Album Upgrade Skipped"
        add_activity_item("", title, outcome.log_line(), "Now", show_toast=outcome.upgraded)
    except Exception as exc:  # noqa: BLE001 - the feed is decoration
        logger.debug("[Album upgrade] activity item failed: %s", exc)


def _library_roots(profile_id: int) -> List[str]:
    roots = []
    try:
        from core.imports.paths import library_root_for_profile, shared_transfer_root
        roots.append(shared_transfer_root())
        own = library_root_for_profile(profile_id, announce=False) if profile_id != 1 else None
        if own:
            roots.append(own)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Album upgrade] library roots unavailable: %s", exc)
    return [r for r in roots if r]


def all_library_roots(database) -> List[str]:
    """The shared library and every profile's own library."""
    roots = _library_roots(1)
    try:
        from core.imports.paths import library_root_for_profile
        for profile in database.get_all_profiles() or []:
            own = library_root_for_profile(profile.get("id"), announce=False)
            if own and own not in roots:
                roots.append(own)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Album upgrade] own libraries unavailable: %s", exc)
    return roots


_QUEUE: List[Tuple[int, int]] = []
_QUEUE_LOCK = threading.Lock()
_WORKER: Optional[threading.Thread] = None


def queue_album_upgrades(database, album_ids: Iterable[int], *, profile_id: int = 1,
                         on_upgraded: Optional[Callable[[Outcome], None]] = None) -> int:
    """Upgrade these albums in the background (a search or Enhance Quality on
    an album whose profile upgrades whole albums). Returns how many queued."""
    global _WORKER
    added = 0
    with _QUEUE_LOCK:
        for album_id in album_ids:
            key = (int(album_id), int(profile_id))
            if key not in _QUEUE:
                _QUEUE.append(key)
                added += 1
        if added and (_WORKER is None or not _WORKER.is_alive()):
            _WORKER = threading.Thread(target=_drain_queue, args=(database, on_upgraded),
                                       name="album-upgrades", daemon=True)
            _WORKER.start()
    return added


def _drain_queue(database, on_upgraded) -> None:
    while True:
        with _QUEUE_LOCK:
            if not _QUEUE:
                return
            album_id, profile_id = _QUEUE.pop(0)
        try:
            outcomes = run_album_upgrades(database, profile_id=profile_id, album_ids=[album_id],
                                          on_upgraded=on_upgraded)
            for outcome in outcomes:
                logger.info("[Album upgrade] %s", outcome.log_line())
        except Exception:  # noqa: BLE001 - the next album still runs
            logger.exception("[Album upgrade] album %s failed", album_id)
        time.sleep(0)


__all__ = [
    "AlbumUnit", "LibraryTrack", "Outcome", "SwapItem",
    "check_complete", "inventory", "load_album_unit", "pending_album_ids",
    "plan_swap", "queue_album_upgrades", "recent_attempts", "record_attempt",
    "recover_interrupted_swaps", "run_album_upgrades", "swap_album",
    "upgrade_album", "upgrades_whole_albums", "validate_files",
]
