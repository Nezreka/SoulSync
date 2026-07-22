from core.downloads.source_policy import (
    SEARCH_MODE_BEST_QUALITY,
    resolve_source_policy,
    source_policy_from_settings,
)


def test_hybrid_order_is_canonical_deduplicated_and_ranked():
    policy = resolve_source_policy(
        mode="hybrid",
        hybrid_order=["hifi", "deezer_dl", "hifi", "soulseek"],
        search_mode="priority",
    )

    assert policy.source_chain == ("hifi", "deezer", "soulseek")
    assert policy.source_priorities == {"hifi": 0, "deezer": 1, "soulseek": 2}
    assert policy.search_all_sources is False


def test_best_quality_pools_the_same_configured_chain():
    policy = resolve_source_policy(
        mode="hybrid",
        hybrid_order=["usenet", "torrent"],
        search_mode=SEARCH_MODE_BEST_QUALITY,
    )

    assert policy.source_chain == ("usenet", "torrent")
    assert policy.search_all_sources is True
    assert policy.quality_first is True


def test_single_source_mode_excludes_every_other_source():
    policy = resolve_source_policy(
        mode="usenet",
        hybrid_order=["torrent", "usenet"],
        search_mode="best_quality",
    )

    assert policy.source_chain == ("usenet",)
    assert policy.search_all_sources is False
    assert policy.permits("usenet") is True
    assert policy.permits("torrent") is False


def test_profile_search_settings_and_download_settings_share_one_policy():
    values = {
        "download_source.mode": "hybrid",
        "download_source.hybrid_order": ["torrent", "usenet"],
    }
    policy = source_policy_from_settings(
        lambda key, default=None: values.get(key, default),
        profile={
            "search_mode": "priority",
            "rank_candidates_by_quality": True,
        },
    )

    assert policy.source_chain == ("torrent", "usenet")
    assert policy.search_all_sources is False
    assert policy.quality_first is True
