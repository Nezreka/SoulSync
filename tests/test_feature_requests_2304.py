"""#2304: a member asks for a feature, an admin answers, and approving
switches it on."""

import sqlite3

import pytest

from core import feature_requests as fr


class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        return sqlite3.connect(self.path)


@pytest.fixture
def db(tmp_path):
    return _Db(str(tmp_path / "m.db"))


KID = {"id": 3, "name": "Kid"}
PERMS = {"podcasts.access": False, "music.explorer": False, "music.discover": True,
         "chat.use": False, "movies.max_rating": "PG"}


def test_what_can_be_asked_for_is_what_is_switched_off(db):
    out = fr.mine(db, 3, PERMS)
    assert [x["key"] for x in out["requestable"]] == ["podcasts.access", "music.explorer", "chat.use"]
    assert out["requestable"][0]["label"] == "Podcasts"
    assert out["pending"] == [] and out["answered"] == []


def test_asking_twice_is_one_request(db):
    first = fr.ask(db, KID, "podcasts.access", PERMS)
    again = fr.ask(db, KID, "podcasts.access", PERMS)
    assert first["id"] == again["id"] and first["status"] == "pending" and first["label"] == "Podcasts"
    assert fr.mine(db, 3, PERMS)["pending"] == ["podcasts.access"]
    assert [r["requester_name"] for r in fr.pending(db)] == ["Kid"]


@pytest.mark.parametrize("feature,status", [
    ("music.discover", 409),          # already theirs
    ("movies.max_rating", 400),       # a limit is not something to ask for here
    ("settings.admin", 400), ("", 400), (None, 400),
])
def test_what_cannot_be_asked_for(db, feature, status):
    with pytest.raises(fr.FeatureRequestError) as err:
        fr.ask(db, KID, feature, PERMS)
    assert err.value.status == status
    assert fr.pending(db) == []


def test_approving_switches_it_on_and_declining_does_not(db):
    granted = []
    a = fr.ask(db, KID, "podcasts.access", PERMS)
    b = fr.ask(db, KID, "chat.use", PERMS)
    out = fr.resolve(db, a["id"], approve=True, by=1, response="enjoy", grant=lambda pid, f: granted.append((pid, f)))
    assert granted == [(3, "podcasts.access")]
    assert out["status"] == "approved" and out["resolved_by"] == 1 and out["admin_response"] == "enjoy"
    fr.resolve(db, b["id"], approve=False, by=1, grant=lambda *_: granted.append("no"))
    assert granted == [(3, "podcasts.access")], "a decline grants nothing"
    assert fr.pending(db) == []
    # answered once: a second answer is refused
    with pytest.raises(fr.FeatureRequestError) as err:
        fr.resolve(db, a["id"], approve=False, by=1)
    assert err.value.status == 409
    mine = fr.mine(db, 3, {**PERMS, "podcasts.access": True})
    assert {x["feature"]: x["status"] for x in mine["answered"]} == {"podcasts.access": "approved", "chat.use": "declined"}
    assert "podcasts.access" not in [x["key"] for x in mine["requestable"]]


def test_a_grant_the_policy_refuses_leaves_the_request_waiting(db):
    a = fr.ask(db, KID, "podcasts.access", PERMS)

    def refused(_pid, _feature):
        raise RuntimeError("the policy doesn't allow podcasts")

    with pytest.raises(RuntimeError):
        fr.resolve(db, a["id"], approve=True, by=1, grant=refused)
    assert fr.get(db, a["id"])["status"] == "pending", "never recorded as approved for a permission not given"


def test_a_pile_of_asks_is_capped(db):
    perms = dict.fromkeys(fr.FEATURES, False)
    for key in list(fr.FEATURES)[:fr.MAX_PENDING]:
        fr.ask(db, KID, key, perms)
    with pytest.raises(fr.FeatureRequestError) as err:
        fr.ask(db, KID, list(fr.FEATURES)[fr.MAX_PENDING], perms)
    assert err.value.status == 429


def test_every_feature_is_a_real_permission():
    from core import permissions as P
    known = set(P.admin_map())
    assert set(fr.FEATURES) <= known, sorted(set(fr.FEATURES) - known)
