"""Verification-status vocabulary + mapping (DB column / file tag / UI badge)."""

from core.matching.verification_status import (
    VERIFIED, UNVERIFIED, FORCE_IMPORTED, HUMAN_VERIFIED,
    status_from_acoustid_result, status_for_import,
)


def test_acoustid_result_maps_to_status():
    assert status_from_acoustid_result('pass') == VERIFIED
    assert status_from_acoustid_result('skip') == UNVERIFIED
    # disabled / error / unknown -> no claim either way
    assert status_from_acoustid_result('disabled') is None
    assert status_from_acoustid_result('error') is None
    assert status_from_acoustid_result(None) is None


def test_force_import_context_wins_over_acoustid():
    ctx = {'_version_mismatch_fallback': 'instrumental', '_acoustid_result': 'pass'}
    assert status_for_import(ctx) == FORCE_IMPORTED


def test_status_for_import_falls_back_to_acoustid_result():
    assert status_for_import({'_acoustid_result': 'pass'}) == VERIFIED
    assert status_for_import({'_acoustid_result': 'skip'}) == UNVERIFIED
    assert status_for_import({}) is None


def test_approved_quarantine_import_is_human_verified():
    # The user clicked Approve on a quarantine entry — an explicit human
    # decision about THIS file. Outranks both the fallback flag and whatever
    # the (earlier, failed) verification said.
    ctx = {'_approved_quarantine_trigger': 'acoustid',
           '_version_mismatch_fallback': 'instrumental',
           '_acoustid_result': 'fail'}
    assert status_for_import(ctx) == HUMAN_VERIFIED
