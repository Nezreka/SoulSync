"""A ratchet on legacy-table usage in production code (docs §50.4.4).

docs §32.3.1 commits to one library: everything reads and writes ``lib2_*`` and
the legacy tables disappear. That happens over three stages, in a lot of files,
across more than one PR. Nothing in the test suite notices a new
``UPDATE artists SET …`` appearing in the meantime, and a half-migrated
codebase is exactly where one would slip in unnoticed.

So the count is pinned. It may only ever go down. When it does, the baseline
comes down with it in the same commit, which turns the number into the progress
bar for the whole legacy removal: reads and writes both to zero.

Reads and writes are counted separately on purpose. Stage 2 moves the
producers, stage 3 the readers, so a single combined figure would barely move
during stage 2 and tell nobody anything.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.library2.legacy_usage import (
    LEGACY_TABLES, count_legacy_usage, scan_production_tree,
)

_BASELINE = pathlib.Path(__file__).with_name("legacy_usage_baseline.json")


class TestTheCounter:
    def test_reads_and_writes_are_counted_separately(self):
        usage = count_legacy_usage(
            "cur.execute('SELECT name FROM artists WHERE id=?')\n"
            "cur.execute('UPDATE artists SET genres=? WHERE id=?')\n"
        )
        assert usage.reads == 1
        assert usage.writes == 1

    def test_a_delete_is_a_write_not_a_read(self):
        """``DELETE FROM artists`` contains ``FROM artists``; counting it twice
        would make the write ratchet unfalsifiable by moving a delete around."""
        usage = count_legacy_usage("cur.execute('DELETE FROM tracks WHERE id=?')")
        assert usage.writes == 1
        assert usage.reads == 0

    def test_lib2_tables_are_not_legacy(self):
        usage = count_legacy_usage(
            "cur.execute('SELECT id FROM lib2_artists')\n"
            "cur.execute('UPDATE lib2_tracks SET bpm=?')\n"
        )
        assert (usage.reads, usage.writes) == (0, 0)

    def test_joins_and_inserts_of_every_legacy_table_are_seen(self):
        usage = count_legacy_usage(
            "'SELECT 1 FROM x JOIN albums a ON a.id=x.album_id'\n"
            "'INSERT OR REPLACE INTO artists (id) VALUES (?)'\n"
            "'INSERT INTO tracks (id) VALUES (?)'\n"
        )
        assert usage.reads == 1
        assert usage.writes == 2

    def test_a_table_whose_name_merely_starts_the_same_is_not_counted(self):
        """``artists_new`` is a migration scratch table, not the legacy one."""
        usage = count_legacy_usage("'INSERT INTO artists_new (id) VALUES (?)'")
        assert usage.writes == 0

    def test_prose_in_a_comment_is_not_access(self):
        """"Get artist from tracks" is a sentence, not a query. Four such
        comments were being counted, which would have left the ratchet stuck
        four above zero with no code left to port."""
        usage = count_legacy_usage(
            "# Try to determine artist from tracks or path\n"
            "artist = self._determine_album_artist(tracks, path)\n"
        )
        assert (usage.reads, usage.writes) == (0, 0)

    def test_prose_in_a_string_is_not_access_either(self):
        """A playlist description and a log line, both real: the keyword has
        to be upper case, which SQL in this tree always is and English never
        is. The cost is stated in the module: lower-case SQL escapes the
        count."""
        usage = count_legacy_usage(
            "SPEC = dict(description='new releases from artists you follow')\n"
            "logger.debug('get artist image from albums failed: %s', e)\n"
        )
        assert (usage.reads, usage.writes) == (0, 0)

    def test_the_table_name_itself_is_still_case_insensitive(self):
        usage = count_legacy_usage("cur.execute('SELECT id FROM Tracks')")
        assert usage.reads == 1

    def test_a_hash_inside_sql_does_not_hide_the_rest_of_the_statement(self):
        """The comment strip must be tokenizer-accurate: a ``#`` inside a
        string is data, and everything after it still counts."""
        usage = count_legacy_usage(
            "cur.execute('SELECT id FROM artists WHERE name = \"#1 Hits\"')\n"
        )
        assert usage.reads == 1

    def test_source_that_does_not_tokenize_is_still_measured(self):
        usage = count_legacy_usage("cur.execute('DELETE FROM tracks WHERE (')\n")
        assert usage.writes == 1

    def test_every_legacy_table_is_actually_watched(self):
        for table in LEGACY_TABLES:
            usage = count_legacy_usage(f"'UPDATE {table} SET x=1'")
            assert usage.writes == 1, table


class TestTheRatchet:
    """The measured state of the tree, pinned."""

    @pytest.fixture(scope="class")
    def measured(self):
        return scan_production_tree()

    def test_write_sites_never_grow(self, measured):
        baseline = json.loads(_BASELINE.read_text())
        assert measured.total.writes == baseline["writes"], _explain(
            "writes", measured, baseline)

    def test_read_sites_never_grow(self, measured):
        baseline = json.loads(_BASELINE.read_text())
        assert measured.total.reads == baseline["reads"], _explain(
            "reads", measured, baseline)


def _explain(kind, measured, baseline) -> str:
    current = getattr(measured.total, kind)
    expected = baseline[kind]
    worst = sorted(
        ((getattr(usage, kind), path) for path, usage in measured.by_file.items()
         if getattr(usage, kind)),
        reverse=True)[:8]
    listing = "\n".join(f"    {count:>4}  {path}" for count, path in worst)
    if current > expected:
        return (
            f"Legacy {kind} grew from {expected} to {current}. Library v2 is "
            f"mid-migration (docs §32.3.1): new code reads and writes lib2_*, "
            f"not the legacy tables. Largest holders right now:\n{listing}"
        )
    return (
        f"Legacy {kind} fell from {expected} to {current} — that is the point. "
        f"Lower the baseline in {_BASELINE.name} to {current} in this same "
        f"commit so the ratchet stays tight. Largest holders left:\n{listing}"
    )
