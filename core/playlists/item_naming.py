"""Optional custom naming for the FILES inside an organize-by-playlist folder.

By default a playlist entry keeps the real library filename (the materialized
folder is a view onto Artist/Album/track.ext). A user can opt into a flat
filename template — e.g. ``$position - $artist - $title`` — so the folder sorts
and plays the way they want (most commonly: in playlist order on a dumb DAP).

It is a **filename** template, never a path:
  - it may NOT contain a path separator (``/`` or ``\\``) — it names the file,
    not a folder tree, and
  - it MUST contain ``$title`` — so every file has a real, non-empty name.

Both rules are validated up front (so the Settings UI can reject a bad value
with a reason) AND re-checked at apply time, where an invalid/empty template or
an empty render falls back to the library filename. So a bad value can never
produce a broken name — the worst case is "no change from today".

Pure logic: no DB, no config, no filesystem. The caller supplies the metadata.
"""

from __future__ import annotations

from typing import Optional, Tuple

from core.imports.paths import sanitize_filename

# Tokens a user may use in the template (for docs / UI hints).
PLAYLIST_ITEM_TOKENS = ("$position", "$artist", "$album", "$track", "$title")


def validate_playlist_item_template(template: Optional[str]) -> Tuple[bool, str]:
    """Return ``(ok, reason)``. An empty template is VALID and means "feature off"
    (keep the library filename). ``reason`` is '' when ok."""
    t = (template or "").strip()
    if not t:
        return True, ""  # empty == disabled, not an error
    if "/" in t or "\\" in t:
        return False, ("Playlist file naming can't contain a folder separator "
                       "( / or \\ ) — it names the file, not a path.")
    if "$title" not in t:
        return False, "Playlist file naming must include $title so every file has a name."
    return True, ""


def render_playlist_item_name(
    template: Optional[str],
    *,
    title: str,
    artist: str = "",
    album: str = "",
    track: object = None,
    position: object = None,
    ext: str = "",
    fallback_name: str = "",
) -> str:
    """Render ``template`` to a sanitized filename WITH ``ext`` appended.

    Falls back to ``fallback_name`` (the library filename) when the template is
    empty/invalid or renders to nothing after sanitizing — so the result is
    never broken. ``position`` is used verbatim (the caller pre-pads it for
    correct sorting); ``track`` is zero-padded to two digits when numeric."""
    ok, _ = validate_playlist_item_template(template)
    t = (template or "").strip()
    if not ok or not t:
        return fallback_name

    pos_str = "" if position is None else str(position)
    if track is None:
        trk_str = ""
    else:
        try:
            trk_str = f"{int(track):02d}"
        except (TypeError, ValueError):
            trk_str = str(track)

    # No token is a prefix of another, so replacement order is irrelevant.
    out = t
    out = out.replace("$position", pos_str)
    out = out.replace("$artist", str(artist or ""))
    out = out.replace("$album", str(album or ""))
    out = out.replace("$track", trk_str)
    out = out.replace("$title", str(title or ""))

    out = sanitize_filename(out).strip()
    if not out:
        return fallback_name
    return out + (ext or "")


__all__ = [
    "PLAYLIST_ITEM_TOKENS",
    "validate_playlist_item_template",
    "render_playlist_item_name",
]
