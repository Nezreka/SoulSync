"""Quality evaluation for none / acceptable / until_cutoff / until_top policies."""

from __future__ import annotations

import json

import pytest

from core.library2.quality_eval import evaluate_file, is_upgrade_policy, profile_targets

_TARGETS = json.dumps([
    {"label": "FLAC 24/96", "format": "flac", "bit_depth": 24, "min_sample_rate": 96000},
    {"label": "FLAC 16", "format": "flac", "bit_depth": 16},
    {"label": "MP3 320", "format": "mp3", "min_bitrate": 320},
])


def _profile(policy: str, cutoff: int = 0):
    return {"ranked_targets": _TARGETS, "upgrade_policy": policy,
            "upgrade_cutoff_index": cutoff}


def _flac16():
    return {"format": "flac", "bitrate": 900, "sample_rate": 44100, "bit_depth": 16}


def _mp3():
    return {"format": "mp3", "bitrate": 320, "sample_rate": 44100, "bit_depth": None}


def test_acceptable_never_upgrades_once_met():
    targets, policy, cutoff = profile_targets(_profile("acceptable"))
    ev = evaluate_file(_flac16(), targets, policy, cutoff)
    assert ev == {"meets_profile": True, "upgrade_candidate": False}


def test_acceptable_upgrades_until_any_target_is_met():
    targets, policy, cutoff = profile_targets(_profile("acceptable"))
    ev = evaluate_file(
        {"format": "mp3", "bitrate": 128}, targets, policy, cutoff)
    assert ev == {"meets_profile": False, "upgrade_candidate": True}


def test_none_never_upgrades_even_when_quality_is_unknown():
    targets, policy, cutoff = profile_targets(_profile("none"))
    assert evaluate_file(None, targets, policy, cutoff) == {
        "meets_profile": None, "upgrade_candidate": False}


def test_until_top_upgrades_below_first_target():
    targets, policy, cutoff = profile_targets(_profile("until_top"))
    ev = evaluate_file(_flac16(), targets, policy, cutoff)
    assert ev["meets_profile"] is True
    assert ev["upgrade_candidate"] is True  # FLAC16 is rank 1, top is rank 0


def test_until_cutoff_respects_cutoff_index():
    # Cutoff at index 1 (FLAC 16): a FLAC16 file is done, an MP3 is not.
    targets, policy, cutoff = profile_targets(_profile("until_cutoff", cutoff=1))
    assert evaluate_file(_flac16(), targets, policy, cutoff)["upgrade_candidate"] is False
    assert evaluate_file(_mp3(), targets, policy, cutoff)["upgrade_candidate"] is True


def test_intentional_hires_downsample_does_not_loop_as_upgrade():
    targets, policy, cutoff = profile_targets(_profile("until_cutoff", cutoff=0))
    file_row = {
        **_flac16(),
        "acquired_quality_json": json.dumps({
            "format": "flac", "sample_rate": 96000, "bit_depth": 24,
            "bitrate": None, "channels": None,
        }),
        "retention_json": json.dumps([{
            "type": "downsample_hires_flac", "source_replaced": True,
            "target_bit_depth": 16, "target_sample_rate": 44100,
        }]),
    }

    assert evaluate_file(file_row, targets, policy, cutoff) == {
        "meets_profile": True,
        "upgrade_candidate": False,
    }


def test_intentional_lossy_replacement_uses_acquired_quality_for_cutoff():
    targets, policy, cutoff = profile_targets(_profile("until_cutoff", cutoff=1))
    file_row = {
        **_mp3(),
        "acquired_quality_json": json.dumps({
            "format": "flac", "sample_rate": 44100, "bit_depth": 16,
            "bitrate": None, "channels": None,
        }),
        "retention_json": json.dumps([{
            "type": "lossy_copy", "source_replaced": True,
            "codec": "mp3", "bitrate": "320",
        }]),
    }

    assert evaluate_file(file_row, targets, policy, cutoff)["upgrade_candidate"] is False


def test_is_upgrade_policy():
    assert is_upgrade_policy("until_top")
    assert is_upgrade_policy("until_cutoff")
    assert is_upgrade_policy("acceptable")
    assert not is_upgrade_policy("none")
    assert not is_upgrade_policy(None)


def test_unknown_quality_is_not_reported_as_satisfied():
    targets, policy, cutoff = profile_targets(_profile("until_cutoff"))
    ev = evaluate_file(None, targets, policy, cutoff)
    assert ev == {"meets_profile": None, "upgrade_candidate": None}


def test_explicit_unknown_format_is_tristate():
    targets, policy, cutoff = profile_targets(_profile("until_cutoff"))
    ev = evaluate_file({"format": "unknown"}, targets, policy, cutoff)
    assert ev == {"meets_profile": None, "upgrade_candidate": None}


def test_invalid_quality_values_are_tristate():
    targets, policy, cutoff = profile_targets(_profile("until_cutoff"))
    ev = evaluate_file(
        {"format": "flac", "bit_depth": "not-a-number"},
        targets,
        policy,
        cutoff,
    )
    assert ev == {"meets_profile": None, "upgrade_candidate": None}


def test_missing_quality_without_targets_remains_unconstrained():
    assert evaluate_file(None, [], "acceptable") == {
        "meets_profile": True,
        "upgrade_candidate": False,
    }


@pytest.mark.parametrize(
    "targets,old_quality,new_quality,fallback,policy,cutoff,allowed",
    [
        ([{"format": "flac", "bit_depth": 24}, {"format": "flac", "bit_depth": 16}],
         ("flac", None, 44_100, 16), ("flac", None, 96_000, 24), True,
         "until_cutoff", 0, True),
        ([{"format": "mp3", "min_bitrate": 320}, {"format": "mp3", "min_bitrate": 128}],
         ("mp3", 128, None, None), ("mp3", 320, None, None), True,
         "until_cutoff", 0, True),
        ([{"format": "flac"}, {"format": "mp3"}],
         ("mp3", 320, None, None), ("flac", None, 44_100, 16), True,
         "until_cutoff", 0, True),
        ([{"format": "flac"}, {"format": "mp3"}],
         ("flac", None, 44_100, 16), ("mp3", 320, None, None), True,
         "until_cutoff", 0, False),
        ([{"format": "mp3", "min_bitrate": 128}],
         ("mp3", 320, None, None), ("mp3", 320, None, None), True,
         "until_cutoff", 0, False),
        # A custom target order wins over the generic lossless tier score.
        ([{"format": "mp3"}, {"format": "flac"}],
         ("flac", None, 44_100, 16), ("mp3", 320, None, None), True,
         "until_cutoff", 0, True),
        # Unmatched fallback is accepted only when the profile permits it.
        ([{"format": "flac"}],
         ("mp3", 128, None, None), ("mp3", 320, None, None), False,
         "until_cutoff", 0, False),
        ([{"format": "flac"}],
         ("mp3", 128, None, None), ("mp3", 320, None, None), True,
         "until_cutoff", 0, True),
        ([{"format": "flac"}],
         ("aac", 256, None, None), ("ogg", 256, None, None), True,
         "until_cutoff", 0, True),
        # Same matched rank only uses tier score within the same format.
        ([{"format": "flac"}, {"format": "mp3", "min_bitrate": 128}],
         ("mp3", 128, None, None), ("mp3", 320, None, None), True,
         "until_cutoff", 0, True),
        ([{"format": "flac", "bit_depth": 24}, {}],
         ("mp3", 128, None, None), ("ogg", 320, None, None), True,
         "until_cutoff", 0, False),
        # Cutoff completion, intermediate progress, and until_top alias.
        ([{"format": "flac", "bit_depth": 24}, {"format": "flac", "bit_depth": 16}],
         ("flac", None, 44_100, 16), ("flac", None, 96_000, 24), True,
         "until_cutoff", 1, False),
        ([{"format": "flac", "bit_depth": 24}, {"format": "flac"},
          {"format": "mp3", "min_bitrate": 320}, {"format": "mp3", "min_bitrate": 128}],
         ("mp3", 128, None, None), ("mp3", 320, None, None), True,
         "until_cutoff", 0, True),
        ([{"format": "flac", "bit_depth": 24}, {"format": "flac", "bit_depth": 16}],
         ("flac", None, 44_100, 16), ("flac", None, 96_000, 24), True,
         "until_top", 1, True),
        # Any accepted target stops `acceptable`; an unmatched old file may upgrade.
        ([{"format": "flac"}],
         ("mp3", 320, None, None), ("flac", None, 44_100, 16), True,
         "acceptable", 0, True),
        ([{"format": "flac"}, {"format": "mp3"}],
         ("mp3", 320, None, None), ("flac", None, 44_100, 16), True,
         "acceptable", 0, False),
        ([{"format": "flac"}],
         ("mp3", 320, None, None), ("flac", None, 44_100, 16), True,
         "none", 0, False),
    ],
)
def test_upgrade_decision_requires_strict_real_quality_improvement(
    imported_conn, tmp_path, monkeypatch, targets, old_quality, new_quality,
    fallback, policy, cutoff, allowed,
):
    from core.library2.quality_eval import decide_track_upgrade
    from core.quality.model import AudioQuality

    conn = imported_conn
    profile_id = conn.execute(
        "INSERT INTO quality_profiles(name, ranked_targets, upgrade_policy, "
        "upgrade_cutoff_index, fallback_enabled) VALUES('Decision',?,?,?,?)",
        (json.dumps(targets), policy, cutoff, int(fallback)),
    ).lastrowid
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Decision Artist')"
    ).lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Decision Album')",
        (artist_id,),
    ).lastrowid
    track_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, quality_profile_id, "
        "quality_profile_explicit) VALUES(?, 'Decision Track', ?, 1)",
        (album_id, profile_id),
    ).lastrowid
    old_path = tmp_path / "old.audio"
    new_path = tmp_path / "new.audio"
    old_path.write_bytes(b"old")
    new_path.write_bytes(b"new")
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format) VALUES(?,?,?)",
        (track_id, str(old_path), old_quality[0]),
    )
    conn.commit()

    qualities = {
        str(old_path): AudioQuality(*old_quality),
        str(new_path): AudioQuality(*new_quality),
    }
    monkeypatch.setattr(
        "core.imports.file_ops.probe_audio_quality", lambda path: qualities[str(path)]
    )

    decision = decide_track_upgrade(conn, track_id, str(new_path))

    assert decision.applicable is True
    assert decision.allowed is allowed
    assert decision.existing_path == str(old_path)
