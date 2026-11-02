"""a recording another profile owns doesn't hide your wanted track.

owned_by_recording_sql says "this position's audio is already on disk under
another release". it checked for any live file, not one in the reading
profile's library, so with own libraries profile B's wanted track vanished
from Missing (and read present on its artist page) because profile A owned the
same recording. reference_owners in the same module already scoped it.
"""

from __future__ import annotations

import sqlite3

from core.library2 import recording_links, sql_util


def test_the_recording_check_carries_the_library_scope(monkeypatch):
    monkeypatch.setattr(sql_util, "owner_clause",
                        lambda scope=None, column="owned_f.owner_profile_id": f" AND +{column} = 2")
    sql = recording_links.owned_by_recording_sql(sqlite3.connect(":memory:"), "t")
    assert "+f.owner_profile_id = 2" in sql
