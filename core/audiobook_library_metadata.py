"""Read existing audiobook metadata without changing files or querying a catalogue."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$", re.I)


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value or "").strip()


def read_metadata(path: Path, files: list[Path], probes=None, include_sidecars=True) -> dict:
    """Prefer sidecars, then embedded album tags, then the name on disk.

    A local identity is assigned by the scanner when no explicit ASIN exists.
    Titles and authors never imply ownership of a particular catalogue edition.
    """
    folder = path if path.is_dir() else path.parent
    facts = {"title": "", "author": "", "narrator": "", "asin": "",
             "series_title": "", "series_sequence": "", "runtime_minutes": 0,
             "language": "", "format_type": ""}

    def put(key, value):
        text = _text(value)
        if text and not facts.get(key):
            facts[key] = text

    # A loose file must not inherit another book's directory sidecar.
    sidecars = [folder / n for n in ("metadata.opf", "book.nfo", "metadata.json")] \
        if path.is_dir() else [path.with_suffix(".opf"), path.with_suffix(".nfo")]
    for sidecar in sidecars if include_sidecars else []:
        try:
            if sidecar.stat().st_size > 2 * 1024 * 1024:
                continue
            text = sidecar.read_text(encoding="utf-8-sig")
            if sidecar.suffix == ".json":
                data = json.loads(text)
                if not isinstance(data, dict):
                    continue
                for key, field in (("title", "title"), ("author", "authors"),
                                   ("narrator", "narrators"), ("asin", "asin")):
                    put(key, data.get(field))
                series = data.get("series") or []
                if series and isinstance(series[0], str):
                    put("series_title", series[0])
                continue
            # Do not expand externally supplied entities in library sidecars.
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                continue
            tree = ET.fromstring(text)
            for element in tree.iter():
                tag = element.tag.rsplit("}", 1)[-1].lower()
                attrs = {k.rsplit("}", 1)[-1].lower(): v for k, v in element.attrib.items()}
                value = element.text or ""
                if tag == "title":
                    put("title", value)
                elif tag == "creator":
                    put("narrator" if attrs.get("role") == "nrt" else "author", value)
                elif tag in ("author", "narrator", "asin", "language"):
                    put(tag, value)
                elif tag in ("identifier", "uniqueid") and \
                        (attrs.get("scheme", "").lower() == "asin" or attrs.get("type") == "asin"):
                    put("asin", value)
                elif tag == "meta":
                    key = {"calibre:series": "series_title", "calibre:series_index": "series_sequence"}.get(attrs.get("name"))
                    if key:
                        put(key, attrs.get("content"))
        except (OSError, ValueError, ET.ParseError, TypeError):
            continue

    duration = 0.0
    for file in files if probes is None else []:
        try:
            import mutagen
            audio = mutagen.File(file)
            if audio is None:
                continue
            duration += float(getattr(audio.info, "length", 0) or 0)
            tags = audio.tags or {}
            for key, names in {
                "title": ("TALB", "album", "\xa9alb"),
                "author": ("TPE2", "albumartist", "aART", "TPE1", "artist", "\xa9ART"),
                "narrator": ("TCOM", "composer", "\xa9wrt"),
                "asin": ("TXXX:ASIN", "asin", "ASIN", "----:com.apple.iTunes:ASIN"),
                "series_title": ("TXXX:SERIES", "series", "----:com.apple.iTunes:SERIES"),
                "series_sequence": ("TXXX:SERIES-PART", "series-part"),
                "language": ("TLAN", "language", "LANGUAGE"),
            }.items():
                for name in names:
                    if name in tags:
                        put(key, tags[name])
        except (ImportError, OSError, ValueError, TypeError, AttributeError):
            continue
        except Exception:  # malformed media is still visible by its filename
            continue
    if probes is not None:
        for probe in probes:
            for key in ("title", "author", "narrator", "asin", "series_title", "series_sequence", "language", "format_type"):
                if key == "title" and probe.get("title_source") == "filename":
                    continue
                put(key, probe.get(key))
            duration += probe.get("duration_seconds", 0)
    facts["duration_seconds"] = duration
    facts["runtime_minutes"] = round(duration / 60)
    facts["title_source"] = "metadata" if facts["title"] else "filename"
    text_title = str(facts["title"]).lower()
    if "unabridged" in text_title:
        facts["format_type"] = "unabridged"
    elif "abridged" in text_title:
        facts["format_type"] = "abridged"
    if not ASIN_RE.fullmatch(str(facts["asin"])):
        facts["asin"] = ""
    else:
        facts["asin"] = str(facts["asin"]).upper()
    facts["title"] = facts["title"] or (path.name if path.is_dir() else path.stem)
    return facts


def embedded_cover(path: Path):
    """Read one embedded cover on demand; never create artwork beside the book."""
    from core.audiobook_organizer import AUDIO_EXTENSIONS
    try:
        if path.is_file():
            files = [path]
        else:
            files = sorted(p for p in path.iterdir() if p.is_file()
                           and not p.is_symlink() and p.suffix.lower() in AUDIO_EXTENSIONS)
        for file in files[:3]:
            try:
                import mutagen
                audio = mutagen.File(file)
                if audio is None:
                    continue
                data = None
                tags = audio.tags or {}
                if tags.get('covr'):
                    data = bytes(tags['covr'][0])
                elif hasattr(tags, 'getall') and tags.getall('APIC'):
                    data = tags.getall('APIC')[0].data
                elif getattr(audio, 'pictures', None):
                    data = audio.pictures[0].data
                if not data or len(data) > 10 * 1024 * 1024:
                    continue
                if data.startswith(b'\xff\xd8\xff'):
                    return data, 'image/jpeg'
                if data.startswith(b'\x89PNG\r\n\x1a\n'):
                    return data, 'image/png'
            except Exception:
                continue
    except OSError:
        pass
    return None
