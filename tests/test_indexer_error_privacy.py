"""HTTP failure diagnostics must not expose indexer/client credentials."""
import logging
import traceback
from unittest.mock import MagicMock

import pytest
import requests
from core.prowlarr_client import ProwlarrClient, ProwlarrSearchError
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


@pytest.mark.parametrize('failure', ['request', 'timeout', 'json'])
def test_prowlarr_errors_do_not_expose_http_exception_urls(monkeypatch, caplog, failure):
    import core.prowlarr_client as module
    client = ProwlarrClient.__new__(ProwlarrClient)
    client._url, client._api_key = 'http://prowlarr.invalid', SECRET
    if failure == 'json':
        response = MagicMock(ok=True, status_code=200)
        response.json.side_effect = ValueError(MESSAGE)
        request = lambda *args, **kwargs: response
    else:
        kind = requests.Timeout if failure == 'timeout' else requests.ConnectionError
        def request(*args, **kwargs): raise kind(MESSAGE)
    monkeypatch.setattr(module.http_requests, 'get', request)
    module.logger.addHandler(caplog.handler)
    try:
        caplog.set_level(logging.ERROR)
        with pytest.raises(ProwlarrSearchError) as caught:
            client._api_get('search', raise_on_error=True)
    finally:
        module.logger.removeHandler(caplog.handler)
    rendered = caplog.text + ''.join(traceback.format_exception(caught.value))
    assert caplog.records
    assert SECRET not in rendered and SIGNATURE not in rendered
