"""Regression tests for issue #1504 Phase 7 — AcoustID relocate carries the
owner through staging via a sidecar JSON breadcrumb.

- relocate_mismatch_to_staging writes <file>.soulsync-owner.json when
  owner_profile_id is given
- no breadcrumb when owner is None (shared behavior unchanged)
"""
import json
import os
import tempfile

from core.repair_jobs.relocate import relocate_mismatch_to_staging


def _noop(*a, **kw):
    return {'success': True}


def test_relocate_writes_owner_breadcrumb():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, 'song.mp3')
        with open(src, 'wb') as f:
            f.write(b'\x00' * 100)
        staging = os.path.join(tmp, 'staging')
        os.makedirs(staging)

        dropped = []
        dest = relocate_mismatch_to_staging(
            src, staging, None,
            write_tags=_noop,
            move_file=lambda s, d: os.rename(s, d),
            drop_db_row=lambda: dropped.append(True),
            exists=os.path.exists,
            owner_profile_id=7,
        )
        assert dropped == [True]
        sidecar = dest + ".soulsync-owner.json"
        assert os.path.isfile(sidecar)
        with open(sidecar) as f:
            crumb = json.load(f)
        assert crumb['owner_profile_id'] == 7


def test_relocate_no_breadcrumb_when_shared():
    """No owner -> no sidecar; default behavior byte-identical."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, 'song.mp3')
        with open(src, 'wb') as f:
            f.write(b'\x00' * 100)
        staging = os.path.join(tmp, 'staging')
        os.makedirs(staging)

        dest = relocate_mismatch_to_staging(
            src, staging, None,
            write_tags=_noop,
            move_file=lambda s, d: os.rename(s, d),
            drop_db_row=lambda: None,
            exists=os.path.exists,
            owner_profile_id=None,
        )
        assert not os.path.exists(dest + ".soulsync-owner.json")
