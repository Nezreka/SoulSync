"""One Subsonic client name, spelled one way (#2005).

Navidrome keys a user's player entries by the client name a Subsonic
request carries in its ``c`` parameter (for the same user and user
agent, the name is the differentiator). The main Navidrome client has
always sent ``c=SoulSync``, but the Settings "test connection" ping —
which runs with the user's real Navidrome account — sent ``c=soulsync``,
so testing a connection left a second, duplicate player in Navidrome
("soulsync [python-requests]" next to "SoulSync [python-requests]",
same username): exactly the screenshot on #2005. The network
auto-detect probe sent the lowercase spelling too (its dummy user can't
mint a player, but the name is now consistent), and the onboarding
fingerprint ping sent no client name at all.

What is pinned here, honestly scoped:

- every LITERAL client name in the backend Python trees (core/, api/,
  services/, database/, utils/, scripts/, tools/, web_server.py and
  the root modules) is the one spelling — dict params, subscript
  assignments, kwarg values and ``c=`` auth strings are all scanned.
  The webui is out of scope: it contains no Subsonic client name of
  its own. (The browser can still end up loading a signed ``c=``
  URL the backend hands it — api/stats and the artwork proxy pass
  external Navidrome URLs through — and Navidrome then sees the
  browser's own user agent. That is a separate, pre-existing player
  shape with the correct name, not the #2005 duplicate, which was
  same-user, same-agent, differently-cased name.);
- the files that send the name today must keep sending it: the scan
  asserts each known sender file still carries it, so DELETING the
  parameter in a refactor fails here, not just re-spelling it;
- the main client's request funnel cannot be re-spelled dynamically:
  ``_make_request`` forces the name after merging caller params, on
  both the GET and POST paths and through the per-user view. The
  client's URL builders bypass that funnel; they are pinned by their
  literals, the scan, and behavior tests below.

What a source scan cannot pin is a name built from a variable outside
the funnel (an f-string interpolation, a constant defined elsewhere).
No such sender exists today. The scan also reads raw text, comments
included, and would flag an unrelated single-letter ``c`` literal in a
scanned tree — none exists today, and a future one would need the
name or a deliberate exemption here, which is the point of a pin.

Note: the duplicate player this bug already minted on a user's
Navidrome stays there until they delete it in Navidrome's own Players
settings; this change stops new ones appearing.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from core import connection_test

ROOT = Path(__file__).resolve().parents[2]

CLIENT_NAME = "SoulSync"

# 'c': 'X' / "c": "X" dict params
_DICT_PARAM = re.compile(r"""['"]c['"]\s*:\s*['"]([^'"]+)['"]""")
# params['c'] = 'X' subscript assignment
_SUBSCRIPT_PARAM = re.compile(r"""\[\s*['"]c['"]\s*\]\s*=\s*['"]([^'"]+)['"]""")
# c='X' kwarg / assignment value
_KWARG_PARAM = re.compile(r"""\bc\s*=\s*['"]([^'"]+)['"]""")
# c=X inside a hand-built auth string (after ?, & or an opening quote)
_URL_PARAM = re.compile(r"""[?&"']c=([A-Za-z0-9%_.\-]+)""")

_PATTERNS = (_DICT_PARAM, _SUBSCRIPT_PARAM, _KWARG_PARAM, _URL_PARAM)

_SCAN_ROOTS = ("core", "api", "services", "database", "utils", "scripts",
               "tools")

# The one table: exact client-name site counts for every file that
# sends the name today. A site deleted from (or added to) one of these
# files fails here even if the file still carries the name elsewhere —
# this is what catches a refactor that drops the parameter entirely,
# which a spelling scan alone cannot see (connection_detect's probe is
# a closure inside run_detection with no behavior test; this table is
# its pin). A legitimate new sender or site means updating this table
# deliberately, in the same change.
_EXACT_SITE_COUNTS = {
    "core/connection_test.py": 1,
    "core/connection_detect.py": 1,
    "core/onboarding/discovery.py": 1,
    "core/onboarding/probes.py": 1,
    "core/metadata/artwork.py": 1,
    "api/stats.py": 1,
    "core/navidrome_client.py": 5,
}


def _backend_files() -> list[Path]:
    files: set[Path] = set()
    for base in _SCAN_ROOTS:
        files.update((ROOT / base).rglob("*.py"))
    files.update(ROOT.glob("*.py"))
    return sorted(files)


def _client_names_in(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    names: list[str] = []
    for pattern in _PATTERNS:
        names.extend(pattern.findall(src))
    return names


def test_every_literal_subsonic_client_name_in_the_backend_is_the_one_name():
    offenders = {}
    names_by_file = {}
    for path in _backend_files():
        names = _client_names_in(path)
        if names:
            names_by_file[str(path.relative_to(ROOT))] = names
        bad = [n for n in names if n != CLIENT_NAME]
        if bad:
            offenders[str(path.relative_to(ROOT))] = bad
    assert offenders == {}
    # The pin must not go vacuous AND must not lose a known sender:
    # every known sender still carries exactly its sites, all spelled
    # the one way — dropping the parameter is the same regression as
    # re-spelling it, and an extra literal cannot mask a deletion.
    for rel, count in _EXACT_SITE_COUNTS.items():
        found = names_by_file.get(rel) or []
        assert found == [CLIENT_NAME] * count, (
            f"{rel} should carry exactly {count} client-name site(s) "
            f"spelled {CLIENT_NAME!r}, found {found}")


def test_connection_test_pings_navidrome_under_the_main_client_name(monkeypatch):
    """The reported path: Settings → Test connection for Navidrome."""
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"] = url
        seen["params"] = params
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"subsonic-response": {"status": "ok", "version": "1.16.1"}},
        )

    monkeypatch.setattr(connection_test.requests, "get", fake_get)
    # run_service_test resolves URLs and stages config through helpers the
    # web layer injects at runtime; stand them in as no-ops (the same
    # patching pattern as tests/test_service_test_buttons.py).
    monkeypatch.setattr(connection_test, "docker_resolve_url", lambda v: v)
    monkeypatch.setattr(connection_test.config_manager, "get",
                        lambda k, d=None: {})
    monkeypatch.setattr(connection_test.config_manager, "set",
                        lambda k, v: None)
    ok, _msg = connection_test.run_service_test("navidrome", {
        "base_url": "http://navidrome.local:4533",
        "username": "someuser",
        "password": "secret",
    })

    assert ok is True
    assert seen["url"].endswith("/rest/ping")
    assert seen["params"]["c"] == CLIENT_NAME


def test_main_navidrome_client_auth_params_use_the_one_name():
    from core.navidrome_client import NavidromeClient

    client = NavidromeClient.__new__(NavidromeClient)
    client.username = "someuser"
    client.password = "secret"
    params = client._generate_auth_params()
    assert params["c"] == CLIENT_NAME


def _fake_navidrome_client(monkeypatch):
    """A NavidromeClient wired to a capturing fake HTTP session."""
    import core.navidrome_client as nav

    sent = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"subsonic-response": {"status": "ok"}}

    class _Session:
        def get(self, url, params=None, timeout=None):
            sent.clear()
            sent.update(params or {})
            sent["_method"] = "get"
            return _Resp()

        def post(self, url, data=None, timeout=None):
            sent.clear()
            sent.update(data or {})
            sent["_method"] = "post"
            return _Resp()

    monkeypatch.setattr(nav, "_http", lambda: _Session())

    client = nav.NavidromeClient.__new__(nav.NavidromeClient)
    client.base_url = "http://navidrome.local:4533"
    client.username = "someuser"
    client.password = "secret"
    client.last_api_error = None
    return nav, client, sent


def test_make_request_cannot_be_respelled_by_caller_params(monkeypatch):
    """The funnel is structural, not a convention: a caller passing its
    own ``c`` in the endpoint params must not change what is sent — on
    the GET path or the POST/write path."""
    _nav, client, sent = _fake_navidrome_client(monkeypatch)

    result = client._make_request("ping", params={"c": "soulsync"})
    assert result == {"status": "ok"}
    assert sent["_method"] == "get"
    assert sent["c"] == CLIENT_NAME

    result = client._make_request("createPlaylist", params={"c": "soulsync"})
    assert result == {"status": "ok"}
    assert sent["_method"] == "post", "createPlaylist must take the POST path"
    assert sent["c"] == CLIENT_NAME


def test_user_view_requests_carry_the_one_name_too(monkeypatch):
    """The per-user view delegates to the base client's funnel, so the
    force must reach its traffic as the bound user."""
    nav, client, sent = _fake_navidrome_client(monkeypatch)

    view = nav.NavidromeUserView(client, "otheruser", "otherpass")
    result = view._make_request("ping", params={"c": "soulsync"})
    assert result == {"status": "ok"}
    assert sent["c"] == CLIENT_NAME
    assert sent["u"] == "otheruser"


def test_onboarding_fingerprint_ping_carries_the_one_name():
    from core.onboarding import discovery

    seen = {}

    def fake_get(url, params=None):
        seen["url"] = url
        seen["params"] = params
        return SimpleNamespace(
            status_code=200, text='{"subsonic-response": {"status": "ok"}}')

    assert discovery._fingerprint(
        "navidrome", "http://navidrome.local:4533", fake_get) is True
    assert seen["url"].endswith("/rest/ping")
    assert seen["params"]["c"] == CLIENT_NAME


def test_client_url_builders_carry_the_one_name(monkeypatch):
    """The cover/stream/image URL builders bypass _make_request; their
    URLs must still carry the one name (asserted on the built URL, so
    dropping the parameter fails here too)."""
    _nav, client, _sent = _fake_navidrome_client(monkeypatch)

    assert f"c={CLIENT_NAME}" in client.build_cover_art_url("cov1")
    assert f"c={CLIENT_NAME}" in client.build_stream_url("trk1")
    assert f"c={CLIENT_NAME}" in client.image_url("/rest/getAlbum?id=alb1")
