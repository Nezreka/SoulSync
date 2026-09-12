"""Configured video libraries; additional roots never change import destinations."""
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


def library_roots(db, kind=None):
    roots = []
    kinds = (kind,) if kind else ("movie", "episode", "youtube")
    for item in kinds:
        key = {"movie": "movies_path", "episode": "tv_path", "youtube": "youtube_path"}[item]
        primary = db.get_setting(key)
        if primary:
            roots.append(str(primary))
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
