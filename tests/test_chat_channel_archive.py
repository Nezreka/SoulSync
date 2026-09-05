"""The chat channel tags must survive the archive (the only-#general bug).

Live messages reach the frontend through web_server's push loop, which
archives its DECODED dicts — and decoding is exactly the step that strips the
!SS1! envelope. Until this fix neither the push loop nor the archive carried
the channel/thread/avatar tags, so every live message filed into #general and
the tag was unrecoverable on reload. Pinned here: the archive round-trip
(write dicts with chan/th/tn/av, read them back under the wire names the
frontend consumes).
"""

from database.music_database import MusicDatabase


def _db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def test_channel_thread_avatar_tags_survive_the_archive(tmp_path):
    db = _db(tmp_path)
    db.add_chat_messages('soulsync', [
        {'username': 'boulder', 'message': 'help me', 'rich': True,
         'timestamp': '2026-08-11T01:00:00Z', 'chan': 'help', 'av': 7},
        {'username': 'nick', 'message': 'in a thread', 'rich': True,
         'timestamp': '2026-08-11T01:01:00Z', 'chan': 'bugs',
         'th': 'boulder|2026-08-11T00:59:00Z', 'tn': 'that weird bug'},
        {'username': 'vanilla-user', 'message': 'plain soulseek line',
         'rich': False, 'timestamp': '2026-08-11T01:02:00Z'},
    ])
    rows = db.get_chat_messages('soulsync')
    assert len(rows) == 3
    by_user = {r['username']: r for r in rows}

    assert by_user['boulder']['chan'] == 'help'
    assert by_user['boulder']['av'] == 7

    threaded = by_user['nick']
    assert threaded['chan'] == 'bugs'
    assert threaded['th'] == 'boulder|2026-08-11T00:59:00Z'
    assert threaded['tn'] == 'that weird bug'

    # An untagged (vanilla Soulseek) message carries NO tag keys at all — the
    # frontend's fallback files it into #general, and a null-ish key would
    # read as a real (unknown) channel.
    plain = by_user['vanilla-user']
    for key in ('chan', 'th', 'tn', 'av'):
        assert key not in plain, key


def test_edit_target_survives_the_archive(tmp_path):
    """An edit carrier's 'ed' key must reload with the message, or an edited
    message silently un-edits after a restart."""
    db = _db(tmp_path)
    db.add_chat_messages('soulsync', [
        {'username': 'boulder', 'message': 'the corrected words', 'rich': True,
         'timestamp': '2026-08-11T03:00:00Z',
         'ed': 'boulder|2026-08-11T02:59:00Z|typo'},
        {'username': 'boulder', 'message': 'no edit here', 'rich': True,
         'timestamp': '2026-08-11T03:01:00Z'},
    ])
    rows = db.get_chat_messages('soulsync')
    by_msg = {r['message']: r for r in rows}
    assert by_msg['the corrected words']['ed'] == 'boulder|2026-08-11T02:59:00Z|typo'
    assert 'ed' not in by_msg['no edit here']


def test_tag_lengths_are_bounded_on_write(tmp_path):
    db = _db(tmp_path)
    db.add_chat_messages('soulsync', [
        {'username': 'x', 'message': 'y', 'rich': True,
         'timestamp': '2026-08-11T02:00:00Z',
         'chan': 'c' * 100, 'th': 't' * 400, 'tn': 'n' * 200, 'av': 'not-an-int'},
    ])
    row = db.get_chat_messages('soulsync')[0]
    assert len(row['chan']) == 24
    assert len(row['th']) == 160
    assert len(row['tn']) == 80
    assert 'av' not in row  # unparseable avatar is dropped, not archived as junk


def test_resolve_library_tracks_batches_in_one_pass(tmp_path):
    """The Listen button's resolver: one IN() query for the whole playlist,
    case-insensitive on title AND artist, missing-file rows excluded."""
    db = _db(tmp_path)
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, name_key) VALUES (?,?,?)",
            (1, 'Ado', 'ado'),
        )
        conn.execute(
            "INSERT INTO lib2_albums (id, primary_artist_id, title, image_url)"
            " VALUES (?,?,?,?)", (1, 1, 'Kyougen', 'k.jpg'),
        )
        conn.execute(
            "INSERT INTO lib2_tracks (id, album_id, title) VALUES (?,?,?)",
            (1, 1, 'Show'),
        )
        conn.execute(
            "INSERT INTO lib2_tracks (id, album_id, title) VALUES (?,?,?)",
            (2, 1, 'Gone File'),
        )
        conn.executemany(
            "INSERT INTO lib2_track_artists (track_id, artist_id, role, position)"
            " VALUES (?,?,?,?)", [(1, 1, 'primary', 0), (2, 1, 'primary', 0)],
        )
        conn.executemany(
            "INSERT INTO lib2_track_files"
            " (track_id, path, is_primary, file_state, source) VALUES (?,?,?,?,?)",
            [(1, '/m/show.flac', 1, 'active', 'import'),
             (2, '', 1, 'active', 'import')],
        )
        conn.commit()
    finally:
        conn.close()

    out = db.resolve_library_tracks([
        ('SHOW', 'ado'),              # case-insensitive both sides
        ('Gone File', 'Ado'),         # exists but no file -> absent
        ('Never Synced', 'Nobody'),   # not in the library -> absent
    ])
    assert set(out.keys()) == {('show', 'ado')}
    row = out[('show', 'ado')]
    assert row['file_path'] == '/m/show.flac'
    assert row['artist_name'] == 'Ado'
    assert row['album_title'] == 'Kyougen'
