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
    assert seen == {"active": True, "full": False, "stage": "artists", "current": 5, "total": 10}
    assert post_import.preparation_state()["active"] is False


def test_the_state_says_whether_it_is_the_full_preparation(monkeypatch):
    """the banner calls only the one-time preparation after an upgrade
    "finishing your library upgrade"; the credit pass a restart repeats is
    not one"""
    seen = []

    def credits(database, config_manager=None, *, progress=None, **kw):
        seen.append(post_import.preparation_state()["full"])

    def precache(database, config_manager, *, progress=None):
        seen.append(post_import.preparation_state()["full"])
        return {"artists": "ok"}

    monkeypatch.setattr(post_import, "prepare_imported_artists", credits)
    monkeypatch.setattr(post_import, "run_post_import_precache", precache)
    monkeypatch.setattr(post_import, "_set_full_pending", lambda *a: None)
    post_import.start_background_preparation(object(), None, full=False).join(5)
    post_import.start_background_preparation(object(), None, full=True).join(5)
    assert seen == [False, True]


def test_the_credit_pass_reports_its_total_before_the_first_lookup(monkeypatch):
    """the first artist is a network lookup; until it answered the banner had
    a stage and no numbers"""
    from core.library2 import native_enrich
    calls = []
    monkeypatch.setattr(native_enrich, "_pending_unmapped_artists",
                        lambda *a, **k: [{"id": 1, "name": "A & B"}, {"id": 2, "name": "C x D"}])
    monkeypatch.setattr(native_enrich, "resolve_and_enrich_native_artist",
                        lambda *a, **k: calls.append("lookup") or {"success": True})
    monkeypatch.setattr(native_enrich, "_mark_reconcile_attempted", lambda *a: None)
    nothing = {"released": 0, "ambiguous": 0, "artist_ids": [], "pruned": 0,
               "cleared": 0, "merged": 0, "split": 0}
    for name in ("release_borrowed_artist_identities", "prune_browse_only_artists",
                 "clear_placeholder_artist_images", "split_combined_credits_locally"):
        monkeypatch.setattr(native_enrich, name, lambda *a, **k: dict(nothing))

    def progress(current, total):
        calls.append((current, total))

    import sqlite3
    native_enrich.reconcile_unmapped_native_artists(sqlite3.connect(":memory:"), progress=progress)
    assert calls[0] == (0, 2) and calls[1] == "lookup"
