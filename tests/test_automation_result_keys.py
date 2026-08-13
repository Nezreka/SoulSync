"""The card's last-run sentences must keep matching what handlers return.

`-automations.format.ts` turns a handler's result dict into a sentence —
"Checked 42 artists, wishlisted 7 tracks" instead of the raw
"artists_scanned: 42 · tracks_added_to_wishlist: 7". That is only an
improvement while the keys it reads are keys the handler still sets.

A sentence built from a key a handler stopped returning does not fail loudly:
it silently reads zero, and the card says "Checked 0 artists" about a scan
that worked. This is the seam that catches the rename.

Deliberately a source-text check rather than an import: the point is to pin
the literal key strings on both sides, and importing the handlers would drag
in the whole scanning stack to answer a question about two dictionaries.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_FORMAT_TS = _ROOT / 'webui/src/routes/automations/-automations.format.ts'
_HANDLERS = _ROOT / 'core/automation/handlers'

# action_type → (handler module, the result keys the sentence reads).
# Keep in step with ACTION_SENTENCES in -automations.format.ts.
SENTENCE_SOURCES = {
    'scan_watchlist': ('scan_watchlist.py',
                       ('artists_scanned', 'new_tracks_found', 'tracks_added_to_wishlist')),
    'run_duplicate_cleaner': ('duplicate_cleaner.py',
                              ('files_scanned', 'duplicates_found', 'files_deleted',
                               'space_freed_mb')),
    'refresh_mirrored': ('refresh_mirrored.py', ('refreshed', 'errors')),
    'start_database_update': ('database_update.py', ('artists', 'albums', 'tracks')),
}


def _format_source() -> str:
    return _FORMAT_TS.read_text(encoding='utf-8')


def _sentence_action_types() -> set:
    """The action types ACTION_SENTENCES actually covers."""
    source = _format_source()
    body = source.split('const ACTION_SENTENCES: Record<string, Sentence> = {', 1)[1]
    body = body.split('\n};', 1)[0]
    return set(re.findall(r'^  ([a-z_]+): \(r\)', body, re.MULTILINE))


@pytest.mark.parametrize('action_type', sorted(SENTENCE_SOURCES))
def test_every_key_the_sentence_reads_is_one_the_handler_sets(action_type):
    module, keys = SENTENCE_SOURCES[action_type]
    source = (_HANDLERS / module).read_text(encoding='utf-8')
    missing = [key for key in keys if f"'{key}'" not in source and f'"{key}"' not in source]
    assert not missing, (
        f"{module} no longer sets {missing}, but the {action_type} card sentence "
        f"still reads them — it will quietly render zero instead of failing."
    )


def test_the_two_sides_cover_the_same_actions():
    """A sentence with no entry here is unguarded; an entry with no sentence is
    a leftover that would make the guard look stronger than it is."""
    assert _sentence_action_types() == set(SENTENCE_SOURCES)


def test_sentences_only_exist_for_real_registered_actions():
    """Guards against a sentence keyed on an action type nothing can emit —
    it would never render, and would read as coverage that is not there."""
    registration = (_HANDLERS / 'registration.py').read_text(encoding='utf-8')
    unknown = [a for a in SENTENCE_SOURCES if f"'{a}'" not in registration]
    assert not unknown, f"no registered handler for: {unknown}"
