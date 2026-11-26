"""which home videos a profile can see: the home libraries not hidden from
it (core/player/libraries.py), and the roots each one holds. one place for
the routes, playback and downloads, so they can't disagree."""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional


def home_libraries(player_db: Any, video_db: Any, hidden: FrozenSet[int] = frozenset()) -> List[Dict[str, Any]]:
    """[{id, name, in_guide, roots: [root id]}] for the home libraries this
    profile may see. none while there are no home video roots."""
    from core.player import libraries as L
    from core.video import home_videos as H
    all_roots = [r["id"] for r in H.roots(video_db)]
    if not all_roots:
        return []
    libs = L.libraries(player_db)
    out = []
    for lib in libs:
        if lib["kind"] != "home" or lib["id"] in hidden:
            continue
        out.append({"id": lib["id"], "name": lib["name"], "in_guide": lib["in_guide"],
                    "is_default": lib["is_default"],
                    "roots": L.home_library_roots(libs, all_roots, lib["id"])})
    return out


def visible_roots(player_db: Any, video_db: Any, hidden: FrozenSet[int] = frozenset(),
                  library_id: Optional[int] = None) -> List[str]:
    """every root this profile can see (or one library's)"""
    out: List[str] = []
    for lib in home_libraries(player_db, video_db, hidden):
        if library_id is None or lib["id"] == int(library_id):
            out.extend(lib["roots"])
    return out


def hidden_now() -> FrozenSet[int]:
    """the libraries hidden from whoever is asking (an admin: none)"""
    from api.video.kids import hidden_libraries, video_cap
    return frozenset(hidden_libraries(video_cap()))
