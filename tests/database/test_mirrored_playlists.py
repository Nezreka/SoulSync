import json

import pytest

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_library_track


def test_update_mirrored_playlist_source_ref_preserves_tracks(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = db.mirror_playlist(
        source="youtube",
        source_playlist_id="oldhash",
        name="Mirror",
        tracks=[
            {
                "track_name": "Song",
                "artist_name": "Artist",
                "source_track_id": "yt1",
                "extra_data": {"discovered": True},
            }
        ],
        profile_id=1,
        description="https://youtube.com/playlist?list=old",
    )

    assert playlist_id is not None

    updated = db.update_mirrored_playlist_source_ref(
        playlist_id,
        "newhash",
        "https://youtube.com/playlist?list=new",
    )

    assert updated is True
    playlist = db.get_mirrored_playlist(playlist_id)
    assert playlist["source_playlist_id"] == "newhash"
    assert playlist["description"] == "https://youtube.com/playlist?list=new"

    tracks = db.get_mirrored_playlist_tracks(playlist_id)
    assert len(tracks) == 1
    assert tracks[0]["track_name"] == "Song"
    assert tracks[0]["extra_data"] is not None


def test_mirror_playlist_refresh_preserves_existing_description(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = db.mirror_playlist(
        source="spotify_public",
        source_playlist_id="hash",
        name="Release Radar",
        tracks=[{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=1,
        description="https://open.spotify.com/playlist/abc",
    )

    refreshed_id = db.mirror_playlist(
        source="spotify_public",
        source_playlist_id="hash",
        name="Release Radar",
        tracks=[{"track_name": "New Song", "artist_name": "Artist"}],
        profile_id=1,
    )

    assert refreshed_id == playlist_id
    playlist = db.get_mirrored_playlist(playlist_id)
    assert playlist["description"] == "https://open.spotify.com/playlist/abc"


def test_file_import_tracks_get_a_stable_source_track_id(tmp_path):
    # #901: file-import tracks arrive with no source_track_id; mirror_playlist must
    # assign a deterministic one so a Find & Add manual match can key on it (and so
    # discovery extra_data survives a re-import).
    db = MusicDatabase(str(tmp_path / "music.db"))
    file_tracks = [
        {"track_name": "Slow Ride", "artist_name": "Foghat", "album_name": "Fool for the City"},
        {"track_name": "I Gotta Feeling", "artist_name": "The Black Eyed Peas"},
    ]
    pid = db.mirror_playlist(source="file", source_playlist_id="myfile", name="From File",
                             tracks=file_tracks, profile_id=1)
    rows = db.get_mirrored_playlist_tracks(pid)
    ids = [r["source_track_id"] for r in rows]
    assert all(i and i.startswith("file:") for i in ids)      # no empty ids
    assert len(set(ids)) == 2                                  # distinct per song

    # Re-import the SAME file → SAME ids (stable), so a recorded match still keys.
    db.mirror_playlist(source="file", source_playlist_id="myfile", name="From File",
                       tracks=list(file_tracks), profile_id=1)
    rows2 = db.get_mirrored_playlist_tracks(pid)
    assert [r["source_track_id"] for r in rows2] == ids


def test_native_ids_still_used_verbatim(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    pid = db.mirror_playlist(source="spotify", source_playlist_id="sp", name="Sp",
                             tracks=[{"track_name": "S", "artist_name": "A", "source_track_id": "spotify123"}],
                             profile_id=1)
    rows = db.get_mirrored_playlist_tracks(pid)
    assert rows[0]["source_track_id"] == "spotify123"         # native id untouched


def test_backfill_fills_existing_empty_ids_idempotently(tmp_path):
    # #901 backfill: a file-import playlist mirrored BEFORE the fix has empty-id rows.
    # The backfill assigns the SAME stable ids a fresh import would, so existing
    # Find & Add matches start working without a re-import.
    db = MusicDatabase(str(tmp_path / "music.db"))
    pid = db.mirror_playlist(source="file", source_playlist_id="old", name="Old",
                             tracks=[{"track_name": "Slow Ride", "artist_name": "Foghat"}], profile_id=1)
    # simulate a pre-fix row: blank out the id
    with db._get_connection() as conn:
        conn.execute("UPDATE mirrored_playlist_tracks SET source_track_id = '' WHERE playlist_id = ?", (pid,))
        conn.commit()

    n = db._backfill_mirrored_track_source_ids()
    assert n == 1
    rows = db.get_mirrored_playlist_tracks(pid)
    from core.playlists.source_refs import stable_source_track_id
    assert rows[0]["source_track_id"] == stable_source_track_id(
        {"track_name": "Slow Ride", "artist_name": "Foghat"})   # same id a fresh import gives

    # idempotent — second run touches nothing
    assert db._backfill_mirrored_track_source_ids() == 0


def test_backfill_leaves_native_ids_untouched(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    pid = db.mirror_playlist(source="spotify", source_playlist_id="sp", name="Sp",
                             tracks=[{"track_name": "S", "artist_name": "A", "source_track_id": "spotify123"}],
                             profile_id=1)
    db._backfill_mirrored_track_source_ids()
    rows = db.get_mirrored_playlist_tracks(pid)
    assert rows[0]["source_track_id"] == "spotify123"


# ── #990: accept the Spotify shape + reject all-empty (silent 21k-empty-rows bug) ──
def test_mirror_accepts_spotify_shaped_tracks(tmp_path):
    """The GET playlist endpoints return Spotify-shaped tracks; feeding them straight
    back must map cleanly instead of storing empty rows."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    spotify_tracks = [{
        "name": "Because of You", "artists": [{"name": "Ne-Yo"}],
        "album": {"name": "Because of You"}, "id": "sp_track_1", "duration_ms": 217000,
    }]
    pid = db.mirror_playlist(source="spotify", source_playlist_id="liked", name="Liked",
                             tracks=spotify_tracks, profile_id=1)
    rows = db.get_mirrored_playlist_tracks(pid)
    assert rows[0]["track_name"] == "Because of You"
    assert rows[0]["artist_name"] == "Ne-Yo"
    assert rows[0]["album_name"] == "Because of You"
    assert rows[0]["source_track_id"] == "sp_track_1"
    assert rows[0]["duration_ms"] == 217000


def test_mirror_rejects_all_empty_payload_and_preserves_existing(tmp_path):
    """A wrong-shaped payload where every track maps to empty must be rejected —
    and must NOT wipe the existing mirror (the reported 21k-row disaster)."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    pid = db.mirror_playlist(source="spotify", source_playlist_id="liked", name="Liked",
                             tracks=[{"track_name": "Real Song", "artist_name": "A", "source_track_id": "x1"}],
                             profile_id=1)
    with pytest.raises(ValueError):
        db.mirror_playlist(source="spotify", source_playlist_id="liked", name="Liked",
                           tracks=[{"duration_ms": 1000}, {"duration_ms": 2000}], profile_id=1)
    rows = db.get_mirrored_playlist_tracks(pid)          # existing mirror untouched
    assert len(rows) == 1 and rows[0]["track_name"] == "Real Song"


def test_coalesce_mirror_track_shapes():
    from core.playlists.source_refs import coalesce_mirror_track
    sp = coalesce_mirror_track({"name": "T", "artists": [{"name": "A"}],
                                "album": {"name": "Al"}, "id": 7, "duration_ms": 5})
    assert (sp["track_name"], sp["artist_name"], sp["album_name"], sp["source_track_id"]) == ("T", "A", "Al", "7")
    assert sp["duration_ms"] == 5                          # non-mapped keys preserved
    m = {"track_name": "X", "artist_name": "Y", "album_name": "Z", "source_track_id": "id1"}
    assert coalesce_mirror_track(m) == m                   # mirror shape untouched
    s = coalesce_mirror_track({"name": "N", "artist": "Solo", "album": "AlbumStr"})
    assert s["artist_name"] == "Solo" and s["album_name"] == "AlbumStr"


def test_mirrored_playlist_quality_profile_is_durable_and_refresh_safe(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    assigned = db.create_quality_profile("Playlist Assigned", {"ranked_targets": []})
    assert assigned is not None
    pid = db.mirror_playlist(
        source="itunes_link",
        source_playlist_id="apple-1",
        name="Apple Mix",
        tracks=[{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=1,
        quality_profile_id=assigned,
    )

    # A provider refresh that does not know about UI preferences must preserve it.
    assert db.mirror_playlist(
        source="itunes_link",
        source_playlist_id="apple-1",
        name="Apple Mix Updated",
        tracks=[{"track_name": "Song 2", "artist_name": "Artist"}],
        profile_id=1,
    ) == pid
    assert db.get_mirrored_playlist(pid)["quality_profile_id"] == assigned


def test_provider_agnostic_assignment_resolver_handles_non_spotify_source(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    pid = db.mirror_playlist(
        source="deezer",
        source_playlist_id="12345",
        name="Deezer Mix",
        tracks=[{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=1,
    )

    assert db.resolve_mirrored_playlist_assignment("12345", "Deezer Mix", 1)["id"] == pid
    assert db.resolve_mirrored_playlist_assignment(f"auto_mirror_{pid}", None, 1)["id"] == pid


# ── Deriving a playlist cover from what discovery matched ──────────────────
#
# Boulder, looking at the new library grid: "images dont appear in most cards.
# none for last.fm, none for listenbrainz, none for soulsync discovery, none
# for spotify public, none for tidal, none for youtube."
#
# They never could. Most sources hand us no poster when a playlist is mirrored,
# and there is no per-track art either. What there IS, once discovery runs, is a
# matched track carrying real album art — so the playlist borrows its cover from
# the first track discovery finds, and has one from the next visit onwards.

from database.music_database import mirrored_cover_from_match


class TestMirroredCoverFromMatch:
    def test_reads_the_top_level_image(self):
        assert mirrored_cover_from_match(
            {"discovered": True, "matched_data": {"image_url": "http://a/cover.jpg"}}
        ) == "http://a/cover.jpg"

    def test_reads_the_nested_album_image(self):
        # _build_fix_modal_spotify_data carries the image in BOTH places for
        # parity with Spotify's own shape; older rows only have this one.
        assert mirrored_cover_from_match(
            {
                "discovered": True,
                "matched_data": {"album": {"images": [{"url": "http://b/cover.jpg"}]}},
            }
        ) == "http://b/cover.jpg"

    def test_prefers_the_top_level_image_when_both_exist(self):
        assert mirrored_cover_from_match(
            {
                "discovered": True,
                "matched_data": {
                    "image_url": "http://top/cover.jpg",
                    "album": {"images": [{"url": "http://nested/cover.jpg"}]},
                },
            }
        ) == "http://top/cover.jpg"

    def test_falls_back_to_spotify_data_for_older_rows(self):
        assert mirrored_cover_from_match(
            {"discovered": True, "spotify_data": {"image_url": "http://c/cover.jpg"}}
        ) == "http://c/cover.jpg"

    def test_an_undiscovered_track_yields_nothing(self):
        # The whole rule: art appears only once discovery has found something.
        assert mirrored_cover_from_match(
            {"discovered": False, "matched_data": {"image_url": "http://a/cover.jpg"}}
        ) == ""

    def test_survives_every_malformed_shape(self):
        for junk in [
            None,
            "nope",
            {},
            {"discovered": True},
            {"discovered": True, "matched_data": "nope"},
            {"discovered": True, "matched_data": {}},
            {"discovered": True, "matched_data": {"album": "Just A Name"}},
            {"discovered": True, "matched_data": {"album": {"images": []}}},
            {"discovered": True, "matched_data": {"album": {"images": [{}]}}},
            {"discovered": True, "matched_data": {"image_url": ""}},
        ]:
            assert mirrored_cover_from_match(junk) == ""


class TestPlaylistCoverBackfill:
    def _mirror(self, db, **over):
        return db.mirror_playlist(
            source=over.get("source", "youtube"),
            source_playlist_id="h1",
            name="Mirror",
            tracks=[
                {"track_name": "One", "artist_name": "A", "source_track_id": "t1"},
                {"track_name": "Two", "artist_name": "B", "source_track_id": "t2"},
            ],
            profile_id=1,
            image_url=over.get("image_url"),
        )

    def test_a_discovered_track_invalidates_the_cached_collage(self, tmp_path):
        # A new match can change what the four tiles should be, so the write
        # drops them and the next library read rebuilds them. Recomputing four
        # distinct covers on every track write would be far more expensive.
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db)
        db.backfill_missing_mirrored_covers(1)
        assert db.get_mirrored_playlist(pid)["cover_tiles"] is not None

        track = db.get_mirrored_playlist_tracks(pid)[0]
        db.update_mirrored_track_extra_data(
            track["id"],
            {"discovered": True, "matched_data": {"image_url": "http://a/cover.jpg"}},
        )
        assert db.get_mirrored_playlist(pid)["cover_tiles"] is None

    def test_it_never_touches_a_poster_the_source_supplied(self, tmp_path):
        # Deezer and the Spotify account tab DO send a real poster. image_url
        # means exactly that, and a mosaic of album art is not that.
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db, image_url="http://original/poster.jpg")

        track = db.get_mirrored_playlist_tracks(pid)[0]
        db.update_mirrored_track_extra_data(
            track["id"],
            {"discovered": True, "matched_data": {"image_url": "http://a/cover.jpg"}},
        )
        db.backfill_missing_mirrored_covers(1)
        assert db.get_mirrored_playlist(pid)["image_url"] == "http://original/poster.jpg"

    def test_the_collage_collects_tiles_in_track_order(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db)
        first, second = db.get_mirrored_playlist_tracks(pid)[:2]
        db.update_mirrored_track_extra_data(
            first["id"], {"discovered": True, "matched_data": {"image_url": "http://first.jpg"}}
        )
        db.update_mirrored_track_extra_data(
            second["id"], {"discovered": True, "matched_data": {"image_url": "http://second.jpg"}}
        )
        db.backfill_missing_mirrored_covers(1)
        assert db.get_mirrored_playlist(pid)["cover_tiles"] == (
            '["http://first.jpg", "http://second.jpg"]'
        )

    def test_the_collage_never_repeats_the_same_cover(self, tmp_path):
        # A playlist from one album would otherwise render the same tile four
        # times, which reads as a rendering bug rather than a design.
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db)
        for track in db.get_mirrored_playlist_tracks(pid):
            db.update_mirrored_track_extra_data(
                track["id"],
                {"discovered": True, "matched_data": {"image_url": "http://same.jpg"}},
            )
        db.backfill_missing_mirrored_covers(1)
        assert db.get_mirrored_playlist(pid)["cover_tiles"] == '["http://same.jpg"]'

    def test_an_undiscovered_write_leaves_the_collage_alone(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db)
        track = db.get_mirrored_playlist_tracks(pid)[0]
        db.backfill_missing_mirrored_covers(1)
        db.update_mirrored_track_extra_data(
            track["id"], {"discovery_attempted": True, "discovered": False}
        )
        # No match, nothing to rebuild — the cached (empty) tiles stand.
        assert db.get_mirrored_playlist(pid)["cover_tiles"] == "[]"

    def test_the_track_write_itself_still_succeeds(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db)
        track = db.get_mirrored_playlist_tracks(pid)[0]
        assert db.update_mirrored_track_extra_data(
            track["id"],
            {"discovered": True, "matched_data": {"image_url": "http://a/cover.jpg"}},
        ) is True
        assert db.get_mirrored_playlist_tracks(pid)[0]["extra_data"] is not None


class TestCoverBackfillForExistingPlaylists:
    """The rows that were discovered BEFORE the derivation existed.

    That is most of Boulder's library: their extra_data already holds the
    match, so nothing will ever write it again. The list read fills them in.
    """

    def _mirror_with_discovered_track(self, db, image=None, cover="http://found.jpg"):
        pid = db.mirror_playlist(
            source="youtube",
            source_playlist_id=f"h{id(db)}{image}",
            name="Mirror",
            tracks=[
                {"track_name": "One", "artist_name": "A", "source_track_id": "t1"},
                {"track_name": "Two", "artist_name": "B", "source_track_id": "t2"},
            ],
            profile_id=1,
            image_url=image,
        )
        # Write the match straight to the column, simulating a row discovered
        # before the derivation existed.
        import json as _json

        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM mirrored_playlist_tracks WHERE playlist_id = ? ORDER BY position",
                (pid,),
            )
            first = cur.fetchall()[0]["id"]
            cur.execute(
                "UPDATE mirrored_playlist_tracks SET extra_data = ? WHERE id = ?",
                (_json.dumps({"discovered": True, "matched_data": {"image_url": cover}}), first),
            )
            cur.execute(
                "UPDATE mirrored_playlists SET image_url = ? WHERE id = ?", (image, pid)
            )
            conn.commit()
        return pid

    def test_an_already_discovered_playlist_gains_its_cover(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror_with_discovered_track(db)
        assert not db.get_mirrored_playlist(pid).get("image_url")

        assert db.backfill_missing_mirrored_covers(1) == 1
        assert db.get_mirrored_playlist(pid)["cover_tiles"] == '["http://found.jpg"]'

    def test_simply_listing_the_library_fixes_it(self, tmp_path):
        # The whole point: the first visit after an upgrade repairs the page.
        db = MusicDatabase(str(tmp_path / "music.db"))
        self._mirror_with_discovered_track(db)
        rows = db.get_mirrored_playlists(profile_id=1)
        assert rows[0]["cover_tiles"] == '["http://found.jpg"]'

    def test_a_second_pass_does_nothing_at_all(self, tmp_path):
        # Self-limiting: it short-circuits on the count once every playlist has
        # a cover, so it is free on every visit after the first.
        db = MusicDatabase(str(tmp_path / "music.db"))
        self._mirror_with_discovered_track(db)
        assert db.backfill_missing_mirrored_covers(1) == 1
        assert db.backfill_missing_mirrored_covers(1) == 0

    def test_a_source_supplied_poster_is_never_replaced(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror_with_discovered_track(db, image="http://original.jpg")
        db.backfill_missing_mirrored_covers(1)
        assert db.get_mirrored_playlist(pid)["image_url"] == "http://original.jpg"

    def test_an_undiscovered_playlist_stays_bare(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = db.mirror_playlist(
            source="youtube",
            source_playlist_id="bare",
            name="Bare",
            tracks=[{"track_name": "One", "artist_name": "A"}],
            profile_id=1,
        )
        assert db.backfill_missing_mirrored_covers(1) == 0
        # Written empty rather than left NULL, so it is not re-scanned forever.
        assert db.get_mirrored_playlist(pid)["cover_tiles"] == "[]"

    def test_another_profile_is_left_alone(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        self._mirror_with_discovered_track(db)
        assert db.backfill_missing_mirrored_covers(2) == 0


class TestServerLink:
    """Recording which server playlist a mirror corresponds to.

    Today that relationship is a NAME match made fresh on every visit to the
    server tab, which is why a disambiguation modal exists. These columns store
    the answer once it has actually been resolved.

    Nothing reads them yet, on purpose: the write lands first and gets checked
    against real data before any behaviour depends on it.
    """

    def _mirror(self, db, profile_id=1):
        return db.mirror_playlist(
            source="spotify",
            source_playlist_id=f"sp{profile_id}",
            name="Road Trip",
            tracks=[{"track_name": "One", "artist_name": "A"}],
            profile_id=profile_id,
        )

    def test_a_new_mirror_starts_unlinked(self, tmp_path):
        # Nullable throughout, so every existing row means "not linked" and
        # behaves exactly as it does today.
        db = MusicDatabase(str(tmp_path / "music.db"))
        row = db.get_mirrored_playlist(self._mirror(db))
        assert row["server_playlist_id"] is None
        assert row["server_type"] is None
        assert row["server_linked_at"] is None

    def test_it_records_the_resolved_link(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db)
        assert db.link_mirrored_playlist_to_server(pid, "12345", "plex") is True

        row = db.get_mirrored_playlist(pid)
        assert row["server_playlist_id"] == "12345"
        assert row["server_type"] == "plex"
        assert row["server_linked_at"] is not None

    def test_relinking_replaces_the_previous_answer(self, tmp_path):
        # A playlist can be re-pointed at a different server playlist, and the
        # newest resolution is the true one.
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db)
        db.link_mirrored_playlist_to_server(pid, "111", "plex")
        db.link_mirrored_playlist_to_server(pid, "222", "jellyfin")

        row = db.get_mirrored_playlist(pid)
        assert row["server_playlist_id"] == "222"
        assert row["server_type"] == "jellyfin"

    def test_it_refuses_an_incomplete_link(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db)
        assert db.link_mirrored_playlist_to_server(pid, "", "plex") is False
        assert db.link_mirrored_playlist_to_server(pid, "123", "") is False
        assert db.get_mirrored_playlist(pid)["server_playlist_id"] is None

    def test_an_unknown_playlist_is_a_no_op(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        assert db.link_mirrored_playlist_to_server(9999, "123", "plex") is False

    def test_another_profile_cannot_link_your_mirror(self, tmp_path):
        # Owner-scoped like every other request-facing mirror write: a foreign
        # mirror must be indistinguishable from a missing one.
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db, profile_id=1)
        assert db.link_mirrored_playlist_to_server(pid, "123", "plex", profile_id=2) is False
        assert db.get_mirrored_playlist(pid)["server_playlist_id"] is None
        assert db.link_mirrored_playlist_to_server(pid, "123", "plex", profile_id=1) is True

    def test_the_link_does_not_disturb_anything_else_on_the_row(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._mirror(db)
        before = db.get_mirrored_playlist(pid)
        db.link_mirrored_playlist_to_server(pid, "123", "plex")
        after = db.get_mirrored_playlist(pid)

        for field in ("name", "source", "source_playlist_id", "track_count", "image_url"):
            assert after[field] == before[field]
        assert len(db.get_mirrored_playlist_tracks(pid)) == 1


class TestTrackArtFromDiscovery:
    """Boulder: "if the tracks of a playlist are discovered, when opening the
    playlist details modal, it should show the pictures for the tracks".

    They never could. The mirror-track projections send no image_url at all, so
    the column is empty for essentially every source and the modal rendered rows
    of blank squares. Discovery DOES know the artwork — it is inside the match
    it wrote to extra_data — so the read hands it back.
    """

    def _playlist_with_tracks(self, db):
        return db.mirror_playlist(
            source="youtube",
            source_playlist_id="arty",
            name="Mirror",
            tracks=[
                {"track_name": "One", "artist_name": "A", "source_track_id": "t1"},
                {"track_name": "Two", "artist_name": "B", "source_track_id": "t2"},
            ],
            profile_id=1,
        )

    def test_a_discovered_track_gets_its_album_art(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._playlist_with_tracks(db)
        first = db.get_mirrored_playlist_tracks(pid)[0]
        assert not first.get("image_url")

        db.update_mirrored_track_extra_data(
            first["id"],
            {"discovered": True, "matched_data": {"image_url": "http://art/cover.jpg"}},
        )
        assert db.get_mirrored_playlist_tracks(pid)[0]["image_url"] == "http://art/cover.jpg"

    def test_an_undiscovered_track_stays_bare(self, tmp_path):
        # Same rule as the playlist cover: art appears only once discovery has
        # found something.
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._playlist_with_tracks(db)
        track = db.get_mirrored_playlist_tracks(pid)[1]
        db.update_mirrored_track_extra_data(track["id"], {"discovered": False})
        assert not db.get_mirrored_playlist_tracks(pid)[1].get("image_url")

    def test_a_source_supplied_track_image_is_never_replaced(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = db.mirror_playlist(
            source="deezer",
            source_playlist_id="dz",
            name="Mirror",
            tracks=[
                {
                    "track_name": "One",
                    "artist_name": "A",
                    "image_url": "http://original/track.jpg",
                }
            ],
            profile_id=1,
        )
        track = db.get_mirrored_playlist_tracks(pid)[0]
        db.update_mirrored_track_extra_data(
            track["id"],
            {"discovered": True, "matched_data": {"image_url": "http://match/cover.jpg"}},
        )
        assert (
            db.get_mirrored_playlist_tracks(pid)[0]["image_url"] == "http://original/track.jpg"
        )

    def test_malformed_extra_data_does_not_break_the_read(self, tmp_path):
        db = MusicDatabase(str(tmp_path / "music.db"))
        pid = self._playlist_with_tracks(db)
        track = db.get_mirrored_playlist_tracks(pid)[0]
        with db._get_connection() as conn:
            conn.execute(
                "UPDATE mirrored_playlist_tracks SET extra_data = ? WHERE id = ?",
                ("{not json", track["id"]),
            )
            conn.commit()
        rows = db.get_mirrored_playlist_tracks(pid)
        assert len(rows) == 2
        assert not rows[0].get("image_url")


# ── the batch status counts' in-library figure ─────────────────────────────
#
# The flag the sync matcher writes is the answer; the join underneath it is the
# fallback for a playlist nobody has synced since the flag was added. That
# fallback reads Library v2, and the whole counts function is wrapped in one
# try/except that logs and returns zeros — so a broken query here reports "you
# own none of it" rather than raising. These pin the SQL.

def _mirror_one(db, *, track_name="Song", artist_name="Artist",
                source_track_id="sp1", extra_data=None):
    return db.mirror_playlist(
        source="spotify",
        source_playlist_id="pl1",
        name="Mirror",
        tracks=[{
            "track_name": track_name,
            "artist_name": artist_name,
            "source_track_id": source_track_id,
            "extra_data": json.dumps(extra_data or {}),
        }],
        profile_id=1,
    )


def _counts(db, playlist_id):
    return db.get_all_mirrored_playlist_status_counts(1)[playlist_id]


def test_the_stored_flag_is_believed_over_the_join(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = _mirror_one(db, extra_data={
        "in_library": True, "library_checked_at": 1,
    })

    counts = _counts(db, playlist_id)
    assert counts["in_library"] == 1
    assert counts["library_checked"] == 1


def test_a_checked_track_the_matcher_rejected_stays_rejected(tmp_path):
    """Owning a same-named track does not overrule the matcher's verdict — the
    flag is right about the cases the join cannot reach, in both directions."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = _mirror_one(db, extra_data={
        "in_library": False, "library_checked_at": 1,
    })
    conn = db._get_connection()
    seed_library_track(conn, artist="Artist", album="Album", title="Song",
                       file_path="/music/Artist/Album/Song.flac")
    conn.commit()
    conn.close()

    assert _counts(db, playlist_id)["in_library"] == 0


def test_an_unchecked_track_falls_back_to_the_library_v2_join(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = _mirror_one(db)
    assert _counts(db, playlist_id)["in_library"] == 0

    conn = db._get_connection()
    seed_library_track(conn, artist="Artist", album="Album", title="Song",
                       file_path="/music/Artist/Album/Song.flac")
    conn.commit()
    conn.close()

    counts = _counts(db, playlist_id)
    assert counts["in_library"] == 1
    # Nobody has actually looked — the fallback answering is not a check.
    assert counts["library_checked"] == 0


def test_the_fallback_matches_on_the_source_id_too(tmp_path):
    """Id-first: a differently-spelled title still counts when the Spotify id
    is the one the mirrored row carries."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = _mirror_one(db, track_name="Song (Remastered)",
                              artist_name="Artist feat. Someone")

    conn = db._get_connection()
    track_id = seed_library_track(conn, artist="Artist", album="Album",
                                  title="Song",
                                  file_path="/music/Artist/Album/Song.flac")
    conn.execute("UPDATE lib2_tracks SET spotify_id='sp1' WHERE id=?", (track_id,))
    conn.commit()
    conn.close()

    assert _counts(db, playlist_id)["in_library"] == 1


def test_a_provider_only_row_with_no_file_is_not_owned(tmp_path):
    """A discography row lib2 knows about but holds no active file for is
    known, not owned."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = _mirror_one(db)

    conn = db._get_connection()
    seed_library_track(conn, artist="Artist", album="Album", title="Song")
    conn.commit()
    conn.close()

    assert _counts(db, playlist_id)["in_library"] == 0


# The single-playlist variant reads the same way. It feeds the sync history
# entry while the batched one renders the card, and one playlist reporting two
# different ownership figures is worse than either being a little wrong.

def _one_count(db, playlist_id):
    return db.get_mirrored_playlist_status_counts(playlist_id)


def test_the_single_playlist_count_believes_the_flag_too(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = _mirror_one(db, extra_data={
        "in_library": True, "library_checked_at": 1,
    })

    assert _one_count(db, playlist_id)["in_library"] == 1


def test_the_single_playlist_count_honours_a_rejection(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = _mirror_one(db, extra_data={
        "in_library": False, "library_checked_at": 1,
    })
    conn = db._get_connection()
    seed_library_track(conn, artist="Artist", album="Album", title="Song",
                       file_path="/music/Artist/Album/Song.flac")
    conn.commit()
    conn.close()

    assert _one_count(db, playlist_id)["in_library"] == 0


def test_the_single_playlist_count_falls_back_to_the_join(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = _mirror_one(db)
    assert _one_count(db, playlist_id)["in_library"] == 0

    conn = db._get_connection()
    seed_library_track(conn, artist="Artist", album="Album", title="Song",
                       file_path="/music/Artist/Album/Song.flac")
    conn.commit()
    conn.close()

    assert _one_count(db, playlist_id)["in_library"] == 1


def test_both_variants_agree_on_the_same_playlist(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = _mirror_one(db)
    conn = db._get_connection()
    seed_library_track(conn, artist="Artist", album="Album", title="Song",
                       file_path="/music/Artist/Album/Song.flac")
    conn.commit()
    conn.close()

    assert _counts(db, playlist_id)["in_library"] == \
        _one_count(db, playlist_id)["in_library"] == 1
