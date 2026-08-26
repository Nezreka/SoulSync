"""Shared path and naming helpers for import processing."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

# Album grouping lives in core.imports.album_naming; this module keeps the
# imported helper because the path builder still needs it.
from core.imports.album_naming import resolve_album_group
from core.library.case_folding import resolve_existing_case_dir
from core.imports.context import (
    extract_artist_name,
    get_import_clean_title,
    get_import_context_album,
    get_import_original_search,
    get_import_source,
    get_import_track_info,
    normalize_import_context,
)

logger = logging.getLogger("imports.paths")


def artist_letter(artist, symbol_fallback=None):
    """The $artistletter value — the ONE implementation all four call sites
    share (both template engines + the music-video path builder).

    Literal mode (default, historical behavior byte-for-byte): the raw first
    character uppercased — '365 Days' → '3', 'Édith Piaf' → 'É'.

    Symbol-fallback mode (file_organization.artistletter_symbol_fallback,
    #1072 QT3496): diacritics fold to their base letter first (Édith → E —
    the iTunes/Plex shelving convention; É belongs under E, not #), then
    anything that doesn't resolve to A-Z (digits, symbols, CJK, Cyrillic,
    ...) lands in one '#' catch-all instead of fragmenting the library into
    per-character folders.

    ``symbol_fallback=None`` reads the config; pass a bool to force either
    mode (tests, previews).
    """
    literal = (artist or "U")[0].upper()
    if symbol_fallback is None:
        try:
            from core.settings import config_manager
            symbol_fallback = bool(config_manager.get(
                "file_organization.artistletter_symbol_fallback", False))
        except Exception:   # config unavailable → historical behavior
            symbol_fallback = False
    if not symbol_fallback:
        return literal
    import unicodedata
    folded = unicodedata.normalize("NFKD", str(artist or ""))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    lead = folded[:1].upper()
    return lead if "A" <= lead <= "Z" else "#"


def _get_config_manager():
    try:
        from core.settings import config_manager
        return config_manager
    except Exception:
        class _FallbackConfig:
            @staticmethod
            def get(key, default=None):
                return default

        return _FallbackConfig()


def _extract_year_from_release_date(release_date) -> str:
    """Return a validated 4-digit year from a release_date, or '' .

    The ``$year`` template variable used to be a blind ``release_date[:4]``
    slice. When something upstream poisons ``release_date`` with a non-date
    value (e.g. the album NAME — #745, where "Mantras (Deluxe)" produced a
    "(Mant)" folder), that slice happily emitted garbage. Validate that the
    leading 4 chars are a plausible year, matching the guard the rest of the
    codebase already uses (see ``soulid_worker._extract_year``). Anything
    that isn't a real year resolves to '' — the template's bracket-cleanup
    then drops the empty ``()`` instead of writing rubbish into the path.
    """
    if not release_date:
        return ""
    candidate = str(release_date)[:4]
    if candidate.isdigit() and 1900 < int(candidate) <= 2100:
        return candidate
    return ""


def _get_itunes_client():
    try:
        from core.metadata_service import get_itunes_client
        return get_itunes_client()
    except Exception:
        return None


def _get_album_tracks_for_source(source: str, album_id: str):
    try:
        from core.metadata_service import get_album_tracks_for_source
        return get_album_tracks_for_source(source, album_id)
    except Exception:
        return None


def docker_resolve_path(path_str: str) -> str:
    """Resolve a configured folder to the canonical absolute path this process
    can use: Docker-hosted Windows drives mapped, ``~`` expanded, made absolute.

    Every caller of this hands the result to the filesystem, and several of them
    also STORE it or compare it against a stored value. Returning a relative root
    verbatim (``./Transfer`` is the shipped default) meant the same folder was
    spelled three different ways depending on which code path produced it, and no
    two of them compared equal. Absolutising here is what makes "the path in the
    catalogue", "the path on disk" and "the path in a root comparison" one string.

    An empty value stays empty: ``os.path.abspath('')`` is the CWD, which would
    turn "this folder is not configured" into the application directory.
    """
    if not path_str:
        return path_str
    if os.path.exists("/.dockerenv") and len(path_str) >= 3 and path_str[1] == ":" and path_str[0].isalpha():
        drive_letter = path_str[0].lower()
        rest_of_path = path_str[2:].replace("\\", "/")
        return f"/host/mnt/{drive_letter}{rest_of_path}"
    return os.path.abspath(os.path.expanduser(path_str))


def config_root_path(path_str: Any, default: str = "") -> str:
    """Canonical absolute form of a CONFIGURED root folder.

    Every root in Settings (music library, downloads, import, music videos,
    playlists) may be written relative — ``./Transfer`` is the shipped default
    and what the settings page shows. :func:`docker_resolve_path` only maps
    Windows drive letters, so that value used to reach the filesystem verbatim
    and one folder ended up with three spellings that never compared equal:

      * ``./Transfer/Artist/…``   — the album path builder (raw string)
      * ``Transfer/Artist/…``     — the single/simple builder (``Path()`` eats
        the leading ``./``)
      * ``/app/Transfer/Artist/…``— anything that realpath()s, e.g. the repair
        filesystem scan

    The first two were written into the catalogue, so a stored path depended on
    the process CWD, and every ``startswith(root)`` check silently missed.
    Resolving once, here, is what makes "the path in the catalogue", "the path
    on disk" and "the path in a root comparison" the same string. A root the
    user already gave in full comes back unchanged apart from normalisation.
    """
    raw = str(path_str if path_str not in (None, "") else (default or "")).strip()
    if not raw:
        return ""
    return docker_resolve_path(raw)


def build_simple_download_destination(context, file_path: str):
    """Build the destination path for a simple download into Transfer."""
    context = normalize_import_context(context)
    search_result = context.get("search_result", {}) or {}
    if not isinstance(search_result, dict):
        search_result = {}

    transfer_dir = Path(config_root_path(
        _get_config_manager().get("soulseek.transfer_path", "./Transfer"), "./Transfer"))
    album_name = None
    original_filename = search_result.get("filename", "")
    if "/" in original_filename or "\\" in original_filename:
        path_parts = original_filename.replace("\\", "/").split("/")
        if len(path_parts) >= 2:
            album_name = path_parts[-2]
    if not album_name:
        album_value = search_result.get("album")
        if isinstance(album_value, dict):
            album_name = album_value.get("name", "")
        else:
            album_name = album_value

    filename = Path(file_path).name
    if album_name and str(album_name).lower() not in {"unknown", "unknown album", ""}:
        album_name = sanitize_filename(str(album_name))
        destination_dir = transfer_dir / album_name
    else:
        album_name = ""
        destination_dir = transfer_dir

    destination_dir.mkdir(parents=True, exist_ok=True)
    return destination_dir / filename, album_name, filename


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for file system compatibility."""
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", filename)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    # Windows forbids TRAILING dots/spaces; Unix hides anything with a LEADING
    # dot. #1129: "...Baby One More Time" created a dotfile directory that was
    # invisible to `ls` and that Plex/Jellyfin/Navidrome skip entirely, so the
    # album silently vanished from the library. Strip both ends.
    sanitized = sanitized.strip(". ") or "_"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", sanitized, re.IGNORECASE):
        sanitized = "_" + sanitized
    return sanitized[:200]


def sanitize_context_values(context: dict) -> dict:
    """Sanitize all string values in a template context for path safety."""
    sanitized = {}
    for key, value in context.items():
        if isinstance(value, str) and value:
            sanitized[key] = sanitize_filename(value)
        else:
            sanitized[key] = value
    return sanitized


def clean_track_title(track_title: str, artist_name: str) -> str:
    """Clean up track title by removing artist prefix and other noise."""
    original = (track_title or "").strip()
    cleaned = original
    cleaned = re.sub(r"^\d{1,2}[\.\s\-]+", "", cleaned)
    artist_pattern = re.escape(artist_name or "") + r"\s*-\s*"
    cleaned = re.sub(f"^{artist_pattern}", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[A-Za-z0-9\.]+\s*-\s*\d{1,2}\s*-\s*", "", cleaned)
    quality_patterns = [
        r"\s*[\[\(][0-9]+\s*kbps[\]\)]\s*",
        r"\s*[\[\(]flac[\]\)]\s*",
        r"\s*[\[\(]mp3[\]\)]\s*",
    ]
    for pattern in quality_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[-\s\.]+", "", cleaned)
    cleaned = re.sub(r"[-\s\.]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else original


# A leading track-number prefix is EITHER a zero-padded number (01, 04, 099 — no
# real song title starts with one), OR a plain number followed by a real separator
# AND a space ("3 - ", "12. "). Deliberately NOT a bare "number space word", so it
# leaves "7 Rings", "99 Luftballons", "50 Ways to Leave Your Lover" and
# "1-800-273-8255" untouched.
_TRACK_NUM_PREFIX_RE = re.compile(r"^\s*(?:0\d{1,2}[\s._)\-]*|\d{1,3}\s*[._)\-]\s+)(?=\S)")


def strip_leading_track_number(title: str) -> str:
    """Conservatively remove a leading track-number prefix from a track title.

    Fixes #890 — files named ``01 - Sun It Rises.flac`` whose stem leaks into the
    title as ``01 - Sun It Rises``, which then never matches the canonical
    ``Sun It Rises`` (false "missing"). Only strips an unambiguous track-number
    prefix; a coincidental leading number that's part of the title is preserved, and
    it never reduces a title to empty or a bare number."""
    s = (title or "").strip()
    if not s:
        return title or ""
    stripped = _TRACK_NUM_PREFIX_RE.sub("", s, count=1).strip()
    # Keep the original if stripping left nothing real — empty, a bare number, or
    # only punctuation (e.g. "01 - " → "-"). A real title has a letter/digit.
    if stripped.isdigit() or not re.search(r"[^\W_]", stripped):
        return s
    return stripped




def get_album_type_display(raw_type, track_count) -> str:
    """Return the display form of an album's type for the $albumtype template variable."""
    raw = (raw_type or "").strip().lower()
    try:
        tc = int(track_count or 0)
    except (TypeError, ValueError):
        tc = 0

    if raw in ("compilation", "compile"):
        return "Compilation"
    if raw == "album":
        return "Album"
    if raw in ("single", "ep"):
        # Unknown track count must not collapse to Single: an EP whose count
        # was lost in a handoff kept getting filed as [Single] (#1064). With
        # no count, trust the source's own word.
        if tc <= 0:
            return "EP" if raw == "ep" else "Single"
        if tc <= 3:
            return "Single"
        if tc <= 6:
            return "EP"
        return "Album"

    if tc <= 0:
        return "Album"
    if tc <= 3:
        return "Single"
    if tc <= 6:
        return "EP"
    return "Album"


def _replace_template_variables(template: str, context: dict) -> str:
    clean_context = sanitize_context_values(context)
    result = template

    album_artist_value = clean_context.get("albumartist", clean_context.get("artist", "Unknown Artist"))
    collab_mode = _get_config_manager().get("file_organization.collab_artist_mode", "first")
    if collab_mode == "first" and album_artist_value:
        artists_list = context.get("_artists_list")
        if artists_list and len(artists_list) > 1:
            first = artists_list[0]
            album_artist_value = first.get("name", first) if isinstance(first, dict) else str(first)
        elif artists_list and len(artists_list) == 1:
            itunes_artist_id = context.get("_itunes_artist_id")
            if itunes_artist_id and ("," in album_artist_value or " & " in album_artist_value):
                try:
                    resolved_client = _get_itunes_client()
                    if resolved_client and hasattr(resolved_client, "resolve_primary_artist"):
                        resolved = resolved_client.resolve_primary_artist(itunes_artist_id)
                        if resolved and resolved != album_artist_value:
                            album_artist_value = resolved
                except Exception as e:
                    logger.debug("resolve primary artist failed: %s", e)

    # $cdnum — smart CD label for multi-disc filenames. Produces "CD01" /
    # "CD02" etc. when the album has 2+ discs, empty string otherwise.
    # Empty output collapses gracefully via the trailing dash cleanup
    # regex below, so single-disc albums don't end up with "CD01" literal
    # in every name.
    _total_discs = _coerce_int(clean_context.get("total_discs", 1), 1)
    _disc_number = _coerce_int(clean_context.get("disc_number", 1), 1)
    cdnum_value = f"CD{_disc_number:02d}" if _total_discs > 1 else ""

    bracket_map = {
        "albumartist": album_artist_value,
        "albumtype": clean_context.get("albumtype", "Album"),
        "playlist": clean_context.get("playlist_name", ""),
        "artistletter": artist_letter(clean_context.get("artist", "U")),
        "artist": clean_context.get("artist", "Unknown Artist"),
        "album": clean_context.get("album", "Unknown Album"),
        "title": clean_context.get("title", "Unknown Track"),
        "track": f"{_coerce_int(clean_context.get('track_number', 1), 1):02d}",
        "cdnum": cdnum_value,
        # #981: ${disc}/${discnum} vanish on single-disc albums, matching ${cdnum}
        # (a track on disc 2+ still shows even if total_discs wasn't populated).
        "disc": (str(_disc_number) if (_total_discs > 1 or _disc_number > 1) else ""),
        "discnum": (str(_disc_number) if (_total_discs > 1 or _disc_number > 1) else ""),
        "year": str(clean_context.get("year", "")),
        "quality": clean_context.get("quality", ""),
    }
    for var_name, val in bracket_map.items():
        result = result.replace("${" + var_name + "}", val)

    result = result.replace("$albumartist", album_artist_value)
    result = result.replace("$albumtype", clean_context.get("albumtype", "Album"))
    result = result.replace("$playlist", clean_context.get("playlist_name", ""))
    result = result.replace("$artistletter", artist_letter(clean_context.get("artist", "U")))
    result = result.replace("$artist", clean_context.get("artist", "Unknown Artist"))
    result = result.replace("$album", clean_context.get("album", "Unknown Album"))
    result = result.replace("$title", clean_context.get("title", "Unknown Track"))
    # $cdnum must replace before $track to follow the longest-prefix-first
    # rule used throughout this function (no current $c* var collides, but
    # ordering matches the web_server.py path-builder for parity).
    result = result.replace("$cdnum", cdnum_value)
    result = result.replace("$track", f"{clean_context.get('track_number', 1):02d}")
    result = result.replace("$year", str(clean_context.get("year", "")))

    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"\s*-\s*-\s*", " - ", result)
    result = result.strip()
    return result


def apply_path_template(template: str, context: dict) -> str:
    """Apply a template to build a path string."""
    return _replace_template_variables(template, context)


# A folder segment that carried nothing but a disc label ("Disc $discnum") has
# to disappear on a single-disc album — leaving a literal "Disc" folder behind
# would silently collapse every disc of a release into one directory.
_DANGLING_DISC_LABEL_RE = re.compile(
    r"^(disc|disk|cd|volume|vol)\s*[.\-\u2013\u2014:]?\s*$", re.IGNORECASE)


def template_uses_disc_variable(template: str) -> bool:
    """Does ``template`` place the disc anywhere, in any of its spellings?

    Must be asked of the RAW template. ``${disc}``, ``${discnum}`` and ``$cdnum``
    are substituted globally, before the rendered path is split into segments, so
    a per-segment look for "$disc" sees only the bare forms and misses the rest.
    """
    return any(token in (template or "")
               for token in ("$disc", "$cdnum", "${disc", "${cdnum"))


def _clean_folder_segment(part: str, disc_value: str, disc_value_raw: str,
                          template_has_disc: bool = False) -> str:
    """Resolve one FOLDER segment of a rendered path template.

    ``$quality`` is the one variable the settings page documents as
    filename-only, and it is stripped here. ``$disc`` and ``$discnum`` used to
    be stripped alongside it, undocumented — so a user asking for a disc folder
    got the folder silently deleted from their path, while ``$cdnum`` and the
    ``${...}`` bracket forms (substituted globally, before the path is split)
    worked. Same family of variables, three different behaviours. They now
    substitute here exactly as they do in the filename, and resolve to the empty
    string on a single-disc album — the ``$cdnum`` rule all along (#981).

    ``$cdnum`` is already gone by this point; the replace is kept as a no-op
    guard so a future change to the global pass cannot leak a raw token into a
    directory name.
    """
    had_disc_var = template_has_disc or "$discnum" in part or "$disc" in part
    part = part.replace("$quality", "")
    part = part.replace("$discnum", disc_value_raw)
    part = part.replace("$disc", disc_value)
    part = part.replace("$cdnum", "")
    part = re.sub(r"\s*\[\s*\]", "", part)
    part = re.sub(r"\s*\(\s*\)", "", part)
    part = re.sub(r"\s*\{\s*\}", "", part)
    part = re.sub(r"\s*-\s*$", "", part)
    part = re.sub(r"^\s*-\s*", "", part)
    part = re.sub(r"\s+", " ", part).strip()
    if had_disc_var and not disc_value_raw and _DANGLING_DISC_LABEL_RE.match(part):
        return ""
    return part


def get_file_path_from_template_raw(template: str, context: dict) -> tuple[str, str]:
    """Build file path using a user-provided template string directly."""
    _template_has_disc = template_uses_disc_variable(template)
    full_path = apply_path_template(template, context)

    quality_value = context.get("quality", "")
    disc_number = _coerce_int(context.get("disc_number", 1), 1)
    # #981: single-disc albums drop $disc/$discnum (like $cdnum) so no "01-" prefix.
    _multi_disc = _coerce_int(context.get("total_discs", 1), 1) > 1 or disc_number > 1
    disc_value = f"{disc_number:02d}" if _multi_disc else ""
    disc_value_raw = str(disc_number) if _multi_disc else ""

    path_parts = full_path.split("/")
    if len(path_parts) > 1:
        folder_parts = path_parts[:-1]
        filename_base = path_parts[-1]

        cleaned_folders = []
        for part in folder_parts:
            part = _clean_folder_segment(part, disc_value, disc_value_raw,
                                         _template_has_disc)
            if part:
                cleaned_folders.append(part)

        filename_base = filename_base.replace("$quality", quality_value)
        filename_base = filename_base.replace("$discnum", disc_value_raw)
        filename_base = filename_base.replace("$disc", disc_value)
        filename_base = re.sub(r"\s*\[\s*\]", "", filename_base)
        filename_base = re.sub(r"\s*\(\s*\)", "", filename_base)
        filename_base = re.sub(r"\s*\{\s*\}", "", filename_base)
        filename_base = re.sub(r"\s*-\s*$", "", filename_base)
        # Leading dash cleanup — lets $cdnum at the start of a filename
        # cleanly disappear on single-disc albums (empty-value case).
        filename_base = re.sub(r"^\s*-\s*", "", filename_base)
        filename_base = re.sub(r"\s+", " ", filename_base).strip()

        sanitized_folders = [sanitize_filename(part) for part in cleaned_folders]
        folder_path = os.path.join(*sanitized_folders) if sanitized_folders else ""
        return folder_path, sanitize_filename(filename_base)

    full_path = full_path.replace("$quality", quality_value)
    full_path = full_path.replace("$discnum", disc_value_raw)
    full_path = full_path.replace("$disc", disc_value)
    full_path = re.sub(r"\s*\[\s*\]", "", full_path)
    full_path = re.sub(r"\s*\(\s*\)", "", full_path)
    full_path = re.sub(r"\s*\{\s*\}", "", full_path)
    full_path = re.sub(r"\s*-\s*$", "", full_path)
    full_path = re.sub(r"\s+", " ", full_path).strip()
    return "", sanitize_filename(full_path)


def get_file_path_from_template(context: dict, template_type: str = "album_path") -> tuple[str, str]:
    """Build complete file path using configured templates."""
    if not _get_config_manager().get("file_organization.enabled", True):
        return None, None

    templates = _get_config_manager().get("file_organization.templates", {})
    template = templates.get(template_type)
    # The settings page used to seed the singles input with this exact string
    # and Save persisted it — so most installs carry the old default without
    # ever having customized anything. A stored value that IS the old default
    # upgrades to the new one (same pattern as the YouTube template upgrade);
    # anything the user actually edited is untouched.
    if template_type == "single_path" and template == "$artist/$artist - $title/$title":
        template = None
    if not template:
        default_templates = {
            "album_path": "$albumartist/$albumartist - $album/$track - $title",
            # $albumartist, not $artist, for the FOLDER identity: only
            # $albumartist honors the Collaborative Album Artist setting, so a
            # multi-artist single ("A, B & C") files under its main artist when
            # the mode is 'first' (TheHomeGuy). For single-artist tracks the two
            # are identical; users with a CUSTOM template are untouched.
            "single_path": "$albumartist/$albumartist - $title/$title",
            "compilation_path": "Compilations/$album/$track - $artist - $title",
            "playlist_path": "$playlist/$artist - $title",
        }
        template = default_templates.get(template_type, "$artist/$album/$track - $title")

    _template_has_disc = template_uses_disc_variable(template)
    full_path = apply_path_template(template, context)

    path_parts = full_path.split("/")
    quality_value = context.get("quality", "")
    disc_number = _coerce_int(context.get("disc_number", 1), 1)
    # #981: $disc/$discnum are empty on single-disc albums, same as $cdnum — a
    # single-disc album shouldn't stamp "01-" on every filename. Multi-disc is
    # either 2+ total discs OR a track that's itself on disc 2+ (so a known disc-3
    # track still shows even if total_discs wasn't populated). The leading-dash
    # cleanup below drops the orphaned separator (e.g. "$disc-$track" -> "$track").
    _multi_disc = _coerce_int(context.get("total_discs", 1), 1) > 1 or disc_number > 1
    disc_value = f"{disc_number:02d}" if _multi_disc else ""
    disc_value_raw = str(disc_number) if _multi_disc else ""

    if len(path_parts) > 1:
        folder_parts = path_parts[:-1]
        filename_base = path_parts[-1]

        cleaned_folders = []
        for part in folder_parts:
            part = _clean_folder_segment(part, disc_value, disc_value_raw,
                                         _template_has_disc)
            if part:
                cleaned_folders.append(part)

        filename_base = filename_base.replace("$quality", quality_value)
        filename_base = filename_base.replace("$discnum", disc_value_raw)
        filename_base = filename_base.replace("$disc", disc_value)
        filename_base = re.sub(r"\s*\[\s*\]", "", filename_base)
        filename_base = re.sub(r"\s*\(\s*\)", "", filename_base)
        filename_base = re.sub(r"\s*\{\s*\}", "", filename_base)
        filename_base = re.sub(r"\s*-\s*$", "", filename_base)
        # Leading dash cleanup — lets $cdnum at the start of a filename
        # cleanly disappear on single-disc albums (empty-value case).
        filename_base = re.sub(r"^\s*-\s*", "", filename_base)
        filename_base = re.sub(r"\s+", " ", filename_base).strip()

        sanitized_folders = [sanitize_filename(part) for part in cleaned_folders]
        folder_path = os.path.join(*sanitized_folders) if sanitized_folders else ""
        filename = sanitize_filename(filename_base)
        return folder_path, filename

    full_path = full_path.replace("$quality", quality_value)
    full_path = full_path.replace("$discnum", disc_value_raw)
    full_path = full_path.replace("$disc", disc_value)
    full_path = re.sub(r"\s*\[\s*\]", "", full_path)
    full_path = re.sub(r"\s*\(\s*\)", "", full_path)
    full_path = re.sub(r"\s*\{\s*\}", "", full_path)
    full_path = re.sub(r"\s*-\s*$", "", full_path)
    full_path = re.sub(r"\s+", " ", full_path).strip()
    return "", sanitize_filename(full_path)


def _max_disc_number(album_tracks: Any) -> int:
    items = []
    if isinstance(album_tracks, dict):
        items = album_tracks.get("items") or album_tracks.get("tracks") or []
    elif isinstance(album_tracks, list):
        items = album_tracks

    max_disc = 1
    for track in items:
        if not isinstance(track, dict):
            continue
        try:
            disc_number = int(track.get("disc_number", 1) or 1)
        except (TypeError, ValueError):
            disc_number = 1
        if disc_number > max_disc:
            max_disc = disc_number
    return max_disc


def _coerce_int(value: Any, default: int = 1) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    return coerced if coerced > 0 else default


def _reachable_original_dir(original_path: Any) -> str | None:
    """Directory of an enhance/upgrade's existing library file, or ``None``.

    ``None`` means "do not replace in place": either the recorded path can't be
    mapped onto anything this process can see, or its folder no longer exists.
    The caller then builds the normal template destination (#1109) instead of
    trying to ``makedirs`` a media-server-only root such as ``/music``.

    Note this only ever RETURNS an existing directory — it never creates one.
    That is the whole point: the old behaviour created the media server's path
    inside the container.
    """
    if not isinstance(original_path, str) or not original_path.strip():
        return None
    candidates = [original_path]
    try:
        from core.library.path_resolver import resolve_library_file_path
        resolved = resolve_library_file_path(
            original_path, config_manager=_get_config_manager())
        if resolved:
            candidates.append(resolved)
    except Exception as exc:  # noqa: BLE001 - resolution is best-effort
        logger.debug("[Enhance] library path resolution failed for %r: %s",
                     original_path, exc)
    roots = _configured_root_dirs()
    for candidate in candidates:
        parent = os.path.dirname(candidate)
        if not parent or not os.path.isdir(parent):
            continue
        # A ROOT is not an album folder. When the recorded file sits loose in
        # the Transfer/download root or a library root, that root exists, so
        # "replace in place" would drop the upgrade straight back into the root
        # — the exact symptom #1109 was reported as. Falling through to the
        # template files it under Artist/Album instead, and the retirement step
        # then removes the loose original.
        if os.path.normpath(parent) in roots:
            logger.debug(
                "[Enhance] original sits in a library root (%s) — filing the "
                "upgrade by template instead of replacing in place", parent)
            continue
        return parent
    return None


def _configured_root_dirs() -> set[str]:
    """Normalized roots that must never be treated as an album folder."""
    roots: set[str] = set()
    try:
        cfg = _get_config_manager()
        raw = [cfg.get("soulseek.transfer_path", "./Transfer"),
               cfg.get("soulseek.download_path", "./downloads")]
        music_paths = cfg.get("library.music_paths", []) or []
        if isinstance(music_paths, str):
            music_paths = [music_paths]
        raw.extend(music_paths)
        for value in raw:
            if isinstance(value, str) and value.strip():
                roots.add(config_root_path(value))
    except Exception as exc:  # noqa: BLE001 - a missing config must not block the upgrade
        logger.debug("[Enhance] could not read the configured roots: %s", exc)
    return roots


def build_final_path_for_track(context, artist_context, album_info, file_ext, create_dirs: bool = True):
    """Shared path builder used by both post-processing and verification.

    ``create_dirs`` gates the directory-creation side effects. The download
    import flow leaves it True (it's about to write the file there). The
    library-reorganize PREVIEW passes False so a dry run can compute the exact
    destination path WITHOUT physically creating the folder — fixes #767 (dry
    run was leaving empty destination folders behind)."""
    _real_makedirs = os.makedirs

    def _ensure_dir(path, **_kw):
        if create_dirs:
            _real_makedirs(path, exist_ok=True)

    transfer_dir = config_root_path(
        _get_config_manager().get("soulseek.transfer_path", "./Transfer"), "./Transfer")
    context = normalize_import_context(context)
    track_info = get_import_track_info(context)
    original_search = get_import_original_search(context)
    album_context = get_import_context_album(context)
    source = get_import_source(context)
    artist_name = extract_artist_name(artist_context)

    source_info = track_info.get("source_info") or {}
    if isinstance(source_info, str):
        try:
            source_info = json.loads(source_info)
        except (json.JSONDecodeError, TypeError):
            source_info = {}
    if source_info.get("enhance") and source_info.get("original_file_path"):
        original_dir = _reachable_original_dir(source_info["original_file_path"])
        if original_dir:
            original_stem = os.path.splitext(
                os.path.basename(source_info["original_file_path"]))[0]
            final_path = os.path.join(original_dir, original_stem + file_ext)
            logger.info("[Enhance] Using original file location: %s", final_path)
            return final_path, True
        # #1109: `original_file_path` is the path as RECORDED, which is usually
        # the media server's view of the library ("/music/…") rather than
        # anything this process can reach. The old code ran makedirs on it
        # unconditionally — creating a bogus container-local tree at best, and
        # at worst dying with PermissionError, which aborted post-processing
        # and left the finished upgrade sitting unsorted in the library root.
        # An unreachable original is not a destination: fall through to the
        # normal template so the upgrade still lands in Artist/Album.
        logger.info(
            "[Enhance] Original location %r is not reachable from here — "
            "using the normal template destination instead.",
            source_info["original_file_path"])

    year = ""
    if album_context and album_context.get("release_date"):
        year = _extract_year_from_release_date(album_context["release_date"])

    raw_album_type = ""
    if album_context:
        raw_album_type = album_context.get("album_type", "") or ""
    total_tracks = (album_context.get("total_tracks", 0) or 0) if album_context else 0
    album_type_display = get_album_type_display(raw_album_type, total_tracks)

    if album_info and album_info.get("is_album"):
        clean_track_name = get_import_clean_title(context, album_info=album_info, default=original_search.get("title", "Unknown Track"))
        track_number = _coerce_int(album_info.get("track_number", 1), 1)
        disc_number = _coerce_int(album_info.get("disc_number", 1), 1)
        _artists = original_search.get("artists") or track_info.get("artists") or []
        _album_ctx = album_context
        _itunes_aid = None
        _is_itunes = source == "itunes" or (isinstance(artist_context, dict) and str(artist_context.get("id", "")).isdigit() and source != "deezer")
        if _is_itunes and isinstance(artist_context, dict):
            _aid = artist_context.get("id", "")
            if str(_aid).isdigit():
                _itunes_aid = str(_aid)
        if not _itunes_aid and _album_ctx:
            _ext = _album_ctx.get("external_urls", {})
            if isinstance(_ext, dict) and _ext.get("itunes_artist_id"):
                _itunes_aid = _ext["itunes_artist_id"]

        _artist_name = artist_name
        _album_artist_name = _artist_name
        _album_artists_for_collab = None
        _explicit_artist_ctx = track_info.get("_explicit_artist_context") if isinstance(track_info, dict) else None
        if isinstance(_explicit_artist_ctx, dict) and _explicit_artist_ctx.get("name"):
            _album_artist_name = _explicit_artist_ctx["name"]
            _album_artists_for_collab = [_explicit_artist_ctx]
        elif isinstance(_explicit_artist_ctx, str) and _explicit_artist_ctx:
            _album_artist_name = _explicit_artist_ctx
            _album_artists_for_collab = [{"name": _explicit_artist_ctx}]
        else:
            _sa_artists = _album_ctx.get("artists", []) if _album_ctx else []
            if _sa_artists:
                _first_sa = _sa_artists[0]
                if isinstance(_first_sa, dict) and _first_sa.get("name"):
                    _album_artist_name = _first_sa["name"]
                elif isinstance(_first_sa, str) and _first_sa:
                    _album_artist_name = _first_sa
                _album_artists_for_collab = _sa_artists

        # #989: an iTunes single's collection carries a placeholder album artist
        # ("Unknown Artist") — or none — while the track artist ($artist) is real.
        # Don't let that bury the file under "Unknown Artist"; fall back to the real
        # track artist so $albumartist matches $artist.
        if (not _album_artist_name or _album_artist_name == "Unknown Artist") and \
                _artist_name and _artist_name != "Unknown Artist":
            _album_artist_name = _artist_name

        template_context = {
            "artist": _artist_name,
            "albumartist": _album_artist_name,
            "album": album_info["album_name"],
            "title": clean_track_name,
            "track_number": track_number,
            "disc_number": disc_number,
            "year": year,
            "quality": context.get("_audio_quality", ""),
            "albumtype": album_type_display,
            "_artists_list": _album_artists_for_collab if _album_artists_for_collab else _artists,
            "_itunes_artist_id": _itunes_aid,
        }
        # A caller that KNOWS the disc count is authoritative: re-deriving it from
        # a live provider tracklist made the destination depend on whether that
        # lookup happened to succeed, so a cache miss or an offline provider filed
        # two tracks of one album in two folders (Album/01.flac vs
        # Album/Disc 1/01.flac).
        #
        # "Knows" has to be stated, not inferred from the value. Almost every
        # context builder writes `.get("total_discs", 1)` (core/downloads/
        # candidates.py, staging.py, master.py) and a Spotify album object carries
        # no disc count at all, so a bare 1 means "nobody told me". Reading that as
        # a declaration would silence the #981 lookup for playlist and wishlist
        # downloads and file a disc-1 track of a real 2-disc release flat.
        _declared_total_discs = bool(
            album_context.get("total_discs_declared")) if album_context else False
        total_discs = _coerce_int(
            album_context.get("total_discs") if album_context else None, 1)

        if total_discs <= 1 and album_context and album_context.get("id"):
            if disc_number > 1:
                # A track that is itself on disc 2+ proves the release is
                # multi-disc whatever the caller said.
                total_discs = disc_number
            elif not _declared_total_discs:
                try:
                    _album_tracks = _get_album_tracks_for_source(source, str(album_context["id"]))
                    if _album_tracks:
                        total_discs = _max_disc_number(_album_tracks)
                        if total_discs > 1:
                            album_context["total_discs"] = total_discs
                            logger.info(
                                "[Multi-Disc] Resolved %s discs for single-track download of %r",
                                total_discs,
                                album_context.get("name"),
                            )
                except Exception as _disc_err:
                    logger.warning("[Multi-Disc] Could not resolve total_discs: %s", _disc_err)

        # Now that total_discs is fully resolved, expose it to the template
        # so $cdnum can decide between "CDxx" and an empty string.
        template_context["total_discs"] = total_discs

        _template_key = "compilation_path" if raw_album_type in ("compilation", "compile") else "album_path"

        album_template = _get_config_manager().get("file_organization.templates", {}).get(_template_key, "") or ""
        # Suppress the auto-injected disc folder when the user already
        # encodes the disc in the filename via $disc, $discnum, or $cdnum.
        user_controls_disc = (
            "$disc" in album_template
            or "$cdnum" in album_template
            or "${disc}" in album_template
            or "${discnum}" in album_template
            or "${cdnum}" in album_template
        )
        disc_label = _get_config_manager().get("file_organization.disc_label", "Disc")

        folder_path, filename_base = get_file_path_from_template(template_context, _template_key)

        # #829: if this album already lives in a single folder on disk, drop the
        # new track there instead of a freshly-templated folder — this is what
        # keeps an album from splitting when $albumtype/$year drift between
        # batches (wishlist, Album Completeness, a missed track later). Strict
        # match + transfer-dir-only + single-folder-only inside the resolver;
        # any miss falls through to the template path below. Best-effort.
        # Compilations skip reuse — their namespace moved from artist-based to
        # Compilations/, so matching an old artist folder would scatter tracks.
        # Multi-disc albums skip reuse too (#1009): their correct layout is
        # SEVERAL folders (one per disc — $cdnum/$disc templates or the auto
        # "Disc N" folder), but the resolver can only answer "the album's one
        # existing folder" (DatabaseTrack carries no disc number). Mid-download
        # that one folder is whichever disc landed first, so every later track
        # of a box set would be funneled into it — collapsing all discs into a
        # single disc folder and colliding same-numbered filenames.
        # Reorganize disables reuse outright (`_no_album_folder_reuse`): its whole
        # job is to move albums OUT of the folder they currently sit in, and the
        # resolver would answer with exactly that folder — making every reorganize
        # compute "destination == current location" and no-op (the template-change
        # complaint from TheHomeGuy).
        reuse_folder = None
        _multi_disc_album = total_discs > 1 or disc_number > 1
        if (filename_base and not _multi_disc_album
                and raw_album_type not in ("compilation", "compile")
                and not context.get("_no_album_folder_reuse")):
            try:
                from core.library.existing_album_folder import resolve_existing_album_folder
                from database.music_database import get_database
                try:
                    _active_server = _get_config_manager().get_active_media_server()
                except Exception:
                    _active_server = None
                _spotify_album_id = (album_context.get("id")
                                     if album_context and str(source).startswith("spotify") else None)
                _expected_tracks = None
                if album_context and album_context.get("total_tracks"):
                    _expected_tracks = _coerce_int(album_context.get("total_tracks"), 0) or None
                reuse_folder = resolve_existing_album_folder(
                    db=get_database(),
                    transfer_dir=transfer_dir,
                    album_name=album_info.get("album_name"),
                    album_artist=template_context.get("albumartist"),
                    spotify_album_id=_spotify_album_id,
                    active_server=_active_server,
                    expected_track_count=_expected_tracks,
                    config_manager=_get_config_manager(),
                )
            except Exception as _reuse_err:
                logger.debug("[Existing Album Folder] lookup failed: %s", _reuse_err)
                reuse_folder = None
        if reuse_folder and filename_base:
            final_path = os.path.join(reuse_folder, filename_base + file_ext)
            _ensure_dir(reuse_folder, exist_ok=True)
            return final_path, True

        if folder_path and filename_base:
            # #1091: steer into the folder that already exists when it differs
            # only in case. Without this, metadata casing that disagrees with
            # disk makes a SECOND folder on case-sensitive filesystems (the
            # album splits in Jellyfin) and, on case-insensitive ones, records
            # a path that is not how the directory is actually spelled.
            # Resolved once and used for BOTH the created directory and the
            # returned path, so the two can never disagree.
            if total_discs > 1 and not user_controls_disc:
                disc_folder = f"{disc_label} {disc_number}"
                album_dir = resolve_existing_case_dir(
                    transfer_dir, os.path.join(folder_path, disc_folder))
            else:
                album_dir = resolve_existing_case_dir(transfer_dir, folder_path)
            final_path = os.path.join(album_dir, filename_base + file_ext)
            _ensure_dir(album_dir, exist_ok=True)
            return final_path, True

        album_name_sanitized = sanitize_filename(album_info["album_name"])
        if raw_album_type in ("compilation", "compile"):
            album_relative = os.path.join("Compilations", album_name_sanitized)
        else:
            artist_name_sanitized = sanitize_filename(template_context["albumartist"])
            album_folder_name = f"{artist_name_sanitized} - {album_name_sanitized}"
            album_relative = os.path.join(artist_name_sanitized, album_folder_name)
        if total_discs > 1:
            album_relative = os.path.join(album_relative, f"{disc_label} {disc_number}")
        # #1091, same rule on the no-template path.
        album_dir = resolve_existing_case_dir(transfer_dir, album_relative)
        _ensure_dir(album_dir, exist_ok=True)
        final_track_name_sanitized = sanitize_filename(clean_track_name)
        new_filename = f"{track_number:02d} - {final_track_name_sanitized}{file_ext}"
        return os.path.join(album_dir, new_filename), True

    clean_track_name = get_import_clean_title(context, album_info=album_info, default=original_search.get("title", "Unknown Track"))
    _artists = original_search.get("artists") or track_info.get("artists") or []
    _album_ctx = album_context
    _itunes_aid = None
    _is_itunes = source == "itunes" or (isinstance(artist_context, dict) and str(artist_context.get("id", "")).isdigit() and source != "deezer")
    if _is_itunes and isinstance(artist_context, dict):
        _aid = artist_context.get("id", "")
        if str(_aid).isdigit():
            _itunes_aid = str(_aid)
    if not _itunes_aid and _album_ctx:
        _ext = _album_ctx.get("external_urls", {})
        if isinstance(_ext, dict) and _ext.get("itunes_artist_id"):
            _itunes_aid = _ext["itunes_artist_id"]

    template_context = {
        "artist": artist_name,
        "albumartist": artist_name,
        "album": album_info.get("album_name", clean_track_name) if album_info else clean_track_name,
        "title": clean_track_name,
        "track_number": 1,
        "disc_number": 1,
        "year": year,
        "quality": context.get("_audio_quality", ""),
        "albumtype": album_type_display,
        "_artists_list": _artists,
        "_itunes_artist_id": _itunes_aid,
    }

    folder_path, filename_base = get_file_path_from_template(template_context, "single_path")
    if filename_base:
        if folder_path:
            final_path = os.path.join(transfer_dir, folder_path, filename_base + file_ext)
            _ensure_dir(os.path.join(transfer_dir, folder_path), exist_ok=True)
        else:
            final_path = os.path.join(transfer_dir, filename_base + file_ext)
            _ensure_dir(transfer_dir, exist_ok=True)
        return final_path, True

    artist_name_sanitized = sanitize_filename(template_context["artist"])
    final_track_name_sanitized = sanitize_filename(clean_track_name)
    artist_dir = os.path.join(transfer_dir, artist_name_sanitized)
    single_folder_name = f"{artist_name_sanitized} - {final_track_name_sanitized}"
    single_dir = os.path.join(artist_dir, single_folder_name)
    _ensure_dir(single_dir, exist_ok=True)
    new_filename = f"{final_track_name_sanitized}{file_ext}"
    return os.path.join(single_dir, new_filename), True
