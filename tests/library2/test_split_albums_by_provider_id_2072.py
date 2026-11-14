"""#2072, Storm's feedback on dev 4b9a3eb: "Perhaps it would help to not
consider only musicbrainz, some albums that are split have the same Deezer or
Discogs ID, so this could be provider agnostic."

Album Tag Consistency joined a release split across album artists only on a
shared folder or on one MusicBrainz release id in the files' tags. His FINAL
FANTASY XIII-2 soundtrack is three albums under three album artists, in three
places, each tagged with a different MusicBrainz release and two different
years, and two of them hold the same Deezer album id."""

import json
from types import SimpleNamespace

import pytest

from core.repair_jobs.album_tag_consistency import (
    AlbumTagConsistencyJob, album_release_ids, albums_sharing_a_release_id)
from core.repair_jobs.base import JobContext
from tests.repair_jobs.test_album_tag_consistency_reporting import _make_flac

RG = "5c5ab9d5-0000-4000-8000-000000000001"


@pytest.fixture
def env(imported_conn, legacy_db, tmp_path):
    return SimpleNamespace(conn=imported_conn, db=legacy_db, tmp=tmp_path,
                           cfg=SimpleNamespace(get=lambda _k, default=None: default))


def _album(env, where, artist, title, tracks, *, year="2013", ids=None, release_group=None,
           upc=None, mbid_tag=None, album_artist_tag=True, start=1):
    conn = env.conn
    artist_id = conn.execute("INSERT INTO lib2_artists(name) VALUES(?)", (artist,)).lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, year, external_ids, "
        "musicbrainz_release_group_id, upc) VALUES(?,?,?,?,?,?)",
        (artist_id, title, int(year), json.dumps(ids or {}), release_group, upc)).lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,'primary')",
                 (album, artist_id))
    folder = env.tmp / where
    folder.mkdir(parents=True, exist_ok=True)
    for number, name in enumerate(tracks, start):
        track = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?,?,?)",
                             (album, name, number)).lastrowid
        path = folder / f"{number:02d} - {name}.flac"
        tags = {"album": title, "title": name, "artist": artist, "date": year}
        if album_artist_tag:
            tags["albumartist"] = artist
        if mbid_tag:
            tags["musicbrainz_albumid"] = mbid_tag
        _make_flac(path, tags)
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)", (track, str(path)))
    conn.commit()
    return album


def _scan(env):
    findings, lines = [], []
    AlbumTagConsistencyJob().scan(JobContext(
        db=env.db, config_manager=env.cfg, transfer_folder=str(env.tmp),
        create_finding=lambda **kw: findings.append(kw) or True,
        report_progress=lambda **kw: lines.append(kw)))
    return [f for f in findings if f["details"].get("folder_split")], lines


def test_storms_final_fantasy_soundtrack_is_one_release(env):
    first = _album(env, "a/Mitsuto Suzuki/FF", "鈴木光人",
                   "FINAL FANTASY XIII-2 Original Soundtrack", ["Parallel Worlds - Aggressive Mix"],
                   year="2013", release_group=RG, mbid_tag="30532fd0-6f4e-4d02-a334-d2c8f2383cf0")
    second = _album(env, "b/Naoshi Mizuta/FF", "Naoshi Mizuta",
                    "FINAL FANTASY XIII-2 Original Soundtrack", ["Full Speed Ahead", "Paradigm Shift"],
                    year="2013", ids={"deezer": "417821727"}, release_group=RG, start=2,
                    mbid_tag="48435038-2a6f-40ad-b514-bc4a1636b061")
    third = _album(env, "c/Various Artists/FF", "Various Artists",
                   "Final Fantasy XIII-2 Original Soundtrack", ["パラダイムシフト"],
                   year="2011", ids={"deezer": "417821727"}, start=4,
                   mbid_tag="40ddee7e-6637-4fae-8cb9-b211a3ac3601")

    findings, _ = _scan(env)

    assert len(findings) == 1
    details = findings[0]["details"]
    assert sorted(details["album_ids"]) == sorted([first, second, third])
    assert details["split_evidence"] == "catalogue"
    assert details["shared_release_id"] == {"source": "deezer", "id": "417821727"}
    assert "same Deezer id (417821727)" in findings[0]["description"]
    assert details["inconsistencies"][0]["field"] == "albumartist"
    assert details["inconsistencies"][0]["canonical"] == "Various Artists"
    assert details["track_count"] == 4


def test_a_classical_compilation_split_in_two_with_no_musicbrainz_tags(env):
    """his Autumn Moods: two albums, nothing from MusicBrainz, one Deezer id"""
    whole = _album(env, "x/Various Artists/Autumn Moods", "Various Artists", "Autumn Moods",
                   ["One", "Two", "Three"], ids={"deezer": "8690673"}, year="2014")
    stray = _album(env, "y/Vivaldi/Autumn Moods", "Antonio Vivaldi", "Autumn Moods",
                   ["Autumn"], ids={"deezer": "8690673", "tidal": "1"}, year="2014", start=4)
    findings, _ = _scan(env)
    assert len(findings) == 1
    assert sorted(findings[0]["details"]["album_ids"]) == sorted([whole, stray])
    assert findings[0]["details"]["inconsistencies"][0]["canonical"] == "Various Artists"


@pytest.mark.parametrize("source", ["deezer", "discogs", "spotify", "itunes", "musicbrainz"])
def test_any_source_whose_ids_hold_up_will_do(env, source):
    _album(env, "x/VA/Mix", "Various Artists", "The Mix", ["One", "Two", "Three"], ids={source: "777"})
    _album(env, "y/Guest/Mix", "Guest", "The Mix", ["Four"], ids={source: "777"}, start=4)
    findings, _ = _scan(env)
    assert len(findings) == 1
    assert findings[0]["details"]["shared_release_id"] == {"source": source, "id": "777"}


@pytest.mark.parametrize("source", ["tidal", "amazon", "qobuz", "bandcamp", "audiodb"])
def test_a_source_that_stamps_one_id_on_every_album_of_a_name_is_not_believed(env, source):
    """Boulder's library, Nov 13 2026: one tidal album id on sixteen unrelated
    artists' albums of the same name, 3,250 ids shared like that."""
    _album(env, "x/Evanescence/Fallen", "Evanescence", "Fallen", ["Going Under"], ids={source: "777"})
    _album(env, "y/Infraction/Fallen", "Infraction Music", "Fallen", ["Fallen"], ids={source: "777"},
           start=2)
    assert _scan(env)[0] == []


def test_a_barcode_is_not_believed_either(env):
    _album(env, "x/Marshmello/Forever", "Marshmello", "Forever", ["Forever"], upc="0000000000017")
    _album(env, "y/Overcrest/Forever", "Overcrest", "Forever", ["Forever"], upc="0000000000017",
           start=2)
    assert _scan(env)[0] == []


def test_two_albums_that_both_have_a_track_one_are_two_albums(env):
    """a believed id can still be a bad match: 2Pac's Me Against The World and
    another artist's four songs of that name had one spotify id. One record
    has one track 1."""
    _album(env, "x/2Pac/MATW", "2Pac", "Me Against The World", ["Intro", "If I Die 2Nite"],
           ids={"deezer": "9"})
    _album(env, "y/1111/MATW", "1111", "Me Against The World", ["Other"], ids={"deezer": "9"})
    assert _scan(env)[0] == []


def test_pieces_with_no_track_numbers_are_left_alone(env):
    _album(env, "x/VA/Mix", "Various Artists", "The Mix", ["One", "Two"], ids={"deezer": "9"})
    _album(env, "y/Guest/Mix", "Guest", "The Mix", ["Three"], ids={"deezer": "9"}, start=0)
    assert _scan(env)[0] == []


def test_every_artist_has_a_greatest_hits_and_their_ids_differ(env):
    _album(env, "x/Abba/GH", "ABBA", "Greatest Hits", ["One", "Two"], ids={"deezer": "1"})
    _album(env, "y/Queen/GH", "Queen", "Greatest Hits", ["Three", "Four"], ids={"deezer": "2"})
    _album(env, "z/Blur/GH", "Blur", "Greatest Hits", ["Five"])
    assert _scan(env)[0] == []


def test_a_link_built_from_the_name_is_not_an_id(env):
    url = "https://www.last.fm/music/Various+Artists/Greatest+Hits"
    _album(env, "x/Abba/GH", "ABBA", "Greatest Hits", ["One", "Two"], ids={"lastfm": url})
    _album(env, "y/Queen/GH", "Queen", "Greatest Hits", ["Three"], ids={"lastfm": url})
    assert _scan(env)[0] == []


def test_two_release_groups_are_two_records_whatever_else_agrees(env):
    _album(env, "x/VA/Mix", "Various Artists", "The Mix", ["One", "Two"], ids={"deezer": "9"},
           release_group=RG)
    _album(env, "y/Guest/Mix", "Guest", "The Mix", ["Three"], ids={"deezer": "9"},
           release_group=RG.replace("0001", "0002"))
    assert _scan(env)[0] == []


def test_the_log_says_single_track_albums_are_compared_too(env):
    _album(env, "x/VA/Mix", "Various Artists", "The Mix", ["One", "Two"], ids={"deezer": "9"})
    _album(env, "y/Guest/Mix", "Guest", "The Mix", ["Three"], ids={"deezer": "9"}, start=3)
    _, lines = _scan(env)
    text = " ".join(str(line.get("log_line") or "") for line in lines)
    assert "single-track ones included" in text
    assert "single-track albums included" in text


def _row(album_id, **ids):
    row = {"id": album_id, "spotify_id": ids.pop("spotify_id", None),
           "musicbrainz_id": ids.pop("musicbrainz_id", None),
           "musicbrainz_release_group_id": ids.pop("release_group", None),
           "upc": ids.pop("upc", None), "external_ids": json.dumps(ids)}
    return row


def test_the_ids_that_name_a_release():
    assert album_release_ids(_row(1, deezer="5", lastfm="url", legacy_unknown="x", spotify_id="sp",
                                  musicbrainz_id="mb", release_group="rg", upc="0123")) == {
        "deezer": "5", "spotify": "sp", "musicbrainz": "mb", "release_group": "rg", "upc": "0123"}
    assert album_release_ids({"id": 1, "spotify_id": "", "musicbrainz_id": None,
                              "musicbrainz_release_group_id": None, "upc": None,
                              "external_ids": "not json"}) == {}


def test_albums_are_joined_through_any_one_shared_id():
    groups = albums_sharing_a_release_id([
        _row(1, release_group="rg"), _row(2, deezer="5", release_group="rg"), _row(3, deezer="5"),
        _row(4, deezer="6"), _row(5, upc="111"), _row(6, upc="111"), _row(7)])
    assert sorted((sorted(members), source, value) for members, source, value in groups) == [
        ([1, 2, 3], "deezer", "5")]


def test_unify_tags_puts_one_album_artist_on_every_file(env):
    from mutagen.flac import FLAC

    from core.repair_worker import RepairWorker

    _album(env, "a/Mitsuto Suzuki/FF", "鈴木光人", "FINAL FANTASY XIII-2 Original Soundtrack",
           ["Parallel Worlds"], year="2013", release_group=RG)
    _album(env, "b/Naoshi Mizuta/FF", "Naoshi Mizuta", "FINAL FANTASY XIII-2 Original Soundtrack",
           ["Full Speed Ahead", "Paradigm Shift"], year="2013", ids={"deezer": "417821727"},
           release_group=RG, start=2)
    _album(env, "c/Various Artists/FF", "Various Artists", "Final Fantasy XIII-2 Original Soundtrack",
           ["Paradigm"], year="2011", ids={"deezer": "417821727"}, start=4)
    findings, _ = _scan(env)
    assert len(findings) == 1

    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = env.db, env.cfg, str(env.tmp)
    out = worker._fix_album_tag_inconsistency(
        "album", findings[0]["entity_id"], None, findings[0]["details"])
    assert out["success"], out
    files = sorted(env.tmp.rglob("*.flac"))
    assert len(files) == 4
    assert all(FLAC(str(path))["albumartist"] == ["Various Artists"] for path in files)
    # each track keeps its own artist
    assert {FLAC(str(path))["artist"][0] for path in files} == {
        "鈴木光人", "Naoshi Mizuta", "Various Artists"}
