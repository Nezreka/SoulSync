"""Opt-in fixture for tests that drive their own synchronous scans."""
import pytest

from core.repair_worker import RepairWorker


@pytest.fixture
def defer_background_refresh_in_synchronous_scan_tests(monkeypatch):
    # These fixtures rescan explicitly with controlled catalog and fingerprint
    # responses. A daemon refresh would outlive the fixture and race its database.
    monkeypatch.setattr(RepairWorker, '_schedule_album_refresh',
                        lambda self, case_id, folder, **kwargs: True)
