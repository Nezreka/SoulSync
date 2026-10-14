from flask import Flask

from api import repair


def test_deezer_preview_refreshes_at_playback_and_accepts_only_deezer_cdn(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(repair.create_blueprint())
    calls = []

    class Response:
        def __init__(self, preview):
            self.preview = preview

        def raise_for_status(self):
            pass

        def json(self):
            return {'preview': self.preview}

    def get(url, timeout):
        calls.append((url, timeout))
        return Response('https://cdnt-preview.dzcdn.net/track.mp3?fresh=1')

    monkeypatch.setattr(repair.requests, 'get', get)
    result = app.test_client().get('/api/repair/release-preview/deezer/2486074331')
    assert result.status_code == 302
    assert result.headers['Location'].endswith('fresh=1')
    assert result.headers['Cache-Control'] == 'no-store'
    assert calls == [('https://api.deezer.com/track/2486074331', 8)]

    monkeypatch.setattr(repair.requests, 'get',
                        lambda *_args, **_kwargs: Response('https://other.example/audio.mp3'))
    assert app.test_client().get('/api/repair/release-preview/deezer/2486074331').status_code == 404
