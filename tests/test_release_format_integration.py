"""#1149 end to end, against a REAL quality_profiles row.

The policy tests use dict profiles, which proves the rules but not the
plumbing: `profile_allowed_formats` goes through `load_profile_by_id`, which
reads the database and reshapes a row into the v3 dict. A column rename or a
JSON-shape change would break the feature while every dict-based test kept
passing.

This is the seam that decides whether Zombiehamser's actual saved profile
turns into a veto.
"""

from __future__ import annotations

import json

import pytest

from core.quality.release_format import allowed_formats_from_profile
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    # Deliberately exercises get_quality_profile (row -> v3 dict) rather than
    # load_profile_by_id, which builds its own MusicDatabase() against the
    # real install path — a test must never reach that.
    return MusicDatabase(str(tmp_path / 'profiles.db'))


def _profile(db, name, targets, fallback):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO quality_profiles (name, ranked_targets, fallback_enabled, is_default) "
        "VALUES (?, ?, ?, 1)",
        (name, json.dumps(targets), 1 if fallback else 0))
    conn.execute("UPDATE quality_profiles SET is_default = 0 WHERE name != ?", (name,))
    conn.commit()
    row = conn.execute("SELECT id FROM quality_profiles WHERE name = ?", (name,)).fetchone()
    conn.close()
    return row[0]


def test_a_saved_lossless_profile_becomes_a_flac_veto(db):
    """Zombiehamser's setup: FLAC targets, fallback disabled."""
    _profile(db, 'Lossless Only',
             [{'label': 'FLAC 24-bit', 'format': 'flac', 'bit_depth': 24},
              {'label': 'FLAC 16-bit', 'format': 'flac', 'bit_depth': 16}],
             fallback=False)

    assert allowed_formats_from_profile(db.get_quality_profile()) == {'flac'}


def test_a_saved_profile_with_fallback_on_vetoes_nothing(db):
    _profile(db, 'Anything', [{'format': 'flac'}], fallback=True)

    assert allowed_formats_from_profile(db.get_quality_profile()) is None


def test_a_saved_mixed_ladder_allows_both(db):
    _profile(db, 'Mixed',
             [{'format': 'flac'}, {'format': 'mp3', 'min_bitrate': 320}],
             fallback=False)

    assert allowed_formats_from_profile(db.get_quality_profile()) == {'flac', 'mp3'}


def test_the_stored_json_survives_the_round_trip(db):
    """ranked_targets is a JSON blob in a TEXT column. If the reshape ever
    stops parsing it, the feature silently becomes 'allow everything' — which
    fails OPEN and would be invisible without this."""
    _profile(db, 'Lossless', [{'format': 'flac', 'bit_depth': 24}], fallback=False)

    profile = db.get_quality_profile()

    assert profile.get('ranked_targets'), 'targets did not survive the read'
    assert allowed_formats_from_profile(profile) is not None, (
        'a strict profile resolved to "allow anything" — the veto is dead')


# ── failing open ─────────────────────────────────────────────────────────────

def test_an_unreadable_profile_allows_everything_rather_than_blocking(monkeypatch):
    """The safety property of the whole feature.

    This veto sits in front of every torrent download. If resolving the
    profile ever throws and that turned into "allow nothing", the symptom
    would be an install where no torrent downloads at all, with no obvious
    cause. It must fail OPEN.
    """
    from core.download_plugins import album_bundle

    def _boom(_id=None):
        raise RuntimeError('database is on fire')

    monkeypatch.setattr('core.quality.selection.load_profile_by_id', _boom)

    assert album_bundle.profile_allowed_formats() is None


def test_a_profile_that_resolves_to_nothing_allows_everything(monkeypatch):
    monkeypatch.setattr('core.quality.selection.load_profile_by_id',
                        lambda _id=None: None)

    from core.download_plugins import album_bundle

    assert album_bundle.profile_allowed_formats() is None
