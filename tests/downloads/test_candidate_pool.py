"""build_source_rows: the inspector payload for one download source."""

from __future__ import annotations

from core.download_plugins.types import TrackResult
from core.downloads.candidate_pool import (
    build_source_rows,
    candidate_row,
    empty_source_rows,
)
from core.downloads.decisions import accept, reject


def _row(username='alice', filename='A/B/01 - Song.flac', **over):
    base = dict(username=username, filename=filename, size=31_457_280, bitrate=1411,
                duration=200_000, quality='flac', free_upload_slots=2, upload_speed=1,
                queue_length=3, artist='Artist', title='Song', bit_depth=24, sample_rate=96_000)
    base.update(over)
    return TrackResult(**base)


def _never(_u, _f):
    return False


def test_row_shape_keeps_the_old_fields_and_adds_evidence():
    row = candidate_row(_row(), accept(0.91), source_name='soulseek', query='artist song')
    assert row['display_name'] == '01 - Song.flac'
    assert row['quality'] == 'FLAC'
    assert row['size_display'] == '30.0 MB'
    assert row['confidence'] == 0.91
    assert row['source_service'] == 'soulseek'
    assert row['source_query'] == 'artist song'
    assert row['blacklisted'] is False
    assert (row['artist'], row['title']) == ('Artist', 'Song')
    assert (row['bit_depth'], row['sample_rate']) == (24, 96_000)
    assert row['quality_label']
    assert row['decision'] == {'accepted': True, 'code': 'accepted', 'detail': '',
                               'stage': 'decision', 'score': 0.91}


def test_service_username_names_the_service():
    row = candidate_row(_row(username='tidal', filename='tid||x'), accept(0.9),
                        source_name='default', query='q')
    assert row['source_service'] == 'tidal'
    assert candidate_row(_row(), accept(0.9), source_name='default', query='q')[
        'source_service'] == 'hybrid'


def test_rejected_row_falls_back_to_the_candidate_score():
    c = _row()
    c.confidence = 0.42
    row = candidate_row(c, reject('match_weak', 'x'), source_name='soulseek', query='q')
    assert row['confidence'] == 0.42
    assert row['decision']['accepted'] is False


def test_split_sort_and_counts():
    good, better, weak, live = _row(filename='1'), _row(filename='2'), _row(filename='3'), _row(filename='4')
    out = build_source_rows(
        [('q', [(good, accept(0.8)), (better, accept(0.95)),
                (weak, reject('match_weak', score=0.3)), (live, reject('version_conflict', score=0.5))])],
        source_name='soulseek', is_blacklisted=_never,
    )
    assert [r['filename'] for r in out['candidates']] == ['2', '1']
    assert [r['filename'] for r in out['rejected']] == ['4', '3']
    assert out['rejected_total'] == 2
    assert out['rejected_counts'] == {'version_conflict': 1, 'match_weak': 1}


def test_same_file_from_two_queries_is_one_row_and_accepted_wins():
    first = _row(filename='same')
    second = _row(filename='same')
    out = build_source_rows(
        [('q1', [(first, reject('match_weak', score=0.5))]),
         ('q2', [(second, accept(0.9))])],
        source_name='soulseek', is_blacklisted=_never,
    )
    assert len(out['candidates']) == 1 and out['rejected'] == []
    assert out['candidates'][0]['source_query'] == 'q2'


def test_first_rejection_is_kept_when_both_sightings_reject():
    out = build_source_rows(
        [('q1', [(_row(filename='same'), reject('preview'))]),
         ('q2', [(_row(filename='same'), reject('match_weak'))])],
        source_name='soulseek', is_blacklisted=_never,
    )
    assert out['rejected_total'] == 1
    assert out['rejected'][0]['decision']['code'] == 'preview'


def test_blacklisted_accepted_row_becomes_a_rejection():
    bad = _row(username='mallory', filename='bad')
    out = build_source_rows(
        [('q', [(bad, accept(0.97)), (_row(filename='ok'), accept(0.9))])],
        source_name='soulseek',
        is_blacklisted=lambda u, f: (u, f) == ('mallory', 'bad'),
    )
    assert [r['filename'] for r in out['candidates']] == ['ok']
    assert out['rejected'][0]['decision']['code'] == 'blacklisted'
    assert out['rejected'][0]['blacklisted'] is True
    assert out['rejected'][0]['confidence'] == 0.97


def test_blacklist_read_failure_does_not_sink_the_source():
    def boom(_u, _f):
        raise RuntimeError('db locked')

    out = build_source_rows([('q', [(_row(), accept(0.9))])],
                            source_name='soulseek', is_blacklisted=boom)
    assert len(out['candidates']) == 1


def test_rejected_rows_are_capped_but_counted():
    pairs = [(_row(filename=str(i)), reject('match_weak', score=i / 100)) for i in range(80)]
    out = build_source_rows([('q', pairs)], source_name='soulseek',
                            is_blacklisted=_never, reject_cap=10)
    assert len(out['rejected']) == 10
    assert out['rejected_total'] == 80
    assert out['rejected_counts'] == {'match_weak': 80}
    assert out['rejected'][0]['filename'] == '79'  # best-scoring rejections first


def test_unscored_rejections_sort_last():
    out = build_source_rows(
        [('q', [(_row(filename='a'), reject('preview')),
                (_row(filename='b'), reject('match_weak', score=0.2))])],
        source_name='soulseek', is_blacklisted=_never,
    )
    assert [r['filename'] for r in out['rejected']] == ['b', 'a']


def test_empty_rows_shape():
    assert empty_source_rows() == {'candidates': [], 'rejected': [],
                                   'rejected_total': 0, 'rejected_counts': {}}
    assert empty_source_rows('boom')['error'] == 'boom'
