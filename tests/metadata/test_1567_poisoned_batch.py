"""Regression tests for #1567 (kevin2xk): 11 unrelated tracks (Finger Eleven,
Foo Fighters, Godsmack, Nickelback, Default, Three Days Grace, Staind,
Audioslave, RHCP) were filed as ONE bogus album "Music" by "Nickelback",
2001, with duplicated track numbers.

Covers:
- extract_source_metadata: a poisoned batch album context (first-row-wins —
  album_ctx.artists and _explicit_artist_context poisoned by the SAME row, so
  the #1316 check can't fire) must not stamp album/date/total_tracks from
  the batch context. Falls back to the track's own provider album, loudly.
- Preserved behavior: single-artist albums, genuine compilations with a real
  shared album id, the explicit "Various Artists" case, and #1316's shape
  (only the artist hint poisoned) keep trusting the album context.
- embed_source_ids: track-level Last.fm/Genius lookups prefer the track
  artist; other sources keep the album_artist-preferred name.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _cfg():
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: {
        "metadata_enhancement.enabled": True,
        "metadata_enhancement.tags.write_multi_artist": False,
        "metadata_enhancement.tags.feat_in_title": False,
        "metadata_enhancement.tags.artist_separator": ", ",
        "file_organization.collab_artist_mode": "first",
    }.get(key, default)
    return cfg


def _extract(context, artist_dict, album_info=None):
    from core.metadata import source as src
    with patch.object(src, "get_config_manager", return_value=_cfg()):
        return src.extract_source_metadata(context, artist_dict, album_info or {})


def _poisoned_batch_context(track_artist, track_title="Everlong", own_album_name="music"):
    """The #1567 shape as it reaches extract_source_metadata: the batch album
    context (name "Music", 10 tracks, 2001, Nickelback) poisoned first-row-wins,
    and _explicit_artist_context poisoned by the same row — so the #1316
    artist check sees agreement and waves it through. The ``artist`` param is
    the batch artist too (core/downloads/candidates.py builds it from
    ``_explicit_artist_context``) — the ground truth lives only in the
    per-track payloads (original_search_result + track_info.spotify_data).
    """
    return {
        "original_search_result": {
            "title": track_title,
            "artist": track_artist,
            "artists": [{"name": track_artist}],
        },
        "album": {
            "id": "wishlist_album",
            "name": "Music",
            "total_tracks": 10,
            "release_date": "2001",
            "artists": [{"name": "Nickelback"}],
        },
        "track_info": {
            "_explicit_artist_context": {"id": "wishlist", "name": "Nickelback", "genres": []},
            "spotify_data": {
                "artists": [{"name": track_artist}],
                "album": {"name": own_album_name},
            },
        },
        "source": "spotify",
    }


def _real_bundle_artist_param():
    """What the ``artist`` param looks like in the real bundle path: the
    poisoned batch artist, NOT the track's own artist (M1)."""
    return {"id": "wishlist", "name": "Nickelback", "genres": []}


def test_1567_poisoned_batch_album_context_distrusted(caplog):
    """M1: the REAL bundle-path call shape — the ``artist`` param IS the
    poisoned batch artist (candidates.py builds it from
    ``_explicit_artist_context``). The round-1 check unioned that param into
    its ground-truth set, so the claim corroborated itself and distrust never
    fired. The fixed check uses only the track's own provider data: the whole
    album identity falls back to the track's own provider album, loudly."""
    with caplog.at_level("WARNING", logger="soulsync.metadata.source"):
        md = _extract(_poisoned_batch_context("Foo Fighters"), _real_bundle_artist_param())
    assert md["artist"] == "Foo Fighters"
    assert md["album_artist"] == "Foo Fighters"  # NOT Nickelback
    assert md["album"] == "music"  # the track's own provider album, not "Music"
    assert md["total_tracks"] == 1  # NOT 10
    assert "date" not in md  # NOT 2001
    warnings = [r for r in caplog.records
                if r.levelno >= 20 and "#1567" in r.getMessage()]
    assert warnings, "expected a loud #1567 warning"
    assert "Nickelback" in warnings[0].getMessage()


def test_1567_poisoned_batch_own_album_data_used_when_present():
    """When the track's own provider album carries real data, the fallback
    uses it (name, count, date) instead of the batch's."""
    ctx = _poisoned_batch_context("Foo Fighters", own_album_name="Echoes, Silence, Patience & Grace")
    ctx["track_info"]["spotify_data"]["album"]["total_tracks"] = 12
    ctx["track_info"]["spotify_data"]["album"]["release_date"] = "2007-09-25"
    md = _extract(ctx, _real_bundle_artist_param())
    assert md["album"] == "Echoes, Silence, Patience & Grace"
    assert md["total_tracks"] == 12
    assert md["date"] == "2007-09-25"


def test_1567_single_artist_album_unaffected():
    """Genuine single-artist batch: the claimed album artist matches the
    track's own artist, so the batch context is trusted as before."""
    context = {
        "original_search_result": {"title": "Song", "artist": "Ryoto",
                                   "artists": [{"name": "Ryoto"}]},
        "album": {"name": "Cha-La Head-Cha-La", "total_tracks": 12,
                  "release_date": "2024-01-01", "artists": [{"name": "Ryoto"}]},
        "track_info": {"_explicit_artist_context": {"id": "wishlist", "name": "Ryoto", "genres": []}},
        "source": "spotify",
    }
    md = _extract(context, {"name": "Ryoto"})
    assert md["album"] == "Cha-La Head-Cha-La"
    assert md["total_tracks"] == 12
    assert md["date"] == "2024-01-01"
    assert md["album_artist"] == "Ryoto"


def test_1567_various_artists_compilation_unaffected():
    """The explicit "Various Artists" claim is a genuine multi-artist
    compilation and is always trusted, even though no single track artist
    matches it."""
    context = {
        "original_search_result": {"title": "Star Fighter", "artist": "Wice",
                                   "artists": [{"name": "Wice"}]},
        "album": {"name": "Magnatron 2.0", "total_tracks": 20,
                  "release_date": "2023-05-05", "artists": [{"name": "Various Artists"}]},
        "track_info": {"_explicit_artist_context": {"id": "wishlist", "name": "Various Artists", "genres": []}},
        "source": "spotify",
    }
    md = _extract(context, {"name": "Wice"})
    assert md["album"] == "Magnatron 2.0"
    assert md["total_tracks"] == 20
    assert md["date"] == "2023-05-05"
    assert md["album_artist"] == "Various Artists"


def test_1567_1316_shape_keeps_trusting_album_context():
    """#1316's shape: the explicit artist hint was poisoned but the album
    context's own artist backs the track — only the hint was bad, so the
    album context stays trustworthy for album/date/total_tracks."""
    context = {
        "original_search_result": {"title": "Snoopafella", "artist": "Snoop Dogg",
                                   "artists": [{"name": "Snoop Dogg"}]},
        "album": {"name": "Tha Doggfather", "total_tracks": 14,
                  "release_date": "1996-11-12", "artists": [{"name": "Snoop Dogg"}]},
        "track_info": {"_explicit_artist_context": {"id": "wishlist", "name": "Alanis Morissette", "genres": []}},
        "source": "spotify",
    }
    md = _extract(context, {"name": "Snoop Dogg"})
    assert md["album_artist"] == "Snoop Dogg"  # the #1316 fix still applies
    assert md["album"] == "Tha Doggfather"  # album context still trusted
    assert md["total_tracks"] == 14
    assert md["date"] == "1996-11-12"


def test_1567_no_batch_context_nothing_changes():
    """No batch claim at all — plain single-track flow is untouched."""
    context = {
        "original_search_result": {"title": "Song", "artists": [{"name": "Solo Act"}]},
        "source": "spotify",
    }
    md = _extract(context, {"name": "Solo Act"})
    assert md["album"] == "Song"
    assert md["total_tracks"] == 1
    assert md["album_artist"] == "Solo Act"


def test_1567_lastfm_and_genius_use_track_artist():
    """Track-level lookups (Last.fm track info, Genius song search) key on
    the TRACK artist; every other source keeps the album_artist-preferred
    name."""
    from core.metadata import source as src

    seen = {}

    def _recorder(name):
        def _fn(pp, metadata, cfg, runtime, track_title, artist_name, **kw):
            seen[name] = artist_name
        return _fn

    with patch.object(src, "_process_lastfm_source", side_effect=_recorder("lastfm")), \
         patch.object(src, "_process_genius_source", side_effect=_recorder("genius")), \
         patch.object(src, "_process_musicbrainz_source", side_effect=_recorder("musicbrainz")), \
         patch.object(src, "_process_audiodb_source", side_effect=_recorder("audiodb")), \
         patch.object(src, "_process_bandcamp_source", side_effect=_recorder("bandcamp")):
        for source_name in ("lastfm", "genius", "musicbrainz", "audiodb", "bandcamp"):
            src._process_source_enrichment(
                source_name, {}, {}, MagicMock(), None, "Everlong",
                "Nickelback",  # album_artist-preferred name
                track_artist_name="Foo Fighters",
            )

    assert seen["lastfm"] == "Foo Fighters"
    assert seen["genius"] == "Foo Fighters"
    assert seen["musicbrainz"] == "Nickelback"
    assert seen["audiodb"] == "Nickelback"
    assert seen["bandcamp"] == "Nickelback"


def test_1567_embed_source_ids_computes_track_artist():
    """embed_source_ids derives the track-artist name from metadata["artist"]
    and hands it to the enrichment dispatcher (which routes it to the
    track-level lookups)."""
    from core.metadata import source as src

    metadata = {"title": "Everlong", "artist": "Foo Fighters", "album_artist": "Nickelback"}
    with patch.object(src, "get_config_manager", return_value=_cfg()), \
         patch.object(src, "get_mutagen_symbols", return_value=object()), \
         patch.object(src, "get_database", return_value=MagicMock()), \
         patch("core.metadata.registry.is_jiosaavn_enabled", return_value=False), \
         patch("core.metadata.registry.is_source_enabled", return_value=True), \
         patch.object(src, "_process_source_enrichment") as proc:
        src.embed_source_ids(MagicMock(), metadata, context={"source": "spotify"})

    assert proc.call_count > 0
    for call in proc.call_args_list:
        assert call.kwargs["track_artist_name"] == "Foo Fighters"
        # the album_artist-preferred name still goes to the other sources
        assert call.args[6] == "Nickelback"


# ---------------------------------------------------------------------------
# Round 2 (reviewer 1): M1/M2/m1/m2 findings
# ---------------------------------------------------------------------------


def _first_row_own_track_context():
    """M2: the batch's FIRST row was Nickelback's own track — the claimed
    (batch) artist legitimately equals the track's own artist, so the artist
    trip-wire cannot fire. The track's own provider album ("Silver Side Up",
    real id) still contradicts the bogus batch context ("music")."""
    return {
        "original_search_result": {
            "title": "How You Remind Me",
            "artist": "Nickelback",
            "artists": [{"name": "Nickelback"}],
        },
        "album": {
            "id": "wishlist_album",
            "name": "music",
            "total_tracks": 10,
            "release_date": "2001",
            "artists": [{"name": "Nickelback"}],
        },
        "track_info": {
            "_explicit_artist_context": {"id": "wishlist", "name": "Nickelback", "genres": []},
            "spotify_data": {
                "artists": [{"name": "Nickelback"}],
                "album": {
                    "id": "6ndxK9p7wN9p7wN9p7wN9",
                    "name": "Silver Side Up",
                    "total_tracks": 11,
                    "release_date": "2001-09-11",
                },
            },
        },
        "source": "spotify",
    }


def test_1567_first_row_own_track_album_identity_distrusts(caplog):
    """M2: claimed artist == the track's own artist (the incident's first row
    WAS Nickelback) — the old code stamped date=2001/total_tracks=10 anyway.
    The album-identity trip-wire compares the track's own provider album
    against the batch context and distrusts on disagreement."""
    with caplog.at_level("WARNING", logger="soulsync.metadata.source"):
        md = _extract(_first_row_own_track_context(), _real_bundle_artist_param())
    assert md["album"] == "Silver Side Up"  # NOT "music"
    assert md["total_tracks"] == 11  # NOT 10
    assert md["date"] == "2001-09-11"  # NOT "2001"
    assert md["album_artist"] == "Nickelback"  # the track's own artist, correctly kept
    warnings = [r for r in caplog.records
                if r.levelno >= 20 and "#1567" in r.getMessage()]
    assert warnings, "expected a loud #1567 warning"
    assert "Silver Side Up" in warnings[0].getMessage()


def test_1567_shared_but_wrong_album_id_distrusts(caplog):
    """M2: both sides carry REAL provider album ids and they differ — the
    strongest album-identity disagreement. Names agree here on purpose, so
    only the id trip-wire can fire."""
    ctx = _first_row_own_track_context()
    ctx["album"]["id"] = "bbbbbbbbbbbbbbbbbbbbbb"
    ctx["album"]["name"] = "Silver Side Up"  # name agrees; ids don't
    ctx["album"]["total_tracks"] = 11
    ctx["album"]["release_date"] = "2001-09-11"
    with caplog.at_level("WARNING", logger="soulsync.metadata.source"):
        md = _extract(ctx, _real_bundle_artist_param())
    assert md["total_tracks"] == 11
    assert md["album"] == "Silver Side Up"
    assert any("#1567" in r.getMessage() for r in caplog.records if r.levelno >= 20)


def test_1567_placeholder_album_ids_never_disagree():
    """Fail-open: pipeline placeholder ids ('wishlist_album', '_name_*', ...)
    are 'unknown', never a disagreement — with everything else agreeing,
    no distrust fires."""
    ctx = _first_row_own_track_context()
    ctx["track_info"]["spotify_data"]["album"]["id"] = "wishlist_album"
    ctx["album"]["id"] = "_name_silver side up"
    ctx["album"]["name"] = "Silver Side Up"
    ctx["album"]["total_tracks"] = 11
    ctx["album"]["release_date"] = "2001-09-11"
    md = _extract(ctx, _real_bundle_artist_param())
    assert md["album"] == "Silver Side Up"
    assert md["total_tracks"] == 11
    assert md["date"] == "2001-09-11"


def test_1567_total_tracks_disagreement_distrusts():
    """M2: artist matches and album names agree, but the track's own album
    knows 11 tracks while the batch claims 10 — affirmative contradiction,
    distrust fires."""
    ctx = _first_row_own_track_context()
    ctx["track_info"]["spotify_data"]["album"]["name"] = "music"  # name agrees (poisoned at add time)
    ctx["track_info"]["spotify_data"]["album"].pop("id")
    md = _extract(ctx, _real_bundle_artist_param())
    assert md["total_tracks"] == 11  # the track's own count, NOT the batch's 10
    assert md["album"] == "music"  # best available name (add-time poisoning is out of scope)


def test_1567_year_only_vs_full_date_agrees():
    """'2001' vs '2001-09-11' is the same release at different precision —
    not a disagreement. With everything else agreeing, no distrust fires."""
    ctx = _first_row_own_track_context()
    ctx["album"]["name"] = "Silver Side Up"
    ctx["album"]["total_tracks"] = 11
    ctx["album"]["id"] = "6ndxK9p7wN9p7wN9p7wN9"  # same real id
    # release years: 2001 vs 2001 — agree
    md = _extract(ctx, _real_bundle_artist_param())
    assert md["album"] == "Silver Side Up"
    assert md["total_tracks"] == 11
    assert md["date"] == "2001"  # trusted batch context's date stands


def test_1567_release_year_disagreement_distrusts():
    """Different release years with everything else agreeing still distrusts —
    when the batch context has no real album id of its own (a real id would
    be authoritative over a year quirk)."""
    ctx = _first_row_own_track_context()
    ctx["album"]["name"] = "Silver Side Up"
    ctx["album"]["total_tracks"] = 11
    ctx["album"]["id"] = "wishlist_album"  # placeholder: no verifiable identity
    ctx["album"]["release_date"] = "2003"  # batch says 2003, track's own says 2001
    md = _extract(ctx, _real_bundle_artist_param())
    assert md["date"] == "2001-09-11"  # the track's own date


def test_1567_va_marker_variants_always_trusted():
    """m2: every Various-Artists spelling keeps the batch context trusted
    even when no track artist matches the claim."""
    for marker in ("va", "v.a.", "v.a", "various", "Various Artists"):
        context = {
            "original_search_result": {"title": "Song", "artist": "Wice",
                                       "artists": [{"name": "Wice"}]},
            "album": {"name": "Magnatron 2.0", "total_tracks": 20,
                      "release_date": "2023-05-05", "artists": [{"name": marker}]},
            "track_info": {"_explicit_artist_context": {"id": "wishlist", "name": marker, "genres": []}},
            "source": "spotify",
        }
        md = _extract(context, {"id": "wishlist", "name": marker, "genres": []})
        assert md["album"] == "Magnatron 2.0", marker
        assert md["total_tracks"] == 20, marker
        assert md["album_artist"] == marker, marker


def test_1567_distrust_quarantines_genres_artwork_and_release_id():
    """m1: when distrust fires, the poisoned batch context must not leak
    through genres, artwork, or the MusicBrainz release id either."""
    ctx = _poisoned_batch_context("Foo Fighters")
    ctx["album"]["genres"] = ["Post-Grunge", "Alternative Metal"]
    ctx["album"]["image_url"] = "http://poison.example/art.jpg"
    ctx["album"]["musicbrainz_release_id"] = "poison-release-id"
    album_info = {"is_album": True, "album_name": "Music", "track_number": 3,
                  "album_image_url": "http://poison.example/info.jpg"}
    md = _extract(ctx, _real_bundle_artist_param(), album_info)
    assert md.get("genre") in (None, ""), "batch genres must not leak"
    assert md.get("album_art_url") in (None, ""), "batch artwork must not leak"
    assert "musicbrainz_release_id" not in md, "batch MB release id must not leak"
    assert md["album"] == "music"  # track's own album (is_album branch quarantined too)
    assert md["total_tracks"] == 1


def test_1567_distrust_uses_track_own_artwork_when_present():
    """m1: the quarantine falls back to track-own sources — the track's own
    provider album art is used when the batch art is refused."""
    ctx = _poisoned_batch_context("Foo Fighters")
    ctx["album"]["image_url"] = "http://poison.example/art.jpg"
    ctx["track_info"]["spotify_data"]["album"]["images"] = [
        {"url": "http://real.example/echoes.jpg"}]
    md = _extract(ctx, _real_bundle_artist_param())
    assert md["album_art_url"] == "http://real.example/echoes.jpg"


def test_1567_track_own_provider_album_partial_dicts():
    """m2: _track_own_provider_album tolerates empty/partial/missing album
    payloads without crashing, returning empty defaults."""
    from core.metadata import source as src
    empty = src._track_own_provider_album({}, {})
    assert empty == {"name": "", "id": "", "id_source": "", "total_tracks": 0,
                     "release_date": "", "image_url": ""}
    assert src._track_own_provider_album(None, None)["name"] == ""
    # string album payloads normalize to a name dict
    assert src._track_own_provider_album({"album": "Just A Name"}, None)["name"] == "Just A Name"
    # spotify_data as a JSON string (wishlist rows store it serialized)
    import json as _json
    sp = {"spotify_data": _json.dumps({"album": {"name": "Real Album", "id": "abc123",
                                                  "total_tracks": 9}})}
    got = src._track_own_provider_album({}, sp)
    assert got["name"] == "Real Album"
    assert got["id"] == "abc123"
    assert got["total_tracks"] == 9
    # garbage total_tracks never raises
    got2 = src._track_own_provider_album({"album": {"total_tracks": "many"}}, {})
    assert got2["total_tracks"] == 0


def test_1567_real_album_id_keeps_artist_credit_trusted():
    """A real shared provider album id makes the batch album identity genuine:
    an artist-credit disagreement is then a credit nuance (soundtrack composer
    vs performer), not a poisoned batch — no distrust fires (#1607)."""
    context = {
        "original_search_result": {"title": "How Far I'll Go",
                                   "artists": [{"name": "Auli'i Cravalho"}]},
        "album": {"id": "14582002", "name": "Moana (Deluxe)", "total_tracks": 59,
                  "release_date": "2016-11-18",
                  "artists": [{"name": "Lin-Manuel Miranda"}]},
        "track_info": {"artists": [{"name": "Auli'i Cravalho"}]},
        "source": "deezer",
    }
    md = _extract(context, {"name": "Auli'i Cravalho", "genres": []})
    assert md["album"] == "Moana (Deluxe)"
    assert md["total_tracks"] == 59
    assert md["date"] == "2016-11-18"
    assert md["album_artist"] == "Lin-Manuel Miranda"


# ---------------------------------------------------------------------------
# Round 3 (reviewer 2): the RESIDUAL path. After demotion, master.py path B
# corrects _explicit_artist_context to the track's own artist, but album_ctx
# is still built from the richest same-key wishlist row — a stranger's row.
# The #1316 check then fired on the disagreement and "trusted the track
# data" — but its own_album_artist came from the POISONED album_ctx, so it
# re-stamped Nickelback/total 10/2001/strangers' art. The fix corroborates
# the batch context's artist against the track's own artists/album artists
# before the #1316 check may stamp it.
# ---------------------------------------------------------------------------


def _residual_path_context(ctx_artist_name="Nickelback", ctx_album_id="_name_music"):
    """The exact residual call shape: path B's album_ctx from the shared map
    (placeholder id, stranger's artists/total/date), the corrected explicit
    artist context (the track's OWN artist — path B sets it with no id key),
    and the row's own stored album dict WITH artists (the sub-case that was
    broken). The ``artist`` param is what core/downloads/candidates.py:583
    builds from the corrected _explicit_artist_context — the track's own
    artist, not the poisoned batch artist.
    """
    return {
        "original_search_result": {
            "title": "Everlong",
            "artist": "Foo Fighters",
            "artists": [{"name": "Foo Fighters"}],
        },
        "album": {
            "id": ctx_album_id,
            "name": "Music",
            "total_tracks": 10,
            "release_date": "2001",
            "images": [{"url": "http://poisoned.example/art.jpg"}],
            "artists": [{"name": ctx_artist_name}],
        },
        "track_info": {
            "_explicit_artist_context": {"name": "Foo Fighters"},
            "artists": [{"name": "Foo Fighters"}],
            "spotify_data": {
                "artists": [{"name": "Foo Fighters"}],
                "album": {"name": "music", "artists": [{"name": "Foo Fighters"}]},
            },
        },
        "source": "spotify",
    }


def _residual_artist_param():
    return {"id": "explicit_artist", "name": "Foo Fighters", "genres": []}


def test_1567_r2_residual_path_stranger_ctx_artist_distrusted(caplog):
    """The blocker: path B corrected the explicit artist but album_ctx is a
    stranger's row. The batch context must be quarantined — album_artist
    falls back to the track's own artist, and total/date/art are NOT stamped
    from the context — with a loud warning."""
    with caplog.at_level("WARNING", logger="soulsync.metadata.source"):
        md = _extract(
            _residual_path_context(),
            _residual_artist_param(),
            {"is_album": True, "album_name": "Music", "track_number": 3},
        )
    assert md["album_artist"] == "Foo Fighters"  # NOT Nickelback
    assert md["total_tracks"] == 1  # NOT the batch's 10 (own album has no total)
    assert md.get("date") != "2001"  # NOT the batch's date
    assert md["album"] == "music"  # the track's own provider album, not the batch's "Music"
    assert md.get("album_art_url") is None  # strangers' art quarantined
    assert "genre" not in md  # strangers' genres quarantined
    warnings = [r for r in caplog.records
                if r.levelno >= 20 and "#1567" in r.getMessage()]
    assert warnings, "expected a loud #1567 warning"
    assert "stranger" in warnings[0].getMessage()


def test_1567_r2_residual_path_va_credit_stays_trusted():
    """A Various Artists batch credit is a genuine multi-artist compilation —
    the #1316 disagreement branch keeps trusting it even with no real id."""
    ctx = _residual_path_context(ctx_artist_name="Various Artists")
    md = _extract(ctx, _residual_artist_param(),
                  {"is_album": True, "album_name": "Music", "track_number": 3})
    assert md["album_artist"] == "Various Artists"
    assert md["total_tracks"] == 10  # context still trusted
    assert md.get("date") == "2001"


def test_1567_r2_residual_path_real_id_stays_trusted():
    """A real shared provider album id is a genuine release: the #1316
    disagreement branch keeps stamping the context's artist (credit nuance,
    not poison — #1607) even when the explicit hint names someone else."""
    ctx = _residual_path_context(ctx_artist_name="Lin-Manuel Miranda",
                                 ctx_album_id="14582002")
    ctx["album"]["name"] = "Moana (Deluxe)"
    ctx["album"]["total_tracks"] = 59
    ctx["album"]["release_date"] = "2016-11-18"
    md = _extract(ctx, _residual_artist_param(),
                  {"is_album": True, "album_name": "Moana (Deluxe)", "track_number": 3})
    assert md["album_artist"] == "Lin-Manuel Miranda"
    assert md["total_tracks"] == 59
    assert md.get("date") == "2016-11-18"


def test_1567_r2_residual_path_empty_ground_truth_fail_open():
    """With no per-track artist data there is nothing to contradict the
    batch context's artist — the #1316 check behaves exactly as before."""
    ctx = _residual_path_context()
    ctx["original_search_result"] = {"title": "Everlong"}
    ctx["track_info"] = {"_explicit_artist_context": {"name": "Foo Fighters"}}
    md = _extract(ctx, {"id": "explicit_artist", "name": "Foo Fighters", "genres": []})
    assert md["album_artist"] == "Nickelback"  # #1316 behavior unchanged


# ---------------------------------------------------------------------------
# Round 3 (reviewer 3) findings
# ---------------------------------------------------------------------------


def test_1567_sentinel_album_ids_are_not_real_provider_ids():
    """R3F1 (unit): the pipeline's own sentinel ids must never count as real
    provider album ids. The local placeholder copy in core/imports/context.py
    drifted from CONTEXT_SENTINEL_IDS, so 'auto_import'/'explicit_artist'
    counted as REAL and silently bypassed every distrust wire."""
    from core.imports.context import is_real_provider_album_id
    for sentinel in ("auto_import", "explicit_album", "explicit_artist",
                     "from_sync_modal", "wishlist_album", "_name_Music", ""):
        assert not is_real_provider_album_id(sentinel), sentinel
    assert is_real_provider_album_id("4aawyAB9vmqN3uQ7FjRGT")  # spotify-shaped
    assert is_real_provider_album_id("12345678")  # deezer-shaped


def test_1567_auto_import_sentinel_album_id_distrusts(caplog):
    """R3F1 (end-to-end): a batch album context stamped with the 'auto_import'
    sentinel id is not a real provider album id — the stranger-artist claim
    must still distrust, loudly. Before the fix this sailed through every
    wire (album_artist='Nickelback', total_tracks=10, date=2001, no warning)."""
    ctx = _poisoned_batch_context("Foo Fighters")
    ctx["album"]["id"] = "auto_import"
    with caplog.at_level("WARNING", logger="soulsync.metadata.source"):
        md = _extract(ctx, _real_bundle_artist_param())
    assert md["album_artist"] == "Foo Fighters"  # NOT Nickelback
    assert md["album"] == "music"
    assert md["total_tracks"] != 10
    assert md.get("date") != "2001"
    warnings = [r for r in caplog.records
                if r.levelno >= 20 and "#1567" in r.getMessage()]
    assert warnings, "expected a loud #1567 warning"


def _featured_guest_context(claimed_album_artist):
    """R3F2 shape: the victim track is Rihanna ft. Eminem — Eminem is a
    FEATURED guest, and the track's own provider album data is sparse (no
    name/count/date to trip wire 2). The pile claims ``claimed_album_artist``
    with a placeholder id. A featured credit must not corroborate an
    album-artist claim."""
    return {
        "original_search_result": {
            "title": "Love The Way You Lie",
            "artist": "Rihanna",
            "artists": [{"name": "Rihanna"}, {"name": "Eminem"}],
            "album": {"artists": [{"name": "Rihanna"}]},
        },
        "album": {
            "id": "wishlist_album",
            "name": "Music",
            "total_tracks": 10,
            "release_date": "2001",
            "artists": [{"name": claimed_album_artist}],
        },
        "track_info": {
            "_explicit_artist_context": {
                "id": "wishlist", "name": claimed_album_artist, "genres": []},
            "spotify_data": {
                "artists": [{"name": "Rihanna"}, {"name": "Eminem"}],
                "album": {"artists": [{"name": "Rihanna"}]},
            },
        },
        "source": "spotify",
    }


def test_1567_featured_guest_does_not_corroborate_album_artist_claim():
    """R3F2: pile claims 'Eminem' as the album artist; the victim merely
    FEATURES Eminem. Pre-fix, the merged contributor set corroborated the
    claim and the full poison stamped (album_artist='Eminem', total=10,
    date=2001). Post-fix the batch is distrusted."""
    md = _extract(_featured_guest_context("Eminem"),
                  {"id": "wishlist", "name": "Eminem", "genres": []})
    assert md["album_artist"] == "Rihanna"  # NOT Eminem
    assert md["total_tracks"] != 10
    assert md.get("date") != "2001"


def test_1567_featured_guest_residual_path_b_corroboration():
    """R3F2, residual path-B variant: the #1316 correction already fixed the
    explicit hint to the track's own artist ('Rihanna'), but the batch album
    ctx still names the stranger ('Eminem'). Pre-fix, 'eminem' in the merged
    set made the stranger look corroborated and the #1316 branch stamped it.
    Post-fix the stranger is uncorroborated and the batch is quarantined."""
    ctx = _featured_guest_context("Eminem")
    ctx["track_info"]["_explicit_artist_context"] = {
        "id": "wishlist", "name": "Rihanna", "genres": []}
    md = _extract(ctx, {"id": "wishlist", "name": "Rihanna", "genres": []})
    assert md["album_artist"] == "Rihanna"  # NOT Eminem
    assert md["total_tracks"] != 10
    assert md.get("date") != "2001"


def test_1567_featured_guest_on_genuine_album_stays_trusted():
    """R3F2 control: Rihanna ft. Eminem on Rihanna's album, pile correctly
    claims Rihanna — the primary artist corroborates, batch stays trusted."""
    md = _extract(_featured_guest_context("Rihanna"),
                  {"id": "wishlist", "name": "Rihanna", "genres": []})
    assert md["album_artist"] == "Rihanna"
    assert md["total_tracks"] == 10  # context still trusted
    assert md.get("date") == "2001"


def test_1567_genuine_primary_album_artist_still_trusted():
    """R3F2 control: genuine single-artist batch where the claimed album
    artist is the track's primary artist — trusted exactly as before."""
    context = {
        "original_search_result": {"title": "Lose Yourself", "artist": "Eminem",
                                   "artists": [{"name": "Eminem"}],
                                   "album": {"name": "8 Mile OST",
                                             "artists": [{"name": "Eminem"}]}},
        "album": {"id": "wishlist_album", "name": "8 Mile OST", "total_tracks": 16,
                  "release_date": "2002", "artists": [{"name": "Eminem"}]},
        "track_info": {"_explicit_artist_context": {"id": "wishlist", "name": "Eminem", "genres": []},
                       "spotify_data": {"artists": [{"name": "Eminem"}]}},
        "source": "spotify",
    }
    md = _extract(context, {"id": "wishlist", "name": "Eminem", "genres": []})
    assert md["album_artist"] == "Eminem"
    assert md["album"] == "8 Mile OST"
    assert md["total_tracks"] == 16


# ---------------------------------------------------------------------------
# R4 M1 (accepted tradeoff, pinned): wire 1 cannot distinguish "a stranger
# from a poisoned batch" from "a curator/composer the provider legitimately
# credits" when there is no real provider album id — both look identical
# (an unshared name, no id). A Moana-style soundtrack whose batch context
# claims the composer as album artist, on a placeholder id, IS distrusted.
# That is the accepted cost of the heuristic: real releases carry real
# provider ids, which keep the credit trusted (see
# test_1567_real_album_id_keeps_artist_credit_trusted).
# ---------------------------------------------------------------------------


def test_1567_r4_composer_credited_release_without_real_id_distrusted():
    """M1 accepted tradeoff: a composer/curator-credited release (Moana-style)
    on a placeholder album id is distrusted — wire 1 cannot tell a legitimate
    curator credit from a poisoned batch stranger without a real id."""
    context = {
        "original_search_result": {
            "title": "How Far I'll Go",
            "artist": "Auli'i Cravalho",
            "artists": [{"name": "Auli'i Cravalho"}],
        },
        "album": {
            "id": "wishlist_album",  # placeholder: no real provider id
            "name": "Moana Soundtrack",
            "total_tracks": 14,
            "release_date": "2016",
            "artists": [{"name": "Lin-Manuel Miranda"}],  # composer credit
        },
        "track_info": {
            "_explicit_artist_context": {
                "id": "wishlist", "name": "Lin-Manuel Miranda", "genres": [],
            },
            "spotify_data": {
                "artists": [{"name": "Auli'i Cravalho"}],
                "album": {"name": "Moana Soundtrack"},
            },
        },
        "source": "spotify",
    }
    md = _extract(
        context,
        {"id": "wishlist", "name": "Lin-Manuel Miranda", "genres": []},
    )
    # Pinned: distrusted (accepted tradeoff — see comment above). The tags
    # fall back to the track's own data, loudly, instead of stamping the
    # uncorroborated curator credit.
    assert context["_batch_album_distrusted"] is True
    assert md["album_artist"] == "Auli'i Cravalho"
