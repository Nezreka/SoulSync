"""Strict JSON field parsing shared by the legacy and modular request handlers.

Audit finding P3-01: several endpoints accepted whatever JSON arrived and let
Python truthiness decide. ``bool("false")`` is ``True``, a list passed as an id
survived until a ``.isdigit()`` deep inside the database layer raised a 500, and
an unknown Quality Profile id silently became the default.

These helpers separate the three states an optional field can be in:

* absent          -> caller keeps its own default (checked with ``in`` before calling)
* present + valid -> the parsed value
* present + junk  -> ``None``, which the caller turns into a 400

They deliberately do NOT coerce: ``"true"``/``"1"`` are accepted because HTML
forms and query strings genuinely produce them, but a number is not a boolean
and a list is never an id.
"""

from __future__ import annotations

from typing import Any, Optional

_TRUE_STRINGS = {"true", "1", "yes", "on"}
_FALSE_STRINGS = {"false", "0", "no", "off", ""}


def parse_strict_bool(value: Any) -> Optional[bool]:
    """A real boolean, or a form/query string spelling of one. Else ``None``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    return None


def parse_strict_int(value: Any) -> Optional[int]:
    """A real int, or a string that is entirely a (possibly signed) integer.

    ``True``/``False`` are rejected: in Python they are ints, but a client that
    sends a boolean where an id belongs has a bug we should surface.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and (stripped.lstrip("+-").isdigit()):
            try:
                return int(stripped)
            except ValueError:
                return None
    return None


def parse_strict_id(value: Any) -> Optional[str]:
    """A non-empty provider id as text.

    Provider ids are strings everywhere in this codebase (Spotify base62, Deezer
    digits, MusicBrainz UUIDs). A JSON number is accepted and normalised to its
    text form; a list/dict/bool/empty string is not an id.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


__all__ = ["parse_strict_bool", "parse_strict_int", "parse_strict_id"]
