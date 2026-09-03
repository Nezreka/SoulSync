"""ntfy and Gotify as first-class automation notifications.

Both are staples of self-hosted setups and both were previously only reachable
by hand-writing a custom webhook payload template. Boulder asked for them
directly: "include the gotify and ntfy functionality for notifications /
automations. music and video side need it i bet."

They ride the shared automation engine, so the music and video sides get them
from the same implementation - there is no second dispatch to keep in step.

The two look interchangeable and are not, which is most of what these tests
pin: different priority scales, different auth, different places the token goes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.automation_engine import AutomationEngine


@pytest.fixture()
def engine():
    return AutomationEngine.__new__(AutomationEngine)


@pytest.fixture()
def sent(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append({'url': url, **kw})
        return SimpleNamespace(status_code=200, text='ok', json=lambda: {'ok': True})

    import core.automation_engine as mod
    monkeypatch.setattr(mod.requests, 'post', fake_post)
    return calls


VARS = {'name': 'Nightly Sync', 'status': 'completed'}


# ── ntfy ─────────────────────────────────────────────────────────────────────
def test_ntfy_posts_json_so_a_title_with_an_accent_survives(engine, sent):
    """The alternative is POST /<topic> with the title in a header, and headers
    have to be ASCII - an album called 'Bjork' with the accent would arrive
    mangled."""
    engine._send_ntfy_notification(
        {'topic': 'soulsync', 'title': '{name}', 'message': 'is {status}'}, VARS)

    assert sent[0]['url'] == 'https://ntfy.sh'
    body = sent[0]['json']
    assert body['topic'] == 'soulsync'
    assert body['title'] == 'Nightly Sync'
    assert body['message'] == 'is completed'


def test_ntfy_defaults_to_the_public_server_but_prefers_yours(engine, sent):
    engine._send_ntfy_notification({'topic': 't'}, VARS)
    assert sent[0]['url'] == 'https://ntfy.sh'

    engine._send_ntfy_notification({'topic': 't', 'server': 'https://ntfy.example.com/'}, VARS)
    assert sent[1]['url'] == 'https://ntfy.example.com'


def test_ntfy_assumes_https_when_the_scheme_is_left_off(engine, sent):
    engine._send_ntfy_notification({'topic': 't', 'server': 'ntfy.example.com'}, VARS)
    assert sent[0]['url'] == 'https://ntfy.example.com'


def test_ntfy_needs_a_topic(engine, sent):
    with pytest.raises(ValueError, match='topic'):
        engine._send_ntfy_notification({'server': 'https://ntfy.sh'}, VARS)
    assert sent == []


def test_ntfy_priority_is_clamped_to_its_own_1_to_5_scale(engine, sent):
    engine._send_ntfy_notification({'topic': 't', 'priority': 99}, VARS)
    assert sent[0]['json']['priority'] == 5
    engine._send_ntfy_notification({'topic': 't', 'priority': -4}, VARS)
    assert sent[1]['json']['priority'] == 1


def test_a_junk_priority_falls_back_to_the_service_default(engine, sent):
    """Better a notification at the wrong priority than no notification."""
    engine._send_ntfy_notification({'topic': 't', 'priority': 'urgent-ish'}, VARS)
    assert 'priority' not in sent[0]['json']


def test_ntfy_tags_are_split_and_trimmed(engine, sent):
    engine._send_ntfy_notification({'topic': 't', 'tags': 'white_check_mark, cd , '}, VARS)
    assert sent[0]['json']['tags'] == ['white_check_mark', 'cd']


def test_ntfy_token_beats_user_and_password(engine, sent):
    """ntfy resolves them in that order, so we must too - filling in both and
    getting basic auth would look like the token was ignored."""
    engine._send_ntfy_notification(
        {'topic': 't', 'token': 'tk_1', 'username': 'u', 'password': 'p'}, VARS)
    assert sent[0]['headers']['Authorization'] == 'Bearer tk_1'
    assert sent[0]['auth'] is None

    engine._send_ntfy_notification({'topic': 't', 'username': 'u', 'password': 'p'}, VARS)
    assert sent[1]['auth'] == ('u', 'p')
    assert 'Authorization' not in sent[1]['headers']


def test_ntfy_sends_no_auth_at_all_when_none_is_configured(engine, sent):
    """A private topic on your own box usually has none, and sending an empty
    Bearer header is worse than sending nothing."""
    engine._send_ntfy_notification({'topic': 't'}, VARS)
    assert sent[0]['headers'] == {}
    assert sent[0]['auth'] is None


def test_ntfy_reports_a_rejection_instead_of_swallowing_it(engine, monkeypatch):
    import core.automation_engine as mod
    monkeypatch.setattr(mod.requests, 'post', lambda url, **kw: SimpleNamespace(
        status_code=403, text='forbidden'))
    with pytest.raises(RuntimeError, match='403'):
        engine._send_ntfy_notification({'topic': 't'}, VARS)


# ── Gotify ───────────────────────────────────────────────────────────────────
def test_gotify_puts_the_token_in_the_query_not_a_header(engine, sent):
    """/message reads it from the query string and nowhere else."""
    engine._send_gotify_notification(
        {'server': 'https://gotify.example.com', 'token': 'A1b2', 'message': '{status}'}, VARS)

    assert sent[0]['url'] == 'https://gotify.example.com/message'
    assert sent[0]['params'] == {'token': 'A1b2'}
    assert sent[0]['json']['message'] == 'completed'


def test_gotify_requires_both_a_server_and_a_token(engine, sent):
    with pytest.raises(ValueError, match='server'):
        engine._send_gotify_notification({'token': 'x'}, VARS)
    with pytest.raises(ValueError, match='token'):
        engine._send_gotify_notification({'server': 'https://g.example.com'}, VARS)
    assert sent == []


def test_gotify_priority_is_0_to_10_not_ntfys_1_to_5(engine, sent):
    """The two scales look alike and are not. Clamping Gotify to 5 would cap
    every message at half its real urgency."""
    engine._send_gotify_notification({'server': 'g.io', 'token': 't', 'priority': 8}, VARS)
    assert sent[0]['json']['priority'] == 8
    engine._send_gotify_notification({'server': 'g.io', 'token': 't', 'priority': 44}, VARS)
    assert sent[1]['json']['priority'] == 10
    engine._send_gotify_notification({'server': 'g.io', 'token': 't', 'priority': 'x'}, VARS)
    assert sent[2]['json']['priority'] == 5


def test_gotify_assumes_http_for_a_bare_host(engine, sent):
    """Gotify is nearly always a box on the LAN, so a scheme-less host is far
    more likely to be plain http than a public https endpoint."""
    engine._send_gotify_notification({'server': 'nas.local:8080', 'token': 't'}, VARS)
    assert sent[0]['url'] == 'http://nas.local:8080/message'


def test_gotify_reports_a_rejection(engine, monkeypatch):
    import core.automation_engine as mod
    monkeypatch.setattr(mod.requests, 'post', lambda url, **kw: SimpleNamespace(
        status_code=401, text='unauthorized'))
    with pytest.raises(RuntimeError, match='401'):
        engine._send_gotify_notification({'server': 'g.io', 'token': 'bad'}, VARS)


# ── both are reachable from the shared dispatch ──────────────────────────────
def test_both_are_wired_into_the_then_action_dispatch():
    """The engine is shared by the music and video sides, so being in this
    dispatch is what gives both of them the feature."""
    import inspect
    src = inspect.getsource(AutomationEngine._execute_then_actions)
    assert "t == 'ntfy'" in src
    assert "t == 'gotify'" in src
