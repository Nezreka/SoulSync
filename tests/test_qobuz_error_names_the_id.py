"""A refused Qobuz call says which id it was about, never the signature."""

from unittest.mock import MagicMock

import core.qobuz_client as qc


def test_a_400_names_the_album_and_not_the_signature(monkeypatch):
    client = qc.QobuzClient.__new__(qc.QobuzClient)
    client.session = MagicMock()
    client.session.get.return_value = MagicMock(status_code=400, headers={})
    monkeypatch.setattr(client, "is_authenticated", lambda: True, raising=False)
    monkeypatch.setattr(qc, "_qobuz_is_rate_limited", lambda: False)
    monkeypatch.setattr(qc, "_qobuz_throttle", lambda: None)
    logged = []
    monkeypatch.setattr(qc.logger, "warning", lambda msg, *a, **k: logged.append(msg))

    assert client._api_request("album/get", {"album_id": "0060253780866", "extra": "tracks",
                                             "request_sig": "s3cret", "user_auth_token": "t"}) is None
    assert logged and "0060253780866" in logged[0]
    assert "s3cret" not in logged[0] and "user_auth_token" not in logged[0]
