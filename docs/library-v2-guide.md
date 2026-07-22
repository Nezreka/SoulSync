# Library V2 — Goal & Working Guidelines (Guide)

Welcome to the Library V2 documentation. This document defines the goal of the project, the core design principles, non-negotiable invariants, and guidelines for development.

## 1. Goal of Library V2
The goal of Library V2 is to provide an opt-in, Lidarr-style library manager for SoulSync that relies on its own robust search, download, processing, and tagging pipeline, without interfering with the legacy library. It acts as a database-centered catalogue providing:
* Stable artist, album, track, and file identities.
* Clear distinctions between Release Groups, Editions, and Recordings.
* Support for multiple files per track, with primary files and lifecycle states.
* Track intent mapping via `lib2_monitor_rules` -> `lib2_wanted_tracks`.
* Persistent correlation of acquisition, grabs, imports, and history.

## 2. Non-Negotiable Design Rules (Core Principles)
* **Media-Server Independence:** Library V2 must never depend on external media servers (e.g. Plex, Jellyfin, Navidrome), not even for artwork. Album/Track artwork is resolved from embedded tags (primary) or metadata providers (fallback). Artist artwork comes from provider artist photos (primary), embedded album covers (fallback), or local disk cache.
* **Monitoring Reflections:** Monitoring states mirror existing systems. Artist monitoring corresponds to the Watchlist, and Album/Single/Track monitoring corresponds to the Wishlist.
* **App-Wide Quality Profiles:** Quality Profiles map to the app-wide `quality_profiles` table, never a parallel copy.
* **DB as Source of Truth:** All file locations are stored in `lib2_track_files`.
* **Asset Reuse:** Always reuse existing SoulSync components (Search, Download, Tagging, Repair, Quality) instead of reinventing them.
* **Path Resolution:** Every file system access must go through `core/library2/paths.resolve_lib2_path` to support path-mapped setups.
* **No Profile() in Background Threads:** Background threads must never call `_profile()`. Solve the active user profile in the request context and pass it explicitly.
* **SQLite Locking Rule:** Commit the `lib2_*` flag update and release the write lock before executing Watchlist/Wishlist methods (which open their own connections).

## 3. Working Guidelines & Workflow
1. **Systematic Debugging:** Never fix a bug without writing a regression test first or establishing an isolated reproduction scenario.
2. **Regression vs Upstream:** Treat migration-caused regressions and post-divergence upstream fixes separately.
3. **Completeness of Fixes:** A fix is only complete when its original behavior, intended V2 behavior, upgrades, retries, and restarts are verified.
4. **AST-Only Updates:** Keep the graphify graph current by running `graphify update .` after changes.
5. **No Placeholders:** Avoid creating simple minimum viable products or using placeholders.

## 4. References & Documentation Index
This document serves as the entry point to the Library V2 documentation. Please refer to:
* **[Features & Specifications](library-v2-features.md):** Detailed phasenplan, reused assets, planned/implemented features, legacy and Lidarr parity gaps.
* **[Bugs & Issues](library-v2-issues.md):** Consolidated catalog of all identified bugs, regression audits, branch reviews, deep dives, and review findings.
* **[Status Tracker](library-v2-status.md):** The central, compact tracking table linking every feature, bug, and tool migration to its status, commit hash, and test run status.
