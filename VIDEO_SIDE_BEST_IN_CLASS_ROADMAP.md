# Video Side Best-In-Class Roadmap

This is the working checklist for making SoulSync's video side feel better than the usual Radarr/Sonarr split: clearer UI, stronger hybrid acquisition, and better recovery when items stall.

## Already Knocked Off

- [x] Fold EXT.to into torrent auto-search results, so torrent mode gets Prowlarr plus EXT.to instead of treating EXT.to as a separate manual-only lane. Commit: `1b09ece53`.
- [x] Preserve EXT.to provenance while enqueueing it as a torrent transport. EXT.to rows keep `indexer_id=extto` and display provenance, while downloads persist as torrent-backed records.
- [x] Record download-client refusal notes on wishlist rows without inflating search attempts. Commit: `d4639504b`.
- [x] Sweep completed YouTube wishlist rows before requeueing. Commit: `5db0bcaf4`.
- [x] Migrate video RSS sync from hourly legacy cadence to the faster Arr-style cadence. Commit: `c56f20c72`.
- [x] Warn immediately when wishlist auto-search cannot run because a movie, TV, or YouTube target folder is missing. Commit: `861936c7a`.
- [x] Let EXT.to answer when Prowlarr can't — the torrent lane only fails when neither half can run. Commit: `b2243fcd2`.
- [x] Back off retries instead of re-searching a dead row hourly forever (174 → 10 searches per tick on the live install). Commit: `6c5018f4a`.
- [x] Say why a search came back empty instead of storing NULL. Commit: `f7cfafc36`.
- [x] Stop hunting episodes of a show the user un-followed. Commit: `c9a233a57`.
- [x] YouTube: back off instead of giving up permanently, and report the split. Commit: `34b86380b`.
- [x] A download that gives up now says why, instead of one generic sentence for ~1,900 history rows. Commit: `6bcf24d9d`.
- [x] Feed the EXT.to board into the RSS wishlist matcher (coverage lane, reads the cached snapshot — never scrapes on the tick). Commit: `5503a6f1f`.
- [x] Give the YouTube wishlist tab its bulk action back — same button, different verb (search on the TMDB tabs, download on YouTube), reusing the drain's enqueue path and concurrency cap. Commit: `b97b18fdd`.
- [x] Cache Plex/Jellyfin artwork on disk like TMDB art, instead of re-fetching every poster on every page view. Commit: `b5c720621`.
- [x] Stop video grids pulling full-size (~2000x3000) posters into 150px cards — watchlist, detail season rail and episode thumbs now request thumbnails. Commit: `f8c5735af`.

## Search Page

- [x] Make every manual search hit show source, transport, indexer, and grab eligibility without requiring expansion.
- [x] Surface rejection/review reasons directly on the card, with the full detail still available in the expanded facts panel.
- [x] Add per-source counts for usable, review, and no-link results in the source tabs. Commit: this phase.
- [x] Show the query variants attempted for TV episodes and season packs, so users can tell when matching is too narrow. Commit: this phase.
- [x] Add a safe "try anyway" path for manual grabs that failed soft rules but still have a valid link. Commit: this phase.
- [x] User-triggered grabs carry an import policy: equal-or-better valid files replace existing copies, lower-quality valid files can land beside them, and background automation remains strict-upgrade only. Commit: this phase.
- [x] Bias TV searches toward season packs when many wanted episodes from the same season are missing. Commit: this phase.

## Wishlist Processing

- [ ] Add a stuck-row diagnostics drawer: last searched, last refusal, attempts, source order, target folder, matched IDs, and queued download state. Partly done in `bea126744` — the row's tooltip now reads last searched, attempts, source order and the per-source breakdown. Still to do: target folder, matched IDs, queued download state, and a real drawer rather than a tooltip.
- [x] Store per-source search outcome snapshots for wanted items: result count, rejected count, accepted count, and top refusal reason. Commit: `bea126744`.
- [ ] Add adaptive TV query fanout: canonical title, aliases, year/no-year, `SxxExx`, `season x episode y`, and scene-number variants. Partly done in `3cddae457` — canonical title, TMDB aliases, and year/no-year spellings now ride the ladder alongside the existing `SxxExx`, `1x02`, air-date and anime-absolute forms. Commit: this phase adds spelled-out `season x episode y` fallbacks. Still to do: general scene-number variants.
- [x] Add season-pack-first drain when multiple episodes from a season are missing and pack rules allow it. Commit: this phase.
- [x] Add temporary source cooldowns when a client or indexer repeatedly refuses the same item. Commit: this phase.
- [x] Add a one-click "retry with all sources" action for stale wishlist rows. Commit: this phase.

## YouTube

- [ ] Separate unavailable, members-only, private, age-gated, cookie-needed, throttled, postprocessing, and disk-space failures in row state. Partly done in `6ffd37ef2` — rows distinguish unavailable (deleted/private/members-only) from a retry backoff, and carry the attempt count and reason. Still to do: age-gated, cookie-needed, throttled, postprocessing and disk-space as distinct states.
- [x] Add a YouTube health tile covering yt-dlp version, cookies status, temp space, output space, and recent HTTP failures.
- [x] Finalize stale active/importing rows that no longer map to a live download worker. Current `recover_and_pump()`/`requeue_orphaned_youtube()` path covers `downloading` and `importing` orphans; verified by `tests/test_youtube_download.py::test_requeue_orphaned_youtube_recovers_only_dead_downloads` and `::test_recover_and_pump_requeues_orphans_then_fills_free_slots`.
- [x] Offer per-channel and per-playlist retry policies, including archive recheck cadence.
- [ ] Prefer the configured hybrid fallback only where it makes sense: YouTube-native first, then alternate search when metadata is strong enough.

## Detail Page

- Design review:
  - The hero reads well, but the acquisition truth is split across hero badges, action buttons, episode rows, and history. Identity is the piece nothing surfaced at all.
  - When a title has no TVDB or IMDb id, nothing on the page says so. Users find out when a search or a wishlist action quietly fails.
  - YouTube channels reuse the show shell well, but nothing stated the channel or playlist id the downloader actually keys on.
  - Owned/wanted, coverage percent, episode counts and format badges are already in the meta line. Anything added under the hero has to say something new or it just competes with the row above it.
- [x] Add an acquisition panel showing owned, wanted, queued, downloading, failed and ignored in one place, above the history section: history says what happened, the panel says what is true. Counted over the unit you act on (a movie is one, a show is its episodes). Queued and downloading are a SUBSET of wanted and the panel says so, rather than printing totals that look like they add up. Commit: `a2d858abb`.
- [x] Hero + episode-panel density pass, from Boulder's screenshots of the Dark Matter page:
  - The Trailer button was invisible. It paints the poster-sampled accent as its background with hard-coded white text, and a pale poster sampled to near-white. Text on the accent is now derived from that accent's luminance.
  - Nine same-sized hero buttons across three rows gave `Play` and `Manage Poster` equal weight. Poster/Manage/Sync/Watched moved behind one `More` menu; a lone survivor stays in the row rather than hiding behind a menu of one.
  - The star rating printed in the meta line while IMDb/Trakt/TVmaze printed theirs two rows below. The meta line now only carries it when that row is empty.
  - An episode's action buttons sat most of a screen away from the episode on a wide monitor. Took three goes to find the right layer: capping the whole page centred the hero (read as "the page shrunk"), then left-anchoring the cap left a dead band down the right of every list. The cause was the ROW grid giving its text column the `1fr`, so every extra pixel went into the description and pushed the buttons out. Capping the text column and packing the tracks left fixes it at source, and the page keeps its full width.
  - Fourteen guest stars, mostly grey initials, out-shouted the episode's own actions. Folds after eight; the CSS cut-off and the button's count are pinned to each other by a test.
  - The expanded episode reprinted the description the row above already showed, and its `No extra info.` empty state was unreachable code. Both fixed.
  - The acquisition panel moved from the page basement to the top of the body, where it is actually findable. Commits: `92a2b3347`, then `ae5679a20`, `1e3045d5a` and `24658e3c3` for the layout, which took three goes to land in the right layer.
- [x] Add per-title overrides. The quality profile half already shipped; this adds preferred sources, release-group allow/block and season-pack preference as seven nullable columns on movies/shows, edited in the manage panel beside the profile picker. Wired at three engine seams: the source chain in `_default_search`, candidate filtering after each source's search, and the season-pack-first decision. Empty everywhere means FOLLOW THE GLOBAL CONFIG — an empty allow-list read as a filter would silently stop a title being grabbed at all, and report it as "no releases exist". Blocked releases are marked rejected WITH the reason rather than dropped, for the same reason. Commit: `732687d37`.
- [x] Show external ID health as a hero band: library id when it resolves, TMDB/TVDB/IMDb for movies and shows, channel or playlist id plus handle and download count for YouTube. Identity only, so it does not restate the meta line. Commit: `c78ff6ba5`.
- [x] Deepen external ID health: a missing TMDB/TVDB/IMDb chip on a library title is now the button that opens the manage panel's match search for that service, and every YouTube episode row states its video id as a link out. Preview titles stay inert, since there is no library row to re-match. Commit: `932a20aad`.
- [x] Add season-level actions. Grab season, manual search and wishlist season already existed but only appeared when episodes were missing; the bar now also carries a monitored/unmonitored toggle and a clear-failures reset, and shows on a complete season too, because a season you own in full is exactly the one you want to stop hunting. Clear-failures reuses the existing all-sources wishlist retry at season scope. Commit: `283db7251`.

## Calendar

- [ ] Overlay acquisition state on upcoming and recent episodes: owned, wanted, queued, downloading, failed, and ignored.
- [ ] Add a focused "needs action" calendar filter for aired-but-missing episodes.
- [ ] Add stale schedule warnings when the local episode list has not refreshed recently.
- [ ] Let calendar cards trigger search, retry, ignore, or detail drill-in without leaving the view.

## Automation And Ops

- [ ] Add source-health snapshots for Prowlarr, EXT.to/FlareSolverr, qBittorrent, slskd, and YouTube.
- [ ] Track Prowlarr per-indexer result counts and failures so weak indexers become visible.
- [ ] Add disk and temp-space guardrails before YouTube and torrent postprocessing starts.
- [ ] Add an import-list setup check for users who want Radarr/Sonarr-like external discovery.
- [ ] Emit structured acquisition audit events so the UI can explain exactly why an item did or did not download.

## Verification Standard

- [ ] Focused Python tests for each backend behavior change.
- [ ] Static JS/CSS tests for critical UI wiring and regressions.
- [ ] Browser screenshot QA for search, wishlist, detail, and calendar pages after larger visual passes.
- [ ] Phase commits after each completed slice so testing can happen incrementally.
