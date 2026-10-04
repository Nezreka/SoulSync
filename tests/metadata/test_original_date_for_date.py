"""Tests for issue #1451 — option to write the original release date
(release-group first-release-date) as DATE, plus the independent precision
guard that never downgrades a more precise same-year date.

Pattern follows tests/metadata/test_musicbrainz_release_tags.py: a stubbed
MusicBrainz runtime builds real post-process state via the production
``_process_musicbrainz_source`` pipeline, then ``_write_embedded_metadata``
is exercised against in-memory audio objects.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mutagen.id3 import ID3
from mutagen.flac import FLAC
from mutagen._vorbis import VCommentDict

from core.metadata import source as ms
from core.metadata.common import get_mutagen_symbols


class Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def release(rid="chosen"):
    credit = [{"name": "Madonna", "artist": {"id": "artist", "name": "Madonna", "sort-name": "Madonna"}}]
    return {"id": rid, "date": "2005-11-14", "title": "Confessions", "artist-credit": credit,
            "status": "Official", "release-group": {"id": "group", "first-release-date": "2005-11-11", "primary-type": "Album"},
            "label-info": [{"label": {"name": "Warner Bros. Records"}, "catalog-number": "CAT"}],
            "media": [{"position": 1, "format": "Digital Media", "tracks": [
                {"id": rid + "-track", "position": 1, "title": "Hung Up", "recording": {"id": rid + "-recording"}}]}]}


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(ms, "mb_release_cache", {("confessions", "madonna"): "wrong"})
    monkeypatch.setattr(ms, "mb_release_detail_cache", {})
    client = SimpleNamespace(get_release=Mock(side_effect=lambda rid, **kw: release(rid)),
                             get_recording=Mock(return_value={"isrcs": ["US1"], "artist-credit": release()["artist-credit"]}))
    service = SimpleNamespace(mb_client=client, match_release=Mock(side_effect=AssertionError("must not select another edition")),
                              match_recording=Mock(side_effect=AssertionError("must use release recording")),
                              match_artist=Mock(side_effect=AssertionError("must use credits")))
    return SimpleNamespace(mb_worker=SimpleNamespace(mb_service=service))


def process(runtime, rid="chosen"):
    metadata = {"musicbrainz_release_id": rid, "title": "Hung Up", "album": "Confessions", "artist": "Madonna", "date": "2005-01-01", "track_number": 1, "disc_number": 1}
    state = ms._blank_post_process_state()
    ms._process_musicbrainz_source(state, metadata, Config(), runtime, "Hung Up", "Madonna")
    return metadata, state


def _flac_audio():
    audio = FLAC.__new__(FLAC)
    audio.tags = VCommentDict()
    return audio


# ---------------------------------------------------------------------------
# _more_precise_date unit tests
# ---------------------------------------------------------------------------


class TestMorePreciseDate:
    def test_more_precise_same_year_wins(self):
        assert ms._more_precise_date("1991-08-12", "1991") == "1991-08-12"

    def test_more_precise_new_wins(self):
        assert ms._more_precise_date("1991", "1991-08-12") == "1991-08-12"

    def test_different_years_new_wins(self):
        # A genuine correction (different year) is not a precision loss.
        assert ms._more_precise_date("2001-01-01", "2000-05-23") == "2000-05-23"

    def test_same_precision_new_wins(self):
        assert ms._more_precise_date("2008-01-01", "2008-10-07") == "2008-10-07"

    def test_empty_existing(self):
        assert ms._more_precise_date("", "1991") == "1991"
        assert ms._more_precise_date(None, "1991") == "1991"

    def test_empty_new(self):
        assert ms._more_precise_date("1991-08-12", "") == ""

    def test_equal_values(self):
        assert ms._more_precise_date("2005", "2005") == "2005"


# ---------------------------------------------------------------------------
# Option off (default): current behavior + precision guard
# ---------------------------------------------------------------------------


class TestOptionOff:
    def test_default_writes_edition_date(self, runtime):
        metadata, state = process(runtime)
        ms._write_embedded_metadata(_flac_audio(), metadata, state, Config(), get_mutagen_symbols())
        assert metadata["date"] == "2005-11-14"
        assert state["id_tags"]["ORIGINALDATE"] == "2005-11-11"  # still tagged

    def test_precision_guard_blocks_same_year_downgrade(self, runtime):
        """Issue #1451: source '1991-08-12' must not become MB edition '1991'."""
        metadata, state = process(runtime)
        metadata["date"] = "1991-08-12"  # precise source date
        state["id_tags"]["DATE"] = "1991"  # vaguer MB edition date
        ms._write_embedded_metadata(_flac_audio(), metadata, state, Config(), get_mutagen_symbols())
        assert metadata["date"] == "1991-08-12"

    def test_precision_guard_allows_same_year_upgrade(self, runtime):
        metadata, state = process(runtime)
        metadata["date"] = "1991"  # bare-year source date
        state["id_tags"]["DATE"] = "1991-08-12"  # more precise MB date
        ms._write_embedded_metadata(_flac_audio(), metadata, state, Config(), get_mutagen_symbols())
        assert metadata["date"] == "1991-08-12"

    def test_precision_guard_allows_year_correction(self, runtime):
        metadata, state = process(runtime)
        metadata["date"] = "2001-01-01"  # source reissue placeholder date
        state["id_tags"]["DATE"] = "2000-05-23"  # MB correction, different year
        ms._write_embedded_metadata(_flac_audio(), metadata, state, Config(), get_mutagen_symbols())
        assert metadata["date"] == "2000-05-23"


# ---------------------------------------------------------------------------
# Option on: original date becomes DATE
# ---------------------------------------------------------------------------


class TestOptionOn:
    CFG = {"musicbrainz.use_original_date_for_date": True}

    def test_original_date_becomes_date(self, runtime):
        metadata, state = process(runtime)
        audio = _flac_audio()
        ms._write_embedded_metadata(audio, metadata, state, Config(**self.CFG), get_mutagen_symbols())
        assert metadata["date"] == "2005-11-11"  # release-group first-release-date
        assert audio["DATE"] == ["2005-11-11"]   # actually written to the file
        assert audio["ORIGINALDATE"] == ["2005-11-11"]  # original tags intact

    def test_id3_original_date_becomes_tdrc(self, runtime):
        metadata, state = process(runtime)
        audio = SimpleNamespace(tags=ID3())
        ms._write_embedded_metadata(audio, metadata, state, Config(**self.CFG), get_mutagen_symbols())
        assert str(audio.tags["TDRC"]) == "2005-11-11"

    def test_missing_original_date_falls_back_to_edition(self, runtime, monkeypatch):
        def no_first_release(rid="chosen"):
            r = release(rid)
            r["release-group"] = {"id": "group", "primary-type": "Album"}  # no first-release-date
            return r
        runtime.mb_worker.mb_service.mb_client.get_release.side_effect = lambda rid, **kw: no_first_release(rid)
        metadata, state = process(runtime)
        ms._write_embedded_metadata(_flac_audio(), metadata, state, Config(**self.CFG), get_mutagen_symbols())
        assert metadata["date"] == "2005-11-14"  # edition date, current behavior

    def test_option_on_still_writes_date_when_release_date_tag_disabled(self, runtime):
        """The option implies writing DATE even if musicbrainz.tags.release_date is off."""
        metadata, state = process(runtime)
        cfg = Config(**self.CFG, **{"musicbrainz.tags.release_date": False})
        ms._write_embedded_metadata(_flac_audio(), metadata, state, cfg, get_mutagen_symbols())
        assert metadata["date"] == "2005-11-11"

    def test_option_on_never_downgrades_precision(self, runtime):
        """Issue #1451, independent of the option: a year-only original must
        not replace a more precise same-year edition date."""
        metadata, state = process(runtime)
        state["id_tags"]["DATE"] = "1991-08-12"       # precise edition date
        state["id_tags"]["ORIGINALDATE"] = "1991"      # vaguer original
        audio = _flac_audio()
        ms._write_embedded_metadata(audio, metadata, state, Config(**self.CFG), get_mutagen_symbols())
        assert metadata["date"] == "1991-08-12"
        assert audio["DATE"] == ["1991-08-12"]

    def test_option_on_precise_original_replaces_bare_year_edition(self, runtime):
        """The Metallica example: edition '1991', original '1991-08-12'."""
        metadata, state = process(runtime)
        state["id_tags"]["DATE"] = "1991"
        state["id_tags"]["ORIGINALDATE"] = "1991-08-12"
        ms._write_embedded_metadata(_flac_audio(), metadata, state, Config(**self.CFG), get_mutagen_symbols())
        assert metadata["date"] == "1991-08-12"

    def test_option_on_missing_original_still_guards_precision(self, runtime):
        """Option ON but no first-release-date: the independent guard must
        still protect a precise source date from a vaguer edition date."""
        metadata, state = process(runtime)
        metadata["date"] = "1991-08-12"   # precise source date
        state["id_tags"]["DATE"] = "1991"  # vaguer MB edition date
        state["id_tags"].pop("ORIGINALDATE", None)
        ms._write_embedded_metadata(_flac_audio(), metadata, state, Config(**self.CFG), get_mutagen_symbols())
        assert metadata["date"] == "1991-08-12"

    def test_no_musicbrainz_tags_keeps_source_date(self, runtime):
        metadata = {"title": "Hung Up", "date": "2005-01-01"}
        state = ms._blank_post_process_state()  # no id_tags at all
        ms._write_embedded_metadata(_flac_audio(), metadata, state, Config(**self.CFG), get_mutagen_symbols())
        assert metadata["date"] == "2005-01-01"
