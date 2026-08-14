"""Does this watchlist artist auto-download — the artist's answer, or the global one.

Reported by swiftpawpaw: the watchlist's Global Override only chooses which release
FORMATS to take. Auto-download is per-artist only, so turning it off across a
225-artist watchlist means opening 225 artists.

Boulder's model, and the one built here: **the global is the DEFAULT, and an
artist's own setting beats it.** Turn the global off and nothing downloads except
the artists you explicitly turned on; turn it on and everything downloads except
the ones you explicitly turned off.

That needs a state the old column could not express. ``auto_download`` is
``INTEGER NOT NULL DEFAULT 1``, so every one of those 225 rows already says ``1``
and nothing can tell "the user chose this" from "nobody ever touched it". A global
switch would be powerless against them. So the artist's preference gets three
states:

    NULL  follow the global
    0     never, whatever the global says
    1     always, whatever the global says

Migration is lossless because today "explicitly on" and "on by default" behave
identically — the difference was never expressible, so nothing is being discarded.
Rows already at 0 keep their deliberate follow-only.

NOTE the deliberate asymmetry with the FORMAT overrides, which stay as they are:
those OVERWRITE each artist at scan time (global trumps user). Only auto-download
uses the default-and-override model, because changing the format flags would alter
behaviour for everyone already relying on them.

Pure: no DB, no config reads.
"""

from __future__ import annotations

from typing import Any, Optional

INHERIT = None
ALWAYS = 1
NEVER = 0


def normalize_pref(value: Any) -> Optional[int]:
    """A stored preference as one of the three states.

    Anything unrecognised becomes INHERIT rather than a guess: a row that cannot
    be read should follow the global, not silently pin itself on."""
    if value is None:
        return INHERIT
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("", "inherit", "none", "null", "default"):
            return INHERIT
        if v in ("1", "true", "yes", "on", "always"):
            return ALWAYS
        if v in ("0", "false", "no", "off", "never"):
            return NEVER
        return INHERIT
    if isinstance(value, bool):
        return ALWAYS if value else NEVER
    try:
        return ALWAYS if int(value) else NEVER
    except (TypeError, ValueError):
        return INHERIT


def effective_auto_download(pref: Any, global_default: Any = True) -> bool:
    """Whether this artist auto-downloads right now.

    The artist wins when it has an opinion; otherwise the global decides. This is
    the single place that answers the question — the scanner, the API and the UI
    all route through it so they cannot drift."""
    state = normalize_pref(pref)
    if state is ALWAYS:
        return True
    if state is NEVER:
        return False
    try:
        return bool(global_default)
    except Exception:      # noqa: BLE001 - a junk global is not a reason to raise
        return True


def effective_with_legacy(pref: Any, legacy: Any = True, global_default: Any = True) -> bool:
    """The same answer, for a row that may predate the preference column.

    ``legacy_column_value`` never writes 0 for an inheriting row, so
    ``pref = NULL`` together with ``auto_download = 0`` can only mean one thing:
    a deliberate follow-only set before the preference existed, on a row the
    backfill did not reach. Honour it rather than letting the global switch it
    back on — silently re-enabling downloads for an artist someone deliberately
    muted is the worst failure this feature can have."""
    if normalize_pref(pref) is INHERIT and not legacy:
        return False
    return effective_auto_download(pref, global_default)


def describe(pref: Any, global_default: Any = True) -> str:
    """What the artist row should say, so "off" is never ambiguous about WHY."""
    state = normalize_pref(pref)
    if state is ALWAYS:
        return "Always downloads (set on this artist)"
    if state is NEVER:
        return "Never downloads (set on this artist)"
    return ("Follows the global setting — currently ON" if global_default
            else "Follows the global setting — currently OFF")


def is_explicit(pref: Any) -> bool:
    """Whether the user has expressed an opinion for this artist. Used to leave
    deliberate choices alone when the global flips."""
    return normalize_pref(pref) is not INHERIT


class _Missing:
    """A field the request did not mention — distinct from one sent as null."""

    def __repr__(self) -> str:      # pragma: no cover - debugging aid
        return "MISSING"


MISSING = _Missing()


def resolve_pref(sent_pref: Any = MISSING, sent_legacy: Any = MISSING,
                 stored: Any = None) -> Optional[int]:
    """The preference a save request means, given what the row holds today.

    Three cases, and the middle one is the whole reason this is a function:

    * ``auto_download_pref`` sent — the client speaks the three-state language,
      so take it verbatim (an explicit ``null`` really does mean "inherit").
    * only the legacy ``auto_download`` boolean sent — an older client, or a
      form post. It has no way to say "inherit", and the user did just move a
      control, so read it as a DELIBERATE choice. Anything else would make the
      old UI's follow-only toggle do nothing whenever the global is on.
    * neither sent — a partial save that never touched auto-download. Keep what
      is stored; in particular do NOT let an untouched row pin itself explicit,
      which would quietly opt it out of every future global flip.

    ``sent_legacy`` must already be parsed to a real bool: the string ``"false"``
    is truthy, and this function is not where that trap gets sprung.
    """
    if sent_pref is not MISSING:
        return normalize_pref(sent_pref)
    if sent_legacy is not MISSING:
        return ALWAYS if sent_legacy else NEVER
    return normalize_pref(stored)


def legacy_column_value(pref: Any) -> int:
    """What to store in the old ``auto_download`` column alongside a preference.

    The column is NOT NULL so it cannot hold "inherit"; the scanner reads the
    preference, not this. It is kept in step purely so an old reader sees the
    explicit choice, and falls back to the column's own default when there is
    no choice to see."""
    state = normalize_pref(pref)
    return 0 if state is NEVER else 1


__all__ = ["effective_auto_download", "effective_with_legacy", "normalize_pref",
           "describe", "is_explicit",
           "resolve_pref", "legacy_column_value", "MISSING",
           "INHERIT", "ALWAYS", "NEVER"]
