"""#1072 (QT3496) — $artistletter '#' fallback for non-alphabetic starts.

One shared implementation (core.imports.paths.artist_letter) feeds all four
call sites — both template engines and the music-video path builder. Two
modes: literal (default, byte-identical to the historical first-character
extraction) and symbol-fallback (diacritics fold to their base letter —
Édith Piaf shelves under E, the iTunes/Plex convention — then digits,
symbols and non-Latin scripts land in one '#' folder).

Hermetic: isolated config via conftest; source pins keep the call sites on
the shared helper so the four copies can never diverge again.
"""

from __future__ import annotations

from pathlib import Path

from core.imports.paths import artist_letter

_ROOT = Path(__file__).resolve().parent.parent.parent


# ── literal mode: the historical behavior, byte for byte ────────────────────

def test_literal_mode_is_the_historical_extraction():
    assert artist_letter('Muse', False) == 'M'
    assert artist_letter('365 Days', False) == '3'
    assert artist_letter('!!!', False) == '!'
    assert artist_letter('Édith Piaf', False) == 'É'
    assert artist_letter('北京', False) == '北'
    assert artist_letter('', False) == 'U'          # the historical empty default
    assert artist_letter(None, False) == 'U'


# ── symbol-fallback mode ────────────────────────────────────────────────────

def test_fallback_groups_non_alphabetic_under_hash():
    assert artist_letter('365 Days', True) == '#'
    assert artist_letter('!!!', True) == '#'
    assert artist_letter("'68", True) == '#'
    assert artist_letter('北京', True) == '#'        # CJK
    assert artist_letter('Кино', True) == '#'        # Cyrillic
    assert artist_letter('平沢進', True) == '#'


def test_fallback_folds_diacritics_to_base_letters():
    assert artist_letter('Édith Piaf', True) == 'E'
    assert artist_letter('Ärzte', True) == 'A'
    assert artist_letter('Ólafur Arnalds', True) == 'O'
    assert artist_letter('ñu', True) == 'N'


def test_fallback_leaves_plain_letters_alone():
    assert artist_letter('Muse', True) == 'M'
    assert artist_letter('a tribe called quest', True) == 'A'


# ── config-driven default ───────────────────────────────────────────────────

def test_config_off_means_literal(tmp_path, monkeypatch):
    """No config value (every existing install) → literal, byte-identical."""
    assert artist_letter('Édith Piaf') == 'É'
    assert artist_letter('365 Days') == '3'


def test_config_toggle_flips_the_default():
    from config.settings import config_manager
    original = config_manager.get('file_organization.artistletter_symbol_fallback', None)
    try:
        config_manager.set('file_organization.artistletter_symbol_fallback', True)
        assert artist_letter('365 Days') == '#'
        assert artist_letter('Édith Piaf') == 'E'
        config_manager.set('file_organization.artistletter_symbol_fallback', False)
        assert artist_letter('365 Days') == '3'
    finally:
        config_manager.set('file_organization.artistletter_symbol_fallback', original if original is not None else False)


# ── the four call sites stay on the shared helper ───────────────────────────

def test_all_call_sites_use_the_shared_helper():
    paths_src = (_ROOT / 'core' / 'imports' / 'paths.py').read_text(encoding='utf-8')
    ws_src = (_ROOT / 'web_server.py').read_text(encoding='utf-8')
    assert paths_src.count('artist_letter(clean_context.get("artist", "U"))') == 2
    assert ws_src.count("_shared_artist_letter(clean_context.get('artist', 'U'))") == 2
    assert "_shared_artist_letter(safe_artist)" in ws_src
    # the raw extraction may never reappear at a call site
    assert "[0].upper()" not in paths_src.replace('literal = (artist or "U")[0].upper()', '')
    for needle in ("(clean_context.get('artist', 'U') or 'U')[0].upper()",
                   "safe_artist[0].upper()"):
        assert needle not in ws_src


def test_settings_ui_round_trip_wiring():
    js = (_ROOT / 'webui' / 'static' / 'settings.js').read_text(encoding='utf-8')
    html = (_ROOT / 'webui' / 'index.html').read_text(encoding='utf-8')
    assert 'id="artistletter-symbol-fallback"' in html
    assert "artistletter_symbol_fallback: document.getElementById('artistletter-symbol-fallback').checked" in js
    assert "settings.file_organization?.artistletter_symbol_fallback === true" in js
