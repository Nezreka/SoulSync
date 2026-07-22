from core.repair_jobs import JOB_ID_MIGRATIONS, PRESERVED_RETIRED_FINDING_IDS
from core.repair_worker import RepairWorker


class _Config:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def test_legacy_quality_configs_merge_into_review_mode():
    config = _Config({
        "repair.master_enabled": True,
        "repair.jobs.quality_upgrade.settings": {
            "include_lossless": True,
            "shared": "older",
        },
        "repair.jobs.quality_upgrade.enabled": True,
        "repair.jobs.quality_upgrade": {
            "enabled": True,
            "interval_hours": 24,
            "settings": {"include_lossless": True, "shared": "older"},
        },
        "repair.jobs.quality_upgrade_scanner": {
            "enabled": False,
            "interval_hours": 6,
            "settings": {"shared": "newer", "minimum_bitrate": 256},
        },
    })
    worker = RepairWorker(database=None)

    worker.set_config_manager(config)

    migrated = config.values["repair.jobs.quality_upgrade_scan"]
    assert migrated["enabled"] is True
    assert migrated["interval_hours"] == 6
    assert migrated["settings"] == {
        "include_lossless": True,
        "shared": "newer",
        "minimum_bitrate": 256,
        "mode": "review",
    }
    assert worker.enabled is True


def test_discography_config_and_manual_ids_have_safe_compatibility():
    config = _Config({
        "repair.jobs.discography_backfill": {
            "enabled": True,
            "interval_hours": 12,
            "settings": {"include_singles": False},
        },
    })
    RepairWorker(database=None).set_config_manager(config)

    migrated = config.values["repair.jobs.monitored_discography_refresh"]
    assert migrated["enabled"] is True
    assert migrated["interval_hours"] == 12
    assert migrated["settings"]["mode"] == "review"
    assert migrated["settings"]["include_singles"] is False
    assert JOB_ID_MIGRATIONS["discography_backfill"] == "monitored_discography_refresh"
    assert "discography_backfill" in PRESERVED_RETIRED_FINDING_IDS
