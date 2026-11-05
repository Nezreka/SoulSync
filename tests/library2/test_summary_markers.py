"""The legacy plex updater's "-updatedAt<date>" marker is not a biography.

It is written into each plex artist's summary as bookkeeping; the library
import copied it as the bio, so 443 artists showed "-updatedAt2026-04-13" on
their page, and real biographies never filled in (Nov 4 2026).
"""

from __future__ import annotations

from core.library2.schema import ensure_library_v2_schema, run_library_v2_backfills
from core.library2.summary_markers import clean_summary, repair_artist_summaries
from tests.lib2_seed import row_conn


def test_a_marker_alone_is_no_biography():
    assert clean_summary("-updatedAt2026-04-13") is None
    assert clean_summary("-IgnoreUpdate\n\n-updatedAt2026-04-13") is None


def test_a_real_biography_keeps_its_text():
    assert clean_summary("An Irish rock band.\n\n-updatedAt2026-04-13") == "An Irish rock band."
    assert clean_summary("Formed in Dublin.\n\n-IgnoreUpdate\n\n-updatedAt2026-01-02") == "Formed in Dublin."


def test_ordinary_text_is_untouched():
    text = "Their 2003 album was up-dated at the label's request -- twice."
    assert clean_summary(text) == text
    assert clean_summary(None) is None


def test_stored_biographies_are_repaired_on_start(tmp_path):
    conn = row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(conn)
    for name, summary in (("A", "-updatedAt2026-04-13"),
                          ("B", "Real bio.\n\n-updatedAt2026-04-13"),
                          ("C", "Untouched bio.")):
        conn.execute("INSERT INTO lib2_artists(name, summary) VALUES(?, ?)", (name, summary))
    conn.commit()

    stats = run_library_v2_backfills(conn, commit=True)

    got = dict(conn.execute("SELECT name, summary FROM lib2_artists").fetchall())
    assert got == {"A": None, "B": "Real bio.", "C": "Untouched bio."}
    assert stats["artist_summary_markers"] == {"cleared": 1, "cleaned": 1}
    assert repair_artist_summaries(conn.cursor()) == {"cleared": 0, "cleaned": 0}
    conn.close()
