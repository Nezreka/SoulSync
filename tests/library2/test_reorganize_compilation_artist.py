"""$artist in a reorganize is the track's performer, $albumartist the album's.

pertti: a compilation filed as
"Compilations/<album>/10 - Super Frika- Love Satisfaction.mp3" was flagged to
move to "10 - Various Artists - Love Satisfaction.mp3". the catalogue credits
a compilation track to its album artist first ("Various Artists", then the
performer) and the planner handed that first credit to the path builder as
the track's artist. a download of the same track already named Super Frika.
"""

from __future__ import annotations

import os

import pytest

from core.library2 import reorganize_plan

ALBUM = "Saturday Night - South African Disco Pop Hits - 1981 to 1987"
COMP_TEMPLATES = {
    "album_path": "$albumartist/$album/$track - $artist - $title",
    "compilation_path": "Compilations/$album/$track - $artist - $title",
}


class _Cfg:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_active_media_server(self):
        return "primary"


@pytest.fixture()
def builder(monkeypatch, tmp_path):
    from core.imports import paths

    cfg = _Cfg({
        "file_organization.templates": COMP_TEMPLATES,
        "file_organization.enabled": True,
        "file_organization.collab_artist_mode": "first",
        "soulseek.transfer_path": str(tmp_path),
        "features.library_v2": True,
    })
    monkeypatch.setattr(paths, "_get_config_manager", lambda: cfg)
    monkeypatch.setattr(paths, "_get_album_tracks_for_source", lambda *a: None)
    return paths.build_final_path_for_track, cfg


def _artist(conn, name):
    row = conn.execute("SELECT id FROM lib2_artists WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO lib2_artists(name) VALUES(?)", (name,)).lastrowid


def _seed(conn, *, album_artist="Various Artists", album=ALBUM, album_type="compilation",
          title="Love Satisfaction", number=10, credits=("Various Artists", "Super Frika"),
          own="Super Frika", path=None):
    album_artist_id = _artist(conn, album_artist)
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, year, album_type) VALUES(?,?,?,?)",
        (album_artist_id, album, 1987, album_type)).lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                 (album_id, album_artist_id))
    track_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, track_artist)"
        " VALUES(?,?,?,1,?)", (album_id, title, number, own)).lastrowid
    for position, name in enumerate(credits):
        conn.execute(
            "INSERT INTO lib2_track_artists(track_id, artist_id, role, position)"
            " VALUES(?,?,?,?)",
            (track_id, _artist(conn, name), "primary" if position == 0 else "featured",
             position))
    conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                 (track_id, path or f"/m/old/{number:02d} - {title}.mp3"))
    conn.commit()
    return album_id


def _planned(conn, album_id, build, tmp_path):
    plan = reorganize_plan.plan_album_reorganize(
        conn, album_id, build_final_path_fn=build, transfer_dir=str(tmp_path),
        resolve_file_path_fn=lambda p: p)
    track = plan["tracks"][0]
    assert track["reason"] is None
    return track["new_path"]


def test_pertti_compilation_names_the_performer(imported_conn, builder, tmp_path):
    album_id = _seed(imported_conn)
    assert _planned(imported_conn, album_id, builder[0], tmp_path) == (
        f"Compilations/{ALBUM}/10 - Super Frika - Love Satisfaction.mp3")


def test_a_compilation_track_with_no_own_artist_names_its_credited_performer(
        imported_conn, builder, tmp_path):
    # a media server scan credits the album artist first and links the
    # performer, without a track_artist to compare against
    album_id = _seed(imported_conn, own=None)
    assert _planned(imported_conn, album_id, builder[0], tmp_path) == (
        f"Compilations/{ALBUM}/10 - Super Frika - Love Satisfaction.mp3")


def test_a_compilation_credited_to_various_artists_alone_reads_the_track_artist(
        imported_conn, builder, tmp_path):
    album_id = _seed(imported_conn, credits=("Various Artists",))
    assert _planned(imported_conn, album_id, builder[0], tmp_path) == (
        f"Compilations/{ALBUM}/10 - Super Frika - Love Satisfaction.mp3")


def test_a_featured_compilation_track_names_its_lead_like_a_download(
        imported_conn, builder, tmp_path):
    # $artist is the first credited performer, as a download fills it from
    # the source's artist list; collab mode only shapes $albumartist
    album_id = _seed(imported_conn, credits=("Various Artists", "Anna", "Ben"),
                     own="Anna feat. Ben")
    assert _planned(imported_conn, album_id, builder[0], tmp_path) == (
        f"Compilations/{ALBUM}/10 - Anna - Love Satisfaction.mp3")


def test_a_single_artist_album_is_unchanged(imported_conn, builder, tmp_path):
    album_id = _seed(imported_conn, album_artist="Drake", album="Views", album_type="album",
                     title="One Dance", number=1, credits=("Drake",), own="Drake")
    assert _planned(imported_conn, album_id, builder[0], tmp_path) == (
        "Drake/Views/01 - Drake - One Dance.mp3")


def test_an_album_track_with_a_guest_keeps_the_album_artist(imported_conn, builder, tmp_path):
    album_id = _seed(imported_conn, album_artist="Drake", album="Views", album_type="album",
                     title="Too Good", number=2, credits=("Drake", "Rihanna"),
                     own="Drake feat. Rihanna")
    assert _planned(imported_conn, album_id, builder[0], tmp_path) == (
        "Drake/Views/02 - Drake - Too Good.mp3")


def test_an_album_track_by_someone_else_names_them(imported_conn, builder, tmp_path):
    # a dj mixtape's track by another act: the ARTIST tag (retag) and a
    # download both name the act, so the filename does too
    album_id = _seed(imported_conn, album_artist="Vlad Jet", album="Capital Heaven",
                     album_type="album", title="Blue Skies", number=3,
                     credits=("Vlad Jet", "Framewerk"), own="Framewerk")
    assert _planned(imported_conn, album_id, builder[0], tmp_path) == (
        "Vlad Jet/Capital Heaven/03 - Framewerk - Blue Skies.mp3")


def test_the_download_path_names_the_performer(builder):
    build = builder[0]
    title = "Love Satisfaction"
    context = {
        "artist": {"name": "Various Artists"},
        "album": {"name": ALBUM, "id": "a1", "release_date": "1987-01-01",
                  "total_tracks": 16, "album_type": "compilation",
                  "artists": [{"name": "Various Artists"}]},
        "track_info": {"name": title, "id": "t1", "track_number": 10, "disc_number": 1,
                       "artists": [{"name": "Super Frika"}]},
        "original_search_result": {"title": title, "clean_title": title,
                                   "clean_album": ALBUM, "clean_artist": "Super Frika",
                                   "artists": [{"name": "Super Frika"}]},
        "source": "spotify", "is_album_download": True,
    }
    path, _ = build(context, {"name": "Various Artists"},
                    {"is_album": True, "album_name": ALBUM, "track_number": 10,
                     "disc_number": 1}, ".mp3", create_dirs=False)
    rel = os.path.relpath(path, builder[1].values["soulseek.transfer_path"])
    assert rel.replace(os.sep, "/") == (
        f"Compilations/{ALBUM}/10 - Super Frika - Love Satisfaction.mp3")


def test_the_path_mismatch_finding_points_at_the_performer(
        imported_conn, legacy_db, builder, tmp_path, monkeypatch):
    from core.library2 import reorganize_bridge as bridge
    from core.repair_jobs.base import JobContext
    from core.repair_jobs.library_reorganize import LibraryReorganizeJob
    from tests.lib2_seed import row_conn

    build, cfg = builder
    old = tmp_path / "Compilations" / "Saturday Night-South African Disco Pop Hits 1981-1987"
    old.mkdir(parents=True)
    src = old / "10 - Super Frika- Love Satisfaction.mp3"
    src.write_bytes(b"audio")
    album_id = _seed(imported_conn, path=str(src))

    class _DB:
        database_path = legacy_db.path

        def _get_connection(self):
            return row_conn(legacy_db.path)

    def _preview(db, config_manager, lib2_album_id, transfer_dir=None):
        return bridge.catalogue_preview_fn(
            album_id=lib2_album_id, db=db, transfer_dir=str(tmp_path),
            resolve_file_path_fn=lambda p: p if p and os.path.isfile(p) else None,
            build_final_path_fn=build)

    monkeypatch.setattr(bridge, "preview_album_reorganize", _preview)
    findings = []
    ctx = JobContext(db=_DB(), transfer_folder=str(tmp_path), config_manager=cfg,
                     create_finding=lambda **kw: findings.append(kw) or True,
                     should_stop=lambda: False, is_paused=lambda: False)
    LibraryReorganizeJob().scan(ctx)

    mine = [f for f in findings if f["details"]["lib2_album_id"] == album_id]
    assert len(mine) == 1
    assert mine[0]["details"]["to"].replace(os.sep, "/") == (
        f"Compilations/{ALBUM}/10 - Super Frika - Love Satisfaction.mp3")
