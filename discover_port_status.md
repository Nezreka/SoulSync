# discover page → React: status

Branch `react-migration-discover`, 47 commits, **not pushed**.

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

## the two visualisations (added after the coverage work above)

Both are now ported at the DECISION layer and heavily verified. State it that
way rather than "the visualisations are done", because the distinction matters:
what remains of them is the imperative DOM and orchestration, and that becomes
JSX and effects when the page gets its React components — there is nothing to
port ahead of that.

A coverage sweep over the two regions (157 function declarations) breaks down as:

| | count |
|---|---|
| ported under the same name, or recorded dead in the audit | 116 |
| decision layer ported under a different name | 31 |
| pure DOM / orchestration, nothing to port yet | 10 |

The last 10 are `_artWebKillLiveLayout`, `_artWebSyncLensButtons`, `_switchGenre`,
`_changeGenre`, `_filterGenrePicker`, `_filterGenreSidebar`, `artMapToggleSimilar`
(its toolbar half — the keyboard half is ported), `artWebExploreInMap`,
`artWebPlayArtist` and `openArtistMapExplorer`. Every one is a class toggle, a
modal, a teardown or a hand-off.

| module | covers | mutants |
|---|---|---|
| `-discover.artist-map.ts` | island layout, camera targets, hit test, reveal stepper, ripple physics | 68, 66 dead (2 equivalent, recorded) |
| `-discover.artist-map.panel.ts` | info panel, island nav + jump menu, tooltip, context menu, shortcuts | 48/48 |
| `-discover.artist-map.render.ts` | offscreen buffer, painters, live overlay, rAF loop, camera easing, image stream | 92/92 |
| `-discover.artist-map.interaction.ts` | wheel/pan/hover/click/touch/keys/resize + dispose | 50/50 |
| `-discover.artist-map.entry.ts` | watchlist / genre / explorer entry, toolbar search, the info hand-off | 39/39 |
| `-discover.artist-web.ts` | palette, edge styling, label renderer, three lens builders, both reducers, spread FX | 86, 85 dead (1 equivalent, recorded) |
| `-discover.artist-web.controller.ts` | lifecycle + generation guards, FA2 supervisor, search, selection, tooltip, path mode | 88/88 (shared with the panel) |
| `-discover.artist-web.panel.ts` | legend + the four side-panel cards | (above) |

### how canvas and sigma code got verified

Canvas calls return nothing to compare, so both sides run against a **recording
2D context** that logs every call and every property assignment in order, and
the two logs are diffed. `document.createElement('canvas')` is stubbed for both
and each canvas is labelled by creation order, so the sequence in which sprites
and buffers are built is part of the diff too.

The event handlers use the same idea with effects: a real jsdom canvas, the same
synthetic events, one recorder standing in for every collaborator.

The Artist Web's lens builders take the Graph CONSTRUCTOR as an argument, so both
sides get one minimal graphology stand-in and their finished graphs are compared
attribute by attribute, with `Math.random` seeded and rewound per side.

### the one deliberate divergence

`attachArtMapInteraction` returns a dispose function. The vanilla guards against
stacking listeners with a flag on the canvas ELEMENT, which works only because
index.html declares that canvas once; React recreates it per mount, so the guard
would stop guarding anything. Same behaviour while mounted.

### two transcription misses the differential caught

Both in code already written and believed correct:

1. the map's key handler bails when the container is missing or hidden (10018).
2. `s` only calls preventDefault INSIDE `if (input)` (10026-10029) — with no
   search box on the page, the key still types an s.

### three vacuous assertions of my own, all the same shape

Asserting a value against the very constant under mutation. One was worse than
vacuous: `SOURCE.toContain('ratio: ' + WEB_CAMERA.cameraToRatio)` passed a
0.12 → 0.15 mutation, because `artWebFocusNode` genuinely uses 0.15 elsewhere in
the file. All three are literals now.

## the UI phase (in progress)

`src/routes/discover/-ui/` now exists. Done, each with a full mutation pass
against a green baseline:

| component | mutants |
| --- | --- |
| `artist-map-hub` / `artist-map-panel` | earlier phases |
| `artist-map-overlay` — canvas lifecycle shell | 26/26 |
| `artist-map-chrome` — tooltip, context menu, shortcuts, search | 29/29 |
| `-discover.artist-web.lifecycle` — sigma/FA2/frame loop | 40/40 |
| …its keyboard half | 19/19 |
| `artist-web-overlay` — toolbar, sidebar, legend, host | 29/29 |
| `artist-web-panel` — three cards, path, guide, hints | 34/34 |
| `discover-section` + `discover-hero` | 32/32 |
| `recommended-shelf` — both recommendation shelves | 25/25 |
| `recommended-modal` | 23/23 |

Two equivalent mutants are recorded in the code rather than papered over: the
context menu's inner `hasId` check (unreachable behind `disabled`) and the
section shell's re-test of `loaded` (unreachable behind `isSectionVisible`).

### findings from this phase

- **`artist.name` does not exist.** The hero response has `artist_name`, and
  `DiscoverHeroArtist`'s index signature means tsc never objects. The types file
  already warned about this class of invention; it caught me anyway.
- **Section ids are used verbatim.** `DiscoverSectionId` already matches the
  vanilla's DOM ids, several of which end in `-section`. Appending another
  produces `listening-recs-section-section` and detaches every rule naming it.
- **The Artist Web's KEY zoom ratios are 0.77/1.3**, not the toolbar buttons'
  0.7/1.4 — a held key repeats, so it steps smaller.
- **`webMountSigma` returns a disposer** instead of carrying the vanilla's
  `_mouseBound` flag, for the same reason the map's canvas guard was dropped:
  React builds a new host per mount, so the flag would bind the first and skip
  every later one.
- **`finishLayout` was missing from both fallback paths** in the first draft of
  the web lifecycle. Nothing throws; selections just stop fanning out. The
  vanilla differential caught it.

### still to build

`your-artists` / `your-albums`, `recent-releases` / `seasonal` / decade shelf,
mixes / Last.fm radio / ListenBrainz, the adventurousness dial / build-playlist /
download bar, then `route.tsx` + the manifest flip, then PR 2's cleanup (delete
discover.js + the 37 dead functions).

**Deletion hazard, unchanged:** `startDecadeSync`, `startDecadeSyncPolling` and
`openDownloadModalForDecade` are LIVE despite sitting inside the dead region.

**Still out:** `rehydrateDiscoverDownloadModal` (~327 lines), documented in
`-discover.download-bar.ts`.
