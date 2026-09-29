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
