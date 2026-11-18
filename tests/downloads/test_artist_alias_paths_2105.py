"""#2105: soulseek had the song, youtube got it.

the reporter's two soulseek copies of "le petit poucet (live)" by joe hisaishi:
one sat under ``music\\久石譲\\...`` and was refused because the matcher needs
the artist named in the path and only knew "Joe Hisaishi". a request made in
japanese refused every copy filed under the romanized name the same way. the
artist's known aliases count as naming him now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from core.download_plugins.types import TrackResult
from core.downloads import validation
from core.downloads.validation import evaluate_candidates
from core.matching_engine import MusicMatchingEngine


@dataclass
class _Track:
    name: str
    artists: tuple
    duration_ms: int = 265_000
    album: Optional[str] = None


def _row(path, **over):
    base = dict(username='peer', filename=path, size=25_570_000, bitrate=None,
                duration=265_000, quality='flac', free_upload_slots=1,
                upload_speed=5_000_000, queue_length=0, bit_depth=16, sample_rate=44_100)
    base.update(over)
    return TrackResult(**base)


KANJI = '@@a\\music\\久石譲\\Spirited Away Suite (2019)\\02 - Le Petit Poucet (Live).flac'
ROMAN = ('@@b\\music\\Joe Hisaishi\\Joe Hisaishi - Spirited Away Suite (Live) (2019)\\'
         '2. Joe Hisaishi - Le Petit Poucet (Live).flac')
ALIASES = {'Joe Hisaishi': ['久石譲', 'Hisaishi Joe'], '久石譲': ['Joe Hisaishi']}


class _Slsk:
    def filter_results_by_quality_preference(self, rows, profile_id=None):
        return list(rows)


class _Orch:
    def client(self, name):
        return _Slsk() if name == 'soulseek' else None


@pytest.fixture
def env(monkeypatch):
    asked = []

    def aliases(name):
        asked.append(name)
        return ALIASES.get(name, [])

    monkeypatch.setattr(validation, 'matching_engine', MusicMatchingEngine())
    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    monkeypatch.setattr(validation, '_artist_aliases', aliases)
    real_get = validation.config_manager.get
    monkeypatch.setattr(validation.config_manager, 'get',
                        lambda key, default=None: 0 if key == 'soulseek.max_peer_queue'
                        else real_get(key, default))
    return asked


def _accepted(rows, track):
    return [r.filename for r, d in evaluate_candidates(rows, track, 'q') if d.accepted]


def test_a_folder_named_in_the_artists_own_script_is_his(env):
    """#2105: 久石譲 is joe hisaishi."""
    want = _Track('Le Petit Poucet (Live)', ('Joe Hisaishi',), album='Spirited Away Suite (Live)')
    assert sorted(_accepted([_row(KANJI), _row(ROMAN, username='other')], want)) == sorted(
        [KANJI, ROMAN])


def test_a_request_in_japanese_takes_the_romanized_folder(env):
    """#2105, the second example: the request itself named 久石譲."""
    want = _Track('Le Petit Poucet (Live)', ('久石譲',), album='Spirited Away Suite (Live)')
    assert ROMAN in _accepted([_row(KANJI), _row(ROMAN, username='other')], want)


def test_a_stranger_is_still_refused(env):
    want = _Track('Le Petit Poucet (Live)', ('Joe Hisaishi',))
    stranger = _row('@@c\\music\\Some Orchestra\\Ghibli Covers\\02 - Le Petit Poucet (Live).flac')
    pairs = evaluate_candidates([stranger], want, 'q')
    assert not pairs[0][1].accepted


def test_aliases_are_only_asked_for_when_something_was_refused(env):
    want = _Track('Le Petit Poucet (Live)', ('Joe Hisaishi',))
    assert _accepted([_row(ROMAN)], want) == [ROMAN]
    assert env == []


def test_a_live_request_never_reads_a_live_file_as_the_wrong_version(env, monkeypatch):
    """the detail sheet said "live version, asked for the original" for a live
    file on a live request; the file was refused for its folder, not that."""
    monkeypatch.setattr(validation, '_artist_aliases', lambda _name: [])
    want = _Track('Le Petit Poucet (Live)', ('Joe Hisaishi',))
    pairs = evaluate_candidates([_row(KANJI)], want, 'q')
    assert pairs[0][1].code != 'version_conflict'
