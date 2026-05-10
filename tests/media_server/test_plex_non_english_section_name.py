"""Pin Plex scan-trigger + scan-status methods against non-English
section names — issue #535.

Background
----------

A Plex server with the music library named "Música" (Spanish),
"Musique" (French), "Musik" (German), etc. — type still
``artist`` — would have ``_find_music_library`` correctly
auto-detect the section by type. ``self.music_library`` was
populated correctly. Read methods (``get_artists`` etc.) routed
through ``_get_music_sections`` which returned ``[self.music_library]``
and worked.

But ``trigger_library_scan`` and ``is_library_scanning`` ignored
``self.music_library`` and called ``self.server.library.section(library_name)``
with a hardcoded ``"Music"`` default. ``server.library.section('Music')``
raised ``NotFound`` for any server whose music section wasn't
literally named "Music", so post-import scans never fired.

Side effect: wishlist.processing kept reporting "Missing from
media server after sync" for tracks that DID import correctly,
re-adding them to the wishlist forever.

These tests pin both methods through the auto-detected-section
path. Both single-library + all-libraries modes get coverage.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.plex_client import PlexClient


def _make_client(*, all_libraries_mode: bool = False, music_library=None, server=None):
    """Same fixture pattern as test_plex_all_libraries.py — minimal
    construction skipping `__init__` so the test owns the entire
    client state."""
    client = PlexClient.__new__(PlexClient)
    client.server = server
    client.music_library = music_library
    client._all_libraries_mode = all_libraries_mode
    client._connection_attempted = server is not None
    client._is_connecting = False
    client._last_connection_check = 0
    client._connection_check_interval = 30
    return client


# ---------------------------------------------------------------------------
# trigger_library_scan — single-library mode (the broken path in #535)
# ---------------------------------------------------------------------------


class TestTriggerLibraryScanNonEnglishSection:

    @pytest.mark.parametrize('section_name', ['Música', 'Musique', 'Musik', 'Musica', '音乐', 'موسيقى'])
    def test_uses_auto_detected_section_regardless_of_locale(self, section_name):
        """The fix's headline assertion. Auto-detected section's
        update() must be called — NOT a literal 'Music' lookup that
        would NotFound on any non-English server."""
        section = MagicMock(title=section_name)
        server = MagicMock()
        # If the code falls back to literal lookup, raise so the test
        # fails loudly instead of silently calling the wrong method.
        server.library.section.side_effect = AssertionError(
            f"Should NOT call server.library.section() when "
            f"music_library is populated with '{section_name}'"
        )
        client = _make_client(server=server, music_library=section)

        result = client.trigger_library_scan()

        assert result is True
        section.update.assert_called_once()

    def test_falls_back_to_literal_lookup_when_no_auto_detection(self):
        """Backward compat: if music_library is None (test fixtures,
        edge cases where auto-detection hasn't run), fall back to the
        literal `library_name` lookup as before. Default 'Music' arg
        preserved — calling with no kwargs still works."""
        looked_up = MagicMock(title='Music')
        server = MagicMock()
        server.library.section.return_value = looked_up
        client = _make_client(server=server, music_library=None)

        result = client.trigger_library_scan()

        assert result is True
        server.library.section.assert_called_once_with('Music')
        looked_up.update.assert_called_once()

    def test_explicit_library_name_arg_used_only_when_no_auto_detection(self):
        """When music_library is populated, the library_name kwarg is
        ignored — auto-detected section wins. Otherwise the kwarg
        controls the literal lookup."""
        section = MagicMock(title='Música')
        server = MagicMock()
        server.library.section.side_effect = AssertionError("must not fall back")
        client = _make_client(server=server, music_library=section)

        # Pass a non-default library_name — auto-detected wins.
        result = client.trigger_library_scan(library_name='Whatever')

        assert result is True
        section.update.assert_called_once()

    def test_logs_correct_section_label_on_success(self, caplog):
        """Log line must surface the actual section's title (`Música`)
        not the unused library_name default ('Music'). Pre-fix the
        success log read 'Triggered Plex library scan for Music' even
        on Spanish servers — confusing when debugging."""
        section = MagicMock(title='Música')
        client = _make_client(server=MagicMock(), music_library=section)

        with caplog.at_level('INFO', logger='soulsync.plex_client'):
            client.trigger_library_scan()

        assert any('Música' in r.getMessage() for r in caplog.records), (
            f"Expected log to mention 'Música'; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# is_library_scanning — symmetric fix
# ---------------------------------------------------------------------------


class TestIsLibraryScanningNonEnglishSection:

    def test_uses_auto_detected_section_for_refreshing_check(self):
        """`is_library_scanning` must check the auto-detected
        section's `refreshing` attribute — NOT a literal-name
        lookup that fails on non-English servers."""
        section = MagicMock(title='Música', refreshing=True)
        server = MagicMock()
        server.activities.return_value = []
        server.library.section.side_effect = AssertionError("must not fall back")
        client = _make_client(server=server, music_library=section)

        result = client.is_library_scanning()

        assert result is True

    def test_activity_match_uses_resolved_section_title(self):
        """Activity feed match must filter by the resolved section's
        title — NOT the library_name kwarg default ('Music'). Pre-fix
        a Spanish server's "Música" scan activity wouldn't match the
        literal 'music' substring check (well, "música".contains("music")
        IS true here by coincidence — but for "Musique" / "Musik" /
        "音乐" / "موسيقى" the substring miss is real)."""
        section = MagicMock(title='موسيقى', refreshing=False)
        activity = MagicMock(type='library.scan', title='Scanning موسيقى')
        server = MagicMock()
        server.activities.return_value = [activity]
        server.library.section.side_effect = AssertionError("must not fall back")
        client = _make_client(server=server, music_library=section)

        result = client.is_library_scanning()

        assert result is True

    def test_no_match_when_activity_for_unrelated_section(self):
        """Sanity: activity for a DIFFERENT section's scan shouldn't
        cause a false-positive "music library is scanning" reading."""
        section = MagicMock(title='Música', refreshing=False)
        # Activity is for a different section (e.g. Movies)
        activity = MagicMock(type='library.scan', title='Scanning Películas')
        server = MagicMock()
        server.activities.return_value = [activity]
        server.library.section.side_effect = AssertionError("must not fall back")
        client = _make_client(server=server, music_library=section)

        assert client.is_library_scanning() is False

    def test_falls_back_to_literal_lookup_when_no_auto_detection(self):
        """Backward compat: music_library=None → literal section
        lookup by library_name (default 'Music'). Same as before fix."""
        looked_up = MagicMock(title='Music', refreshing=False)
        server = MagicMock()
        server.library.section.return_value = looked_up
        server.activities.return_value = []
        client = _make_client(server=server, music_library=None)

        result = client.is_library_scanning()

        assert result is False
        server.library.section.assert_called_once_with('Music')
