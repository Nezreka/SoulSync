"""Genre-diff-suppression consistency (commit 73ec9c7a):

``build_tag_diff`` suppresses a genre "change" when the DB's genre value
is a pure subset of the file's existing (richer) genre list — meant to
stop automated enrichment from downgrading a rich file tag to a generic
single DB genre (e.g. "Pop"). But ``write_tags_to_file`` had no matching
guard: a batch write (gated on ``build_tag_diff``'s ``changed`` flag)
would silently skip the file entirely, while a single-track write (which
calls ``write_tags_to_file`` directly, bypassing the diff) would still
overwrite the richer file value — contradicting what the diff/preview
told the user. The same subset check must apply in both places so the
preview and the actual write always agree.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from mutagen.flac import FLAC

from core.tag_writer import build_tag_diff, write_tags_to_file


def _make_minimal_flac(path):
    minimal = (
        b'fLaC' + b'\x80\x00\x00\x22' + b'\x00\x10\x00\x10'
        + b'\x00\x00\x00\x00\x00\x00' + b'\x0a\xc4\x42\xf0\x00\x00\x00\x00' + b'\x00' * 16
    )
    with open(path, 'wb') as f:
        f.write(minimal)


@pytest.fixture
def flac_path():
    fd, path = tempfile.mkstemp(suffix='.flac')
    os.close(fd)
    _make_minimal_flac(path)
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


def test_write_preserves_richer_existing_genre_when_db_is_a_subset(flac_path):
    audio = FLAC(flac_path)
    audio['genre'] = ['Electronic; House; Techno']
    audio.save()

    result = write_tags_to_file(flac_path, {'genres': ['Electronic']}, embed_cover=False)
    assert result['success'] is True
    # The narrower DB genre must not clobber the richer existing tag —
    # matches what build_tag_diff already shows as "no change" for this pair.
    assert FLAC(flac_path).get('genre') == ['Electronic; House; Techno']


def test_write_still_applies_a_genuinely_different_genre(flac_path):
    audio = FLAC(flac_path)
    audio['genre'] = ['Pop']
    audio.save()

    result = write_tags_to_file(
        flac_path, {'genres': ['Electronic', 'House']}, embed_cover=False)
    assert result['success'] is True
    assert FLAC(flac_path).get('genre') == ['Electronic, House']


def test_write_applies_genre_when_file_has_none_yet(flac_path):
    result = write_tags_to_file(flac_path, {'genres': ['Electronic']}, embed_cover=False)
    assert result['success'] is True
    assert FLAC(flac_path).get('genre') == ['Electronic']


def test_diff_and_write_agree_on_subset_genre(flac_path):
    """The preview (build_tag_diff) and the actual writer must reach the
    SAME verdict for the same genre pair — no silent divergence between
    what the UI shows and what a write would actually do."""
    audio = FLAC(flac_path)
    audio['genre'] = ['Electronic; House; Techno']
    audio.save()

    diff = build_tag_diff(
        {'genre': 'Electronic; House; Techno'}, {'genres': ['Electronic']})
    genre_diff = next(d for d in diff if d['field'] == 'Genre')
    assert genre_diff['changed'] is False

    write_tags_to_file(flac_path, {'genres': ['Electronic']}, embed_cover=False)
    # The write must match what the diff promised: no actual change.
    assert FLAC(flac_path).get('genre') == ['Electronic; House; Techno']
