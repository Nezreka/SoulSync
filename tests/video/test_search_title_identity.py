"""A manual episode search named no title, so it was judged by the Default
profile (SeadogsBooty: a 3 GB episode cap showed 4 GB releases as meeting the
profile) and searched the indexers by text alone, missing releases the
indexers had. The show page names its show by library id; the server reads
the profile and the tvdb/imdb/tmdb ids from that row."""

from __future__ import annotations

from types import SimpleNamespace

import api.video.downloads as dl


class _DB:
    def __init__(self):
        self.asked = []

    def search_ids_for(self, kind, *, library_id=None, tmdb_id=None):
        self.asked.append((kind, library_id, tmdb_id))
        if kind == "show" and (library_id == 2021 or tmdb_id == 247718):
            return {"id": 2021, "tmdb_id": 247718, "imdb_id": "tt31510819", "tvdb_id": 446831}
        return {}

    def quality_profile_id_for(self, kind, *, tmdb_id=None, library_id=None):
        return 5 if (library_id == 2021 or tmdb_id == 247718) else None


def test_an_episode_search_by_library_id_gets_the_show_ids():
    body = dl._with_title_identity(_DB(), {"scope": "episode", "media_id": 2021,
                                           "media_source": "library", "title": "MobLand"})
    assert dl._external_ids(body) == {"tmdb_id": 247718, "imdb_id": "tt31510819", "tvdb_id": 446831}


def test_ids_the_client_sent_win():
    body = dl._with_title_identity(_DB(), {"scope": "episode", "media_id": 2021,
                                           "media_source": "library", "imdb_id": "tt-mine"})
    assert body["imdb_id"] == "tt-mine"


def test_a_title_outside_the_library_keeps_its_body():
    src = {"scope": "movie", "title": "x"}
    assert dl._with_title_identity(_DB(), src) == src


def test_the_profile_comes_from_the_library_row(monkeypatch):
    import core.video.quality_profile as qp
    monkeypatch.setattr(qp, "profile_by_id", lambda db, pid: SimpleNamespace(pid=pid))
    by_library = dl._profile_for_request(_DB(), {"scope": "episode", "media_id": 2021,
                                                 "media_source": "library"})
    by_tmdb = dl._profile_for_request(_DB(), {"scope": "season", "tmdb_id": 247718})
    nothing = dl._profile_for_request(_DB(), {"scope": "episode", "title": "MobLand"})
    assert (by_library[1], by_tmdb[1], nothing[1]) == (5, 5, None)
