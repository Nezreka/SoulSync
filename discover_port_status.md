# discover page → React: status

Branch `react-migration-discover`, 38 commits, **not pushed**.

## the honest summary

The **logic layer is partly done and what exists is heavily verified. There is
still no UI and nothing is mountable**, so there is nothing for you to click yet.

**Corrected after a coverage audit** (`discover_coverage_gaps.md`): "region X is
done" previously meant "I ported the functions I enumerated while reading region
X", and hand enumeration missed live functions in several regions — including
four of the seven live mix feeders, the download bar's bubble modal and its
bootstrap. 40 live functions outside the two visualisations are still un-ported.
The table below marks what is complete vs partial. discover.js is
12,319 lines — the largest file on the music side — and about 4,500 of those are
the two canvas/sigma visualisations, which are untouched.

Full suite: **2,714 tests / 131 files green.** Every module below was written by
reading its vanilla source end to end first, then mutation-tested — a mutation
that survives means the test was vacuous, and each pass is recorded in its
commit.

## what is done

| module | covers | notes |
|---|---|---|
| `-discover.helpers.ts` | 8 pure helpers | part of the **218 differential tests** against the real discover.js |
| `-discover.types.ts` | response shapes | traced to handlers, not invented |
| `-discover.api.ts` | 40+ endpoints | outcome-returning; dead endpoints marked |
| `-discover.layout.ts` | section order + pairing | order pinned verbatim |
| `-discover.section-state.ts` | the five-state lifecycle | loading/rendered/empty/stale/error |
| `-discover.limiter.ts` | the 5-way request pool | protects Flask+GIL, not the browser |
| `-discover.use-page.ts` | load tiering | above/below fold, load-once |
| `-discover.hero.ts` | hero + Watch All + the watchlist state check | id chain, bands, indicators |
| `-discover.your-albums.ts` | grid + paging | |
| `-discover.your-albums-actions.ts` | search, card download, sources, bulk, batch modal | |
| `-discover.your-artists.ts` | cards + stale polling | |
| `-discover.your-artists-actions.ts` | info modal, watchlist, sources, all-artists modal | **3 bug fixes** |
| `-discover.mixes.ts` | registry + covers + modal actions + the 4 live feeders + #1079 selection bar | gaps closed |
| `-discover.download-bar.ts` | the shared download bar + bubble modal routing | **cross-file window contract**; `rehydrateDiscoverDownloadModal` (~327 lines) still out |
| `-discover.playlist-sync.ts` | start / poll / resume + the download modal | **1 bug fix** |
| `-discover.cache-sections.ts` | 5 cache sections + Genre Deep Dive | |
| `-discover.build-playlist.ts` | seed picker + generator | |
| `-discover.bylt.ts` | Because You Listen To | **1 bug fix** |
| `-discover.adventurousness.ts` | the dial + the bounded loader pool | wave maths differentially tested |
| `-discover.recommended.ts` | both carousels + the "View All" modal | |
| `-discover.seasonal.ts` | seasonal albums/playlist + artist context | context differentially tested (27 cases) |
| `-discover.listenbrainz-cache.ts` | the LB cross-file cache contract | closes a carried requirement |

### bugs found and fixed (all pinned by a test that names them)

1. **`refreshYourArtists` never re-enables its button.** Gives up after 60
   attempts with a bare `clearInterval; return;` (5590) — a refresh that never
   settles leaves the button dead until reload. Your Albums re-enables from its
   timeout; this did not.
2. **The Your Artists sources modal bails silently on a disconnected source**
   (5673, 5680). That is the exact complaint the Your Albums hints were written
   to fix (1665-1667); the fix was never copied across. Now shares one hint table.
3. **The all-artists modal doesn't reset to page 1 on search or sort** (only on
   the source pills, 5772) — searching from page 3 asks for page 3 of a smaller
   result set and renders an empty grid whose only exit is Prev.
4. **`listening_mix` has no display name.** `startDiscoverPlaylistSync` sources
   eight playlist types; the completion-toast map lists seven (and is duplicated
   in the socket and polling paths, which is how they drifted). That sync toasts
   the raw key: "listening_mix sync complete!".
5. **One malformed BYLT section blanks every later shelf.**
   `section.tracks.map(...)` (10438) is unguarded inside the `onRendered` loop.

### the dead-code audit — `discover_dead_code_audit.md`

Computing reachability over the whole file (after nearly porting a subsystem
with no callers) found **35 of 363 top-level functions, ~1,190 lines, ~10% of
the file, unreachable**. The entire genre browser (its containers are not in
index.html), the decade browser's pre-shelf card and tab paths, and
`createTabbedBrowserSection` itself. PR 2 can delete all of it; none of it
should be ported.

One of those findings corrected my own work: `loadPersonalizedDailyMixes` is
unreachable, so the `daily_mix_*` cards have never rendered for users, and I had
listed that feeder as live a commit earlier.

## what is NOT done

* **no UI, no route, no mount** — nothing is clickable. This is the whole
  remaining gap between "verified logic" and "you can test it".
* **Artist Map** (canvas, 5829-6523) and **Artist Web** (sigma.js, 6523-10361) —
  ~4,500 lines, untouched. The CDN→npm decision (bundle graphology/sigma) is
  made but not executed.
* Remaining live sections not yet ported: Last.fm Track Radio (3235-3395) and
  the ListenBrainz playlists section (3397-4259). Both were read this session
  but not yet transcribed.
* PR 2 (delete discover.js + the dead code) — not started.
* ~~live functions outside the visualisations un-ported~~ — **ZERO**. The union
  coverage check went 40 → 24 → 0. Re-run it (`discover_coverage_gaps.md`)
  before claiming any region is complete; it is the only claim of completeness
  worth trusting, mine included.
* **`startDecadeSync` / `startDecadeSyncPolling` / `openDownloadModalForDecade`
  are LIVE** (reached from the decade mix card at 2672) even though the tabbed
  decade browser around them is dead. Do not delete them with the dead region.

## outstanding requirements — do not lose these

* ~~`checkForActiveDiscoverSyncs`~~ — **done**, in `-discover.playlist-sync.ts`.
* ~~your-mixes feeders~~ — **done**; there are eight call sites but only SEVEN
  live feeders (`daily_mix_*` is dead). `YOUR_MIX_FEEDERS` carries a `live` flag.
* ~~`window.listenbrainzTracksCache` / `listenbrainzPlaylistsCache`~~ — **done**,
  in `-discover.listenbrainz-cache.ts`. Two traps now pinned: `init.js` clears
  both caches IN PLACE, so identity must be stable (`= {}` orphans its
  reference); and `sync-listenbrainz.js` forks its OWN cache if ours is
  undefined when it runs, with no error, so Sync and Discover silently stop
  sharing. Still OPEN and deliberately unfixed: the two writers disagree on the
  cached track shape (discover writes the raw payload, sync writes a normalised
  row with `recording_mbid` renamed to `mbid`). Unifying them changes what every
  existing discover reader sees — its own change, with its own testing.
* **item-level types still unverified**: `DiscoverArtist`, `DiscoverTrack`.
  Trace each against its handler before building its cards. `DiscoverAlbum`,
  `DiscoverHeroArtist`, `YourAlbumsResponse`, `SourcesResponse` and
  `SeasonalResponse` ARE traced.
* **`discoverDownloads` is a shared global with two UNGUARDED external readers**
  (`wishlist-tools.js:7443` and `:7551` call `Object.keys(...)` / index it with
  no `typeof` check). It must be on `window` before wishlist-tools runs, which is
  why `publishDownloadGlobals` runs at module load rather than from an effect.

## the rule this page keeps proving

**Read the source end to end before writing the React version. Not a grep.**

Everything I found by grepping was partial; everything I found by reading was
right. Two concrete instances this session: the tabbed-browser subsystem I was
about to port had no callers, and the mix-feeder count in the previous version
of this document was wrong.

Corollary, from the reachability tooling: my first attempt brace-matched
function bodies, desynchronised on nested template literals, and confidently
reported **zero** dead code. A tool that agrees with you is not evidence.
