"""The redownload modal's metadata step answers for Library v2 ids (the
artist-page cleanup removed it while Tools findings still open the modal)."""

from types import SimpleNamespace

import pytest

from database.music_database import MusicDatabase
from tests.lib2_seed import track


def test_search_metadata_reads_the_library_v2_track(tmp_path, monkeypatch):
    import core.library.redownload as redownload
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        tid = track(conn, "Various Artists", "Hitzone 43", "It's Not Over (Radio Edit)",
                    credit="Daughtry", duration=213000, spotify_id="sp1",
                    external_ids='{"deezer": "dz1"}', path="/m/a.flac")
        conn.commit()
    seen = {}

    def fake_search(query, sources, clean_title=None):
        seen.update(query=query, clean_title=clean_title)
        return SimpleNamespace(metadata_results={"deezer": [{"name": "It's Not Over"}]},
                               best_match={"source": "deezer"})

    monkeypatch.setattr(redownload, "get_database", lambda: db)
    monkeypatch.setattr("core.metadata.multi_source_search.search_all_sources", fake_search)
    monkeypatch.setattr(redownload, "spotify_client", None)

    result = redownload.search_metadata(tid)

    q = seen["query"]
    assert (q.artist, q.album, q.duration_ms, q.spotify_track_id, q.deezer_id) == (
        "Daughtry", "Hitzone 43", 213000, "sp1", "dz1")
    assert seen["clean_title"] == "It's Not Over"
    assert result["current_track"]["format"] == "FLAC"
    assert result["best_match"] == {"source": "deezer"}
    with pytest.raises(LookupError):
        redownload.search_metadata(tid + 999)
