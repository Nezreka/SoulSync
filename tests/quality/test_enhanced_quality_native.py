"""The enhanced artist adapter derives badges from native live profiles."""

from tests.lib2_seed import track


def test_enhanced_artist_endpoint_stays_available_without_a_legacy_quality_scan(monkeypatch):
    import web_server

    db = web_server.get_database()
    profile = db.create_quality_profile("Endpoint MP3 cutoff", {
        "ranked_targets": [{"format": "mp3", "min_bitrate": 320}],
        "upgrade_policy": "until_cutoff", "upgrade_cutoff_index": 0,
    })
    with db._get_connection() as conn:
        tid = track(conn, "Quality Endpoint Artist", "Album", "Song", credit="Quality Endpoint Artist",
                    quality_profile_id=profile, quality_profile_explicit=1)
        conn.execute("UPDATE lib2_track_files SET format='mp3', bitrate=128 WHERE track_id=?", (tid,))
        artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Quality Endpoint Artist'").fetchone()[0]
        conn.commit()
    monkeypatch.setattr(web_server, "media_server_engine", None)
    client = web_server.app.test_client()
    with client.session_transaction() as session:
        session["profile_id"] = 1
    response = client.get(f"/api/library/artist/{artist_id}/enhanced")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    album, = payload["albums"]
    assert album["upgradable_count"] == 1
    assert album["tracks"][0]["quality_upgrade"]["current"] == "MP3 128kbps"
