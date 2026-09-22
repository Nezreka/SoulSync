"""A compilation belongs to Various Artists, not to whoever was downloading.

sassmastawillis opened an artist page for Don Felder — an Eagles guitarist —
and found 45 albums and 234 tracks under him: Shrek, Shrek 2, Shrek the Third,
Fifty Shades Freed, Bomb Rush Cyberfunk. He plays on ONE track of each. The
album row took its artist from the import context, which is whoever the
download was for, so every soundtrack containing one of his recordings landed
on him.
"""

from __future__ import annotations

import pytest

from core.imports.compilation import (
    VARIOUS_ARTISTS,
    compilation_album_artist,
    is_compilation_album_context,
    is_various_artists_name,
)


@pytest.mark.parametrize('name', [
    'Various Artists', 'various artists', '  VARIOUS  ', 'VA', 'v.a.',
    'Various Artist', 'Diverse Interpreten',
])
def test_the_spellings_sources_actually_ship(name):
    assert is_various_artists_name(name) is True


@pytest.mark.parametrize('name', ['Don Felder', '', None, 'Variety', 'Va Va Voom'])
def test_a_real_artist_is_never_various(name):
    assert is_various_artists_name(name) is False


# ── reading the album's own metadata ──

def test_an_album_credited_to_various_artists_is_a_compilation():
    assert is_compilation_album_context({'artists': [{'name': 'Various Artists'}]}) is True


def test_a_compilation_type_is_enough_on_its_own():
    # spotify says album_type, deezer says record_type ('compile')
    assert is_compilation_album_context({'album_type': 'compilation'}) is True
    assert is_compilation_album_context({'record_type': 'compile'}) is True


def test_a_soundtrack_credited_to_one_composer_stays_with_the_composer():
    """The guard that keeps this from over-firing: a score is a normal album."""
    ctx = {'secondary_types': ['Soundtrack'], 'artists': [{'name': 'Hans Zimmer'}]}
    assert is_compilation_album_context(ctx) is False


def test_a_soundtrack_credited_to_various_artists_is_a_compilation():
    ctx = {'secondary_types': ['Soundtrack'], 'artists': [{'name': 'Various Artists'}]}
    assert is_compilation_album_context(ctx) is True


def test_a_mixed_credit_is_not_various():
    """Two named artists is a collaboration, not a compilation."""
    ctx = {'artists': [{'name': 'Various Artists'}, {'name': 'Don Felder'}]}
    assert is_compilation_album_context(ctx) is False


def test_an_ordinary_album_is_not_a_compilation():
    ctx = {'album_type': 'album', 'artists': [{'name': 'Don Felder'}], 'name': 'Airborne'}
    assert is_compilation_album_context(ctx) is False


@pytest.mark.parametrize('ctx', [None, {}, 'not a dict', []])
def test_a_missing_album_context_is_not_a_compilation(ctx):
    assert is_compilation_album_context(ctx) is False


# ── the decision the import makes ──

def test_the_reported_case_moves_off_the_contributing_artist():
    ctx = {'name': 'Shrek 2 (Original Motion Picture Soundtrack)',
           'album_type': 'compilation',
           'artists': [{'name': 'Various Artists'}]}
    assert compilation_album_artist(ctx, 'Don Felder', enabled=True) == VARIOUS_ARTISTS


def test_an_ordinary_album_is_left_alone():
    ctx = {'name': 'Airborne', 'album_type': 'album', 'artists': [{'name': 'Don Felder'}]}
    assert compilation_album_artist(ctx, 'Don Felder', enabled=True) is None


def test_an_import_that_already_resolved_to_various_is_left_alone():
    """None means "nothing changed", so the caller can log only real moves."""
    ctx = {'album_type': 'compilation'}
    assert compilation_album_artist(ctx, 'Various Artists', enabled=True) is None


def test_the_setting_switches_it_off():
    ctx = {'album_type': 'compilation', 'artists': [{'name': 'Various Artists'}]}
    assert compilation_album_artist(ctx, 'Don Felder', enabled=False) is None


def test_the_setting_is_read_from_config_when_not_passed(monkeypatch):
    import core.imports.compilation as mod

    ctx = {'album_type': 'compilation'}
    monkeypatch.setattr(mod, '_detect_enabled', lambda: False)
    assert compilation_album_artist(ctx, 'Don Felder') is None
    monkeypatch.setattr(mod, '_detect_enabled', lambda: True)
    assert compilation_album_artist(ctx, 'Don Felder') == VARIOUS_ARTISTS


# ── the setting has to be reachable, not just readable ──

def _repo_root():
    from pathlib import Path
    return Path(__file__).resolve().parents[2]


def test_the_toggle_exists_in_the_settings_page():
    """It was config-only: the backend read it, both halves obeyed it, and
    there was no way for a user to see or change it (sassmastawillis went
    looking for it and there was nothing to find)."""
    html = (_repo_root() / 'webui' / 'index.html').read_text(encoding='utf-8')
    assert 'id="detect-multi-artist-compilations"' in html


def test_the_toggle_saves_and_loads_under_the_key_the_backend_reads():
    js = (_repo_root() / 'webui' / 'static' / 'settings.js').read_text(encoding='utf-8')
    assert ("detect_multi_artist_compilations: "
            "document.getElementById('detect-multi-artist-compilations').checked") in js
    # `!== false` and not `=== true`: the backend default is ON, so a config
    # that has never stored the key must still render the box ticked.
    assert "settings.file_organization?.detect_multi_artist_compilations !== false" in js


def test_the_backend_default_and_the_checkbox_default_agree():
    """A checkbox that renders unticked while the importer is doing the thing
    is worse than no checkbox."""
    from core.settings import config_manager
    import inspect
    defaults_src = inspect.getsource(type(config_manager))
    assert '"detect_multi_artist_compilations": True' in defaults_src or \
           "'detect_multi_artist_compilations': True" in defaults_src
    html = (_repo_root() / 'webui' / 'index.html').read_text(encoding='utf-8')
    block = html.split('id="detect-multi-artist-compilations"')[1][:60]
    assert 'checked' in block, 'markup default must match the backend default'
