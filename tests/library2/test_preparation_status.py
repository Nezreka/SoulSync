"""The library banner can see the upgrade's finishing work.

The credit split, tracklists and tags moved behind a working library, so the
migration banner, which only knew the import, went away while pages were
still slow. The preparation now reports its stage and counts."""

import threading

from core.library2 import post_import


def test_the_preparation_reports_its_stage_and_clears_when_done(monkeypatch):
    seen, release = {}, threading.Event()

    def credits(database, config_manager=None, *, progress=None, **kw):
        progress("artists", 5, 10)
        seen.update(post_import.preparation_state())
        release.wait(5)

    monkeypatch.setattr(post_import, "prepare_imported_artists", credits)
    thread = post_import.start_background_preparation(object(), None, full=False)
    release.set()
    thread.join(5)
    assert seen == {"active": True, "stage": "artists", "current": 5, "total": 10}
    assert post_import.preparation_state()["active"] is False
