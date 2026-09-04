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
