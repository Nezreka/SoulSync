"""Pin the MusicBrainz service alias methods + worker enrichment.

Issue #442 — these methods feed the alias data the helper compares
against. Three layers covered:

1. ``fetch_artist_aliases`` — pulls aliases off the MB get-artist
   response, defensive against missing fields, broken JSON, network
   errors.
2. ``update_artist_aliases`` — persists to ``artists.aliases`` as a
   JSON array. Empty/None → column cleared.
3. ``get_artist_aliases`` — reads back by artist NAME (not id) since
   that's what the verifier has at quarantine time.

Worker enrichment integration covered separately: when MB worker
matches an artist, it calls fetch + update so subsequent verifier
calls find aliases in the library DB without firing live MB.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Real MusicDatabase against a tmp file. Uses the production
    schema (so the `aliases` column from commit 1's migration is
    present) and the production update/get methods we're pinning."""
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'test.db'))
    from database.music_database import MusicDatabase
    return MusicDatabase()


@pytest.fixture
def service(temp_db):
    """MusicBrainzService with stubbed mb_client. The DB is real;
    the network is not."""
    from core.musicbrainz_service import MusicBrainzService
    svc = MusicBrainzService(temp_db)
    svc.mb_client = MagicMock()
    return svc


_seed_counter = 0


def _seed_artist(db, name: str, **fields) -> str:
    """Insert a row into the artists table.

    `artists.id` is TEXT (NOT INTEGER auto-increment), so we mint a
    deterministic test id rather than relying on rowid magic.
    Returns the id as str — that's what the production code paths
    use too (read methods, joins, etc.).
    """
    global _seed_counter
    _seed_counter += 1
    artist_id = f"test-artist-{_seed_counter}"
    conn = db._get_connection()
    cursor = conn.cursor()
    cols = ['id', 'name'] + list(fields.keys())
    placeholders = ','.join('?' * len(cols))
    cursor.execute(
        f"INSERT INTO artists ({','.join(cols)}) VALUES ({placeholders})",
        [artist_id, name] + list(fields.values()),
    )
    conn.commit()
    conn.close()
    return artist_id


# ---------------------------------------------------------------------------
# fetch_artist_aliases — MB get-artist response parser
# ---------------------------------------------------------------------------


class TestFetchArtistAliases:
    def test_extracts_alias_names_from_mb_response(self, service):
        """Reporter's case 1 shape: MB returns aliases for Hiroyuki
        Sawano including the Japanese kanji form. Extract the `name`
        from each alias entry. Issue #586 — also include the canonical
        ``name`` and ``sort-name`` from the artist record itself, plus
        per-alias ``sort-name`` when present, for cross-script bridging."""
        service.mb_client.get_artist.return_value = {
            'id': '60d2ea34-1912-425f-bf9c-fc544e4448cd',
            'name': 'Hiroyuki Sawano',
            'sort-name': 'Sawano, Hiroyuki',
            'aliases': [
                {'name': '澤野弘之', 'sort-name': '澤野弘之', 'locale': 'ja', 'primary': True},
                {'name': 'SawanoHiroyuki', 'sort-name': 'SawanoHiroyuki', 'locale': None},
                {'name': 'Sawano Hiroyuki', 'sort-name': 'Sawano, Hiroyuki', 'locale': 'en'},
            ],
        }

        aliases = service.fetch_artist_aliases('60d2ea34-1912-425f-bf9c-fc544e4448cd')

        assert 'Hiroyuki Sawano' in aliases  # canonical name
        assert 'Sawano, Hiroyuki' in aliases  # canonical sort-name (also matches alias sort-name)
        assert '澤野弘之' in aliases
        assert 'SawanoHiroyuki' in aliases
        assert 'Sawano Hiroyuki' in aliases

    def test_dedup_case_insensitive(self, service):
        """Same name with different casing should collapse — MB
        sometimes returns duplicate-looking entries with locale
        variations."""
        service.mb_client.get_artist.return_value = {
            'aliases': [
                {'name': 'Hiroyuki Sawano'},
                {'name': 'hiroyuki sawano'},
                {'name': 'HIROYUKI SAWANO'},
            ],
        }
        aliases = service.fetch_artist_aliases('mbid-x')
        assert len(aliases) == 1

    def test_empty_alias_entries_skipped(self, service):
        service.mb_client.get_artist.return_value = {
            'aliases': [
                {'name': ''},
                {'name': '   '},
                {'name': None},
                {'name': 'Real Name'},
            ],
        }
        aliases = service.fetch_artist_aliases('mbid-x')
        assert aliases == ['Real Name']

    def test_missing_aliases_key_returns_canonical_name_only(self, service):
        """MB artist record without an aliases array still returns the
        canonical name (post-#586). Pre-fix this returned [] which
        meant cross-script bridging was impossible."""
        service.mb_client.get_artist.return_value = {
            'id': 'mbid-x',
            'name': 'Some Artist',
        }
        assert service.fetch_artist_aliases('mbid-x') == ['Some Artist']

    def test_aliases_null_returns_empty(self, service):
        """MB sometimes returns `aliases: null` instead of empty array."""
        service.mb_client.get_artist.return_value = {'aliases': None}
        assert service.fetch_artist_aliases('mbid-x') == []

    def test_get_artist_failure_returns_empty(self, service):
        """Network / API failure → empty list, NOT raise. Caller
        must treat empty as 'no aliases available, fall back to
        direct match' so transient MB outages never trigger
        stricter quarantine decisions than today."""
        service.mb_client.get_artist.side_effect = Exception("network error")
        assert service.fetch_artist_aliases('mbid-x') == []

    def test_get_artist_returns_none_returns_empty(self, service):
        service.mb_client.get_artist.return_value = None
        assert service.fetch_artist_aliases('mbid-x') == []

    def test_empty_mbid_returns_empty_without_api_call(self, service):
        assert service.fetch_artist_aliases('') == []
        assert service.fetch_artist_aliases(None) == []
        service.mb_client.get_artist.assert_not_called()

    def test_includes_aliases_in_request(self, service):
        """Verify the MB API call requests the aliases include
        explicitly — without `inc=aliases` the response wouldn't
        carry them."""
        service.mb_client.get_artist.return_value = {'aliases': []}
        service.fetch_artist_aliases('mbid-x')
        service.mb_client.get_artist.assert_called_once_with(
            'mbid-x', includes=['aliases'],
        )


# ---------------------------------------------------------------------------
# update_artist_aliases — persistence
# ---------------------------------------------------------------------------


class TestUpdateArtistAliases:
    def test_persists_as_json_array(self, service, temp_db):
        artist_id = _seed_artist(temp_db, 'Hiroyuki Sawano')
        service.update_artist_aliases(artist_id, ['澤野弘之', 'SawanoHiroyuki'])

        conn = temp_db._get_connection()
        row = conn.execute("SELECT aliases FROM artists WHERE id = ?", (artist_id,)).fetchone()
        conn.close()
        parsed = json.loads(row[0])
        assert parsed == ['澤野弘之', 'SawanoHiroyuki']

    def test_idempotent_overwrite(self, service, temp_db):
        artist_id = _seed_artist(temp_db, 'X')
        service.update_artist_aliases(artist_id, ['a'])
        service.update_artist_aliases(artist_id, ['b', 'c'])
        conn = temp_db._get_connection()
        row = conn.execute("SELECT aliases FROM artists WHERE id = ?", (artist_id,)).fetchone()
        conn.close()
        assert json.loads(row[0]) == ['b', 'c']

    def test_empty_list_clears_column(self, service, temp_db):
        artist_id = _seed_artist(temp_db, 'X', aliases=json.dumps(['old']))
        service.update_artist_aliases(artist_id, [])
        conn = temp_db._get_connection()
        row = conn.execute("SELECT aliases FROM artists WHERE id = ?", (artist_id,)).fetchone()
        conn.close()
        assert row[0] is None

    def test_none_artist_id_is_noop(self, service, temp_db):
        """Defensive: caller might pass None on edge cases. Don't crash."""
        service.update_artist_aliases(None, ['x'])  # no exception


# ---------------------------------------------------------------------------
# get_artist_aliases — read back by artist NAME (verifier path)
# ---------------------------------------------------------------------------


class TestGetArtistAliases:
    def test_returns_aliases_for_known_artist(self, service, temp_db):
        artist_id = _seed_artist(
            temp_db, 'Hiroyuki Sawano',
            aliases=json.dumps(['澤野弘之', 'SawanoHiroyuki']),
        )
        aliases = service.get_artist_aliases('Hiroyuki Sawano')
        assert '澤野弘之' in aliases
        assert 'SawanoHiroyuki' in aliases

    def test_case_insensitive_lookup(self, service, temp_db):
        """Verifier passes the artist name from track metadata —
        casing might differ from how the library stored it."""
        _seed_artist(temp_db, 'Hiroyuki Sawano', aliases=json.dumps(['澤野弘之']))
        assert service.get_artist_aliases('hiroyuki sawano') == ['澤野弘之']
        assert service.get_artist_aliases('HIROYUKI SAWANO') == ['澤野弘之']

    def test_returns_empty_for_unknown_artist(self, service):
        assert service.get_artist_aliases('NeverHeardOf') == []

    def test_returns_empty_for_artist_without_aliases(self, service, temp_db):
        _seed_artist(temp_db, 'X')  # no aliases column set
        assert service.get_artist_aliases('X') == []

    def test_handles_corrupt_json_gracefully(self, service, temp_db):
        _seed_artist(temp_db, 'X', aliases='not-valid-json')
        # Returns [] instead of raising — defensive against legacy
        # rows that might have been written by an older format
        assert service.get_artist_aliases('X') == []

    def test_handles_non_list_json_gracefully(self, service, temp_db):
        _seed_artist(temp_db, 'X', aliases=json.dumps({'wrong': 'shape'}))
        assert service.get_artist_aliases('X') == []

    def test_empty_name_returns_empty_without_query(self, service):
        assert service.get_artist_aliases('') == []
        assert service.get_artist_aliases(None) == []


# ---------------------------------------------------------------------------
# Worker integration — alias enrichment fires on successful match
# ---------------------------------------------------------------------------


class TestWorkerAliasEnrichment:
    def test_matched_artist_triggers_alias_fetch_and_persist(self, temp_db, monkeypatch):
        """End-to-end: worker matches an artist, immediately fetches
        + persists aliases. Subsequent verifier calls find them in
        the library DB without firing live MB."""
        from core.musicbrainz_worker import MusicBrainzWorker

        artist_id = _seed_artist(temp_db, 'Hiroyuki Sawano')

        worker = MusicBrainzWorker.__new__(MusicBrainzWorker)
        worker.database = temp_db
        worker.mb_service = MagicMock()
        worker.mb_service.match_artist.return_value = {
            'mbid': '60d2ea34-1912-425f-bf9c-fc544e4448cd', 'name': 'Hiroyuki Sawano',
        }
        worker.mb_service.fetch_artist_aliases.return_value = ['澤野弘之', 'SawanoHiroyuki']
        worker.stats = {'matched': 0, 'not_found': 0, 'errors': 0}

        # Bypass _get_existing_id (would query DB for prior MBID)
        worker._get_existing_id = MagicMock(return_value=None)

        worker._process_item({'type': 'artist', 'id': artist_id, 'name': 'Hiroyuki Sawano'})

        worker.mb_service.update_artist_mbid.assert_called_once_with(
            artist_id, '60d2ea34-1912-425f-bf9c-fc544e4448cd', 'matched',
        )
        worker.mb_service.fetch_artist_aliases.assert_called_once_with(
            '60d2ea34-1912-425f-bf9c-fc544e4448cd',
        )
        worker.mb_service.update_artist_aliases.assert_called_once_with(
            artist_id, ['澤野弘之', 'SawanoHiroyuki'],
        )

    def test_no_alias_call_when_artist_not_matched(self, temp_db):
        """If MB returned no MBID match, don't fetch aliases —
        nothing to enrich."""
        from core.musicbrainz_worker import MusicBrainzWorker
        artist_id = _seed_artist(temp_db, 'Unknown')

        worker = MusicBrainzWorker.__new__(MusicBrainzWorker)
        worker.database = temp_db
        worker.mb_service = MagicMock()
        worker.mb_service.match_artist.return_value = None
        worker.stats = {'matched': 0, 'not_found': 0, 'errors': 0}
        worker._get_existing_id = MagicMock(return_value=None)

        worker._process_item({'type': 'artist', 'id': artist_id, 'name': 'Unknown'})

        worker.mb_service.fetch_artist_aliases.assert_not_called()
        worker.mb_service.update_artist_aliases.assert_not_called()

    def test_existing_mbid_path_backfills_aliases_when_column_empty(self, temp_db):
        """Issue #442 perf followup: existing-MBID short-circuit path
        was skipping alias enrichment entirely. Users with libraries
        enriched BEFORE this PR shipped have MBIDs but NULL aliases.
        Worker should fetch aliases on the existing-id path too —
        one-time backfill on first re-scan post-deploy."""
        from core.musicbrainz_worker import MusicBrainzWorker
        artist_id = _seed_artist(temp_db, 'Hiroyuki Sawano')

        worker = MusicBrainzWorker.__new__(MusicBrainzWorker)
        worker.database = temp_db
        worker.db = temp_db  # _artist_aliases_empty uses self.db
        worker.mb_service = MagicMock()
        worker.mb_service.fetch_artist_aliases.return_value = ['澤野弘之', 'SawanoHiroyuki']
        worker.stats = {'matched': 0, 'not_found': 0, 'errors': 0}
        # Existing MBID path
        worker._get_existing_id = MagicMock(return_value='mb-existing-id')

        worker._process_item({'type': 'artist', 'id': artist_id, 'name': 'Hiroyuki Sawano'})

        # MBID was preserved
        worker.mb_service.update_artist_mbid.assert_called_once_with(
            artist_id, 'mb-existing-id', 'matched',
        )
        # Aliases backfilled
        worker.mb_service.fetch_artist_aliases.assert_called_once_with('mb-existing-id')
        worker.mb_service.update_artist_aliases.assert_called_once_with(
            artist_id, ['澤野弘之', 'SawanoHiroyuki'],
        )

    def test_existing_mbid_path_skips_backfill_when_aliases_already_set(self, temp_db):
        """If aliases are already populated, don't re-fetch — re-scan
        cycles after backfill complete should be no-ops."""
        from core.musicbrainz_worker import MusicBrainzWorker
        artist_id = _seed_artist(
            temp_db, 'X', aliases=json.dumps(['existing-alias']),
        )

        worker = MusicBrainzWorker.__new__(MusicBrainzWorker)
        worker.database = temp_db
        worker.db = temp_db
        worker.mb_service = MagicMock()
        worker.stats = {'matched': 0, 'not_found': 0, 'errors': 0}
        worker._get_existing_id = MagicMock(return_value='mb-x')

        worker._process_item({'type': 'artist', 'id': artist_id, 'name': 'X'})

        # No alias work — column already populated
        worker.mb_service.fetch_artist_aliases.assert_not_called()
        worker.mb_service.update_artist_aliases.assert_not_called()

    def test_existing_mbid_backfill_failure_does_not_break_match(self, temp_db):
        """Backfill is best-effort — failure to fetch aliases must
        NOT prevent the MBID-preservation update from happening."""
        from core.musicbrainz_worker import MusicBrainzWorker
        artist_id = _seed_artist(temp_db, 'X')

        worker = MusicBrainzWorker.__new__(MusicBrainzWorker)
        worker.database = temp_db
        worker.db = temp_db
        worker.mb_service = MagicMock()
        worker.mb_service.fetch_artist_aliases.side_effect = Exception("MB down")
        worker.stats = {'matched': 0, 'not_found': 0, 'errors': 0}
        worker._get_existing_id = MagicMock(return_value='mb-x')

        # Should NOT raise
        worker._process_item({'type': 'artist', 'id': artist_id, 'name': 'X'})

        # MBID still preserved despite alias backfill failure
        worker.mb_service.update_artist_mbid.assert_called_once_with(
            artist_id, 'mb-x', 'matched',
        )

    def test_alias_fetch_failure_does_not_break_match(self, temp_db):
        """If alias fetch raises (network error, malformed response,
        whatever), the artist match still gets recorded — alias
        enrichment is best-effort, not a gate."""
        from core.musicbrainz_worker import MusicBrainzWorker
        artist_id = _seed_artist(temp_db, 'X')

        worker = MusicBrainzWorker.__new__(MusicBrainzWorker)
        worker.database = temp_db
        worker.mb_service = MagicMock()
        worker.mb_service.match_artist.return_value = {'mbid': 'mb-x', 'name': 'X'}
        worker.mb_service.fetch_artist_aliases.side_effect = Exception("boom")
        worker.stats = {'matched': 0, 'not_found': 0, 'errors': 0}
        worker._get_existing_id = MagicMock(return_value=None)

        worker._process_item({'type': 'artist', 'id': artist_id, 'name': 'X'})

        # MBID still got updated despite alias failure
        worker.mb_service.update_artist_mbid.assert_called_once_with(
            artist_id, 'mb-x', 'matched',
        )
        # No alias write attempted (fetch raised before update)
        worker.mb_service.update_artist_aliases.assert_not_called()
        # And the match was still counted
        assert worker.stats['matched'] == 1


# ---------------------------------------------------------------------------
# lookup_artist_aliases — multi-tier resolution (library → cache → live)
# ---------------------------------------------------------------------------


class TestLookupArtistAliasesMultiTier:
    def test_tier1_library_db_hit(self, service, temp_db):
        """Fast path: artist already enriched in library DB.
        No MB API call fired."""
        _seed_artist(temp_db, 'Hiroyuki Sawano',
                     aliases=json.dumps(['澤野弘之', 'SawanoHiroyuki']))

        aliases = service.lookup_artist_aliases('Hiroyuki Sawano')

        assert '澤野弘之' in aliases
        service.mb_client.search_artist.assert_not_called()
        service.mb_client.get_artist.assert_not_called()

    def test_tier3_live_mb_lookup_when_not_in_library(self, service, temp_db):
        """Cache miss + library miss → MB search → fetch by MBID →
        cache the result."""
        service.mb_client.search_artist.return_value = [
            {'id': 'mb-sawano', 'name': 'Hiroyuki Sawano', 'score': 100},
        ]
        service.mb_client.get_artist.return_value = {
            'aliases': [{'name': '澤野弘之'}, {'name': 'SawanoHiroyuki'}],
        }

        aliases = service.lookup_artist_aliases('Hiroyuki Sawano')

        assert '澤野弘之' in aliases
        service.mb_client.search_artist.assert_called_once()
        service.mb_client.get_artist.assert_called_once_with(
            'mb-sawano', includes=['aliases'],
        )

    def test_tier2_cache_hit_skips_live_lookup(self, service, temp_db):
        """Second call for same artist hits the cache, doesn't
        re-query MB. Critical for the verifier path — 100 quarantine
        candidates with the same artist must NOT trigger 100 MB
        calls."""
        service.mb_client.search_artist.return_value = [
            {'id': 'mb-x', 'name': 'X', 'score': 100},
        ]
        service.mb_client.get_artist.return_value = {
            'aliases': [{'name': 'X-alias'}],
        }

        # First call — populates cache
        first = service.lookup_artist_aliases('X')
        # Second call — should be cached
        second = service.lookup_artist_aliases('X')

        assert first == second == ['X-alias']
        # Only ONE round-trip to MB despite two calls
        assert service.mb_client.search_artist.call_count == 1
        assert service.mb_client.get_artist.call_count == 1

    def test_empty_name_returns_empty_no_api_call(self, service):
        assert service.lookup_artist_aliases('') == []
        assert service.lookup_artist_aliases(None) == []
        service.mb_client.search_artist.assert_not_called()

    def test_search_failure_returns_empty(self, service):
        """Network outage on search — return empty, cache the empty
        result so we don't keep retrying."""
        service.mb_client.search_artist.side_effect = Exception("network down")
        aliases = service.lookup_artist_aliases('Anyone')
        assert aliases == []

    def test_no_search_results_returns_empty(self, service):
        """Artist not found on MB under either strict or non-strict
        search — empty return, cached so we don't re-search the same
        name forever. Issue #586: strict-then-non-strict means TWO
        search calls per uncached lookup; the empty cache prevents
        further calls on the next invocation."""
        service.mb_client.search_artist.return_value = []
        aliases = service.lookup_artist_aliases('NeverHeardOf')
        assert aliases == []
        # First lookup: strict + non-strict fallback = 2 calls.
        assert service.mb_client.search_artist.call_count == 2
        # Second call should hit cache, not re-search at all.
        service.lookup_artist_aliases('NeverHeardOf')
        assert service.mb_client.search_artist.call_count == 2

    def test_low_confidence_match_skipped(self, service):
        """Search returned something but the name similarity is too
        low — don't trust it. Could pull in aliases for the wrong
        artist (e.g. searching 'John Smith' returns a different
        John Smith). Empty return + cached."""
        service.mb_client.search_artist.return_value = [
            {'id': 'mb-different', 'name': 'Completely Different Artist', 'score': 30},
        ]
        aliases = service.lookup_artist_aliases('Hiroyuki Sawano')
        assert aliases == []
        # Didn't even try fetching aliases for the bad match
        service.mb_client.get_artist.assert_not_called()

    def test_moderate_confidence_match_now_skipped_strict_threshold(self, service):
        """Threshold tightened to 0.85 (was 0.6) — moderate matches
        (sim ~0.7) are no longer trusted. Reduces false-positive
        risk on ambiguous artist names."""
        service.mb_client.search_artist.return_value = [
            # Different name, MB matched on weak signal — combined
            # score lands around 0.6-0.7, below the new 0.85 floor.
            {'id': 'mb-x', 'name': 'John Williams', 'score': 50},
        ]
        aliases = service.lookup_artist_aliases('John Smith')
        assert aliases == []
        service.mb_client.get_artist.assert_not_called()

    def test_ambiguous_results_skipped(self, service):
        """When MB search returns multiple results with similar high
        scores (within 0.1 of each other), the artist name is
        ambiguous — common name with multiple distinct artists
        ('John Smith' returning 10 different John Smiths). Pulling
        aliases for one could mismatch the wrong artist's data
        against our file. Skip + cache empty."""
        service.mb_client.search_artist.return_value = [
            {'id': 'mb-smith-1', 'name': 'John Smith', 'score': 100},
            {'id': 'mb-smith-2', 'name': 'John Smith', 'score': 100},
            {'id': 'mb-smith-3', 'name': 'John Smith', 'score': 100},
        ]
        aliases = service.lookup_artist_aliases('John Smith')
        assert aliases == []
        # Didn't fetch aliases for either ambiguous candidate
        service.mb_client.get_artist.assert_not_called()

    def test_unambiguous_high_confidence_match_succeeds(self, service):
        """Sanity: a clear winner (top result high, no near-tie with
        runner-up) still triggers alias fetch — the ambiguity gate
        doesn't break the legit case."""
        service.mb_client.search_artist.return_value = [
            {'id': 'mb-sawano', 'name': 'Hiroyuki Sawano', 'score': 100},
            {'id': 'mb-other', 'name': 'Unrelated Artist', 'score': 30},
        ]
        service.mb_client.get_artist.return_value = {
            'aliases': [{'name': '澤野弘之'}],
        }
        aliases = service.lookup_artist_aliases('Hiroyuki Sawano')
        assert '澤野弘之' in aliases

    def test_library_with_empty_aliases_falls_through_to_live(self, service, temp_db):
        """Edge case: library has the artist but `aliases` column is
        NULL (worker hasn't enriched yet). Don't get stuck — fall
        through to live MB lookup."""
        _seed_artist(temp_db, 'Hiroyuki Sawano')  # no aliases
        service.mb_client.search_artist.return_value = [
            {'id': 'mb-sawano', 'name': 'Hiroyuki Sawano', 'score': 100},
        ]
        service.mb_client.get_artist.return_value = {
            'aliases': [{'name': '澤野弘之'}],
        }

        aliases = service.lookup_artist_aliases('Hiroyuki Sawano')

        assert '澤野弘之' in aliases
        service.mb_client.search_artist.assert_called_once()
