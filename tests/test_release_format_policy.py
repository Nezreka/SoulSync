"""Release-format policy for the torrent path (#1149, Zombiehamser).

A lossless profile with fallback disabled still enqueued MP3 and mixed
releases, because torrent candidate selection never consulted a quality
profile. The releases downloaded, filled queue slots, and were rejected at
import.

The acceptance matrix in the report is the spine of this file: FLAC accepted,
MP3/Opus/M4A rejected, mixed rejected unless explicitly allowed, unknown
rejected rather than falling back to MP3.
"""

from __future__ import annotations

import pytest

from core.quality.release_format import (
    allowed_formats_from_profile,
    evaluate_release,
    formats_in_files,
    formats_in_title,
)

FLAC_ONLY = {'flac'}


# ── reading a title ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('title,expected', [
    ('Kendrick Lamar - GNX (2024) [FLAC]', {'flac'}),
    ('Artist - Album [MP3 320]', {'mp3'}),
    ('Artist - Album (Opus)', {'opus'}),
    ('Artist - Album [M4A]', {'aac'}),
    ('Artist - Album [AAC]', {'aac'}),
    ('Artist - Album [OGG]', {'ogg'}),
    ('Artist - Album [WAV 24-96]', {'wav'}),
    ('Artist - Album [AIFF]', {'wav'}),
    ('Artist - Album [DSF]', {'dsf'}),
    ('Artist - Album [DFF]', {'dsf'}),
    ('Artist - Album [DSD256]', {'dsf'}),
])
def test_a_named_format_is_read_from_the_title(title, expected):
    assert formats_in_title(title) == expected


def test_a_lossless_claim_with_no_codec_reads_as_flac():
    """'24bit' / 'Lossless' / 'Hi-Res' assert losslessness without naming a
    codec. On music trackers that means FLAC, and a file list will correct us
    when we have one."""
    assert formats_in_title('Artist - Album 24bit WEB') == {'flac'}
    assert formats_in_title('Artist - Album [Lossless]') == {'flac'}
    assert formats_in_title('Artist - Album Hi-Res') == {'flac'}


def test_a_bare_bitrate_reads_as_lossy():
    assert formats_in_title('Artist - Album V0') == {'mp3'}
    assert formats_in_title('Artist - Album 320kbps') == {'mp3'}


def test_a_title_advertising_both_reads_as_both():
    """Noticing the mix is the point — resolving to whichever matched first
    is how a mixed release slips through as FLAC."""
    assert formats_in_title('Artist - Album [FLAC + MP3]') == {'flac', 'mp3'}


def test_an_unreadable_title_says_nothing_rather_than_mp3():
    """The original bug. _guess_quality_from_title returned 'mp3' for anything
    it could not read, so an ambiguous release was labelled lossy and ranked
    instead of being rejected."""
    assert formats_in_title('Artist - Album (2024)') == set()
    assert formats_in_title('') == set()


# ── reading a file list ──────────────────────────────────────────────────────

def test_audio_files_decide_the_format():
    assert formats_in_files(['01 - Track.flac', '02 - Track.flac']) == {'flac'}


def test_non_audio_files_are_ignored():
    """A release is not lossy because it ships a JPEG."""
    files = ['cover.jpg', 'folder.png', 'info.nfo', 'rip.log', 'album.cue',
             '01 - Track.flac']

    assert formats_in_files(files) == {'flac'}


def test_a_mixed_file_list_reports_both():
    assert formats_in_files(['01.flac', '02.mp3']) == {'flac', 'mp3'}


def test_an_empty_or_art_only_release_determines_nothing():
    assert formats_in_files([]) == set()
    assert formats_in_files(['cover.jpg', 'readme.txt']) == set()


# ── the profile ──────────────────────────────────────────────────────────────

def test_a_flac_only_profile_with_no_fallback_allows_only_flac():
    profile = {
        'ranked_targets': [
            {'label': 'FLAC 24-bit', 'format': 'flac', 'bit_depth': 24},
            {'label': 'FLAC 16-bit', 'format': 'flac', 'bit_depth': 16},
        ],
        'fallback_enabled': False,
    }

    assert allowed_formats_from_profile(profile) == {'flac'}


def test_fallback_enabled_means_anything_goes():
    """Same equivalence the import guard already uses. This is why the feature
    needs no new setting — and why turning fallback on must not be quietly
    overridden by a stricter torrent filter."""
    profile = {
        'ranked_targets': [{'format': 'flac'}],
        'fallback_enabled': True,
    }

    assert allowed_formats_from_profile(profile) is None


def test_no_targets_means_anything_goes():
    assert allowed_formats_from_profile({'ranked_targets': [], 'fallback_enabled': False}) is None


def test_a_mixed_ladder_allows_every_format_on_it():
    profile = {
        'ranked_targets': [{'format': 'flac'}, {'format': 'mp3', 'min_bitrate': 320}],
        'fallback_enabled': False,
    }

    assert allowed_formats_from_profile(profile) == {'flac', 'mp3'}


def test_junk_in_gives_permissive_out():
    """A profile we cannot read must not silently block every download."""
    assert allowed_formats_from_profile(None) is None
    assert allowed_formats_from_profile({}) is None


# ── the acceptance matrix from the report ────────────────────────────────────

def test_a_flac_release_is_accepted():
    ok, _ = evaluate_release(FLAC_ONLY, 'Artist - Album [FLAC]')

    assert ok is True


@pytest.mark.parametrize('title', [
    'Artist - Album [MP3 320]',
    'Artist - Album (Opus)',
    'Artist - Album [M4A]',
    'Artist - Album [AAC]',
])
def test_a_lossy_only_release_is_rejected(title):
    ok, reason = evaluate_release(FLAC_ONLY, title)

    assert ok is False
    assert 'profile allows flac' in reason


def test_a_mixed_release_is_rejected_by_default():
    ok, reason = evaluate_release(FLAC_ONLY, 'Artist - Album [FLAC + MP3]')

    assert ok is False
    assert 'mixed' in reason


def test_a_mixed_release_is_accepted_when_the_policy_says_so():
    ok, _ = evaluate_release(FLAC_ONLY, 'Artist - Album [FLAC + MP3]', allow_mixed=True)

    assert ok is True


def test_an_unknown_release_is_rejected_rather_than_assumed_lossy():
    """The headline of the report: 'if the release format cannot be determined
    reliably, reject it rather than falling back to MP3'."""
    ok, reason = evaluate_release(FLAC_ONLY, 'Artist - Album (2024)')

    assert ok is False
    assert 'undetermined' in reason


def test_exact_mp3_category_is_preserved_by_the_strict_gate():
    ok, reason = evaluate_release(
        {'mp3'},
        'Artist - Album (2024)',
        categories=[3000, 3010],
    )

    assert ok is True
    assert 'mp3' in reason


def test_a_named_codec_outranks_a_generic_mp3_category():
    """3010 is Audio/MP3, and this used to read as a contradiction.

    It was treated as codec evidence equal to the title, so a FLAC torrent
    filed under 3010 became a mixed flac/mp3 release and a lossless-only
    profile refused it. Plenty of indexers map their whole music category to
    3010, so it says which bucket the indexer used, not what this release is.
    The title names the release and wins; the category only fills a title that
    said nothing.
    """
    assert evaluate_release({'flac'}, 'Artist - Album [FLAC]', categories=[3010])[0] is True
    assert evaluate_release({'mp3'}, 'Artist - Album [FLAC]', categories=[3010])[0] is False
    # A title that names nothing still takes the category's word for it.
    assert evaluate_release({'mp3'}, 'Artist - Album (2019)', categories=[3010])[0] is True


def test_lossless_category_disproves_a_lossy_title_without_inventing_flac():
    assert evaluate_release(
        {'mp3'}, 'Artist - Album [MP3 320]', categories=[3040]
    )[0] is False
    assert evaluate_release(
        {'flac'}, 'Artist - Album (2024)', categories=[3040]
    )[0] is False


def test_a_permissive_profile_accepts_everything_including_unknown():
    """A strict torrent filter must not appear for users who never asked."""
    for title in ('Artist - Album [MP3]', 'Artist - Album (2024)', ''):
        ok, _ = evaluate_release(None, title)
        assert ok is True


# ── evidence beats the title ─────────────────────────────────────────────────

def test_the_file_list_overrides_a_lying_title():
    """'Prefer a release with verified FLAC files over a title-based guess'."""
    ok, reason = evaluate_release(
        FLAC_ONLY, 'Artist - Album [FLAC]', file_names=['01.mp3', '02.mp3'])

    assert ok is False
    assert 'file list' in reason


def test_the_file_list_rescues_a_release_whose_title_says_nothing():
    ok, reason = evaluate_release(
        FLAC_ONLY, 'Artist - Album (2024)', file_names=['01.flac'])

    assert ok is True
    assert 'file list' in reason


@pytest.mark.parametrize('file_name,allowed', [
    ('01.wav', {'wav'}),
    ('01.aiff', {'wav'}),
    ('01.aifc', {'wav'}),
    ('01.dsf', {'dsf'}),
    ('01.dff', {'dsf'}),
])
def test_supported_lossless_files_use_profile_canonical_formats(file_name, allowed):
    ok, reason = evaluate_release(
        allowed,
        'Artist - Album (2024)',
        file_names=[file_name],
    )

    assert ok is True, reason


def test_an_art_only_file_list_falls_back_to_the_title():
    """No audio in the list is not evidence of anything, so the title still
    gets its say rather than the release being rejected as undetermined."""
    ok, _ = evaluate_release(
        FLAC_ONLY, 'Artist - Album [FLAC]', file_names=['cover.jpg'])

    assert ok is True


def test_the_reason_names_what_was_found_and_what_was_wanted():
    """The reason is surfaced to the user and drives the fallback chain, so it
    has to say more than 'rejected'."""
    ok, reason = evaluate_release(FLAC_ONLY, 'Artist - Album [MP3 320]')

    assert ok is False
    assert 'mp3' in reason and 'flac' in reason


# ── the picker gate (#1149) ──────────────────────────────────────────────────
#
# Quality was the SECOND sort key after seeders, so a well-seeded MP3 rip beat
# a FLAC rip with fewer seeders whatever the profile said. Sorting cannot
# express "never this", only "prefer that" — which is why this is a veto and
# not a heavier weight.

from types import SimpleNamespace       # noqa: E402

from core.download_plugins.album_bundle import pick_best_album_release  # noqa: E402


def _rel(title, seeders=10, size=300_000_000, **extra):
    return SimpleNamespace(title=title, seeders=seeders, size=size,
                           grabs=None, **extra)


def _flac_or_bust(title):
    return 'flac' if 'flac' in title.lower() else 'mp3'


def test_a_better_seeded_mp3_no_longer_beats_a_flac_release():
    """The bug in one line."""
    mp3 = _rel('Artist - Album [MP3 320]', seeders=900)
    flac = _rel('Artist - Album [FLAC]', seeders=3)

    picked = pick_best_album_release(
        [mp3, flac], _flac_or_bust, allowed_formats={'flac'})

    assert picked is flac


def test_an_all_lossy_field_refuses_rather_than_picking_the_least_bad():
    """Returning None is what makes the caller fall back to the next source
    instead of queueing something the import will throw away."""
    pool = [_rel('Artist - Album [MP3 320]', seeders=900),
            _rel('Artist - Album (Opus)', seeders=400)]

    assert pick_best_album_release(pool, _flac_or_bust, allowed_formats={'flac'}) is None


def test_an_undetermined_release_is_not_picked_under_a_strict_profile():
    pool = [_rel('Artist - Album (2024)', seeders=900)]

    assert pick_best_album_release(pool, _flac_or_bust, allowed_formats={'flac'}) is None


def test_a_mixed_release_is_refused():
    pool = [_rel('Artist - Album [FLAC + MP3]', seeders=900)]

    assert pick_best_album_release(pool, _flac_or_bust, allowed_formats={'flac'}) is None


def test_a_verified_file_list_beats_a_lying_title():
    liar = _rel('Artist - Album [FLAC]', seeders=900,
                file_names=['01.mp3', '02.mp3'])
    honest = _rel('Artist - Album [FLAC]', seeders=2,
                  file_names=['01.flac'])

    picked = pick_best_album_release(
        [liar, honest], _flac_or_bust, allowed_formats={'flac'})

    assert picked is honest


def test_no_profile_restriction_leaves_the_old_behaviour_exactly_as_it_was():
    """Users who allow lossy must see no change at all — including the
    seeders-first ordering they have today."""
    mp3 = _rel('Artist - Album [MP3 320]', seeders=900)
    flac = _rel('Artist - Album [FLAC]', seeders=3)

    assert pick_best_album_release([mp3, flac], _flac_or_bust) is mp3
    assert pick_best_album_release([mp3, flac], _flac_or_bust, allowed_formats=None) is mp3


def test_the_format_gate_runs_before_the_seeder_gate():
    """Both can be true at once. "nothing in the right format" is the
    actionable message, so it must be the one that wins."""
    pool = [_rel('Artist - Album [MP3 320]', seeders=0)]

    assert pick_best_album_release(
        pool, _flac_or_bust, allowed_formats={'flac'}, min_seeders=1) is None


def test_a_profile_allowing_mp3_still_takes_mp3():
    pool = [_rel('Artist - Album [MP3 320]', seeders=50)]

    picked = pick_best_album_release(
        pool, _flac_or_bust, allowed_formats={'flac', 'mp3'})

    assert picked is pool[0]


def test_album_picker_honors_exact_mp3_category_for_a_bare_title():
    release = _rel(
        'Artist - Album (2024)',
        seeders=50,
        categories=[3000, 3010],
    )

    assert pick_best_album_release(
        [release], _flac_or_bust, allowed_formats={'mp3'}
    ) is release


# ── the per-track path (#1149) ───────────────────────────────────────────────
#
# Declining here is the download engine's "try the next source" contract
# (core/download_engine/engine.py), so a strict-lossless user falls through to
# a source that can satisfy them rather than queueing a release the import
# guard will throw away.

from unittest.mock import patch          # noqa: E402

from core.download_plugins.candidate_store import get_candidate_store  # noqa: E402
from core.download_plugins.torrent import (  # noqa: E402
    _FILENAME_SEP,
    _encode_candidate,
    TorrentDownloadPlugin,
)


def _queued_filename(title, categories=None):
    token = get_candidate_store().put(
        _encode_candidate('http://indexer/x.torrent', 'magnet:?xt=urn:btih:abc'),
        metadata={'categories': list(categories or [])},
    )
    return f"{token}{_FILENAME_SEP}{title}"


def _plugin():
    plugin = TorrentDownloadPlugin()
    plugin.is_configured = lambda: True
    return plugin


def _download(plugin, title, allowed, categories=None):
    import asyncio
    with patch('core.download_plugins.torrent.profile_allowed_formats',
               return_value=allowed):
        with patch.object(plugin, '_download_thread', lambda *a, **k: None):
            return asyncio.new_event_loop().run_until_complete(
                plugin.download(
                    'torrent',
                    _queued_filename(title, categories=categories),
                    0,
                ))


def test_a_lossy_track_is_declined_so_the_next_source_gets_a_turn():
    assert _download(_plugin(), 'Artist - Album [MP3 320]', {'flac'}) is None


def test_an_undetermined_track_is_declined_too():
    assert _download(_plugin(), 'Artist - Album (2024)', {'flac'}) is None


def test_a_flac_track_proceeds():
    assert _download(_plugin(), 'Artist - Album [FLAC]', {'flac'}) is not None


def test_a_bare_category_identified_mp3_track_proceeds():
    assert _download(
        _plugin(),
        'Artist - Album (2024)',
        {'mp3'},
        categories=[3010],
    ) is not None


def test_a_permissive_profile_changes_nothing_on_this_path():
    """The guarantee for everyone who allows lossy: identical behaviour."""
    plugin = _plugin()

    assert _download(plugin, 'Artist - Album [MP3 320]', None) is not None
    assert _download(plugin, 'Artist - Album (2024)', None) is not None
