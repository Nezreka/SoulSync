"""#2074: Use File Tags for the whole library, as a job. One finding per
album whose files say something the Library does not; approving it runs the
album's own Use file tags."""

from __future__ import annotations

from core.repair_jobs.base import JobContext
from core.repair_jobs.use_file_tags import UseFileTagsJob, describe_plan
from core.repair_worker import RepairWorker
from tests.library2.test_tags_to_library_2070 import _best_of, lib  # noqa: F401


def _wire(lib, monkeypatch, tags):  # noqa: F811
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda p, **_k: p)
    monkeypatch.setattr(
        "core.tag_writer.read_file_tags",
        lambda path: tags.get(path.rsplit("/", 1)[-1].removesuffix(".flac"), {}))
    subjects = [
        {"album_id": lib["album"], "track_id": track_id, "file_id": n + 1,
         "path": f"/m/{title}.flac", "album_title": "The Very Best of Aerosmith",
         "album_artist_name": "Aerosmith", "artist_name": "Aerosmith",
         "artist_id": lib["artist"], "owner_profile_id": None}
        for n, (title, track_id) in enumerate(lib["tracks"].items())]
    monkeypatch.setattr(
        "core.library2.maintenance_subjects.active_file_subjects", lambda *_a, **_k: subjects)


def _scan(lib):  # noqa: F811
    findings = []
    result = UseFileTagsJob().scan(JobContext(
        db=lib["db"], config_manager=None, transfer_folder="/m",
        create_finding=lambda **kw: findings.append(kw) or True))
    return result, findings


def _worker(lib):  # noqa: F811
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = lib["db"], None, "/m"
    return worker


SAME = {name: _best_of(title=name, track_number=str(n))
        for n, name in ((1, "Pink"), (2, "Dream On"), (3, "Crazy"))}


def test_an_album_that_already_matches_its_files_is_not_reported(lib, monkeypatch):  # noqa: F811
    _wire(lib, monkeypatch, SAME)
    result, findings = _scan(lib)
    assert findings == []
    assert result.scanned == 1 and result.skipped == 1 and result.errors == 0


def test_a_differing_album_is_one_finding_that_says_what_would_change(lib, monkeypatch):  # noqa: F811
    tags = {name: {**value, "year": "2005"} for name, value in SAME.items()}
    tags["Pink"] = {**tags["Pink"], "title": "Pink (Remastered)"}
    _wire(lib, monkeypatch, tags)
    result, findings = _scan(lib)
    assert result.findings_created == 1
    finding = findings[0]
    assert finding["finding_type"] == "file_tags_differ"
    assert finding["entity_id"] == f"lib2:{lib['album']}"
    assert finding["title"] == "File tags differ: The Very Best of Aerosmith by Aerosmith"
    assert 'Year becomes "2005"' in finding["description"]
    assert '"Pink": title becomes "Pink (Remastered)"' in finding["details"]["changes"]


def test_approving_it_makes_the_library_say_what_the_files_say(lib, monkeypatch):  # noqa: F811
    tags = {name: {**value, "year": "2005"} for name, value in SAME.items()}
    tags["Pink"] = {**tags["Pink"], "title": "Pink (Remastered)"}
    _wire(lib, monkeypatch, tags)
    _, findings = _scan(lib)
    out = _worker(lib)._fix_file_tags_differ(
        "album", findings[0]["entity_id"], None, findings[0]["details"])
    assert out["success"], out
    assert out["action"] == "used_file_tags"
    # the second look finds nothing left to say, and so does the second approve
    assert _scan(lib)[1] == []
    again = _worker(lib)._fix_file_tags_differ(
        "album", findings[0]["entity_id"], None, findings[0]["details"])
    assert again["action"] == "already_matching"


def test_a_legacy_finding_is_refused(lib):  # noqa: F811
    assert not _worker(lib)._fix_file_tags_differ("album", "41", None, {})["success"]


def test_describe_plan_leads_with_the_album_then_moves_then_tracks():
    plan = {
        "albums": [{"rename": "Nine Lives",
                    "changes": [{"field": "genres", "label": "Genre", "file": ["Rock", "Hard Rock"]}]}],
        "tracks": [
            {"title": "Pink", "move": {"album_title": "Nine Lives"}, "changes": []},
            {"title": "Crazy", "move": None,
             "changes": [{"field": "track_number", "label": "Track #", "file": 3}]}],
    }
    assert describe_plan(plan) == [
        'album title becomes "Nine Lives"', 'genre becomes "Rock, Hard Rock"',
        '1 track moves to "Nine Lives"', '"Crazy": track # becomes "3"']
    assert describe_plan({"albums": [], "tracks": []}) == []
