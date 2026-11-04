"""MetaSync metadata depot — SoulSync's store for network answers
(core/metasync_depot.py, api/metasync_depot.py, the export bit in
api/metasync.py, the tagging consult in core/metadata/source.py).

The pinned contract (DESIGN-METADATA §1, §2.4, §5, §8, §9):

* network answers land in the depot ONLY — never in library records
  on arrival — as per-field candidates with tier, support bucket,
  verified_at and a 90-day bound;
* promotion into the library happens only above §8's floor, checked
  at promotion time: (a) strong-verified + fresh, (b) a local
  confirmation fetch passing an independent KEY check (name and
  duration agreement never count), or (c) a single survivor picked
  by a verified anchor. The support bucket alone NEVER promotes;
* a zero-support non-local value is treated as a network reflection
  and promotes only under leg (a);
* invalidation flags, never rewrites: retracted fields stop
  promoting at once, library values already written stay written;
* every promotion is marked, and the export emits the mark as
  network_fields only while the library still holds that value.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from api import create_api_blueprint, limiter
from core import metasync_depot as depot
from core.soulid_worker import generate_soul_id
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
RAW_KEY = "sk_metasync_test_key"


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "depot.db"))


def _value(value, tier="unverified", support="1-2", local=False,
           verified_at=None):
    entry = {"value": value, "tier": tier, "local": local,
             "support": support,
             "support_label": "1-2 nodes" if support == "1-2" else support}
    if verified_at is not None:
        entry["verified_at"] = verified_at
    return entry


def _field(values, local_values=None):
    return {"values": values, "local_value": None,
            "local_values": local_values or [], "disputed": False,
            "value": values[0]["value"] if values else None,
            "tier": values[0]["tier"] if values else "unverified",
            "local": False, "support": "1-2"}


def _record(subject, kind, fields, found=True, disambiguation=None,
            name=None):
    record = {"version": 1, "subject": subject, "kind": kind,
              "name": name or ["x"], "found": found, "fields": fields}
    if disambiguation is not None:
        record["disambiguation"] = disambiguation
    return record


def _strong(value, days_ago=1, support="none"):
    verified = (NOW - timedelta(days=days_ago)).timestamp()
    return _value(value, tier="strong-verified", support=support,
                  verified_at=verified)


# ── ingest ────────────────────────────────────────────────────────

def test_store_and_read_back_candidates(db):
    subject = "soul_track_a"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("USRC10000001", support="3+")]),
        "spotify_id": _field([_strong("sp123")]),
    }), now=NOW)
    rows = depot.get_fields(db, subject, now=NOW)
    by_field = {r["field"]: r for r in rows}
    assert by_field["isrc"]["value"] == "USRC10000001"
    assert by_field["isrc"]["support_bucket"] == "3+"
    assert by_field["spotify_id"]["effective_tier"] == "strong-verified"
    assert by_field["spotify_id"]["tier_lapses_at"] is not None


def test_store_rejects_garbage(db):
    assert depot.store_record(db, None) is False
    assert depot.store_record(db, {"subject": "nope", "kind": "track"}) is False
    assert depot.store_record(db, {"subject": "soul_x", "kind": "video"}) is False


def test_not_found_record_clears_candidates(db):
    subject = "soul_track_gone"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("USRC10000001")])}), now=NOW)
    assert depot.get_fields(db, subject, now=NOW)
    depot.store_record(db, _record(subject, "track", {}, found=False), now=NOW)
    assert depot.get_fields(db, subject, now=NOW) == []


def test_superseding_record_drops_dead_values(db):
    subject = "soul_track_s"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("OLD")])}), now=NOW)
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("NEW")])}), now=NOW)
    values = [r["value"] for r in depot.get_fields(db, subject, "isrc", now=NOW)]
    assert values == ["NEW"]


def test_ingest_cleans_control_characters(db):
    subject = "soul_track_dirty"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("US\x00RC1\x7f")])}), now=NOW)
    rows = depot.get_fields(db, subject, "isrc", now=NOW)
    assert rows[0]["value"] == "USRC1"


def test_local_values_are_stored_but_never_candidates(db):
    subject = "soul_track_l"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("NET")], local_values=["MINE"])}), now=NOW)
    ok, leg = depot.promotion_verdict(db, subject, "isrc", "MINE", now=NOW)
    assert (ok, leg) == (False, None)


def test_depot_is_bounded(db, monkeypatch):
    monkeypatch.setattr(depot, "MAX_ENTRIES", 2)
    for n in range(3):
        depot.store_record(db, _record(f"soul_track_{n}", "track", {
            "isrc": _field([_value(f"V{n}")])}),
            now=NOW + timedelta(minutes=n))
    assert depot.depot_status(db)["entries"] == 2


# ── the floor: leg (a) ────────────────────────────────────────────

def test_leg_a_strong_and_fresh_promotes_even_at_zero_support(db):
    subject = "soul_track_a1"
    depot.store_record(db, _record(subject, "track", {
        "spotify_id": _field([_strong("sp1", support="none")])}), now=NOW)
    assert depot.promotion_verdict(db, subject, "spotify_id", "sp1",
                                   now=NOW) == (True, "a")


def test_leg_a_stale_verdict_does_not_promote(db):
    subject = "soul_track_a2"
    depot.store_record(db, _record(subject, "track", {
        "spotify_id": _field([_strong("sp1", days_ago=91, support="3+")])}),
        now=NOW)
    ok, _ = depot.promotion_verdict(db, subject, "spotify_id", "sp1", now=NOW)
    assert ok is False


def test_support_bucket_alone_never_promotes(db):
    """§9: peer agreement orders display; it never makes library
    truth. A 3+ bucket with no verification, no confirmation fetch
    and no anchor is display-only."""
    subject = "soul_track_vote"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("USRC1", support="3+")])}), now=NOW)
    ok, _ = depot.promotion_verdict(db, subject, "isrc", "USRC1", now=NOW)
    assert ok is False


# ── the floor: leg (b) ────────────────────────────────────────────

def test_confirmation_key_check_keys_only():
    response = {"isrc": "USRC10000001", "title": "Totally Different",
                "duration_ms": 12345}
    assert depot.confirmation_key_check(response, {"isrc": "usrc-1000-0001"})
    # name + duration agreement is exactly the refused test
    assert not depot.confirmation_key_check(
        {"title": "Same Name", "duration_ms": 200000},
        {"isrc": "USRC10000001"})
    # an MBID key agrees
    assert depot.confirmation_key_check(
        {"recording_mbid": "MB-1"}, {"mbid": "mb-1"})
    # album + position agrees
    assert depot.confirmation_key_check(
        {"album_id": "alb9", "position": [1, 3]},
        {"album_id": "alb9", "position": [1, 3]})
    assert not depot.confirmation_key_check(
        {"album_id": "alb9", "position": [1, 4]},
        {"album_id": "alb9", "position": [1, 3]})


def test_confirmation_never_vouched_by_network_marked_local_key():
    """§8: an earlier promotion cannot confirm a later one."""
    response = {"isrc": "USRC10000001"}
    local = {"isrc": {"value": "USRC10000001", "network": True}}
    assert not depot.confirmation_key_check(response, local)


def test_leg_b_through_verdict(db):
    subject = "soul_track_b"
    depot.store_record(db, _record(subject, "track", {
        "deezer_id": _field([_value("555", support="1-2")])}), now=NOW)
    ok, leg = depot.promotion_verdict(
        db, subject, "deezer_id", "555", now=NOW,
        confirmation_response={"isrc": "USRC10000001"},
        confirmation_fetched_id="555",
        local_keys={"isrc": "USRC10000001"})
    assert (ok, leg) == (True, "b")
    # the same fetch agreeing only on the name promotes nothing
    ok, _ = depot.promotion_verdict(
        db, subject, "deezer_id", "555", now=NOW,
        confirmation_response={"title": "The Title"},
        local_keys={"isrc": "USRC10000001"})
    assert ok is False


def test_network_reflected_value_is_leg_a_only(db):
    """Zero standing support + non-local: §2.4's reflection rule —
    a confirmation fetch cannot rescue it, only the node's own
    strong verification can."""
    subject = "soul_track_r"
    depot.store_record(db, _record(subject, "track", {
        "deezer_id": _field([_value("555", support="none")])}), now=NOW)
    ok, _ = depot.promotion_verdict(
        db, subject, "deezer_id", "555", now=NOW,
        confirmation_response={"isrc": "USRC10000001"},
        local_keys={"isrc": "USRC10000001"})
    assert ok is False


# ── the floor: leg (c) ────────────────────────────────────────────

def _artist_with_settlement(db, settled="deezer:111", promotion=True,
                            via="fetched_link", days_ago=1, candidates=("111",)):
    subject = "soul_artist_c"
    fields = {"deezer_id": _field([_value(c, support="1-2")
                                   for c in candidates])}
    block = {"settled": settled, "via": via, "promotion": promotion,
             "survivors": [settled] if settled else [],
             "settled_at": (NOW - timedelta(days=days_ago)).timestamp()}
    depot.store_record(db, _record(subject, "artist", fields,
                                   disambiguation=block), now=NOW)
    return subject


def test_leg_c_fetched_link_settlement_promotes(db):
    subject = _artist_with_settlement(db)
    assert depot.promotion_verdict(db, subject, "deezer_id", "111",
                                   now=NOW) == (True, "c")


def test_leg_c_counting_settlement_is_display_only(db):
    subject = _artist_with_settlement(db, promotion=False,
                                      via="local_link_count")
    ok, _ = depot.promotion_verdict(db, subject, "deezer_id", "111", now=NOW)
    assert ok is False


def test_leg_c_two_survivors_promote_neither(db):
    subject = _artist_with_settlement(db, candidates=("111", "222"))
    ok, _ = depot.promotion_verdict(db, subject, "deezer_id", "111", now=NOW)
    assert ok is False


def test_leg_c_stale_settlement_promotes_nothing(db):
    subject = _artist_with_settlement(db, days_ago=120)
    ok, _ = depot.promotion_verdict(db, subject, "deezer_id", "111", now=NOW)
    assert ok is False


def test_leg_c_track_in_verified_tracklist(db):
    """Concerns §4.1: a settled, strong-verified tracklist slot
    proves WHICH track this is — it says nothing about whether the
    provider ID attached to it is right, and a lone answering node
    is automatically the single survivor. The slot anchor no
    longer promotes a provider ID, at the right slot or any
    other; the ID needs leg (a), (b), or a settlement."""
    album = "soul_album_c"
    track = "soul_track_c"
    variant = {"variants": {
        "mb:rel-1": {"fingerprint": "mb:rel-1", "settled": True,
                     "tracks": [{"track": track, "disc": 1, "position": 2,
                                 "tier": "strong-verified",
                                 "support": "1-2", "local": False}]},
    }, "local_value": None, "local_values": [], "disputed": False}
    depot.store_record(db, _record(album, "album",
                                   {"album_track": variant}), now=NOW)
    depot.store_record(db, _record(track, "track", {
        "spotify_id": _field([_value("sp9", support="1-2")])}), now=NOW)
    ok, leg = depot.promotion_verdict(
        db, track, "spotify_id", "sp9", now=NOW,
        album_subject=album, disc=1, position=2)
    assert (ok, leg) == (False, None)
    ok, _ = depot.promotion_verdict(
        db, track, "spotify_id", "sp9", now=NOW,
        album_subject=album, disc=1, position=3)
    assert ok is False


def test_leg_c_settlement_still_promotes_a_non_id_field(db):
    """§4.1 turned off only the slot anchor: leg (c)'s actual
    single-survivor disambiguation — a promotion-flagged, fresh
    settlement — still promotes a non-ID field it settled."""
    subject = "soul_album_year_c"
    block = {"settled": "year:1991", "via": "fetched_link",
             "promotion": True, "survivors": ["year:1991"],
             "settled_at": (NOW - timedelta(days=1)).timestamp()}
    depot.store_record(db, _record(subject, "album", {
        "year": _field([_value(1991, support="1-2")])},
        disambiguation=block), now=NOW)
    assert depot.promotion_verdict(db, subject, "year", 1991,
                                   now=NOW) == (True, "c")


def test_lone_claim_with_no_anchor_is_not_a_survivor(db):
    subject = "soul_track_lone"
    depot.store_record(db, _record(subject, "track", {
        "spotify_id": _field([_value("sp9", support="1-2")])}), now=NOW)
    ok, _ = depot.promotion_verdict(db, subject, "spotify_id", "sp9",
                                    now=NOW)
    assert ok is False


# ── invalidation: flags, never rewrites ───────────────────────────

def _seed_lib2(db, *, artist="Artist", album="Album", title="Song"):
    """A library v2 artist + album + track, seeded the way dev's
    own export tests seed the catalogue. Returns
    (artist_id, album_id, track_id) — lib2 integer ids."""
    conn = db._get_connection()
    artist_id = seed_artist(conn, server_id="ar1", name=artist)
    album_id = seed_album(conn, server_id="al1", title=album,
                          artist_id=artist_id)
    track_id = seed_track(conn, server_id="tr1", title=title,
                          album_id=album_id, artist_id=artist_id)
    conn.commit()
    return artist_id, album_id, track_id


def _mark_promoted_track(db):
    """The depot-side state of a track whose spotify id was
    depot-promoted: the §2.4 mark. The library side of that
    promotion lives in the export rows the tests build, which is
    where network_fields_for_export reads it from."""
    subject = generate_soul_id("track", "Artist", "Song")
    depot.record_mark(db, "track", subject, "spotify_id", "sp1")
    return subject


def test_field_invalidation_stops_promotion(db):
    subject = "soul_track_i"
    depot.store_record(db, _record(subject, "track", {
        "spotify_id": _field([_strong("sp1")])}), now=NOW)
    assert depot.promotion_verdict(db, subject, "spotify_id", "sp1",
                                   now=NOW)[0]
    result = depot.apply_invalidation(db, {
        "kind": "fields",
        "items": [{"subject": subject, "field": "spotify_id",
                   "value": "sp1"}],
        "reason": "revocation"})
    assert result == {"status": "ok", "retracted_fields": 1}
    assert depot.promotion_verdict(db, subject, "spotify_id", "sp1",
                                   now=NOW) == (False, None)


def test_subject_invalidation_and_status(db):
    subject = "soul_track_i2"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("X")])}), now=NOW)
    depot.apply_invalidation(db, {"kind": "subjects",
                                  "subjects": [subject],
                                  "reason": "revocation"})
    assert depot.get_fields(db, subject, now=NOW) == []
    assert depot.depot_status(db)["invalidated_entries"] == 1


def test_invalidation_never_rewrites_library_values(db):
    subject = generate_soul_id("track", "Artist", "Song")
    _artist_id, _album_id, track_id = _seed_lib2(db)
    conn = db._get_connection()
    conn.execute(
        "UPDATE lib2_tracks SET soul_id = ?, spotify_id = 'sp1' "
        "WHERE id = ?", (subject, track_id))
    conn.commit()
    depot.record_mark(db, "track", subject, "spotify_id", "sp1")
    depot.apply_invalidation(db, {"kind": "subjects",
                                  "subjects": [subject],
                                  "reason": "revocation"})
    row = conn.execute(
        "SELECT spotify_id FROM lib2_tracks WHERE id = ?",
        (track_id,)).fetchone()
    assert row[0] == "sp1"  # flagged in the depot, untouched in the library


def test_update_push_stores_records(db):
    result = depot.apply_update_push(db, {"records": [
        _record("soul_track_u", "track",
                {"isrc": _field([_value("U1")])})]}, now=NOW)
    assert result == {"status": "ok", "stored": 1}
    assert depot.get_fields(db, "soul_track_u", now=NOW)


# ── reconcile + collect (node client, monkeypatched) ──────────────

def test_reconcile_drops_what_the_node_no_longer_holds(db, monkeypatch):
    live = "soul_track_live"
    dead = "soul_track_dead"
    for subject in (live, dead):
        depot.store_record(db, _record(subject, "track", {
            "isrc": _field([_value("X")])}), now=NOW)
    monkeypatch.setattr(
        depot, "fetch_node_record",
        lambda cfg, subject: _record(live, "track", {
            "isrc": _field([_value("X")])}) if subject == live else None)
    result = depot.reconcile(db, lambda k, d=None: "x", now=NOW)
    assert result["refreshed"] == 1 and result["dropped"] == 1
    assert depot.get_fields(db, dead, now=NOW) == []
    assert depot.get_fields(db, live, now=NOW)


def test_collect_subjects_deposits_answers(db, monkeypatch):
    monkeypatch.setattr(
        depot, "query_node",
        lambda cfg, kind, subjects: [
            _record(s, kind, {"isrc": _field([_value("C1")])})
            for s in subjects])
    result = depot.collect_subjects(
        db, lambda k, d=None: "x", {"track": ["soul_track_c1"]}, now=NOW)
    assert result["stored"] == 1
    assert depot.get_fields(db, "soul_track_c1", now=NOW)


# ── marks and the export bit ──────────────────────────────────────

def test_export_bit_follows_the_marked_value(db):
    subject = _mark_promoted_track(db)
    # the export row is the row being served (id + its own values),
    # which is how api/metasync.py calls this
    assert depot.network_fields_for_export(
        db, "track", {"id": "t1", "soul_id": subject,
                      "spotify_track_id": "sp1"}) == ["spotify_id"]
    # the value is replaced by the user's own matching (the served
    # row now carries their value): the mark stops applying instead
    # of mislabelling fresh data
    assert depot.network_fields_for_export(
        db, "track", {"id": "t1", "soul_id": subject,
                      "spotify_track_id": "sp_other"}) == []


# ── the tagging consult (core/metadata/source.py) ─────────────────

def _pp():
    return {"id_tags": {}, "recording_mbid": None, "artist_mbid": None,
            "release_mbid": "", "mb_genres": [], "isrc": None,
            "release_year": None, "depot_genres": []}


class _Cfg:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)


def test_tagging_consult_promotes_above_floor_only(db):
    from core.metadata.source import _process_metasync_depot_source

    track_sid = generate_soul_id("track", "The Artist", "The Song")
    album_sid = generate_soul_id("album", "The Artist", "The Album")
    depot.store_record(db, _record(track_sid, "track", {
        "spotify_id": _field([_strong("sp_strong")]),
        "deezer_id": _field([_value("dz_weak", support="none")]),
    }), now=NOW)
    depot.store_record(db, _record(album_sid, "album", {
        "year": _field([_strong(1991, support="1-2")])}), now=NOW)

    pp = _pp()
    metadata = {"album": "The Album", "track_number": 1, "disc_number": 1}
    _process_metasync_depot_source(
        pp, metadata, _Cfg(), None, "The Song", "The Artist",
        context=None, db=db)
    assert pp["id_tags"].get("SPOTIFY_TRACK_ID") == "sp_strong"
    assert "DEEZER_TRACK_ID" not in pp["id_tags"]  # below the floor
    assert pp["release_year"] == "1991"
    marks = {(m[0], m[2]) for m in pp["_depot"]["marks"]}
    assert ("track", "spotify_id") in marks
    assert ("album", "year") in marks
    assert ("track", "deezer_id") not in marks


def test_tagging_consult_disabled_by_config(db):
    from core.metadata.source import _process_metasync_depot_source

    track_sid = generate_soul_id("track", "The Artist", "The Song")
    depot.store_record(db, _record(track_sid, "track", {
        "spotify_id": _field([_strong("sp_strong")])}), now=NOW)
    pp = _pp()
    _process_metasync_depot_source(
        pp, {"album": "The Album"}, _Cfg({"metasync.depot_enabled": False}),
        None, "The Song", "The Artist", context=None, db=db)
    assert pp["id_tags"] == {}


# ── API surface ───────────────────────────────────────────────────

class _Config:
    def __init__(self):
        self._keys = [{
            "id": "k1", "label": "test",
            "key_hash": hashlib.sha256(RAW_KEY.encode()).hexdigest(),
            "key_prefix": RAW_KEY[:11],
        }]

    def get(self, key, default=None):
        return self._keys if key == "api_keys" else default

    def set(self, *a, **kw):
        return None

    def save_config(self, *a, **kw):
        return None


@pytest.fixture
def client(db, monkeypatch):
    import database.music_database as mdb
    monkeypatch.setattr(mdb, "get_database", lambda: db)
    import api.metasync as ms
    monkeypatch.setattr(ms, "get_database", lambda: db)
    import api.metasync_depot as msd
    monkeypatch.setattr(msd, "get_database", lambda: db)

    app = Flask(__name__)
    app.config["RATELIMIT_ENABLED"] = False
    limiter.init_app(app)
    app.register_blueprint(create_api_blueprint(), url_prefix="/api/v1")
    app.soulsync = {"config_manager": _Config()}
    return app.test_client()


def _auth():
    return {"Authorization": f"Bearer {RAW_KEY}"}


def test_push_routes_require_the_api_key(client):
    resp = client.post("/api/v1/metasync/depot/invalidate",
                       json={"kind": "subjects", "subjects": []})
    assert resp.status_code in (401, 403)


def test_push_invalidate_and_records_roundtrip(client, db):
    subject = "soul_track_api"
    resp = client.post("/api/v1/metasync/depot/records", headers=_auth(),
                       json={"records": [_record(subject, "track", {
                           "spotify_id": _field([_strong("sp1")])})]})
    assert resp.status_code == 200
    assert depot.promotion_verdict(db, subject, "spotify_id", "sp1",
                                   now=NOW)[0]
    resp = client.post("/api/v1/metasync/depot/invalidate", headers=_auth(),
                       json={"kind": "fields",
                             "items": [{"subject": subject,
                                        "field": "spotify_id",
                                        "value": "sp1"}],
                             "reason": "revocation"})
    assert resp.status_code == 200
    assert depot.promotion_verdict(db, subject, "spotify_id", "sp1",
                                   now=NOW) == (False, None)


def test_export_carries_network_fields_and_release_group_id(client, db):
    subject = _mark_promoted_track(db)
    conn = db._get_connection()
    artist_id, album_id, track_id = _seed_lib2(db)
    conn.execute(
        "UPDATE lib2_tracks SET soul_id = ?, spotify_id = 'sp1' "
        "WHERE id = ?", (subject, track_id))
    conn.execute(
        "UPDATE lib2_albums SET soul_id = 'soul_album_x', "
        "musicbrainz_release_group_id = 'rg-1' WHERE id = ?",
        (album_id,))
    conn.commit()
    resp = client.get("/api/v1/metasync/export?entity=track",
                      headers=_auth())
    assert resp.status_code == 200
    items = resp.get_json()["data"]["items"]
    mine = [i for i in items if i["soul_id"] == subject]
    assert mine and mine[0]["network_fields"] == ["spotify_id"]
    resp = client.get("/api/v1/metasync/export?entity=album",
                      headers=_auth())
    albums = resp.get_json()["data"]["items"]
    mine = [i for i in albums if i["soul_id"] == "soul_album_x"]
    assert mine and mine[0]["musicbrainz_release_group_id"] == "rg-1"
    assert mine[0]["network_fields"] == []


# ══ fold round — the hostile reviews' findings, pinned ═══════════

# ── MF: node timestamps are clamped at receipt ────────────────────

def test_future_verified_at_is_clamped_to_receipt(db):
    """A verified_at in the year 2100 behaves exactly like 'verified
    just now': it promotes today, and its tier lapses 90 days after
    RECEIPT — not ~75 years from now. Without the clamp the lapse
    lands in 2100 too and leg (a)'s age test goes negative and
    passes forever."""
    subject = "soul_track_future"
    future = datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp()
    depot.store_record(db, _record(subject, "track", {
        "spotify_id": _field([_value("spF", tier="strong-verified",
                                     support="none",
                                     verified_at=future)])}), now=NOW)
    row = depot.get_fields(db, subject, "spotify_id", now=NOW)[0]
    assert datetime.fromisoformat(row["verified_at"]) == NOW
    assert datetime.fromisoformat(row["tier_lapses_at"]) == (
        NOW + timedelta(days=90))
    assert depot.promotion_verdict(db, subject, "spotify_id", "spF",
                                   now=NOW) == (True, "a")
    # 91 days after receipt the verdict is history: the tier has
    # lapsed on the stored row, and the floor no longer passes.
    assert depot.effective_tier(row, NOW + timedelta(days=91)) == \
        "unverified"
    ok, _ = depot.promotion_verdict(db, subject, "spotify_id", "spF",
                                    now=NOW + timedelta(days=91))
    assert ok is False


def test_future_settled_at_is_clamped_to_receipt(db):
    subject = "soul_artist_future"
    future = datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp()
    block = {"settled": "deezer:111", "via": "fetched_link",
             "promotion": True, "survivors": ["deezer:111"],
             "settled_at": future}
    depot.store_record(db, _record(subject, "artist", {
        "deezer_id": _field([_value("111", support="1-2")])},
        disambiguation=block), now=NOW)
    stored = depot.get_disambiguation(db, subject)
    assert datetime.fromisoformat(stored["settled_at"]) == NOW
    assert depot.promotion_verdict(db, subject, "deezer_id", "111",
                                   now=NOW) == (True, "c")
    ok, _ = depot.promotion_verdict(db, subject, "deezer_id", "111",
                                    now=NOW + timedelta(days=91))
    assert ok is False


# ── MF: leg (b) — real client shape, no crashes, no stranded marks ─

def test_confirmation_key_check_never_raises_on_malformed_position():
    """A response whose slot is missing or non-numeric (the shape a
    misread client key produces) simply does not pass the check."""
    local = {"album_id": "alb9", "position": [1, 3]}
    for bad in ([1, None], [None, 3], ["x", 3], [1, "three"]):
        assert not depot.confirmation_key_check(
            {"album_id": "alb9", "position": bad}, local)
    assert not depot.confirmation_key_check(
        {"album_id": "alb9", "position": [1, 3]},
        {"album_id": "alb9", "position": [1, None]})
    assert depot.confirmation_key_check(
        {"album_id": "alb9", "position": [1, 3]}, local)


def test_leg_c_anchor_tolerates_non_numeric_slot(db):
    """A variant slot whose disc/position arrived as junk text
    must not raise into tagging — and with the slot anchor off
    (§4.1) the verdict refuses regardless."""
    album = "soul_album_junk"
    track = "soul_track_junk"
    variant = {"variants": {
        "mb:rel-1": {"fingerprint": "mb:rel-1", "settled": True,
                     "tracks": [{"track": track, "disc": "one",
                                 "position": "two",
                                 "tier": "strong-verified",
                                 "support": "1-2", "local": False}]},
    }, "local_value": None, "local_values": [], "disputed": False}
    depot.store_record(db, _record(album, "album",
                                   {"album_track": variant}), now=NOW)
    depot.store_record(db, _record(track, "track", {
        "spotify_id": _field([_value("sp9", support="1-2")])}), now=NOW)
    ok, leg = depot.promotion_verdict(
        db, track, "spotify_id", "sp9", now=NOW,
        album_subject=album, disc=1, position=2)
    assert (ok, leg) == (False, None)


class _FakeDeezerClient:
    def __init__(self, details):
        self._details = details
        self.calls = []

    def get_track_details(self, track_id):
        self.calls.append(track_id)
        return self._details


def _deezer_details(track_position=3):
    """The REAL enhanced shape, built by the client's own builder
    from a raw API payload — if the client's keys ever change, this
    shape changes with them and the seam tests below go red."""
    from types import SimpleNamespace

    from core.deezer_client import DeezerClient
    raw = {"id": 555, "title": "The Song",
           "track_position": track_position, "disk_number": 1,
           "duration": 200, "isrc": "",
           "artist": {"name": "The Artist"},
           "album": {"id": 999, "title": "The Album", "nb_tracks": 10,
                     "release_date": "1991-01-01"}}
    details = DeezerClient._build_enhanced_track(SimpleNamespace(), raw)
    assert details["track_number"] == track_position
    assert details["disc_number"] == 1
    return details


def _leg_b_setup(db, track_position=3):
    from types import SimpleNamespace

    from core.metadata.source import _process_metasync_depot_source
    track_sid = generate_soul_id("track", "The Artist", "The Song")
    depot.store_record(db, _record(track_sid, "track", {
        "deezer_id": _field([_value("555", support="1-2")])}), now=NOW)
    client = _FakeDeezerClient(_deezer_details(track_position))
    runtime = SimpleNamespace(
        deezer_worker=SimpleNamespace(client=client))
    pp = _pp()
    metadata = {"album": "The Album", "track_number": 3,
                "disc_number": 1, "source": "deezer",
                "source_album_id": "999"}
    _process_metasync_depot_source(
        pp, metadata, _Cfg(), runtime, "The Song", "The Artist",
        context=None, db=db)
    return pp, client


def test_tagging_leg_b_confirmation_uses_the_real_client_shape(db):
    """The album+position sub-leg carries this confirmation: the
    response ISRC is empty, so only the position can pass it. With
    the raw-API key names read by mistake the position came out
    [disc, None] and this path could never pass (and the check
    raised on the None)."""
    pp, client = _leg_b_setup(db)
    assert client.calls == ["555"]
    assert pp["id_tags"].get("DEEZER_TRACK_ID") == "555"
    assert pp["_depot_deezer_id"] == "555"
    marks = {(m[0], m[2], m[3]) for m in pp["_depot"]["marks"]}
    assert ("track", "deezer_id", "555") in marks


def test_tagging_leg_b_wrong_position_promotes_nothing(db):
    pp, client = _leg_b_setup(db, track_position=4)
    assert client.calls == ["555"]  # the fetch was spent
    assert "DEEZER_TRACK_ID" not in pp["id_tags"]
    assert "_depot" not in pp  # nothing promoted, nothing published


def test_depot_marks_survive_a_later_consult_exception(db, monkeypatch):
    """pp['_depot'] is published at the FIRST fill. A verdict that
    blows up on a later field (the caller catches the exception)
    must not strand the fills already applied — promoted values in
    tags with no mark is the laundering the mark exists to stop."""
    from core.metadata.source import _process_metasync_depot_source

    track_sid = generate_soul_id("track", "The Artist", "The Song")
    album_sid = generate_soul_id("album", "The Artist", "The Album")
    depot.store_record(db, _record(track_sid, "track", {
        "spotify_id": _field([_strong("sp_strong")])}), now=NOW)
    depot.store_record(db, _record(album_sid, "album", {
        "year": _field([_strong(1991, support="1-2")])}), now=NOW)
    original = depot.promotion_verdict

    def exploding(db_, subject, field, value, **kw):
        if field == "year":
            raise RuntimeError("boom")
        return original(db_, subject, field, value, **kw)

    monkeypatch.setattr(depot, "promotion_verdict", exploding)
    pp = _pp()
    with pytest.raises(RuntimeError):
        _process_metasync_depot_source(
            pp, {"album": "The Album", "track_number": 1,
                 "disc_number": 1},
            _Cfg(), None, "The Song", "The Artist",
            context=None, db=db)
    assert pp["id_tags"].get("SPOTIFY_TRACK_ID") == "sp_strong"
    marks = {(m[0], m[2]) for m in pp["_depot"]["marks"]}
    assert ("track", "spotify_id") in marks


# ── MF/SF: embed_source_ids marks — steered year, per-mark isolation

def _run_embed(db, monkeypatch, enrich=None):
    """Drive embed_source_ids with the world stubbed down to the
    depot consult + one source slot, so the §2.4 marks block runs
    against the real pp the consult produced."""
    import core.metadata.source as source_mod

    monkeypatch.setattr(source_mod, "get_database", lambda: db)
    monkeypatch.setattr(
        source_mod, "get_config_manager",
        lambda: _Cfg({"metadata_enhancement.post_process_order":
                      ["deezer"]}))
    monkeypatch.setattr(source_mod, "get_mutagen_symbols",
                        lambda: object())
    monkeypatch.setattr(source_mod, "_collect_source_ids",
                        lambda metadata, cfg: {})
    monkeypatch.setattr(source_mod, "_write_embedded_metadata",
                        lambda *a, **kw: None)
    monkeypatch.setattr(source_mod, "_process_source_enrichment",
                        enrich or (lambda *a, **kw: None))
    metadata = {"title": "The Song", "artist": "The Artist",
                "album_artist": "The Artist", "album": "The Album",
                "track_number": 1, "disc_number": 1}
    source_mod.embed_source_ids(object(), metadata, context={},
                                runtime=None)


def test_steered_deezer_year_is_marked(db, monkeypatch):
    """The steered fetch fills release_year off the promoted ID's
    album. Year is an export claim field: unmarked, it would land
    in tags and the DB as the user's own data (§2.4)."""
    track_sid = generate_soul_id("track", "The Artist", "The Song")
    album_sid = generate_soul_id("album", "The Artist", "The Album")
    depot.store_record(db, _record(track_sid, "track", {
        "deezer_id": _field([_strong("555")])}), now=NOW)
    _artist_id, album_id, _track_id = _seed_lib2(
        db, artist="The Artist", album="The Album", title="The Song")
    conn = db._get_connection()
    conn.execute(
        "UPDATE lib2_albums SET soul_id = ?, year = 1991 WHERE id = ?",
        (album_sid, album_id))
    conn.commit()

    def enrich(source_name, pp, metadata, cfg, runtime, track_title,
               artist_name, provenance=None, audio_file=None,
               track_artist_name=None):
        if source_name == "deezer" and \
                (provenance or {}).get("track_id") == "555":
            pp["release_year"] = "1991"  # the steered fetch's fill

    _run_embed(db, monkeypatch, enrich=enrich)
    assert "year" in depot.marks_for(db, "album", album_sid)
    assert depot.network_fields_for_export(
        db, "album", {"id": album_id, "soul_id": album_sid,
                      "year": 1991}) == ["year"]


def test_one_bad_mark_does_not_drop_the_rest(db, monkeypatch):
    track_sid = generate_soul_id("track", "The Artist", "The Song")
    depot.store_record(db, _record(track_sid, "track", {
        "spotify_id": _field([_strong("spS")]),
        "deezer_id": _field([_strong("555")])}), now=NOW)
    original = depot.record_mark

    def flaky(db_, kind, soul_id, field, value, via="promoted",
              conn=None):
        if field == "spotify_id":
            raise RuntimeError("mark store hiccup")
        return original(db_, kind, soul_id, field, value, via=via,
                        conn=conn)

    monkeypatch.setattr(depot, "record_mark", flaky)
    _run_embed(db, monkeypatch)
    marks = depot.marks_for(db, "track", track_sid)
    assert "deezer_id" in marks
    assert "spotify_id" not in marks


# ── SF: export bit — merged genre lists, cosmetic ID changes ──────

def test_export_genres_bit_survives_a_merged_library_list(db):
    """Tagging stores ONE merged list (depot genres + enrichment);
    marks hold only the depot subset. The bit fires while any
    marked genre is still in the library's list — whole-list
    equality almost never held, so genres exported as own data."""
    subject = generate_soul_id("artist", "Artist")
    depot.record_mark(db, "artist", subject, "genres", ["Rock"])
    assert depot.network_fields_for_export(
        db, "artist", {"id": "ar1", "soul_id": subject,
                       "genres": ["Rock", "Local Addition"]}) == ["genres"]
    # the last marked genre leaves the served list: the bit goes
    # with it
    assert depot.network_fields_for_export(
        db, "artist", {"id": "ar1", "soul_id": subject,
                       "genres": ["Local Addition"]}) == []


def test_export_bit_survives_recasing_of_an_id(db):
    """A later writer's cosmetic re-case/trim of the same promoted
    ID must not drop the network bit while the value is still the
    promoted one."""
    subject = _mark_promoted_track(db)
    assert depot.network_fields_for_export(
        db, "track", {"id": "t1", "soul_id": subject,
                      "spotify_track_id": " SP1 "}) == ["spotify_id"]


# ── SF: ingest bounds + stored artifacts are cleaned ──────────────

def test_ingest_bounds_field_rows(db):
    """The entry cap means nothing if one record writes unbounded
    rows: candidate lists, local values, variant counts and track
    lists are all capped, and fields outside the vocabulary the
    depot has a floor for are not stored at all."""
    subject = "soul_track_flood"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field(
            [_value(f"V{i}") for i in range(
                depot.MAX_VALUES_PER_FIELD + 10)],
            local_values=[f"L{i}" for i in range(
                depot.MAX_LOCAL_VALUES_PER_FIELD + 5)]),
        "mystery_field": _field([_value("X")]),
    }), now=NOW)
    rows = depot.get_fields(db, subject, now=NOW)
    network = [r for r in rows if r["field"] == "isrc"
               and not r["is_local"]]
    local = [r for r in rows if r["is_local"]]
    assert len(network) == depot.MAX_VALUES_PER_FIELD
    assert len(local) == depot.MAX_LOCAL_VALUES_PER_FIELD
    assert not [r for r in rows if r["field"] == "mystery_field"]

    album = "soul_album_flood"
    variants = {
        f"v{i}": {"fingerprint": f"v{i}", "settled": False,
                  "tracks": [{"track": "soul_t", "disc": 1,
                              "position": n, "tier": "unverified",
                              "support": "none", "local": False}
                             for n in range(
                                 depot.MAX_TRACKS_PER_VARIANT + 10)]}
        for i in range(depot.MAX_VARIANTS + 5)}
    entry = {"variants": variants, "local_value": None,
             "local_values": [], "disputed": False}
    depot.store_record(db, _record(album, "album",
                                   {"album_track": entry}), now=NOW)
    rows = depot.get_fields(db, album, "album_track", now=NOW)
    assert len(rows) == depot.MAX_VARIANTS
    assert len(rows[0]["value"]["tracks"]) == \
        depot.MAX_TRACKS_PER_VARIANT


def test_ingest_cleans_support_label_disambiguation_and_record(db):
    subject = "soul_track_clean2"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("USRC1")])}, disambiguation={
            "settled": "deezer:111\x00", "via": "fetched_link",
            "promotion": True, "survivors": [],
            "settled_at": NOW.timestamp()}), now=NOW)
    conn = db._get_connection()
    label = conn.execute(
        "SELECT support_label FROM metasync_depot_fields "
        "WHERE subject = ?", (subject,)).fetchone()[0]
    assert "\x00" not in (label or "")
    block = depot.get_disambiguation(db, subject)
    assert block["settled"] == "deezer:111"
    raw = conn.execute(
        "SELECT record_json FROM metasync_depot_entries "
        "WHERE subject = ?", (subject,)).fetchone()[0]
    assert "\x00" not in raw


# ── SF: node-call circuit breaker + reconcile bounds ──────────────

def _node_cfg(key, default=None):
    return {"metasync.node_url": "http://node:8790",
            "metasync.local_api_key": "mn_key"}.get(key, default)


def test_node_circuit_breaker_skips_calls_after_failure(db, monkeypatch):
    """A hung node must not re-tax every file in a batch import:
    after a transport failure, asks raise from the cooldown memo
    without leaving the process. Depot-local answers never gated."""
    calls = []

    def failing_post(*a, **kw):
        calls.append(1)
        raise depot.requests.ConnectionError("down")

    monkeypatch.setattr(depot.requests, "post", failing_post)
    with pytest.raises(depot.NodeUnreachable):
        depot.query_node(_node_cfg, "track", ["soul_x"])
    with pytest.raises(depot.NodeUnreachable):
        depot.query_node(_node_cfg, "track", ["soul_x"])
    assert len(calls) == 1  # the second ask never went out

    subject = "soul_track_local"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("L1")])}), now=NOW)
    assert depot.get_fields(db, subject, now=NOW)  # local still serves

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "records": []}

    monkeypatch.setattr(depot.requests, "post",
                        lambda *a, **kw: _Resp())
    depot._node_failure_until.clear()
    assert depot.query_node(_node_cfg, "track", ["soul_x"]) == []


def test_reconcile_ignores_invalidated_entries(db, monkeypatch):
    """Confirmed-dead entries stay OUT of the rotation: past 500 of
    them, the old oldest-first window never reached a live subject
    again — the mechanism that catches lost pushes went dark."""
    dead = [f"soul_track_dead_{i}" for i in range(501)]
    for subject in dead:
        depot.store_record(db, _record(subject, "track", {
            "isrc": _field([_value("X")])}), now=NOW)
    depot.apply_invalidation(db, {"kind": "subjects",
                                  "subjects": dead,
                                  "reason": "revocation"}, now=NOW)
    live = "soul_track_live"
    depot.store_record(db, _record(live, "track", {
        "isrc": _field([_value("X")])}),
        now=NOW + timedelta(minutes=1))
    calls = []

    def fake_fetch(cfg, subject):
        calls.append(subject)
        return _record(live, "track", {"isrc": _field([_value("X")])})

    monkeypatch.setattr(depot, "fetch_node_record", fake_fetch)
    result = depot.reconcile(db, _node_cfg, now=NOW)
    assert calls == [live]
    assert result["checked"] == 1 and result["refreshed"] == 1
    assert result["budget_exhausted"] is False


def test_reconcile_time_budget(db, monkeypatch):
    """A UI-triggered run against a slow node stops at its
    wall-clock budget and says so, instead of spinning per-subject
    timeouts for over an hour."""
    import time

    subjects = [f"soul_track_slow_{i}" for i in range(3)]
    for subject in subjects:
        depot.store_record(db, _record(subject, "track", {
            "isrc": _field([_value("X")])}), now=NOW)

    def slow_fetch(cfg, subject):
        time.sleep(0.25)
        return _record(subject, "track",
                       {"isrc": _field([_value("X")])})

    monkeypatch.setattr(depot, "fetch_node_record", slow_fetch)
    result = depot.reconcile(db, _node_cfg, time_budget_seconds=0.1,
                             now=NOW)
    assert result["budget_exhausted"] is True
    assert result["checked"] == 1


def test_fetch_node_record_quotes_the_subject(db, monkeypatch):
    urls = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"subject": "soul_x", "kind": "track", "found": True}

    def fake_get(url, **kw):
        urls.append(url)
        return _Resp()

    monkeypatch.setattr(depot.requests, "get", fake_get)
    cfg = lambda k, d=None: {  # noqa: E731 — a different node than the
        "metasync.node_url": "http://node2:8790",  # breaker test's, so
        "metasync.local_api_key": "mn_key"}.get(k, d)  # no cooldown leaks
    depot.fetch_node_record(cfg, "soul_a/b?c#d")
    assert urls[0].endswith("/local/meta/record/soul_a%2Fb%3Fc%23d")


def test_fetch_node_record_unwraps_the_nodes_envelope(db, monkeypatch):
    """the node answers /local/meta/record as {"ok": true, "record":
    {...}}. handing the envelope to store_record rejected every
    subject, so a real reconcile refreshed and dropped nothing."""
    record = {"subject": "soul_x", "kind": "track", "found": True, "fields": {}}

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "record": record}

    monkeypatch.setattr(depot.requests, "get", lambda url, **kw: _Resp())
    cfg = lambda k, d=None: {  # noqa: E731
        "metasync.node_url": "http://node3:8790",
        "metasync.local_api_key": "mn_key"}.get(k, d)
    assert depot.fetch_node_record(cfg, "soul_x") == record
    assert depot.store_record(db, depot.fetch_node_record(cfg, "soul_x")) is True


def test_depot_reconcile_route_is_admin_only():
    import inspect

    from api import metasync_depot as route

    src = inspect.getsource(route)
    head = src.split("def post_metasync_depot_reconcile")[0].rsplit("@bp.route", 1)[1]
    assert "@admin_only" in head


# ── minor: the seam's SoulIDs are the worker's SoulIDs ────────────

def test_tagging_soul_ids_match_the_workers(db):
    """Marks are keyed by SoulIDs computed at tagging time from the
    pipeline's matched names; the export looks them up by the
    soul_id the WORKER minted into the library row from the
    library's own strings. Pin that the two agree for the shapes
    the seam marks — including a featuring-artist title, where the
    seam's name carries the suffix the library's may not (the
    normaliser strips parentheticals on both sides)."""
    from core.soulid_worker import SoulIDWorker

    conn = db._get_connection()
    ar1 = seed_artist(conn, server_id="ar1", name="The Artist")
    a1 = seed_album(conn, server_id="a1", title="The Album",
                    artist_id=ar1)
    t1 = seed_track(conn, server_id="t1", title="The Song",
                    album_id=a1, artist_id=ar1,
                    file_path="/music/the-song.flac")
    t2 = seed_track(conn, server_id="t2",
                    title="The Song (feat. Guest)",
                    album_id=a1, artist_id=ar1,
                    file_path="/music/the-song-feat.flac")
    conn.commit()

    worker = SoulIDWorker.__new__(SoulIDWorker)
    worker.db = db
    worker.batch_size = 100
    worker.should_stop = False
    worker.current_item = None
    worker.stats = {"artists_processed": 0, "albums_processed": 0,
                    "tracks_processed": 0, "errors": 0, "pending": 0}
    worker._process_artists()
    worker._process_albums()
    worker._process_tracks()

    stored = {}
    for table, row_id, key in (("lib2_artists", ar1, "ar1"),
                               ("lib2_albums", a1, "a1"),
                               ("lib2_tracks", t1, "t1"),
                               ("lib2_tracks", t2, "t2")):
        stored[key] = conn.execute(
            f"SELECT soul_id FROM {table} WHERE id = ?",
            (row_id,)).fetchone()[0]
    # the seam's computation, from the tagging pipeline's names
    assert stored["ar1"] == generate_soul_id("artist", "The Artist")
    assert stored["a1"] == generate_soul_id(
        "album", "The Artist", "The Album")
    assert stored["t1"] == generate_soul_id(
        "track", "The Artist", "The Song")
    # the featuring case: the seam's title carries the suffix, the
    # library row t1 does not — same ID either way, and the row
    # that kept the suffix (t2) mints that same song ID too.
    assert generate_soul_id(
        "track", "The Artist", "The Song (feat. Guest)") == stored["t1"]
    assert stored["t2"] == stored["t1"]


# ══ gate fold — the Claude gate's findings, pinned ═══════════════

# ── MF-1: the export bit is checked against THE exported row ────

def test_export_bit_is_checked_against_the_exported_row(db):
    """Soul ids are NOT unique — a track's is artist+title, shared
    by the single and the album version on purpose. Two rows share
    this soul_id; only one holds the promoted ISRC. Checking the
    mark against an arbitrary same-soul_id row (the old
    fetchone-by-soul_id) lets the user's own row shadow the
    promoted one, and the network ISRC exports unmarked."""
    subject = generate_soul_id("track", "Artist", "Song")
    depot.record_mark(db, "track", subject, "isrc", "NETISRC")
    own_row = {"id": "t_own", "soul_id": subject, "isrc": "OWNISRC"}
    net_row = {"id": "t_net", "soul_id": subject, "isrc": "NETISRC"}
    assert depot.network_fields_for_export(db, "track", net_row) == ["isrc"]
    assert depot.network_fields_for_export(db, "track", own_row) == []
    # a row that does not carry the value gets no bit — the export
    # walk always serves the value columns, so there is nothing to
    # resolve a value FROM, and guessing (by id, by soul_id) is
    # exactly the shadowing this test exists to forbid
    assert depot.network_fields_for_export(
        db, "track", {"id": "t_net", "soul_id": subject}) == []
    assert depot.network_fields_for_export(
        db, "track", {"id": "t_own", "soul_id": subject}) == []


def test_export_route_bits_are_per_row(client, db):
    subject = generate_soul_id("track", "Artist", "Song")
    conn = db._get_connection()
    artist_id = seed_artist(conn, server_id="ar1", name="Artist")
    album_id = seed_album(conn, server_id="al1", title="Album",
                          artist_id=artist_id)
    own_id = seed_track(conn, server_id="t_own", title="Song",
                        album_id=album_id, artist_id=artist_id)
    net_id = seed_track(conn, server_id="t_net", title="Song",
                        album_id=album_id, artist_id=artist_id)
    conn.execute(
        "UPDATE lib2_tracks SET soul_id = ?, isrc = 'OWNISRC' "
        "WHERE id = ?", (subject, own_id))
    conn.execute(
        "UPDATE lib2_tracks SET soul_id = ?, isrc = 'NETISRC' "
        "WHERE id = ?", (subject, net_id))
    conn.commit()
    depot.record_mark(db, "track", subject, "isrc", "NETISRC")
    resp = client.get("/api/v1/metasync/export?entity=track",
                      headers=_auth())
    assert resp.status_code == 200
    items = [i for i in resp.get_json()["data"]["items"]
             if i["soul_id"] == subject]
    assert len(items) == 2
    by_isrc = {i["isrc"]: i for i in items}
    assert by_isrc["NETISRC"]["network_fields"] == ["isrc"]
    assert by_isrc["OWNISRC"]["network_fields"] == []


# ── nit: a mapped column the table does not carry fails closed ──

def test_export_bit_fails_closed_for_a_column_the_table_lacks(db):
    """The export never serves a discogs_id on track rows (Discogs
    enrichment is artist/album level), but a mark can still exist
    for the field. The bit reads the served row only — a value the
    row does not carry cannot match, and asking must fail closed:
    no raise, no bit. (The pre-v2 code resolved the value by id
    against the library table; the column wasn't there, sqlite
    raised, and the export route 500'd.)"""
    subject = generate_soul_id("track", "Artist", "Song")
    depot.record_mark(db, "track", subject, "discogs_id", "1234567")
    row = {"id": "t1", "soul_id": subject}
    assert depot.network_fields_for_export(db, "track", row) == []


def test_export_value_columns_match_the_export_lists():
    """The bit fails CLOSED when a served row doesn't carry the
    mapped column — so a mapped name the export never serves is a
    bit that can never fire, and the value would export as the
    user's own (the laundering direction). Pin every mapped name
    against the export's own column lists. The one stated
    exception: track discogs_id, which the export deliberately
    never serves (Discogs is artist/album level) — its fail-closed
    behaviour is pinned above."""
    import database.music_database as mdb

    lists = {"artist": mdb._EXPORT_ARTIST_COLUMNS,
             "album": mdb._EXPORT_ALBUM_COLUMNS,
             "track": mdb._EXPORT_TRACK_COLUMNS}
    for (kind, field), column in depot._EXPORT_VALUE_COLUMNS.items():
        if (kind, field) == ("track", "discogs_id"):
            continue
        assert column in lists[kind], (kind, field, column)


# ── MF-2: marks are keyed by value, not one slot per field ───────

def test_marks_are_keyed_by_value(db):
    """The gate's case: the depot promotes ISRC A, the steered
    fetch returns ISRC B (recordings often carry several), and B's
    fetched-via mark used to OVERWRITE A's — while A is the value
    written to the tags. Both marks must coexist."""
    subject = generate_soul_id("track", "Artist", "Song")
    depot.record_mark(db, "track", subject, "isrc", "ISRC-A")
    depot.record_mark(db, "track", subject, "isrc", "ISRC-B",
                      via="fetched_via_promoted_id")
    keys = [json.loads(k) for k in
            depot.marks_for(db, "track", subject)["isrc"]]
    assert sorted(keys) == ["ISRC-A", "ISRC-B"]
    # re-marking the same value refreshes its row, never duplicates
    depot.record_mark(db, "track", subject, "isrc", "ISRC-A")
    assert len(depot.marks_for(db, "track", subject)["isrc"]) == 2


def test_export_bit_survives_a_second_marked_value(db):
    subject = generate_soul_id("track", "Artist", "Song")
    depot.record_mark(db, "track", subject, "isrc", "ISRC-A")
    depot.record_mark(db, "track", subject, "isrc", "ISRC-B",
                      via="fetched_via_promoted_id")
    # A is what the library holds: its bit must still fire
    row = {"id": "t1", "soul_id": subject, "isrc": "ISRC-A"}
    assert depot.network_fields_for_export(db, "track", row) == ["isrc"]


def test_genre_marks_from_two_paths_coexist(db):
    """Album genres: the depot's promoted genres and the genres
    MusicBrainz returned by way of a promoted MBID are two marks
    on one field; neither may evict the other."""
    subject = generate_soul_id("album", "Artist", "Album")
    depot.record_mark(db, "album", subject, "genres", ["Rock"])
    depot.record_mark(db, "album", subject, "genres", ["Jazz"],
                      via="fetched_via_promoted_id")
    assert len(depot.marks_for(db, "album", subject)["genres"]) == 2
    row = {"id": "alb1", "soul_id": subject, "genres": ["Rock", "Jazz"]}
    assert depot.network_fields_for_export(db, "album", row) == ["genres"]
    # either marked genre alone in the list still carries the bit
    row = {"id": "alb1", "soul_id": subject, "genres": ["Jazz"]}
    assert depot.network_fields_for_export(db, "album", row) == ["genres"]


# ── §4.1: leg (c)'s slot anchor is off for provider IDs ─────────

def test_leg_c_slot_anchor_off_for_provider_ids(db):
    """The settled slot proves WHICH track this is, not which
    values are right. It used to promote a provider ID at the
    slot; concerns §4.1 turns that off — every provider-ID field
    (the MBIDs included) now refuses at the very same slot, and
    the value fields refuse too, exactly as they always did: all
    of them need (a), (b), or a settlement."""
    album = "soul_album_slot"
    track = "soul_track_slot"
    variant = {"variants": {
        "mb:rel-1": {"fingerprint": "mb:rel-1", "settled": True,
                     "tracks": [{"track": track, "disc": 1, "position": 2,
                                 "tier": "strong-verified",
                                 "support": "1-2", "local": False}]},
    }, "local_value": None, "local_values": [], "disputed": False}
    depot.store_record(db, _record(album, "album",
                                   {"album_track": variant}), now=NOW)
    depot.store_record(db, _record(track, "track", {
        "spotify_id": _field([_value("sp9", support="1-2")]),
        "deezer_id": _field([_value("dz9", support="1-2")]),
        "recording_mbid": _field([_value("mb9", support="1-2")]),
        "isrc": _field([_value("USRC1", support="1-2")]),
        "year": _field([_value(1991, support="1-2")]),
    }), now=NOW)
    ctx = {"album_subject": album, "disc": 1, "position": 2}
    assert depot.promotion_verdict(db, track, "spotify_id", "sp9",
                                   now=NOW, **ctx) == (False, None)
    assert depot.promotion_verdict(db, track, "deezer_id", "dz9",
                                   now=NOW, **ctx) == (False, None)
    assert depot.promotion_verdict(db, track, "recording_mbid", "mb9",
                                   now=NOW, **ctx) == (False, None)
    assert depot.promotion_verdict(db, track, "isrc", "USRC1",
                                   now=NOW, **ctx) == (False, None)
    assert depot.promotion_verdict(db, track, "year", 1991,
                                   now=NOW, **ctx) == (False, None)


# ── SF-4: leg (b) is tied to the candidate the fetch was for ────

def test_leg_b_response_must_be_fetched_by_the_candidate(db):
    subject = "soul_track_b2"
    depot.store_record(db, _record(subject, "track", {
        "deezer_id": _field([_value("555", support="1-2")])}), now=NOW)
    response = {"isrc": "USRC10000001"}
    local = {"isrc": "USRC10000001"}
    # a response fetched by a DIFFERENT candidate's ID confirms
    # nothing about this one, however well its keys agree
    ok, _ = depot.promotion_verdict(
        db, subject, "deezer_id", "555", now=NOW,
        confirmation_response=response, confirmation_fetched_id="999",
        local_keys=local)
    assert ok is False
    # the same response, fetched by THIS candidate's ID, confirms
    assert depot.promotion_verdict(
        db, subject, "deezer_id", "555", now=NOW,
        confirmation_response=response, confirmation_fetched_id="555",
        local_keys=local) == (True, "b")
    # and the bare key check enforces the same tie
    assert not depot.confirmation_key_check(
        response, local, fetched_id="999", fetched_for="555")
    assert depot.confirmation_key_check(
        response, local, fetched_id="555", fetched_for="555")


# ── SF-5: unknown tiers/buckets fail closed at ingest ────────────

def test_unknown_bucket_behaves_as_none(db):
    """A bucket this build doesn't know ("1+" from a newer or
    broken node) is zero standing support: the value becomes a
    network reflection, leg (a) or nothing — a confirmation fetch
    cannot rescue it."""
    subject = "soul_track_bucket"
    depot.store_record(db, _record(subject, "track", {
        "deezer_id": _field([_value("555", support="1+")])}), now=NOW)
    row = depot.get_fields(db, subject, "deezer_id", now=NOW)[0]
    assert row["support_bucket"] == "none"
    ok, _ = depot.promotion_verdict(
        db, subject, "deezer_id", "555", now=NOW,
        confirmation_response={"isrc": "USRC10000001"},
        confirmation_fetched_id="555",
        local_keys={"isrc": "USRC10000001"})
    assert ok is False


def test_unknown_tier_is_stored_unverified(db):
    subject = "soul_track_tier"
    depot.store_record(db, _record(subject, "track", {
        "spotify_id": _field([_value(
            "spX", tier="super-verified", support="3+",
            verified_at=NOW.timestamp())])}), now=NOW)
    row = depot.get_fields(db, subject, "spotify_id", now=NOW)[0]
    assert row["tier"] == "unverified"
    assert row["effective_tier"] == "unverified"
    ok, _ = depot.promotion_verdict(db, subject, "spotify_id", "spX",
                                    now=NOW)
    assert ok is False


# ── SF-6: stored records are bounded ─────────────────────────────

def test_record_json_is_a_bounded_summary(db):
    """Nothing reads the stored record — the field rows ARE the
    depot — so the entry keeps a provenance summary, not a second
    copy of the record (values and all)."""
    subject = "soul_track_summary"
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("USRC10000001")])}), now=NOW)
    conn = db._get_connection()
    raw = conn.execute(
        "SELECT record_json FROM metasync_depot_entries "
        "WHERE subject = ?", (subject,)).fetchone()[0]
    summary = json.loads(raw)
    assert summary["found"] is True
    assert summary["fields"] == ["isrc"]
    assert "USRC10000001" not in raw  # no values ride along


def test_deeply_nested_push_is_bounded_not_a_500(db):
    """A 200-deep record used to RecursionError inside the route
    and surface as a 500. It now stores (cleaned + truncated) and
    the depot works on."""
    deep = cur = {}
    for _ in range(200):
        cur["x"] = {}
        cur = cur["x"]
    record = _record("soul_track_deep", "track", {
        "isrc": _field([_value("D1")])}, name=deep,
        disambiguation=deep)
    assert depot.store_record(db, record, now=NOW) is True
    block = depot.get_disambiguation(db, "soul_track_deep")
    assert isinstance(block, dict)  # truncated, still a dict
    assert depot.get_fields(db, "soul_track_deep", now=NOW)


def test_update_push_caps_the_record_list(db, monkeypatch):
    monkeypatch.setattr(depot, "MAX_PUSH_RECORDS", 3)
    records = [_record(f"soul_track_push_{i}", "track", {
        "isrc": _field([_value("P1")])}) for i in range(5)]
    result = depot.apply_update_push(db, {"records": records}, now=NOW)
    assert result == {"status": "ok", "stored": 3}
    assert depot.apply_update_push(db, {"records": "nope"}) == \
        {"status": "bad_payload"}


# ── SF-7: gated MusicBrainz values inherit the mark ──────────────

class _FakeMBClient:
    def __init__(self, recording=None, release=None):
        self._recording = recording or {}
        self._release = release or {}

    def get_recording(self, mbid, includes=None):
        return self._recording

    def get_release(self, mbid, includes=None):
        return self._release


class _FakeMBService:
    def __init__(self, artist_result, recording, release):
        self.mb_client = _FakeMBClient(recording, release)
        self._artist_result = artist_result
        self._recording = recording
        self.gated_with = []

    def match_artist(self, name):
        return self._artist_result

    def match_recording(self, title, artist_name, artist_mbid=None,
                        duration_ms=None, isrc=None):
        self.gated_with.append(artist_mbid)
        if self._recording:
            return {"mbid": "rec-gated", "recording_disambiguation": None}
        return None

    def match_release(self, album, artist_name):
        return {"mbid": "rel-1"}


def _run_embed_mb(db, monkeypatch, mb_service):
    """Drive embed_source_ids with ONLY the musicbrainz slot live
    (the real processor) against a fake MB service."""
    import core.metadata.album_mbid_cache as persisted
    import core.metadata.source as source_mod
    from types import SimpleNamespace

    monkeypatch.setattr(source_mod, "get_database", lambda: db)
    monkeypatch.setattr(
        source_mod, "get_config_manager",
        lambda: _Cfg({"metadata_enhancement.post_process_order":
                      ["musicbrainz"]}))
    monkeypatch.setattr(source_mod, "get_mutagen_symbols",
                        lambda: object())
    monkeypatch.setattr(source_mod, "_collect_source_ids",
                        lambda metadata, cfg: {})
    monkeypatch.setattr(source_mod, "_write_embedded_metadata",
                        lambda *a, **kw: None)
    monkeypatch.setattr(persisted, "lookup", lambda *a, **kw: None)
    monkeypatch.setattr(persisted, "record", lambda *a, **kw: None)

    def enrich(source_name, pp, metadata, cfg, runtime, track_title,
               artist_name, provenance=None, audio_file=None,
               track_artist_name=None):
        if source_name == "musicbrainz":
            source_mod._process_musicbrainz_source(
                pp, metadata, cfg, runtime, track_title, artist_name,
                audio_file=audio_file)

    monkeypatch.setattr(source_mod, "_process_source_enrichment", enrich)
    runtime = SimpleNamespace(
        mb_worker=SimpleNamespace(mb_service=mb_service))
    metadata = {"title": "Gate Song", "artist": "Gate Artist",
                "album_artist": "Gate Artist", "album": "Gate Album",
                "track_number": 1, "disc_number": 1}
    source_mod.embed_source_ids(object(), metadata, context={},
                                runtime=runtime)


def _seed_gate_depot(db):
    artist_sid = generate_soul_id("artist", "Gate Artist")
    track_sid = generate_soul_id("track", "Gate Artist", "Gate Song")
    album_sid = generate_soul_id("album", "Gate Artist", "Gate Album")
    depot.store_record(db, _record(artist_sid, "artist", {
        "artist_mbid": _field([_strong("mb-artist-1", support="none")])}),
        now=NOW)
    # a track entry so the consult doesn't go asking the node; the
    # isrc candidate is a zero-support reflection and never promotes
    depot.store_record(db, _record(track_sid, "track", {
        "isrc": _field([_value("UNPROMOTED", support="none")])}), now=NOW)
    depot.store_record(db, _record(album_sid, "album", {}), now=NOW)
    return artist_sid, track_sid, album_sid


def _gate_mb_service(artist_result):
    recording = {"isrcs": ["ISRCGATED1"],
                 "genres": [{"name": "Rock", "count": 5}]}
    release = {"id": "rel-1",
               "artist-credit": [{
                   "name": "Gate Artist",
                   "artist": {"id": "mb-artist-1",
                              "name": "Gate Artist"}}],
               "release-group": {"id": "rg-1",
                                 "first-release-date": "1999-05-05"},
               "media": [], "genres": []}
    return _FakeMBService(artist_result, recording, release)


def test_gated_mb_values_are_marked(db, monkeypatch):
    """MB's own artist lookup comes back empty, so the depot's
    promoted artist MBID gates match_recording and
    release_by_artist. Everything those gated lookups land —
    recording MBID, release-group ID, year (album AND track),
    ISRC, genres — was reached BY WAY OF the promoted ID and
    inherits its mark."""
    _artist_sid, track_sid, album_sid = _seed_gate_depot(db)
    service = _gate_mb_service(artist_result=None)
    _run_embed_mb(db, monkeypatch, service)
    assert service.gated_with == ["mb-artist-1"]  # the gate happened

    track_marks = depot.marks_for(db, "track", track_sid)
    album_marks = depot.marks_for(db, "album", album_sid)
    assert [json.loads(k) for k in track_marks["recording_mbid"]] == \
        ["rec-gated"]
    assert [json.loads(k) for k in track_marks["isrc"]] == ["ISRCGATED1"]
    assert [json.loads(k) for k in track_marks["year"]] == ["1999"]
    assert [json.loads(k) for k in album_marks["release_group_mbid"]] == \
        ["rg-1"]
    assert [json.loads(k) for k in album_marks["year"]] == ["1999"]
    assert [json.loads(k) for k in album_marks["genres"]] == [["Rock"]]

    # and the bits fire on the rows those values land in
    track_row = {"id": "gt1", "soul_id": track_sid,
                 "musicbrainz_recording_id": "rec-gated",
                 "isrc": "ISRCGATED1", "year": 1999}
    assert depot.network_fields_for_export(db, "track", track_row) == \
        ["isrc", "recording_mbid", "year"]
    album_row = {"id": "ga1", "soul_id": album_sid,
                 "musicbrainz_release_group_id": "rg-1", "year": 1999,
                 "genres": ["Rock"]}
    assert depot.network_fields_for_export(db, "album", album_row) == \
        ["genres", "release_group_mbid", "year"]


def test_ungated_mb_values_are_not_marked_by_the_gate(db, monkeypatch):
    """When MusicBrainz's OWN artist lookup succeeds, nothing was
    gated by the depot: the gated-mark mechanism must not fire.
    (The recording/release-group/year values MB found by its own
    search carry no fetched-via mark from this mechanism.)"""
    _artist_sid, track_sid, album_sid = _seed_gate_depot(db)
    service = _gate_mb_service(artist_result={"mbid": "mb-artist-1"})
    _run_embed_mb(db, monkeypatch, service)
    track_marks = depot.marks_for(db, "track", track_sid)
    album_marks = depot.marks_for(db, "album", album_sid)
    assert "recording_mbid" not in track_marks
    assert "year" not in track_marks
    assert "release_group_mbid" not in album_marks
    assert "year" not in album_marks


def test_steered_year_marks_the_track_too(db, monkeypatch):
    """The steered Deezer year lands in the file's DATE tag, which
    becomes the TRACK row's year at import — tracks export year,
    so the track kind carries the mark, not just the album."""
    track_sid = generate_soul_id("track", "The Artist", "The Song")
    album_sid = generate_soul_id("album", "The Artist", "The Album")
    depot.store_record(db, _record(track_sid, "track", {
        "deezer_id": _field([_strong("555")])}), now=NOW)
    _artist_id, album_id, track_id = _seed_lib2(
        db, artist="The Artist", album="The Album", title="The Song")
    conn = db._get_connection()
    conn.execute(
        "UPDATE lib2_albums SET soul_id = ?, year = 1991 WHERE id = ?",
        (album_sid, album_id))
    conn.execute(
        "UPDATE lib2_tracks SET soul_id = ? WHERE id = ?",
        (track_sid, track_id))
    conn.commit()

    def enrich(source_name, pp, metadata, cfg, runtime, track_title,
               artist_name, provenance=None, audio_file=None,
               track_artist_name=None):
        if source_name == "deezer" and \
                (provenance or {}).get("track_id") == "555":
            pp["release_year"] = "1991"  # the steered fetch's fill

    _run_embed(db, monkeypatch, enrich=enrich)
    track_marks = depot.marks_for(db, "track", track_sid)
    assert [json.loads(k) for k in track_marks["year"]] == ["1991"]
    row = {"id": track_id, "soul_id": track_sid, "year": 1991}
    assert depot.network_fields_for_export(db, "track", row) == ["year"]


def test_depot_year_promotion_marks_the_track_too(db):
    """A year the DEPOT promotes (not a steered fetch) becomes the
    track's year the same way, so its mark covers the track kind
    from the consult itself."""
    from core.metadata.source import _process_metasync_depot_source

    track_sid = generate_soul_id("track", "The Artist", "The Song")
    album_sid = generate_soul_id("album", "The Artist", "The Album")
    depot.store_record(db, _record(album_sid, "album", {
        "year": _field([_strong(1991, support="1-2")])}), now=NOW)
    depot.store_record(db, _record(track_sid, "track", {}), now=NOW)
    pp = _pp()
    _process_metasync_depot_source(
        pp, {"album": "The Album", "track_number": 1, "disc_number": 1},
        _Cfg(), None, "The Song", "The Artist", context=None, db=db)
    assert pp["release_year"] == "1991"
    marks = {(m[0], m[2], str(m[3])) for m in pp["_depot"]["marks"]}
    assert ("album", "year", "1991") in marks
    assert ("track", "year", "1991") in marks


# ── SF-8: reconcile rotation + miss memo ─────────────────────────

def test_reconcile_rotates_a_rejected_subject(db, monkeypatch):
    """A record whose shape store_record rejects used to stay at
    the front of the fetched_at window forever — the same
    subjects re-fetched every run, everything behind them never
    checked. A reject now rotates the entry to the back and is
    counted; its stored candidates are untouched."""
    subject = "soul_track_reject"
    earlier = NOW - timedelta(days=1)
    depot.store_record(db, _record(subject, "track", {
        "isrc": _field([_value("X")])}), now=earlier)
    monkeypatch.setattr(
        depot, "fetch_node_record",
        lambda cfg, subject: {"subject": subject, "kind": "bogus"})
    result = depot.reconcile(db, _node_cfg, now=NOW)
    assert result["rejected"] == 1 and result["refreshed"] == 0
    conn = db._get_connection()
    fetched_at, rejects = conn.execute(
        "SELECT fetched_at, reconcile_rejects FROM "
        "metasync_depot_entries WHERE subject = ?",
        (subject,)).fetchone()
    assert datetime.fromisoformat(fetched_at) == NOW  # rotated
    assert rejects == 1
    # the candidates it already held were NOT dropped on a reject
    assert depot.get_fields(db, subject, now=NOW)


def test_collect_memoizes_found_false_subjects(db, monkeypatch):
    """A found:false entry has no fields, so the consult's
    have-anything check stays false and a batch re-asked the node
    for every file. The miss is now memoized for a short TTL."""
    calls = []

    def fake_query(cfg, kind, subjects):
        calls.append((kind, list(subjects)))
        return [_record(s, kind, {}, found=False) for s in subjects]

    monkeypatch.setattr(depot, "query_node", fake_query)
    depot._node_miss_until.clear()
    first = depot.collect_subjects(
        db, _node_cfg, {"track": ["soul_track_miss"]}, now=NOW)
    second = depot.collect_subjects(
        db, _node_cfg, {"track": ["soul_track_miss"]}, now=NOW)
    assert calls == [("track", ["soul_track_miss"])]  # asked once
    assert first["stored"] == 1 and second["stored"] == 0


# ── e2e: depot -> tags -> download import -> export ─────────────

def test_promoted_value_round_trips_to_the_export(client, db, monkeypatch):
    """The whole promotion loop, production code at every hop:

    depot candidate -> the tagging consult fills the tag value and
    the pipeline's marks block writes the §2.4 mark -> the SAME
    import context embed_source_ids filled carries the tag set
    into the download import (link_download_into_library_v2 reads
    SPOTIFY_TRACK_ID out of it and writes it onto the lib2 track
    it creates) -> the real SoulID worker mints the row's soul_id
    from the row's own names -> the export route serves the value
    back byte-identical, with the network bit on.

    Nothing is represented by hand: the value crosses every seam
    in the form production passes it in."""
    import core.library2.autolink as autolink
    import core.metadata.source as source_mod
    from core.soulid_worker import SoulIDWorker

    monkeypatch.setattr(autolink, "_warm_new_artwork",
                        lambda *a, **kw: None)

    track_sid = generate_soul_id("track", "The Artist", "The Song")
    depot.store_record(db, _record(track_sid, "track", {
        "spotify_id": _field([_strong("spE2E")])}), now=NOW)

    # tagging: _run_embed's stubs, except the tag writer captures
    # the pp it is handed — that pp IS the tag set written to the
    # file in production. The context dict is kept: embed_source_ids
    # publishes the final tag set back into it for the import.
    captured = {}

    def capture_write(audio_file, metadata, pp, cfg, symbols):
        captured["pp"] = pp

    monkeypatch.setattr(source_mod, "get_database", lambda: db)
    monkeypatch.setattr(
        source_mod, "get_config_manager",
        lambda: _Cfg({"metadata_enhancement.post_process_order":
                      ["deezer"]}))
    monkeypatch.setattr(source_mod, "get_mutagen_symbols",
                        lambda: object())
    monkeypatch.setattr(source_mod, "_collect_source_ids",
                        lambda metadata, cfg: {})
    monkeypatch.setattr(source_mod, "_write_embedded_metadata",
                        capture_write)
    monkeypatch.setattr(source_mod, "_process_source_enrichment",
                        lambda *a, **kw: None)
    metadata = {"title": "The Song", "artist": "The Artist",
                "album_artist": "The Artist", "album": "The Album",
                "track_number": 1, "disc_number": 1}
    embed_context = {}
    source_mod.embed_source_ids(object(), metadata,
                                context=embed_context, runtime=None)

    tag_value = captured["pp"]["id_tags"]["SPOTIFY_TRACK_ID"]
    assert tag_value == "spE2E"
    assert embed_context["_embedded_id_tags"]["SPOTIFY_TRACK_ID"] == "spE2E"
    # the mark was written by the pipeline's own marks block
    assert depot.marks_for(db, "track", track_sid)["spotify_id"]

    # download import: the context the pipeline hands the import,
    # with the tag set embed_source_ids published into it. No
    # track_info id on purpose: the Spotify ID can only arrive
    # from the embedded tags.
    import_context = {
        "_final_processed_path":
            "/music/The Artist/The Album/01 The Song.flac",
        "username": "test",
        "track_info": {
            "name": "The Song",
            "artists": [{"name": "The Artist"}],
            "album": {"name": "The Album", "total_tracks": 1,
                      "album_type": "album"},
            "track_number": 1,
            "provider": "spotify",
        },
        "_embedded_id_tags": embed_context["_embedded_id_tags"],
    }
    file_id = autolink.link_download_into_library_v2(import_context)
    assert file_id is not None
    conn = db._get_connection()
    track_id, stored = conn.execute(
        "SELECT t.id, t.spotify_id FROM lib2_track_files tf "
        "JOIN lib2_tracks t ON t.id = tf.track_id WHERE tf.id = ?",
        (file_id,)).fetchone()
    assert stored == "spE2E"  # written by the import, from the tags

    # identity: the real worker mints the row's soul_id from its
    # own names — the agreement the mark lookup depends on
    worker = SoulIDWorker.__new__(SoulIDWorker)
    worker.db = db
    worker.batch_size = 100
    worker.should_stop = False
    worker.current_item = None
    worker.stats = {"artists_processed": 0, "albums_processed": 0,
                    "tracks_processed": 0, "errors": 0, "pending": 0}
    worker._process_artists()
    worker._process_albums()
    worker._process_tracks()
    minted = conn.execute(
        "SELECT soul_id FROM lib2_tracks WHERE id = ?",
        (track_id,)).fetchone()[0]
    assert minted == track_sid

    # export: the real route serves the value back, still marked
    resp = client.get("/api/v1/metasync/export?entity=track",
                      headers=_auth())
    assert resp.status_code == 200
    mine = [i for i in resp.get_json()["data"]["items"]
            if i["soul_id"] == track_sid]
    assert mine and mine[0]["spotify_track_id"] == "spE2E"
    assert mine[0]["network_fields"] == ["spotify_id"]


# ── the consult never waits on the network ───────────────────────
# A network ask holds the node's relay round open for its collection
# window (6s by default). On a depot miss the consult used to make that
# ask inside tagging, so every new track of an import could wait ~6s.

def test_the_consult_asks_cache_only_and_queues_its_misses(db, monkeypatch):
    from core.metadata.source import _process_metasync_depot_source
    asks, queued = [], []

    def fake_query(cfg, kind, subjects, network=True):
        asks.append((kind, network))
        return [_record(s, kind, {}, found=False) for s in subjects]

    monkeypatch.setattr(depot, "query_node", fake_query)
    monkeypatch.setattr(depot, "ask_network_soon",
                        lambda db_, cfg, subjects: queued.append(subjects) or True)
    depot._node_miss_until.clear()
    pp = _pp()
    _process_metasync_depot_source(
        pp, {"album": "The Album", "track_number": 1}, _Cfg(_node_cfg_values()),
        None, "The Song", "The Artist", context=None, db=db)

    assert asks and all(network is False for _kind, network in asks)
    track_sid = generate_soul_id("track", "The Artist", "The Song")
    assert queued and track_sid in queued[0]["track"]


def _node_cfg_values():
    return {"metasync.node_url": "http://node:8790", "metasync.local_api_key": "mn_key"}


def test_a_cache_only_miss_neither_clears_the_depot_nor_counts_as_a_miss(db, monkeypatch):
    subject = "soul_track_cached"
    depot.store_record(db, _record(subject, "track", {"isrc": _field([_value("K1")])}), now=NOW)
    monkeypatch.setattr(depot, "query_node",
                        lambda cfg, kind, subjects, network=True:
                        [_record(s, kind, {}, found=False) for s in subjects])
    depot._node_miss_until.clear()

    depot.collect_subjects(db, _node_cfg, {"track": [subject]}, now=NOW, network=False)

    assert depot.get_fields(db, subject, now=NOW), "the stored candidates survive"
    assert not depot._miss_memoized("http://node:8790", "track", subject)


def test_the_network_ask_is_what_records_a_miss(db, monkeypatch):
    monkeypatch.setattr(depot, "query_node",
                        lambda cfg, kind, subjects, network=True:
                        [_record(s, kind, {}, found=False) for s in subjects])
    depot._node_miss_until.clear()
    depot.collect_subjects(db, _node_cfg, {"track": ["soul_track_gone"]}, now=NOW)
    assert depot._miss_memoized("http://node:8790", "track", "soul_track_gone")


def test_query_node_says_whether_to_ask_the_network(monkeypatch):
    sent = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "records": []}

    monkeypatch.setattr(depot.requests, "post",
                        lambda url, json=None, **kw: sent.append(json) or _Resp())
    depot._node_failure_until.clear()
    depot.query_node(_node_cfg, "track", ["soul_x"], network=False)
    depot.query_node(_node_cfg, "track", ["soul_x"])
    assert [b["network"] for b in sent] == [False, True]


@pytest.mark.parametrize("answer", ["http_500", "not_json"])
def test_a_bad_answer_opens_the_breaker_too(monkeypatch, answer):
    """a node answering 500s (or a proxy's html page) was asked again
    for every file of a batch import: only transport errors tripped it"""
    calls = []

    class _Resp:
        status_code = 500 if answer == "http_500" else 200

        def json(self):
            raise ValueError("not json")

    def post(*a, **kw):
        calls.append(1)
        return _Resp()

    monkeypatch.setattr(depot.requests, "post", post)
    depot._node_failure_until.clear()
    for _ in range(2):
        with pytest.raises(depot.NodeUnreachable):
            depot.query_node(_node_cfg, "track", ["soul_x"])
    assert len(calls) == 1
    depot._node_failure_until.clear()


def test_the_background_asker_deposits_the_network_answer(db, monkeypatch):
    asked = []

    def fake_query(cfg, kind, subjects, network=True):
        asked.append((kind, tuple(subjects), network))
        return [_record(s, kind, {"isrc": _field([_value("BG1")])}) for s in subjects]

    monkeypatch.setattr(depot, "query_node", fake_query)
    monkeypatch.setattr(depot, "BACKGROUND_ASK", True)
    depot._node_miss_until.clear()
    depot._node_failure_until.clear()
    depot._bg_queue.clear()

    assert depot.ask_network_soon(db, _node_cfg, {"track": ["soul_track_bg"]}) is True
    depot._bg_state["thread"].join(10)

    assert asked == [("track", ("soul_track_bg",), True)]
    assert depot.get_fields(db, "soul_track_bg")


def test_the_background_queue_is_bounded(db, monkeypatch):
    monkeypatch.setattr(depot, "BACKGROUND_ASK", True)
    monkeypatch.setattr(depot, "BACKGROUND_QUEUE_MAX", 3)
    depot._bg_queue.clear()
    depot._node_miss_until.clear()
    depot._node_failure_until.clear()
    # a live worker already holds the queue: nothing new starts, the queue caps
    alive = type("T", (), {"is_alive": lambda self: True})()
    monkeypatch.setitem(depot._bg_state, "thread", alive)

    depot.ask_network_soon(db, _node_cfg, {"track": [f"soul_track_{i}" for i in range(10)]})

    assert list(depot._bg_queue) == [("track", f"soul_track_{i}") for i in (7, 8, 9)]
    depot._bg_queue.clear()


def test_no_node_means_no_background_ask(db, monkeypatch):
    monkeypatch.setattr(depot, "BACKGROUND_ASK", True)
    depot._bg_queue.clear()
    assert depot.ask_network_soon(db, lambda k, d=None: d, {"track": ["soul_x"]}) is False
    assert not depot._bg_queue
