"""Which folder the auto-import scan actually reads.

``_resolve_staging_path`` was the only staging consumer that did not use the
shared resolver: it ran a bare ``isdir()`` on the configured value and then
silently fell back to ``./Staging`` / ``/app/Staging``. A configured path the
shared resolver would have handled (a Windows drive letter under Docker, a
``~``, anything relative to a different CWD) failed that ``isdir()``, so the
scan read a DIFFERENT folder, found nothing in it, and logged
"0 candidates in ./Staging" forever — while the Albums tab, which does use the
shared resolver, read the user's real files. Reported from Docker on TrueNAS,
Sept 22 2026.
"""

from __future__ import annotations

import os

import pytest

from core.auto_import_worker import AutoImportWorker


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture
def worker():
    w = AutoImportWorker.__new__(AutoImportWorker)
    w.staging_path = './Staging'
    w._config_manager = None
    w._warned_staging_fallbacks = set()
    return w


def test_a_configured_path_is_used_as_given(tmp_path, worker):
    staging = tmp_path / 'music-staging'
    staging.mkdir()
    worker._config_manager = _Config({'import.staging_path': str(staging)})

    assert worker._resolve_staging_path() == str(staging)


def test_a_relative_path_resolves_instead_of_being_rejected(tmp_path, worker, monkeypatch):
    """The shipped default is relative. Resolving it against the CWD is what
    the other consumers do; the bare isdir() used to depend on where the
    process happened to be started from."""
    (tmp_path / 'Staging').mkdir()
    monkeypatch.chdir(tmp_path)
    worker._config_manager = _Config({'import.staging_path': './Staging'})

    resolved = worker._resolve_staging_path()
    assert resolved == str(tmp_path / 'Staging')
    assert os.path.isabs(resolved), 'a relative root must not reach the filesystem verbatim'


def test_a_tilde_path_is_expanded(tmp_path, worker, monkeypatch):
    home = tmp_path / 'home'
    (home / 'Staging').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(os.path, 'expanduser',
                        lambda p: p.replace('~', str(home), 1) if p.startswith('~') else p)
    worker._config_manager = _Config({'import.staging_path': '~/Staging'})

    assert worker._resolve_staging_path() == str(home / 'Staging')


# ── the silent substitution ──

def test_an_unusable_configured_path_warns_before_falling_back(tmp_path, worker, monkeypatch, caplog):
    """The heart of the bug: the scan read ./Staging and said nothing about
    the folder the user actually configured."""
    (tmp_path / 'Staging').mkdir()
    monkeypatch.chdir(tmp_path)
    worker._config_manager = _Config({'import.staging_path': '/mnt/nas/music/staging'})

    with caplog.at_level('WARNING'):
        resolved = worker._resolve_staging_path()

    assert resolved == str(tmp_path / 'Staging')
    logged = ' '.join(r.getMessage() for r in caplog.records)
    assert '/mnt/nas/music/staging' in logged, 'the warning must name the configured path'
    assert 'PUID' in logged, 'a bind mount is the usual cause; say so'


def test_the_fallback_warning_is_not_repeated_every_cycle(tmp_path, worker, monkeypatch, caplog):
    (tmp_path / 'Staging').mkdir()
    monkeypatch.chdir(tmp_path)
    worker._config_manager = _Config({'import.staging_path': '/mnt/nas/music/staging'})

    with caplog.at_level('WARNING'):
        for _ in range(4):
            worker._resolve_staging_path()

    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 1, 'scan runs on a timer; one warning per pair, not per cycle'


def test_a_path_that_exists_but_is_not_a_directory_says_which(tmp_path, worker, monkeypatch, caplog):
    (tmp_path / 'Staging').mkdir()
    not_a_dir = tmp_path / 'staging.txt'
    not_a_dir.write_text('x')
    monkeypatch.chdir(tmp_path)
    worker._config_manager = _Config({'import.staging_path': str(not_a_dir)})

    with caplog.at_level('WARNING'):
        worker._resolve_staging_path()

    logged = ' '.join(r.getMessage() for r in caplog.records)
    assert 'exists but is not a readable directory' in logged


def test_nothing_usable_anywhere_returns_none_and_explains(tmp_path, worker, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)  # no ./Staging here
    worker._config_manager = _Config({'import.staging_path': '/mnt/nas/music/staging'})

    with caplog.at_level('WARNING'):
        assert worker._resolve_staging_path() is None

    logged = ' '.join(r.getMessage() for r in caplog.records)
    assert '/mnt/nas/music/staging' in logged
    assert 'Nothing will be imported' in logged


def test_a_working_configured_path_never_warns(tmp_path, worker, monkeypatch, caplog):
    staging = tmp_path / 'music-staging'
    staging.mkdir()
    (tmp_path / 'Staging').mkdir()
    monkeypatch.chdir(tmp_path)
    worker._config_manager = _Config({'import.staging_path': str(staging)})

    with caplog.at_level('WARNING'):
        assert worker._resolve_staging_path() == str(staging)

    assert [r for r in caplog.records if r.levelname == 'WARNING'] == []
