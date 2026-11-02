"""music_database keeps the enhanced matching engine when it loses the import
cycle.

core.matching_engine reaches database.music_database through settings and the
db, so a process that imports the engine first finds it half loaded and the
import-time ``_matching_engine`` comes out None. that used to stick: every
similarity fell back to plain levenshtein, which scores 'Song (Live)' as the
studio 'Song', so a library check said a live version you don't have was
owned. it only showed up in some test orders, and a test that reloaded
music_database hid it.
"""

from __future__ import annotations

import database.music_database as mdb
from core.matching_engine import MusicMatchingEngine


def test_an_unset_engine_is_imported_on_first_use(monkeypatch):
    monkeypatch.setattr(mdb, "_matching_engine", None)
    monkeypatch.setattr(mdb, "_matching_engine_unavailable", False)
    assert isinstance(mdb._resolve_matching_engine(), MusicMatchingEngine)


def test_a_set_engine_is_kept(monkeypatch):
    engine = object()
    monkeypatch.setattr(mdb, "_matching_engine", engine)
    assert mdb._resolve_matching_engine() is engine


def test_a_live_version_scores_like_the_engine_not_levenshtein(tmp_path, monkeypatch):
    db = mdb.MusicDatabase(str(tmp_path / "m.db"))
    expected = MusicMatchingEngine().similarity_score(
        "llamando a la tierra live", "llamando a la tierra")
    monkeypatch.setattr(mdb, "_matching_engine", None)
    monkeypatch.setattr(mdb, "_matching_engine_unavailable", False)
    assert db._string_similarity("llamando a la tierra live", "llamando a la tierra") == expected
