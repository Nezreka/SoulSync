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


def test_status_carries_the_running_phase_and_its_numbers():
    import json
    from core import startup_progress
    from core.startup_application import StartupApplication
    release = threading.Event()
    app = StartupApplication(lambda: (release.wait(5), None)[1])
    try:
        startup_progress.begin('Backing up the database', step=2, steps=4, size=4096)
        startup_progress.advance(1024, 4096)
        status = json.loads(_request(app, '/api/startup-status')[1])
        assert {k: status[k] for k in ('status', 'label', 'step', 'steps', 'current', 'total')} == {
            'status': 'starting', 'label': 'Backing up the database', 'step': 2, 'steps': 4,
            'current': 1024, 'total': 4096}
        assert isinstance(status['elapsed'], int)
        assert b'id="phase"' in _request(app)[1]
    finally:
        release.set()
        app.wait(5)


def test_the_healthcheck_probe_is_healthy_while_starting_and_once_up():
    # docker's healthcheck probes /api/startup-status. while a big library's
    # backup and upgrade run, every other /api path is 503 (the old probe,
    # /api/setup/status, was one of them), which marked the container
    # unhealthy and got it restarted mid-upgrade by autoheal and friends
    from core.startup_application import StartupApplication
    release = threading.Event()

    def load():
        assert release.wait(5)
        return lambda env, start: (start('200 OK', []), [b'normal app'])[1]

    app = StartupApplication(load)
    try:
        assert _request(app, '/api/startup-status')[0] == '200 OK'
        assert _request(app, '/api/setup/status')[0] == '503 Service Unavailable'
    finally:
        release.set()
        assert app.wait(5)
    status, body = _request(app, '/api/startup-status')
    assert status == '200 OK' and b'"ready"' in body
