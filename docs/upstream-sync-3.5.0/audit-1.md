Final merge audit for upstream/dev 3.5.0. Overall completion and verification are recorded in [the handoff](../upstream-sync-3.5.0.md).

# Upstream audit 1 — 98 commits

Compared every `git show` patch against upstream/dev final and the working tree; superseding/reverted changes checked. Equal final files, native ports and removed legacy surfaces were distinguished. No web server or full suite was started.

## Material findings

1. **Resolved upstream defect:** Operations Studio referenced nonexistent IDs and silently dropped playbook operations. The final port uses the registered preview, genre, numbering, lyrics, artwork, folder and MBID job IDs, removes retired quality/duplicate scan promises, and directs duplicate review to the Library page. Four new playbook regressions failed before correction; the final targeted frontend run passed all 16 cases. Commits 94/97 are PORTED after that fix; see audit 3 for details.

2. **Resolved integration gap:** commit 92’s rescan fence did not cover pre-existing branch full-refresh clears or artist/album removal, and an empty deep scan could bypass stopped/failed-identity protection. Reproduced six mapping-loss failures, then two full-refresh ordering failures using real Library v2 rows. Shared `_can_detach_server_mappings` at `core/database_update_worker.py:228` now guards all detach paths, remembers rescans seen before inventory, rechecks after fetching, and retains small stale-set behavior when the probe raises. Empty refresh clears now wait for processing/identity repair. Trusted quiet empty servers still detach.

## B1–B5 verification

- B1: `_free_soulsync_track_id` checks mappings/file holders before native `upsert_track`; collision remints retained.
- B2: enhanced-search key includes current profile and ambient library scope.
- B3: `_record_lossy_replacement` commits before journaled original deletion; DB-failure regression passes.
- B4: staging ownership uses `original_batch_id or batch_id`, preserving batch library choice.
- B5: edition guard counts only rows with a file; wanted catalogue rows do not inflate holdings.

Validation: 34 targeted B3/B5/cache tests passed; 50 scan tests passed during repair; final 39 targeted detach/fence/empty-library/cache tests passed, with `git diff --check` clean. Changed worker, new `tests/media_server/test_scan_detach_safety.py`, and the intentional ordering assertion in `tests/test_scan_cache_staleness.py`. No staging or commit.

## Commit table

| # | Commit | Verdict | Final behavior / omission reason |
|---:|---|---|---|
| 1 | `488c2d63a` | PORTED | Native file subjects use shared walker; retired scanner omitted. |
| 2 | `c99eedd6a` | TAKEN | AppleDouble-only exclusion; ordinary hidden files retained. |
| 3 | `10b0648f1` | TAKEN | $atypes defaults, formatting, paths and settings retained. |
| 4 | `33eb05981` | TAKEN | $atypes guidance retained. |
| 5 | `e66829f70` | TAKEN | VA credit guard and M3U variables retained. |
| 6 | `b1f59006d` | TAKEN | M3U follows actual audio folder. |
| 7 | `d6252ba1f` | PORTED | Reorganize carries release labels through native catalogue. |
| 8 | `6ae03b5a6` | TAKEN | First release-type token stays primary. |
| 9 | `9e14118f3` | TAKEN | M3U audio-folder override requires library containment. |
| 10 | `0e2664a76` | TAKEN | Single $atypes defaults source. |
| 11 | `4ecbdf7c0` | TAKEN | Wiring tests retained. |
| 12 | `70b0f7c07` | TAKEN | ID3 NUL splitting retained. |
| 13 | `f2de8f374` | SKIPPED-OK | Empty release commit; no file change. |
| 14 | `88b9975f1` | TAKEN | Docs rebuild and script wiring retained. |
| 15 | `1ac8c2b77` | TAKEN | Doc-pin corrections retained. |
| 16 | `aeb12f816` | TAKEN | Video docs consolidation retained. |
| 17 | `01c04380c` | TAKEN | Raw multipart upload mock retained. |
| 18 | `2c45f4005` | TAKEN | Third-round docs corrections retained. |
| 19 | `bed6b86ad` | TAKEN | Expanded documentation sections retained. |
| 20 | `f36ab09a6` | TAKEN | README corrections retained. |
| 21 | `6946c983b` | TAKEN | Comma release types and EP primary override retained. |
| 22 | `8ad9001e9` | TAKEN | Candidate rejection decisions retained. |
| 23 | `560797890` | PORTED | Inspector retained; native redownload IDs normalized. |
| 24 | `2c55e9582` | TAKEN | Durable task decisions linked to imported provenance. |
| 25 | `25d5ffb73` | TAKEN | Quality call-site test correction retained. |
| 26 | `85b6bbc3f` | TAKEN | History commits before best-effort decisions cleanup. |
| 27 | `66f008682` | PORTED | Wishlist/failed inspector retained; native redownload entrypoint. |
| 28 | `b1551261f` | SKIPPED-OK | Retired quality-job badges/action omitted; inspector cutoff retained. |
| 29 | `1e583f60f` | TAKEN | Best-in-class plan retained. |
| 30 | `1dd94fd9a` | TAKEN | Inspector-path test retained. |
| 31 | `a1d25e2a4` | PORTED | Blocked-artist guards preserved over native library readers. |
| 32 | `e389c253a` | TAKEN | Unified explanation shape retained. |
| 33 | `a6f914e1f` | TAKEN | Feedback storage and recommendation menus retained. |
| 34 | `8c70d2951` | TAKEN | Admin-gate profile cleanup retained. |
| 35 | `194b961e3` | PORTED | Inbox ownership uses scoped native live files. |
| 36 | `b21e81e4d` | TAKEN | Created-profile cleanup retained. |
| 37 | `b974d80bc` | TAKEN | Single-profile fixtures retained. |
| 38 | `b7727db42` | PORTED | Recipe ownership/track credits/provider IDs use lib2. |
| 39 | `6983545b2` | PORTED | Profile recipes, feedback and inbox pruning preserved. |
| 40 | `41b4df2e7` | TAKEN | Duplicate multipart-fix commit retained. |
| 41 | `224aa8508` | TAKEN | Non-surface POST exemptions retained. |
| 42 | `0632bb0a0` | TAKEN | Track album artist outranks poisoned batch hint. |
| 43 | `ba545eb3a` | SKIPPED-OK | Duplicate detector and its tests deliberately retired. |
| 44 | `56d0f0f0d` | TAKEN | Collapsible enrichment coverage retained. |
| 45 | `5fac8d2d1` | TAKEN | Enrichment layout/scoped labels retained. |
| 46 | `755d96760` | TAKEN | Unique SoulSync session cookie retained. |
| 47 | `47b505be9` | TAKEN | Manual-match button styling retained. |
| 48 | `9238d9ffb` | TAKEN | Single .m4b Soulseek discovery retained. |
| 49 | `05e3b870f` | TAKEN | Persisted audiobook database path retained. |
| 50 | `2c64df1fa` | TAKEN | Audiobook wishlist timeout correction retained. |
| 51 | `9bba0af17` | TAKEN | Owned audiobook wishlist completion retained. |
| 52 | `ad25103e4` | TAKEN | Optional owned-book wishlist cleanup retained. |
| 53 | `9816495f1` | TAKEN | Bare album type remains non-explicit. |
| 54 | `c0212e59e` | PORTED | Direct-share resolver uses lib2 credits and scoped files. |
| 55 | `58958c7b6` | PORTED | Direct-share fallbacks retained over native catalogue. |
| 56 | `46963bc27` | PORTED | Automation targets registered native maintenance jobs. |
| 57 | `f911646e8` | PORTED | Parallel corruption scan/memory over native subjects. |
| 58 | `30c8ee58a` | PORTED | Corrupt quarantine uses delete journal; FLAC validation retained. |
| 59 | `9abf50d28` | PORTED | Scoped scans preserve unseen corruption memory. |
| 60 | `47c2407a8` | PORTED | Explanation components retained; recipe IDs resolve lib2. |
| 61 | `0fcc9367c` | TAKEN | Cross-source best-quality candidate deduplication retained. |
| 62 | `b81e94daa` | PORTED | Bulk wishlist operations preserve owner runtime. |
| 63 | `b38923960` | PORTED | News/retry/blocklist retained with per-owner wishlist batching. |
| 64 | `cf0899394` | TAKEN | Search provenance and candidate policy facet retained. |
| 65 | `449fb7508` | PORTED | Corrupt confirmation, actual corrupt count and native groups. |
| 66 | `fc55f95e9` | TAKEN | Per-profile Discover layout API/customizer retained. |
| 67 | `fbef3768b` | TAKEN | Resolve-share exception logging retained. |
| 68 | `952392465` | TAKEN | Recipe-editor fixture fields retained. |
| 69 | `e59a0191f` | TAKEN | Fresh phase treated as inactive. |
| 70 | `289080c1c` | TAKEN | Layout export/zone coverage tests retained. |
| 71 | `b35fcc518` | SKIPPED-OK | Duplicate layout tests reverted by commit 72. |
| 72 | `cfeb62c09` | TAKEN | Upstream duplicate-test revert preserved. |
| 73 | `faf6a0539` | TAKEN | Layout non-surface/isolation tests retained. |
| 74 | `b2743cf9f` | TAKEN | Equivalent fresh-phase fix retained. |
| 75 | `ff32e2706` | TAKEN | API-only custom retry option disabled. |
| 76 | `7efb80c85` | PORTED | Wanted metadata reads native artwork; dashboard/P2P retained. |
| 77 | `ccb6f156b` | PORTED | Wishlist overhaul keeps native artwork fallback fields. |
| 78 | `7fdd98ac2` | SKIPPED-OK | Legacy album route/page retired; artist-detail additions retained. |
| 79 | `00e955c3a` | SKIPPED-OK | Tests only target retired legacy album browsing. |
| 80 | `4431ac2ab` | SKIPPED-OK | Legacy album-grid filter correction not applicable. |
| 81 | `1f7704f08` | TAKEN | Automation/corruption lint corrections retained. |
| 82 | `854fcee7c` | TAKEN | Artist-detail album query handoff retained. |
| 83 | `78b9318cf` | PORTED | Artist sorting/letters retained; legacy album grid retired. |
| 84 | `9d8e5a3cd` | TAKEN | FLAC verification checkbox present. |
| 85 | `8b3544a3d` | TAKEN | Deezer album-type track-count verification retained. |
| 86 | `980663baf` | PORTED | Suspect tags use native subjects and hand-tag protection. |
| 87 | `604df3417` | TAKEN | Video validation, safe transfer and repair retained. |
| 88 | `6b2191e0c` | PORTED | Compilation regex retained in native suspect-tag detector. |
| 89 | `f946e5848` | TAKEN | Findings formatting retained around native adaptations. |
| 90 | `8b15fc34c` | TAKEN | Flair, retention and read-marker behavior retained. |
| 91 | `3bd2275a1` | TAKEN | Badge profanity guard/avatar ordering retained. |
| 92 | `c8bbfae66` | PORTED | Rescan fence extended to every mapping-detach path; repaired. |
| 93 | `53436fe88` | PORTED | Native artwork/groups retained; fake-lossless stays review-only. |
| 94 | `4892f8a64` | PORTED | Playbooks use registered jobs; retired scan promises removed. |
| 95 | `005800337` | TAKEN | Triage/bulk/HUD retained; job-ID defect originates in 94. |
| 96 | `bae74d87b` | TAKEN | Finding focus and redownload inspector retained. |
| 97 | `1e8edf718` | PORTED | Workstation associations use registered IDs and native review. |
| 98 | `adc1d8d0f` | TAKEN | HUD/bulk styling retained. |
