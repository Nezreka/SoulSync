"""The track-number prefix a ripped filename carries ahead of its title.

Soulseek and rip filenames put the track's position in front of the title
in a handful of shapes:

    "01 - Title"      "01. Title"    "01_Title"      "01 Title"
    "1-05 - Title"    "2.03 Title"   (disc-track)
    "0113 - Title"    (disc 1, track 13, packed)
    "A1 - Title"      "B12 Title"    (vinyl side)
    "(01) Title"      "[01] Title"
    "Track 01 - Title"

The matching engine only needs the prefix gone; the Soulseek identity
matcher needs the number and disc it encodes. Both read it from here so a
shape learned once is recognized everywhere. Pure, no I/O.

A bare number with nothing after it ("22", "1979") is a title, not a
prefix, so a separator or whitespace is required before the remainder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_PREFIX = re.compile(
    r"""
    ^\s*
    (?:
        (?P<side>[a-d])(?P<side_number>\d{1,2})
      | (?P<disc>\d{1,2})[-._](?P<disc_number>\d{1,2})
      | (?P<packed_disc>0[1-9])(?P<packed_number>\d{2})
      | [(\[]\s*(?P<bracketed_number>\d{1,3})\s*[)\]]
      | (?:track\s*)?(?P<number>\d{1,3})
    )
    (?:\s*[-._)\]]+\s*|\s+)
    (?=\S)
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class TrackPrefix:
    remainder: str
    number: int | None = None
    disc: int | None = None
    side: str | None = None


def parse_track_prefix(stem: str) -> TrackPrefix:
    """Split ``stem`` into its leading track position and the remainder.

    ``stem`` is a basename without extension. When no prefix is recognized
    the remainder is ``stem`` unchanged and every field is ``None``.
    """
    text = str(stem or "")
    found = _PREFIX.match(text)
    if not found:
        return TrackPrefix(text)
    remainder = text[found.end():]
    if found.group("side"):
        return TrackPrefix(remainder, int(found.group("side_number")),
                           side=found.group("side").upper())
    if found.group("disc"):
        return TrackPrefix(remainder, int(found.group("disc_number")),
                           disc=int(found.group("disc")))
    if found.group("packed_disc"):
        return TrackPrefix(remainder, int(found.group("packed_number")),
                           disc=int(found.group("packed_disc")))
    number = found.group("bracketed_number") or found.group("number")
    return TrackPrefix(remainder, int(number))


def strip_track_prefix(stem: str) -> str:
    """``stem`` without its leading track position."""
    return parse_track_prefix(stem).remainder
