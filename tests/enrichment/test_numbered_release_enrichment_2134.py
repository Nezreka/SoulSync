"""#2134: enrichment crossed 701 and 702's metadata.

each provider worker searches the album by name and keeps the top result
when its name scores 0.80. deezer's search for 'Group Therapy 701' returns
703 and 702 first, and the workers drop the '(...)' before scoring, so
'Group Therapy 702 (DJ Mix)' scored 0.94 of 701 and 'Off My Mind (ABGT697)
(Mixed)' scored 1.00 of 696's. the wrong ids were stored and the next
re-tag wrote the other episode's tags. lib2's native enrich had a sequence
guard, but it only read a number at the very end of the title, and these
end in '(DJ Mix)'.
"""

import importlib

import pytest

from core.library2.native_enrich import titles_are_same_release

_WORKERS = [
    ("core.amazon_worker", "AmazonWorker"),
    ("core.bandcamp_worker", "BandcampWorker"),
    ("core.audiodb_worker", "AudioDBWorker"),
    ("core.discogs_worker", "DiscogsWorker"),
    ("core.deezer_worker", "DeezerWorker"),
    ("core.genius_worker", "GeniusWorker"),
    ("core.itunes_worker", "iTunesWorker"),
    ("core.jiosaavn_worker", "JioSaavnWorker"),
    ("core.lastfm_worker", "LastFMWorker"),
    ("core.qobuz_worker", "QobuzWorker"),
    ("core.tidal_worker", "TidalWorker"),
    ("core.spotify_worker", "SpotifyWorker"),
]


def _worker(module, cls):
    worker = object.__new__(getattr(importlib.import_module(module), cls))
    worker.name_similarity_threshold = 0.80
    return worker


@pytest.mark.parametrize("module,cls", _WORKERS)
def test_worker_never_takes_another_episode(module, cls):
    worker = _worker(module, cls)
    assert not worker._name_matches("Group Therapy 701 (DJ Mix)", "Group Therapy 702 (DJ Mix)")
    assert not worker._name_matches("Off My Mind (ABGT696) (Mixed)", "Off My Mind (ABGT697) (Mixed)")
    assert worker._name_matches("Group Therapy 701 (DJ Mix)", "Group Therapy 701 (DJ Mix)")
    assert worker._name_matches("Off My Mind (ABGT696) (Mixed)", "Off My Mind (ABGT696)")


def test_native_enrich_sees_a_number_before_the_suffix():
    assert not titles_are_same_release("Group Therapy 701 (DJ Mix)", "Group Therapy 702 (DJ Mix)")
    assert not titles_are_same_release("Off My Mind (ABGT696) (Mixed)",
                                       "Off My Mind (ABGT697) (Mixed)")
    assert titles_are_same_release("Group Therapy 701 (DJ Mix)", "Group Therapy 701 [DJ Mix]")
