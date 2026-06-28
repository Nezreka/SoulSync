import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.spotify_client import normalize_spotify_oauth_config

def test_normalization():
    # Whitespace + quotes are stripped (paste garbage); the redirect_uri's
    # trailing slash is PRESERVED — Spotify matches it exactly against the app
    # dashboard, so stripping it could break a valid registration (#942 follow-up).
    config = {
        "client_id": '  "client_id"   ',
        "client_secret": "  client_secret  ",
        "redirect_uri": "  http://127.0.0.1:8888/callback/  "
    }
    expected = {
        "client_id": "client_id",
        "client_secret": "client_secret",
        "redirect_uri": "http://127.0.0.1:8888/callback/"   # slash kept
    }
    assert normalize_spotify_oauth_config(config) == expected


def test_trailing_slash_on_redirect_uri_is_preserved():
    """Regression guard: Spotify requires an EXACT redirect-URI match against the
    app dashboard, so a trailing slash a user registered must NOT be stripped —
    stripping it would send '…/callback' and trigger INVALID_CLIENT (#942)."""
    with_slash = {"client_id": "x", "client_secret": "y",
                  "redirect_uri": "http://127.0.0.1:8888/callback/"}
    without_slash = {"client_id": "x", "client_secret": "y",
                     "redirect_uri": "http://127.0.0.1:8888/callback"}
    assert normalize_spotify_oauth_config(with_slash)["redirect_uri"] == "http://127.0.0.1:8888/callback/"
    assert normalize_spotify_oauth_config(without_slash)["redirect_uri"] == "http://127.0.0.1:8888/callback"

def test_empty_values():
    # Empty input values
    config = {
        "client_id": "",
        "client_secret": None,
        "redirect_uri": ""
    }
    # When value is None, it falls into the else branch: normalized[key] = value
    # value is None, so expected is None for client_secret
    expected = {
        "client_id": "",
        "client_secret": None,
        "redirect_uri": ""
    }
    assert normalize_spotify_oauth_config(config) == expected

def test_missing_keys():
    # Input dictionary with missing keys
    config = {
        "client_id": "client_id"
    }
    # .get(key, "") means missing keys become ""
    expected = {
        "client_id": "client_id",
        "client_secret": "",
        "redirect_uri": ""
    }
    assert normalize_spotify_oauth_config(config) == expected

def test_non_string_values():
    # Input dictionary with non-string values for the keys
    config = {
        "client_id": 123,
        "client_secret": True,
        "redirect_uri": None
    }
    # When value is not a string, it falls into the else branch: normalized[key] = value
    expected = {
        "client_id": 123,
        "client_secret": True,
        "redirect_uri": None
    }
    assert normalize_spotify_oauth_config(config) == expected

def test_no_input():
    # Empty input dictionary
    config = {}
    # .get(key, "") means missing keys become ""
    expected = {
        "client_id": "",
        "client_secret": "",
        "redirect_uri": ""
    }
    assert normalize_spotify_oauth_config(None) == {}
    assert normalize_spotify_oauth_config(config) == expected