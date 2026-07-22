"""#1056 — one user-set override for streaming-source search timeouts.

Unset/0/junk = None = every source keeps its own built-in default (HiFi/Qobuz
15s, Deezer 10s, stream search 15s) — so shipping this changes NOTHING until a
user types a number. Clamped 5-120. Soulseek is explicitly NOT governed (its
windowed soulseek.search_timeout has different semantics and its own UI field).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import ConfigManager

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def cfg(monkeypatch):
    c = ConfigManager.__new__(ConfigManager)
    store = {}
    monkeypatch.setattr(c, 'get', lambda k, d=None: store.get(k, d), raising=False)
    c._store = store
    return c


@pytest.mark.parametrize('raw,expected', [
    (None, None), (0, None), ('', None), ('junk', None), (-5, None),
    (45, 45), ('45', 45), (45.7, 45),
    (3, 5),          # clamp floor
    (500, 120),      # clamp ceiling
])
def test_helper_coercion_and_clamps(cfg, raw, expected):
    if raw is not None:
        cfg._store['download_source.source_search_timeout'] = raw
    assert cfg.get_source_search_timeout() == expected


def test_unset_means_none_not_fifteen(cfg):
    """None (not 15) is the contract: each call site supplies its OWN historical
    default via `or`, so Deezer keeps 10s while HiFi/Qobuz keep 15s."""
    assert cfg.get_source_search_timeout() is None


# ── seam pins: each site uses override-or-its-own-historical-default ─────────

def _src(rel):
    return (_ROOT / rel).read_text(encoding='utf-8')


def test_hifi_default_resolves_override_or_15():
    s = _src('core/hifi_client.py')
    assert 'timeout = config_manager.get_source_search_timeout() or 15' in s
    # explicit-timeout callers (20s manifest/track fetches) are preserved
    assert 'timeout=20' in s


def test_qobuz_uses_override_or_15():
    assert 'config_manager.get_source_search_timeout() or 15' in _src('core/qobuz_client.py')


def test_deezer_keeps_its_own_10s_default():
    s = _src('core/deezer_download_client.py')
    assert "get_source_search_timeout', lambda: None)() or 10" in s


def test_stream_search_uses_override_or_15():
    s = _src('core/search/stream.py')
    assert "get_source_search_timeout', lambda: None)() or 15" in s
    assert 'timeout=search_timeout' in s


def test_soulseek_is_untouched():
    """soulseek.search_timeout stays the ONLY timeout the slskd client reads."""
    s = _src('core/soulseek_client.py')
    assert 'get_source_search_timeout' not in s
    assert "config_manager.get('soulseek.search_timeout', 60)" in s


def test_settings_ui_wired():
    assert 'id="source-search-timeout"' in _src('webui/index.html')
    js = _src('webui/static/settings.js')
    assert 'source_search_timeout' in js
