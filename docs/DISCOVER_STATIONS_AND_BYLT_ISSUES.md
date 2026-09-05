# Discover: Recommended Stations and Because You Listen To — diagnosis and implementation handoff

Date: 2026-09-05. Reviewed application code through `fdaaf48ea`.

## 1. Scope and current status

**Implemented 2026-09-05.** Every task below (B01–B06, S01–S04) is built and
tested, in the sequence §5 sets out. The implementation record — behaviour,
files, contract changes, validation, and what is still open — is
[DISCOVER_BIC_PROGRESS.md](DISCOVER_BIC_PROGRESS.md), under "Recommended
Stations and Because You Listen To (2026-09-05)". The release-gate sample is
[after-fix-sample.json](discover-stations-bylt-evidence/after-fix-sample.json)
and it is FIXTURE-DERIVED: none of this has been run against Boulder's real
library, and §7's closing caution stands unanswered — structural checks passing
is not evidence of musical relevance.

The text below is preserved as written, as the diagnosis these changes answer.

This was an investigation and implementation specification, not a claim that these features have been repaired. The preceding mix duration and download metadata fix was committed separately as `fdaaf48ea`. No station or BYLT feature changes are included in this handoff.

The user reports two substantive product failures: station cards offer playback without a way to inspect, download or sync their selection; BYLT shelves repeat substantially the same material under different reasons and sometimes display a duplicate, nearly empty shelf. Both are supported by the code. The repetition is also confirmed by live responses and a read-only database audit.

Implement the tasks below using the existing playback, download, sync and curated snapshot infrastructure. Do not create parallel acquisition engines. Do not treat a visual redesign alone as completion. The underlying recommendation identities, selection, persistence and action contracts must be repaired first.

## 2. Evidence and confidence

Evidence files captured during this investigation:

- [Live endpoint payloads](discover-stations-bylt-evidence/live-payloads.json): `/api/discover/because-you-listen-to` and `/api/discover/stations`.
- [Read-only database audit](discover-stations-bylt-evidence/database-audit.json): stored shelf slots, dates, track coverage, seed lookup and genre evidence.
- The user's screenshot: repeated Halogen artwork beneath Katy Perry and Ariana Grande, followed by another Ariana Grande heading with one card.

The audit did not start playback, download, sync or force a recommendation regeneration. Payload and database observations are separate reads; background jobs may run between reads.

| Finding | Evidence | Interpretation |
|---|---|---|
| Katy Perry shelf has 10 tracks, 8 by Halogen from Baked | Live response | Artist and album concentration are real, not merely identical placeholder artwork. |
| First Ariana Grande shelf also has 10 tracks, 8 by Halogen from Baked | Live response | Both shelves have the same concentration problem. |
| Those two shelves share 9 exact track IDs | Live response | 90% overlap per shelf; Jaccard overlap is 9/11, about 82%. Reordering is not distinct discovery. |
| Third shelf repeats Ariana Grande with one Scorpixter track | Live response and persisted row | A duplicate heading and sparse result are persisted, not just a client rendering issue. |
| Slots 0 and 1 are dated September 5; slot 2 is dated August 29 | Database | Consistent with old slots surviving a later partial generation. Source confirms no complete-set reconciliation. |
| Slot 2 stores one ID and that ID still resolves | Database | **This particular one-card shelf is not evidence of nine evicted tracks.** It was already stored with one track. |
| Katy Perry and Ariana Grande are absent from this profile's watchlist | Database | The generator's watchlist-only seed lookup cannot resolve these seeds through its current direct similarity path. |
| Katy Perry, Ariana Grande, Halogen, Drama and Ruth B. all have Pop in the inspected genre data | Database | Generic genre overlap explains why fallback can admit the same artists for both seeds. It does not establish strong seed-specific relevance. |
| Stations endpoint returns 10 artist seed cards, without track snapshots | Live response and source | Download/sync lacks a finite, inspectable selection contract. |

Do not claim that Halogen is inherently unsuitable for either listener. The defect is weak evidence of relevance, excessive concentration and indistinguishable shelves. Multi-profile failures, source-switch hydration loss and pool-window eviction are source-supported risks; this audit did not reproduce all of them live.

## 3. BYLT root causes

### B01 — Seed similarity loses identity before ranking even begins (P1)

Inspect `core/watchlist_scanner.py` around 4017–4096, especially the `_wa_id_to_name` lookup, and `database/music_database.py::get_top_similar_artists` around 14193.

The seed name map uses watchlist database row IDs. Modern similarity writes in `core/watchlist_scanner.py` around 2746 use a supplied/provider artist ID, with a watchlist row ID only as a final fallback. The `SimilarArtist` dataclass comment describing the field as a watchlist row ID is therefore not a reliable description of all stored data. Provider IDs and internal row IDs cannot be compared as though they share one namespace.

Even if those IDs happened to match, the lookup only covers watchlisted artists. Listening to an owned artist does not imply that it is watched. The two observed seeds are not watched, making the direct path unavailable for them.

There is a second independent defect: `get_top_similar_artists` is an aggregate recommendation query. It groups by similar artist name and selects `MAX(source_artist_id)` and other aggregate fields. It does not preserve every seed-to-candidate edge. Its global limit of 200 is also not a per-seed candidate budget. Increasing that limit alone cannot restore lost edges.

Required implementation:

1. Introduce a seed-specific raw-edge query, scoped to the correct profile and provider identity. Do not reuse the grouped top-similar query for this purpose.
2. Resolve listening seeds through canonical library/provider mappings, with watchlist mappings as an additional source rather than a prerequisite.
3. Represent provider identity as a pair such as `{provider, artist_id}`. A numeric Deezer ID must not collide with an iTunes ID or an internal row ID.
4. Handle legacy mixed semantics explicitly. Migrate rows only where their origin is provable; mark ambiguous edges unusable rather than guessing by numeric equality.
5. Preserve relationship source, rank and seed identity through candidate scoring and the resulting shelf.
6. Review `_build_listening_recommendations` around 4126 onward and the `core/discovery/listening_recommendations` helpers for reusable raw-edge handling. BYLT and the separate listening recommendation feature must not be confused.

Acceptance: an artist absent from the watchlist but present in listening history can produce a shelf from valid provider edges; edges from two seeds survive independently; equal IDs from different providers never cross-match; ambiguous legacy edges fall back truthfully.

### B02 — Pool insertion order masquerades as recommendation quality (P1)

The generator chooses the first available discovery source and reads at most 2,000 tracks. `get_discovery_pool_tracks` orders by newest `added_date`. Each seed walks this same list, collects the first 15 matches, shuffles them and saves 10. There are no per-artist, per-album or cross-shelf diversity limits. Shuffling after truncation cannot fix an album-heavy candidate set.

When direct similarity fails, any genre intersection may suffice. Genre lookup is built from library artists, so candidates absent from that cache may be missed. Generic Pop is not discriminating evidence. The fallback stops after 20 matching artist names, again influenced by pool order.

Required implementation:

- Extract a pure, testable candidate selection function. Separate collection, relevance scoring, deduplication, diversification and final ordering.
- Collect candidates per seed relationship rather than accepting the first insertion-ordered batch. Use bounded, explicit per-related-artist budgets and observable provider coverage.
- Score direct relationships above genre fallback. Weight specific shared tags more than generic tags; use normalized genre identities. Unknown genres must remain unknown.
- Deduplicate provider-equivalent recordings where reliable mappings exist. Do not collapse remixes, live recordings or distinct versions merely because titles look similar.
- Starting product targets: 10 tracks, at most 2 per primary artist, at most 1 per canonical album, and at least 4 distinct artists for a full shelf. These are proposed defaults to validate, not a claim of industry-standard optimality.
- Allocate tracks across the visible shelf set, not independently. Prefer zero exact-recording overlap when enough relevant candidates exist. Assign a shared candidate to its strongest seed, then backfill the other shelf.
- Use stable tie-breaking within a generation. Refresh should not mean an arbitrary shuffle of the same weak 15 tracks.
- Do not pad to 10 with unrelated material. If evidence is insufficient, return fewer shelves or a clearly labelled broader fallback.
- Define familiarity deliberately: owned and unowned tracks may coexist, but expose library status and do not call owned material new to the library.

Acceptance: a fixture with a newly ingested 20-track album cannot monopolize a shelf; the captured Halogen-heavy pattern fails the diversity gate; distinct valid seed edges produce distinct selections; limited supply produces an honest compact/empty result rather than invented relevance. Record candidate counts before and after each filter to make this diagnosable.

### B03 — Ordinal slots retain old generations and split labels from tracks (P1)

The scanner writes `because_you_listen_to_0..2` only when a slot has matches. Skipped slots are not cleared. Artist labels live separately in global `bylt_artist_0..2` metadata. Track lists are profile-scoped. The API loops all three slots regardless of whether they belong to the same generation.

The old one-track Ariana Grande slot is consistent with exactly this failure: a later generation updates other positions without removing the unused old position. Do not patch this solely by hiding duplicate names in React; that conceals invalid stored state.

Required implementation:

1. Persist one complete shelf-set generation with a schema version, profile ID, source context, generation ID, timestamp and ordered sections.
2. Give each section a stable seed identity, not an ordinal identity. Keep its heading, explanation and track snapshot in the same scoped record.
3. Build a generation fully, validate it, then atomically replace the visible generation. A successful empty generation replaces the old set with an explicit empty state.
4. Distinguish failure from empty success. On failure, retain the last valid generation with stale/error metadata instead of replacing it with a deceptive empty success.
5. Migrate legacy slots with explicit provenance. Never combine an old slot with a new generation. Do not delete another profile's data while retiring global keys.
6. Audit `get_top_artists` around 5685: it reads listening history without a profile argument. Define and implement profile attribution before promising personal recommendations. If history is intentionally shared, say so in the product and data contract.

Acceptance: 3 shelves becoming 2 removes the third; seed rank changes never duplicate a seed; zero valid sections clears old successful content; interrupted writes expose either the old or new complete generation; two profiles cannot overwrite headings or consume the other's personal history unintentionally.

### B04 — Hydration can silently shrink or erase saved shelves (P1)

`api/discover_routes.py` around 1075–1145 resolves bare saved IDs against only the newest 5,000 tracks from the currently active source. Missing IDs disappear silently. The generator may have selected a different fallback source. Mapping only Spotify/iTunes/Deezer IDs creates additional provider-contract limitations.

This is a real structural risk, but **was not the cause of the captured one-card slot**, whose stored count was already one.

Required implementation:

- Persist complete display/action metadata with the generation, following the established `core/discovery/curated_full.py` approach where suitable. Alternatively, hydrate exact requested identities through an indexed lookup without a recency-window limit.
- Keep provider and source identity with every ID. Never resolve a stored source-A ID as source B merely because the user's active source changed.
- Preserve title, artist credits, album identity/title, artwork, explicit `duration_ms`, provider references and available library identity. Reuse the committed metadata normalizers at action boundaries.
- Return requested, resolved and unavailable counts plus meaningful unavailable reasons. Do not silently advertise a count that differs from the actionable selection.
- Return real failure status for endpoint failures. The current exception path reports `success: true` with no sections, hiding errors and allowing them to be cached as success.
- Document provider coverage and add contract tests for every supported source. Unsupported sources should yield a clear unavailable result, not an unexplained blank shelf.

Acceptance: a snapshot remains inspectable after its tracks leave the recent pool window; source switches do not relabel identities; missing items remain explainable; duration units and artists survive through preview, playback, download and sync.

### B05 — Cache and refresh contracts can preserve obsolete content (P2)

The shelf cache in `api/discover_routes.py` around 47–76 lasts 30 minutes; the BYLT key does not include a source/generation extra. The client query in `-ui/discover-page.tsx` around 316 uses `['discover', 'bylt']` with infinite stale and garbage-collection times. Verify application-wide invalidation before changing shared behavior; this audit has not established that every profile switch leaks client data.

Required: make profile/source context explicit in client and server keys; expose generation identity; invalidate or revalidate after successful generation, relevant source/profile changes and supported preference changes. Prevent an old request from replacing a newer profile's result. Preserve valid visible content during refresh and show retryable failures distinctly from insufficient recommendations. Define a finite freshness policy and test it with a fake clock. Do not add aggressive polling as a substitute for correct invalidation.

### B06 — Track cards conceal identity and have incomplete interaction semantics (P2)

Inspect `-discover.bylt.ts`, `-ui/bylt-sections.tsx`, and `-ui/discover-page.tsx` around 853. The view model omits track identity and useful metadata. Cards are clickable divs. Clicking a track-shaped recommendation invokes an album-resolution path using name strings; users cannot clearly distinguish playing that track from opening its album.

Required presentation:

- Identify the section as track recommendations. Keep seed portrait and heading, then add a short truthful reason such as a direct relationship or shared specific genre. Never fabricate provider explanations.
- Show track title, artist and an accessible full-title treatment; expose album and duration in a readable expanded/list view. Avoid ten album-sized cards that all appear to be the same album.
- Use native controls. Give the primary action a clear meaning, such as opening track details. Provide explicit Play, queue and acquisition actions where supported; offer Open album separately.
- Preserve stable identities through every action. Never resolve a specific track solely from a display title if its provider ID is known.
- Use compact treatment for 3–5 useful tracks. Below the agreed quality threshold, omit the full horizontal shelf or present a truthful insufficient-data module. Never leave a single card consuming a full shelf's height by accident.
- Add library/download state, per-item pending/error feedback and feedback controls only when their persistence and recommendation effects are implemented. A decorative dislike button is not complete.
- Support keyboard activation, visible focus, accessible action names, 200% zoom, narrow screens and reduced motion. Avoid nested buttons. Touch users must have the same actions as hover users.

Acceptance: keyboard users can reach and activate all actions; long titles remain discoverable; clicking a track has an unambiguous result; the screenshot's repeated-art pattern is reduced by real selection diversity, not hidden with alternate artwork.

## 4. Recommended Stations: required product and technical changes

### S01 — Define a finite station snapshot without interrupting radio (P1)

`core/discovery/stations.py::build_stations` emits an artist ID, name, image and companion names. It selects artists with at least three library tracks carrying file paths. `stations-row.tsx` calls `window.startArtistRadioById`; `webui/static/stats-automations.js` around 6044 chooses a random owned track and starts radio. `media-player.js::npFetchRadioTracks` refills from `/api/library/radio` using current playback state. There is an evolving queue, but no immutable station playlist returned to the card.

Implement this concrete default: **View station** opens a finite preview of up to 40 library tracks generated from the station seed. **Play radio** retains the existing non-stop behavior. Preview generation must not call the playback bridge, pause audio or mutate the current queue. Forty is a proposed product default; keep the limit explicit and bounded.

A snapshot service should return an immutable snapshot ID, profile/source context, station seed identity, generation time, algorithm version, ordered tracks, counts and supported actions. A suggested route is `POST /api/discover/stations/{artist_id}/snapshot`; adapt naming to repository conventions. This operation creates a preview only, never starts acquisition or playback.

Reuse the existing radio selection logic where feasible. Apply exclusion/diversity rules consistently and return the actual selected tracks. Keep a preview stable while the user selects tracks. A refresh creates a new revision; it must not silently swap the selection under an open download modal.

An optional later action, Save current radio queue, is a different operation: capture the current ordered queue at one instant and label it accordingly. Do not conflate it with generating a new station preview.

Acceptance: previewing a station while another track plays leaves playback and queue unchanged; fewer than 40 available tracks yields the honest count; unavailable/empty results explain why; repeated actions use the same selected snapshot until explicit refresh; profile changes cancel or discard obsolete responses.

### S02 — Add download and sync using that exact selection (P1)

Current radio selects library rows, so acquisition may legitimately have zero missing tracks. A file path is evidence of a library reference, not proof that the file is still accessible. Do not claim every track is definitely on disk without availability checks.

The station preview must offer selection controls, Download selected and Sync playlist. Download selected should enter the existing library analysis flow and truthfully report when everything selected is already available. Do not queue redundant downloads to make the button appear useful. Sync should create/update the chosen supported destination playlist using the finite snapshot. Export may be offered through existing infrastructure.

If the product later supports stations containing unowned recommendations, introduce an explicit discovery mode with provider resolution and availability reporting. Do not silently change owned-library radio into external acquisition to satisfy this request.

Reuse `-discover.use-playlist-sync.ts`, `webui/static/core.js::startDiscoverVirtualSync` and `webui/static/downloads.js::openDownloadMissingModalForYouTube` through an appropriately named adapter. The legacy function name must not cause the modal to mislabel a SoulSync station as by YouTube. Carry an explicit display/source context instead of adding another ambiguous fallback.

Requirements:

- Stable operation identity includes profile, station and snapshot revision. Do not reuse a Daily Mix sync key.
- Preserve library IDs separately from provider IDs and normalize duration units once at the boundary.
- Pass the exact selected tracks, artist credits and album data. Selection cannot change as radio refills.
- Use existing duplicate-job protection, destination validation and progress/error contracts. Retrying must not create duplicate playlists or downloads.
- Explain finite scope in copy: Sync these 40 tracks, not Sync endless radio. A continuously maintained station playlist would need a separately specified automation policy.
- Show unsupported destination or unavailable capability explicitly; do not silently do nothing when a global bridge is absent.

Acceptance: sync receives exactly the checked snapshot tracks; all-owned analysis reports zero missing correctly; metadata regression tests cover both seconds-derived and millisecond-derived inputs; retry is idempotent; failed sync remains retryable with selection intact.

### S03 — Make companion names truthful (P2)

Station companion labels come from `similar_artists`, while `database/music_database.py::get_radio_tracks` around 20252 selects using same-artist, genre, mood/style and library fallback tiers through `core/radio/selection.py`. The subtitle's artist list is not an enforced input to the queue. Some named artists may not be available at all.

Use With X and Y only for artists actually present in the finite preview or guaranteed by the relevant playback contract. Otherwise use a clearly weaker Related artists label, or omit the names. Do not imply a preview guarantees the entire future radio stream. Test an unowned related artist and a library with no eligible companion tracks.

### S04 — Make station controls observable and accessible (P2)

`stations-row.tsx` currently fetches into local state, collapses failure to an empty array and exposes one playback button with no card-level pending/error contract. Replace this with explicit loading, empty, failed and loaded states, using the application's query conventions and profile-aware keys.

Use a card with separate native View station and Play radio controls, plus a labelled menu if space requires it. Provide a visible starting state for the clicked station only, prevent accidental duplicate activation and report failure close to the action. Audit the station playback bridge's completion contract; do not mark success merely because a function was invoked. Missing bridges must produce a recoverable error.

On mobile, keep the primary controls visible and give menus adequate touch targets. The preview dialog needs an accessible name, focus management, Escape/close behavior and focus restoration. Pending snapshot requests must not reopen a dismissed dialog. Test switching profiles and rapidly opening two stations.

## 5. Implementation sequence and review boundaries

1. Add regression fixtures from the observed BYLT pattern and tests for seed identity, stale slots and profile scoping.
2. Implement B01 and B03 together enough to establish trustworthy seed and generation identity. Do not prioritize cosmetic deduplication over this foundation.
3. Implement B02 diversification and explainable fallback against those identities.
4. Implement B04 snapshot/hydration and B05 invalidation. Confirm old stored rows have an explicit migration path.
5. Implement B06 presentation and actions against the completed contract.
6. Implement S01 snapshot service, then S02 acquisition/sync adapters, then S03/S04 truthful card presentation and feedback. These station tasks may be reviewed independently of the BYLT backend changes.

For each implementation PR, report: reproduced problem; resulting behavior; files and contract changes; validation commands/results; failure and accessibility behavior; remaining limitations. Update `DISCOVER_BIC_PROGRESS.md` and the original review only after the corresponding acceptance criteria pass. Partial delivery must name what remains. Do not tick an entire feature complete because its happy-path button renders.

## 6. Required verification matrix

| Scenario | Required result |
|---|---|
| Listening seed is not watched | Valid provider similarity still works. |
| Provider IDs collide numerically | No cross-provider artist or track matching. |
| Two seeds share some related artists | Best-fit allocation avoids near-identical shelves when supply permits. |
| One newly ingested album dominates the pool | Artist/album caps hold before final ordering. |
| Only generic genre evidence exists | Truthful weaker reason or insufficient-data treatment. |
| Three shelves become two; seed ranks change | No stale third shelf or duplicate heading. |
| Generator fails halfway | Previous complete generation remains; failure is observable. |
| Two profiles generate concurrently | Headings, selections and listening provenance remain correctly scoped. |
| Saved tracks leave newest 5,000 pool rows | Snapshot stays inspectable; unavailable actions are explained. |
| Source changes while request is pending | Old source response cannot replace the new context. |
| Endpoint/provider fails | Retryable error, not cached fake empty success. |
| Station preview while music is playing | Playback and current queue unchanged. |
| Station snapshot contains only owned tracks | Analysis honestly reports no missing acquisition. |
| Station selection is synced twice after retry | No accidental duplicate destination playlist/job. |
| Radio refills while preview/download is open | Checked snapshot identities remain unchanged. |
| Duration and artist formats vary by provider | Correct duration and credits through every modal/action. |
| Missing browser bridge or destination | Visible recoverable failure, no silent no-op. |
| Keyboard, narrow screen, long names, 200% zoom | Reachable actions, visible focus, readable content and usable dialogs. |

Use backend unit tests for identity, ranking and generation transactions; API tests for contracts and source/profile behavior; frontend interaction tests for selection and actions; one live smoke test for the integrated preview-to-analysis/sync handoff using an appropriate test destination. Existing station tests and `curated_full` tests provide starting points. Existing tests of rendering and the old play handler do not establish download/sync correctness.

## 7. Release gate and diagnostics

Before calling this complete, preserve a new evidence sample containing generation identity, distinct artist/album counts, pairwise shelf overlap, reason provenance, stored/resolved counts and station snapshot/action results. Keep operational IDs and scoring diagnostics in logs or an appropriate diagnostic surface, not normal user-facing copy.

Minimum gate: no unresolved P1 issue above; no duplicate seed within a generation; diversity limits hold when candidate supply permits; all fallbacks are truthful; metadata survives every action; station preview is side-effect-free with respect to playback; download and sync operate on the exact finite selection; failure and keyboard paths pass.

These changes are prerequisites for a strong discovery experience. Passing structural diversity checks does not itself prove best-in-class musical relevance. Evaluate several real listening profiles, sparse libraries and provider combinations after implementation, then tune relevance with evidence rather than declaring success from one visually improved screenshot.
