"""The production HTTP listener remains responsive during upgrade backup."""
import threading


def _request(app, path='/'):
    status = []
    body = b''.join(app({'PATH_INFO': path, 'REQUEST_METHOD': 'GET'}, lambda s, h: status.append(s)))
    return status[0], body


def test_boot_does_not_block_http_and_hands_off_only_when_ready():
    from core.startup_application import StartupApplication
    entered, release = threading.Event(), threading.Event()

    def load():
        entered.set()
        assert release.wait(5)
        return lambda env, start: (start('200 OK', []), [b'normal app'])[1]

    app = StartupApplication(load)
    assert entered.wait(2)
    try:
        status, body = _request(app)
        assert status == '200 OK'
        assert b'Preparing your library' in body
        assert b'"starting"' in _request(app, '/api/startup-status')[1]
    finally:
        release.set()
        assert app.wait(5)
    assert _request(app) == ('200 OK', b'normal app')


def test_failed_backup_keeps_app_closed_and_reports_failure():
    from core.startup_application import StartupApplication

    def load():
        raise RuntimeError('private database path')

    app = StartupApplication(load)
    assert app.wait(5)
    status, body = _request(app, '/api/startup-status')
    assert status == '503 Service Unavailable'
    assert b'"failed"' in body and b'private database path' not in body


def test_start_page_polls_under_reverse_proxy_prefix():
    from core.startup_application import StartupApplication
    release = threading.Event()
    app = StartupApplication(lambda: (release.wait(5), None)[1])
    try:
        body = b''.join(app({'PATH_INFO': '/library', 'SCRIPT_NAME': '/soulsync'}, lambda *_: None))
        assert b'/soulsync/api/startup-status' in body
    finally:
        release.set()
        app.wait(5)
