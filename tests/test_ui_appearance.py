"""Pure UI-appearance default rules (core/ui_appearance.py).

Pins the worker-orbs default contract: explicit saved choice ALWAYS wins; when unset,
default OFF on Firefox (the blurred orb canvas is the main remaining Firefox lag
source) and ON elsewhere."""

from core.ui_appearance import is_firefox_user_agent, resolve_worker_orbs_default

_FIREFOX_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
               "Gecko/20100101 Firefox/128.0")
_CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_SAFARI_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
_EDGE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0")


# ── is_firefox_user_agent ──

def test_detects_firefox():
    assert is_firefox_user_agent(_FIREFOX_UA) is True


def test_non_firefox_browsers_are_false():
    for ua in (_CHROME_UA, _SAFARI_UA, _EDGE_UA):
        assert is_firefox_user_agent(ua) is False


def test_empty_or_none_ua_is_not_firefox():
    assert is_firefox_user_agent('') is False
    assert is_firefox_user_agent(None) is False


# ── resolve_worker_orbs_default: explicit ALWAYS wins ──

def test_unset_defaults_off_on_firefox():
    assert resolve_worker_orbs_default(None, is_firefox=True) is False


def test_unset_defaults_on_elsewhere():
    assert resolve_worker_orbs_default(None, is_firefox=False) is True


def test_explicit_true_wins_even_on_firefox():
    # A Firefox user who explicitly enabled orbs keeps them — default never overrides.
    assert resolve_worker_orbs_default(True, is_firefox=True) is True


def test_explicit_false_wins_even_off_firefox():
    assert resolve_worker_orbs_default(False, is_firefox=False) is False


def test_explicit_values_ignore_browser():
    assert resolve_worker_orbs_default(True, is_firefox=False) is True
    assert resolve_worker_orbs_default(False, is_firefox=True) is False
