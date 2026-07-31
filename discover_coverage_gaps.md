# discover port — coverage gaps found by verification

> **STATUS: the gaps inside regions I had called "done" are now CLOSED.**
> Re-running the union check after the fixes: **40 → 24** un-ported live
> functions outside the two visualisations (~1,243 → ~768 lines). Two of the
> remaining 24 are false positives (see below), so the real figure is **22**,
> all in regions already reported as not started.

Produced by auditing my own work rather than re-reading my own prose. Four
mechanical checks; the fourth found real overstatement.

## what passed

| check | result |
|---|---|
| every user-facing string exists in the vanilla | **245 literals, 0 invented** |
| every `/api/` endpoint exists in the vanilla or `web_server.py` | **25 endpoints, 0 unresolved** |
| every `` `funcName` (NNNN) `` citation points at that function | **77/78** — the one flag was `init.js:1964`, correct but ambiguously written |
| every cross-file line citation (`downloads.js:954` etc.) | **9/9 exact** |

## what failed: coverage

Of **328 live** top-level functions, **189 are neither cited by line nor named
anywhere in the port** (~5,213 lines).

**149 of those (~3,970 lines) are the Artist Map + Artist Web**, which I have
consistently reported as untouched. That leaves **40 functions (~1,243 lines)**
that are genuinely un-ported — and several sit in regions I described as done.
That description was too strong.

### gaps inside regions I called "done" — ALL CLOSED

| line | function | region I claimed |
|---|---|---|
| 11793 | `openDiscoverDownloadModal` | **download bar** — this is what clicking a bubble opens |
| 11851 | `initializeDiscoverDownloadBar` | **download bar** — the bottom-of-file bootstrap (12314) |
| 11129 | `openDownloadModalForDiscoverPlaylist` | **playlist actions** — the mix modal's Download |
| 2060 | `loadDiscoverReleaseRadar` | **mixes** — a live shelf feeder |
| 2089 | `loadDiscoverWeekly` | **mixes** — a live shelf feeder |
| 4553 | `loadPersonalizedPopularPicks` | **mixes** — a live shelf feeder |
| 4580 | `loadPersonalizedHiddenGems` | **mixes** — a live shelf feeder |
| 4692 | `renderCompactPlaylist` | **mixes** — the modal's track table |
| 4757-4792 | `_previewMixTrack`, `_mixCheckedBoxes`, `_onMixTrackToggle`, `_mixToggleSelectAll`, `_mixClearSelection`, `_updateMixSelBar` | **mixes** — the #1079 preview + selective download |
| 548 | `checkAndUpdateDiscoverHeroWatchlistButton` | **hero** |
| 811 | `renderRecommendedArtistsModal` | **recommended** — the "View All" modal |

I ported the mix *registry* and called the mixes done. Four of the seven live
feeders that put cards on that shelf are not ported, nor is the modal's track
table, nor the selection bar Boulder shipped as #1079.

### gaps I had already reported as not done

* Last.fm Radio — 8 functions (3240-3397)
* ListenBrainz — `switchListenBrainzTab`, `switchListenBrainzSubTab`,
  `loadListenBrainzTabContent`, `loadTracksForPlaylists`,
  `displayListenBrainzTracks`
* Recent Releases — `_renderRecentReleaseCard` (1284)
* Discovery blacklist modal — `openDiscoveryBlacklistModal` and its three
  helpers (5058-5174). Note `loadDiscoveryBlacklist` (5827) is an EMPTY STUB,
  confirmed — nothing to port there.

### the decade shelf's actions are live and unported

`startDecadeSync` (2718), `startDecadeSyncPolling` (2785) and
`openDownloadModalForDecade` (2870) are reachable from the LIVE mix card
(`onclick: startDecadeSync(${year})`, 2672) even though the tabbed decade
browser around them is dead. Easy to lose when deleting the dead region — the
dead-code audit lists the tab path as deletable, and these three are NOT part of
it.

## false alarms worth recording

* `_yaaShowDisconnectedHint` (1675) is flagged but IS ported, as
  `disconnectedHint`. A citation gap, not a port gap.
* Name-based coverage alone under-reports (helpers are cited by name, not line);
  line-based coverage alone under-reports the opposite way. Only the union is
  meaningful — v1 of this check said 229 uncovered, v2 said 199, the union says
  189.

## the lesson

"I ported region X" meant "I ported the functions I happened to enumerate while
reading region X". Enumeration by hand missed a third of the live functions in
some regions. The fix is the mechanical union check above, re-run before any
claim that a region is complete.


## after the fixes

Closed, each with its own mutation pass:

| gap | commit | mutants |
|---|---|---|
| download bar — `openDiscoverDownloadModal`, `initializeDiscoverDownloadBar` | cf045977d | 8/8 |
| mixes — 4 live feeders, `renderCompactPlaylist`, the #1079 selection bar | (mixes) | 10/10 |
| hero check, "View All" modal, playlist download modal | (final) | 8/8 |

Two of those mutation runs found REAL bug classes rather than missing
assertions, and both distinguishing inputs came from this codebase:

* `startsWith('discover_album_')` loosened to `includes('album')` passed every
  test until `seasonal_album_*` — a real id built by `-discover.seasonal.ts` —
  was tried. The loose version refetches it from `/api/spotify/album`.
* A feeder def pointing at the dead `daily_mix_*` key is now caught by
  cross-checking `MIX_FEEDERS` against `LIVE_MIX_FEEDERS`.

### the 24 that remain (22 real)

All in regions already reported as not started:

* Last.fm Radio — 8 functions (3240-3397)
* ListenBrainz — 5 functions
* Discovery blacklist modal — 4 functions (5058-5174)
* the decade shelf's live actions — `startDecadeSync`, `startDecadeSyncPolling`,
  `openDownloadModalForDecade` (the deletion hazard above)
* `_renderRecentReleaseCard` (1284)

False positives, confirmed by hand:

* `_yaaShowDisconnectedHint` (1675) — ported as `disconnectedHint`
* `loadDiscoveryBlacklist` (5827) — an EMPTY STUB, `function ... () { }`

Still entirely untouched and NOT counted above: `rehydrateDiscoverDownloadModal`
(~327 lines, documented in `-discover.download-bar.ts`), and the Artist Map +
Artist Web (~3,970 lines).
