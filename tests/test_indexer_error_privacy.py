"""HTTP failure diagnostics must not expose indexer/client credentials."""
import logging
from unittest.mock import MagicMock

import pytest
import requests
from core.usenet_clients.sabnzbd import SABnzbdAdapter

SECRET = 'synthetic-private-client-key'
SIGNATURE = 'synthetic-signed-link-value'
MESSAGE = f'Failed GET /api?apikey={SECRET}&name=https%3A%2F%2Findexer.invalid%2Fnzb%3Flink%3D{SIGNATURE}'


@pytest.mark.parametrize('method', ['get', 'post'])
@pytest.mark.parametrize('failure', ['request', 'json'])
def test_sab_error_logs_exclude_nested_signed_urls(monkeypatch, caplog, method, failure):
    import core.usenet_clients.sabnzbd as module
    adapter = SABnzbdAdapter.__new__(SABnzbdAdapter)
    adapter._url, adapter._api_key = 'http://sab.invalid', SECRET
    if failure == 'request':
        def request(*args, **kwargs): raise requests.ConnectionError(MESSAGE)
    else:
        response = MagicMock(ok=True)
        response.json.side_effect = ValueError(MESSAGE)
        request = lambda *args, **kwargs: response
    monkeypatch.setattr(module.http_requests, method, request)
    module.logger.addHandler(caplog.handler)
    try:
        caplog.set_level(logging.ERROR)
        call = adapter._call_sync if method == 'get' else adapter._post_sync
        assert call('addurl', name='https://indexer.invalid/synthetic.nzb') is None
    finally:
        module.logger.removeHandler(caplog.handler)
    assert caplog.records
    assert SECRET not in caplog.text and SIGNATURE not in caplog.text
    assert '/api?' not in caplog.text
