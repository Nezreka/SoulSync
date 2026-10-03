Final merge audit for upstream/dev 3.5.0. Overall completion and verification are recorded in [the handoff](../upstream-sync-3.5.0.md).

# Upstream audit: range 3

Each commit was inspected with git show and compared with the final worktree. Unchanged files match upstream/dev; changed catalogue/runtime paths were traced. Verdicts include the subsequent local audit fixes.

| Commit | Verdict | Evidence / omission reason |
|---|---|---|
| cf6082e83 | TAKEN | Companion README retained. |
| c9cc864d8 | TAKEN | API reference retained; doc limitations below. |
| 1311e4924 | PORTED | Sample backend, lib2 tracks/files/search; rough superseded. |
| f3e3e9b0e | TAKEN | Studio waveform and UI retained. |
| dd2720cbb | TAKEN | iTunes stores, Deezer skips, cover ranking. |
| 33da6be49 | PORTED | Compatibility artist API strips punctuation; native follow-up. |
| 7baf5d96d | TAKEN | ONNX stems retained; rough removed intentionally. |
| 3c871d7c2 | TAKEN | ListenBrainz Discover button retained. |
| 00257a6e5 | PORTED | Sample schema and interactive lib2 search retained. |
| 76a0912aa | TAKEN | Explorer scroll/zoom retained. |
| df27e680e | TAKEN | Cleaner deep-link retained. |
| 065eb1f8c | TAKEN | Mirror cards open playlist. |
| 7127b0051 | TAKEN | Provider playlist naming retained. |
| 662c35585 | TAKEN | Rolling ListenBrainz week ordering retained. |
| 63dcb4dc7 | TAKEN | Source-tab visibility retained. |
| 54ce9cbda | TAKEN | Auto-sync board spacing retained. |
| cd526ade6 | TAKEN | Playlist-backup explanation retained. |
| 998f2d119 | TAKEN | Download review UI retained. |
| 9234befcd | TAKEN | Backups default off; server safeguards retained. |
| 990f09a4d | TAKEN | Three sync button names retained. |
| 27d3aa9a3 | TAKEN | Auto-sync board retained. |
| 5eafc40e6 | TAKEN | Drag targets glow. |
| 3769391c0 | TAKEN | Sky-blue drag styling retained. |
| 66a480689 | TAKEN | Export coverage tests retained. |
| 504b2497c | TAKEN | Root auto-sync host retained. |
| df1c3c0bb | TAKEN | Personalized card settings hidden. |
| 5de08e560 | PORTED | Daily genres weighted from lib2 artist genres. |
| 2af1c94f9 | TAKEN | Quality-profile selects load. |
| 64d4b4c77 | PORTED | Deezer genres retained; local cache uses lib2. |
| bbc016644 | TAKEN | Mirror process-all respects owner. |
| 16db11f32 | TAKEN | Intermediate release superseded by 3.5.0. |
| f4dd660c3 | TAKEN | Discography modal reflects shown albums. |
| a7efe5bb0 | TAKEN | Spotify fallback test seam updated. |
| 61eab0f2e | TAKEN | Release-task torrent timeout exemptions retained. |
| 05b8a5f29 | TAKEN | Gap-fill modal test retained. |
| e573bd5fc | SKIPPED-OK | Duplicate detector retired; helper/UI compatibility retained. |
| 03595fa75 | TAKEN | Separate Navidrome user settings retained. |
| e5c6bafcb | TAKEN | Download errors report source reason. |
| 34c832bf4 | TAKEN | Release notes retained. |
| 1e7acf909 | TAKEN | Deezer no-init tolerance retained. |
| 1794cd467 | TAKEN | Folder artist uses album parent. |
| 660ce7d8f | TAKEN | Daily mix shares discovery generator. |
| 5be093f21 | TAKEN | Personalized auto-sync live cards retained. |
| c4837ae61 | TAKEN | Single beat detection per frame. |
| 9b1d40cd8 | TAKEN | Theater visuals retained. |
| 649d5e3ed | TAKEN | Mini-player artwork and glow retained. |
| 683d3633d | TAKEN | Player release notes retained. |
| b3a939f0f | TAKEN | Refresh-only mirror pipeline retained. |
| d5a7c3e62 | TAKEN | Updated release notes retained. |
| 23116ce2c | PORTED | Per-profile recs retained; listening fixtures lib2. |
| 1223827c3 | TAKEN | MusicBrainz retries trust HTTP status. |
| 6c1577fdf | TAKEN | Provider errors trust HTTP status. |
| c52ded1d5 | PORTED | Both canonical loaders project provider artist IDs. |
| 822d7911f | PORTED | Alternate lookup rejects catalogue artist IDs. |
| ac61ad011 | PORTED | Durable matches translate catalogue to server IDs. |
| c53246e08 | TAKEN | Partial staging import finishes. |
| e3395b57f | PORTED | AcoustID preserves album artist and track credit. |
| 8148fd722 | TAKEN | SFV/SRR residual junk recognized. |
| 9ceb6fd8a | PORTED | Early stop metadata retained; retired quality omitted. |
| 7b03313a4 | PORTED | Sparse legacy loader replaced by lib2 projection. |
| e416839ef | TAKEN | Discover warming runs under each profile. |
| 686dfcf17 | TAKEN | Every Navidrome user star protects downloads. |
| 525776a56 | TAKEN | Server playlists use per-profile access. |
| ecd612028 | TAKEN | Playlist ownership UI retained. |
| c20145902 | TAKEN | Same-name mirrors receive distinct sync names. |
| e49078763 | TAKEN | Fresh-install automation defaults tested. |
| 7f038ea7d | PORTED | Live-file/server presence; collision/scope regression fixed. |
| bf5ead99b | PORTED | All-profile cleaner protection; legacy deletes omitted. |
| fe05dc840 | SKIPPED-OK | Upstream reverted #1418 in a95363f46. |
| 868888e6f | SKIPPED-OK | Upstream reverted #1420 in a95363f46. |
| a95363f46 | TAKEN | Video duration/kind fix and upstream reverts. |
| 1da55e08d | TAKEN | Video place-file modal styling retained. |
| c44b7dd6d | TAKEN | Playlist naming and missing-track wishlist retained. |
| c30183772 | PORTED | Native lossy tags/art; lib2 replacement recording. |
| 1f7d5055f | TAKEN | Video Discover visual overhaul retained. |
| e4d961ea8 | TAKEN | Release artist and track-slot guard retained. |
| 8e9fcf6db | TAKEN | Romanized/native title comparison guard retained. |
| c0f793b0a | TAKEN | All-profile startup automation rearming retained. |
| f7b3fc45b | TAKEN | Video story blocks retained. |
| ce20e7a43 | TAKEN | Video trust dots retained. |
| 78a1bbd9a | TAKEN | Playlist naming export tests retained. |
| 3aca80595 | TAKEN | Single path override restored in 3dfe76ac2. |
| e054ae941 | TAKEN | Video trust harness variable fixed. |
| 4b3027ae7 | TAKEN | Video trailers/art/banners retained. |
| d976da423 | TAKEN | Hero badge/ticker/tile styling retained. |
| 28c8ba6c6 | TAKEN | Eager artwork/crossfade retained. |
| 2f5a1ddd0 | TAKEN | Genre poster dedupe retained. |
| 072eaf8d7 | TAKEN | Show API total_episodes retained. |
| 326c7848a | TAKEN | Video manual-library match retained. |
| 745bf64eb | SKIPPED-OK | Empty duplicate commit; e054ae941 retained. |
| 3b75278b2 | TAKEN | Picked-title hero styles retained. |
| 7cac2d7fe | TAKEN | Video rematch clears stale artwork. |
| 49d22719f | PORTED | Lib2 release-kind/file-count folder guard retained. |
| f37343130 | TAKEN | Episode-level library checks retained. |
| 67a674615 | TAKEN | Remaining provider rate-limit status fixes retained. |
| 03ac61f8c | TAKEN | 3.4.10 notes superseded by 3.5.0. |
| 98af22151 | TAKEN | 3.5.0 release notes retained. |
| 7b2690c1c | TAKEN | 3.5.0 version/workflow/changelog retained. |
| 3dfe76ac2 | PORTED | Single override retained; lib2 planner types repaired. |

## Findings and B11–B15

Fixed merge regression: `core/sync/library_presence.py:32` accepted a deleted Plex ID when an unrelated Navidrome track reused that ID, or when another library still owned the same catalogue track. Three new tests failed before correction. Presence now requires the active server source and a live nonblank path in the selected library; two UNION lists use 450-ID chunks. Eight presence tests pass; the unrelated async sync-result test was deselected because sandbox socketpair writes fail with EPERM (root reruns outside sandbox).

B11 passes: `core/metadata/canonical_resolver.py:356` and `core/library_reorganize.py:845` carry the artist’s Spotify/MusicBrainz IDs and external provider IDs, without trusting catalogue IDs as provider IDs.

B12 repaired by parent: `core/library2/reorganize_plan.py:123` previously converted stored single/EP/compilation to album and discarded secondary types. The omission was inherited, but broke newly imported $atypes/custom-single behavior. The corrected planner carries effective album_type and parsed secondary_types, the release track count, and offline tags type-source behavior.

Fake-lossless remains review-only: parent removed album inspector re-download/bulk paths; `webui/src/routes/tools/-ui/album-inspection-tray.test.tsx` passes two behavior tests proving individual suppression and a mixed album bulk operation excludes review findings.

B13 passes: enhanced detail no longer invokes legacy finding annotation; `core/automation/blocks.py:380` hides Apply Quality Upgrades. The handler remains registered solely for saved-rule compatibility. B14 passes: both redownload endpoints strip lib2: subjects. B15 passes: recently-added type=tracks returns serialized lib2 tracks in data.items.

Intentional omissions: B7 duplicate-title normalization belongs to the retired detector; legacy library grid/album browsing and `/api/library/albums` stay removed because Library v2 supplies the page. Upstream itself reverted #1418/#1420. No retired quality jobs are restored.

New docs originally advertised retired jobs (`03-dashboard.js:207`, `11-settings.js:138`); those references were corrected to Library v2 monitoring/Wishlist behavior and fake-lossless review. `09-library.js` now describes the native Library v2 page, file versions/recording duplicates, and previewed database-only/permanent removal with journal and quarantine distinctions. The v1 API reference’s albums routes remain valid and are distinct from the removed session route.

Inherited follow-up: native `core/library2/queries.py:24` sorts raw sort_name, and importer stores punctuation unchanged. Upstream punctuation handling is ported to the compatibility artist API, but Library v2 UI still files *NSYNC before alphabetic artists. This pre-existing native-page gap is not a merge regression.

Additional merge regression from audit 1: Tools playbooks used nonexistent registry IDs, silently dropping lyrics/artwork/genre/numbering/preview/folder work. Updated Operations Studio to registered IDs, removed retired quality/duplicate scan promises, and retained duplicate review on the Library page. Four behavioral playbook tests failed before correction; both frontend files now pass all 16 tests, and every playbook/pillar job ID matches the repair registry. Docs pass node syntax checks; owned diffs pass whitespace checks.
