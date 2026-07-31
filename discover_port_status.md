# discover port — status as of Jul 31, ~00:00

Branch `react-migration-discover`, **10 commits**, everything green:
full vitest **2,130 / 116 files**, tsc clean, `npm run check` clean.
**Nothing pushed.**

## the honest headline

**The data layer is done and verified. No UI is built yet.** I did not finish
the page. What exists is correct; what's missing is missing, not half-done.

## what is DONE and verified

| file | what it is | verification |
|---|---|---|
| `-discover.helpers.ts` | 8 pure helpers | 101 **differential** tests vs the REAL vanilla, extracted by brace-matching from discover.js |
| `-discover.limiter.ts` | the 5-at-a-time request pool | 8 tests; matches the vanilla for the reason the vanilla states (Flask+GIL contention, not browser limits) |
| `-discover.layout.ts` | section order + pairing rule | 20 tests; order pinned verbatim, lone-pair-member promotion covered |
| `-discover.section-state.ts` | the **five-state** section lifecycle | 19 tests; per-section copy/hide/toast/stale transcribed from the controller |
| `-discover.api.ts` | 40+ endpoints, outcome-returning | 24 tests; paths/methods/params/bodies all traced to handlers |
| `-discover.use-page.ts` | load tiering + section resolution | 15 tests; above/below fold gating, limiter wiring, load-once caching |

**61+ mutants raised, all caught.** Every number and rule was traced to source.

## what is NOT done

* **no UI at all** — hero, your-albums/your-artists, the shelves, the download bar
* **Artist Map** (canvas, 71 fns) and **Artist Web** (sigma.js, 76 fns) — untouched.
  The CDN→npm decision is made (bundle graphology/sigma) but not executed.
* mount / route / manifest flip
* PR 2 (delete discover.js) — not started

## the mistake pattern, and the fix

Five defects in this session all had ONE cause: **I wrote code from a reading of
the vanilla and verified afterwards.** That produced an invented hero type
(`name`/`id`/`logo_url`/`followers` — none exist), an invented `staleTime`, a
missing dial query, a missing `listening_mix` feeder, and a two-state failure
model where the vanilla has five.

The last one only surfaced when I read `discover-section-controller.js` **in
full** rather than grepping it. Everything I found by grepping was partial;
everything I found by reading was right.

**Rule for whoever picks this up (including me): read the source file end to
end BEFORE writing the React version. Not a grep. The whole thing.**

## outstanding requirements — do not lose these

* **`checkForActiveDiscoverSyncs`** — last thing `loadDiscoverPage` calls. Polls
  `/api/sync/status/discover_release_radar|_discovery_weekly|_seasonal_playlist`;
  if a sync is mid-flight it re-shows the status, disables the button and
  resumes polling. Reloading mid-sync without it makes a running sync look dead.
  Lands with the first of those three sections.
* ~~**`your-mixes-section` feeders still to add**~~ — DONE, and the count in this
  doc was wrong. There are **eight** feeders, not seven, now pinned by
  `YOUR_MIX_FEEDERS` in `-discover.mixes.ts`: `release_radar`,
  `discovery_weekly`, `seasonal_playlist`, `popular_picks`, `hidden_gems`,
  `listening_mix`, `daily_mix_${index}`, `discovery_shuffle`. `daily_mix_*` is
  variadic AND the only one with no `syncKey` (so its modal correctly gets no
  actions). Still true that every feeder must count in `hasContent`, or the
  shelf hides for a user who only has that one mix.
* **`window.listenbrainzTracksCache` / `listenbrainzPlaylistsCache`** must be
  read/written on `window` by the LB phase — documented shared contract with the
  Sync tab. A `typeof`-guarded consumer degrades SILENTLY, which is worse than
  the loud `cleanArtistName` break the globals guard catches.
* **item-level types are still unverified** — `DiscoverAlbum`, `DiscoverArtist`,
  `DiscoverTrack`. Trace each against its handler before building its cards.
  `DiscoverHeroArtist`, `YourAlbumsResponse`, `SourcesResponse` and
  `SeasonalResponse` ARE traced.

## proven safe

Simulating the deletion of discover.js fails `test_vanilla_globals_resolve`,
naming `loadDiscoverPage`, `cleanArtistName`, `addDiscoverDownload`,
`removeDiscoverDownload`, `discoverDownloads` and five init.js hooks. PR 2
cannot silently orphan them.

## also on this branch's dev, unrelated

`video_db_flake_diagnosis.md` — the nightly CI flake, mechanism found, not from
PR #1107. Cheap narrowing + real root cause both written up there.
