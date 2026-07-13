"""Plex incremental delta — the episode-level fallback that catches newly-imported episodes
even when Plex doesn't bump the parent show's updatedAt. Pure: the Plex section is mocked."""

from __future__ import annotations

from core.video.sources import _union_episode_delta_shows


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
