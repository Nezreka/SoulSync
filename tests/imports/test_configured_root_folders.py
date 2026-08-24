"""Every configured root folder must be read under the key the settings page
writes, and must resolve to one canonical absolute form.

Two separate defects lived here:

* ``core/imports/file_ops.py`` read ``soulseek.staging_path``. The settings page
  writes ``import.staging_path`` (``webui/static/settings.js``), and every other
  reader uses that key. So the import folder was NOT in the protected-root set
  that issue #976 added to stop the post-import cleanup ``rmdir``-ing it, and the
  self-heal recreated a literal ``./Staging`` instead of the folder the user
  configured.
* A root written relative ("./Staging", the shipped default) was compared and
  stored verbatim, so it never matched the same folder spelled absolutely.
"""

import os

import pytest

import core.imports.file_ops as file_ops
import core.imports.paths as import_paths
import core.imports.staging as staging_mod


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture()
def configured(monkeypatch, tmp_path):
    """The five roots exactly as the settings page stores them."""
    cfg = _Config({
        "import.staging_path": str(tmp_path / "Staging"),
        "soulseek.download_path": str(tmp_path / "downloads"),
        "soulseek.transfer_path": str(tmp_path / "Transfer"),
    })
    monkeypatch.setattr(file_ops, "config_manager", cfg)
    monkeypatch.setattr(staging_mod, "_get_config_manager", lambda: cfg)
    return tmp_path


# ── the import folder is a protected root (#976) ─────────────────────────────

def test_the_configured_import_folder_is_protected(configured):
    roots = file_ops.protected_root_dirs()
    assert str(configured / "Staging") in roots, (
        "the import folder is not protected — the post-import cleanup can rmdir it"
    )


def test_download_and_library_roots_stay_protected(configured):
    roots = file_ops.protected_root_dirs()
    assert str(configured / "downloads") in roots
    assert str(configured / "Transfer") in roots


def test_the_self_heal_recreates_the_configured_folder(configured):
    file_ops.ensure_staging_dir()
    assert (configured / "Staging").is_dir()
    assert not (configured / "." / "Staging" / "Staging").exists()


# ── relative roots resolve to the same string everywhere ─────────────────────

def test_a_relative_import_folder_resolves_absolutely(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = _Config({"import.staging_path": "./Staging",
                   "soulseek.download_path": "./downloads",
                   "soulseek.transfer_path": "./Transfer"})
    monkeypatch.setattr(file_ops, "config_manager", cfg)
    monkeypatch.setattr(staging_mod, "_get_config_manager", lambda: cfg)

    assert staging_mod.get_staging_path() == str(tmp_path / "Staging")
    assert str(tmp_path / "Staging") in file_ops.protected_root_dirs()


def test_config_root_path_is_stable_across_spellings(monkeypatch, tmp_path):
    """The three spellings that used to name one folder must collapse to one."""
    monkeypatch.chdir(tmp_path)
    canonical = str(tmp_path / "Transfer")
    for spelling in ("./Transfer", "Transfer", canonical, canonical + "/"):
        assert import_paths.config_root_path(spelling) == canonical


def test_config_root_path_leaves_an_empty_value_empty():
    assert import_paths.config_root_path("") == ""
    assert import_paths.config_root_path(None) == ""
