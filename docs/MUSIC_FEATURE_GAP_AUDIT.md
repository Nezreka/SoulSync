# Music feature coverage and gap audit

Reviewed 2026-09-05 against `9b8bec8c8`. **Investigation only; no feature changes.**

Purpose: identify what the music product already does, where user journeys break or stop short, and what to implement next. This file was saved and updated during investigation to preserve progress across context changes.

## 1. Audit status and evidence

- [x] Feature inventory cross-checked against implementation.
- [x] Discovery and recommendation lifecycle.
- [x] Playback, queue and listening experience.
- [x] Search, library browsing and metadata-management coverage.
- [x] Playlist import, editing, sync and history.
- [x] Watchlist, wishlist, downloads and recovery.
- [x] Profiles, setup and operational visibility.
- [x] Consolidated findings and document completeness verified.

Method: targeted source tracing across the music routes, browser player, backend and existing review documents. This is not an exhaustive live test of every integration, a security audit, or a comparison with current competitor releases. No watchlist scan, playback, acquisition, external playlist write or configuration change was performed.

**Evidence classes:** Confirmed = inspected code establishes the limitation, not necessarily live reproduced. Product gap = a coherent capability was not found in the inspected journey, although related infrastructure exists. Validation gap = feature exists but the required behavior is not established; investigate before changing policy or claiming a defect.

**Priorities:** P1 = correctness, trust or completion of a core workflow; P2 = substantial everyday functionality; P3 = optional expansion. Effort S/M/L is a planning estimate, not a commitment. Keep finding IDs stable as implementation progresses.

## 2. What already exists — do not rebuild it

| Area | Verified existing capability | Main opportunity |
|---|---|---|
| Discovery | Daily Mixes, BYLT, Release Radar, Discovery Weekly, seasonal/genre/decade mixes, cache shelves, stations, artist/genre graphs | Refresh, exclusions, taste feedback, relevance |
| Playback | Queue/reorder, shuffle/repeat, radio, missing-track preparation, lyrics, sleep timer, Media Session, experimental crossfade | Resume, seamless transitions, keyboard queue, saved collections |
| Library | Artist grid, discography, enhanced editing, manual matching, tag preview/write, quality upgrades, organization | Cross-library album/track browsing |
| Search | Multiple providers, ID lookup, recent searches, library checks, raw Soulseek search, fallback | Library-first unified results |
| Playlists | Provider/file/link imports, mirrors, seed builder, explorer, sync, server compare/add/remove/replace/reorder, export/materialized folders | Native user playlists and exact revisions |
| Acquisition | Hybrid sources, quality profiles, retries, watchlist filters, wishlist, live candidate/source details, verification/quarantine | Connected track lifecycle and recovery |
| Recovery | Failing filter/manual search/temporary ignore, grouped repair findings, safety metadata, deleted-file restoration, backups | Handoffs and recovery validation |
| Automation | Workflows, schedules, signals, pipelines, jobs, history, notifications | Dependable status and clear lifecycle |
| Listening | Stats, Last.fm/ListenBrainz, server favorite/rating ingestion | Profile attribution and user taste actions |
| Administration | Setup wizard, settings, profiles/page access, standalone/Plex/Jellyfin/Navidrome | Awaited saves and capability-aware states |
| Mobile/web | Responsive CSS, dialog helpers, image/static service-worker cache | Verified task parity; optional offline audio |

Entry points: `webui/src/routes/`, `webui/static/media-player.js`, `webui/static/setup-wizard.js`, `core/playlists/`, `core/discovery/`, `core/personalized/`, `core/imports/`, `core/wishlist/`, `database/music_database.py`.

Recently repaired: mix duration/artist metadata, finite station preview/download/sync, compact four-track BYLT previews, station partial failures, radio startup repeat-click protection and inline errors. **Do not reopen these as unimplemented features.** Remaining availability and listening semantics below are separate concerns.

## 3. Prioritized list

| ID | Gap | Class | Priority | Effort |
|---|---|---|---|---|
| M01 | Personal listening history is not reliably personal | Confirmed | P1 | L |
| M02 | Discovery exclusions are inconsistent and cross profiles | Confirmed | P1 | M |
| M03 | Queue persistence does not resume a listening session | Confirmed | P2 | M |
| M04 | Crossfade reloads instead of seamlessly continuing | Confirmed | P2 | M/L |
| M05 | Queue play/reorder lacks keyboard equivalence | Confirmed | P1 | S/M |
| M06 | No clear native favorites/personal-playlist journey | Product gap | P2 | L |
| M07 | Main library lacks equivalent album/track browse views | Product gap | P2 | M/L |
| M08 | Search requires source selection instead of one answer | Product gap | P2 | L |
| M09 | Recommendation refresh depends on watchlist curation | Confirmed | P1 | M |
| M10 | Taste feedback is weaker than permanent blocking | Product gap | P2 | M/L |
| M11 | Playlist history lacks restorable content revisions | Confirmed in inspected pipeline | P2 | L |
| M12 | Old export status can erase newer job state | Confirmed | P1 | S/M |
| M13 | Recovery context is split across operational screens | Product gap | P2 | M/L |
| M14 | Setup advances/completes without confirmed save success | Confirmed | P1 | M |
| M15 | Local-file presence is confused with playability | Confirmed | P1 | M |
| M16 | Shared identity/version contract coverage is incomplete | Validation gap | P1 | M/L |
| M17 | Loudness tags do not establish normalized browser audio | Validation gap | P2 | M |
| M18 | Server downloads are not device-offline listening | Product gap | P3 | L |
| M19 | Explicit-content policy coverage needs verification | Validation gap | P1 if advertised as a restriction | M |
| M20 | Accessible/mobile tasks are not fully validated | Validation gap | P2 | M |
| M21 | Whole-journey speed and recommendation quality need evidence | Validation gap | P2 | M |

### M01 — Make personal listening history profile-aware

**Gap:** profiles isolate several settings and collections, but recommendation input remains shared. Household ownership is not personal taste. One person's frequent artist can shape another person's recommendations and stats.

**Evidence:** `database/music_database.py::_add_listening_history_table` and `get_top_artists`; BYLT passes a profile but the current history schema cannot attribute all listens. `core/discovery/stations.py::build_stations` calls top artists without a profile. BYLT declares shared history where applicable; that is useful honesty, not completed personalization.

**Resolve:** attribute new events to source account/user and local profile. Define account-to-profile mappings for external scrobbles. Retain old unassigned events as shared/unassigned; do not guess owners. Offer personal/shared recommendation modes intentionally. Make stats, recommendations and cache keys consume the same policy. Scope clear-history actions explicitly.

**Acceptance:** two profiles with different listening obtain independently attributable results; shared mode remains available; migration does not arbitrarily assign plays; remapping an external account does not silently transfer historical personal data.

### M02 — Enforce exclusions consistently without crossing profiles

**Gap:** blocking exists, but `get_discovery_blacklist_names` explicitly combines artist blocks from **all profiles**, and an album shelf uses it. One user's block can hide another user's music. BYLT/station generation has no explicit unified artist/album/track exclusion stage in the inspected path.

**Evidence:** `database/music_database.py::get_discovery_blacklist_names` around 16266; `api/discover_routes.py` around 1039 and `/api/blocklist`; BYLT/station builders. Download/wishlist guards already exist: reuse canonical matching instead of adding another name-only list.

**Resolve:** require profile identity in queries; share provider-aware policy across generation, snapshot serving, radio and acquisition. Distinguish hide from recommendations, never auto-download and reject this particular source file. Decide manual-play semantics deliberately. Invalidate affected recommendations when policy changes.

**Acceptance:** blocking in A does not affect B; artist/album/recording exclusions work across mapped providers; cached shelves respect changes; unblocking reverses them; versions and same-name artists remain distinct.

### M03 — Resume the session, not merely its track list

**Gap:** reload loses the listening position. Queue persistence saves an index without restoring it and saves no elapsed time. The queue storage key is browser-global rather than profile-specific.

**Evidence:** `webui/static/media-player.js::npPersistQueue`, `npRestoreQueue`, `NP_QUEUE_STORAGE_KEY` around 2786. Recent searches likewise use a global browser key in `search/-search.helpers.ts`; include them in the profile-state audit. This does not imply a server stream-session isolation defect: the backend already has session handling.

**Resolve:** persist profile/device session identity, queue revision, current track, elapsed position, context and shuffle/repeat. Offer Resume explicitly without autoplay. Validate availability and clamp positions. Scope storage on profile switch; integrate Save queue with M06.

**Acceptance:** reload at 2:10 offers approximately that position; another profile cannot inherit it; missing tracks offer recovery; corrupt/quota-limited storage does not break playback; completed tracks do not repeatedly resume at their end.

### M04 — Finish the actual crossfade/gapless transition

**Gap:** the second audio element starts the next track, then `npFinishCrossfade` pauses/clears it and calls `playQueueItem`, reloading the main element. This can interrupt the transition and restart material instead of continuing already-audible audio. Shuffle and repeat wrapping are excluded.

**Evidence:** `media-player.js::npCrossfadeTick`, `npStartCrossfade`, `npFinishCrossfade` around 2175–2250. The source acknowledges a silent reload. Its old single-global-stream comment should not be treated as current backend architecture; session-scoped streaming exists.

**Resolve:** promote the playing next element or transfer without restarting elapsed position. Determine the next queue identity before preloading, including shuffle/repeat. Separate gapless album playback from selected crossfade duration. Preserve gain, Media Session, visualizer and scrobble ownership; count one listen exactly once.

**Acceptance:** measured transitions have no reload/repeated intro; shuffled choice is the one heard; manual cancel/skip tears down correctly; gapless live-album playback works where format/browser permits; unsupported modes are visible.

### M05 — Make the queue usable without a mouse

**Gap:** rows are clickable draggable divs. Global keyboard shortcuts do not make every queue item playable or movable by keyboard.

**Evidence:** `media-player.js::renderNpQueue` around 2690; drag handlers around 2828. Remove is a button, but row play/reorder has no equivalent native control in that renderer.

**Resolve:** add named row Play and keyboard-accessible Move earlier/later, Play next and Remove actions. Preserve focus and announce position. Use queue-entry identities so duplicate recordings move independently. Touch should not require precise dragging.

**Acceptance:** keyboard-only users can play track 12, move it next and remove track 4 without losing focus or disturbing unrelated playback; announcements name track and position; no nested controls.

### M06 — Add native favorites and personal playlists

**Gap:** importing/mirroring is strong, but no clear local Like, Saved tracks, Create playlist and Add to playlist journey was found in the inspected player/music routes. Server favorite/rating signals already exist; the gap is not absence of all favorite data.

**Evidence:** music database curation signals around 17855; player; sync routes; `core/playlists/sources/`. Existing server editors and generated mixes remain useful.

**Resolve:** add profile-owned collections with stable recording identities, ordered entries and revisions. Expose consistent Save/Like/Add to playlist from player, library, search and discovery. Support rename/reorder/remove/duplicate/save queue/export/sync. Keep mirrors read-only by default or explicitly fork before personal editing. Specify external favorite reconciliation rather than overwriting server choices.

**Acceptance:** create a playlist without a streaming account; add from several surfaces; preserve order; sync to supported servers; deleting a playlist never deletes audio. Likes appear in Saved tracks and influence recommendations only according to an explicit setting.

### M07 — Browse the library by albums and tracks

**Gap:** the main library is an artist grid with alphabet/watchlist/source filters. Artist detail exposes albums/tracks, but there is no equivalent cross-library browse flow for recently added albums, all lossless tracks or albums from a decade. Repair screens are not a comfortable listening browser.

**Evidence:** `library/-ui/library-page.tsx`, `-library.types.ts`, `-library.api.ts`. Discover/stats subsets do not replace a complete owned-library index.

**Resolve:** add Artists / Albums / Tracks views using indexed pagination. Start with text, date added, release year, genre and availability; make quality optional. Preserve URL/filter/return state. Support selection and personal-playlist actions through shared contracts.

**Acceptance:** large libraries remain paginated/virtualized; filters/counts agree; album play follows disc/track order; selection works without visiting each artist; view switching retains sensible scope.

### M08 — Offer a library-first unified search mode

**Gap:** source tabs and per-source caches require users to know where to search. They are useful expert controls, but a listener requesting a song should first see what they can play, then alternatives.

**Evidence:** `search/-search.types.ts::SOURCE_ORDER`, `-search.use-controller.ts`, `-search.api.ts`. Recent searches, ID lookup and fallback already exist; do not list them as missing.

**Resolve:** add All sources/library-first search with immediate local results and progressive provider responses. Group confidently mapped recordings/releases, retaining source badges and version alternatives. Keep expert tabs. Distinguish local, server-streamable, obtainable and unknown. Bound provider concurrency, cancel obsolete requests and isolate individual provider failures.

**Acceptance:** local results do not wait for providers; one provider failure leaves others usable; covers/remixes/clean versions are not wrongly merged; query changes never show old results as the new answer.

### M09 — Give recommendations their own refresh lifecycle

**Gap:** users must know watchlist curation builds BYLT. A new algorithm can ship while old slots keep rendering. Refreshing the page cannot create a missing generation.

**Evidence:** `core/watchlist_scanner.py::_build_because_you_listen_to` and curation caller; BYLT endpoint legacy fallback; `-discover.bylt.ts::byltStatusNote`. The prior live review found legacy slots with no new generation. This audit did not rerun that database check or start a scan.

**Resolve:** extract a bounded recommendation-refresh job callable from Discover and automation. Show generated-at/source/status and Refresh recommendations. Schedule upgrade rebuilds and display updating/legacy status until complete. Preserve the last valid generation on failure with Retry. Do not rescan external artist catalogs when sufficient local inputs already exist.

**Acceptance:** fresh/upgraded installs can generate shelves without knowing scanner internals; double refresh coalesces; source/profile switches cannot mix results; completion updates the visible generation without hard reload.

### M10 — Let users steer recommendations without permanently banning music

**Gap:** blocking is blunt. BYLT/stations lack a complete inspected feedback loop for less of this, already know this, not this version or more adventurous. Listening frequency alone can reinforce old habits.

**Evidence:** `discover/-ui/bylt-sections.tsx` actions; `core/discovery/bylt.py`; `core/personalized/daily_mixes.py`; existing `/api/blocklist`.

**Resolve:** add profile-owned reversible feedback events with reason/entity/version/time. Start with Like, Not interested and Hide this version, plus an optional familiarity control. Give ranking bounded interpretable effects and decay temporary negatives. Provide undo/history. Explanations must reflect real evidence. Do not add controls before their effects are implemented.

**Acceptance:** feedback affects later generations and is reversible; disliking a remix does not erase its original; sparse supply is admitted; metrics detect an excessively narrow feedback loop.

### M11 — Store restorable playlist revisions and define sync authority

**Gap:** pipeline history captures aggregate before/after counts, not exact ordered contents. A playlist can change completely and still show 50→50. Re-running sync is not restoring a previous selection.

**Evidence:** `core/playlists/pipeline.py::_playlist_history_snapshot` around 197 stores names/source/counts; `sync/-sync.use-history.ts` re-runs jobs. Server compare/edit/reorder and conflict checks already exist in `web_server.py` and `core/sync/playlist_edit.py`.

**Resolve:** persist bounded content revisions with canonical ordered entries and provider/server mappings. Show additions/removals/replacements/reordering. Explain whether source, SoulSync or destination owns future edits. Offer restore-as-copy first, then guarded in-place restore using a fresh destination revision. Preserve intentional duplicate entries.

**Acceptance:** equal-count changes show actual tracks; restore prior content without redownloading owned files; concurrent destination edits produce a conflict; scheduled refresh does not silently erase personal changes.

### M12 — Make playlist job status belong to a specific operation

**Gap:** an old export completion timer can erase a newer status for the same playlist. Poll errors silently retry; navigation ends this hook's observation. Durable history should support reattachment.

**Evidence:** `sync/-sync.use-export.ts::paint`, timer handling and polling catch. The source comment explicitly preserves the old clearing race.

**Resolve:** key status/timers by job ID/revision; cancel superseded timers and ignore late responses. Reattach after navigation. Distinguish export failure from temporarily unavailable status. Keep terminal results in history instead of relying on disappearing text/toasts.

**Acceptance:** A's 12-second hide cannot clear B; returning to the page shows the real job; outage reports reconnecting; out-of-order completion cannot mark a newer job successful.

### M13 — Connect existing acquisition/recovery tools around one track

**Gap:** downloads have rich detail and wishlist has retries/manual search, but context is divided among task, batch, wishlist and library-history IDs. Users should not have to reconstruct why a track never became playable.

**Evidence:** `active-downloads/-adl.types.ts` live/history/review fields; `wishlist/-wishlist.types.ts` and list; import/re-identification and repair routes. This is a consolidation opportunity, not missing retry/error tooling.

**Resolve:** add a stable acquisition-intent/correlation identity and track timeline: request → source attempts → verification → import → library registration → playlist inclusion. Link existing screens into it. Offer reason-specific recovery: reconnect source, choose version, retry failed stage, review match or ignore temporarily. Carry original quality/version/metadata intent into manual search.

**Acceptance:** from one failed wishlist entry reach its attempts and review/import outcome without logs; retry resumes necessary work only; completed work is not duplicated; legacy uncorrelated steps are labelled unknown.

### M14 — Make setup completion mean saved and validated

**Gap:** `wizardNext` starts an async save and advances immediately. `_saveWizardStepSettings` does not check HTTP/application success and reports network errors only in the console. Closing marks setup complete even when dismissed early.

**Evidence:** `webui/static/setup-wizard.js::wizardNext`, `_saveWizardStepSettings` around 120–169 and `closeSetupWizard`. Setup and first-download flows already exist.

**Resolve:** await a typed save result, retain the step on failure and show actionable errors. Disable duplicate Next while saving. Separate Skip for now from Finish setup. Persist progress independently from validated configuration. Reuse connection tests and provide an explicit optional end-to-end test; never start acquisition automatically simply to mark readiness.

**Acceptance:** rejected saves cannot advance; reopening resumes incomplete setup; Finish reflects persisted values; closing does not falsely claim readiness; standalone and server modes validate their own required fields.

### M15 — Separate owned, local, streamable and downloadable

**Gap:** station snapshots use `os.path.exists(raw_path)` for availability, and the modal calls unavailable rows missing from disk. Playback can resolve mapped paths and proxy unmounted Navidrome tracks. Locally absent does not mean unplayable; a saved boolean also ages badly after mount/server changes.

**Evidence:** `core/discovery/stations.py::_snapshot_row`; `discover/-ui/station-modal.tsx`; `web_server.py::stream_library_audio` around 11437 uses `_resolve_library_file_path` and `_build_library_stream_url`. Not live-tested against a remote server during this audit.

**Resolve:** share an availability contract: owned, local-accessible, server-streamable, acquisition-required, unknown, reason and checked-at. Reuse playback resolution instead of raw existence checks. Refresh transient availability separately from immutable playlist selection with bounded caching.

**Acceptance:** an unmounted streamable track offers Play without a false missing warning; disconnected servers report unavailable/unknown and recover; owned-but-missing tracks go through analysis before redownload; selection stays stable as availability changes.

### M16 — Lock down shared identity/version/metadata contracts

**Gap:** music actions bridge provider metadata, library rows and legacy browser shapes. Recent duration/artist bugs show why this matters. Those specific bugs are fixed; the remaining gap is evidence that their invariants hold across every source and workflow.

**Evidence:** `discover/-discover.helpers.ts`, `-discover.stations.ts`, `core/discovery/curated_full.py`, `core/playlists/sources/` and modal adapters. BYLT ownership uses title/artist lookup in `core/discovery/bylt_view.py::owned_lookup_from_library`; validate versions before treating a particular recording as owned.

**Resolve:** specify a shared recording/release action contract: namespaced provider IDs, separate library ID, explicit duration unit, artist credits, release/version and availability. Normalize only at boundaries. Prefer reliable exact IDs/ISRC/MBIDs, preserve confidence and distinguish remaster/live/remix/clean variants. Do not infer canonical identity solely from display names.

**Acceptance:** per-source fixtures survive search → preview → queue → analysis → import → sync; no 1000× durations, missing-artist regression or wrong-version ownership; incomplete metadata stays explicitly unknown.

### M17 — Verify and expose browser loudness normalization

**Gap:** ReplayGain analysis/tagging exists, but the inspected player does not show ReplayGain/loudness consumption. Written tags are not proof the browser honors them. Measure before claiming normalization is absent across every stream/transcode path.

**Evidence:** README post-processing support and repair finding types in `tools/-tools.groups.ts`; no ReplayGain/loudness handling found in the inspected player/shell code.

**Resolve:** trace tagged fixtures through real playback. If not normalized, add Off / Track / Album normalization, headroom and clipping protection. Prefer album gain during album playback; avoid double gain from media-server processing. Keep separate from permanent audio modification and the volume slider.

**Acceptance:** measured fixtures follow the selected mode without clipping/double gain; transitions preserve gain; missing tags degrade predictably rather than being interpreted as zero loudness.

### M18 — Optional: offline listening on the user's device

**Gap:** acquiring music on the server does not make it available offline on a phone. The service worker caches images/static resources and passes HTML/API through. Portable playlist copy/export exists but is a different journey.

**Evidence:** `webui/static/sw.js` strategy/fetch handlers; `core/playlists/materialize.py` copy mode.

**Resolve:** prioritize only if SoulSync is meant to replace the everyday player. Add explicit Save to this device, quota/size estimates, progress, complete/partial state, removal, offline navigation and queue metadata. Scope storage to device/profile. Document browser eviction/background limitations; reuse supported exports where device caching is unsuitable.

**Acceptance:** disconnect and play an explicitly saved playlist; identify missing tracks before going offline; eviction/logout behavior is clear; cover-art caching is never presented as downloaded audio.

### M19 — Audit explicit-content rules as a consistent policy

**Gap:** Allow explicit and Prefer explicit settings exist. Inspected BYLT/station/personalized builders show no direct consumption. This does not prove all policy paths are bypassed, but is insufficient evidence for promising a consistent restriction.

**Evidence:** `webui/static/settings.js` save around 4716; explicit metadata in `core/watchlist_scanner.py`; discovery/personalized builders. Trace matching, acquisition and playback before deciding actual scope.

**Resolve:** define whether the setting controls matching preference, auto-acquisition, recommendations or all playback. Centralize intended enforcement and preserve explicit/clean/unknown metadata. Specify unknown handling. Do not market a preference as a child-profile restriction.

**Acceptance:** known explicit, clean and unknown tracks obey documented semantics across search/radio/discovery/manual play/download; restricted profiles cannot bypass any claimed protection by changing entry point.

### M20 — Validate complete mobile and accessible tasks

**Gap:** responsive CSS and accessible dialog helpers exist, but earlier progress leaves zoom, screen-reader and visual passes open. Native buttons are only one part of accessible task completion. M05 is a concrete example.

**Evidence:** `docs/DISCOVER_BIC_PROGRESS.md` limitations; `webui/src/components/dialog/use-accessible-modal.ts`; legacy/player surfaces. No new browser visual pass in this audit.

**Resolve:** test complete tasks: find/play owned album, inspect/download mix, recover wishlist item, reorder queue, select/sync station tracks, switch profile, close layered dialogs. Cover keyboard/touch, 200% zoom, reduced motion, focus restoration and player overlap. Record viewport/screenshots and fix shared patterns before page-specific patches.

**Acceptance:** no hover-only essential action, covered button, trapped focus, clipped control or animation-dependent information; record observations rather than ticking completion because CSS exists.

### M21 — Measure the whole journey before declaring best-in-class

**Gap:** prior evidence distinguishes fast local audio acknowledgment from slower resolution: a 40-track resolution previously took 5.529 seconds. That historical sample is not a current benchmark. Ranking fixtures verify constraints, not whether people like the selections.

**Evidence:** `docs/DISCOVER_BIC_PROGRESS.md` playback timing and fixture-only quality validation; original review performance/task requirements.

**Resolve:** instrument click → resolution → queue ready → browser playing, and acquisition → verified/imported → server visible. Separate cold/warm, local/remote and provider conditions; record library size. Measure recommendation evidence, artist/album concentration, overlap, availability and acceptance/skips across profiles. Establish baselines before setting realistic release budgets.

**Acceptance:** reproducible datasets with median/tail timings; no toast substituted for audio onset; sparse libraries and failures included; people review several listening profiles; regressions measured across the entire journey.

## 4. Recommended implementation order

### First: trust and existing workflows

1. M14 awaited setup/resumable completion.
2. M12 job identity/timer correctness.
3. M05 keyboard queue actions.
4. M15 consistent availability.
5. M02 scoped exclusions and M09 independent recommendation refresh.
6. Begin M01 listening attribution, a larger foundational change.

These repair existing promises and should precede more recommendation rows or provider integrations.

### Second: everyday listening

1. M03 session resume.
2. M06 favorites/native playlists.
3. M07 album/track browsing.
4. M10 reversible taste feedback.
5. M04 seamless transitions and M17 normalization.

Keep artwork, identity and the next useful action prominent. Do not turn every discovery module into an expanded management table.

### Third: advanced control and reach

M11 exact playlist revisions; M13 connected recovery; M08 unified search; M18 offline audio if wanted strategically. Run M16/M19/M20/M21 alongside related implementation rather than treating them as a final cleanup phase.

## 5. Implementation tracking contract

Every task should reference its M-ID and record:

1. Reproduced journey, profile/source/server context and baseline.
2. Exact scope and existing modules being reused.
3. Identity/state/cache/failure contract and migration where needed.
4. Implementation and commit reference.
5. Acceptance cases above plus meaningful edge-case tests.
6. Evidence: tests, screenshots/timings where relevant, and remaining limits.
7. Status: not started / in progress / partial / complete.

All M01–M21 are **not implemented by this audit**. Confirmed describes evidence, not completion. Product/validation gaps require the stated tracing before changes. No test suite was run for this documentation-only audit; source-backed claims must not be mistaken for newly reproduced integration tests.

## 6. Relationship to existing handoffs

- [Discover best-in-class review](DISCOVER_BEST_IN_CLASS_REVIEW.md): deeper design and previously open batches.
- [Discover progress](DISCOVER_BIC_PROGRESS.md): fixes, tests and remaining verification.
- [Stations/BYLT diagnosis](DISCOVER_STATIONS_AND_BYLT_ISSUES.md): snapshot and recommendation-generation contracts.

This audit expands beyond Discover. Reconcile overlapping statuses in both documents when implementing: fixed bugs should not remain falsely open, and untested behavior should not become falsely complete.

## 7. Phased implementation ledger

User authorized implementation in phases with a separate commit per phase on 2026-09-05. Existing unrelated working-tree files are excluded. Audit items remain open until all their acceptance criteria pass; a committed slice is not blanket completion.

| Phase | Scope | Status |
|---|---|---|
| 1 | M14 setup save/finish reliability; M12 export status revision guards | Verified initial slice; committed separately |
| 2 | M05 queue keyboard actions; M03 profile-scoped resume foundation | Planned |
| 3 | M15 capability-aware availability; M02 profile-scoped exclusions | Planned |
| 4 | M09 independent recommendation refresh; M10 feedback foundations | Planned |
| 5 | M01 listening attribution; M06 native collections | Planned |
| 6 | M07 library browse; M08 unified search; M11 revisions; M13 recovery links | Planned |
| 7 | M04 transitions; M17 normalization; M16/M19/M20/M21 cross-workflow validation; M18 optional offline scope | Planned |

### Phase 1 implementation record

- Setup Next awaits HTTP and application success; failed saves stay on the current step with an alert. Duplicate navigation is guarded while saving.
- Finish requires confirmed settings save and server completion. Dismissal does not set completion. Reopening within the same page preserves unfinished input; secrets are not copied into browser persistence.
- In-progress wizard settings no longer imply setup is complete merely because a download mode was saved. Existing configured installations retain their compatibility fallback.
- Export status updates and pending responses belong to operation revisions; old completion timers cannot clear newer status. Polling failures show reconnecting and retain retry behavior.
- Remaining M14 work: durable cross-reload step restoration from persisted settings and integrated connection/path validation. Remaining M12 work: reattaching after navigation and durable per-job history. These items remain partial after the initial fix.
- Validation in progress: real wizard script tests with delayed/rejected saves; hook tests for superseded timers/responses; existing export/modal tests. No real connection settings or external playlist was changed during tests.

Phase 1 validation: 64 focused frontend tests passed; `node --check webui/static/setup-wizard.js`, `python -m py_compile web_server.py`, and patch whitespace checks passed. M14 and M12 remain partial for the follow-up work named above.
