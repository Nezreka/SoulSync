"""#1504 — maintenance_roots: every tree the maintenance jobs walk.

shared transfer folder first, then every active own-library root. deduped,
nested own-library roots dropped (no double-scan / double-count), the shared
folder never dropped, fail-open to shared-only on any failure.

Hermetic: own_library_roots is faked.
"""

from __future__ import annotations

import os

import pytest

from core.repair_jobs.base import maintenance_roots


@pytest.fixture
def fake_roots(monkeypatch):
    def _set(roots):
        monkeypatch.setattr(
            "core.imports.paths.own_library_roots", lambda: roots)
    return _set


def _n(*parts):
    # canonical form, matching maintenance_roots' realpath normalization
    return os.path.normpath(os.path.realpath(os.path.join(*parts)))


def test_shared_only_when_no_own_libraries(fake_roots):
    fake_roots([])
    assert maintenance_roots("/Transfer") == [_n("/Transfer")]


def test_own_library_roots_appended(fake_roots):
    fake_roots([(2, "/lib/u2"), (3, "/lib/u3")])
    assert maintenance_roots("/Transfer") == [_n("/Transfer"), _n("/lib/u2"), _n("/lib/u3")]


def test_nested_own_root_dropped(fake_roots):
    # a profile pointing its library at the shared folder (or inside it)
    # must not make any job scan the same tree twice
    fake_roots([(2, "/Transfer/u2"), (3, "/lib/u3")])
    assert maintenance_roots("/Transfer") == [_n("/Transfer"), _n("/lib/u3")]


def test_duplicate_roots_deduped(fake_roots):
    fake_roots([(2, "/lib/u2"), (3, "/lib/u2")])
    assert maintenance_roots("/Transfer") == [_n("/Transfer"), _n("/lib/u2")]


def test_nested_transfer_covered_by_outer_own_root(fake_roots):
    # transfer nested under an own root: the own root walk covers it,
    # so no root is walked twice
    fake_roots([(2, "/data")])
    assert maintenance_roots("/data/Transfer") == [_n("/data")]


def test_fail_open_when_helper_blows_up(monkeypatch):
    def _boom():
        raise RuntimeError("no db")
    monkeypatch.setattr("core.imports.paths.own_library_roots", _boom)
    assert maintenance_roots("/Transfer") == [_n("/Transfer")]
