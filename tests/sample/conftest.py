"""Sample-test isolation: saved chops now render into configured sample
folders (Phase 6). Redirect those folders into tmp_path so no test ever
writes into ./samples under the pytest working directory."""

import pytest


@pytest.fixture(autouse=True)
def sample_tmp_folder(tmp_path, monkeypatch):
    from core.sample import folders as sample_folders

    dest = tmp_path / "samples"
    monkeypatch.setattr(
        sample_folders, "_configured_paths", lambda: [str(dest)]
    )
    return dest


class _EmptyConfig:
    def get(self, key, default=None):
        return default


@pytest.fixture(autouse=True)
def isolated_sample_resolver(monkeypatch):
    """no leaked resolver in, none out: web_server's boot wiring (if an
    earlier test imported it) can't steer these tests, and a test's own
    configure() call is undone afterwards."""
    from core.sample import worker as sample_worker

    monkeypatch.setattr(sample_worker, "_resolve_path_fn", None)
    monkeypatch.setattr(sample_worker, "_config_manager", _EmptyConfig())
    monkeypatch.setattr(sample_worker, "_resolve_cache", {})
