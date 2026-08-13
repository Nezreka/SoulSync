# playlist explorer on react

the 10th music page. 1,136 lines of `pages-extra.js` and 101 lines of markup
gone, rebuilt as a route with 170 tests behind it. `pages-extra.js` survives —
it hosted three pages and only the explorer moves.

## what it is now

- **the picker** — source tabs, the readiness gate (under 50% discovered and
  the card is inert, exactly as before, with a Discover button instead), the
  five-step badge ladder, live discovery percentages
- **the tree** — root → artists → albums → tracklists, rows growing 2, 3, 4,
  5…, with the SVG bezier layer measured off the laid-out DOM
- **the interactions** — the 250ms single-vs-double-click discriminator, zoom,
  fit, viewport-scoped wheel, middle/right-drag pan
- **Add to Wishlist** — reuses the discography modal's classes, CSS, footer
  strings AND its NDJSON reader, because it posts to the same endpoint

## three real bugs fixed on the way

**every duration in the download-audit UI shows `0:00` today.** three files
declare `_formatDuration`; `pages-extra.js` loaded last, so its
*millisecond*-based copy shadowed the two *second*-based ones in
`stats-automations.js` and `wishlist-tools.js`. a 3m35s download rendered as
`0:00`. deleting the explorer's copy unshadows them.

**the connection lines could collide.** the artist key collapses every
non-alphanumeric to `_`, so "AC/DC" and "AC-DC" produce the same key — and
would have produced two SVG paths with the same React key. path ids carry the
artist's position now.

**the build progress bar wrote `NaN%`** when a playlist reported 0 artists.

## and the one vanilla change

`core.js` re-broadcasts `discovery:progress` as `ss:discovery-progress`, the
same seam `ss:watchlist-scan` and `ss:automation-progress` already use. both
`socket` and `youtubePlaylistStates` are module-scoped `let`s in that file, so
no module can reach either — but the frame carries the phase the poller needed,
so this is one bridge instead of two. purely additive.

## faithful quirks, kept deliberately

- the pending click is a SINGLE slot, so clicking a second album doesn't cancel
  the first's selection — and then clears the second's pending state
- the build button settles on the shorter "Explore" after the first build
- an album with no `spotify_id` can be selected and counts in "N selected", but
  is silently dropped from the submit
- on finish the whole selection is marked added, not just what was submitted
- the picker shows `name`, not `display_name`, so a custom playlist alias
  doesn't appear here though it does everywhere else

the last two are arguably worth fixing — separately, as behaviour changes.

## verification

full webui suite green (216 files / 4,658 tests), full python suite green, zero
lint/type/format errors, production build clean, both edited vanilla files
parse. every new module is mutation-checked — 39 mutations, each one fails a
test.

the citation comments name the vanilla *function* plus a verified line, checked
mechanically: each cited line must fall inside the function it names. 74
verified, 0 mismatched.
