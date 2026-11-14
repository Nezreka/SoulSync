"""#2073: the Artist Merge Review job is where a merge is seen and put right:
undo one SoulSync made, decide a doubtful pair, split one merged by mistake."""

import json

from core.library2 import artist_merges
from core.library2.dedup_repair import repair_duplicate_artists
from core.repair_jobs.artist_merge_review import ArtistMergeReviewJob
from core.repair_jobs.base import JobContext
from core.repair_worker import RepairWorker
from tests.library2.test_artist_catalog_merge_2073 import _alive
from tests.library2.test_dedup_repair import _album, _artist, repair_db  # noqa: F401


def _files(conn, artist_id, title, folder, year=2010):
    album = _album(conn, artist_id, title, with_track=True, release_date=f"{year}-01-01")
    track = conn.execute("SELECT id FROM lib2_tracks WHERE album_id=?", (album,)).fetchone()[0]
    conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, ?)",
                 (track, f"/music/{folder}/{title}/01.flac"))
    return album


def _scan(legacy_db, monkeypatch, tags=None):
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda p, **_k: p)
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda path: {
        'album_artist': (tags or {}).get(path.split('/')[2])})
    findings = []
    result = ArtistMergeReviewJob().scan(JobContext(
        db=legacy_db, config_manager=None, transfer_folder='/music',
        create_finding=lambda **kw: findings.append(kw) or True))
    return result, findings


def _worker(legacy_db):
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = legacy_db, None, '/music'
    return worker


def _of(findings, kind):
    return [f for f in findings if f['finding_type'] == kind]


def test_a_merge_it_made_is_shown_and_approving_undoes_it(repair_db, monkeypatch):  # noqa: F811
    legacy_db, conn = repair_db
    real = _artist(conn, "Odetari", spotify_id="7ITMCzIU9uII8gwRg8JAhc")
    fragment = _artist(conn, "Odetari w", spotify_id="7ITMCzIU9uII8gwRg8JAhc")
    _files(conn, fragment, "I LOVE YOU HOE", "Odetari w")
    conn.commit()
    repair_duplicate_artists(legacy_db)
    assert _alive(conn, real, fragment) == 1

    _, findings = _scan(legacy_db, monkeypatch)
    made = _of(findings, 'artist_merge_made')
    assert len(made) == 1
    assert 'into' in made[0]['title'] and '1 album' in made[0]['description']

    out = _worker(legacy_db)._fix_artist_merge_made('artist', made[0]['entity_id'], None, made[0]['details'])
    assert out['success'], out
    assert _alive(conn, real, fragment) == 2
    # undone: not shown again, and not merged again
    assert _of(_scan(legacy_db, monkeypatch)[1], 'artist_merge_made') == []
    assert repair_duplicate_artists(legacy_db)['artists_merged'] == 0
    again = _worker(legacy_db)._fix_artist_merge_made('artist', made[0]['entity_id'], None, made[0]['details'])
    assert again['success'] is False and 'already undone' in again['error']


def test_a_doubtful_pair_is_asked_about_and_approving_merges_it(repair_db, monkeypatch):  # noqa: F811
    legacy_db, conn = repair_db
    welsh = _artist(conn, "Attack! Attack!", external_ids=json.dumps({"deezer": "150915"}))
    american = _artist(conn, "Attack Attack!", external_ids=json.dumps({"deezer": "150915"}))
    _files(conn, welsh, "The Latest Fashion", "Attack! Attack!")
    for title in ("Someday Came Suddenly", "This Means War"):
        _files(conn, american, title, "Attack Attack!")
    conn.commit()
    assert repair_duplicate_artists(legacy_db)['artists_merged'] == 0

    _, findings = _scan(legacy_db, monkeypatch)
    doubt = _of(findings, 'artist_merge_doubt')
    assert len(doubt) == 1
    assert 'same deezer id' in doubt[0]['description']
    assert 'This Means War' in doubt[0]['description']

    out = _worker(legacy_db)._fix_artist_merge_doubt('artist', doubt[0]['entity_id'], None, doubt[0]['details'])
    assert out['success'], out
    # the one with more albums keeps its page, and the merge is in the journal like any other
    assert _alive(conn, american) == 1 and _alive(conn, welsh) == 0
    assert artist_merges.list_merges(conn)[0]['reason'] == 'by hand'


def test_an_artist_holding_another_artists_albums_is_found_and_split(repair_db, monkeypatch):  # noqa: F811
    """the reporter's library: merged before any journal, files still in two folders"""
    legacy_db, conn = repair_db
    welsh = _artist(conn, "Attack! Attack!")
    own = _files(conn, welsh, "The Latest Fashion", "Attack! Attack!")
    wrong = [_files(conn, welsh, "Someday Came Suddenly", "Attack Attack!"),
             _files(conn, welsh, "This Means War", "Attack Attack!")]
    # a shelf folder under three artists, and a mistagged-nowhere folder: neither is a second artist
    for n in range(3):
        other = _artist(conn, f"Shelved {n}")
        _files(conn, other, f"Own {n}", f"Shelved {n}")
        _files(conn, other, f"Loose {n}", "Music")
    _files(conn, welsh, "Untagged Elsewhere", "Somewhere")
    conn.commit()

    tags = {"Attack Attack!": "Attack Attack!", "Attack! Attack!": "Attack! Attack!", "Music": "Music"}
    _, findings = _scan(legacy_db, monkeypatch, tags)
    mistakes = _of(findings, 'artist_merged_by_mistake')
    assert len(mistakes) == 1
    details = mistakes[0]['details']
    assert (details['artist_id'], details['other_name']) == (welsh, "Attack Attack!")
    assert details['album_ids'] == sorted(wrong)

    out = _worker(legacy_db)._fix_artist_merged_by_mistake(
        'artist', mistakes[0]['entity_id'], None, details)
    assert out['success'], out
    owners = dict(conn.execute(
        "SELECT al.id, ar.name FROM lib2_albums al JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
        "WHERE al.id IN (?,?,?)", (own, *wrong)).fetchall())
    assert owners == {own: "Attack! Attack!", wrong[0]: "Attack Attack!", wrong[1]: "Attack Attack!"}
    # put right: nothing left to report, and the repair does not fold them back
    assert _of(_scan(legacy_db, monkeypatch, tags)[1], 'artist_merged_by_mistake') == []
    assert repair_duplicate_artists(legacy_db)['artists_merged'] == 0


def test_a_folder_the_tags_do_not_back_up_is_not_a_second_artist(repair_db, monkeypatch):  # noqa: F811
    legacy_db, conn = repair_db
    artist = _artist(conn, "Oliver Tree")
    _files(conn, artist, "Ugly Is Beautiful", "Oliver Tree")
    _files(conn, artist, "Cowboy Tears", "Oliver")
    conn.commit()
    # the files in "Oliver" say Oliver Tree: a folder named short, not another artist
    _, findings = _scan(legacy_db, monkeypatch, {"Oliver": "Oliver Tree", "Oliver Tree": "Oliver Tree"})
    assert _of(findings, 'artist_merged_by_mistake') == []


def test_another_spelling_or_a_collaboration_is_not_a_second_artist():
    from core.library2.artist_merges import _same_act
    # one act, written another way
    assert _same_act("Jason Der\u00fclo", "Jason Derulo")
    assert _same_act("Jackson 5", "The Jackson 5")
    assert _same_act("Gorillaz", "Gorillaz feat. D.R.A.M.")
    assert _same_act("Le Castle Vania", "Le Castle Vania, Addison")
    assert _same_act("Oliver Tree", "Oliver")
    # two acts: punctuation is all that tells these apart, and it is enough to ask
    assert not _same_act("Attack! Attack!", "Attack Attack!")
    assert not _same_act("Killigrew", "Krisu")
    assert not _same_act("Various Artists", "Coldplay")


# ── Storm, Nov 13 2026: one artist written two ways is not two artists ───────

STORM_FALSE_POSITIVES = [
    ("Luce Twinkle Wink", "Luce Twinkle Wink☆", ()),
    ("Art Blakey and the Jazz Messengers", "Art Blakey & The Jazz Messengers", ()),
    ('SOIL & "PIMP" SESSIONS', "Soil & 'Pimp' Sessions", ()),
    ("George Gershwin", "George Gershwin-", ()),
    ("Hikaru Utada", "宇多田ヒカル", ("宇多田ヒカル", "Utada")),
    ("Iori Nomizu", "野水いおり", ("野水いおり",)),
    ("Nothing's Carved In Stone", "Nothing’s Carved In Stone", ()),
    ("Vincenzo Bellini", "Vicenzo Bellini", ()),
    ("HYPNOSISMIC -D.R.B- (Division All Stars)", "HYPNOSISMIC -D.R.B.-(Division All Stars)", ()),
    ("BLU‐SWING", "Blu-Swing", ()),
]


def test_a_name_typed_another_way_misspelled_or_a_known_alias_is_the_same_artist():
    from core.library2.artist_merges import _same_act
    for artist, other, aliases in STORM_FALSE_POSITIVES:
        assert _same_act(artist, other, aliases), (artist, other)
        assert _same_act(other, artist, (artist, *aliases)), (other, artist)


def test_what_is_still_asked_about():
    from core.library2.artist_merges import _same_act
    # the two he called right: another act is named in front
    assert not _same_act("Royal Philharmonic Orchestra", "Edward Elgar; Royal Philharmonic Orchestra")
    assert not _same_act("Tsutchie", "Force of Nature / Tsutchie")
    # the case this job was built for, with the names musicbrainz lists for each band
    assert not _same_act("Attack! Attack!", "Attack Attack!")
    assert not _same_act("Attack Attack!", "Attack! Attack!",
                         ("Attack Attack!", "Attack Attack! (US)", "Attack Attack"))
    # another script with nothing on the artist to say it is the same person
    assert not _same_act("Hikaru Utada", "宇多田ヒカル")
    # one letter apart in a short name is another name
    assert not _same_act("Low", "Lowe")
    assert not _same_act("Kygo", "Kyga")
    assert not _same_act("Killigrew", "Krisu")


def test_storms_list_files_nothing_and_closes_what_was_filed(repair_db, monkeypatch):  # noqa: F811
    """the detection end to end, one artist per pair: an own-named folder and a
    folder under the other spelling whose files carry that spelling."""
    legacy_db, conn = repair_db
    tags, ids = {}, {}
    for artist_name, other, aliases in STORM_FALSE_POSITIVES:
        artist = _artist(conn, artist_name)
        conn.execute("UPDATE lib2_artists SET aliases=? WHERE id=?", (json.dumps(list(aliases)), artist))
        _files(conn, artist, f"{artist_name} one", artist_name)
        _files(conn, artist, f"{artist_name} two", other)
        tags[artist_name], tags[other] = artist_name, other
        ids[artist] = other
    # and the real thing beside them
    welsh = _artist(conn, "Attack! Attack!")
    _files(conn, welsh, "The Latest Fashion", "Attack! Attack!")
    _files(conn, welsh, "Someday Came Suddenly", "Attack Attack!")
    tags.update({"Attack! Attack!": "Attack! Attack!", "Attack Attack!": "Attack Attack!"})
    conn.execute(
        "CREATE TABLE IF NOT EXISTS repair_findings (id INTEGER PRIMARY KEY, job_id TEXT, "
        "finding_type TEXT, severity TEXT, entity_type TEXT, entity_id TEXT, file_path TEXT, "
        "title TEXT, description TEXT, details_json TEXT, status TEXT DEFAULT 'pending', "
        "user_action TEXT, resolved_at TIMESTAMP, updated_at TIMESTAMP)")
    # findings an earlier build filed for every one of them
    for artist, other in {**ids, welsh: "Attack Attack!"}.items():
        conn.execute(
            "INSERT INTO repair_findings(job_id, finding_type, severity, entity_type, entity_id, "
            "title, description, details_json) VALUES('artist_merge_review', "
            "'artist_merged_by_mistake', 'info', 'artist', ?, 't', 'd', ?)",
            (f"lib2:{artist}", json.dumps({"other_name": other})))
    conn.commit()

    _, findings = _scan(legacy_db, monkeypatch, tags)

    mistakes = _of(findings, 'artist_merged_by_mistake')
    assert [(m['details']['artist_name'], m['details']['other_name']) for m in mistakes] == [
        ("Attack! Attack!", "Attack Attack!")]
    pending = [r[0] for r in conn.execute(
        "SELECT entity_id FROM repair_findings WHERE job_id='artist_merge_review' "
        "AND finding_type='artist_merged_by_mistake' AND status='pending'")]
    assert pending == [f"lib2:{welsh}"]
    closed = conn.execute(
        "SELECT COUNT(*) FROM repair_findings WHERE job_id='artist_merge_review' "
        "AND status='resolved' AND user_action='obsolete'").fetchone()[0]
    assert closed == len(STORM_FALSE_POSITIVES)
