Final merge audit for upstream/dev 3.5.0. Overall completion and verification are recorded in [the handoff](../upstream-sync-3.5.0.md).

# Audit range 2 — all 98 commits

Final working-tree audit against upstream/dev 732a83fe4. Each commit was inspected via git show; current backend hunks/callers were checked, and UI files were compared against the final upstream versions (later upstream supersession is accepted). All referenced MusicDatabase methods exist. No remaining LOST/BROKEN item was confirmed in this range after the authorized audit fixes. No staging or commits performed.

## Concrete regressions found and repaired

- Sample Studio used the global primary file and shared worker state despite independent libraries. `core/sample/store.py:175` now filters ownership; `api/sample.py:43,68` requires a live file in the selected library before analysis/stem access. `worker.py:174` and `stems_worker.py:46` key status/dedup/progress by scope and preserve queued context, including explicit None. Source signatures include path identity; stem directories separate libraries; missing source identities never authorize derived audio. These are new merge-port regressions.
- Inbox refresh dropped ContextVar scope, marking private-library news added from a shared-library file. `core/discovery/inbox.py:515` now carries the initiating scope. A real background-refresh regression confirms the release stays unread. This is a new merge-port regression.

## B6–B10

- B6 verified: `library_match.py:264–270` requires a file, permits absent server_source, rejects explicit other-server sources. Candidate rows are scoped by the database helper.
- B7 SKIPPED-OK: retired duplicate detector provenance normalization was not revived. Native `library2/duplicate_relationship.py:23` remains intentionally conservative; an optional broader title validator is outside this merge.
- B8 corrected: unfinished inbox comment now describes selected-library file ownership.
- B9 verified: both `discovery/sync.py:191` and `services/sync_service.py:1030` translate durable catalogue IDs with manual_match_server_id before materializing server tracks.
- B10 verified: `repair_worker.py:4783` writes track_artist and artists_list, preserving album artist.

## Commit classification

| Commit | Verdict | Retained behavior / omission reason |
|---|---|---|
| c9a0376f7 | TAKEN | Stats/keepsakes |
| 8fe619f63 | TAKEN | Automations dashboard |
| 736a18fa1 | PORTED | Native repair logging |
| 80a36a7ca | TAKEN | Stats responsiveness |
| 7a5b1e07b | TAKEN | UI formatting/types |
| 820c1cc99 | TAKEN | Automation cadence/buttons |
| f03bd65e3 | TAKEN | Formatting |
| 4a34aad9a | PORTED | lib2 imports/repairs; owner batching |
| 804a88ee1 | TAKEN | Responsive automation cards |
| ba364642b | TAKEN | Bracket-title preservation |
| e10b32d6a | TAKEN | Dashboard redesign |
| 41fe63bf6 | PORTED | lib2 owned-id CTE |
| 662c69ef7 | TAKEN | Baseline tests; atypes paths |
| f9924bb32 | TAKEN | M3U path helpers |
| 9033d6c7f | PORTED | Batch profile/library retention |
| 874883f4f | TAKEN | Disambiguation toggle |
| 6e7de05bd | TAKEN | Bracket-only verification |
| ce83731b8 | SKIPPED-OK | Duplicate detector retired |
| 8ac7311d9 | PORTED | Live-file edition count |
| 7b26b063f | TAKEN | Ownership artist/title guards |
| dc6911c3b | PORTED | Native repairs; retired job omitted |
| aa0a69a14 | TAKEN | Deezer type/count |
| d243fb09c | TAKEN | Unknown album count |
| accfc9c75 | TAKEN | Fetched release reconciliation |
| d0bd54437 | TAKEN | Extensionless file matching |
| 8f3a65479 | TAKEN | Obsolete documents removed |
| 72bba6ce7 | TAKEN | SoulSync chat badge |
| 7ea9ad9c3 | TAKEN | 3.4.8; superseded by 3.5.0 |
| 0aa2ac5b3 | TAKEN | Current finding labels |
| 378d4bf32 | TAKEN | Strict wishlist identity |
| 0513f0262 | TAKEN | Distinct subtitle/punctuation |
| 568763bb6 | TAKEN | Unknown subtitle identity |
| 23e9b5b0f | PORTED | lib2 requested-album fallback |
| 1065758f7 | TAKEN | Artwork URL normalization |
| 661598f96 | PORTED | Sample lib2 catalogue/files; scope fixed |
| 0b209258c | TAKEN | API-key session gates |
| cd9509daa | TAKEN | Librosa Python-3.11 pin |
| debf8cbe9 | PORTED | Scoped search/recent tracks |
| ac42887a0 | TAKEN | API CORS/preflights |
| 3060d53e8 | PORTED | lib2 interactive search |
| 61d533e5f | TAKEN | API-key profile context |
| b727d8e74 | TAKEN | DSP/Studio guided UX |
| e22387d61 | TAKEN | Separator availability gate |
| af3d5264a | TAKEN | CSS brace repair |
| ae7b9cfa1 | TAKEN | Discover navigation |
| a08f0c704 | TAKEN | Billboard hero |
| e3f36af69 | TAKEN | Personalized banners |
| 4cd265021 | TAKEN | Discover explanations |
| 3d89a5411 | PORTED | lib2 inbox ownership; context fixed |
| a7c8d0c5b | PORTED | lib2 labels |
| c8dea4a09 | TAKEN | Discover mobile CSS |
| 0fa9a57d7 | TAKEN | Album rail layout |
| ec1288077 | PORTED | lib2 mood tracks |
| 1af23bea2 | SKIPPED-OK | Duplicate detector retired |
| dfb031a2d | SKIPPED-OK | Duplicate detector retired |
| 6a4cab2b5 | SKIPPED-OK | Retired detector tests |
| 6c9df5e05 | TAKEN | Studio FX/keys/recipes |
| f2191ee77 | PORTED | lib2 history/catalogue IDs |
| e3109ef17 | PORTED | lib2 artist artwork |
| f48ab5b9a | TAKEN | Hero image fallback |
| c10e69050 | TAKEN | Blocked-aware video backdrops |
| 0659044db | TAKEN | Promo video backgrounds |
| 83d811774 | TAKEN | Bento/video rail |
| 9f5fd4382 | TAKEN | Interactive video controls |
| 3c72f66d5 | TAKEN | Player theater |
| 5ddf532dd | TAKEN | Theater themes |
| 61349c90b | TAKEN | Theater polish |
| 1f746601e | TAKEN | Theater follow-up polish |
| 9000439d7 | TAKEN | Strict weighted zip |
| 7c5e1a1fb | TAKEN | Video groups/posters |
| 0cdbc97a6 | TAKEN | Song-reactive theater |
| 33aac3901 | TAKEN | Painter visuals |
| 97b6447d3 | TAKEN | Analyser avoids audio destination |
| 30d2685d6 | TAKEN | Qualifier-only remix filter |
| 4076482ff | TAKEN | Unreachable slskd backoff |
| e628ec292 | TAKEN | Vanished batch state |
| 697ee5419 | TAKEN | Findings auto-refresh |
| fd4d2f3fb | TAKEN | Compilation path setting |
| 626701b95 | TAKEN | Only running vanished process ends |
| 0d4103de9 | TAKEN | Preserve selected findings |
| 3ebdb554f | TAKEN | Externally cancelled modal state |
| 6f52569c5 | SKIPPED-OK | Duplicate detector retired |
| 42cb1e75c | TAKEN | Metadata/recording distinction |
| a26e7978e | TAKEN | Download-finish ownership refresh |
| 9350b8255 | TAKEN | One-sided subtitle compatibility |
| 85f8c34fc | TAKEN | Fresh completed-album modal |
| 4c19988ba | TAKEN | Album artist on playlist import |
| 319fb1c11 | TAKEN | Provider release-type semantics |
| d69444123 | TAKEN | Release-specific discography ownership |
| 558d96864 | PORTED | lib2 reorganize release metadata |
| 350d166d7 | TAKEN | Locked artist-page release types |
| 2716dbacb | TAKEN | Locked-type logging |
| 5b2625597 | TAKEN | Search type lock/reopen gate |
| 12c035be0 | TAKEN | Spotify public Premium fallback |
| 4f09b31b8 | PORTED | lib2 release type source |
| b86977826 | TAKEN | Raw-ID Spotify alias resolution |
| 82432edbb | TAKEN | Public artwork retained |
| 6922b0cb4 | TAKEN | Playback-equivalent Sample resolution |

## Verification

New scope regressions failed before fixes (ownership, cache identity, job separation, missing-source derived reads, inbox thread scope). Targeted verification: 97 passed, 4 route tests excluded in 12.90s across Sample scope/gaps/status/path/stems and inbox tests; excluded route fixtures boot web_server. Final complete Sample suite plus durable-sync fixtures: 188 passed in 22.18s outside the sandbox. The sandboxed asyncio.to_thread checks stalled in the selector wakeup and were stopped after external verification succeeded. FX/e2e fixtures now retain their queued analysis database until completion and isolate sticky test outcomes; durable-match fixtures use distinct catalogue/server IDs. Ruff passed affected production/tests. Root owns full-suite verification and local merge commit. Persistent analysis/stem DB caches remain catalogue-keyed: switching different owned copies may regenerate artifacts, while file identity and ownership guards prevent serving another library’s audio.
