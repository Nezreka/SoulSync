"""A repack of the same quality is the better copy — Lidarr's Revision, here.

Lidarr parses PROPER / REPACK / vN / REAL out of a release title and compares
it only when the quality itself ties, so a corrected rip wins over the original
without ever outranking a better format. Nothing in SoulSync read those words,
so a repack and the broken rip it replaces looked identical.
"""

import pytest

from core.quality.release_format import release_revision


@pytest.mark.parametrize('title, version, is_repack', [
    ('Artist - Album [FLAC]', 1, False),
    ('Artist - Album PROPER [FLAC]', 2, False),
    ('Artist - Album REPACK [FLAC]', 2, True),
    ('Artist - Album RERIP [FLAC]', 2, True),
    ('Artist - Album [v2] [FLAC]', 2, False),
    ('Artist - Album REPACK2 [FLAC]', 3, True),
])
def test_revision_matrix(title, version, is_repack):
    revision = release_revision(title)

    assert (revision.version, revision.is_repack) == (version, is_repack)


def test_real_outranks_a_higher_version():
    """Lidarr compares Real before Version, and so must the ordering here."""
    real = release_revision('Artist - Album REAL PROPER [FLAC]')
    versioned = release_revision('Artist - Album [v5] [FLAC]')

    assert real.rank > versioned.rank


def test_real_is_case_sensitive():
    """Lowercase "real" is an ordinary word in album titles."""
    assert release_revision('Artist - For Real [FLAC]').real == 0


def test_mp3_is_not_a_version_marker():
    """``MP3`` ends in a digit followed by nothing; it is not "v3"."""
    assert release_revision('Artist - Album [MP3 320]').version == 1


def test_an_all_caps_title_does_not_get_a_free_real_bump():
    """REAL is matched case sensitively so the ordinary word does not count.

    That only works while the title has a case to read. Plenty of indexers
    post everything upper cased, and there `REAL` says nothing, so a release
    called THE REAL THING was outranking a genuinely better seeded one.
    """
    assert release_revision('ARTIST - THE REAL THING 2020 FLAC').real == 0
    assert release_revision('Artist - Album REAL PROPER [FLAC]').real == 1


@pytest.mark.parametrize('title', [
    'Artist - Album [V0]',
    'Artist - Album [V2]',
    'Artist - Album [MP3] [v2]',
])
def test_a_vbr_preset_is_not_a_release_version(title):
    """V0 and V2 are LAME presets, and this module reads them as a bitrate.

    Counting them again as Lidarr's Revision put a V0 rip on version 0 — below
    the neutral 1 an unmarked release gets — and a V2 rip on version 2. So the
    picker's revision tiebreaker ranked the WORSE preset above the better one,
    and both against releases that never said anything. V0 is one of the most
    common things written in an mp3 torrent title.
    """
    assert release_revision(title).version == 1


def test_a_version_still_counts_on_a_codec_without_presets():
    """The suppression is scoped to codecs that write vN as a quality setting.

    FLAC has no VBR preset, so [v2] there is a genuine second upload.
    """
    assert release_revision('Artist - Album [FLAC] [v2]').version == 2
    assert release_revision('Artist - Album [v5] [FLAC]').version == 5


def test_repack_still_speaks_for_an_mp3_release():
    """Only the bare vN token is ambiguous. REPACK/PROPER name themselves."""
    assert release_revision('Artist - Album [MP3 320] REPACK').version == 2
    assert release_revision('Artist - Album RERIP [MP3]').version == 2


def test_a_version_never_sorts_below_an_unmarked_release():
    """A marker we could not read must not make a release worse than silence."""
    assert release_revision('Artist - Album [FLAC] [v0]').version >= 1
