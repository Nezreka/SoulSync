"""#2206: a re-identify comes back asking for the wrong song.

wolf39us re-identified "Let It Be" (track 26 of the Beatles' "1") to the Let
It Be album. the file is the 3:50 single cut and the album lists the song at
4:03, so the tracklist matcher turned the real song down on length and took
"Dig a Pony" (3:55, track 2) on the artist and a shared "Remastered" instead.
the import then held the file to Dig a Pony's length and quarantined it,
asking for Dig a Pony.

the user's pick rides through the hint by id; when the tracklist spells ids
another way the pick's title decides, and position only counts when the
titles agree or there's no title.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core import auto_import_worker as aiw
from core.imports.file_integrity import expected_duration_for_check
from core.imports.rematch_apply import build_reidentify_hint
from core.imports.rematch_hints import build_identification_from_hint, pick_hinted_track
from core.imports.rematch_search import resolve_hint_fields
from tests.imports.test_reidentify_1567 import _worker, hermetic, library  # noqa: F401

_LET_IT_BE = [
    ("Two Of Us", 216), ("Dig A Pony", 235), ("Across The Universe", 228),
    ("I Me Mine", 146), ("Dig It", 50), ("Let It Be", 243), ("Maggie Mae", 40),
    ("I've Got A Feeling", 218), ("One After 909", 174),
    ("The Long And Winding Road", 218), ("For You Blue", 152), ("Get Back", 189),
]


def _tracks(ids=None):
    return [{"id": (ids or "t{}").format(n), "name": f"{name} - Remastered 2009",
             "track_number": n, "disc_number": 1, "duration_ms": secs * 1000,
             "artists": [{"name": "The Beatles"}], "source": "spotify"}
            for n, (name, secs) in enumerate(_LET_IT_BE, start=1)]


STAGED = "/Staging/26 - Let It Be (Remastered 2015) [reid-32149].flac"
FILE_TAGS = {"title": "Let It Be (Remastered 2015)", "artist": "The Beatles", "album": "1",
             "track_number": 26, "disc_number": 1, "duration_ms": 230400}


class _Client:
    """the source the modal searched: the picked row is Let It Be on the album"""

    def __init__(self, tracks):
        self._tracks = tracks

    def get_track_details(self, track_id):
        track = next(t for t in self._tracks if t["id"] == track_id)
        return {"name": track["name"], "track_number": track["track_number"], "disc_number": 1,
                "album": {"id": "alb-lib", "name": "Let It Be (Remastered)", "album_type": "album",
                          "total_tracks": 12},
                "artists": [{"id": "beatles", "name": "The Beatles"}]}

    def get_album(self, album_id):
        return {"id": album_id, "name": "Let It Be (Remastered)", "album_type": "album",
                "total_tracks": 12, "tracks": {"items": self._tracks}}


def _identification_for(picked_id, tracks):
    """the modal's pick through resolve → hint → identification, as the apply
    route and the worker do it"""
    fields = resolve_hint_fields("spotify", picked_id, client_factory=lambda _s: _Client(tracks))
    hint = build_reidentify_hint(32149, fields, STAGED, "sig", replace=True, replace_file_id=7)
    return hint, build_identification_from_hint(hint)


def _match(identification, tracks, monkeypatch, tags=FILE_TAGS):
    monkeypatch.setattr(aiw, "_read_file_tags", lambda _f: dict(tags))
    worker = aiw.AutoImportWorker.__new__(aiw.AutoImportWorker)
    candidate = aiw.FolderCandidate(path="/Staging", name=STAGED.rsplit("/", 1)[1],
                                    audio_files=[STAGED], folder_hash="h")
    client = MagicMock(wraps=_Client(tracks))
    with patch("core.metadata_service.get_client_for_source", return_value=client), \
         patch("core.metadata_service.get_album_tracks_for_source", return_value=None):
        return worker._match_tracks(candidate, identification)


def test_let_it_be_from_1_files_as_let_it_be_end_to_end(monkeypatch):
    tracks = _tracks()
    hint, ident = _identification_for("t6", tracks)
    # the pick is carried exactly into the hint
    assert (hint.track_id, hint.track_title, hint.track_number) == ("t6", "Let It Be - Remastered 2009", 6)
    assert ident["track_id"] == "t6" and ident["hint_track_number"] == 6

    result = _match(ident, tracks, monkeypatch)
    assert [m["track"]["name"] for m in result["matches"]] == ["Let It Be - Remastered 2009"]


def test_tracklist_ids_spelled_another_way_still_go_by_the_title(monkeypatch):
    # a source whose album tracklist doesn't carry the search row's id: the
    # pick's title decides, never the file's 26 or a stale 2
    picked = _tracks()
    _hint, ident = _identification_for("t6", picked)
    ident["hint_track_number"] = 2      # a stale number that names Dig a Pony
    result = _match(ident, _tracks("alb:{}"), monkeypatch)
    assert [m["track"]["name"] for m in result["matches"]] == ["Let It Be - Remastered 2009"]


def test_a_pick_the_file_title_disagrees_with_is_still_carried_exactly(monkeypatch):
    # the user is the authority: they said this file is Get Back
    tracks = _tracks()
    _hint, ident = _identification_for("t12", tracks)
    result = _match(ident, tracks, monkeypatch)
    assert [m["track"]["id"] for m in result["matches"]] == ["t12"]


def test_a_title_the_release_lacks_never_lands_on_another_song(monkeypatch):
    # neither id nor title places it: the fuzzy matcher's Dig a Pony is
    # refused instead of filed
    tracks = [t for t in _tracks() if t["id"] != "t6"]
    ident = build_identification_from_hint(build_reidentify_hint(
        32149, {"source": "spotify", "track_id": "gone", "album_id": "alb-lib",
                "track_title": "Let It Be - Remastered 2009", "track_number": 6},
        STAGED, "sig", replace=True))
    assert _match(ident, tracks, monkeypatch) is None


def test_position_counts_only_when_titles_agree_or_say_nothing():
    tracks = _tracks("x{}")
    # titles say Let It Be, number says Dig a Pony: the title wins
    assert pick_hinted_track(tracks, title="Let It Be", track_number=2)["track_number"] == 6
    # no title anywhere: the pick's position is all there is
    assert pick_hinted_track(tracks, title="", file_title="Track 02", track_number=2)["track_number"] == 2
    # two agreeing titles: the file's length breaks the tie, then position
    twins = [dict(tracks[5]), dict(tracks[5], id="bonus", track_number=13, duration_ms=230000)]
    assert pick_hinted_track(twins, title="Let It Be", file_duration_ms=230400)["id"] == "bonus"
    same = [dict(tracks[5], duration_ms=0), dict(tracks[5], id="bonus", track_number=13, duration_ms=0)]
    assert pick_hinted_track(same, title="Let It Be", track_number=13)["id"] == "bonus"
    # nothing agrees: no pick at all
    assert pick_hinted_track(tracks[:2], title="Let It Be", track_number=2) is None


def test_without_a_hint_the_tracklist_matcher_is_unchanged(monkeypatch):
    # an ordinary staged file keeps the old behaviour, wrong as it is here
    tracks = _tracks()
    result = _match({"method": "tags", "source": "spotify", "album_id": "alb-lib",
                     "album_name": "Let It Be (Remastered)", "track_id": "t6",
                     "identification_confidence": 1.0, "force_album_match": True},
                    tracks, monkeypatch)
    assert [m["track"]["id"] for m in result["matches"]] == ["t2"]


def test_the_review_asks_for_let_it_be_and_skips_the_length_check(tmp_path, library, monkeypatch):
    """what the pipeline gets: the review's "asked for" is the corrected
    track, and the duration leg (3:50 single cut vs the album's 4:03) is off
    for the user's own re-staged file"""
    tracks = _tracks()
    _hint, ident = _identification_for("t6", tracks)
    result = _match(ident, tracks, monkeypatch)

    staged = tmp_path / "Staging" / "26 - Let It Be (Remastered 2015) [reid-32149].flac"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"staged copy")
    result["matches"][0]["file"] = str(staged)
    seen = []

    def _capture(_key, ctx, _path):
        seen.append(ctx)

    w = _worker(tmp_path, db_path=library, callback=_capture)
    from tests.imports.test_auto_import_library_precheck import _Candidate
    cand = _Candidate(path=str(staged), name=staged.name, audio_files=[str(staged)], is_single=True)
    w._process_matches(cand, dict(ident, image_url="", release_date="1970"), result)

    [ctx] = seen
    assert ctx["track_info"]["name"] == "Let It Be - Remastered 2009"
    assert ctx["track_info"]["track_number"] == 6
    assert expected_duration_for_check(ctx["track_info"]["duration_ms"], ctx.get("is_local_import")) is None


def test_a_shared_remaster_tail_is_no_likeness():
    from core.imports.reassign_album import titles_conflict
    # half the words shared, all of them pressing words
    assert titles_conflict("Let It Be - Remastered 2009", "Dig It - Remastered 2009")
    # the same song under two pressings is still one song
    assert not titles_conflict("Let It Be (Remastered 2015)", "Let It Be - Remastered 2009")
    assert not titles_conflict("1979 - Remastered 2012", "1979")
