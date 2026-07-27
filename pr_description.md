# wishlist cleanup — delete the vanilla page, fix two bugs it was hiding

follow-up to the wishlist react migration (#1088), now that the page has run live. removes 528 lines of unreachable code and fixes two real bugs found on the way.

## the bugs (fix first, delete second — they're causally linked)

**1. the page went stale after Cleanup / Clear All.** boulder spotted this independently: the orbs kept showing removed tracks until you navigated away and back. both handlers live in `downloads.js` and refreshed the page by calling `initializeWishlistPage()` — which repainted the hidden vanilla markup, not the react page. now the vanilla side announces `ss:wishlist-changed` and the page listens, same seam as `ss:watchlist-scan`. my migration tests missed it because they asserted the delegation *fires*, not what happened afterwards.

**2. the overview modal's Back button toggled the wrong elements.** it called `_nebulaBack()`, which poked `#wishlist-nebula` / `#wishlist-category-tracks` — ids the modal **duplicates**, and the page's copy came first in document order. predates the migration; the port just made it visible.

these had to land before the deletion: running the reachability sweep first returned only **one** dead function, because `initializeWishlistPage` and `_nebulaBack` were still anchored from downloads.js and holding the whole cycle alive. cutting those anchors is what made the rest collectable.

## the deletion

```
api-monitor.js   2321 -> 1892  (-429, 14 functions + 3 module globals)
index.html      11756 -> 11657 (-99, the page markup)
init.js          the dead nebula-poller stop
```

the sweep had to be **seeded by hand** with four functions whose only callers now live in TSX — a js-only analysis calls them dead, and deleting them would have broken the download flow and both search handoffs:

`_nebulaDownload` · `_showNebulaDownloadChoice` · `_searchWishlistTrackManually` · `_navigateToArtistFromWishlist`

all four verified present after the delete.

**kept on purpose:** `style.css` — those wishlist rules are used *by* the react page now. and the `wishlistCountdownInterval` clear in `loadPageData`, because react calls `startWishlistCountdownTimer` so navigating away must still clear that timer.

## tests followed the code

`test_wishlist_failing_badge.py` now asserts against the react source instead of `api-monitor.js`. it pins liveleak's failing-hub — retry data, the 3-attempt threshold, marks on all three surfaces, the rollup, the filter chip, both search buttons. behaviour didn't change, only its home.

two of its assertions are gone deliberately and the file records why: they guarded `_wlAttr` / `escapeForInlineJs`, which existed because the vanilla page concatenated markup — a quote in a failure reason broke out of `title="…"`, an apostrophe broke `onclick="fn('…')"`. react escapes props itself, so both bug classes are structurally gone rather than merely untested.

## also riding along

one-line `.gitattributes` fix: `routeTree.gen.ts` is generated with LF but was checked out CRLF, so every `npm run build` left it "modified" with a zero-content diff — enough to block a pull, which it did after the last merge.

and one unrelated red-CI fix that has nothing to do with this branch — it just happened to go off while the PR was open. `test_select_expired_filters` called `select_expired()` without `now=`, so it fell back to wall-clock while its fixtures pin `created_at` relative to a frozen `NOW` of 2026-06-07. the "too new" entry therefore aged in real time and crossed the 2mo window **on 2026-07-27** — a time bomb that armed itself on a date nobody touched anything. every other test in the file goes through `_check()`, which passes `now=NOW`; this was the only direct call in the repo. product code was always right, `select_expired` honours `now=` fine. re-verified the assertion still bites by mutating each entry in turn — aging the young one, zeroing the played one's count, unprotecting the protected one — all three change the result, so every keep-guard is live.

## verified

- 0 dangling references in the vanilla layer
- index.html div balance unchanged (2954 → 2931, diff 0 both sides), neighbouring pages intact
- all three vanilla scripts still parse; script-split integrity 64/64 (every `onclick` still resolves)
- 576 python tests over every file touching the edited sources, 245 webui tests, 0 type errors, build clean

## follow-up spotted, not done here

the wishlist **overview modal** is itself unreachable now — the dashboard button navigates to the page instead, and `handleWishlistButtonClick` (its only route) is an orphan. that's another few hundred lines, but it's a different question from "the vanilla page is obsolete" and needs its own careful scoping, since `cleanupWishlistOverview` / `clearEntireWishlist` / `openWishlistIgnoreModal` / `openDownloadMissingWishlistModal` live right next to it and are all still called.
