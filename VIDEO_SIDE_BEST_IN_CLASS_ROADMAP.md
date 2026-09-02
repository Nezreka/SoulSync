# Video Side Best-In-Class Roadmap

This is the working checklist for making SoulSync's video side feel better than the usual Radarr/Sonarr split: clearer UI, stronger hybrid acquisition, and better recovery when items stall.

## Already Knocked Off

- [x] Fold EXT.to into torrent auto-search results, so torrent mode gets Prowlarr plus EXT.to instead of treating EXT.to as a separate manual-only lane. Commit: `1b09ece53`.
- [x] Preserve EXT.to provenance while enqueueing it as a torrent transport. EXT.to rows keep `indexer_id=extto` and display provenance, while downloads persist as torrent-backed records.
- [x] Record download-client refusal notes on wishlist rows without inflating search attempts. Commit: `d4639504b`.
- [x] Sweep completed YouTube wishlist rows before requeueing. Commit: `5db0bcaf4`.
- [x] Migrate video RSS sync from hourly legacy cadence to the faster Arr-style cadence. Commit: `c56f20c72`.
- [x] Warn immediately when wishlist auto-search cannot run because a movie, TV, or YouTube target folder is missing. Commit: `861936c7a`.

## Search Page

- [x] Make every manual search hit show source, transport, indexer, and grab eligibility without requiring expansion.
- [x] Surface rejection/review reasons directly on the card, with the full detail still available in the expanded facts panel.
- [ ] Add per-source counts for usable, review, and no-link results in the source tabs.
- [ ] Show the query variants attempted for TV episodes and season packs, so users can tell when matching is too narrow.
- [ ] Add a safe "try anyway" path for manual grabs that failed soft rules but still have a valid link.
- [ ] Bias TV searches toward season packs when many wanted episodes from the same season are missing.

## Wishlist Processing

- [ ] Add a stuck-row diagnostics drawer: last searched, last refusal, attempts, source order, target folder, matched IDs, and queued download state.
- [ ] Store per-source search outcome snapshots for wanted items: result count, rejected count, accepted count, and top refusal reason.
- [ ] Add adaptive TV query fanout: canonical title, aliases, year/no-year, `SxxExx`, `season x episode y`, and scene-number variants.
- [ ] Add season-pack-first drain when multiple episodes from a season are missing and pack rules allow it.
- [ ] Add temporary source cooldowns when a client or indexer repeatedly refuses the same item.
- [ ] Add a one-click "retry with all sources" action for stale wishlist rows.

## YouTube

- [ ] Separate unavailable, members-only, private, age-gated, cookie-needed, throttled, postprocessing, and disk-space failures in row state.
- [ ] Add a YouTube health tile covering yt-dlp version, cookies status, temp space, output space, and recent HTTP failures.
- [ ] Finalize stale active/importing rows that no longer map to a live download worker.
- [ ] Offer per-channel and per-playlist retry policies, including archive recheck cadence.
- [ ] Prefer the configured hybrid fallback only where it makes sense: YouTube-native first, then alternate search when metadata is strong enough.

## Detail Page

- [ ] Add an acquisition panel showing owned, wanted, queued, downloading, failed, and ignored states in one place.
- [ ] Add per-title overrides for quality profile, preferred sources, release group allow/block, and pack preference.
- [ ] Show external ID health: TMDB, TVDB, IMDb, YouTube channel/playlist/video IDs, and library IDs.
- [ ] Add season-level actions: search missing, grab season pack, mark monitored/unmonitored, and clear stale failures.

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
