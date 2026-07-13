"""Plex incremental delta — the episode-level fallback that catches newly-imported episodes
even when Plex doesn't bump the parent show's updatedAt. Pure: the Plex section is mocked."""

from __future__ import annotations

import datetime

from core.video.sources import _union_episode_delta_shows, _union_jf_episode_delta_series


class _Ep:
    def __init__(self, gk):
        self.grandparentRatingKey = gk


class _Show:
    def __init__(self, rk):
        self.ratingKey = rk


class _Section:
    def __init__(self, eps_by_filter, shows_by_key):
        self._eps = eps_by_filter            # {'addedAt': [...], 'updatedAt': [...]}
        self._shows = shows_by_key           # {ratingKey:int -> _Show}
        self.searched, self.fetched = [], []

    def search(self, libtype=None, filters=None, sort=None, maxresults=None):
        assert libtype == "episode"
        key = "addedAt" if "addedAt>>" in filters else "updatedAt"
        self.searched.append(key)
        return self._eps.get(key, [])

    def fetchItem(self, rk):
        self.fetched.append(rk)
        return self._shows[int(rk)]


def test_union_adds_parent_shows_of_new_episodes():
    existing = [_Show(100)]                   # show-level delta already found show 100
    sec = _Section(
        eps_by_filter={"addedAt": [_Ep("200"), _Ep("100")], "updatedAt": [_Ep("300")]},
        shows_by_key={200: _Show(200), 300: _Show(300)})
    out = _union_episode_delta_shows(sec, "since", existing)
    assert sorted(str(s.ratingKey) for s in out) == ["100", "200", "300"]   # 100 kept, 200+300 added
    assert sec.searched == ["addedAt", "updatedAt"]                          # both signals queried
    assert sorted(sec.fetched) == [200, 300]                                # only the NEW shows fetched


def test_union_is_best_effort_when_search_explodes():
    class _Boom:
        def search(self, **kw):
            raise RuntimeError("plex down")

        def fetchItem(self, rk):
            raise AssertionError("must not fetch")

    out = _union_episode_delta_shows(_Boom(), "since", [_Show(1)])
    assert [s.ratingKey for s in out] == [1]                                # unchanged, no crash


def test_union_skips_a_show_it_cannot_refetch():
    class _Sec2:
        def search(self, libtype=None, filters=None, sort=None, maxresults=None):
            return [_Ep("999")] if "addedAt>>" in filters else []

        def fetchItem(self, rk):
            raise RuntimeError("gone")

    out = _union_episode_delta_shows(_Sec2(), "since", [_Show(5)])
    assert [s.ratingKey for s in out] == [5]                                # 999 unfetchable → dropped


# ── Jellyfin twin ─────────────────────────────────────────────────────────────
_SINCE = datetime.datetime(2026, 7, 1, 12, 0, 0)


def test_jf_union_adds_parent_series_of_new_episodes():
    calls = []

    def req(path, params):
        calls.append(params)
        if params.get("IncludeItemTypes") == "Episode":
            # episodes saved since the baseline → series 200 (new) + 100 (already in delta)
            return {"Items": [{"SeriesId": "200"}, {"SeriesId": "100"}]}
        if params.get("Ids"):
            return {"Items": [{"Id": sid, "Name": "S" + sid} for sid in params["Ids"].split(",")]}
        return {}

    existing = [{"Id": "100", "Name": "Already"}]
    out = _union_jf_episode_delta_series(req, "u1", "view1", _SINCE, existing, "Fields")
    assert sorted(s["Id"] for s in out) == ["100", "200"]        # 100 kept, 200 added, no dup
    ep_call = next(c for c in calls if c.get("IncludeItemTypes") == "Episode")
    assert ep_call["MinDateLastSaved"] == _SINCE.isoformat() and ep_call["Recursive"] == "true"


def test_jf_union_no_new_episodes_makes_no_fetch():
    def req(path, params):
        return {"Items": []} if params.get("IncludeItemTypes") == "Episode" else \
            (_ for _ in ()).throw(AssertionError("must not fetch series"))

    out = _union_jf_episode_delta_series(req, "u1", "v", _SINCE, [{"Id": "1"}], "F")
    assert [s["Id"] for s in out] == ["1"]


def test_jf_union_best_effort_on_error():
    def req(path, params):
        raise RuntimeError("jellyfin down")

    out = _union_jf_episode_delta_series(req, "u1", "v", _SINCE, [{"Id": "9"}], "F")
    assert [s["Id"] for s in out] == ["9"]                       # unchanged, no crash
