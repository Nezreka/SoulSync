"""Full disc images are never grabbed.

A release like ``Crawlers.2026.MULTi.COMPLETE.BLURAY-MONUMENT`` is one .iso of the
whole Blu-ray. The importer only takes playable files, so a grab of one downloads
~30 GB, finishes, and fails with nothing to import. It also scored as a plain
bluray-2160p, so it won the search every time and came back on each wishlist sweep
(15 grabs of the same release on one install). These pin the parse flag and the
hard reject, and the encodes and packs that must not be caught by it.
"""

from __future__ import annotations

import pytest

from core.video.quality_eval import evaluate_release
from core.video.quality_profile import TIERS, default_profile, normalize
from core.video.release_parse import parse_release

DISC = [
    "Crawlers.2026.MULTi.COMPLETE.BLURAY-MONUMENT",
    "The.Addams.Family.1991.MULTi.COMPLETE.UHD.BLURAY-MONUMENT",
    "Some.Movie.2019.COMPLETE.UHD.BLU-RAY-GROUP",
    "Some.Movie.2020.1080p.BDMV-GROUP",
    "Some Movie 2019 BD50 1080p AVC DTS-HD MA-GROUP",
    "Some.Movie.2024.BD66.2160p.HEVC-GROUP",
    "Some.Movie.2018.1080p.BR-DISK-GROUP",
    "Some.Movie.2001.DVD9-GROUP",
    "Some.Movie.2003.PAL.DVDR-GROUP",
    "Some.Movie.2010.ISO-GROUP",
]

NOT_DISC = [
    "Some.Movie.2023.1080p.BluRay.x264-GROUP",
    "Some.Movie.2022.2160p.UHD.BluRay.x265.10bit-GROUP",
    "Some.Movie.2023.2160p.UHD.BluRay.REMUX.HEVC.TrueHD.7.1-GROUP",
    "Some.Movie.2019.1080p.BluRay.AVC.REMUX-GROUP",
    "A.Show.S01.COMPLETE.1080p.BluRay.x264-GROUP",
    "A.Show.S02.COMPLETE.BLURAY.1080p.x265-GROUP",
    "A.Show.COMPLETE.SERIES.1080p.WEB-DL.DDP5.1-GROUP",
    "The.Iso.Show.S01E01.1080p.WEB-GROUP",
    "Iso.2020.1080p.BluRay.x264-GROUP",
]


@pytest.mark.parametrize("name", DISC)
def test_disc_images_are_flagged(name):
    assert parse_release(name)["disc_image"] is True


@pytest.mark.parametrize("name", NOT_DISC)
def test_encodes_remuxes_and_packs_are_not_disc_images(name):
    assert parse_release(name)["disc_image"] is False


def _every_tier_on():
    return normalize({**default_profile(),
                      "tiers": [{"key": k, "enabled": True} for k in TIERS],
                      "rejects": []})


def test_a_disc_image_is_rejected_even_with_every_tier_on_and_no_rejects():
    """No profile setting can let one through: there is nothing to import."""
    v = evaluate_release(parse_release(DISC[0]), _every_tier_on(), scope="movie")
    assert v["accepted"] is False
    assert "disc image" in (v["rejected"] or "").lower()


def test_the_same_film_as_an_encode_still_passes():
    v = evaluate_release(parse_release("Crawlers.2026.2160p.UHD.BluRay.x265-GROUP"),
                         _every_tier_on(), scope="movie")
    assert v["accepted"] is True, v["rejected"]


def test_a_disc_image_is_rejected_on_the_default_profile_too():
    v = evaluate_release(parse_release("Some.Movie.2020.1080p.BDMV-GROUP"),
                         default_profile(), scope="movie")
    assert v["accepted"] is False
    assert "disc image" in (v["rejected"] or "").lower()
