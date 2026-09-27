"""Cross-source dedupe in best-quality mode (core/downloads/candidates.py).

``quality_first=True`` (best-quality) fans out across every chain source, so
the same recording surfaces once per source. ``dedupe_cross_source_pool``
collapses same-recording sightings in the already-ranked pool, keeping the
first (best-ranked) one. Priority mode, mixed-source priority pools, and the
confidence-band path never dedupe.
"""

from types import SimpleNamespace

from core.downloads.candidates import (
    _identity_key,
    order_candidates,
)


def _cand(source, artist="Artist", title="Song", duration_ms=210000,
          confidence=0.9, version_type="original"):
    return SimpleNamespace(
        username=source,
        filename="%s-%s.flac" % (title, source),
        artist=artist,
        title=title,
        duration=duration_ms,
        confidence=confidence,
        quality_score=0.0,
        upload_speed=0,
        queue_length=0,
        free_upload_slots=0,
        size=10_000_000,
        audio_quality=None,
        version_type=version_type,
    )


def test_same_release_two_sources_dedupes_keeping_best_ranked():
    a = _cand("peer1", confidence=0.90)
    b = _cand("youtube", confidence=0.95)
    ordered = order_candidates([a, b], quality_first=True, targets=[])
    # Same recording on two sources; the better-ranked sighting survives.
    assert [c.username for c in ordered] == ["youtube"]


def test_different_editions_are_kept():
    a = _cand("peer1", version_type="original")
    b = _cand("peer2", version_type="remix")
    ordered = order_candidates([a, b], quality_first=True, targets=[])
    assert len(ordered) == 2


def test_durations_beyond_the_10s_bucket_are_kept():
    a = _cand("peer1", duration_ms=210000)   # bucket 21
    b = _cand("peer2", duration_ms=221000)   # bucket 22 — a different cut
    ordered = order_candidates([a, b], quality_first=True, targets=[])
    assert len(ordered) == 2


def test_durations_within_the_10s_bucket_merge():
    a = _cand("peer1", duration_ms=210000, confidence=0.90)
    b = _cand("peer2", duration_ms=214999, confidence=0.95)  # same bucket
    ordered = order_candidates([a, b], quality_first=True, targets=[])
    assert [c.username for c in ordered] == ["peer2"]


def test_priority_mode_never_dedupes():
    a = _cand("peer1", confidence=0.90)
    b = _cand("peer2", confidence=0.95)
    ordered = order_candidates([a, b], quality_first=False, targets=[])
    assert len(ordered) == 2


def test_mixed_source_priority_pool_never_dedupes():
    # Mixed pools also rank by quality, but the gate is STRICTLY the
    # best-quality flag — priority mode keeps every source's sighting.
    a = _cand("peer1", confidence=0.90)
    b = _cand("youtube", confidence=0.95)
    ordered = order_candidates([a, b], quality_first=False, targets=[])
    assert len(ordered) == 2


def test_missing_identity_never_collapses():
    a = _cand("peer1", artist="")
    b = _cand("peer2", artist="")
    assert _identity_key(a) is None
    assert _identity_key(b) is None
    ordered = order_candidates([a, b], quality_first=True, targets=[])
    assert len(ordered) == 2


def test_missing_or_nonpositive_duration_never_collapses():
    a = _cand("peer1", duration_ms=0)
    b = _cand("peer2", duration_ms=None)
    assert _identity_key(a) is None
    assert _identity_key(b) is None
    ordered = order_candidates([a, b], quality_first=True, targets=[])
    assert len(ordered) == 2


def test_featuring_stripped_but_other_parentheticals_kept():
    feat = _cand("peer1", title="Song (feat. Guest)", confidence=0.90)
    plain = _cand("peer2", title="Song", confidence=0.95)
    merged = order_candidates([feat, plain], quality_first=True, targets=[])
    assert [c.username for c in merged] == ["peer2"]

    live = _cand("peer1", title="Song (Live)", confidence=0.90)
    studio = _cand("peer2", title="Song", confidence=0.95)
    kept = order_candidates([live, studio], quality_first=True, targets=[])
    assert len(kept) == 2
