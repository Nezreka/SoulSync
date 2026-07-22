# Library V2 — Status Tracker

This document tracks the current status of all features, bugs, and tool migrations in Library V2, mapping them to commit hashes and test run status.

---

## 1. Feature Status Tracker

| Feature ID | Description | Status | Commit / Reference | Test Coverage |
|---|---|---|---|---|
| [F-01](library-v2-features.md#feat-artwork) | Media-Server Independent Artwork | Done | `f3abaf16` | Full |
| [F-02](library-v2-features.md#feat-monitoring) | Watchlist & Wishlist Monitoring Reflection | Done | `f3abaf16` | Full |
| [F-03](library-v2-features.md#feat-quality) | App-Wide Quality Profiles | Done | `f3abaf16` | Full |
| [F-04](library-v2-features.md#feat-discography) | Discography Expansion & Discovery | Done | `f3abaf16` | Full |
| [F-05](library-v2-features.md#feat-bootstrap) | Automatic Initial Import Bootstrap | Done | `80b5af95` | Full |
| [F-06](library-v2-features.md#feat-alias) | Artist Alias Registry | Done | `ce7b4516` | Full |
| [F-07](library-v2-features.md#feat-duplicate) | Album/Artist Duplicate Resolution | Done | `f3abaf16` | Full |
| [F-08](library-v2-features.md#feat-unmapped) | Unmapped Artists & Collaboration Splits | Done | `f3abaf16` | Full |
| [F-09](library-v2-features.md#feat-playlists) | Playlist Scoped Processing | Done | `f3abaf16` | Full |
| [F-10](library-v2-features.md#feat-history) | Pipeline History/Timeline | Done | `abfa27a7` | Full |
| [F-11](library-v2-features.md#feat-playback) | Track Playback / Preview | Done | `f3abaf16` | Full |
| [UI-01](library-v2-features.md#ui-icons) | Icons & Nomenklatur | Done | `f3abaf16` | Full |
| [UI-02](library-v2-features.md#ui-columns) | Configurable Columns Options | Done | `f3abaf16` | Full |
| [UI-03](library-v2-features.md#ui-bulk) | Track Table Bulk Operations | Done | `f3abaf16` | Full |

---

## 2. Issues & Findings Tracker

| ID | Description | Status | Commit | Test Coverage |
|---|---|---|---|---|
| [Finding 1](library-v2-issues.md#find22-01) | Update only the file that reorganize moved | Done | `4622f624` | Specific |
| [Finding 2](library-v2-issues.md#find22-02) | Serialize each acquisition import before dispatch | Done | `d6d37eb2` | Specific |
| [Finding 3](library-v2-issues.md#find22-03) | Synchronize automatic expiry deletes with V2 | Done | `804538c7` | Specific |
| [Finding 4](library-v2-issues.md#find22-04) | Break the bootstrap into bounded transactions | Done | `c2d99eda` | Specific |
| [Finding 5](library-v2-issues.md#find22-05) | Stream legacy rows during bootstrap | Done | `e9730afe` | Specific |
| [Finding 6](library-v2-issues.md#find22-06) | Reject arbitrary artwork fetch targets | Done | `80b5af95` | Specific |
| [Finding 7](library-v2-issues.md#find22-07) | Require artist context when matching Enrich | Done | `280716d9` | Specific |
| [Finding 8](library-v2-issues.md#find22-08) | Bound artist-list aggregation to page | Done | `6c827c33` | Specific |
| [Finding 9](library-v2-issues.md#find22-09) | Preserve non-Latin Enrich titles | Done | `abfa27a7` | Specific |
| [Finding 10](library-v2-issues.md#find22-10) | Keep native Enrich's metadata-update contract | Done | `87b990bb` | Specific |
| [Finding 11](library-v2-issues.md#find22-11) | Fail monitor mutation when outbox write fails | Done | `088e1dc7` | Specific |
| [Finding 12](library-v2-issues.md#find22-12) | Fold alias rows into search and totals | Done | `ce7b4516` | Specific |
| [Finding 13](library-v2-issues.md#find22-13) | Resolve alias groups for artist actions | Pending | — | None |
| [Finding 14](library-v2-issues.md#find22-14) | Rebuild album artist credits during re-import | Pending | — | None |
| [Finding 15](library-v2-issues.md#find22-15) | Poll queue status once per artist page | Pending | — | None |
| [Finding 16](library-v2-issues.md#find22-16) | Verify existing acquisition copies by content | Pending | — | None |
| [Finding 17](library-v2-issues.md#find22-17) | Make Refresh & Scan asynchronous | Done | `7ded959c` | Specific |
| [C-01](library-v2-issues.md#c-01) | Preview/Null-Header can replace complete file | Pending | — | None |
| [H-01](library-v2-issues.md#h-01) | Old Repair-Job-IDs and settings lost | Pending | — | None |
| [H-02](library-v2-issues.md#h-02) | Existing Quality-Automation starts downloads | Pending | — | None |
| [H-03](library-v2-issues.md#h-03) | Bootstrap-lease has no owner fencing | Pending | — | None |
| [H-04](library-v2-issues.md#h-04) | Empty Fresh-Install watermarks | Pending | — | None |
| [H-05](library-v2-issues.md#h-05) | Non-Admin profiles mutate global V2 intent | Pending | — | None |
| [H-06](library-v2-issues.md#h-06) | Composite Remove demonitors multiple releases | Pending | — | None |
| [H-07](library-v2-issues.md#h-07) | Watchlist-Artist-Match loses provider namespace | Pending | — | None |
| [H-08](library-v2-issues.md#h-08) | Repair-Intent remove/redownload goes lost | Pending | — | None |
| [H-09](library-v2-issues.md#h-09) | Finding resolved despite failed V2 sync | Pending | — | None |
| [H-10](library-v2-issues.md#h-10) | Track Number repair uses incomplete subset | Pending | — | None |
| [H-11](library-v2-issues.md#h-11) | Track number fixes leave legacy data stale | Pending | — | None |
| [H-12](library-v2-issues.md#h-12) | Multi-File findings dedup away different files | Pending | — | None |
| [H-13](library-v2-issues.md#h-13) | Reorganize leaves V2 path stale | Pending | — | None |
| [H-14](library-v2-issues.md#h-14) | V2-Track-ID interpreted as Legacy/Server ID | Pending | — | None |
| [H-15](library-v2-issues.md#h-15) | Alias view and action scope contradict | Pending | — | None |
| [H-16](library-v2-issues.md#h-16) | allowed_pages bypassed | Pending | — | None |
| [H-17](library-v2-issues.md#h-17) | Acquisition Review backend has no UI | Pending | — | None |
| [H-18](library-v2-issues.md#h-18) | features.library_v2=false disables repair | Pending | — | None |
| [LV2-001](library-v2-issues.md#lv2-001) | Track Automatic Search wishlist row creation | Done | `f3abaf16` | Specific |
| [LV2-002](library-v2-issues.md#lv2-002) | Stale terminal task queued status | Done | `f3abaf16` | Specific |
| [LV2-003](library-v2-issues.md#lv2-003) | Import pipeline callbacks wrapper missing | Done | `f3abaf16` | Specific |
| [LV2-004](library-v2-issues.md#lv2-004) | Post-move database orphan | Done | `f3abaf16` | Specific |
| [LV2-005](library-v2-issues.md#lv2-005) | Quarantine approve scan trigger | Done | `f3abaf16` | Specific |
| [LV2-006](library-v2-issues.md#lv2-006) | Stale legacy_dispatched grab state | Done | `f3abaf16` | Specific |
| [LV2-007](library-v2-issues.md#lv2-007) | Orphan detector legacy-only | Done | `f3abaf16` | Specific |
| [LV2-008](library-v2-issues.md#lv2-008) | Human approve verification status | Done | `f3abaf16` | Specific |
| [LV2-009](library-v2-issues.md#lv2-009) | Recover to staging sidecar logic | Done | `f3abaf16` | Specific |
| [LV2-010](library-v2-issues.md#lv2-010) | Missing suspected amber state | Done | `f3abaf16` | Specific |
| [LV2-011](library-v2-issues.md#lv2-011) | Artist credit features split | Done | `f3abaf16` | Specific |
| [LV2-012](library-v2-issues.md#lv2-012) | Provider-ID-Dedup | Done | `f3abaf16` | Specific |
| [LV2-013](library-v2-issues.md#lv2-013) | E2E integrity reconciler | Done | `f3abaf16` | Specific |
| [LV2-014](library-v2-issues.md#lv2-014) | Enhanced search "In Your Library" | Pending | — | None |
| [LV2-015](library-v2-issues.md#lv2-015) | Playlist sync global wishlist bleed | Done | `f3abaf16` | Specific |
| [LV2-016](library-v2-issues.md#lv2-016) | Phantom artist monitoring defaults | Done | `f3abaf16` | Specific |
| [LV2-017](library-v2-issues.md#lv2-017) | Reorganize rename desync | Pending | — | None |
| [Orphan Bug](library-v2-issues.md#orphan-bug) | Quarantine approve -> orphan bug | Pending | — | None |
