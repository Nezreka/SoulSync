"""a preserved quality finding's Delete deletes, and its wishlist add goes to
the library's owner.

3.6.1's quality finding offered Redownload / Delete / Ignore. Library v2's port
sent every action but Ignore to the wishlist, so Delete queued a download, and
it dropped the owner profile (#1504) so another profile's track landed on the
admin's wishlist.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.repair_worker import RepairWorker


def _worker(db):
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = "/tmp/Transfer"
    return worker


def test_delete_deletes_instead_of_wishlisting():
    calls = []
    db = SimpleNamespace(add_to_wishlist=lambda **kw: calls.append(("wish", kw)) or True)
    worker = _worker(db)
    worker._delete_quality_finding_file = lambda entity_id, path: (
        calls.append(("delete", entity_id, path)) or {"success": True, "action": "deleted_file"})
    result = worker._fix_legacy_quality_upgrade(
        "track", "lib2:9", "/music/low.mp3",
        {"_fix_action": "delete", "matched_track_data": {"name": "Song"}})
    assert result["action"] == "deleted_file"
    assert calls == [("delete", "lib2:9", "/music/low.mp3")]


def test_the_wishlist_add_goes_to_the_owner_and_a_queued_track_is_fine():
    seen = []

    def detailed(**kwargs):
        seen.append(kwargs["profile_id"])
        return {"status": "updated", "applied": True}
    worker = _worker(SimpleNamespace(add_to_wishlist_detailed=detailed))
    result = worker._fix_legacy_quality_upgrade(
        "track", "lib2:9", "/music/low.mp3",
        {"matched_track_data": {"name": "Song", "owner_profile_id": 2}})
    assert seen == [2]
    assert result == {"success": True, "action": "already_wishlisted"}
