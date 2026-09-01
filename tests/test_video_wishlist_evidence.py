"""Keeping the receipt for a fruitless wishlist search.

A wanted item searches hourly, judges every candidate against the quality
profile, takes the best acceptable one — and when nothing qualifies, throws the
entire result set away and increments a counter. So a row that has searched forty
times looks identical to one waiting for next week's episode, and the user cannot
tell "be patient" from "this will never work".

Three rows on Boulder's install sit at thirteen fruitless searches (Aussie Shore
S02E03/E09/E10) with nothing on screen explaining any of them.

Everything needed is already in hand at the moment of refusal — each candidate
carries its ``quality_label`` and the ``rejected`` string naming the rule that
turned it down. The reason strings asserted here are taken from
``core.video.quality_eval``, not invented, because a classifier matched against
imagined text is worse than none: it would look right and silently classify
nothing.

The distinction that makes this worth storing: "Wrong season" means the release
is not this item (search noise), while "1080p WEB isn't in your enabled tiers"
means it IS this item and a rule of yours refused it. Only the second is worth
showing, and conflating them would report another show's episode as "best
available".
"""

from __future__ import annotations

from core.video.wishlist_evidence import (
    BLOCKED,
    IDENTITY,
    OTHER,
    QUALITY,
    SIZE,
    SWARM,
    classify_refusal,
    is_actionable,
    refusal_line,
    summarize_refusals,
)


def _c(label, rejected, accepted=False):
    return {"quality_label": label, "rejected": rejected, "accepted": accepted}


# ── the real reason strings ──────────────────────────────────────────────────
# Every string below is produced by core.video.quality_eval / _evaluate_hits.

def test_identity_refusals_are_recognised():
    """These say the release is not this item at all."""
    for r in ("Wrong season",
              "Wrong year (2025 — wanted 2026)",
              "Wrong title (Some Other Show — wanted This Show)",
              "This is a TV release, not the movie",
              "Not a single episode",
              "Release is S03E07, not the episode requested"):
        assert classify_refusal(r) == IDENTITY, r


def test_quality_refusals_are_recognised():
    for r in ("720p WEB isn't in your enabled tiers",
              "Unknown / unsupported quality",
              "x265 codec is on your reject list",
              "BluRay is on your reject list",
              "3D is on your reject list",
              "HDR required but this is SDR",
              "Format score 5 is below your minimum 20"):
        assert classify_refusal(r) == QUALITY, r


def test_swarm_and_size_and_block_refusals_are_recognised():
    assert classify_refusal("No seeders — nobody is sharing this") == SWARM
    assert classify_refusal("Only 2 seeder(s) — your floor is 5") == SWARM
    assert classify_refusal("Over your 20 GB size cap") == SIZE
    assert classify_refusal("Uploader blocklisted") == BLOCKED
    assert classify_refusal("Blocklisted release") == BLOCKED
    # _evaluate_hits COMPOSES these: "Blocklisted release · <prior verdict>".
    # When the prior half is an availability rule, the block is the operative
    # refusal and the most actionable thing to say.
    assert classify_refusal("Uploader blocklisted · 720p WEB isn't in your enabled tiers") == BLOCKED


def test_a_blocklisted_hit_for_the_wrong_season_is_still_just_the_wrong_season():
    """The composed form is real ('Blocklisted release · Wrong season'), and the
    identity half has to win: the release is not this item, so reporting it as
    "best found" would describe another season's episode."""
    assert classify_refusal("Blocklisted release · Wrong season") == IDENTITY
    assert summarize_refusals([_c("1080p WEB", "Blocklisted release · Wrong season")]) is None


def test_an_unknown_reason_is_other_not_identity():
    """Guessing IDENTITY for something unrecognised would silently hide a real
    availability problem — the row would look like it found nothing when it
    found something it refused for a reason nobody thought to classify."""
    for r in ("some brand new rule", "", None, 42):
        assert classify_refusal(r) == OTHER, r


# ── the receipt ──────────────────────────────────────────────────────────────

def test_the_best_refused_release_is_the_one_reported():
    """"Best" because that is the copy the user would take if they relaxed
    something — reporting the worst would understate what is actually available."""
    s = summarize_refusals([
        _c("480p WEB", "480p WEB isn't in your enabled tiers"),
        _c("720p WEB", "720p WEB isn't in your enabled tiers"),
        _c("576p WEB", "576p WEB isn't in your enabled tiers"),
    ])
    assert s["quality_label"] == "720p WEB"
    assert s["seen"] == 3


def test_identity_noise_never_becomes_the_answer():
    """A hundred hits for the wrong season mean the search found nothing. Calling
    another show's 1080p "best available" would be worse than silence."""
    assert summarize_refusals([
        _c("1080p WEB", "Wrong season"),
        _c("2160p WEB", "Wrong title (Other Show — wanted This Show)"),
    ]) is None


def test_identity_hits_are_not_counted_among_what_was_seen():
    s = summarize_refusals([
        _c("1080p WEB", "Wrong season"),
        _c("720p WEB", "720p WEB isn't in your enabled tiers"),
    ])
    assert s["seen"] == 1 and s["quality_label"] == "720p WEB"


def test_an_accepted_release_is_not_a_refusal():
    """If something was acceptable the row is not stuck — it grabbed."""
    assert summarize_refusals([_c("1080p WEB", None, accepted=True)]) is None


def test_nothing_found_at_all_yields_nothing_to_say():
    for empty in ([], None, [None], [{}], ["junk"]):
        assert summarize_refusals(empty) is None


def test_an_unrankable_label_still_ranks_by_its_digits():
    """A tier the ladder doesn't know ('1440p WEB') must not sort below 720p and
    silently under-report what is on offer."""
    s = summarize_refusals([
        _c("720p WEB", "720p WEB isn't in your enabled tiers"),
        _c("1440p WEB", "1440p WEB isn't in your enabled tiers"),
    ])
    assert s["quality_label"] == "1440p WEB"


def test_a_missing_label_does_not_crash_the_summary():
    s = summarize_refusals([_c(None, "No seeders — nobody is sharing this")])
    assert s["quality_label"] is None and s["kind"] == SWARM


# ── the line the user reads ──────────────────────────────────────────────────

def test_the_line_names_the_tier_and_the_rule():
    """Either alone leaves them guessing: "720p" doesn't say why it was refused,
    and "isn't in your enabled tiers" doesn't say what was on offer."""
    line = refusal_line(summarize_refusals([
        _c("720p WEB", "720p WEB isn't in your enabled tiers"),
        _c("720p WEB", "720p WEB isn't in your enabled tiers"),
    ]))
    assert "720p WEB" in line and "enabled tiers" in line
    assert "(2 releases)" in line, "one hit is bad luck; twelve is a setting"


def test_a_single_hit_does_not_claim_a_count():
    line = refusal_line(summarize_refusals([_c("720p", "720p isn't in your enabled tiers")]))
    assert "releases)" not in line


def test_a_swarm_refusal_reads_without_a_tier_if_there_is_none():
    line = refusal_line(summarize_refusals([_c(None, "No seeders — nobody is sharing this")]))
    assert line and "No seeders" in line


def test_there_is_no_line_when_there_is_nothing_to_say():
    for bad in (None, {}, "x", {"reason": None}):
        assert refusal_line(bad) is None


# ── which ones the user can act on ───────────────────────────────────────────

def test_settings_the_user_owns_are_actionable():
    for reason in ("720p WEB isn't in your enabled tiers", "Over your 20 GB size cap",
                   "Uploader blocklisted"):
        assert is_actionable(summarize_refusals([_c("720p", reason)])) is True, reason


def test_a_dead_swarm_is_not_something_they_can_fix():
    """Offering "accept this anyway" for a torrent nobody seeds would hand them a
    download that cannot finish."""
    assert is_actionable(summarize_refusals([_c("1080p", "No seeders — nobody is sharing this")])) is False


def test_junk_is_never_actionable():
    for bad in (None, {}, "x", 7):
        assert is_actionable(bad) is False


# ── it has to reach the page ─────────────────────────────────────────────────

def _wishlist_js() -> str:
    from pathlib import Path
    return Path("webui/static/video/video-wishlist.js").read_text(encoding="utf-8")


def test_both_card_shapes_share_one_explanation():
    """Movies and episodes render from different functions; two copies of this
    would drift, and the episode one is the shape Boulder's three stuck rows are."""
    js = _wishlist_js()
    assert js.count("function failWhy(") == 1
    assert js.count("esc(failWhy(") == 2, "movie card AND episode row"


def test_the_chip_shows_the_stored_reason_when_there_is_one():
    js = _wishlist_js()
    fn = js[js.index("function failWhy("):js.index("function statusPill(")]
    assert "row.last_refusal" in fn


def test_it_still_says_something_useful_with_no_receipt():
    """Rows that have not searched since the upgrade have no receipt yet — the
    chip must not go blank for them."""
    js = _wishlist_js()
    fn = js[js.index("function failWhy("):js.index("function statusPill(")]
    assert "searches without a grab" in fn
    assert "try Search now" in fn, "keep the old advice as the fallback"


# ── the two empty-handed cases (the 144-of-147 gap) ──────────────────────────

def test_a_search_that_found_nothing_says_so():
    """summarize_refusals returns None here, which CLEARED the row's reason and
    left a warning badge with no sentence. On the live install 144 of 147 stuck
    rows looked like this - including a 1999 film searched 959 times."""
    from core.video.wishlist_evidence import NOT_FOUND, refusal_line, summarize_search

    s = summarize_search([])
    assert s["kind"] == NOT_FOUND
    assert refusal_line(s) == "Nothing found for this search"


def test_results_that_were_all_some_other_title_say_that_instead():
    """'Nothing found' would be wrong here — the search DID bring things back,
    they just weren't this item. That difference is the user's next move."""
    from core.video.wishlist_evidence import IDENTITY, refusal_line, summarize_search

    s = summarize_search([{"rejected": "Wrong season"}, {"rejected": "Wrong year"}],
                         noun="episode")
    assert s["kind"] == IDENTITY
    assert refusal_line(s) == "2 results, none were this episode"


def test_a_real_refusal_still_wins_over_the_empty_wording():
    """The actionable receipt is the whole point; it must not be replaced."""
    from core.video.wishlist_evidence import refusal_line, summarize_search

    s = summarize_search([
        {"rejected": "Wrong season"},
        {"rejected": "SD isn't in your enabled tiers", "quality_label": "SD"},
    ])
    assert refusal_line(s) == "Best found: SD — SD isn't in your enabled tiers"


def test_neither_empty_case_pretends_to_be_actionable():
    """is_actionable drives 'you could change a setting'. Nobody can fix
    'nobody has it' by editing a quality profile."""
    from core.video.wishlist_evidence import is_actionable, summarize_search

    assert not is_actionable(summarize_search([]))
    assert not is_actionable(summarize_search([{"rejected": "Wrong year"}]))


def test_the_count_is_not_printed_twice():
    """refusal_line appends '(N releases)' when seen > 1, and the wording here
    already carries the count."""
    from core.video.wishlist_evidence import refusal_line, summarize_search

    line = refusal_line(summarize_search([{"rejected": "Wrong year"}] * 5))
    assert line == "5 results, none were this release"
    assert "releases)" not in line
