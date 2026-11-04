"""``add_to_wishlist`` must distinguish "refreshed" from "refused".

Review-round-2 finding R2-03/R2-04/R2-09: the duplicate paths did a correct
in-place UPDATE and then returned a bare ``False``. Artist Enhance counted every
re-queued track as ``'Wishlist add failed'`` and ``POST /wishlist`` answered 409
after successfully applying an explicit ``quality_profile_id`` — with no body,
so the client could not even read back what was stored.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _track(track_id="t-1", name="Song", artist="Artist", album_id="alb-1", album_name="Album"):
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": artist}],
        "album": {"id": album_id, "name": album_name, "images": []},
    }


def _profile(db, name):
    return db.create_quality_profile(name, {})


def test_first_add_reports_created(db):
    outcome = db.add_to_wishlist_detailed(_track(), source_type="manual")

    assert outcome["status"] == "created"
    assert outcome["created"] is True
    assert outcome["applied"] is True
    # Composite key canonical from the first insert (P1-09) — see
    # tests/wishlist/test_wishlist_idempotency.py for the full contract.
    assert outcome["track_id"] == "t-1::alb-1"


def test_authoritative_refresh_reports_updated_not_failure(db):
    profile_id = _profile(db, "Lossless")
    db.add_to_wishlist_detailed(_track(), source_type="manual")

    outcome = db.add_to_wishlist_detailed(
        _track(), source_type="manual", quality_profile_id=profile_id
    )

    assert outcome["status"] == "updated"
    assert outcome["applied"] is True, "a refreshed row is not a failure"
    assert outcome["created"] is False
    assert db.get_wishlist_track("t-1::alb-1")["quality_profile_id"] == profile_id


def test_enhance_rerun_is_not_reported_as_failed(db):
    db.add_to_wishlist_detailed(_track(), source_type="manual")

    outcome = db.add_to_wishlist_detailed(_track(), source_type="enhance")

    assert outcome["applied"] is True


def test_unknown_quality_profile_is_rejected_not_merely_falsy(db):
    outcome = db.add_to_wishlist_detailed(_track(), quality_profile_id=987654)

    assert outcome["status"] == "rejected"
    assert outcome["applied"] is False


def test_second_album_reports_the_composite_key_it_actually_wrote(db):
    """R2-09: the caller must be able to read back its own write."""
    db.add_to_wishlist_detailed(_track(album_id="alb-1"), source_type="manual")

    outcome = db.add_to_wishlist_detailed(
        _track(album_id="alb-2", album_name="Other Album"), source_type="manual"
    )

    assert outcome["status"] == "created"
    assert outcome["track_id"] == "t-1::alb-2"
    stored = db.get_wishlist_track(outcome["track_id"])
    assert stored is not None
    data = stored["spotify_data"]
    if isinstance(data, str):
        import json
        data = json.loads(data)
    assert data["album"]["id"] == "alb-2"


def test_legacy_bool_wrapper_is_unchanged(db):
    assert db.add_to_wishlist(_track(), source_type="manual") is True
    # A duplicate still answers False on the legacy contract — callers that were
    # never updated keep behaving exactly as before.
    assert db.add_to_wishlist(_track(), source_type="manual") is False
