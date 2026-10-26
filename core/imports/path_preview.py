"""What a naming template will make, before anything is saved (#1868).

the settings page sends the templates and toggles as they stand in the form.
each one renders through the same code a real import, music video or sample
chop uses, against a made-up release, so the preview can't drift from what
lands on disk. the release changes with the scenario: a plain album, an
explicit one, disc 2 of a two-disc set, or an artist that shares its name
with another one in the library. nothing here reads the library or saves.
"""

from __future__ import annotations

import posixpath
from typing import Any, Dict

from core.imports.album_types import format_album_types
from core.imports.paths import (
    artist_letter,
    auto_disc_folder,
    get_file_path_from_template,
    is_single_path_customized,
    preview_settings,
    template_uses_disc_variable,
)
from utils.logging_config import get_logger

logger = get_logger("imports.path_preview")

SCENARIOS = ("standard", "explicit", "multidisc", "samename")

# the toggles in the form that change how a path renders
_SETTING_KEYS = (
    "auto_disambiguation",
    "collab_artist_mode",
    "artistletter_symbol_fallback",
    "unicode_substitution",
    "disc_label",
)

_RELEASES: Dict[str, Dict[str, Any]] = {
    "standard": {
        "artist": "Daft Punk", "album": "Random Access Memories", "year": "2013",
        "title": "Get Lucky", "track_number": 8, "label": "Columbia",
        "explicit": False, "disc_number": 1, "total_discs": 1, "disambiguation": "",
        "facts": {"disambiguation": "French electronic duo", "country": "FR",
                  "genre": "Electronic", "clash": False},
        "single": "One More Time", "single_year": "2000",
    },
    "explicit": {
        "artist": "Kendrick Lamar", "album": "DAMN.", "year": "2017",
        "title": "HUMBLE.", "track_number": 8, "label": "Top Dawg Entertainment",
        "explicit": True, "disc_number": 1, "total_discs": 1, "disambiguation": "",
        "facts": {"disambiguation": "", "country": "US", "genre": "Hip Hop", "clash": False},
        "single": "HUMBLE.", "single_year": "2017",
    },
    "multidisc": {
        "artist": "The Beatles", "album": "The Beatles", "year": "1968",
        "title": "Birthday", "track_number": 1, "label": "Apple",
        "explicit": False, "disc_number": 2, "total_discs": 2,
        "disambiguation": "2009 remaster",
        "facts": {"disambiguation": "", "country": "GB", "genre": "Rock", "clash": False},
        "single": "Hey Jude", "single_year": "1968",
    },
    "samename": {
        "artist": "Nirvana", "album": "Bleach", "year": "1989",
        "title": "Blew", "track_number": 1, "label": "Sub Pop",
        "explicit": False, "disc_number": 1, "total_discs": 1, "disambiguation": "",
        "facts": {"disambiguation": "US grunge band", "country": "US",
                  "genre": "Grunge", "clash": True},
        "single": "Love Buzz", "single_year": "1988",
    },
}

_COMPILATION = {
    "album": "Pulp Fiction (Music From the Motion Picture)", "year": "1994",
    "artist": "Dick Dale & His Del-Tones", "title": "Misirlou", "track_number": 2,
    "label": "MCA",
    "facts": {"disambiguation": "", "country": "US", "genre": "Surf Rock", "clash": False},
}

_VA_FACTS = {"disambiguation": "", "country": "", "genre": "", "clash": False}
_EXT = ".flac"


def _atypes(album_type: str, various: bool = False) -> str:
    from core.imports.album_types import album_types_config
    from core.imports.paths import _get_config_manager
    try:
        return format_album_types({"album_type": album_type},
                                  album_types_config(_get_config_manager()),
                                  is_various_artists=various)
    except Exception as e:  # noqa: BLE001 - a label never breaks the preview
        logger.debug("preview atypes failed: %s", e)
        return ""


def _track_context(release: dict, *, single: bool = False) -> dict:
    artist = release["artist"]
    return {
        "artist": artist,
        "albumartist": artist,
        "album": release["single"] if single else release["album"],
        "title": release["single"] if single else release["title"],
        "track_number": 1 if single else release["track_number"],
        "disc_number": 1 if single else release["disc_number"],
        "total_discs": 1 if single else release["total_discs"],
        "year": release["single_year"] if single else release["year"],
        "quality": "FLAC 16bit",
        "albumtype": "Single" if single else "Album",
        "atypes": _atypes("single" if single else "album"),
        "_artists_list": [{"name": artist}],
        "_track_artists_list": [{"name": artist}],
        "disambiguation": "" if single else release["disambiguation"],
        "track_disambiguation": "",
        "label": release["label"],
        "explicit": release["explicit"],
        "album_explicit": release["explicit"],
        "_artist_facts": {"album": release["facts"]},
    }


def _compilation_context(release: dict) -> dict:
    comp = _COMPILATION
    return {
        "artist": comp["artist"],
        "albumartist": "Various Artists",
        "album": comp["album"],
        "title": comp["title"],
        "track_number": comp["track_number"],
        "disc_number": release["disc_number"],
        "total_discs": release["total_discs"],
        "year": comp["year"],
        "quality": "FLAC 16bit",
        "albumtype": "Compilation",
        "atypes": _atypes("compilation", various=True),
        "_artists_list": [{"name": "Various Artists"}],
        "_track_artists_list": [{"name": comp["artist"]}],
        "disambiguation": "",
        "track_disambiguation": "",
        "label": comp["label"],
        "explicit": release["explicit"],
        "album_explicit": release["explicit"],
        "_artist_facts": {"album": _VA_FACTS, "track": comp["facts"]},
    }


def _album_path(context: dict, template_type: str, template: str, disc_label: str) -> str:
    folder, filename = get_file_path_from_template(context, template_type)
    folder = (folder or "").replace("\\", "/")
    disc = auto_disc_folder(template, context["total_discs"], context["disc_number"], disc_label)
    if folder and disc:
        folder = posixpath.join(folder, disc)
    return posixpath.join(folder, filename + _EXT) if folder else filename + _EXT


def _video_path(template: str, release: dict) -> str:
    from core.downloads.music_video import render_path
    folder, stem = render_path(template, release["artist"], release["title"],
                               release["year"], artist_letter)
    return posixpath.join(folder, stem + ".mp4") if folder else stem + ".mp4"


def _sample_path(template: str, release: dict) -> str:
    from core.sample.organize import render_sample_path
    segments = render_sample_path(template, {
        "artist": release["artist"], "track": release["title"],
        "album": release["album"], "chop": "Intro chop", "stem": "drums",
    })
    return "/".join(segments) + ".wav"


def _playlist_item(template: str, release: dict, library_name: str) -> str:
    from core.playlists.item_naming import render_playlist_item_name
    return render_playlist_item_name(
        template, title=release["title"], artist=release["artist"],
        album=release["album"], track=release["track_number"], position="03",
        ext=_EXT, fallback_name=library_name)


def preview_paths(templates: dict, settings: dict, scenario: str = "standard") -> dict:
    """{template key: example path} for each template, plus how the
    multi-disc label plays out. a template that fails to render gets an
    "error" entry instead of taking the rest down with it."""
    templates = {k: str(v or "") for k, v in (templates or {}).items()}
    settings = settings or {}
    scenario = scenario if scenario in _RELEASES else "standard"
    release = _RELEASES[scenario]
    disc_label = str(settings.get("disc_label") or "Disc")

    overrides = {f"file_organization.{k}": settings[k] for k in _SETTING_KEYS if k in settings}
    overrides["file_organization.enabled"] = True
    overrides["file_organization.templates"] = templates
    overrides["file_organization.disc_label"] = disc_label

    jobs = {
        "album_path": lambda: _album_path(_track_context(release), "album_path",
                                          templates.get("album_path", ""), disc_label),
        "single_path": lambda: _album_path(_track_context(release, single=True), "single_path",
                                           templates.get("single_path", ""), disc_label),
        # a release the source calls a single goes through the album template
        # until the single one is changed from its default
        "single_as_album": lambda: (
            None if is_single_path_customized()
            else _album_path(_track_context(release, single=True), "album_path",
                             templates.get("album_path", ""), disc_label)),
        "compilation_path": lambda: _album_path(_compilation_context(release), "compilation_path",
                                                templates.get("compilation_path", ""), disc_label),
        # empty keeps the library filename, so it falls back to the album one
        "playlist_item": lambda: _playlist_item(
            templates.get("playlist_item", ""), release,
            posixpath.basename(_album_path(_track_context(release), "album_path",
                                           templates.get("album_path", ""), disc_label))),
        "video_path": lambda: _video_path(templates.get("video_path", ""), release),
        "sample_path": lambda: _sample_path(templates.get("sample_path", ""), release),
        # the label only shows on a multi-disc album, so it always previews one
        "disc_label": lambda: _album_path(_track_context(_RELEASES["multidisc"]), "album_path",
                                          templates.get("album_path", ""), disc_label),
    }
    paths: Dict[str, Any] = {}
    with preview_settings(overrides):
        for key, job in jobs.items():
            try:
                paths[key] = job()
            except Exception as e:  # noqa: BLE001 - one bad template, one error line
                logger.debug("path preview failed for %s: %s", key, e)
                paths[key] = {"error": str(e) or e.__class__.__name__}
    return {
        "scenario": scenario,
        "paths": paths,
        # the template placing the disc itself means the label goes unused
        "disc_label_used": not template_uses_disc_variable(templates.get("album_path", "")),
    }
