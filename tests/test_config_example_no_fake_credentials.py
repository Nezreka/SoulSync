"""#1124 — the shipped `config.example.json` must not carry fake-but-truthy
credentials.

`Dockerfile` copies `config/config.example.json` to `/defaults/config.json`, and
`entrypoint.sh` copies THAT to `/app/config/config.json` the first time a
container starts with an empty config volume. So this file is the literal
starting config for every Docker install — not documentation.

Every "is X configured?" check in the app is a plain truthiness test (see
`ConfigManager.validate_config` / `is_configured`). Placeholder strings like
`"JELLYFIN_API_KEY"` and `"http://localhost:8096"` are truthy, so a fresh
install reported every service as configured and then tried to actually reach
them — which is how #1124 got a `Connection refused` against
`http://localhost:8096` from a user who had never configured Jellyfin at all.

The in-code defaults (`ConfigManager._get_default_config`) use empty strings for
exactly this reason; the example file must agree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "config.example.json"


@pytest.fixture(scope="module")
def example() -> dict:
    with EXAMPLE.open(encoding="utf-8") as fh:
        return json.load(fh)


# (section, key) pairs every truthiness-based "configured" check reads.
CREDENTIAL_FIELDS = [
    ("spotify", "client_id"),
    ("spotify", "client_secret"),
    ("tidal", "client_id"),
    ("tidal", "client_secret"),
    ("plex", "base_url"),
    ("plex", "token"),
    ("jellyfin", "base_url"),
    ("jellyfin", "api_key"),
    ("navidrome", "base_url"),
    ("navidrome", "username"),
    ("navidrome", "password"),
    ("soulseek", "api_key"),
    ("listenbrainz", "token"),
]


@pytest.mark.parametrize(("section", "key"), CREDENTIAL_FIELDS)
def test_credential_fields_ship_empty(example, section, key):
    """A falsy value is what makes `validate_config` correctly report the
    service as unconfigured on a fresh install."""
    value = (example.get(section) or {}).get(key, "")
    assert not value, (
        f"config.example.json ships {section}.{key}={value!r}. This is the seed "
        f"config for every Docker install, and every 'configured' check is a "
        f"truthiness test — a non-empty value makes SoulSync treat an "
        f"unconfigured {section} as real and try to connect to it (#1124)."
    )


def test_no_media_server_looks_configured(example):
    """Reproduces the exact checks in `ConfigManager.validate_config`."""
    def cfg(section):
        return example.get(section) or {}

    assert not (cfg("plex").get("base_url") and cfg("plex").get("token"))
    assert not (cfg("jellyfin").get("base_url") and cfg("jellyfin").get("api_key"))
    assert not (
        cfg("navidrome").get("base_url")
        and cfg("navidrome").get("username")
        and cfg("navidrome").get("password")
    )
    assert not (cfg("spotify").get("client_id") and cfg("spotify").get("client_secret"))


def test_no_private_lan_addresses_leak(example):
    """The example previously shipped a real home-LAN Plex address. Nothing in
    a public seed config should point at a specific private host."""
    blob = json.dumps(example)
    for leaked in ("192.168.", "10.0.0.", "172.16."):
        assert leaked not in blob, (
            f"config.example.json contains {leaked!r} — a private LAN address "
            f"must not ship as a default."
        )


def test_structural_defaults_are_preserved(example):
    """The blanking must not have taken genuine defaults with it — these are
    correct values, not placeholders."""
    assert example["spotify"]["redirect_uri"] == "http://127.0.0.1:8888/callback"
    assert example["tidal"]["redirect_uri"] == "http://127.0.0.1:8889/tidal/callback"
    assert example["soulseek"]["download_path"] == "/app/downloads"
    assert example["soulseek"]["transfer_path"] == "/app/Transfer"
    # slskd_url is the correct host for the documented Docker setup, not a
    # fake credential — it stays.
    assert example["soulseek"]["slskd_url"] == "http://host.docker.internal:5030"
    assert example["file_organization"]["templates"]["album_path"]
