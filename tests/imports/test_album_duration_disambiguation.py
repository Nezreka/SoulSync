"""Tests for album-variant disambiguation by staged-file durations."""

from __future__ import annotations

from types import SimpleNamespace

from core.imports.album_matching import (
    pick_album_by_duration_fit,
    score_album_duration_fit,
)


def _album(album_id: str, name: str):
    return SimpleNamespace(id=album_id, name=name)


def _tracks(*entries):
    """``(track_number, duration_ms)`` tuples."""
    return [
        {'name': f'Track {num}', 'track_number': num, 'duration_ms': dur}
        for num, dur in entries
    ]


class TestScoreAlbumDurationFit:
    def test_all_files_match_by_track_number(self):
        file_tags = {
            'a.mp3': {'duration_ms': 261000, 'track_number': 3},
            'b.mp3': {'duration_ms': 236000, 'track_number': 2},
        }
        tracks = _tracks((2, 237000), (3, 261000))
        assert score_album_duration_fit(file_tags, tracks) == 1.0

    def test_partial_fit_when_one_track_wrong_duration(self):
        """Mirrors the Sheesha case on the wrong JioSaavn album listing."""
        file_tags = {
            'jaane.mp3': {'duration_ms': 261000, 'track_number': 3},
            'har.mp3': {'duration_ms': 278000, 'track_number': 4},
            'khawa.mp3': {'duration_ms': 254000, 'track_number': 8},
            'sheesha.mp3': {'duration_ms': 236000, 'track_number': 2},
        }
        wrong_album = _tracks(
            (2, 336000), (3, 261000), (4, 278000), (8, 254000),
        )
        right_album = _tracks(
            (2, 237000), (3, 262000), (4, 279000), (8, 255000),
        )
        assert score_album_duration_fit(file_tags, wrong_album) == 0.75
        assert score_album_duration_fit(file_tags, right_album) == 1.0


class TestPickAlbumByDurationFit:
    def test_picks_better_duration_fit_among_similar_name_scores(self):
        album_a = _album('1017247', '3 Nights 4 Days')
        album_b = _album('1062318', '3 Nights And 4 Days')
        scored = [(0.92, album_a), (0.88, album_b)]
        file_tags = {
            'jaane.mp3': {'duration_ms': 261000, 'track_number': 3},
            'har.mp3': {'duration_ms': 278000, 'track_number': 4},
            'khawa.mp3': {'duration_ms': 254000, 'track_number': 8},
            'sheesha.mp3': {'duration_ms': 236000, 'track_number': 2},
        }
        tracks_by_id = {
            '1017247': _tracks(
                (2, 336000), (3, 261000), (4, 278000), (8, 254000),
            ),
            '1062318': _tracks(
                (2, 237000), (3, 262000), (4, 279000), (8, 255000),
            ),
        }
        picked, fit, used = pick_album_by_duration_fit(
            scored, file_tags, tracks_by_id,
        )
        assert used is True
        assert picked.id == '1062318'
        assert fit == 1.0

    def test_skips_tiebreak_when_name_scores_not_close(self):
        album_a = _album('1', 'Album A')
        album_b = _album('2', 'Album B')
        scored = [(0.95, album_a), (0.50, album_b)]
        file_tags = {'a.mp3': {'duration_ms': 200000, 'track_number': 1}}
        tracks_by_id = {
            '1': _tracks((1, 300000)),
            '2': _tracks((1, 200000)),
        }
        picked, _fit, used = pick_album_by_duration_fit(
            scored, file_tags, tracks_by_id,
        )
        assert used is False
        assert picked.id == '1'

    def test_skips_tiebreak_without_file_durations(self):
        album_a = _album('1', 'Album A')
        album_b = _album('2', 'Album B')
        scored = [(0.9, album_a), (0.85, album_b)]
        picked, _fit, used = pick_album_by_duration_fit(
            scored, {'a.mp3': {'title': 'Song'}}, {},
        )
        assert used is False
        assert picked.id == '1'

    def test_keeps_top_hit_when_its_tracklist_fetch_failed(self):
        """Failed get_album must not score as 0%% fit and lose to a lower hit."""
        album_a = _album('1', '3 Nights 4 Days')
        album_b = _album('2', '3 Nights And 4 Days')
        scored = [(0.92, album_a), (0.88, album_b)]
        file_tags = {'sheesha.mp3': {'duration_ms': 236000, 'track_number': 2}}
        tracks_by_id = {
            '2': _tracks((2, 237000)),
        }
        picked, _fit, used = pick_album_by_duration_fit(
            scored, file_tags, tracks_by_id,
        )
        assert used is False
        assert picked.id == '1'

    def test_disc_aware_position_matching(self):
        """Same track number on different discs must not count as a position hit."""
        file_tags = {
            'd1.mp3': {'duration_ms': 200000, 'track_number': 1, 'disc_number': 1},
            'd2.mp3': {'duration_ms': 210000, 'track_number': 1, 'disc_number': 2},
        }
        tracks = [
            {'track_number': 1, 'disc_number': 1, 'duration_ms': 201000},
            {'track_number': 1, 'disc_number': 2, 'duration_ms': 211000},
        ]
        assert score_album_duration_fit(file_tags, tracks) == 1.0

        wrong_disc_tracks = [
            {'track_number': 1, 'disc_number': 2, 'duration_ms': 201000},
            {'track_number': 1, 'disc_number': 2, 'duration_ms': 211000},
        ]
        assert score_album_duration_fit(file_tags, wrong_disc_tracks) == 0.5
