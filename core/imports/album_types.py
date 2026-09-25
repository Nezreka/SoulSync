"""The ``$atypes`` template variable — beets-compatible release-type labels.

``$albumtype`` answers "what kind of release is this?" with exactly one word,
and always answers: a plain album gets the literal string ``Album``. That is
useful in a filename, and wrong in a folder name, because it puts the word
``Album`` in front of every album a user owns.

beets solves the same problem with its ``albumtypes`` plugin, which emits
*nothing* for an ordinary album and a bracketed label for each qualifier a
release actually carries:

    [2017][EP][Live] Audiotree Live
    [2007][Live][Anthology] Salival
    [2019] Tokyo                        <- a plain album, no marker

Libraries organised by beets before moving to SoulSync are full of those, so
without an equivalent, SoulSync can never reproduce the layout it inherited and
every album it touches drifts into a second convention. This module is that
equivalent, deliberately configured the same way beets is, so a user can copy
the ``types``/``bracket``/``ignore_va`` block straight out of their beets
config.

Where the labels come from
--------------------------
MusicBrainz gives a release group one PRIMARY type (Album, Single, EP, ...) and
any number of SECONDARY types (Live, Compilation, Soundtrack, Remix, ...), and
SoulSync already carries both on the album context. A release is frequently
several things at once — a live EP is ``EP`` + ``Live`` — which is why the
output concatenates rather than picking a winner.

Emission order follows the CONFIGURED order, not the order the source happened
to list them in, so the same release always produces the same folder name.
Sources disagree about ordering and a folder name that depends on which one
answered is a folder that splits.

Only MusicBrainz publishes secondary types. Spotify, Deezer and iTunes carry a
primary type alone, so on those sources this realistically emits ``[EP]`` and
``[Single]`` and nothing else — the qualifiers that make the variable
interesting need an MB-backed release.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("imports.album_types")

# Mirrors the example in the beets albumtypes documentation, which is what most
# beets configs are derived from. Ordered: the output follows this order.
DEFAULT_TYPES: Tuple[Tuple[str, str], ...] = (
    ("ep", "EP"),
    ("single", "Single"),
    ("soundtrack", "OST"),
    ("live", "Live"),
    ("compilation", "Anthology"),
    ("remix", "Remix"),
)
DEFAULT_BRACKET = "[]"
# A various-artists compilation is already filed under Compilations/; repeating
# the qualifier in the folder name is noise. beets calls this ignore_va.
DEFAULT_IGNORE_VA: Tuple[str, ...] = ("compilation",)


def normalize_types(raw: Any) -> List[Tuple[str, str]]:
    """The configured type→label pairs, in order.

    Accepts both shapes so a beets config can be pasted without translation:

    * a mapping — ``{"ep": "EP", "live": "Live"}`` (insertion ordered)
    * beets' own list of single-key mappings — ``[{"ep": "EP"}, {"live": "Live"}]``

    A label that is empty or not a string drops the entry rather than emitting
    ``[]``.
    """
    if raw is None:
        return list(DEFAULT_TYPES)

    pairs: List[Tuple[str, str]] = []
    if isinstance(raw, Mapping):
        items: Iterable[Tuple[Any, Any]] = raw.items()
    elif isinstance(raw, (list, tuple)):
        items = []
        for entry in raw:
            if isinstance(entry, Mapping):
                items.extend(entry.items())
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                items.append((entry[0], entry[1]))
    else:
        logger.debug("album_types.types is %s, not a mapping or list — using defaults",
                     type(raw).__name__)
        return list(DEFAULT_TYPES)

    for key, label in items:
        key_s = str(key or "").strip().lower()
        label_s = str(label or "").strip()
        if key_s and label_s:
            pairs.append((key_s, label_s))
    return pairs


def _brackets(raw: Any) -> Tuple[str, str]:
    """``(open, close)`` from a beets-style bracket string.

    ``'[]'`` → ``('[', ']')``; a single character wraps on both sides; empty
    means no bracketing at all, which beets also allows.
    """
    text = "" if raw is None else str(raw)
    if not text:
        return "", ""
    if len(text) == 1:
        return text, text
    return text[0], text[1]


def _ignore_va(raw: Any) -> set:
    if raw is None:
        return set(DEFAULT_IGNORE_VA)
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        return set(DEFAULT_IGNORE_VA)
    return {str(v or "").strip().lower() for v in values if str(v or "").strip()}


def release_types(album_ctx: Optional[Mapping[str, Any]]) -> set:
    """Every release-type string the source gave us, lowercased.

    Primary type under whichever key the source used, plus MusicBrainz's
    secondary types. Mirrors ``core.imports.compilation._album_types``; kept
    separate because that one answers a yes/no question about compilations and
    this one feeds a user-visible folder name.
    """
    if not isinstance(album_ctx, Mapping):
        return set()
    types = set()
    for key in ("album_type", "record_type", "primary_type", "type"):
        value = album_ctx.get(key)
        if str(value or "").strip():
            types.add(str(value).strip().lower())
    for key in ("secondary_types", "secondary_type"):
        secondary = album_ctx.get(key)
        if isinstance(secondary, str):
            secondary = [secondary]
        if isinstance(secondary, (list, tuple, set)):
            for entry in secondary:
                if str(entry or "").strip():
                    types.add(str(entry).strip().lower())
    return types


def format_album_types(album_ctx: Optional[Mapping[str, Any]],
                       config: Optional[Mapping[str, Any]] = None,
                       *, is_various_artists: bool = False) -> str:
    """The ``$atypes`` value for a release — possibly, and usually, empty.

    Empty is the common case and the point of the variable: a plain album
    carries no qualifier, so it gets no marker and the folder is just
    ``[year] Album``.
    """
    config = config or {}
    pairs = normalize_types(config.get("types"))
    if not pairs:
        return ""

    present = release_types(album_ctx)
    if not present:
        return ""

    # ignore_va is about the CREDIT, not the type. MusicBrainz gives a single
    # artist's own anthology secondary=[Compilation], which map_release_group_type
    # turns into album_type="compilation" — keying off that dropped [Anthology]
    # from Tool's Salival, which is not a various-artists release.
    skip = _ignore_va(config.get("ignore_va")) if is_various_artists else set()
    open_b, close_b = _brackets(config.get("bracket", DEFAULT_BRACKET))

    out = []
    for key, label in pairs:
        if key in present and key not in skip:
            out.append(f"{open_b}{label}{close_b}")
    return "".join(out)


def album_types_config(config_manager) -> Dict[str, Any]:
    """Read ``file_organization.album_types``, tolerating a missing config."""
    try:
        cfg = config_manager.get("file_organization.album_types", None)
    except Exception:  # noqa: BLE001 - a config read must never fail a path build
        cfg = None
    return cfg if isinstance(cfg, Mapping) else {}


__all__ = [
    "DEFAULT_BRACKET",
    "DEFAULT_IGNORE_VA",
    "DEFAULT_TYPES",
    "album_types_config",
    "format_album_types",
    "normalize_types",
    "release_types",
]
