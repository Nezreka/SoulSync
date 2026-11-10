"""The version words in a track title: what makes "Song (Live)" a different
recording from "Song".

Two lists, for two questions. ``RECORDING`` is the narrow one: words that
name another recording (a live take, a remix, a radio edit). A remaster or
an explicit/clean tag is the same recording and stays out, so a catalogue
spelling "Song - Remastered 2009" still meets a download called "Song".
``DEDUP_3_6_1`` is the list 3.6.1's Single/Album Dedup refused to pair on
(core/repair_jobs/single_album_dedup.py at 18a85ee31^1), kept as it was so
the duplicate detector that replaced it pairs exactly what it paired.
"""

from __future__ import annotations

import re
from typing import FrozenSet, Pattern

RECORDING: Pattern[str] = re.compile(
    r"\b(live|acoustic|unplugged|remix|remixed|rmx|demo|instrumental|karaoke|"
    r"radio edit|radio version|single edit|album edit|extended|"
    r"a\s?cappella|sped up|slowed|reverb|club mix|dub mix|vip mix)\b",
    re.IGNORECASE,
)

DEDUP_3_6_1: Pattern[str] = re.compile(
    r"\b(live|acoustic|remix|remixed|demo|instrumental|radio edit|extended|karaoke|"
    r"a\s?cappella|remaster(?:ed)?|deluxe|bonus|stripped|unplugged|orchestral|"
    r"sped up|slowed|reverb|clean|explicit|mono|stereo|alternate|alt\.?\s*(?:version|mix)|"
    r"club mix|dub mix|vip mix|edit)\b",
    re.IGNORECASE,
)


def version_words(title: str, pattern: Pattern[str] = RECORDING) -> FrozenSet[str]:
    """the version words ``title`` carries, lower-cased, spaces folded"""
    return frozenset(" ".join(m.group(1).lower().split()) for m in pattern.finditer(title or ""))


def versions_differ(one: str, two: str, pattern: Pattern[str] = RECORDING) -> bool:
    """the two titles name different versions of a song"""
    return version_words(one, pattern) != version_words(two, pattern)
