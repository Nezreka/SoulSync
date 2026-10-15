"""Backend-review (Sep 2026) wishlist/watchlist regression tests.

Each test executes the REAL code from the backend under test (AST-extracted
verbatim blocks from core/downloads/master.py, or the real public functions)
rather than a re-implementation, so a passing test proves the shipped logic.
"""
import ast
import copy
import json
import logging
import uuid
from pathlib import Path

import core.imports.paths as import_paths

REPO_ROOT = Path(__file__).resolve().parents[2]
_MASTER_SRC = (REPO_ROOT / "core" / "downloads" / "master.py").read_text()
_MASTER_TREE = ast.parse(_MASTER_SRC)

logger = logging.getLogger("tests")


_MAP_NAMES = {
    "wishlist_album_disc_counts",
    "wishlist_album_artist_map",
    "wishlist_album_context_map",
    "wishlist_album_fallback_artist",
}


def _iter_stmt_slots(node):
    """Yield (stmt_list, index, stmt) for every statement in every block."""
    for field in ("body", "orelse", "finalbody"):
        lst = getattr(node, field, None)
        if isinstance(lst, list):
            for i, s in enumerate(lst):
                yield lst, i, s
                yield from _iter_stmt_slots(s)
    for handler in getattr(node, "handlers", []) or []:
        yield from _iter_stmt_slots(handler)


def _is_playlist_wishlist_test(test):
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "playlist_id"
        and any(isinstance(c, ast.Constant) and c.value == "wishlist" for c in test.comparators)
    )


def _extract_first_pass_block():
    """The real wishlist first-pass block from core/downloads/master.py: the
    ``wishlist_album_*`` map declarations plus the ``if playlist_id ==
    'wishlist':`` block that builds ``wishlist_album_artist_map``
    (first-row-wins per album)."""
    for lst, i, stmt in _iter_stmt_slots(_MASTER_TREE):
        if not (isinstance(stmt, ast.If) and _is_playlist_wishlist_test(stmt.test)):
            continue
        has_store = any(
            isinstance(sub, ast.Subscript)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "wishlist_album_artist_map"
            and isinstance(sub.ctx, ast.Store)
            for sub in ast.walk(stmt)
        )
        if not has_store:
            continue
        start = i
        for j in range(i - 1, -1, -1):
            prev = lst[j]
            if isinstance(prev, ast.Assign) and any(
                getattr(t, "id", "") in _MAP_NAMES for t in prev.targets
            ):
                start = j
            else:
                break
        return lst[start : i + 1]
    raise AssertionError("wishlist first-pass block not found in master.py")


def _extract_stamp_block():
    """The real ``elif playlist_id == 'wishlist':`` per-task stamping block
    from ``for res in missing_tracks:`` in core/downloads/master.py — the
    body only (the ``if batch_is_album ...`` preamble is skipped)."""
    for node in ast.walk(_MASTER_TREE):
        if not isinstance(node, ast.For):
            continue
        if not (isinstance(node.iter, ast.Name) and node.iter.id == "missing_tracks"):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.If):
                continue
            test = sub.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "playlist_id"
                and any(isinstance(c, ast.Constant) and c.value == "wishlist" for c in test.comparators)
            ):
                return sub.body
    raise AssertionError("wishlist stamping block not found in master.py")


def _album_context_richness_fn():
    """The real module-level ``_album_context_richness`` helper from master.py."""
    for node in ast.walk(_MASTER_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == "_album_context_richness":
            ns = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<master>", "exec"), ns)
            return ns["_album_context_richness"]
    raise AssertionError("_album_context_richness not found in master.py")


_FIRST_PASS_STMTS = _extract_first_pass_block()
_STAMP_STMTS = _extract_stamp_block()
_RICHNESS_FN = _album_context_richness_fn()


def _run_wishlist_first_pass(tracks):
    ns = {
        "tracks_json": tracks,
        "playlist_id": "wishlist",
        "logger": logger,
        "_album_context_richness": _RICHNESS_FN,
    }
    exec(compile(ast.Module(body=_FIRST_PASS_STMTS, type_ignores=[]), "<master-first-pass>", "exec"), ns)
    return ns


def _stamp_wishlist_track(track, first_pass_ns):
    """Run the real per-task stamping block for one wishlist track; returns
    the stamped ``track_info`` dict."""
    ns = {
        "res": {"track": copy.deepcopy(track)},
        "playlist_id": "wishlist",
        "json": json,
        "uuid": uuid,
        "logger": logger,
        "wishlist_album_artist_map": first_pass_ns["wishlist_album_artist_map"],
        "wishlist_album_context_map": first_pass_ns["wishlist_album_context_map"],
        "wishlist_album_disc_counts": first_pass_ns["wishlist_album_disc_counts"],
        "wishlist_album_fallback_artist": first_pass_ns["wishlist_album_fallback_artist"],
    }
    prelude = "track_info = res['track'].copy()"
    exec(prelude, ns)
    exec(compile(ast.Module(body=_STAMP_STMTS, type_ignores=[]), "<master-stamp>", "exec"), ns)
    return ns["track_info"]


def _poisoned_wishlist_tracks():
    """Two wishlist rows of album alb-1; the FIRST row's album.artists[0] is
    poisoned (#1316 shape)."""
    poisoned = {
        "name": "Poisoned Track",
        "artist": "Poisoned Artist",
        "artists": [{"name": "Poisoned Artist"}],
        "spotify_data": {
            "name": "Poisoned Track",
            "artists": [{"name": "Poisoned Artist"}],
            "disc_number": 1,
            "album": {
                "id": "alb-1",
                "name": "Real Album",
                "artists": [{"name": "Poisoned Artist"}],
                "release_date": "2024-01-01",
                "total_tracks": 2,
            },
        },
    }
    victim = {
        "name": "Victim Song",
        "artist": "Real Artist",
        "artists": [{"name": "Real Artist"}],
        "spotify_data": {
            "name": "Victim Song",
            "artists": [{"name": "Real Artist"}],
            "disc_number": 1,
            "album": {
                "id": "alb-1",
                "name": "Real Album",
                "artists": [{"name": "Real Artist"}],
                "release_date": "2024-01-01",
                "total_tracks": 2,
            },
        },
    }
    return poisoned, victim


def test_c3_poisoned_row_does_not_stamp_victim_tracks():
    """C3: a poisoned first wishlist row must not stamp its album artist onto
    a later track whose own album artist disagrees — the stamping side must
    apply the same #1316 disagreement check the tag path does."""
    poisoned, victim = _poisoned_wishlist_tracks()
    first_pass = _run_wishlist_first_pass([poisoned, victim])
    stamped = _stamp_wishlist_track(victim, first_pass)
    ctx_name = stamped.get("_explicit_artist_context", {}).get("name")
    assert ctx_name == "Real Artist", (
        f"victim's own album artist was overridden by the first-row map: {ctx_name!r}"
    )


class _StubConfig:
    """Minimal stand-in for the config manager the path builder reads."""

    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def test_c3_victim_filed_under_own_artist_end_to_end(monkeypatch, tmp_path):
    """C3 end-to-end: the stamped victim track must build a final filing path
    under its OWN artist, through the real paths.py builder."""
    poisoned, victim = _poisoned_wishlist_tracks()
    first_pass = _run_wishlist_first_pass([poisoned, victim])
    stamped = _stamp_wishlist_track(victim, first_pass)
    stamped["track_number"] = 2
    stamped["disc_number"] = 1

    config = _StubConfig(
        {
            "soulseek.transfer_path": str(tmp_path / "Transfer"),
            "file_organization.enabled": True,
            "file_organization.templates": {
                "album_path": "$albumartist/$albumartist - $album/$track - $title",
                "single_path": "$artist/$artist - $title",
            },
            "file_organization.collab_artist_mode": "first",
            "file_organization.disc_label": "Disc",
        }
    )
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)

    context = {
        "source": "soulseek",
        "is_album_download": False,
        "artist": {"name": "Real Artist"},
        "album": {
            "name": "Real Album",
            "id": "alb-1",
            "release_date": "2024-01-01",
            "total_tracks": 10,
            "album_type": "album",
            "artists": [{"name": "Real Artist"}],
        },
        "track_info": stamped,
        "original_search_result": {
            "title": "Victim Song",
            "clean_title": "Victim Song",
            "clean_album": "Real Album",
            "clean_artist": "Real Artist",
            "artists": [{"name": "Real Artist"}],
        },
    }
    final_path, _created = import_paths.build_final_path_for_track(
        context,
        {"name": "Real Artist"},
        {"is_album": True, "album_name": "Real Album", "track_number": 2, "disc_number": 1},
        ".flac",
        create_dirs=False,
    )
    assert "Poisoned Artist" not in final_path, f"victim filed under poisoned artist: {final_path}"
    assert "Real Artist" in final_path, f"victim not filed under own artist: {final_path}"


def test_c3_agreeing_tracks_keep_shared_album_artist():
    """C3 guard: tracks whose own album artist AGREES with the first-row map
    still get the shared artist context (no behavior change for clean data)."""
    _poisoned, victim = _poisoned_wishlist_tracks()
    clean_first = copy.deepcopy(victim)
    clean_first["name"] = "Clean First"
    first_pass = _run_wishlist_first_pass([clean_first, victim])
    stamped = _stamp_wishlist_track(victim, first_pass)
    ctx_name = stamped.get("_explicit_artist_context", {}).get("name")
    assert ctx_name == "Real Artist"



# ---------------------------------------------------------------------------
# #1616: an album with no stored credit is not filed under its singer
# ---------------------------------------------------------------------------

def _uncredited_soundtrack_tracks():
    """two rows of a VA soundtrack whose stored album has no artists and no
    track count, the shape a deezer playlist sync leaves on the wishlist."""
    def row(title, singer):
        return {
            "name": title,
            "artist": singer,
            "artists": [{"name": singer}],
            "spotify_data": {
                "name": title,
                "artists": [{"name": singer}],
                "disc_number": 1,
                "album": {"id": "moana", "name": "Moana (Deluxe Edition)",
                          "release_date": "2017-01-06"},
            },
        }
    return row("How Far I'll Go", "Auli'i Cravalho"), row("You're Welcome", "Dwayne Johnson")


def test_1616_no_album_credit_leaves_the_folder_to_the_album_lookup():
    first, second = _uncredited_soundtrack_tracks()
    first_pass = _run_wishlist_first_pass([first, second])
    for track in (first, second):
        stamped = _stamp_wishlist_track(track, first_pass)
        assert "_explicit_artist_context" not in stamped
        assert stamped["_is_explicit_album_download"] is True
        # one fallback for the whole album, so a failed lookup keeps one folder
        assert stamped["_fallback_album_artist"] == "Auli'i Cravalho"


def test_1616_unknown_track_count_stays_unknown_for_the_lookup():
    first, _second = _uncredited_soundtrack_tracks()
    stamped = _stamp_wishlist_track(first, _run_wishlist_first_pass([first]))
    assert stamped["_explicit_album_context"]["total_tracks"] == 0


def test_1616_a_stored_album_credit_is_still_used():
    _poisoned, victim = _poisoned_wishlist_tracks()
    stamped = _stamp_wishlist_track(victim, _run_wishlist_first_pass([victim]))
    assert stamped["_explicit_artist_context"]["name"] == "Real Artist"
    assert "_fallback_album_artist" not in stamped
    assert stamped["_explicit_album_context"]["total_tracks"] == 2
