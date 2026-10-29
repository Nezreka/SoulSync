"""Configured video libraries; additional roots never change import destinations.

The optional 4K libraries (``movies_4k_path`` / ``tv_4k_path``) DO: a title on a
quality profile that saves to the 4K library imports there. Each is off until
its switch (``movies_4k_enabled`` / ``tv_4k_enabled``) is on and a folder is
set, and a switched-off 4K folder is invisible everywhere (routing, scanning,
the monitor's roots), so turning it off is a clean undo."""
import json
import os

ADDITIONAL_KEYS = ("movies_additional_paths", "tv_additional_paths")


def normalize_paths(value):
    if not isinstance(value, list) or any(not isinstance(p, str) for p in value):
        raise ValueError("Additional library paths must be a list of folder paths")
    result = []
    seen = set()
    for path in value:
        path = path.strip()
        if not path:
            continue
        if "\x00" in path:
            raise ValueError("Library paths cannot contain null characters")
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def additional_paths(db, key):
    try:
        value = db.get_setting(key) or "[]"
        return normalize_paths(json.loads(value) if isinstance(value, str) else value)
    except (ValueError, TypeError):
        return []


# kind -> (switch, folder) for the optional 4K libraries
FOUR_K_KEYS = {"movie": ("movies_4k_enabled", "movies_4k_path"),
               "episode": ("tv_4k_enabled", "tv_4k_path")}


def _on(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def four_k_path(db, kind):
    """The 4K library folder for 'movie' or 'episode', or '' while it's off."""
    keys = FOUR_K_KEYS.get(kind)
    if not keys:
        return ""
    enabled, folder = keys
    path = str(db.get_setting(folder) or "").strip()
    return path if path and _on(db.get_setting(enabled)) else ""


def output_paths(db):
    """Every folder a finished download can be filed in, as target_dir_for
    reads them. The legacy single transfer folder stands in for an unset
    Movies folder; a 4K folder that's switched off is ''."""
    out = {key: str(db.get_setting(key) or "") for key in
           ("movies_path", "tv_path", "anime_path", "youtube_path")}
    if not out["movies_path"]:
        out["movies_path"] = str(db.get_setting("transfer_path") or "")
    out["movies_4k_path"] = four_k_path(db, "movie")
    out["tv_4k_path"] = four_k_path(db, "episode")
    return out


# the video folders the default compose file maps
DOCKER_DEFAULTS = {"movies_path": "/media/movies", "tv_path": "/media/tv",
                   "youtube_path": "/media/youtube"}


def seed_docker_defaults(db, *, docker, mounts):
    """Docker installs start on the compose file's video folders, the way
    music starts on /app/Transfer. Only a folder never saved (a cleared one
    stays cleared), never over the legacy transfer folder, and only one the
    container really has mapped: a default on no volume would download into
    the container and be lost on the next recreate. Returns what it set."""
    if not docker:
        return {}
    from core.onboarding.paths import on_mapped_volume
    seeded = {}
    for key, path in DOCKER_DEFAULTS.items():
        if db.get_setting(key) is not None:
            continue
        if key == "movies_path" and db.get_setting("transfer_path"):
            continue
        if on_mapped_volume(path, mounts) is True:
            db.set_setting(key, path)
            seeded[key] = path
    return seeded


def library_roots(db, kind=None):
    roots = []
    kinds = (kind,) if kind else ("movie", "episode", "youtube")
    for item in kinds:
        key = {"movie": "movies_path", "episode": "tv_path", "youtube": "youtube_path"}[item]
        primary = db.get_setting(key)
        if primary:
            roots.append(str(primary))
        four_k = four_k_path(db, item)
        if four_k:
            roots.append(four_k)
        if item == "episode":
            # the optional anime folder is a TV library too (new episodes of
            # anime-type shows import there); normalize_paths dedupes it if it
            # is also listed as an additional TV library
            anime = db.get_setting("anime_path")
            if anime:
                roots.append(str(anime))
        if item != "youtube":
            roots.extend(additional_paths(db, key.replace("_path", "_additional_paths")))
    return normalize_paths(roots)


def containing_root(path, roots):
    """Use the deepest configured parent, keeping renames on their current drive."""
    matches = []
    absolute = os.path.abspath(path)
    for root in roots:
        candidate = os.path.abspath(root)
        try:
            if os.path.commonpath([absolute, candidate]) == candidate:
                matches.append(candidate)
        except ValueError:
            continue
    return max(matches, key=len) if matches else None
