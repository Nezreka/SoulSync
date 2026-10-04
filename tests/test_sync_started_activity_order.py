"""The "Sync Started" activity must only fire when a sync actually starts.

Issue #1455: ``start_playlist_sync_from_payload`` logged "Sync Started" BEFORE
the in-progress guard, so a duplicate click that was rejected with 409 still
showed up in activity as if a sync had begun.

Importing ``api/source_playlists.py`` drags the whole app (and third-party
modules this environment doesn't have), so — following the precedent in
``test_deezer_playlist_sync_status.py`` — this pins the ordering structurally:
within the function, the activity call must come AFTER the 409 guard return.
"""

from __future__ import annotations

import re

SOURCE = "api/source_playlists.py"


def _function_body():
    with open(SOURCE, encoding="utf-8") as handle:
        src = handle.read()
    match = re.search(
        r"^def start_playlist_sync_from_payload\(.*?\):\n", src, re.M
    )
    assert match, "start_playlist_sync_from_payload not found"
    rest = src[match.end():]
    end = re.search(r"^(?:def |@bp\.route)", rest, re.M)
    return rest[: end.start()] if end else rest


def test_sync_started_activity_fires_after_in_progress_guard():
    body = _function_body()
    guard = body.find('), 409')
    assert guard != -1, "the 409 already-in-progress guard is gone?"
    activity = body.find('add_activity_item("", "Sync Started"')
    assert activity != -1, "the Sync Started activity call is gone?"
    assert activity > guard, (
        "Sync Started must fire AFTER the in-progress 409 guard (#1455): "
        "rejected duplicate clicks must not log a sync that never ran"
    )


def test_exactly_one_sync_started_activity_call():
    body = _function_body()
    calls = re.findall(r'add_activity_item\(\s*""\s*,\s*"Sync Started"', body)
    assert len(calls) == 1, f"expected one Sync Started activity call, found {len(calls)}"
