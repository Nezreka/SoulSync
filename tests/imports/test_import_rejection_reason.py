"""Tests for import_rejection_reason — the manual-import quarantine guard.

Regression for #764: the manual-import routes call
``post_process_matched_download`` directly. A quarantine (integrity / AcoustID
/ bit-depth) or race-guard rejection returns NORMALLY (no exception) and leaves
the file in ss_quarantine, not the library — but the routes counted any
no-exception return as a successful import, so the UI showed a green "Done" for
a file that had actually vanished. ``import_rejection_reason`` reads the context
flags the inner pipeline sets so the routes can report those as errors.
"""

from __future__ import annotations

import os
import tempfile

from core.imports.pipeline import import_rejection_reason
from core.imports.routes import ImportRouteRuntime, process_single_import_file


def test_clean_import_returns_none():
    assert import_rejection_reason({}) is None
    assert import_rejection_reason({'is_album': True, 'track_info': {}}) is None


def test_integrity_failure_detected():
    reason = import_rejection_reason({'_integrity_failure_msg': 'duration drift 12s'})
    assert reason is not None
    assert 'integrity' in reason.lower()
    assert 'duration drift 12s' in reason


def test_acoustid_quarantine_detected():
    reason = import_rejection_reason({
        '_acoustid_quarantined': True,
        '_acoustid_failure_msg': 'wrong artist: got Oasis expected Coldplay',
    })
    assert reason is not None
    assert 'acoustid' in reason.lower()
    assert 'Coldplay' in reason


def test_acoustid_quarantine_without_message_still_flags():
    # The flag alone must trip it even if no message was stashed.
    reason = import_rejection_reason({'_acoustid_quarantined': True})
    assert reason is not None
    assert 'acoustid' in reason.lower()


def test_bitdepth_rejection_detected():
    reason = import_rejection_reason({'_bitdepth_rejected': True})
    assert reason is not None
    assert 'bit-depth' in reason.lower()


def test_race_guard_failure_detected():
    reason = import_rejection_reason({'_race_guard_failed': True})
    assert reason is not None
    assert 'disappeared' in reason.lower()


def test_falsy_flags_do_not_trip():
    # A flag present but falsy (e.g. integrity passed) must NOT be a rejection.
    ctx = {
        '_integrity_failure_msg': '',
        '_acoustid_quarantined': False,
        '_bitdepth_rejected': False,
        '_race_guard_failed': False,
    }
    assert import_rejection_reason(ctx) is None


def test_integrity_takes_precedence_when_multiple_set():
    # Deterministic ordering: integrity first.
    reason = import_rejection_reason({
        '_integrity_failure_msg': 'truncated',
        '_acoustid_quarantined': True,
        '_bitdepth_rejected': True,
    })
    assert 'integrity' in reason.lower()


# ── route-level wiring: a quarantine must NOT report as a successful import ──


def _runtime_with_post_process(post_process):
    """Build an ImportRouteRuntime wired with stub resolvers + the supplied
    post_process_matched_download. Resolvers return a shared context dict so a
    flag the post-processor sets is what import_rejection_reason later reads."""
    ctx = {}

    return ImportRouteRuntime(
        get_single_track_import_context=lambda *a, **k: {"context": ctx, "source": "local"},
        normalize_import_context=lambda c: c,
        get_import_context_artist=lambda c: {"name": "Coldplay"},
        get_import_track_info=lambda c: {"name": "Yellow"},
        post_process_matched_download=post_process,
    ), ctx


def _tmp_audio_file():
    fd, path = tempfile.mkstemp(suffix=".flac")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(b"fLaC")  # only needs to exist for os.path.isfile
    return path


def test_single_import_quarantine_reported_as_error():
    # post-processing quarantines the file (sets the flag, returns normally).
    def quarantining_post_process(context_key, context, file_path):
        context['_acoustid_quarantined'] = True
        context['_acoustid_failure_msg'] = 'wrong track'

    runtime, _ctx = _runtime_with_post_process(quarantining_post_process)
    path = _tmp_audio_file()
    try:
        outcome, payload = process_single_import_file(
            runtime, {"full_path": path, "filename": "Coldplay - Yellow.flac",
                      "title": "Yellow", "artist": "Coldplay"},
        )
    finally:
        os.remove(path)

    assert outcome == "error"          # NOT "ok" -> route won't count it processed
    assert "AcoustID" in payload


def test_single_import_clean_reports_ok():
    # post-processing succeeds (no flags) -> import counts as processed.
    def clean_post_process(context_key, context, file_path):
        return None

    runtime, _ctx = _runtime_with_post_process(clean_post_process)
    path = _tmp_audio_file()
    try:
        outcome, payload = process_single_import_file(
            runtime, {"full_path": path, "filename": "Coldplay - Yellow.flac",
                      "title": "Yellow", "artist": "Coldplay"},
        )
    finally:
        os.remove(path)

    assert outcome == "ok"
    assert payload == "Yellow"
