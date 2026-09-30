"""Sample Studio — chop organization (Phase 6).

Renders the ``file_organization.templates.sample_path`` template into folder
segments inside the chosen sample folder, following the same substitution
pattern as the podcast/audiobook renderers (``$var`` and ``${var}`` forms,
empty segments collapse, every segment sanitized).

Supported variables:
    $artist  source track's artist
    $track   source track's title
    $album   source track's album
    $chop    the chop's save name
    $stem    stem the chop was cut from, empty for the full mix

Default template: ``$artist/$track - $chop``.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List

from core.imports.paths import sanitize_filename
from utils.logging_config import get_logger

logger = get_logger("sample.organize")

SAMPLE_PATH_TEMPLATE_DEFAULT = "$artist/$track - $chop"

# Punctuation-only segments collapse away, like the audiobook renderer.
_COLLAPSIBLE_SEGMENT_RE = re.compile(r"^[\s\-_.,;:()\[\]{}]*$")


def sample_template() -> str:
    """Configured sample_path template, or the default when unset."""
    from core.settings import config_manager

    return (
        config_manager.get("file_organization.templates.sample_path", None)
        or SAMPLE_PATH_TEMPLATE_DEFAULT
    )


def render_sample_path(template: str | None, info: Dict[str, str]) -> List[str]:
    """Render a template into sanitized folder segments.

    ``info`` keys: artist, track, album, chop, stem (all optional; missing or
    blank values collapse their segment). A template that renders to nothing
    falls back to the sanitized chop name so a chop always has somewhere to go.
    """
    # Sanitize values BEFORE substitution so a stray "/" in metadata (AC/DC)
    # cannot invent subfolders — same approach as sanitize_context_values().
    def _v(raw: str) -> str:
        raw = str(raw or "").strip()
        return sanitize_filename(raw) if raw else ""

    variables = [
        ("artist", _v(info.get("artist"))),
        ("track", _v(info.get("track"))),
        ("album", _v(info.get("album"))),
        ("chop", _v(info.get("chop")) or "Untitled chop"),
        ("stem", _v(info.get("stem"))),
    ]
    chop_value = dict(variables)["chop"]
    variables.sort(key=lambda kv: -len(kv[0]))

    rendered = template or SAMPLE_PATH_TEMPLATE_DEFAULT
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
        safe = sanitize_filename(part)
        if safe:
            segments.append(safe)

    if not segments:
        segments = [chop_value or "Untitled chop"]
    return segments


def unique_chop_path(folder: str, segments: List[str], ext: str) -> str:
    """Absolute destination path, with numeric disambiguation on collision.

    ``segments`` are the rendered template parts (folders + filename stem);
    ``ext`` includes the dot (".wav"/".flac"). Creates the parent folders.
    A second chop that would land on the same file becomes
    ``name (2).wav``, ``name (3).wav``, … — existing chops are never
    overwritten.
    """
    *dirs, filename = segments
    dest_dir = os.path.join(folder, *dirs) if dirs else folder
    os.makedirs(dest_dir, exist_ok=True)
    candidate = os.path.join(dest_dir, filename + ext)
    n = 1
    while os.path.exists(candidate):
        n += 1
        candidate = os.path.join(dest_dir, f"{filename} ({n}){ext}")
    return candidate
