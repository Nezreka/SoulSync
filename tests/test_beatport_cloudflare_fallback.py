"""the beatport scraper's cloudflare escape hatch.

beatport 403s plain requests sessions. with flaresolverr configured the
scraper solves once, serves the rendered page, and rides the clearance
cookies on later requests. without flaresolverr it must behave exactly
as it always has - blocked response returned untouched, solver never
consulted.
"""

import pytest

import beatport_unified_scraper as bus


class _FakeResponse:
    def __init__(self, status_code=200, content=b"<html>direct</html>"):
        self.status_code = status_code
        self.content = content
        self.text = content.decode()

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} for url")


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.cookies = _FakeCookies()
        self.headers = {}

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self.responses.pop(0) if self.responses else _FakeResponse(403)


class _FakeCookies:
    def __init__(self):
        self.jar = {}

    def set(self, name, value):
        self.jar[name] = value


class _NeverClient:
    def __init__(self, *a, **k):
        raise AssertionError("flaresolverr must not be consulted")


class _SolvingClient:
    instances = 0

    last_session_id = None

    def __init__(self, base_url, timeout=30, session_id=""):
        type(self).instances += 1
        type(self).last_session_id = session_id
        self.session_id = session_id
        self.last_cookies = {"cf_clearance": "tok"}
        self.last_user_agent = "solved-ua"

    def request(self, method, url, data=None):
        return 200, "<html><div class='partial-artwork'>solved</div></html>", url


@pytest.fixture(autouse=True)
def _fresh_clearance(monkeypatch):
    monkeypatch.setattr(bus, "_cf_clearance", {"at": 0.0, "cookies": {}, "ua": ""})
    _SolvingClient.instances = 0


def _scraper(monkeypatch, responses, solver="http://flare:8191", client=_SolvingClient):
    s = bus.BeatportUnifiedScraper()
    monkeypatch.setattr(s, "session", _FakeSession(responses))
    monkeypatch.setattr(bus, "flaresolverr_url", lambda: solver)
    monkeypatch.setattr(bus, "FlareSolverrClient", client)
    return s


def test_a_direct_200_never_touches_the_solver(monkeypatch):
    s = _scraper(monkeypatch, [_FakeResponse(200)], client=_NeverClient)
    r = s._get("https://beatport.com/x")
    assert r.status_code == 200 and r.content == b"<html>direct</html>"


def test_without_flaresolverr_the_403_surfaces_exactly_as_before(monkeypatch):
    s = _scraper(monkeypatch, [_FakeResponse(403)], solver="", client=_NeverClient)
    r = s._get("https://beatport.com/x")
    assert r.status_code == 403
    assert s.session.calls == ["https://beatport.com/x"]
    # and get_page turns that into None, the shape every caller handles
    assert s.get_page("https://beatport.com/x") is None


def test_a_403_with_flaresolverr_serves_the_rendered_page(monkeypatch):
    s = _scraper(monkeypatch, [_FakeResponse(403)])
    soup = s.get_page("https://beatport.com/genre/x/1")
    assert soup is not None
    assert soup.find(class_="partial-artwork").get_text() == "solved"
    # the clearance rides the session for later direct requests
    assert s.session.cookies.jar == {"cf_clearance": "tok"}
    assert s.session.headers["User-Agent"] == "solved-ua"
    assert _SolvingClient.instances == 1


def test_a_cached_clearance_is_tried_before_paying_for_a_new_solve(monkeypatch):
    first = _scraper(monkeypatch, [_FakeResponse(403)])
    first._get("https://beatport.com/a")
    assert _SolvingClient.instances == 1
    # a second scraper hits a 403, applies the cached clearance, and its
    # direct retry succeeds - no second browser solve
    second = _scraper(monkeypatch, [_FakeResponse(403), _FakeResponse(200)])
    r = second._get("https://beatport.com/b")
    assert r.status_code == 200
    assert _SolvingClient.instances == 1
    assert second.session.cookies.jar == {"cf_clearance": "tok"}


def test_a_failed_solve_returns_the_original_block(monkeypatch):
    class _Exploding:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            raise RuntimeError("challenge timeout")

    s = _scraper(monkeypatch, [_FakeResponse(403)], client=_Exploding)
    r = s._get("https://beatport.com/x")
    assert r.status_code == 403


def test_a_solve_that_still_blocks_returns_the_original_block(monkeypatch):
    class _StillBlocked(_SolvingClient):
        def request(self, method, url, data=None):
            return 403, "<html>denied</html>", url

    s = _scraper(monkeypatch, [_FakeResponse(403)], client=_StillBlocked)
    r = s._get("https://beatport.com/x")
    assert r.status_code == 403
    assert not isinstance(r, bus._SolvedResponse)


def test_the_solver_session_is_beatports_own(monkeypatch):
    # its own browser session id, so a poisoned ext.to session never costs
    # beatport a re-solve (and vice versa)
    s = _scraper(monkeypatch, [_FakeResponse(403)])
    s._get("https://beatport.com/x")
    assert _SolvingClient.last_session_id == "soulsync-beatport"
