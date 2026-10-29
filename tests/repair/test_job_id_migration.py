from core.repair_jobs import JOB_ID_MIGRATIONS, PRESERVED_RETIRED_FINDING_IDS
from core.repair_worker import RepairWorker


class _Config:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def test_legacy_quality_configs_are_left_inert():
    """The quality-upgrade lineage ends rather than migrating onwards.

    quality_upgrade → quality_upgrade_scanner → quality_upgrade_scan all did
    the same thing, and that thing is not a job any more: the wanted projection
    queues an upgrade candidate continuously and `monitoring_list_reconcile`
    mirrors the result. There is nothing to migrate these configs INTO, and
    folding them into an unrelated job would let a long-disabled quality
    scanner switch that job off. So they are left exactly where they are.
    """
    config = _Config({
        "repair.master_enabled": True,
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

    assert "repair.jobs.quality_upgrade_scan" not in config.values
    # Untouched, not deleted: a downgrade must still find its own settings.
    assert config.values["repair.jobs.quality_upgrade"]["enabled"] is True
    assert config.values["repair.jobs.quality_upgrade_scanner"]["interval_hours"] == 6
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
