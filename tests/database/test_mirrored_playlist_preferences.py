"""Mirror preference writes are atomic, and mirror resolution knows the provider.

Audit findings:

* P2-02 — ``organize_by_playlist`` and ``quality_profile_id`` were two separate
  statements with their own commit. A request carrying a valid toggle and an
  invalid Quality Profile answered 400 *after* durably flipping the toggle.
* P2-01 — two providers may legitimately publish the same upstream playlist id
  (and even the same name). The heuristic resolver returned ``None`` for that
  pair, so the caller silently fell back to the global default profile.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _mirror(db, **kw):
    kw.setdefault("source", "spotify")
    kw.setdefault("source_playlist_id", "p1")
    kw.setdefault("name", "Mix")
    kw.setdefault("tracks", [{"track_name": "Song", "artist_name": "Artist"}])
    kw.setdefault("profile_id", 1)
    return db.mirror_playlist(**kw)


# ── P2-02: atomic preferences ────────────────────────────────────────────────

def test_preferences_update_applies_both_fields(db):
    quality_id = db.create_quality_profile("Hi-Res", {})
    pk = _mirror(db)

    assert db.update_mirrored_playlist_preferences(
        pk, profile_id=1, organize_by_playlist=True, quality_profile_id=quality_id
    ) == 'ok'

    row = db.get_mirrored_playlist(pk)
    assert row["organize_by_playlist"] is True
    assert row["quality_profile_id"] == quality_id


def test_invalid_quality_profile_rolls_back_the_organize_toggle(db):
    """The exact reported repro: valid toggle + unknown profile id."""
    pk = _mirror(db)
    before = db.get_mirrored_playlist(pk)

    assert db.update_mirrored_playlist_preferences(
        pk, profile_id=1, organize_by_playlist=True, quality_profile_id=999999
    ) == 'unknown_quality_profile'

    after = db.get_mirrored_playlist(pk)
    assert after["organize_by_playlist"] == before["organize_by_playlist"] is False
    assert after["quality_profile_id"] == before["quality_profile_id"]


def test_preferences_update_is_owner_scoped(db):
    pk = _mirror(db, profile_id=2)

    assert db.update_mirrored_playlist_preferences(
        pk, profile_id=1, organize_by_playlist=True
    ) == 'not_found'
    assert db.get_mirrored_playlist(pk)["organize_by_playlist"] is False


def test_preferences_update_with_no_fields_is_a_noop(db):
    pk = _mirror(db)
    assert db.update_mirrored_playlist_preferences(pk, profile_id=1) == 'ok'


# ── P2-01: provider-aware resolution ─────────────────────────────────────────

def test_same_source_id_across_providers_resolves_by_provider(db):
    spotify_pk = _mirror(db, source="spotify", source_playlist_id="12345", name="Party")
    deezer_pk = _mirror(db, source="deezer", source_playlist_id="12345", name="Party")
    assert spotify_pk != deezer_pk

    # Without a provider the pair is genuinely ambiguous — including by name.
    assert db.resolve_mirrored_playlist_assignment("12345", "Party", 1) is None

    assert db.resolve_mirrored_playlist_assignment(
        "12345", "Party", 1, source="deezer")["id"] == deezer_pk
    assert db.resolve_mirrored_playlist_assignment(
        "12345", "Party", 1, source="spotify")["id"] == spotify_pk


def test_unknown_provider_falls_back_to_the_legacy_heuristics(db):
    """A provider alias we don't store must not break an otherwise clear match."""
    pk = _mirror(db, source="itunes_link", source_playlist_id="apple-1", name="Apple Mix")

    assert db.resolve_mirrored_playlist_assignment(
        "apple-1", "Apple Mix", 1, source="itunes")["id"] == pk


def test_provider_hint_is_still_profile_scoped(db):
    _mirror(db, source="deezer", source_playlist_id="12345", name="Party", profile_id=2)

    assert db.resolve_mirrored_playlist_assignment(
        "12345", "Party", 1, source="deezer") is None
