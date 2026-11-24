"""retro commercials only download while the admin has them on.

any profile or paired phone asking for a break used to start an
archive.org download into a cache of up to 500 MB, switch or no switch.
"""

from __future__ import annotations

import time

from core.player import commercials


def _settle():
    for _ in range(100):
        if not commercials._warming:
            return
        time.sleep(0.01)


def test_off_downloads_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(commercials, "_server_enabled", lambda: False)
    monkeypatch.setattr(commercials, "_download_archive_era", lambda *a, **k: calls.append(a))
    assert commercials.warm_async("80s") is False
    _settle()
    assert calls == []


def test_on_downloads(monkeypatch):
    calls = []
    monkeypatch.setattr(commercials, "_server_enabled", lambda: True)
    monkeypatch.setattr(commercials, "_download_archive_era", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(commercials, "_clips_cache", {})
    assert commercials.warm_async("80s") is True
    _settle()
    assert calls


def test_an_unreadable_switch_counts_as_off(monkeypatch):
    def boom():
        raise RuntimeError("no db")
    monkeypatch.setattr("database.player_database.get_player_database", boom)
    assert commercials._server_enabled() is False


def test_a_break_with_an_empty_cache_does_not_download_when_off(monkeypatch):
    calls = []
    monkeypatch.setattr(commercials, "_server_enabled", lambda: False)
    monkeypatch.setattr(commercials, "_download_archive_era", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(commercials, "_clips_cache", {})
    monkeypatch.setattr(commercials, "_scan_local_locked", lambda: None)
    monkeypatch.setattr(commercials, "_load_locked", lambda: None)
    assert commercials.pick_break(era="80s") == []
    _settle()
    assert calls == []


# the mobile app sends every field of its request, the unset ones as null
def _put(monkeypatch, body):
    from core.player import settings as S
    seen = {}

    def put(_db, _pid, changes):
        seen.update(changes)
        return {"commercials_enabled": True, "commercials_era": "auto", "commercials_mode": "cable", **changes}

    monkeypatch.setattr(S, "put", put)
    monkeypatch.setattr(commercials, "warm_async", lambda *a, **k: False)
    commercials.put_server_config(object(), body)
    return seen


def test_a_null_field_is_left_alone(monkeypatch):
    # the switch from the phone: era null used to be saved as "none" and refused
    assert _put(monkeypatch, {"enabled": True, "era": None, "mode": None}) == {"commercials_enabled": True}
    # an era pick from the phone: enabled null used to switch commercials off
    assert _put(monkeypatch, {"enabled": None, "era": "80s", "mode": None}) == {"commercials_era": "80s"}


def test_all_null_is_nothing_to_change(monkeypatch):
    import pytest
    from core.player import settings as S
    with pytest.raises(S.SettingsError):
        _put(monkeypatch, {"enabled": None, "era": None, "mode": None})


# a fetch fills the 500 MB cache (to FILL_BYTES), not a fixed handful per era
def _clip(cid, era, mb, archive_id=None):
    return commercials.CommercialClip(id=cid, title=cid, era=era, duration_seconds=30, path="/x",
                                      source="archive", archive_id=archive_id or cid,
                                      size_bytes=mb * 1024 * 1024, added_at=0)


def test_a_full_cache_fetches_nothing_and_says_so(monkeypatch):
    share_mb = commercials.FILL_BYTES // 3 // (1024 * 1024) + 1
    cache = {f"{e}": _clip(e, e, share_mb) for e in ("80s", "90s", "mix")}
    monkeypatch.setattr(commercials, "_clips_cache", cache)
    monkeypatch.setattr(commercials, "_load_locked", lambda: None)
    assert commercials.warm_async("auto", enabled=True) is False
    # one era short: it fetches
    cache.pop("90s")
    calls = []
    monkeypatch.setattr(commercials, "_download_archive_era", lambda *a, **k: calls.append((a, k)))
    assert commercials.warm_async("auto", enabled=True) is True
    _settle()
    assert calls == [(("90s",), {"fill_bytes": commercials.FILL_BYTES // 3})]


def test_it_pages_past_what_it_has_until_the_era_is_full(monkeypatch):
    cache = {"a": _clip("a", "80s", 1, "have-1")}
    monkeypatch.setattr(commercials, "_clips_cache", cache)
    pages = {1: [{"identifier": "have-1"}, {"identifier": "n1"}], 2: [{"identifier": "n2"}],
             3: [{"identifier": "n3"}], 4: []}
    asked, got = [], []
    monkeypatch.setattr(commercials, "_search_docs", lambda era, page: (asked.append(page), pages.get(page, []))[1])

    def download(era, docs, needed, fill_bytes=0):
        for d in docs:
            got.append(d["identifier"])
            cache[d["identifier"]] = _clip(d["identifier"], era, 1)
        return len(docs)

    monkeypatch.setattr(commercials, "_download_docs", download)
    assert commercials._download_archive_era("80s", fill_bytes=3 * 1024 * 1024) == 2
    assert got == ["n1", "n2"] and asked == [1, 2]        # stopped once 3 MB was there
