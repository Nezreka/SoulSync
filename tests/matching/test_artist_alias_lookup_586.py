"""Cross-script artist alias lookup — issue #586.

The verifier's alias path was missing the Cyrillic spelling for
"Dmitry Yablonsky" because:

1. ``fetch_artist_aliases`` only read ``data['aliases']`` and ignored
   the canonical ``name`` / ``sort-name`` fields. When MB's canonical
   name IS the cross-script form, the Latin spelling never made it
   into the alias output (and vice-versa).

2. ``lookup_artist_aliases`` ran search in strict mode only, which
   queries ``artist:"..."`` and skips alias / sortname indexes. Cross-
   script searches found nothing under strict.

3. The trust gate weighted local similarity 70%, so cross-script
   matches scored ~0.30 even when MB's own confidence was 100, getting
   rejected as low-confidence.

These tests pin all three fixes plus the original Hiroyuki Sawano
case from #442 (regression guard).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.musicbrainz_service import MusicBrainzService


@pytest.fixture
def service():
    """Build a MusicBrainzService with mocked client + DB. The instance
    is built bypassing __init__ so we don't need real config / DB."""
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc._calculate_similarity = lambda a, b: _simple_sim(a, b)
    svc.get_artist_aliases = MagicMock(return_value=[])
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    return svc


def _simple_sim(a: str, b: str) -> float:
    """Tiny stub similarity — exact match=1.0 else 0.0. Cross-script
    pairs naturally fall into 0.0 since no characters overlap."""
    if not a or not b:
        return 0.0
    return 1.0 if a.lower() == b.lower() else 0.0


# ──────────────────────────────────────────────────────────────────────
# fetch_artist_aliases — canonical name + sort-name now included
# ──────────────────────────────────────────────────────────────────────

def test_fetch_aliases_includes_canonical_name(service):
    service.mb_client.get_artist.return_value = {
        'name': 'Дмитрий Яблонский',
        'sort-name': 'Yablonsky, Dmitry',
        'aliases': [
            {'name': 'Dmitry Yablonsky', 'sort-name': 'Yablonsky, Dmitry'},
        ],
    }
    aliases = service.fetch_artist_aliases('mbid-yablonsky')
    # Canonical name MUST be present so cross-script matching works
    # whichever direction the canonical form points.
    assert 'Дмитрий Яблонский' in aliases
    assert 'Dmitry Yablonsky' in aliases
    # Sort-name covered too
    assert 'Yablonsky, Dmitry' in aliases


def test_fetch_aliases_dedupes_canonical_against_alias_entry(service):
    # MB sometimes lists the canonical name as ALSO an alias entry.
    # No duplicate output.
    service.mb_client.get_artist.return_value = {
        'name': 'Hiroyuki Sawano',
        'sort-name': 'Sawano, Hiroyuki',
        'aliases': [
            {'name': 'Hiroyuki Sawano', 'sort-name': 'Sawano, Hiroyuki'},
            {'name': '澤野弘之'},
        ],
    }
    aliases = service.fetch_artist_aliases('mbid-sawano')
    assert aliases.count('Hiroyuki Sawano') == 1
    assert '澤野弘之' in aliases


def test_fetch_aliases_handles_missing_canonical_gracefully(service):
    service.mb_client.get_artist.return_value = {
        'aliases': [{'name': 'Dmitry Yablonsky'}],
    }
    aliases = service.fetch_artist_aliases('mbid-x')
    assert aliases == ['Dmitry Yablonsky']


def test_fetch_aliases_returns_empty_on_no_data(service):
    service.mb_client.get_artist.return_value = None
    assert service.fetch_artist_aliases('mbid-x') == []


def test_fetch_aliases_returns_empty_on_exception(service):
    service.mb_client.get_artist.side_effect = RuntimeError('boom')
    assert service.fetch_artist_aliases('mbid-x') == []


# ──────────────────────────────────────────────────────────────────────
# lookup_artist_aliases — strict + non-strict fallback
# ──────────────────────────────────────────────────────────────────────

def test_lookup_falls_back_to_non_strict_when_strict_returns_nothing(service):
    # Strict search returns nothing (typical cross-script case).
    # Non-strict hits the alias index and finds the artist.
    def search(name, limit, strict):
        if strict:
            return []
        return [{'id': 'mbid-yab', 'name': 'Дмитрий Яблонский', 'score': 100}]
    service.mb_client.search_artist.side_effect = search
    service.mb_client.get_artist.return_value = {
        'name': 'Дмитрий Яблонский',
        'aliases': [{'name': 'Dmitry Yablonsky'}],
    }

    aliases = service.lookup_artist_aliases('Dmitry Yablonsky')
    assert 'Дмитрий Яблонский' in aliases
    assert 'Dmitry Yablonsky' in aliases
    # Confirm both modes were attempted
    calls = service.mb_client.search_artist.call_args_list
    assert any(call.kwargs.get('strict') is True for call in calls) or any(
        len(call.args) >= 3 and call.args[2] is True for call in calls
    )


def test_lookup_falls_back_to_non_strict_when_strict_score_too_low(service):
    # Strict returns a low-confidence match (cross-script — local sim
    # near 0). Non-strict hits a stronger match via alias index.
    def search(name, limit, strict):
        if strict:
            return [{'id': 'mbid-other', 'name': 'Some Other Artist', 'score': 30}]
        return [{'id': 'mbid-yab', 'name': 'Дмитрий Яблонский', 'score': 100}]
    service.mb_client.search_artist.side_effect = search
    service.mb_client.get_artist.return_value = {
        'name': 'Дмитрий Яблонский',
        'aliases': [{'name': 'Dmitry Yablonsky'}],
    }

    aliases = service.lookup_artist_aliases('Dmitry Yablonsky')
    assert 'Дмитрий Яблонский' in aliases


# ──────────────────────────────────────────────────────────────────────
# Trust gate — MB-score-only escape for cross-script
# ──────────────────────────────────────────────────────────────────────

def test_trust_gate_passes_on_high_mb_score_even_with_zero_local_sim(service):
    # The cross-script case: local sim ~0, MB score 100, single
    # unambiguous result → should now pass.
    service.mb_client.search_artist.return_value = [
        {'id': 'mbid-yab', 'name': 'Дмитрий Яблонский', 'score': 100},
    ]
    service.mb_client.get_artist.return_value = {
        'name': 'Дмитрий Яблонский',
        'aliases': [{'name': 'Dmitry Yablonsky'}],
    }

    aliases = service.lookup_artist_aliases('Dmitry Yablonsky')
    assert 'Дмитрий Яблонский' in aliases
    assert 'Dmitry Yablonsky' in aliases


def test_trust_gate_rejects_low_mb_score_low_local_sim(service):
    # Low confidence on both axes — must NOT pull aliases (false-
    # positive risk: pulling random artist's aliases).
    service.mb_client.search_artist.return_value = [
        {'id': 'mbid-other', 'name': 'Some Random Artist', 'score': 40},
    ]
    aliases = service.lookup_artist_aliases('Dmitry Yablonsky')
    assert aliases == []
    service.mb_client.get_artist.assert_not_called()


def test_trust_gate_rejects_when_two_high_mb_scores_tie(service):
    # Two artists named the same with score 100 → ambiguous → skip
    # even with MB-only escape (the unambiguity check still gates).
    service.mb_client.search_artist.return_value = [
        {'id': 'mbid-a', 'name': 'John Smith', 'score': 100},
        {'id': 'mbid-b', 'name': 'John Smith', 'score': 100},
    ]
    aliases = service.lookup_artist_aliases('John Smith')
    assert aliases == []


def test_trust_gate_passes_combined_score_when_local_sim_strong(service):
    # Same-script case from #442 — local sim high. Should still pass
    # (no regression on the existing path).
    service.mb_client.search_artist.return_value = [
        {'id': 'mbid-saw', 'name': 'Hiroyuki Sawano', 'score': 100},
    ]
    service.mb_client.get_artist.return_value = {
        'name': 'Hiroyuki Sawano',
        'aliases': [{'name': '澤野弘之'}],
    }

    aliases = service.lookup_artist_aliases('Hiroyuki Sawano')
    assert '澤野弘之' in aliases


# ──────────────────────────────────────────────────────────────────────
# End-to-end — reporter scenario via artist_names_match
# ──────────────────────────────────────────────────────────────────────

def test_yablonsky_reporter_scenario_end_to_end(service):
    """Issue #586 exact case: expected 'Dmitry Yablonsky', actual
    'Русская филармония, Дмитрий Яблонский', MB returns artist with
    canonical name in Cyrillic and Latin in aliases. Strict search
    finds nothing; non-strict finds the artist with high MB score."""
    def search(name, limit, strict):
        if strict:
            return []
        return [{'id': 'mbid-yab', 'name': 'Дмитрий Яблонский', 'score': 100}]
    service.mb_client.search_artist.side_effect = search
    service.mb_client.get_artist.return_value = {
        'name': 'Дмитрий Яблонский',
        'sort-name': 'Yablonsky, Dmitry',
        'aliases': [{'name': 'Dmitry Yablonsky'}],
    }

    aliases = service.lookup_artist_aliases('Dmitry Yablonsky')

    # Must include the Cyrillic canonical so artist_names_match can
    # bridge the credit-split actual.
    assert 'Дмитрий Яблонский' in aliases

    # Verify the full bridge with the real artist_names_match helper.
    from core.matching.artist_aliases import artist_names_match
    matched, score = artist_names_match(
        'Dmitry Yablonsky',
        'Русская филармония, Дмитрий Яблонский',
        aliases=aliases,
    )
    assert matched is True
    assert score >= 0.6
