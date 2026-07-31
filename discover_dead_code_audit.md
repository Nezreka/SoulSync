# discover.js — unreachable code audit

Produced while porting the discover page to React. I was about to port the
tabbed-browser subsystem when its entry point turned out to have no callers, so
I computed the reachability closure over the whole file instead of chasing
functions one at a time.

**35 of 363 top-level functions (~1,190 lines, ~10% of the file) cannot be
entered by any user action.** None of it should be ported. PR 2 can delete all
of it.

## method

Seeds = every discover.js function name referenced from **outside** discover.js
(the other `static/*.js`, `index.html`) plus module-level code and the
bottom-of-file bootstrap. Then closure over the call graph built from each
function's own body — including names appearing inside the inline `onclick=`
strings that discover.js generates, so a handler reachable only through markup
the page paints still counts as reachable.

Spans run declaration-to-declaration, so module-level statements between two
functions are attributed to the preceding one. That can only **add** edges, so
the method errs toward calling things reachable. Everything below is dead with
margin.

> A first attempt brace-matched function bodies and reported zero dead code. It
> desynchronised on nested template literals (`` `${ ... `inner` ... }` ``), so
> one function absorbed the rest of the file and everything looked reachable.
> Worth knowing if this is ever re-run.

Every entry below was then confirmed by hand: each name occurs exactly once in
the repo — its own definition — or only inside other dead functions.

## the dead set

### 1. Genre browser — entirely dead (~390 lines)

`#genre-tabs` and `#genre-tab-contents` **do not exist in `index.html`**, and
`loadGenreBrowserTabs` has no callers. (The `genre-browser-modal` that IS in
index.html belongs to Beatport, not to discover.)

| line | function |
|---|---|
| 2215 | `_renderGenreCard` |
| 2233 | `loadGenreBrowser` |
| 2251 | `getGenreIcon` |
| 2318 | `capitalizeGenre` |
| 2334 | `openGenrePlaylist` |
| 2916 | `_genreTabId` |
| 2923 | `_getGenreBrowserTabsCtrl` |
| 3001 | `loadGenreBrowserTabs` |
| 3005 | `switchGenreTab` |
| 3032 | `loadGenreTracks` |
| 3036 | `startGenreSync` |
| 3105 | `startGenreSyncPolling` |
| 3190 | `openDownloadModalForGenre` |

Module state that dies with it: `genreTracksCache`, `availableGenres`.

### 2. Decade browser — the old card + tab paths (~200 lines)

Time Machine was rebuilt as a mix-card shelf. `loadDecadeBrowserTabs` (2646) is
the live entry and is NOT in this list. The tab strip it replaced is
`display:none` in index.html and hidden again at 2652, and nothing ever paints
`.decade-tab` buttons, so the switch/lazy-load path is unreachable.

| line | function |
|---|---|
| 2123 | `_renderDecadeCard` |
| 2142 | `loadDecadeBrowser` |
| 2160 | `getDecadeIcon` |
| 2174 | `openDecadePlaylist` |
| 2529 | `_getDecadeBrowserTabsCtrl` |
| 2687 | `switchDecadeTab` |
| 2714 | `loadDecadeTracks` |

`startDecadeSync` is **live** — the mix-card action calls it, and the shelf's
`fetchTracks` keeps `decadeTracksCache` populated for it.

### 3. The shared tabbed-browser helper (~150 lines)

With both configs dead, the helper has no consumer.

| line | function |
|---|---|
| 2381 | `_renderSyncStatusBlock` |
| 2425 | `createTabbedBrowserSection` |
| 2609 | `_renderTabbedTrackList` *(reported reachable only via the dead configs)* |

### 4. Odds and ends

| line | function | note |
|---|---|---|
| 2037 | `_renderCompactTrackRow` | superseded by the mix modal's row renderer |
| 3867 | `_toggleWingItDropdownLB` | |
| 3924 | `_wingItFromLBCard` | |
| 4144 | `openListenBrainzPlaylist` | |
| 4505 | `openDownloadModalForSeasonalPlaylist` | |
| 4522 | `syncSeasonalPlaylist` | |
| 4639 | `loadPersonalizedDailyMixes` | **see below** |
| 5034 | `blockDiscoveryArtist` | the "Blocked artists" idea never got a caller |
| 8655 | `_artMapCompositeNode` | |
| 9286 | `artMapZoomToNode` | |
| 11117 | `openDailyMix` | |
| 11200 | `updateDiscoverDownloadButton` | only caller is 11225, also dead |
| 11225 | `checkForActiveDiscoverDownloads` | |

## the one that changes the port

`loadPersonalizedDailyMixes` (4639) is the **only** producer of the
`daily_mix_${index}` cards on the Your Mixes shelf. It is unreachable, so those
cards have not rendered for users. `-discover.api.ts` had already recorded its
endpoint (`/api/discover/personalized/daily-mixes`) as dead; I then contradicted
that in the first draft of `-discover.mixes.ts` by listing `daily_mix_*` as a
live feeder. Corrected: the shelf has **seven** live feeders, and
`YOUR_MIX_FEEDERS` now carries a `live` flag with a test pinning it, so the port
cannot silently "restore" a section nobody has seen.

Note also `checkForActiveDiscoverDownloads` is dead while
`checkForActiveDiscoverSyncs` (a different function) is live and still required
— they are easy to confuse.

## what this does NOT cover

Only top-level `function` declarations. Module-level `const` arrow functions,
object-literal methods and the `_artMap` / artist-web internals reached through
event wiring are outside the graph, so the real dead total is likely higher.
Nothing here depends on that.
