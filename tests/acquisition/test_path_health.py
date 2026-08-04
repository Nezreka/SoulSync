from core.acquisition.path_health import (
    inspect_mapping_configuration,
    inspect_reported_path,
)


def _config(values):
    return lambda key, default=None: values.get(key, default)


def test_mapping_configuration_reports_invalid_and_unmounted_targets(tmp_path):
    readable = tmp_path / "downloads"
    readable.mkdir()
    config_get = _config({
        "download_source.usenet_path_mappings": [
            {"from": "/sab/complete", "to": str(readable)},
            {"from": "/nzb/complete", "to": str(tmp_path / "offline")},
            {"from": "", "to": str(readable)},
            "invalid",
        ],
    })

    health = inspect_mapping_configuration(config_get)

    assert health.to_public_dict() == {
        "configured_count": 4,
        "valid_count": 2,
        "readable_target_count": 1,
        "invalid_indexes": [2, 3],
        "healthy": False,
    }


def test_reported_path_health_distinguishes_direct_mapped_and_unavailable(tmp_path):
    direct = tmp_path / "direct"
    direct.mkdir()
    mapped = tmp_path / "mapped"
    mapped.mkdir()
    config_get = _config({
        "download_source.usenet_path_mappings": [
            {"from": "/sab/complete", "to": str(mapped)},
        ],
    })

    direct_health = inspect_reported_path(
        str(direct), config_get=config_get, resolver=lambda path, _cfg: path)
    mapped_health = inspect_reported_path(
        "/sab/complete/Album",
        config_get=config_get,
        resolver=lambda _path, _cfg: str(mapped),
    )
    unavailable = inspect_reported_path(
        "/sab/complete/Missing",
        config_get=config_get,
        resolver=lambda path, _cfg: path,
    )
    unreadable = inspect_reported_path(
        "/other/Missing",
        config_get=config_get,
        resolver=lambda path, _cfg: path,
    )

    assert direct_health.to_public_dict()["status"] == "direct"
    assert mapped_health.to_public_dict() == {
        "status": "mapped",
        "readable": True,
        "remapped": True,
        "matching_mapping": True,
    }
    assert unavailable.status == "mapping_unavailable"
    assert unreadable.status == "unreadable"


def test_path_health_public_shape_never_contains_paths(tmp_path):
    secret_path = tmp_path / "secret-mount"
    secret_path.mkdir()
    health = inspect_reported_path(
        "smb://user:password@server/music",
        config_get=_config({}),
        resolver=lambda _path, _cfg: str(secret_path),
    )

    public = str(health.to_public_dict())
    assert "password" not in public
    assert str(secret_path) not in public
