"""review 5 R5-I-2: the acoustid scanner honours every profile's manual
"skip acoustid" override, not only the admin's. web_server records the
override under the requesting profile."""
from types import SimpleNamespace

from database.music_database import MusicDatabase
from tests import lib2_seed
from core.repair_jobs.base import JobContext


class _Client:
    def is_available(self):
        return True, ''

    def test_api_key(self):
        return True, ''


def _run(db, monkeypatch):
    from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob
    import core.acoustid_verification as av
    seen = []

    def fake_verify(self, fpath, *a, **k):
        seen.append(fpath)
        return av.VerificationResult.ERROR, 'stub'
    monkeypatch.setattr(av.AcoustIDVerification, 'verify_audio_file', fake_verify)
    cfg = SimpleNamespace(get=lambda k, d=None: d, set=lambda *a, **k: None)
    AcoustIDScannerJob().scan(JobContext(db, '/music', cfg, acoustid_client=_Client()))
    return seen


def _seed(tmp_path, profile_id):
    from core.library2.manual_skips import attach_manual_skip_file, record_manual_skip
    db = MusicDatabase(str(tmp_path / 'ac.db'))
    audio = tmp_path / 'forced.flac'
    audio.write_bytes(b'fLaC')
    with db._get_connection() as conn:
        tid = lib2_seed.track(conn, 'Artist', 'Album', 'Forced', track_number=1)
        conn.execute('UPDATE lib2_track_files SET path=? WHERE track_id=?', (str(audio), tid))
        conn.commit()
    record_manual_skip(db, content_key='u::forced.flac', title='Forced', artist='Artist',
                       skipped_checks=['acoustid'], profile_id=profile_id)
    attach_manual_skip_file(db, content_key='u::forced.flac', file_path=str(audio))
    return db


def test_admin_override_is_honoured(tmp_path, monkeypatch):
    # control: profile 1's override skips the file
    assert _run(_seed(tmp_path, 1), monkeypatch) == []


def test_other_profiles_override_is_honoured(tmp_path, monkeypatch):
    # profile 2 made the same manual "skip AcoustID" download
    assert _run(_seed(tmp_path, 2), monkeypatch) == []
