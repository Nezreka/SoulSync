"""ConfigManager.resolve_secret — test the EFFECTIVE secret, not the mask (#870).

The settings UI renders a saved-but-untouched secret as the redaction sentinel
(shown as dots). The Deezer ARL connection test was sending that sentinel as the
token, so Deezer rejected it ("Invalid ARL token — USER_ID=0") and it looked like
the ARL kept resetting — even though the saved value was fine. resolve_secret maps
an empty/sentinel posted value back to the stored token.
"""

from __future__ import annotations

from config.settings import ConfigManager

S = ConfigManager.REDACTED_SENTINEL


def _cm(config_data):
    cm = object.__new__(ConfigManager)  # bypass __init__/DB — get() reads config_data
    cm.config_data = config_data
    return cm


def test_sentinel_resolves_to_stored():
    cm = _cm({'deezer_download': {'arl': 'real_arl_123'}})
    assert cm.resolve_secret('deezer_download.arl', S) == 'real_arl_123'


def test_empty_or_none_resolves_to_stored():
    cm = _cm({'deezer_download': {'arl': 'real_arl_123'}})
    assert cm.resolve_secret('deezer_download.arl', '') == 'real_arl_123'
    assert cm.resolve_secret('deezer_download.arl', '   ') == 'real_arl_123'
    assert cm.resolve_secret('deezer_download.arl', None) == 'real_arl_123'


def test_real_value_passes_through_trimmed():
    cm = _cm({'deezer_download': {'arl': 'old'}})
    assert cm.resolve_secret('deezer_download.arl', 'new_token') == 'new_token'
    assert cm.resolve_secret('deezer_download.arl', '  spaced  ') == 'spaced'


def test_sentinel_with_nothing_stored_returns_empty():
    cm = _cm({})
    assert cm.resolve_secret('deezer_download.arl', S) == ''
