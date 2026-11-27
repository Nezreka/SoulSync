"""base-title veto with titles in other scripts (#2329).

base_title_of kept only a-z and 0-9, so 魔法少女A folded to "a" and a russian
title lost everything but its english tail. the veto then compared those
leftovers and threw out the right file.
"""

from __future__ import annotations

import pytest

from core.download_plugins.types import TrackResult
from core.matching_engine import MusicMatchingEngine
from core.spotify_client import Track as SpotifyTrack


@pytest.fixture
def engine() -> MusicMatchingEngine:
    return MusicMatchingEngine()


def _track(name, artists, duration_ms=200000):
    return SpotifyTrack(
        id='1', name=name, artists=artists, album='Some Album',
        duration_ms=duration_ms, popularity=0, preview_url=None, external_urls={},
    )


def _result(filename, duration_ms=200000, username='peer'):
    return TrackResult(
        username=username, filename=filename, size=30_000_000, bitrate=1000,
        duration=duration_ms, quality='flac',
        free_upload_slots=1, upload_speed=100000, queue_length=0,
    )


def _score(engine, track, result):
    return engine.calculate_slskd_match_confidence_enhanced(track, result)


def test_base_title_keeps_every_script(engine):
    assert engine.base_title_of('魔法少女A') == '魔法少女a'
    assert engine.base_title_of('Когда душе твоей больно') == 'когда душе твоей больно'
    assert engine.base_title_of('사랑해') == '사랑해'
    assert engine.base_title_of('ガッチャ') == 'ガッチャ'
    assert engine.base_title_of('Café del Mar') == 'cafe del mar'
    # full-width forms fold to plain ones
    assert engine.base_title_of('ＡＢＣ　１２３') == 'abc 123'
    # 【】 is a bracket too
    assert engine.base_title_of('魔法少女A【MV】') == '魔法少女a'


def test_ascii_titles_fold_as_before(engine):
    assert engine.base_title_of("Don't Stop Me Now") == 'dont stop me now'
    assert engine.base_title_of('hip_hop-song (Remastered 2011)') == 'hiphopsong'
    assert engine.base_title_of(
        '03 - 99 Problems.flac', from_filename=True) == '99 problems'


def test_japanese_mv_from_the_issue_not_vetoed(engine):
    track = _track('魔法少女A', ['彩糸乃七'])
    result = _result(
        'ljZbvuZmQpg||彩糸乃七 feat.初音ミク / 魔法少女A( Magical Girl A)【MV】',
        username='youtube')
    agree, *_ = engine.base_titles_agree(
        '魔法少女A', '彩糸乃七 feat.初音ミク / 魔法少女A( Magical Girl A)【MV】',
        '彩糸乃七')
    assert agree
    conf, version = _score(engine, track, result)
    assert version != 'rejected_base_title_mismatch'
    assert conf >= 0.58


def test_russian_title_with_translation_from_the_issue_not_vetoed(engine):
    name = 'Когда душе твоей больно (When Your Soul Hurts) - Arknights Image Song'
    track = _track(name, ['Ploho'], duration_ms=254000)
    result = _result(
        'Ploho/Arknights/01. Ploho, 塞壬唱片-MSR - Когда душе твоей больно.flac',
        duration_ms=254000)
    conf, version = _score(engine, track, result)
    assert version != 'rejected_base_title_mismatch'
    assert conf >= 0.58


def test_translated_title_alone_agrees(engine):
    agree, *_ = engine.base_titles_agree(
        'Когда душе твоей больно (When Your Soul Hurts)',
        '01 - When Your Soul Hurts.flac', 'Ploho', from_filename=True)
    assert agree


def test_different_russian_song_still_vetoed(engine):
    agree, *_ = engine.base_titles_agree(
        'Дом', '05 - Звезда по имени Солнце.flac',
        'Ploho', from_filename=True)
    assert not agree


def test_different_japanese_song_still_vetoed(engine):
    agree, *_ = engine.base_titles_agree(
        '魔法少女A', '02 - 夜に駆ける.flac', '彩糸乃七', from_filename=True)
    assert not agree


def test_one_shared_kanji_is_not_enough(engine):
    agree, *_ = engine.base_titles_agree(
        '愛', '07 - 愛してるって言わなきゃ殺す.flac', 'x', from_filename=True)
    assert not agree


def test_latin_titles_get_no_bracket_alternates(engine):
    # (Orchestral Version) and (feat. X) never count as titles of their own
    agree, *_ = engine.base_titles_agree(
        'Crystalline (feat. Drake)', '07 - Damnation Flame feat. Drake.flac',
        'Dark Sanctuary', from_filename=True)
    assert not agree


def test_preferred_version_matches_non_latin_title(engine):
    track = _track('夜に駆ける', ['YOASOBI'])
    r = _result('YOASOBI/Single/01 - 夜に駆ける (Extended Mix).flac')
    r.version_type = 'remix'
    assert engine.preferred_match_ids(track, [r], 'remix') == {id(r)}
