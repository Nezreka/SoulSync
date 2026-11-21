"""the Help line for library coverage: a warning with a way to the folders
when SoulSync reads under half of the media server's music, quiet when there's
nothing to judge."""

from __future__ import annotations

import pytest

from core.help import health


@pytest.fixture(autouse=True)
def _clean():
    health.reset()
    yield
    health.reset()


def _result(pct, total=1000, folders=None):
    return {"server": "plex", "total_tracks": total, "reachable_pct": pct,
            "reachable_tracks": int(total * pct / 100) if pct is not None else 0,
            "folders": folders if folders is not None else
            [{"status": "covered"}, {"status": "not_mounted"}, {"status": "not_library"}]}


def test_low_coverage_warns_and_links_to_the_folders():
    health.configure(library_coverage=lambda: _result(8.0))
    [f] = health.check_library_coverage()
    assert f["severity"] == "warning" and f["id"] == "paths.library_coverage"
    assert f["title"] == "SoulSync can only read 8% of your Plex music"
    assert "2 of Plex's music folders are not SoulSync library folders yet" in f["detail"]
    assert f["fix"] == [{"label": "Open library folders", "kind": "settings", "tab": "library"}]
    assert "—" not in f["detail"]


def test_good_coverage_is_ok():
    health.configure(library_coverage=lambda: _result(92.0))
    [f] = health.check_library_coverage()
    assert f["severity"] == "ok" and "92%" in f["title"]


def test_the_threshold_is_half():
    health.configure(library_coverage=lambda: _result(health.COVERAGE_WARN_PCT))
    assert health.check_library_coverage()[0]["severity"] == "ok"
    health.configure(library_coverage=lambda: _result(health.COVERAGE_WARN_PCT - 0.1))
    assert health.check_library_coverage()[0]["severity"] == "warning"


@pytest.mark.parametrize("result", [
    None, {}, {"server": "", "total_tracks": 1000, "reachable_pct": 1.0},
    _result(None), _result(1.0, total=health.COVERAGE_MIN_TRACKS - 1),
])
def test_quiet_without_anything_to_judge(result):
    health.configure(library_coverage=lambda: result)
    assert health.check_library_coverage() == []


def test_quiet_without_the_provider():
    assert health.check_library_coverage() == []


def test_it_is_in_the_plan():
    assert "library_coverage" in [cid for cid, *_ in health._plan()]


def test_one_folder_reads_singular():
    health.configure(library_coverage=lambda: _result(10.0, folders=[{"status": "not_mounted"}]))
    assert "1 of Plex's music folders is not a SoulSync library folder yet" in health.check_library_coverage()[0]["detail"]
