# Library V2 — Features, Specs & Parity Gaps

This document details all implemented and planned features of Library V2, including original roadmap milestones, design decisions (ADRs), UI requirements, and parity gaps.

---

## 1. Original Roadmap & Milestones
<a name="roadmap"></a>
* **Milestone 1 (Foundation & Read-UI):** Read-only projection from DB, artist lists, album list, track tables. Local artwork cache, connection mappings.
* **Phase B (Interactive Search & Download):** Trigger manual search, parse candidates, initiate downloads.
* **Phase C (Re-Tag, Maintenance, Manual Import):** Edit metadata, tag-preview, retag files, manual import.
* **Phase D (Quality Profiles & Manage Tracks):** App-wide profile selection, upgrading files, single vs album folders, duplicate folding.
* **Phase 3 (Provider Snapshots & Refresh Boundaries):** Fetching complete provider tracklists, caching provider responses.
* **Phase 4 (Acquisition & Decision Engine):** Server-side download dispatcher, torrent/usenet integration, quarantine.

---

## 2. Reused Assets (Quick Index)
<a name="reused-assets"></a>
Library V2 integrates and builds upon the following existing components of SoulSync:
* **Search & Candidates:** `core/downloads/candidates.py`, `core/downloads/source_policy.py`.
* **Download Client Interface:** `core/torrent_clients/`, `core/usenet_clients/`.
* **Tag Writer:** `core/tag_writer.py` for writing files metadata.
* **Verification & AcoustID:** `core/library2/verification.py`, `core/downloads/wishlist_failed.py`.
* **ReplayGain & Lyrics:** `core/repair_jobs/` for tag writing, LRC checks.

---

## 3. Planned & Implemented Features Detail
Below are the detailed feature specifications and decisions consolidated from `library-v2.md` and related documents.

### <a name="feat-artwork"></a> F-01: Media-Server Independent Artwork
Album/Track artwork is resolved from embedded tags (primary) or metadata providers (fallback). Artist artwork comes from provider artist photos (primary), embedded album covers (fallback), or local disk cache. Cache location: `<db_dir>/lib2_artwork/`, served via `/api/library/v2/artwork/<kind>/<id>[?size=thumb]`.

### <a name="feat-monitoring"></a> F-02: Watchlist & Wishlist Monitoring Reflection
Artist-Monitor mirrors Watchlist; Album/Single/Track-Monitor mirrors Wishlist. A track remains monitored if it is needed for future quality upgrades.

### <a name="feat-quality"></a> F-03: App-Wide Quality Profiles
Quality Profiles map to the app-wide `quality_profiles` table. Any dispatcher dynamically checks profile settings (cutoff, upgrades, etc.).

### <a name="feat-discography"></a> F-04: Discography Expansion & Discovery (Lidarr-Style)
Allows fetching all releases of an artist and monitoring missing tracks/albums automatically based on `monitor_new_items` settings.

### <a name="feat-bootstrap"></a> F-05: Automatic Initial Import Bootstrap
On first start with `features.library_v2=true`, `core/library2/bootstrap.py` automatically initiates an idempotent initial import (`import_legacy_library`). It is protected with single-row state locks in `lib2_bootstrap_state` to prevent concurrent double-starts.

### <a name="feat-alias"></a> F-06: Artist Alias Registry
Allows linking artists as aliases to resolve catalog duplicates. Searching by the alias name resolves the canonical ID, and release lists merge their contents.

### <a name="feat-duplicate"></a> F-07: Album/Artist Duplicate Resolution (Hiroyuki Sawano Case)
A 5-stage dedup process resolving duplicates from provider splits:
* **Stage 1:** Matching hardening in discography sync.
* **Stage 2:** Alternative releases treated as editions.
* **Stage 3:** MusicBrainz Release-Group Reconcile.
* **Stage 4:** Artist duplicity prevention and merges.
* **Stage 5:** Namespace sanitization.

### <a name="feat-unmapped"></a> F-08: Unmapped Artists & Collaboration Splits
Enables native V2-only artists to be enriched directly, splitting collaboration credits dynamically.

### <a name="feat-playlists"></a> F-09: Playlist Phase 2 conflicts & Scoped Processing
Playlist downloads are scoped correctly to run pipeline wishlist items only, avoiding global queue fallout.

### <a name="feat-history"></a> F-10: Pipeline History/Timeline
Unified history feed merging `acquisition_history`, `lib2_entity_history`, `lib2_file_delete_operations` to show exactly how a track went through the pipeline.

### <a name="feat-playback"></a> F-11: Track Playback / Preview
Allows direct streaming/playback of tracks in V2 tables via shell bridge and the legacy player.

### <a name="feat-acq-review"></a> F-12: Acquisition Review / Manual Grab Assignments UI
Provides a frontend interface for manual assignments when ambiguous album grabs occur. Allows users to resolve ambiguous track-file mappings manually since the backend already supports `/acquisition/requests*`, `/acquisition/imports*` (including `/resolve`), and similar endpoints.

---

## 4. UI/UX Requirements
Consolidated from `library-v2-ui-requirements.md` and subsequent user reviews.

### <a name="ui-icons"></a> UI-01: Icons & Nomenklatur (Lidarr Alignment)
* **Interactive Search:** Human icon (`User` icon) on artist toolbars, album blocks, track lines.
* **Automatic Search:** Lupe icon (`Magnifying Glass`) for automated searches.
* **Quality Profile:** Star icon (`Star`) is retained app-wide.
* **Options:** Zahnrad icon (`Settings`) for tables options.

### <a name="ui-columns"></a> UI-02: Configurable Columns & Zahnrad Option
Allows users to show/hide columns (e.g. #, Disc, Artists, Match, Quality, Features, Metadata, Duration, BPM, File path, Format) and persist preferences per profile. Columns resizing was considered but deferred.

### <a name="ui-bulk"></a> UI-03: Track Table Bulk Operations
Checkbox multi-selection enabling bulk operations: Monitor/Unmonitor, Quality Profile override, ReplayGain scan, Write Tags, and Delete Files.

---

## 5. Tool / Repair-Job Integration
<a name="tool-integration"></a>
Audit of all 33 repair tools mapping to Library V2. Jobs are marked as **Nativ** (read/write V2 directly), **Brücke** (synchronized with legacy), **Neutral** (operative only), or **Entfernt** (retired in V2).

| Tool ID | Name | Status in V2 | Scope / Description |
|---|---|---|---|
| 1 | Track Number Repair | **Brücke / Nativ (P1)** | Findings report metadata/tag/path changes; V2-file is rescanned. |
| 2 | Cache Maintenance | **Neutral** | Cleans expired cache; no V2-entity impacts. |
| 3 | Orphan File Detector | **Dual Read (P1)** | Integrates V2 paths and identities to prevent false positive scans. |
| 4 | Dead File Cleaner | **Brücke (P1)** | Marks V2-file deleted and recomputes Wanted status on file removal. |
| 5 | Duplicate Detector | **Entfernt** | Replaced by `dedup_repair.py` running during imports. |
| 6 | AcoustID Scanner | **Dual Read (P1)** | Scans V2-primary files directly; stores verification status. |
| 7 | Cover Art Filler | **Dual Read (P1)** | Fills metadata and artwork cache using V2 artwork resolver. |
| 8 | Lyrics Filler | **Dual Read (P1)** | Fetches LRC/embedded lyrics for V2 files; triggers rescan. |
| 9 | ReplayGain Filler | **Dual Read (P1)** | Analyzes gain and updates tags in V2 catalog. |
| 10 | Empty Folder Cleaner | **Neutral** | Directory cleaning; storage health checks remain active. |
| 11 | Expired Download Cleaner | **Entfernt** | Retired; file deletes handled in central V2 lifecycle bridge. |
| 12 | Metadata Gap Filler | **Dual Read (P1)** | Projects tags to V2 subjects and writes to `lib2_tracks`. |
| 13 | Album Completeness | **Entfernt** | Replaced by native completeness placeholder materialization. |
| 14 | Fake Lossless Detector | **Dual Read (P1)** | Checks all V2 files; findings get V2 subjects. |
| 15 | Quality Check (flag) | **Entfernt** | Replaced by `lib2_upgrade_scan` mode=`review`. |
| 16 | Library Reorganize | **Entfernt** | Dry-run scan removed; planner active via reorganize bridge. |
| 17 | MBID Mismatch Detector | **Entfernt** | Replaced by native dedup namespace-sanitize stage. |
| 18 | Single/Album Dedup | **Entfernt** | Replaced by native dedup folding stage. |
| 19 | Lossy Converter | **Dual Read (P1)** | Registers new files and deletes converted originals. |
| 20 | Album Tag Consistency | **Dual Read (P1)** | Audits tag consistency directly on V2 files. |
| 21 | Live/Commentary Cleaner | **Brücke** | Deletion updates V2 file and recomputes Wanted. |
| 22 | Fix Unknown Artists | **Entfernt** | Replaced by native artist-enrichment splitting. |
| 23 | Discography Backfill | **Entfernt** | Replaced by V2 discography refresh + wanted views. |
| 24 | Resolve Canonical Album | **Entfernt** | Replaced by dedup group reconciliation. |
| 25 | Library Re-tag | **Entfernt** | Replaced by native V2 retag modal writing. |
| 26 | Quality Upgrade Finder | **Entfernt** | Replaced by `lib2_upgrade_scan` mode=`automatic`. |
| 27 | Preview Clip Cleanup | **Dual Read (P1)** | Cleans short previews; marks deleted and rewishes. |
| 28 | Corrupt File Detector | **Dual Read (P1)** | Detects decode corruptions; marks deleted and rewishes. |
| 29 | Quality Upgrade Scan | **Nativ & Gegated** | `lib2_upgrade_scan` for monitored upgrade evaluation. |
| 30 | Skip-Audit Cleanup | **Nativ & Gegated** | Cleans expired manual skip entries. |
| 31 | Discography Refresh | **Nativ & Gegated** | `lib2_discography_refresh` discography expansion. |
| 32 | Outbox Mirror Reconcile | **Nativ / Gegated** | Retries watchlist/wishlist mirror outbox. |
| 33 | Monitored Wishlist Reconcile | **Nativ / Gegated** | Reconciles monitor rules and wishlist items. |

---

## 6. Legacy Parity & Lidarr Gaps

### <a name="parity-legacy"></a> H-Parity: Legacy Parity Gaps
* **H1: Track-Playback/Preview** — Implemented via shell bridge.
* **H3: Discography-Download-Modal** — Deferred; user preferred direct monitor/wishlist.
* **H4: Track-Redownload-Modal** — Deferred; search & replace flow is sufficient.
* **H5: Track/Album Delete** — Implemented with DB-only/permanent options.
* **H6: A-Z Alphabet Selector** — Rejected; text search is sufficient.
* **H7: Inline Edit in Table** — Rejected; modals are robust.
* **H8: Bulk-Selektion + Bulk-Bar** — Implemented (see B6/UI-03).
* **H12: Playlist/M3U Export** — Deferred.
* **H13: Reorganize-Queue-Status-Panel** — Implemented.

### <a name="parity-lidarr"></a> I-Parity: Lidarr Parity Gaps
* **I1: Add Artist (Monitor options on add)** — Rejected; search/watchlist is preferred.
* **I2: Wanted-Views (Missing/Cutoff Unmet lists)** — Implemented globally.
* **I3: Mass Editor** — Rejected.
* **I4: Metadata Profile** — Rejected; watchlist settings suffice.
* **I5: Calendar / Upcoming releases** — Rejected.
* **I6: Queue Visibility at Entity Level** — Implemented (visualizes active grabs).
* **I8: Root Folder & Diskspace per Artist** — Implemented.
* **I10: Search on Monitor** — Rejected.
