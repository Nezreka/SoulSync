"""How much of the media server's music SoulSync can read directly.

SoulSync scans the folders it's given (the music library folder and the extra
library folders), and the path resolver finds a media server's file under one
of them by its trailing folders. So the question for each music folder the
media server reports is: is that folder one SoulSync is given, mounted and
readable? When it isn't, why not, in words a person can act on:

* ``not_mounted``  SoulSync can't see the folder at all. The drive or share has
  to be mounted into SoulSync (a Docker volume, an fstab line).
* ``not_library``  SoulSync can see it, but it isn't one of its library
  folders, so nothing in it is scanned. One click adds it.
* ``partly_covered`` some folders inside it are library folders, the rest
  isn't scanned.
* ``covered``      it is a library folder (under its own name, or a different
  one SoulSync worked out); the sample says how much of it reads.

Each folder's share is measured, not assumed: up to ``SAMPLE`` of the tracks
the server reports under it are translated to SoulSync's path and opened.
Counting and sampling are range scans on
``idx_lib2_media_server_mappings_track_path``, so a million-track library
costs a few index seeks per folder, never a table scan.
"""

from __future__ import annotations

import os
import random
import stat
import threading
import time
from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from core.library import path_mappings
from utils.logging_config import get_logger

logger = get_logger("library.coverage")

SAMPLE = 40
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_EXAMPLES = 3

# reasons a sampled file can't be read, in the words the page shows
REASONS = {
    "ok": "Read fine",
    "not_mounted": "The folder isn't there inside SoulSync",
    "missing": "The folder is there but this file isn't, so the folder may be the wrong one",
    "permission": "The file is there but SoulSync isn't allowed to read it",
    "size": "A file is there but its size doesn't match the one SoulSync recorded",
    "unreadable": "The file is there but reading it failed",
    "not_a_file": "That path is a folder, not a file",
    "not_library": "SoulSync can read it, but it isn't in one of SoulSync's library folders",
}


# ---------------------------------------------------------------- the index
_TRACKS = ("FROM lib2_media_server_mappings"
           " WHERE entity_type = 'track' AND server_source = ?")


def folder_range(folder: str) -> Tuple[str, str]:
    """``(low, high)`` so ``low <= server_path < high`` is every path under the
    folder. The separator is the folder's own: '/' for /music, '\\' for
    D:\\Music."""
    text = str(folder or "")
    sep = "\\" if "\\" in text and "/" not in text else "/"
    base = text.rstrip("/\\")
    return base + sep, base + chr(ord(sep) + 1)


def count_tracks(conn: Any, server_kind: str, folder: Optional[str] = None) -> int:
    """the server's tracks with a path, under ``folder`` when given"""
    if folder is None:
        sql = f"SELECT COUNT(*) {_TRACKS} AND server_path IS NOT NULL AND server_path <> ''"
        return int(conn.execute(sql, (server_kind,)).fetchone()[0] or 0)
    low, high = folder_range(folder)
    sql = f"SELECT COUNT(*) {_TRACKS} AND server_path >= ? AND server_path < ?"
    return int(conn.execute(sql, (server_kind, low, high)).fetchone()[0] or 0)


def sample_tracks(conn: Any, server_kind: str, folder: Optional[str], n: int = SAMPLE,
                  rng: Optional[random.Random] = None) -> List[Tuple[int, int, str]]:
    """up to ``n`` ``(mapping id, track id, server path)`` under ``folder``,
    spread over the folder: each pick is one seek to the first path at or
    after a random point in it. When the random picks keep landing on the same
    tracks (a small folder), the rest is read in order, so a folder with fewer
    than ``n`` tracks returns all of them."""
    rng = rng or random.Random()
    if folder is None:
        low, high = "", "\U0010ffff"
    else:
        low, high = folder_range(folder)
    seek = (f"SELECT id, entity_id, server_path {_TRACKS}"
            " AND server_path >= ? AND server_path < ? ORDER BY server_path LIMIT 1")
    picked: Dict[int, Tuple[int, int, str]] = {}
    for _ in range(n * 2):
        if len(picked) >= n:
            break
        pivot = low + "".join(rng.choice(_ALPHABET) for _ in range(2))
        row = conn.execute(seek, (server_kind, max(low, pivot), high)).fetchone() \
            or conn.execute(seek, (server_kind, low, high)).fetchone()
        if not row:
            return []
        picked.setdefault(int(row[0]), (int(row[0]), int(row[1]), str(row[2])))
    if len(picked) < n:
        walk = (f"SELECT id, entity_id, server_path {_TRACKS}"
                " AND server_path >= ? AND server_path < ? ORDER BY server_path LIMIT ?")
        for r in conn.execute(walk, (server_kind, low, high, n * 2)).fetchall():
            if len(picked) >= n:
                break
            picked.setdefault(int(r[0]), (int(r[0]), int(r[1]), str(r[2])))
    return sorted(picked.values(), key=lambda r: r[2])


def recorded_size(conn: Any, track_id: int, local_path: str) -> Optional[int]:
    """the size SoulSync's catalogue has for this file at this path, if any"""
    try:
        row = conn.execute(
            "SELECT size FROM lib2_track_files WHERE path = ? AND track_id = ? LIMIT 1",
            (local_path, int(track_id))).fetchone()
    except Exception:  # noqa: BLE001 - no file table: nothing to compare with
        return None
    return int(row[0]) if row and row[0] else None


# ---------------------------------------------------------------- one file
def probe_file(path: str, expected_size: Optional[int] = None) -> str:
    """'ok' or the reason the file can't be read (a REASONS key)"""
    try:
        st = os.stat(path)
    except PermissionError:
        return "permission"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    if not stat.S_ISREG(st.st_mode):
        return "not_a_file"
    if expected_size and st.st_size != expected_size:
        return "size"
    try:
        with open(path, "rb") as fh:
            fh.read(4096)
    except PermissionError:
        return "permission"
    except OSError:
        return "unreadable"
    return "ok"


# ---------------------------------------------------------------- folders
def _parts(path: str) -> List[str]:
    return [p for p in str(path or "").replace("\\", "/").split("/") if p]


def inside(path: str, root: str) -> bool:
    """``path`` is ``root`` or under it (case folded for Windows paths)"""
    return path_mappings.relative_to(path, root) is not None


def library_folder_for(local: str, library_dirs: Iterable[str]) -> Optional[str]:
    """the SoulSync library folder holding ``local``, the deepest one"""
    best = None
    for root in library_dirs:
        if root and inside(local, root) and (best is None or len(root) > len(best)):
            best = root
    return best


def library_folders_inside(local: str, library_dirs: Iterable[str]) -> List[str]:
    """SoulSync library folders that sit inside ``local``: only those parts of
    it are scanned"""
    return [root for root in library_dirs if root and inside(root, local)
            and path_mappings.normalize_prefix(root) != path_mappings.normalize_prefix(local)]


def _tail_score(server_folder: str, local_folder: str) -> int:
    """how many trailing folder names the two share (case folded)"""
    a, b = _parts(server_folder), _parts(local_folder)
    n = 0
    while n < min(len(a), len(b)) and a[-1 - n].casefold() == b[-1 - n].casefold():
        n += 1
    return n


def _translate_with(server_path: str, server_folder: str, local_folder: str) -> Optional[str]:
    rest = path_mappings.relative_to(server_path, server_folder)
    if rest is None:
        return None
    return path_mappings.join_local(local_folder, rest)


def learned_folder(pairs: Iterable[Tuple[str, str]], server_folder: str,
                   isdir: Callable[[str], bool]) -> Optional[str]:
    """SoulSync's name for ``server_folder``, from files the catalogue holds
    under both names: (local file, server file) pairs whose tails agree below
    the folder vote for the local folder. Three agreeing files, or every file
    when there are fewer, and the folder has to exist here."""
    votes: Counter = Counter()
    for local, server in pairs:
        rest = path_mappings.relative_to(server, server_folder)
        if not rest or not local:
            continue
        rest_parts, local_parts = _parts(rest), _parts(local)
        if len(local_parts) < len(rest_parts) or [p.casefold() for p in local_parts[-len(rest_parts):]] \
                != [p.casefold() for p in rest_parts]:
            continue
        text = str(local).rstrip("/\\")
        cut = len(text)
        for _ in rest_parts:
            cut = max(text.rfind("/", 0, cut), text.rfind("\\", 0, cut))
        root = text[:cut] if cut > 0 else ("/" if text.startswith("/") else "")
        if root:
            votes[root] += 1
    if not votes:
        return None
    root, n = votes.most_common(1)[0]
    total = sum(votes.values())
    if n < min(3, total) or not isdir(root):
        return None
    return root


def suffix_candidates(server_folder: str, dirs: Iterable[str],
                      listdir: Callable[[str], List[str]]) -> List[str]:
    """folders SoulSync sees whose trailing names match the server folder's,
    best first: each library folder, its subfolders, each mount point"""
    seen, scored = set(), []
    pool: List[str] = []
    for d in dirs:
        if not d or d in seen:
            continue
        seen.add(d)
        pool.append(d)
        try:
            for child in listdir(d)[:200]:
                pool.append(os.path.join(d, child))
        except OSError:
            continue
    for cand in pool:
        score = _tail_score(server_folder, cand)
        if score:
            scored.append((score, cand))
    scored.sort(key=lambda s: (-s[0], len(s[1])))
    out: List[str] = []
    for _s, cand in scored:
        if cand not in out:
            out.append(cand)
    return out


class Env:
    """the filesystem and settings the check reads; tests pass their own"""

    def __init__(self, *, library_dirs: List[str], mounts: Optional[List[str]] = None,
                 docker: bool = False, isdir: Callable[[str], bool] = os.path.isdir,
                 listdir: Callable[[str], List[str]] = None,
                 probe: Callable[[str, Optional[int]], str] = probe_file,
                 learned: Callable[[str], Optional[str]] = None,
                 overrides: Optional[List[dict]] = None):
        self.library_dirs = [d for d in library_dirs if d]
        self.mounts = list(mounts or [])
        self.docker = docker
        self.isdir = isdir
        self.listdir = listdir or _subdirs
        self.probe = probe
        self.learned = learned or (lambda _folder: None)
        self.overrides = overrides or []


def _subdirs(path: str) -> List[str]:
    out = []
    with os.scandir(path) as it:
        for e in it:
            try:
                if e.is_dir():
                    out.append(e.name)
            except OSError:
                continue
    return sorted(out)


def locate_folder(conn: Any, server_kind: str, server_folder: str, env: Env,
                  sample: List[Tuple[int, int, str]]) -> Tuple[Optional[str], str, Optional[str]]:
    """``(local folder, how, broken override)``. ``how`` is override, same,
    learned or suffix; ``(None, '', ...)`` when SoulSync can't see it."""
    broken = None
    for local in path_mappings.translate(server_folder, env.overrides, server_kind=server_kind):
        if env.isdir(local):
            return local, "override", None
        broken = broken or local
    if not path_mappings.foreign_rooted(server_folder) and env.isdir(server_folder):
        return server_folder, "same", broken
    learned = env.learned(server_folder)
    if learned and env.isdir(learned):
        return learned, "learned", broken
    try:
        from core.library2.scan_paths import server_pairs
        pairs = server_pairs(conn, server_kind, limit=2000)
    except Exception:  # noqa: BLE001 - no catalogue pairs: nothing learned
        pairs = []
    learned = learned_folder(pairs, server_folder, env.isdir)
    if learned:
        return learned, "learned", broken
    # a different name: a library folder, a subfolder of one or a mount point
    # whose trailing names match, and that the sampled files are really under
    probes = sample[:5]
    for cand in suffix_candidates(server_folder, env.library_dirs + env.mounts, env.listdir):
        if not env.isdir(cand):
            continue
        hits = 0
        for _mid, _tid, server_path in probes:
            local = _translate_with(server_path, server_folder, cand)
            if local and env.probe(local, None) in ("ok", "size", "permission"):
                hits += 1
        if hits:
            return cand, "suffix", broken
    return None, "", broken


def volume_example(server_folder: str) -> str:
    """a compose volume line for a folder SoulSync can't see"""
    name = (_parts(server_folder) or ["music"])[-1].lower().replace(" ", "-")
    return f"- /where/{name}/is/on/the/host:/{name}"


def check_folder(conn: Any, server_kind: str, server_folder: str, env: Env, *,
                 library: str = "", n: int = SAMPLE,
                 rng: Optional[random.Random] = None) -> dict:
    """one folder the media server reports, with SoulSync's view of it"""
    tracks = count_tracks(conn, server_kind, server_folder)
    if not tracks and path_mappings.windows_style(server_folder) and "/" in server_folder:
        # an override stores D:/Music; the server reports D:\Music\...
        native = server_folder.replace("/", "\\")
        native_tracks = count_tracks(conn, server_kind, native)
        if native_tracks:
            server_folder, tracks = native, native_tracks
    sample = sample_tracks(conn, server_kind, server_folder, n, rng)
    local, how, broken = locate_folder(conn, server_kind, server_folder, env, sample)
    out: Dict[str, Any] = {
        "library": library, "server_folder": server_folder, "tracks": tracks,
        "local_folder": local, "found_by": how or None, "broken_override": broken,
    }
    if local is None:
        out["status"] = "not_mounted"
        if env.docker:
            out["volume_example"] = volume_example(server_folder)
    else:
        root = library_folder_for(local, env.library_dirs)
        parts = [] if root else library_folders_inside(local, env.library_dirs)
        out["library_folder"] = root
        out["library_parts"] = parts
        out["status"] = "covered" if root else ("partly_covered" if parts else "not_library")
    reasons: Counter = Counter()
    examples: List[dict] = []
    for _mid, track_id, server_path in sample:
        target = _translate_with(server_path, server_folder, local) if local else None
        if not target:
            reason = "not_mounted"
        else:
            expected = None
            try:
                expected = recorded_size(conn, track_id, target)
            except Exception:  # noqa: BLE001 - no size to compare with
                expected = None
            reason = env.probe(target, expected)
            # a file SoulSync can open but doesn't scan isn't in its library
            if reason == "ok" and not library_folder_for(target, env.library_dirs):
                reason = "not_library"
        reasons[reason] += 1
        if reason != "ok" and len(examples) < _EXAMPLES:
            examples.append({"server_path": server_path, "local_path": target,
                             "reason": reason, "why": REASONS.get(reason, reason)})
    sampled = len(sample)
    readable = reasons.get("ok", 0)
    out.update({
        "sampled": sampled, "readable": readable,
        "readable_pct": round(100.0 * readable / sampled, 1) if sampled else None,
        "reasons": dict(reasons), "examples": examples,
    })
    out["reachable_tracks"] = int(round(tracks * readable / sampled)) if sampled else 0
    return out


# ---------------------------------------------------------------- the server's folders
def infer_folders(conn: Any, server_kind: str, rng: Optional[random.Random] = None,
                  n: int = 200) -> List[str]:
    """the music folders of a server that doesn't say (Navidrome), from the
    paths it reports: each sampled path minus artist/album/file, keeping
    folders behind at least one in fifty of the sample. '' when the paths are
    relative (Navidrome's Report Real Path is off)."""
    sample = sample_tracks(conn, server_kind, None, n, rng)
    if not sample:
        return []
    votes: Counter = Counter()
    for _mid, _tid, path in sample:
        text = str(path)
        if not (text.startswith(("/", "\\")) or path_mappings.windows_style(text)):
            votes[""] += 1
            continue
        parts = _parts(text)
        if len(parts) < 4:
            continue
        sep = "\\" if "\\" in text and "/" not in text else "/"
        lead = "\\\\" if text.startswith(("\\\\", "//")) else ("/" if text.startswith("/") else "")
        votes[lead + sep.join(parts[:-3])] += 1
    floor = max(1, len(sample) // 50)
    kept = [f for f, c in votes.most_common() if c >= floor]
    # a folder inside another kept folder is the same library
    out = []
    for f in sorted(kept, key=len):
        if f and any(inside(f, o) for o in out if o):
            continue
        out.append(f)
    return out


def server_folders(client: Any) -> List[dict]:
    """``[{library, folder}]`` from the client, [] when it doesn't expose paths"""
    getter = getattr(client, "get_music_library_folders", None)
    if not callable(getter):
        return []
    out = []
    try:
        for lib in getter() or []:
            for folder in lib.get("folders") or []:
                if folder:
                    out.append({"library": str(lib.get("library") or ""), "folder": str(folder)})
    except Exception as exc:  # noqa: BLE001 - an unreachable server: fall back to inference
        logger.debug("media server folders unavailable: %s", exc)
        return []
    return out


def run_check(conn: Any, server_kind: str, client: Any, env: Env, *,
              n: int = SAMPLE, seed: Optional[int] = None) -> dict:
    """every music folder the media server has, checked, plus the overall share"""
    rng = random.Random(seed)
    total = count_tracks(conn, server_kind)
    reported = server_folders(client) if client is not None else []
    source = "server"
    if not reported:
        source = "inferred"
        reported = [{"library": "", "folder": f} for f in infer_folders(conn, server_kind, rng)]
    folders: List[dict] = []
    relative_paths = False
    for item in reported:
        if not item["folder"]:
            relative_paths = True
            continue
        folders.append(check_folder(conn, server_kind, item["folder"], env,
                                    library=item["library"], n=n, rng=rng))
    reachable = sum(f["reachable_tracks"] for f in folders)
    counted = sum(f["tracks"] for f in folders)
    return {
        "server": server_kind,
        "folders_from": source,
        "relative_paths": relative_paths,
        "total_tracks": total,
        "tracks_in_folders": counted,
        "outside_folders": max(total - counted, 0),
        "reachable_tracks": reachable,
        "reachable_pct": round(100.0 * reachable / total, 1) if total else None,
        "library_dirs": env.library_dirs,
        "docker": env.docker,
        "folders": folders,
        "checked_at": int(time.time()),
    }


# ---------------------------------------------------------------- last result
# the Help check reads the last result rather than asking the media server
_last_lock = threading.Lock()
_last: Dict[str, Any] = {}


def remember(result: dict) -> None:
    with _last_lock:
        _last.clear()
        _last.update(result or {})


def last_result() -> Optional[dict]:
    with _last_lock:
        return dict(_last) if _last else None


def forget() -> None:
    with _last_lock:
        _last.clear()


__all__ = [
    "Env", "REASONS", "SAMPLE", "check_folder", "count_tracks", "folder_range",
    "forget", "infer_folders", "inside", "last_result", "learned_folder",
    "library_folder_for", "library_folders_inside", "locate_folder", "probe_file", "remember", "run_check",
    "sample_tracks", "server_folders", "suffix_candidates", "volume_example",
]
