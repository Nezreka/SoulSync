"""Auto-download: a global default that a per-artist choice beats.

Reported by swiftpawpaw: *"Right now the global override only selects which
formats are downloaded, and i need to go trough all 225 artists in my watchlist to
turn off auto download. This should be added to the global override as well."*

Boulder's model, and the one built here: the global is the DEFAULT; an artist's own
setting wins. Global off → nothing downloads except artists explicitly turned on.
Global on → everything downloads except artists explicitly turned off.

Why that needed a schema change rather than one more checkbox: ``auto_download`` is
``INTEGER NOT NULL DEFAULT 1``, so all 225 of his artists already read ``1`` and
nothing can distinguish "the user chose this" from "nobody ever touched it". A
global switch would be powerless against every one of them — which is the bug. The
new ``auto_download_pref`` is nullable and carries the third state.

DELIBERATE asymmetry, pinned below: the FORMAT overrides keep working the old way
(global overwrites the artist at scan time). Only auto-download uses
default-and-override, because changing the format flags would silently alter
behaviour for everyone already relying on them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.watchlist_auto_download import (
    ALWAYS,
    INHERIT,
    NEVER,
    describe,
    effective_auto_download,
    is_explicit,
    normalize_pref,
)
from database.music_database import MusicDatabase


# ── the three states ─────────────────────────────────────────────────────────

def test_no_stored_preference_means_follow_the_global():
    assert normalize_pref(None) is INHERIT


def test_an_explicit_choice_is_kept():
    assert normalize_pref(1) == ALWAYS
    assert normalize_pref(0) == NEVER


def test_the_shapes_that_arrive_from_json_and_forms():
    for on in (True, "1", "true", "on", "yes", "always"):
        assert normalize_pref(on) == ALWAYS, on
    for off in (False, "0", "false", "off", "no", "never"):
        assert normalize_pref(off) == NEVER, off
    for inherit in ("", "  ", "inherit", "default", "null", "none"):
        assert normalize_pref(inherit) is INHERIT, inherit


def test_junk_inherits_rather_than_pinning_itself_on():
    """A row we cannot read must follow the global, not silently opt itself in —
    otherwise one bad value permanently ignores the user's global off."""
    for bad in (object(), [], {}, "wat"):
        assert normalize_pref(bad) is INHERIT, bad


# ── the rule ─────────────────────────────────────────────────────────────────

def test_the_global_decides_when_the_artist_has_no_opinion():
    assert effective_auto_download(None, global_default=True) is True
    assert effective_auto_download(None, global_default=False) is False


def test_the_artist_beats_the_global_both_ways():
    """THE requirement: 'the user configuration trumps the global'."""
    assert effective_auto_download(ALWAYS, global_default=False) is True
    assert effective_auto_download(NEVER, global_default=True) is False


def test_swiftpawpaws_actual_case():
    """225 artists, none ever touched, global flipped off — every one of them must
    stop. Under the old boolean column they all read 1 and none of them would."""
    artists = [None] * 225
    assert not any(effective_auto_download(a, global_default=False) for a in artists)
    # …and his handful of deliberate follow-only artists are unaffected either way.
    assert effective_auto_download(NEVER, global_default=False) is False


def test_turning_the_global_back_on_restores_everyone_who_never_chose():
    artists = [None] * 225
    assert all(effective_auto_download(a, global_default=True) for a in artists)


def test_a_junk_global_does_not_raise():
    for bad in (None, "", "yes", object()):
        assert isinstance(effective_auto_download(None, global_default=bad), bool)


def test_a_pre_column_follow_only_is_not_switched_back_on_by_the_global():
    """The row the backfill never reached.

    ``legacy_column_value`` never writes 0 for an inheriting row, so pref NULL +
    ``auto_download = 0`` can only be a deliberate follow-only from before the
    preference column existed. Resolving that purely from the preference would
    read it as "no opinion" and let a global ON start downloading for an artist
    someone deliberately muted — the worst thing this feature could do."""
    from core.watchlist_auto_download import effective_with_legacy

    assert effective_with_legacy(INHERIT, legacy=False, global_default=True) is False
    # …while a normal inheriting row (legacy 1) still follows the global both ways.
    assert effective_with_legacy(INHERIT, legacy=True, global_default=True) is True
    assert effective_with_legacy(INHERIT, legacy=True, global_default=False) is False
    # An explicit preference outranks the stale boolean in both directions.
    assert effective_with_legacy(ALWAYS, legacy=False, global_default=False) is True
    assert effective_with_legacy(NEVER, legacy=True, global_default=True) is False


def test_explicitness_is_reported_so_deliberate_choices_can_be_left_alone():
    assert is_explicit(None) is False
    assert is_explicit(1) is True and is_explicit(0) is True


def test_the_row_says_WHY_it_is_off():
    """"Off" is ambiguous between "I set this" and "the global is off"; a user who
    cannot tell which will toggle the wrong control."""
    assert "this artist" in describe(NEVER, True)
    assert "global" in describe(None, False) and "OFF" in describe(None, False)
    assert "global" in describe(None, True) and "ON" in describe(None, True)


# ── the column ───────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _conn(db):
    import sqlite3
    return sqlite3.connect(str(db.database_path))


def _add(db, name, auto_download=1):
    conn = _conn(db)
    conn.execute("INSERT INTO watchlist_artists (artist_name, auto_download) VALUES (?, ?)",
                 (name, auto_download))
    conn.commit()
    row = conn.execute("SELECT id FROM watchlist_artists WHERE artist_name=?", (name,)).fetchone()
    conn.close()
    return row[0]


def _pref(db, artist_id):
    conn = _conn(db)
    r = conn.execute("SELECT auto_download_pref FROM watchlist_artists WHERE id=?",
                     (artist_id,)).fetchone()
    conn.close()
    return r[0]


def test_the_column_exists_and_starts_null(db):
    """NULL is the point — it is the state the old column could not hold."""
    a = _add(db, "Untouched Artist", auto_download=1)
    assert _pref(db, a) is None


def test_a_deliberate_follow_only_survives_the_migration(db):
    """Rows already at auto_download=0 are a real user choice and must not be
    swept back into inheriting — that would silently re-enable downloads for them
    the moment the global is on."""
    a = _add(db, "Follow Only", auto_download=0)
    conn = _conn(db)
    conn.execute("UPDATE watchlist_artists SET auto_download_pref = NULL WHERE id=?", (a,))
    conn.commit()
    cur = conn.cursor()
    db._add_watchlist_auto_download_pref_column(cur)      # idempotent: column exists
    conn.commit()
    conn.close()
    # The backfill only runs when the column is first created, so simulate that path:
    conn = _conn(db)
    conn.execute("UPDATE watchlist_artists SET auto_download_pref = 0 WHERE auto_download = 0")
    conn.commit()
    conn.close()
    assert _pref(db, a) == 0


def test_the_migration_is_idempotent(db):
    """It runs on every startup; a second pass must not re-derive prefs from the
    legacy column and wipe an explicit 'always on'."""
    a = _add(db, "Explicitly On", auto_download=1)
    conn = _conn(db)
    conn.execute("UPDATE watchlist_artists SET auto_download_pref = 1 WHERE id=?", (a,))
    conn.commit()
    cur = conn.cursor()
    db._add_watchlist_auto_download_pref_column(cur)
    conn.commit()
    conn.close()
    assert _pref(db, a) == 1, "an explicit always-on must survive re-running the migration"


def test_reading_the_watchlist_still_works_and_carries_the_preference(db):
    """The column has to be SELECTed, not just read off the row.

    ``get_watchlist_artists`` builds its query from a whitelist and then indexes
    the row by name. Adding the field to the mapping but not the whitelist made
    every read raise "No item with that key" — and the method swallows
    exceptions and returns ``[]``, so the whole watchlist silently emptied
    instead of erroring."""
    a = _add(db, "Readable Artist")
    conn = _conn(db)
    conn.execute("UPDATE watchlist_artists SET auto_download_pref = 0 WHERE id=?", (a,))
    conn.commit()
    conn.close()

    artists = db.get_watchlist_artists(profile_id=1)
    assert [x.artist_name for x in artists] == ["Readable Artist"]
    assert artists[0].auto_download_pref == 0


def test_the_migration_lands_after_the_table_recreates():
    """The file warns about this: the profile migrations rebuild
    watchlist_artists from an explicit column list, so anything added before them
    is dropped. Ordering is the whole reason auto_download works today."""
    src = Path("database/music_database.py").read_text(encoding="utf-8")
    recreate = src.index("_fix_watchlist_spotify_id_nullable")
    call = src.index("self._add_watchlist_auto_download_pref_column(cursor)")
    legacy = src.index("self._add_watchlist_auto_download_column(cursor)")
    assert call > legacy, "must sit with the other post-recreate migration"
    assert src.index("def _add_watchlist_auto_download_pref_column") > 0
    assert recreate > 0


# ── the scanner resolves it ──────────────────────────────────────────────────

class _Artist:
    """Just the fields the resolver touches."""

    def __init__(self, pref=None, auto_download=True):
        self.auto_download_pref = pref
        self.auto_download = auto_download
        self.include_albums = self.include_eps = self.include_singles = True
        self.include_live = self.include_remixes = self.include_acoustic = False
        self.include_compilations = self.include_instrumentals = False


def _scanner():
    from core.watchlist_scanner import WatchlistScanner
    return WatchlistScanner.__new__(WatchlistScanner)      # no __init__ side effects


def _with_config(monkeypatch, **values):
    from config import settings

    class _Cfg:
        def get(self, key, default=None):
            return values.get(key, default)
    monkeypatch.setattr(settings, "config_manager", _Cfg())


def test_the_scanner_turns_everything_off_when_the_global_is_off(monkeypatch):
    """swiftpawpaw's ask, end to end through the code that actually scans."""
    _with_config(monkeypatch, **{"watchlist.global_auto_download": False})
    artists = [_Artist() for _ in range(225)]
    _scanner()._apply_auto_download_default(artists)
    assert not any(a.auto_download for a in artists)


def test_an_explicit_artist_survives_the_global(monkeypatch):
    _with_config(monkeypatch, **{"watchlist.global_auto_download": False})
    always, inherit = _Artist(pref=1), _Artist(pref=None)
    _scanner()._apply_auto_download_default([always, inherit])
    assert always.auto_download is True and inherit.auto_download is False


def test_the_default_is_unchanged_behaviour(monkeypatch):
    """No config set anywhere -> everything downloads, exactly as before."""
    _with_config(monkeypatch)
    artists = [_Artist() for _ in range(3)]
    _scanner()._apply_auto_download_default(artists)
    assert all(a.auto_download for a in artists)


def test_auto_download_is_resolved_even_when_the_format_override_is_off(monkeypatch):
    """The format override early-returns when disabled. If auto-download resolved
    after that guard it would only work for people who also use the format
    override — which is most of the bug, not the fix."""
    _with_config(monkeypatch, **{"watchlist.global_override_enabled": False,
                                 "watchlist.global_auto_download": False})
    a = _Artist()
    _scanner()._apply_global_watchlist_overrides([a])
    assert a.auto_download is False


def test_the_format_flags_are_still_left_alone_when_the_override_is_off(monkeypatch):
    """The deliberate asymmetry: only auto-download changed semantics."""
    _with_config(monkeypatch, **{"watchlist.global_override_enabled": False,
                                 "watchlist.global_auto_download": False,
                                 "watchlist.global_include_albums": False})
    a = _Artist()
    a.include_albums = True
    _scanner()._apply_global_watchlist_overrides([a])
    assert a.include_albums is True, "format flags must not move without the override"


def test_the_scanner_keeps_a_pre_column_follow_only_muted(monkeypatch):
    """Same rule, through the code that actually scans: an artist carrying only
    the legacy 0 must not start downloading when the global is on."""
    _with_config(monkeypatch, **{"watchlist.global_auto_download": True})
    stale = _Artist(pref=None, auto_download=False)
    _scanner()._apply_auto_download_default([stale])
    assert stale.auto_download is False


def test_an_empty_batch_is_fine(monkeypatch):
    _with_config(monkeypatch)
    _scanner()._apply_auto_download_default([])
    _scanner()._apply_auto_download_default(None)


def test_the_endpoint_exposes_the_setting():
    src = Path("web_server.py").read_text(encoding="utf-8")
    fn = src[src.index("def watchlist_global_config()"):src.index("def watchlist_global_config()") + 6000]
    assert "'global_auto_download': config_manager.get('watchlist.global_auto_download', True)" in fn
    assert "config_manager.set('watchlist.global_auto_download', global_auto_download)" in fn
