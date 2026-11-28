"""OpenSubsonic replayGain on songs: the file's gains and peaks, as JSON
fields and as an xml child, and nothing for a song with none."""

from __future__ import annotations

from contextlib import closing

from core.library_scope import library_scope
from core.subsonic import library as L
from core.subsonic import response as R


def test_songs_carry_replay_gain(mdb, seed):
    a = seed.artist("Air")
    al = seed.album(a, "Moon Safari")
    tagged = seed.track(al, "La Femme d'Argent", n=1)
    bare = seed.track(al, "Sexy Boy", n=2)
    with closing(mdb._get_connection()) as conn:
        conn.execute("UPDATE lib2_track_files SET tags_json = ? WHERE track_id = ?",
                     ('{"replaygain_track_gain": "-6.20 dB", "replaygain_track_peak": "0.912000", '
                      '"replaygain_album_gain": "-7.10 dB", "replaygain_album_peak": "0.998000"}', tagged))
        conn.commit()
    with closing(mdb._get_connection()) as conn, library_scope("shared"):
        lib = L.Library(conn, db_key=str(mdb.database_path), scope="shared", hide_explicit=False)
        song = lib.song(tagged)
        assert song["replayGain"] == {"trackGain": -6.2, "albumGain": -7.1, "trackPeak": 0.912,
                                      "albumPeak": 0.998, "baseGain": 0.0}
        assert "replayGain" not in lib.song(bare)
        xml = R.to_xml(R.ok({"song": song}))
        assert '<replayGain ' in xml and 'trackGain="-6.2"' in xml and 'baseGain="0.0"' in xml


def test_replay_gain_shape_edges():
    assert L.replay_gain(None) is None
    assert L.replay_gain({"track_gain": None, "album_gain": None}) is None
    assert L.replay_gain({"track_gain": -3.0, "reference_lufs": -18.0}) == {"trackGain": -3.0, "baseGain": 0.0}
