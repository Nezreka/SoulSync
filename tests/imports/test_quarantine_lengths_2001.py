"""#2001: a quarantined 13 minute extended mix next to a 4:26 single read as
just "duration mismatch". the review shows both lengths now, from what the
integrity check measured and what the release said."""

import json

from core.imports.quarantine import list_quarantine_entries, serialize_quarantine_context


def _quarantine(tmp_path, context):
    (tmp_path / "abc123_DoYaThing.mp3.quarantined").write_bytes(b"x")
    (tmp_path / "abc123_DoYaThing.json").write_text(json.dumps({
        "original_filename": "1-02 DoYaThing.mp3", "quarantine_reason": "Integrity check failed",
        "expected_track": "DoYaThing", "expected_artist": "Gorillaz", "trigger": "integrity",
        "context": serialize_quarantine_context(context)}), encoding="utf-8")
    return list_quarantine_entries(str(tmp_path))


def test_both_lengths_reach_the_review(tmp_path):
    [entry] = _quarantine(tmp_path, {"_expected_length_s": 266.0,
                                     "_integrity_checks": {"actual_length_s": 784.04}})
    assert entry["expected_length_s"] == 266.0
    assert entry["actual_length_s"] == 784.0


def test_an_older_sidecar_without_them_says_unknown(tmp_path):
    [entry] = _quarantine(tmp_path, {})
    assert entry["expected_length_s"] is None and entry["actual_length_s"] is None
