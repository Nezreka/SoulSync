"""Settings connection checks that protect Usenet acquisition recovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from core import connection_test


def _config_get(values):
    def get(key, default=None):
        if key == "usenet_client":
            return {}
        return values.get(key, default)

    return get


def _run_sab_test(*, category_exists: bool):
    values = {
        "usenet_client.type": "sabnzbd",
        "usenet_client.url": "http://sab:8080",
        "usenet_client.api_key": "secret",
        "usenet_client.category": "soulsync",
    }
    adapter = MagicMock()
    adapter.is_configured.return_value = True
    adapter.check_connection = AsyncMock(return_value=True)
    adapter.category_exists = AsyncMock(return_value=category_exists)

    with (
        patch.object(
            connection_test.config_manager,
            "get",
            side_effect=_config_get(values),
        ),
        patch.object(connection_test.config_manager, "set"),
        patch.object(connection_test, "docker_resolve_url", side_effect=lambda value: value),
        patch("core.usenet_clients.adapter_for_type", return_value=adapter),
    ):
        result = connection_test.run_service_test(
            "usenet_client",
            {
                "type": "sabnzbd",
                "url": "http://sab:8080",
                "api_key": "secret",
                "category": "soulsync",
            },
        )
    return result, adapter


def test_sab_connection_test_rejects_missing_acquisition_category() -> None:
    result, adapter = _run_sab_test(category_exists=False)

    assert result == (
        False,
        "Connected to SABnzbd, but category 'soulsync' is not configured "
        "there. Add the same category in SABnzbd before enabling acquisition.",
    )
    adapter.category_exists.assert_awaited_once_with("soulsync")


def test_sab_connection_test_accepts_configured_acquisition_category() -> None:
    result, adapter = _run_sab_test(category_exists=True)

    assert result == (True, "Connected to sabnzbd")
    adapter.category_exists.assert_awaited_once_with("soulsync")
