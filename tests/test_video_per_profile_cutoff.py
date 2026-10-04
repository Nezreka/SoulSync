"""Fix 14 follow-up: a landed file must not clear another profile's higher-cutoff wish."""
from unittest.mock import MagicMock


def test_movie_below_cutoff_keeps_high_profile_wish():
    from core.video import download_monitor as dm
    db = MagicMock()
    # profile 1 wants 1080p, profile 2 wants 4K; a 1080p file lands
    db.get_movie_wishlist_profiles.return_value = [(1, 10), (2, 20)]

    import core.video.quality_eval as qe
    import core.video.quality_profile as qp
    orig_meets = qe.meets_cutoff
    orig_profile = qp.profile_by_id
    try:
        # profile 10 (1080p cutoff): 1080p meets. profile 20 (4K cutoff): 1080p doesn't.
        qe.meets_cutoff = lambda label, profile: profile == "p1080"
        qp.profile_by_id = lambda db, pid: "p1080" if pid == 10 else "p4k"
        dm._remove_satisfied_movie_wishes(db, 123, "1080p", user_initiated=False)
    finally:
        qe.meets_cutoff = orig_meets
        qp.profile_by_id = orig_profile

    # only profile 1's row removed; profile 2's 4K wish stays
    db.remove_from_wishlist.assert_called_once_with("movie", tmdb_id=123, profile_id=1)


def test_movie_meeting_all_cutoffs_removes_all():
    from core.video import download_monitor as dm
    db = MagicMock()
    db.get_movie_wishlist_profiles.return_value = [(1, 10), (2, 10)]

    import core.video.quality_eval as qe
    import core.video.quality_profile as qp
    orig_meets = qe.meets_cutoff
    orig_profile = qp.profile_by_id
    try:
        qe.meets_cutoff = lambda label, profile: True
        qp.profile_by_id = lambda db, pid: "p1080"
        dm._remove_satisfied_movie_wishes(db, 123, "1080p", user_initiated=False)
    finally:
        qe.meets_cutoff = orig_meets
        qp.profile_by_id = orig_profile

    assert db.remove_from_wishlist.call_count == 2


def test_episode_below_cutoff_keeps_high_profile_wish():
    from core.video import download_monitor as dm
    db = MagicMock()
    db.get_episode_wishlist_profiles.return_value = [(1, 10), (2, 20)]

    import core.video.quality_eval as qe
    import core.video.quality_profile as qp
    orig_meets = qe.meets_cutoff
    orig_profile = qp.profile_by_id
    try:
        qe.meets_cutoff = lambda label, profile: profile == "p1080"
        qp.profile_by_id = lambda db, pid: "p1080" if pid == 10 else "p4k"
        dm._remove_satisfied_episode_wishes(db, 456, 1, 2, "1080p", user_initiated=False)
    finally:
        qe.meets_cutoff = orig_meets
        qp.profile_by_id = orig_profile

    db.remove_from_wishlist.assert_called_once_with(
        "episode", tmdb_id=456, season_number=1, episode_number=2, profile_id=1)


def test_unreadable_label_removes_all_movie_wishes():
    # unparseable quality → classic remove-on-obtain; never wedge rows open
    from core.video import download_monitor as dm
    db = MagicMock()
    db.get_movie_wishlist_profiles.return_value = [(1, 10), (2, 20)]
    dm._remove_satisfied_movie_wishes(db, 123, "", user_initiated=False)
    db.remove_from_wishlist.assert_called_once_with("movie", tmdb_id=123, profile_id=None)
    db.get_movie_wishlist_profiles.assert_not_called()


def test_unreadable_label_removes_all_episode_wishes():
    from core.video import download_monitor as dm
    db = MagicMock()
    db.get_episode_wishlist_profiles.return_value = [(1, 10), (2, 20)]
    dm._remove_satisfied_episode_wishes(db, 456, 1, 2, "garbage-label", user_initiated=False)
    db.remove_from_wishlist.assert_called_once_with(
        "episode", tmdb_id=456, season_number=1, episode_number=2, profile_id=None)
    db.get_episode_wishlist_profiles.assert_not_called()
