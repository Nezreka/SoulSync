"""Guard: whose intent a read shows.

The ``monitored`` column on lib2_tracks / lib2_albums / lib2_artists is the
SHARED library's intent (the admin's). Every other profile's intent lives in
lib2_monitor_rules for that profile, read through ``sql_util.monitored_sql``.
Five reviews of Library v2 kept finding reads that showed a household member
or an own library the admin's flags (review 5: R5-B-1, B-2, B-3, X1-1).

This test reads every SQL string in the code and fails when:

* a module that answers a profile (routes, read models) reads the raw column,
  outside the reasoned exceptions below; or
* a background module gains a raw read it didn't have (a ratchet: lower the
  number when one goes, and say why when one is added).

A write (``UPDATE ... SET monitored``) is not a read and isn't counted: the
shared column is written for the shared library by design.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_ALIASED = re.compile(r"\b(\w+)\.monitored\b")
_BARE = re.compile(r"(?<![\.\w])monitored\b")
_CATALOGUE = re.compile(r"FROM\s+lib2_(?:tracks|albums|artists)\b")
# aliases that name a rule or projection row, not a catalogue row
_RULE_ALIASES = {"mr", "r", "r2", "r3", "r4", "rule", "rules", "w", "wt", "lr",
                 "mr2", "rr", "excluded", "e", "tr", "ab", "aa"}

# modules whose reads answer a request for one profile
PROFILE_FACING = sorted(
    [str(p.relative_to(ROOT)) for p in (ROOT / "api").rglob("*.py")]
    + [f"core/library2/{name}.py" for name in (
        "queries", "wanted_views", "artist_reader", "artist_page", "history_feed",
        "artist_settings", "match_status", "unmatched_review")]
    + [str(p.relative_to(ROOT)) for p in (ROOT / "core" / "wishlist").glob("*.py")])

# a profile-facing raw read that is right: (module, a snippet of its line)
PROFILE_FACING_EXCEPTIONS = {
    # the admin branch of _row_select: profile 1's intent IS the column
    ("core/library2/wanted_views.py", 'monitored = "t.monitored"'),
}

# background modules: the shared library's own logic. counts, not exceptions
# (the four marked "split literal" were always there: their SQL is split over
# adjacent string literals, which the token reader saw one piece at a time)
BACKGROUND_BASELINE = {
    "core/library2/artist_rollup.py": 2,
    # artwork precache: covers of what the shared library owns or wants (split literal)
    "core/library2/artwork.py": 1,
    # +1: a new tracklist row takes its album's shared flag (split literal)
    "core/library2/completeness.py": 3,
    "core/library2/dedup_repair.py": 1,
    # +1: the shared library's re-expansion follows the shared artist row;
    # members' new releases come from their own watchlist scan (split literal)
    "core/library2/discography.py": 5,
    "core/library2/importer.py": 3,
    "core/library2/maintenance_subjects.py": 5,
    "core/library2/mb_reconcile.py": 1,
    # selects the album's row, inserts the new slot unmonitored (split literal)
    "core/library2/missing_tracks.py": 1,
    "core/library2/monitor_sync.py": 1,
    "core/library2/native_enrich.py": 10,
    # the projection itself: the shared flag is one of its inputs
    "core/library2/wanted.py": 2,
}


def _sql_strings(src: str):
    """(line number, text) of every string literal in ``src``. an f-string is
    read whole, each ``{expr}`` standing as ``{}``: the tokenizer splits
    f-strings at their braces from python 3.12 and keeps them whole before,
    so reading tokens checked different text on 3.11 (ci) and 3.12."""
    tree = ast.parse(src)
    inside_fstring = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            inside_fstring.update(id(v) for v in node.values)
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    inside_fstring.update(id(n) for n in ast.walk(v))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield node.lineno, "".join(
                v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in inside_fstring):
            yield node.lineno, node.value


def _raw_reads(path: Path):
    """(line number, source line) of each raw catalogue ``monitored`` read in
    a SQL string of ``path``."""
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    found = []
    for line_no, text in _sql_strings(src):
        if "monitored" not in text:
            continue
        upper = text.upper()
        if "UPDATE" in upper and "SELECT" not in upper:
            continue
        hits = [m for m in _ALIASED.finditer(text) if m.group(1) not in _RULE_ALIASES]
        # "<expr> AS monitored" names a computed column (monitored_sql's
        # answer), it doesn't read the shared one
        bare = [m for m in _BARE.finditer(text)
                if not re.search(r"\bAS\s+$", text[:m.start()], re.I)]
        if not hits and "SELECT" in upper and _CATALOGUE.search(text) and bare:
            hits = [None]
        for _ in hits:
            found.append((line_no, lines[line_no - 1].strip()))
    return found


def test_a_profile_facing_read_never_shows_the_shared_column():
    leaks = []
    for rel in PROFILE_FACING:
        path = ROOT / rel
        if not path.exists():
            continue
        for line_no, line in _raw_reads(path):
            if any(rel == mod and snippet in line for mod, snippet in PROFILE_FACING_EXCEPTIONS):
                continue
            leaks.append(f"{rel}:{line_no}: {line}")
    assert not leaks, (
        "these reads show every profile the shared library's (the admin's) "
        "monitored flag; use sql_util.monitored_sql(...) for the caller's "
        "intent, or add a reasoned exception:\n" + "\n".join(leaks))


def test_background_raw_reads_only_ever_go_down():
    counts = {}
    for path in sorted((ROOT / "core" / "library2").glob("*.py")):
        rel = str(path.relative_to(ROOT))
        if rel in PROFILE_FACING:
            continue
        n = len(_raw_reads(path))
        if n:
            counts[rel] = n
    grew = {rel: (n, BACKGROUND_BASELINE.get(rel, 0)) for rel, n in counts.items()
            if n > BACKGROUND_BASELINE.get(rel, 0)}
    assert not grew, (
        "new raw reads of the shared monitored column (now, baseline). if it "
        "is the shared library's own logic, raise the baseline with a reason; "
        "if a profile sees it, use monitored_sql:\n" + repr(grew))


def test_the_guard_sees_a_raw_read(tmp_path):
    """negative control: the scanner must catch the shapes it exists for"""
    probe = tmp_path / "probe.py"
    probe.write_text(
        'A = "SELECT t.id, t.monitored FROM lib2_tracks t"\n'
        'B = f"SELECT id, monitored FROM lib2_albums WHERE id={1}"\n'
        'C = "UPDATE lib2_tracks SET monitored=1 WHERE id=?"\n'
        'D = "SELECT mr.monitored FROM lib2_monitor_rules mr"\n',
        encoding="utf-8")
    assert [n for n, _ in _raw_reads(probe)] == [1, 2]
