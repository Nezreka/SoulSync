"""One answer to "is this artist string one act or several?" (#2150).

Every place that read an artist credit used to answer this on its own: the
legacy import, the ghost-artist healers, the "first listed artist" folder
and tag rule, the Split Artists repair job, and a dozen comparison helpers,
each with its own separator regex and its own guesses. The same bug came back
in each of them in turn ("Above & Beyond" became "Above", "K/DA" became "K",
"Lil Nas X" became "Lil Nas"), and each got its own patch.

This module is the one place. Three layers:

**Syntax.** One tokenizer knows every separator and how sure it is:

- ``;``, ``|`` and a spaced `` / `` (and their full-width forms) propose
  boundaries between credits; stored artist names can contain them too
- ``feat.``, ``ft.``, ``featuring``, ``with``, ``w/``, ``vs.``, ``pres.``,
  ``presents``, ``meets``, a spaced lowercase ``x`` and ``×`` mark guests:
  they cut too ("Lil Nas X" has no spaced lowercase x, "Axwell Λ Ingrosso"
  no separator at all)
- ``,``, ``、``, ``&``, ``and``, ``+`` and an unspaced ``/`` also sit inside
  names ("Earth, Wind & Fire", "Florence + the Machine", "AC/DC"): they
  need evidence

A cut is never taken on trust alone: the whole string is checked before it
(a credit list, a name the caller vouches for, a known act), and every
piece is checked again after it.

``split_names`` splits at all of them, for comparing ("is X one of the names
in this credit"). It never decides who an artist is.

**Evidence.** A track's own credit lists say more than any name. This
module only READS them: the lists a download or match stored
(``lib2_provider_credits``), the answers the credit refresh recorded for the
track's provider ids (``lib2_credit_checks``), the catalogue's credits. When
they are missing, a reader asks the writer for that one track
(``core.library2.provider_credits.ensure_track_credits``) and reads again;
nothing here calls a provider.

**Decision.** ``decide_credit`` walks one chain and stops at the first step
that knows:

1. a credit list holds the string as one artist (whole) or its names make up
   the string (split)
2. before a cut, the whole string is checked: a name a list credited, a
   name the caller vouches for, a known act (a keep-whole name, or
   ``is_act``) stays whole
3. the cutting separators cut it into pieces, and each piece is checked
   again, from the top:
   a. a name a credit list holds, or names it holds that make up the piece
   b. a name the caller vouches for (the album's artist, the track's
      artist) is the piece or stands inside it (split around it), unless the
      piece is the band that artist leads ("Bob Marley & The Wailers")
   c. a known act
   d. names ``is_artist`` knows make up the piece (split)
   e. otherwise it stays whole; in a piece of a proven list, commas split,
      and each part of that goes through a-e again
4. a string with no cutting separator goes through a-e as one piece

Nothing is split on a guess: an unknown "A & B" stays one credit, which a
later match can still split, where a wrong split leaves artists behind that
nothing can merge reliably (P2-24).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("matching.artist_credit")


# ── syntax ───────────────────────────────────────────────────────────────────

_SEP_RE = re.compile(
    r"(?P<hard>\s*[;；|]\s*|\s+[/／]\s+)"
    r"|(?P<marker>\s+(?:feat\.?|ft\.?|featuring|with|w/|vs\.?|pres\.|presents|meets)\s+"
    r"|\s+(?-i:x)\s+|\s*×\s*)"
    r"|(?P<comma>\s*[,，、]\s*)"
    r"|(?P<amp>\s*[&＆]\s*|\s+and\s+|\s*\+\s*)"
    r"|(?P<slash>[/／])",
    re.IGNORECASE,
)
SURE = ("hard", "marker")
_BYTES_REPR_RE = re.compile(r"""^b(['"])(.*)\1$""", re.S)


def unwrap_bytes_repr(text: Any) -> Any:
    """``"b'Above & Beyond'"`` -> ``"Above & Beyond"``: a tag read as bytes
    and turned into text with str(). Anything else comes back as it was."""
    if not isinstance(text, str):
        return text
    match = _BYTES_REPR_RE.match(text.strip())
    return match.group(2) if match else text


def clean_credit(text: Any) -> str:
    """the credit as one line: bytes repr unwrapped, whitespace collapsed"""
    return " ".join(str(unwrap_bytes_repr(text) or "").split())


def name_key(name: Any) -> str:
    """casefold + whitespace collapse, comma spacing canonical. Not for display."""
    text = re.sub(r"\s*,\s*", ", ", clean_credit(name))
    return " ".join(text.casefold().split())


def tokens(text: Any) -> Tuple[List[str], List[Tuple[str, str]]]:
    """(names, separators) of a credit, ``separators[i]`` sitting between
    ``names[i]`` and ``names[i + 1]`` as (kind, raw text). An empty piece
    ("A,, B", a trailing "&") is dropped with the separator before it."""
    text = clean_credit(text)
    names: List[str] = []
    seps: List[Tuple[str, str]] = []
    pos = 0
    pending: Optional[Tuple[str, str]] = None
    for match in _SEP_RE.finditer(text):
        piece = text[pos:match.start()].strip()
        if piece:
            if names and pending is not None:
                seps.append(pending)
            elif names:
                seps.append(("amp", " "))
            names.append(piece)
            pending = None
        if names:
            kind = match.lastgroup or "amp"
            # the stronger separator wins when two meet ("A, & B")
            if pending is None or _strength(kind) > _strength(pending[0]):
                pending = (kind, match.group(0))
        pos = match.end()
    tail = text[pos:].strip()
    if tail:
        if names:
            seps.append(pending or ("amp", " "))
        names.append(tail)
    return names, seps


def _strength(kind: str) -> int:
    return {"hard": 4, "marker": 3, "comma": 2, "amp": 1, "slash": 0}.get(kind, 0)


def split_names(*credits: Any, conjunctions: bool = True,
                known: Optional[Callable[[str], Any]] = None) -> List[str]:
    """every name the credits could hold, split at every separator, in order,
    duplicates dropped. For comparing a name against a credit, never for
    deciding who an artist is (``decide_credit`` does that).

    ``conjunctions=False`` keeps a spelled-out " and " inside a name.
    ``known(name)``: a run of pieces that spells a name it knows stays one
    name, the longest run first ("Above & Beyond, Mat Zo" is two names when
    "Above & Beyond" is known, #2136). No run crosses a sure separator."""
    out: List[str] = []
    seen = set()
    for credit in credits:
        names, seps = tokens(credit)
        if known is not None and len(names) > 1:
            runs = dict(_runs(names, seps, lambda t: _ask(known, t), skip_whole=False,
                              stop_at=SURE))
            pieces, k = [], 0
            while k < len(names):
                end = runs.get(k, k)
                pieces.append(_join(names, seps, k, end))
                k = end + 1
            names, seps = pieces, []
        if not conjunctions and names and seps:
            merged = names[:1]
            for (_kind, raw), name in zip(seps, names[1:], strict=True):
                if raw.strip().casefold() == "and":
                    merged[-1] = merged[-1] + raw + name
                else:
                    merged.append(name)
            names = merged
        for name in names:
            name = name.strip().strip("-").strip()
            key = name_key(name)
            if key and key not in seen:
                seen.add(key)
                out.append(name)
    return out


def _join(names: Sequence[str], seps: Sequence[Tuple[str, str]], i: int, j: int) -> str:
    """the text of names[i..j] with the separators between them"""
    out = names[i]
    for k in range(i, j):
        out += seps[k][1] + names[k + 1]
    return out.strip()


# ── known acts ───────────────────────────────────────────────────────────────

# There is no built-in list of band names. A name is one act because data says
# so: a provider credited it whole on the track, the library holds it as an
# identified artist (``is_act``), or the user said so (Keep Whole Names).

# the names the user said are one artist each (#2150). They live in the
# Split Artists job's settings, so the list can be read and edited on the
# Tools page; a finding's "One artist" and "Edit split" add to it.
KEPT_WHOLE_SETTINGS_KEY = "repair.jobs.comma_artist_splitter.settings"
KEPT_WHOLE_SETTING = "keep_whole_names"


def parse_kept_whole_names(value: Any) -> List[str]:
    """the names in a keep-whole setting, one per ';' or line. A name with
    ',' '&' or '/' in it is the point, so those never split here."""
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        items = re.split(r"[;\n]", str(value or ""))
    out, seen = [], set()
    for item in items:
        name = " ".join(item.split())
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def kept_whole_artist_names(config: Any = None) -> List[str]:
    """the names the user said are one artist each. [] when unreadable."""
    try:
        if config is None:
            from core.settings import config_manager as config
        settings = config.get(KEPT_WHOLE_SETTINGS_KEY, {}) or {}
        if not isinstance(settings, dict):
            return []
        return parse_kept_whole_names(settings.get(KEPT_WHOLE_SETTING))
    except Exception as exc:  # noqa: BLE001 - an unreadable setting keeps nothing
        logger.debug("keep-whole names unreadable: %s", exc)
        return []


def remember_kept_whole_names(names: Iterable[Any], config: Any = None) -> List[str]:
    """add ``names`` to the keep-whole list. Returns the list as saved."""
    if config is None:
        from core.settings import config_manager as config
    settings = config.get(KEPT_WHOLE_SETTINGS_KEY, {}) or {}
    settings = dict(settings) if isinstance(settings, dict) else {}
    merged = parse_kept_whole_names(
        parse_kept_whole_names(settings.get(KEPT_WHOLE_SETTING)) + list(names or ()))
    settings[KEPT_WHOLE_SETTING] = "; ".join(merged)
    config.set(KEPT_WHOLE_SETTINGS_KEY, settings)
    return merged


def kept_whole_keys(config: Any = None) -> set:
    """the keep-whole names as ``name_key`` keys"""
    return {name_key(name) for name in kept_whole_artist_names(config)}


def known_act_keys(config: Any = None) -> frozenset:
    """every name that is one act without asking a provider or the library:
    the user's keep-whole names"""
    return frozenset(kept_whole_keys(config))


# "<frontman> & The <band>": the band an artist leads, filed on that
# artist's own album. It carries the artist as a part and is one act.
_FRONTMAN_BAND_RE = re.compile(
    r"^\s*(?P<lead>.+?)\s*(?:&|\band\b|\+)\s*(?P<band>the\s+\S.*)$", re.IGNORECASE)


def leads_a_band(name: Any, anchors: Iterable[str]) -> bool:
    """True for "<one of ``anchors``> & The <band>" without a sure separator."""
    names, seps = tokens(name)
    if any(kind in SURE for kind, _raw in seps):
        return False  # "A & The B feat. C" says it's a list
    match = _FRONTMAN_BAND_RE.match(clean_credit(name))
    return bool(match) and name_key(match.group("lead")) in {name_key(a) for a in anchors}


# ── evidence ─────────────────────────────────────────────────────────────────

Evidence = Tuple[str, Sequence[Any]]          # (via, names)


def _names_of(items: Iterable[Any]) -> List[str]:
    out = []
    for item in items or ():
        name = item.get("name") if isinstance(item, dict) else item
        name = clean_credit(name)
        if name:
            out.append(name)
    return out


def credits_cover(name: Any, credits: Iterable[Any]) -> Optional[List[str]]:
    """``credits`` in the order ``name`` lists them, when together they are
    all of ``name`` with only separators between; None otherwise. A single
    credit splits nothing, and neither does a list that leaves part of the
    string uncredited."""
    text = name_key(name)
    original = text
    found = []
    for credit in sorted({c for c in _names_of(credits)}, key=len, reverse=True):
        key = name_key(credit)
        at = text.find(key)
        if at < 0:
            return None
        found.append((at, credit))
        text = text[:at] + "\0" * len(key) + text[at + len(key):]
    if len(found) < 2 or _SEP_RE.sub("", text.replace("\0", "")).strip():
        return None
    ordered = sorted(found)
    for (at, credit), (next_at, _next_credit) in zip(ordered, ordered[1:], strict=False):
        gap = original[at + len(name_key(credit)):next_at]
        if not _SEP_RE.search(gap) or _SEP_RE.sub("", gap).strip():
            return None
    return [credit for _at, credit in ordered]


def credit_verdict(name: Any, evidence: Iterable[Evidence]) -> Optional[Tuple[str, List[str], str]]:
    """("whole" | "split", names, via) from the first list that decides, or
    None. ``evidence`` is (via, names) pairs, most trusted first, and may be
    lazy: nothing after the deciding list is asked. A via ending in
    ":split" only counts as a split (the catalogue holding the string as one
    artist is the dummy itself, not proof of an act)."""
    key = name_key(name)
    if not key:
        return None
    for via, names in evidence:
        split_only = via.endswith(":split")
        via = via[:-len(":split")] if split_only else via
        names = _names_of(names)
        if not split_only and any(name_key(n) == key for n in names):
            return "whole", [clean_credit(name)], via
        parts = credits_cover(name, names)
        if parts:
            return "split", parts, via
    return None


STORED_SOURCES = ("spotify", "deezer", "musicbrainz", "tidal", "discogs", "jiosaavn")


def stored_track_credits(conn: Any, track_id: Any) -> List[Evidence]:
    """What the library knows about a track's credits, most trusted first:
    each stored provider list (Spotify first), the one-artist answers the
    credit refresh recorded for the track's provider ids, and last the
    catalogue's own credits as split-only evidence. Never raises."""
    lists: List[Evidence] = []
    try:
        by_source: dict = {}
        for source, credit in conn.execute(
                "SELECT source, name FROM lib2_provider_credits "
                "WHERE entity_type='track' AND entity_id=? ORDER BY source, position",
                (int(track_id),)).fetchall():
            by_source.setdefault(str(source), []).append(credit)
        order = sorted(by_source, key=lambda s: STORED_SOURCES.index(s)
                       if s in STORED_SOURCES else len(STORED_SOURCES))
        lists.extend((f"{s} credits", by_source[s]) for s in order)
    except Exception as exc:  # noqa: BLE001 - an old schema has no snapshot
        logger.debug("no stored provider credits for track %s: %s", track_id, exc)
    try:
        from core.library2.provider_credits import checked_track_credits
        lists.extend((f"{s} credits", names) for s, names in checked_track_credits(conn, track_id)
                     if f"{s} credits" not in {via for via, _n in lists})
    except Exception as exc:  # noqa: BLE001
        logger.debug("no credit checks for track %s: %s", track_id, exc)
    try:
        lists.append(("catalogue:split", [r[0] for r in conn.execute(
            "SELECT ar.name FROM lib2_track_artists ta "
            "JOIN lib2_artists ar ON ar.id = ta.artist_id "
            "WHERE ta.track_id = ? ORDER BY ta.position, ta.artist_id",
            (int(track_id),)).fetchall()]))
    except Exception as exc:  # noqa: BLE001
        logger.debug("no catalogue credits for track %s: %s", track_id, exc)
    return lists


# ── decision ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CreditDecision:
    """the artists a credit names, and which step decided it"""
    names: Tuple[str, ...]
    via: str

    @property
    def split(self) -> bool:
        return len(self.names) > 1

    @property
    def primary(self) -> str:
        return self.names[0] if self.names else ""


def _ask(check: Optional[Callable[[str], Any]], name: str) -> bool:
    if check is None or not name:
        return False
    try:
        return bool(check(name))
    except Exception:  # noqa: BLE001 - a lookup never decides by failing
        return False


def _runs(names: List[str], seps: List[Tuple[str, str]], known: Callable[[str], bool],
          skip_whole: bool = True, stop_at: Sequence[str] = ()) -> List[Tuple[int, int]]:
    """the longest runs of names (left to right) whose text ``known`` holds.
    A run never crosses a separator whose kind is in ``stop_at``."""
    found = []
    i, n = 0, len(names)
    while i < n:
        hit = None
        last = i
        while last + 1 < n and seps[last][0] not in stop_at:
            last += 1
        for j in range(last, i - 1, -1):
            if skip_whole and i == 0 and j == n - 1:
                continue
            if known(_join(names, seps, i, j)):
                hit = j
                break
        if hit is None:
            i += 1
        else:
            found.append((i, hit))
            i = hit + 1
    return found


def _pieces(names, seps, runs) -> List[Tuple[str, bool]]:
    """the segment cut at the runs: (text, is_a_run), the gaps kept whole"""
    out: List[Tuple[str, bool]] = []
    pos = 0
    for i, j in runs:
        if i > pos:
            out.append((_join(names, seps, pos, i - 1), False))
        out.append((_join(names, seps, i, j), True))
        pos = j + 1
    if pos < len(names):
        out.append((_join(names, seps, pos, len(names) - 1), False))
    return out


class _Segment:
    def __init__(self, known_keys, is_act, is_artist, min_runs, credited=None, acts=frozenset()):
        # the user's keep-whole names, read once
        self.acts = acts
        # every name a provider credited on this track, by key -> the list
        # that credited it: each is an artist as written, so a segment cut
        # out of a longer credit is checked against them again
        self.credited = credited or {}
        self.known_keys = known_keys
        self.is_act = is_act
        self.is_artist = is_artist
        self.min_runs = min_runs

    def act(self, text: str) -> bool:
        key = name_key(text)
        return key in self.acts or _ask(self.is_act, text)

    def decide(self, text: str, in_list: bool) -> Tuple[List[str], str]:
        names, seps = tokens(text)
        if len(names) < 2:
            return [clean_credit(text)], "single"
        key = name_key(text)
        # 1b. after a cut: a name a provider credits on this track is an
        # artist as written, and names it credits make up the segment
        if key in self.credited:
            return [clean_credit(text)], self.credited[key]
        runs = _runs(names, seps, lambda t: name_key(t) in self.credited)
        if runs and sum(j - i + 1 for i, j in runs) == len(names):
            return [_join(names, seps, i, j) for i, j in runs], self.credited[
                name_key(_join(names, seps, *runs[0]))]
        # 3. a name the caller vouches for, or the user keeps whole
        if key in self.known_keys or key in self.acts:
            return [clean_credit(text)], "known" if key in self.known_keys else "act"
        runs = _runs(names, seps, lambda t: name_key(t) in self.known_keys)
        # Syntax proposes boundaries; stored identity spans veto boundaries
        # inside an artist. The very same decision function handles the whole
        # credit and every smaller piece, with identical evidence precedence.
        if any(kind in SURE for kind, _raw in seps):
            # a ";", "|" or " / " is never inside an act's name: across one,
            # only a credit list holds a name together (the spans below)
            if (not runs and not any(kind == "hard" for kind, _raw in seps)
                    and self.act(text)):
                return [clean_credit(text)], "act"
            protected = set()
            spans = _runs(names, seps, lambda t: name_key(t) in self.credited
                          or name_key(t) in self.known_keys or name_key(t) in self.acts)
            for i, j in spans:
                protected.update(range(i, j))
            pieces, start = [], 0
            for k, (kind, _raw) in enumerate(seps):
                if kind in SURE and k not in protected:
                    pieces.append(_join(names, seps, start, k))
                    start = k + 1
            pieces.append(_join(names, seps, start, len(names) - 1))
            if len(pieces) > 1:
                out, vias = [], []
                for piece in pieces:
                    found, via = self.decide(piece, True)
                    out.extend(found)
                    vias.append(via)
                deciding = [v for v in vias if v not in ("single", "whole", "act")]
                return out, deciding[0] if deciding else "structure"
        if runs:
            anchors = [_join(names, seps, i, j) for i, j in runs]
            if not leads_a_band(text, anchors):
                return self._cut(names, seps, runs), "known"
            return [clean_credit(text)], "band"
        # 4. a known act
        if self.act(text):
            return [clean_credit(text)], "act"
        # 5. names the library or the caller knows make it up; a keep-whole
        # name is an artist for this too
        if self.is_artist is not None or self.acts:
            runs = _runs(names, seps, lambda t: name_key(t) in self.acts
                         or _ask(self.is_artist, t))
            covered = sum(j - i + 1 for i, j in runs) == len(names)
            enough = covered if self.min_runs is None else len(runs) >= self.min_runs
            if runs and (covered or enough) and (len(runs) >= 2 or not covered):
                return self._cut(names, seps, runs), "artist"
        # 6. whole, or the commas of a proven list
        if in_list and any(kind == "comma" for kind, _raw in seps):
            out = []
            start = 0
            for k, (kind, _raw) in enumerate(seps):
                if kind == "comma":
                    out.append(_join(names, seps, start, k))
                    start = k + 1
            out.append(_join(names, seps, start, len(names) - 1))
            return [p for piece in out for p in self.decide(piece, False)[0]], "list"
        return [clean_credit(text)], "whole"

    def _cut(self, names, seps, runs) -> List[str]:
        """the runs as artists; what lies between them is a proven list's
        rest, so its commas split and the rest of the chain decides each piece"""
        out = []
        for piece, is_run in _pieces(names, seps, runs):
            out.extend([piece] if is_run else self.decide(piece, True)[0])
        return out


def decide_credit(credit: Any, *, evidence: Iterable[Evidence] = (),
                  known: Iterable[Any] = (), is_act: Optional[Callable[[str], Any]] = None,
                  is_artist: Optional[Callable[[str], Any]] = None,
                  min_runs: Optional[int] = None, config: Any = None) -> CreditDecision:
    """the artists ``credit`` names, by the chain in the module docstring.

    - ``evidence``: credit lists, most trusted first, may be lazy
    - ``known``: names this context vouches for (album artist, track artist,
      the artist a download is for, a provider's partial list)
    - ``is_act(name)``: is this whole name one act (library, provider)
    - ``is_artist(name)``: is this a known artist, for the parts
    - ``min_runs``: parts ``is_artist`` must know to split; None means they
      must make up the whole segment
    - ``config``: where the user's keep-whole names are read (default the
      app's settings)
    """
    text = clean_credit(credit)
    if not text:
        return CreditDecision((), "empty")
    names, seps = tokens(text)
    if len(names) < 2:
        return CreditDecision((text,), "single")
    # 1. a credit list. Every list read on the way is kept: when none
    # decides the whole string, its names still decide the pieces (1b)
    read: List[Evidence] = []

    def remember(lists):
        for entry in lists:
            read.append(entry)
            yield entry
    verdict = credit_verdict(text, remember(evidence))
    if verdict is not None:
        return CreditDecision(tuple(verdict[1]), verdict[2])
    credited: dict = {}
    for via, names_ in read:
        if via.endswith(":split"):
            continue
        for name in _names_of(names_):
            credited.setdefault(name_key(name), via)
    known_keys = {name_key(n) for n in _names_of(known)} - {""}
    segment = _Segment(known_keys, is_act, is_artist, min_runs, credited, known_act_keys(config))
    found, via = segment.decide(text, in_list=False)
    return CreditDecision(_dedupe(found), via)


def lookup_name(credit: Any, **knowledge: Any) -> str:
    """the one name to search a provider by: the lead ``decide_credit``
    finds, an act whose name reads as a list kept whole ("Above & Beyond"),
    and for a list nobody can read ("Armin van Buuren, Funk Tribu") its first
    listed name, since no provider has a page for the joined string (#1178).
    For lookups only; it decides nobody's identity."""
    decision = decide_credit(credit, **knowledge)
    if decision.split or decision.via in ("act", "single", "known", "band"):
        return decision.primary
    names, seps = tokens(credit)
    for k, (kind, _raw) in enumerate(seps):
        if kind in ("comma", "hard", "marker"):
            return _join(names, seps, 0, k)
    return decision.primary


def _dedupe(names: Iterable[str]) -> Tuple[str, ...]:
    out, seen = [], set()
    for name in names:
        key = name_key(name)
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return tuple(out)


__all__ = [
    "CreditDecision", "Evidence", "KEPT_WHOLE_SETTING", "KEPT_WHOLE_SETTINGS_KEY",
    "STORED_SOURCES", "clean_credit", "kept_whole_artist_names",
    "kept_whole_keys", "known_act_keys", "parse_kept_whole_names",
    "remember_kept_whole_names",
    "credit_verdict", "credits_cover", "decide_credit", "leads_a_band", "lookup_name", "name_key",
    "split_names", "stored_track_credits", "tokens", "unwrap_bytes_repr",
]
