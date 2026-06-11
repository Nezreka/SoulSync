"""Seam tests for resolve_history_audio_path — the fallback chain a DESTRUCTIVE
delete trusts. The collision-safety rules (artist filter / single-candidate) are
what stop delete() from removing the wrong same-title file, so they're locked here."""

from __future__ import annotations

from core.matching.history_paths import resolve_history_audio_path


def _resolver(existing=(), resolve_map=None, titled=None):
    existing = set(existing)
    resolve_map = resolve_map or {}
    titled = titled or {}
    return dict(
        exists=lambda p: p in existing,
        resolve_library_path=lambda raw: resolve_map.get(raw),
        lookup_titled_paths=lambda title: list(titled.get(title.lower(), [])),
    )


def test_recorded_path_used_when_it_exists():
    r = resolve_history_audio_path({'file_path': '/m/a.mp3'}, **_resolver(existing={'/m/a.mp3'}))
    assert r == '/m/a.mp3'


def test_falls_back_to_prefix_resolved_path():
    r = resolve_history_audio_path(
        {'file_path': '/transfer/a.mp3'},
        **_resolver(existing={'/library/a.mp3'}, resolve_map={'/transfer/a.mp3': '/library/a.mp3'}))
    assert r == '/library/a.mp3'


def test_tracks_table_single_candidate_no_artist():
    r = resolve_history_audio_path(
        {'file_path': '/gone.mp3', 'title': 'Song'},
        **_resolver(existing={'/library/song.mp3'},
                    resolve_map={'/lib/song.mp3': '/library/song.mp3'},
                    titled={'song': ['/lib/song.mp3']}))
    assert r == '/library/song.mp3'


def test_collision_no_artist_multiple_candidates_returns_none():
    # THE safety rule: same title, no artist to disambiguate -> refuse to guess
    # (delete() must not remove an arbitrary one of two same-title files).
    r = resolve_history_audio_path(
        {'file_path': '', 'title': 'Intro'},
        **_resolver(existing={'/library/a/intro.mp3', '/library/b/intro.mp3'},
                    resolve_map={'/a/intro.mp3': '/library/a/intro.mp3', '/b/intro.mp3': '/library/b/intro.mp3'},
                    titled={'intro': ['/a/intro.mp3', '/b/intro.mp3']}))
    assert r is None


def test_artist_filter_picks_only_the_matching_path():
    # Two same-title files by different artists -> only the one whose path
    # mentions the row's artist is eligible (won't delete the other artist's file).
    r = resolve_history_audio_path(
        {'file_path': '', 'title': 'Intro', 'artist_name': 'Alpha'},
        **_resolver(existing={'/music/Alpha/intro.mp3', '/music/Beta/intro.mp3'},
                    resolve_map={'/Alpha/intro.mp3': '/music/Alpha/intro.mp3',
                                 '/Beta/intro.mp3': '/music/Beta/intro.mp3'},
                    titled={'intro': ['/Alpha/intro.mp3', '/Beta/intro.mp3']}))
    assert r == '/music/Alpha/intro.mp3'


def test_artist_named_but_no_path_mentions_it_returns_none():
    r = resolve_history_audio_path(
        {'file_path': '', 'title': 'Intro', 'artist_name': 'Gamma'},
        **_resolver(existing={'/music/Alpha/intro.mp3'},
                    resolve_map={'/Alpha/intro.mp3': '/music/Alpha/intro.mp3'},
                    titled={'intro': ['/Alpha/intro.mp3']}))
    assert r is None


def test_no_title_returns_none():
    assert resolve_history_audio_path({'file_path': '/gone.mp3'}, **_resolver()) is None


def test_nothing_resolves_returns_none():
    assert resolve_history_audio_path(
        {'file_path': '/gone.mp3', 'title': 'X'},
        **_resolver(titled={'x': ['/also/gone.mp3']})) is None


def test_empty_lookup_returns_none():
    # DB-error proxy: lookup yields [] -> None (never a stray delete target).
    assert resolve_history_audio_path(
        {'file_path': '', 'title': 'X'}, **_resolver(titled={})) is None
