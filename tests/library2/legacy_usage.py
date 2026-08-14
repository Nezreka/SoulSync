"""Count legacy-table access in production Python.

Backs the ratchet in ``test_legacy_usage_ratchet.py``. Kept beside it rather
than in ``core/`` because it measures the codebase; it is not part of the app.

The definition of a "site" is deliberately mechanical, because a ratchet is
only useful if the number is reproducible: one SQL keyword pairing per match.
It undercounts dynamically built SQL and f-string table names, and it does not
try to guess. What it must never do is drift, so that a change in the figure
always means a change in the code.
"""

from __future__ import annotations

import io
import pathlib
import re
import tokenize
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

LEGACY_TABLES: Tuple[str, ...] = ("artists", "albums", "tracks")

_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Where production code lives. ``tests`` is excluded on purpose: a test may
# legitimately build a legacy fixture table for as long as legacy exists.
_TREES: Tuple[str, ...] = ("core", "database", "api", "utils", "services")
_ROOT_FILES: Tuple[str, ...] = ("web_server.py", "dev.py")
UPGRADE_ONLY_FILES: Tuple[str, ...] = ("core/library2/importer.py",)

_SKIP_PARTS = frozenset({
    "__pycache__", ".venv", "node_modules", "webui", "tests", "docs",
})

_TABLES = "|".join(LEGACY_TABLES)
# `(?<![\w.])` keeps `lib2_artists` and `self.artists` out; `\b` at the end
# keeps `artists_new` out. Both matter: the first is the whole point, and the
# second is a migration scratch table that would otherwise inflate the figure.
#
# The KEYWORD is matched case-sensitively, the table name is not. Every SQL
# statement in this tree writes its keywords in upper case, while English does
# not: "new releases from artists you follow" is a playlist description, "get
# artist image from albums failed" is a log line. Three such sentences were
# being counted as legacy reads. The trade-off is stated rather than hidden —
# lower-case SQL would escape this counter, and that is the less likely
# mistake by a wide margin.
_WRITE = re.compile(
    rf"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+"
    rf"(?<![\w.])(?i:{_TABLES})\b")
_READ = re.compile(
    rf"\b(?:FROM|JOIN)\s+(?<![\w.])(?i:{_TABLES})\b")


@dataclass(frozen=True)
class Usage:
    reads: int = 0
    writes: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.reads + other.reads, self.writes + other.writes)

    def __bool__(self) -> bool:
        return bool(self.reads or self.writes)


@dataclass(frozen=True)
class TreeUsage:
    total: Usage
    by_file: Dict[str, Usage]
    upgrade_total: Usage
    upgrade_by_file: Dict[str, Usage]


def _without_comments(source: str) -> str:
    """The same source with ``#`` comments removed.

    English prose is full of "from tracks" and "from artists" — four such
    comments were being counted as legacy reads, which would have left the
    ratchet unable to reach zero however much code was ported. Tokenizing is
    the only way to tell a comment from a ``#`` inside a SQL string; source
    that does not tokenize (there is none, but a syntax error must not take
    the measurement down with it) is measured as-is.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return source
    return tokenize.untokenize(
        [token for token in tokens if token.type != tokenize.COMMENT])


def count_legacy_usage(source: str) -> Usage:
    """Reads and writes in one blob of source.

    Writes are matched first and their spans withheld from the read pass:
    ``DELETE FROM tracks`` contains ``FROM tracks``, and counting it as both
    would let a write be moved around without the write figure changing.
    """
    source = _without_comments(source)
    write_spans = [match.span() for match in _WRITE.finditer(source)]
    reads = 0
    for match in _READ.finditer(source):
        start, end = match.span()
        if any(w_start <= start and end <= w_end for w_start, w_end in write_spans):
            continue
        reads += 1
    return Usage(reads=reads, writes=len(write_spans))


def _production_files() -> Iterable[pathlib.Path]:
    for name in _ROOT_FILES:
        path = _ROOT / name
        if path.is_file():
            yield path
    for tree in _TREES:
        base = _ROOT / tree
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if _SKIP_PARTS.isdisjoint(path.parts):
                yield path


def scan_production_tree() -> TreeUsage:
    total = Usage()
    upgrade_total = Usage()
    by_file: Dict[str, Usage] = {}
    upgrade_by_file: Dict[str, Usage] = {}
    for path in _production_files():
        usage = count_legacy_usage(path.read_text(encoding="utf-8", errors="replace"))
        if usage:
            relative = str(path.relative_to(_ROOT))
            if relative in UPGRADE_ONLY_FILES:
                upgrade_by_file[relative] = usage
                upgrade_total = upgrade_total + usage
            else:
                by_file[relative] = usage
                total = total + usage
    return TreeUsage(total, by_file, upgrade_total, upgrade_by_file)


__all__ = [
    "LEGACY_TABLES", "UPGRADE_ONLY_FILES", "TreeUsage", "Usage", "count_legacy_usage",
    "scan_production_tree",
]
