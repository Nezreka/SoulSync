"""Last-resort acceptance of a version-mismatched quarantine candidate.

When a track's retries are fully exhausted and EVERY quarantined candidate for
it failed the same way (same wrong version, e.g. all instrumental), the only
available version is that one — accept the best (first-tried) one instead of
leaving the track missing. Strict guards: version-mismatch only, all the same
matched version, and a minimum count.
"""

from __future__ import annotations

from core.imports.version_mismatch_fallback import (
    select_version_mismatch_fallback,
    try_accept_version_mismatch_fallback,
)


def _entry(eid, reason, *, track="Barricades (Movie Ver.)", artist="Hiroyuki Sawano",
           ctx=True):
    return {
        "id": eid,
        "reason": reason,
        "expected_track": track,
        "expected_artist": artist,
        "has_full_context": ctx,
    }


_VM = "Version mismatch: expected 'Barricades (Movie Ver.)' (original) but file is 'Barricades <MOVIEver.> ({v})' ({v})"


def _vm(version):
    return _VM.format(v=version)


def test_picks_oldest_when_all_same_version_and_count_met():
    # 3 instrumental mismatches → all same kind → pick first-tried (smallest id).
    entries = [
        _entry("20260605_120300", _vm("instrumental")),
        _entry("20260605_120100", _vm("instrumental")),  # oldest = first tried
        _entry("20260605_120200", _vm("instrumental")),
    ]
    chosen = select_version_mismatch_fallback(entries, "Barricades (Movie Ver.)",
                                              "Hiroyuki Sawano", min_count=2)
    assert chosen is not None
    assert chosen["id"] == "20260605_120100"


def test_none_when_below_min_count():
    entries = [_entry("20260605_120100", _vm("instrumental"))]
    assert select_version_mismatch_fallback(
        entries, "Barricades (Movie Ver.)", "Hiroyuki Sawano", min_count=2) is None


def test_none_when_mixed_versions():
    # instrumental + live → inconsistent → never auto-accept (ambiguous).
    entries = [
        _entry("20260605_120100", _vm("instrumental")),
        _entry("20260605_120200", _vm("live")),
    ]
    assert select_version_mismatch_fallback(
        entries, "Barricades (Movie Ver.)", "Hiroyuki Sawano", min_count=2) is None


def test_ignores_non_version_mismatch_reasons():
    # Audio mismatch (wrong artist/song) and integrity must NOT count.
    entries = [
        _entry("20260605_120100",
               "Audio mismatch: file identified as 'X' by 'Y' (artist=0%)"),
        _entry("20260605_120200",
               "Integrity check failed: Duration mismatch: file is 175s, expected 182s"),
    ]
    assert select_version_mismatch_fallback(
        entries, "Barricades (Movie Ver.)", "Hiroyuki Sawano", min_count=1) is None


def test_only_counts_entries_for_this_track():
    entries = [
        _entry("20260605_120100", _vm("instrumental")),
        _entry("20260605_120200", _vm("instrumental"), track="Call Your Name (Gv)"),
    ]
    # Only one entry matches this track → below min_count of 2.
    assert select_version_mismatch_fallback(
        entries, "Barricades (Movie Ver.)", "Hiroyuki Sawano", min_count=2) is None


def test_excludes_thin_sidecar_entries_without_context():
    # Can't approve without embedded context → exclude from the candidate pool.
    entries = [
        _entry("20260605_120100", _vm("instrumental"), ctx=False),
        _entry("20260605_120200", _vm("instrumental"), ctx=False),
    ]
    assert select_version_mismatch_fallback(
        entries, "Barricades (Movie Ver.)", "Hiroyuki Sawano", min_count=2) is None


def test_track_match_is_case_and_space_insensitive():
    entries = [
        _entry("20260605_120100", _vm("instrumental")),
        _entry("20260605_120200", _vm("instrumental")),
    ]
    chosen = select_version_mismatch_fallback(
        entries, "  barricades (movie ver.) ", "HIROYUKI SAWANO", min_count=2)
    assert chosen is not None
    assert chosen["id"] == "20260605_120100"


# ── Orchestration (try_accept_version_mismatch_fallback) ──────────────────────

def _cfg(enabled=True, min_count=2):
    values = {
        "post_processing.accept_version_mismatch_fallback": enabled,
        "post_processing.version_mismatch_min_count": min_count,
    }
    return lambda key, default=None: values.get(key, default)


def _two_instrumental_entries():
    return [
        _entry("20260605_120100", _vm("instrumental")),
        _entry("20260605_120200", _vm("instrumental")),
    ]


def test_orchestration_disabled_does_nothing():
    calls = {"approve": 0, "reprocess": 0}

    def approve(*a, **k):
        calls["approve"] += 1
        return ("/restored.flac", {}, "acoustid")

    def reprocess(*a, **k):
        calls["reprocess"] += 1

    ok = try_accept_version_mismatch_fallback(
        quarantine_dir="/q", restore_dir="/r",
        expected_title="Barricades (Movie Ver.)", expected_artist="Hiroyuki Sawano",
        task_id="t1", batch_id="b1",
        config_get=_cfg(enabled=False),
        list_entries=lambda d: _two_instrumental_entries(),
        approve_entry=approve, reprocess=reprocess,
    )
    assert ok is False
    assert calls == {"approve": 0, "reprocess": 0}


def test_orchestration_accepts_and_reprocesses_with_acoustid_bypass():
    captured = {}

    def approve(qdir, entry_id, rdir):
        captured["entry_id"] = entry_id
        return ("/restored.flac", {"existing": 1}, "acoustid")

    def reprocess(path, context, task_id, batch_id):
        captured["path"] = path
        captured["context"] = context
        captured["task_id"] = task_id

    ok = try_accept_version_mismatch_fallback(
        quarantine_dir="/q", restore_dir="/r",
        expected_title="Barricades (Movie Ver.)", expected_artist="Hiroyuki Sawano",
        task_id="t1", batch_id="b1",
        config_get=_cfg(),
        list_entries=lambda d: _two_instrumental_entries(),
        approve_entry=approve, reprocess=reprocess,
    )
    assert ok is True
    assert captured["entry_id"] == "20260605_120100"      # oldest/best
    assert captured["path"] == "/restored.flac"
    assert captured["task_id"] == "t1"
    # Only AcoustID bypassed — integrity/bit-depth gates still run.
    assert captured["context"]["_skip_quarantine_check"] == "acoustid"
    assert captured["context"]["_version_mismatch_fallback"] == "instrumental"
    assert captured["context"]["task_id"] == "t1"
    assert captured["context"]["batch_id"] == "b1"


def test_orchestration_no_candidate_does_not_reprocess():
    calls = {"reprocess": 0}

    def reprocess(*a, **k):
        calls["reprocess"] += 1

    ok = try_accept_version_mismatch_fallback(
        quarantine_dir="/q", restore_dir="/r",
        expected_title="Barricades (Movie Ver.)", expected_artist="Hiroyuki Sawano",
        task_id="t1", batch_id="b1",
        config_get=_cfg(),
        list_entries=lambda d: [_entry("20260605_120100", _vm("instrumental"))],  # 1 < min 2
        approve_entry=lambda *a, **k: ("/x", {}, "acoustid"),
        reprocess=reprocess,
    )
    assert ok is False
    assert calls["reprocess"] == 0


def test_orchestration_approve_failure_returns_false():
    ok = try_accept_version_mismatch_fallback(
        quarantine_dir="/q", restore_dir="/r",
        expected_title="Barricades (Movie Ver.)", expected_artist="Hiroyuki Sawano",
        task_id="t1", batch_id="b1",
        config_get=_cfg(),
        list_entries=lambda d: _two_instrumental_entries(),
        approve_entry=lambda *a, **k: None,   # thin sidecar / move failed
        reprocess=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not reprocess")),
    )
    assert ok is False
