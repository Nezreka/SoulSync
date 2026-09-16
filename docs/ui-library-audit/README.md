# Library UI review — 6 September 2026

[Local review build](http://127.0.0.1:8008/library?artist=28&header=compact&artistView=releases&releaseView=table&section=artists&view=cards&monitored=all&page=1&releases=library&wantedKind=missing). This document records the first committed UI-review checkpoint. Screenshots come from the running test instance.

## Changes

- One monitoring bookmark beside the artist name in both header sizes, with a 28 × 30 px frame, including mobile. The header-size switch stays beside the title. Metadata and provider links remain visible. The genre-to-provider gap is reduced to 6 px.
- Artist Settings consolidates the artist quality profile and future-release settings. Tools groups file actions, metadata actions and maintenance, including Edit Metadata and Change Photo.
- Album rows show one colored owned/total fraction: green complete, amber partial, red missing, neutral unknown. Date, size and quality stay below the title. Redundant progress bars and missing/complete labels are removed.
- Artist tables keep columns on wide screens; optional facts move below the name at narrower widths. Expanded tracks, Wanted, search results and file versions also reflow without horizontal scrolling. Sorting stays available in compact layouts. Interactive Search quality badges wrap within their column, including the below-profile result.
- Music Videos is a separate tab beside My Library and All Releases. YouTube search starts when this tab opens. Returning preserves the selected release view. The video spotlight has a bounded height.
- Preview Retag, Preview Rename / Organize, Manage Tracks, History and the album/track editors use the same maximum 1,780 × 1,050 px workspace as Interactive Search. Their top and bottom edges align at the same viewport size. Small pickers such as Change Photo remain content-sized; Artist Settings keeps its previous width.
- Retag has a compact header, album groups and field-by-field current → proposed values. Unchanged files are hidden by default and the results area uses the full shared workspace.
- Manual Retag overrides remain protected: Current file, Your manual edit and Discovery / provider are labelled separately. Keep mine is the default; choosing a provider value releases only that field on a selected track. Keep mine for all clears every pending provider choice. Tests cover preserving, selectively releasing and restoring all manual choices. The latest screenshots show the real SWAG title edit (SWEET SPOTs).
- Rename / Organize opens All releases by default, with all current/destination paths in one preview. The scope selector can narrow to one release, including singles and EPs. Unchanged/skipped/conflict states remain visible. No-op and blocked previews cannot be applied. Full paths can be copied.
- History groups related activity while retaining individual events. Expanded groups show track, release, result and reason. Search, category filtering, ungrouping and loading older events remain available. Its compact controls and fixed footer leave the shared workspace to the event list.
- Album and track editors share the same stable workspace dimensions. Album Quality and Metadata were checked separately; the quality choices sit side by side and metadata uses compact horizontal field groups.
- The track action opens Metadata directly. Metadata, Quality, Tags, Lyrics, Info and History were each checked at desktop width. The six tabs wrap into two rows on mobile. Manage Tracks places a short version legend beside the filter on wide screens.
- Photo selection, metadata editing, file management, export and reassignment use consistent dialog framing. Narrow-window fixes include collapsed Retag cells, hidden Wanted search buttons, clipped reassignment search controls and oversized path-copy spacing.

## Continuation after the checkpoint

- The 390 px Artist card view now keeps two compact cards per row. It shows several artists per screen while retaining the same bookmark treatment and colored owned/total fraction. The Artist table remains the denser alternative.
- Artist searches and monitoring filters with zero matches now show an explicit no-results state and a Reset filters action. A filtered result count of zero no longer exposes the misleading Import library action.
- The All Releases filter bar uses smaller page-scoped gaps and controls, so Show, Include, Status and Sources remain on one line at a 1,440 px viewport. On narrow screens they wrap without horizontal scrolling.
- Long release names use a two-line clamp in the mobile Discover grid, making similarly named editions distinguishable without increasing the square cards.
- The Manage Tracks removal confirmation is content-sized at up to 960 px instead of inheriting the full tool-workspace height. Impact counts, path review, both removal modes, the blocking reason and final action remain visible together.
- Retag, Rename / Organize and expanded History were rechecked at 390 px. Manual-tag ownership, current/proposed paths and history reasons remain visible with zero page-level horizontal overflow.
- Provider artists from Search now link directly to Library V2 discovery. The link carries the artist source, id and name together with All Releases, card view and the rich header, so an artist outside the catalogue no longer visits the legacy `/artist-detail` route before reaching the intended view. The old route remains as a fallback for saved links and other callers.

File-action naming and grouping were informed by [Lidarr’s artist details toolbar](https://github.com/Lidarr/Lidarr/blob/develop/frontend/src/Artist/Details/ArtistDetails.js).

## Visual evidence

Browser screenshots cover desktop (including 2,280 px), 760 px and 390 px widths. Tables/results were also measured against their containers; the checked responsive layouts do not require horizontal scrolling.

| Area | Screenshots |
| --- | --- |
| Artist headers and albums | [Compact](screenshots/final-artist-compact.png), [Rich](screenshots/final-artist-rich.png) |
| Artist table | [Desktop](screenshots/final-artists-table.png), [760 px](screenshots/final-artists-table-narrow.png), [390 px](screenshots/final-artists-table-mobile.png) |
| Tracks and Wanted | [Tracks](screenshots/final-tracks-narrow.png), [Wanted](screenshots/final-wanted-mobile.png) |
| Retag | [Live manual edit](screenshots/final-retag-manual-live.png), [760 px](screenshots/final-retag-manual-narrow.png), [390 px](screenshots/final-retag-manual-mobile.png) |
| History | [Compact](screenshots/final-history-compact.png), [Expanded](screenshots/final-history-expanded.png), [390 px](screenshots/final-history-mobile.png) |
| Rename / Organize | [All releases](screenshots/final-rename-all-wide.png), [760 px](screenshots/final-rename-all-narrow.png) |
| Images and metadata | [Photo](screenshots/final-change-photo.png), [Metadata](screenshots/final-edit-metadata.png), [Album desktop](screenshots/final-album-editor-wide.png), [Album mobile](screenshots/final-album-metadata-mobile.png) |
| Track editor | [Desktop](screenshots/final-track-editor-wide.png), [390 px](screenshots/final-track-editor-mobile.png) |
| Other tools | [Search desktop](screenshots/final-interactive-search.png), [Search 760 px](screenshots/final-interactive-search-narrow.png), [Files desktop](screenshots/final-manage-tracks-wide.png), [Files 760 px](screenshots/final-manage-tracks-narrow.png), [Reassign](screenshots/final-reassign-mobile.png), [Export](screenshots/final-export-mobile.png) |
| Music Videos | [Desktop](screenshots/final-music-videos.png), [390 px](screenshots/final-music-videos-mobile.png) |
| Shared workspace checkpoint | [Interactive Search](screenshots/uniform-interactive-search.png), [Retag](screenshots/uniform-preview-retag.png), [Rename / Organize](screenshots/uniform-preview-rename-organize.png), [Manage Tracks](screenshots/uniform-manage-tracks.png), [History](screenshots/uniform-history-expanded.png) |
| Album editor checkpoint | [Quality](screenshots/uniform-album-edit-quality.png), [Metadata](screenshots/uniform-album-edit-metadata.png) |
| Track editor checkpoint | [Metadata](screenshots/uniform-track-edit-metadata.png), [Quality](screenshots/uniform-track-edit-quality.png), [Tags](screenshots/uniform-track-edit-tags.png), [Lyrics](screenshots/uniform-track-edit-lyrics.png), [Info](screenshots/uniform-track-edit-info.png), [History](screenshots/uniform-track-edit-history.png), [390 px Metadata](screenshots/uniform-track-edit-metadata-mobile.png) |
| Continuation pass | [Artist cards 390 px](screenshots/continue-artists-cards-fixed-390.png), [filtered empty state](screenshots/continue-artists-empty-fixed-390.png), [All Releases filters](screenshots/continue-all-releases-filters-fixed.png), [long release names](screenshots/continue-long-release-mobile-fixed.png), [compact removal confirmation](screenshots/continue-manage-tracks-confirm-fixed.png), [Retag 390 px](screenshots/continue-retag-390.png), [expanded History 390 px](screenshots/continue-history-expanded-390.png) |
| Search to Library V2 | [External artist, rich discovery view](screenshots/search-provider-direct-library-v2.jpg) |

Earlier `before-*` and `after-*` captures document the iterations. Live data and provider results can change between captures. The `uniform-*` captures are the authoritative images for this checkpoint's dialog dimensions.

## Validation and limits

- Latest focused frontend run: 49 files, 400 tests passed (Library plus ArtistVideosSection).
- Final file-management/track-table follow-up: 27 tests passed.
- Album layout follow-up: 44 tests passed across mutation boundaries, artist release views and track tables.
- History backend: 34 tests passed.
- Type-aware lint of 14 changed implementation modules: zero warnings/errors.
- Production build succeeded; whitespace diff check clean.
- Earlier full frontend run: 411 files, 8,051 tests passed, before the final responsive/video refinements. Focused checks cover the refinements.
- Repository-wide `npm run check` has pre-existing formatting and lint failures outside this Library change; it is not claimed as passing.
- Older acoustic-check events did not store the historical verdict. Their UI labels the available value as current file status; historical results are not invented.
- Browser checks opened previews and searched sources. No retag write, rename apply, reassignment, deletion or media download was submitted.
- At a 2,279 × 1,280 viewport, Interactive Search, Retag, Rename / Organize, Manage Tracks, History and both editors each measured 1,780 × 1,050 px at x=249.5/y=115. Track editor content measured zero horizontal overflow at desktop, 760 px and 390 px.
- The continuation pass used browser rendering and geometry checks only; no test suite and no Retag, rename, delete, import or monitoring write was run.
- The Search routing follow-up was checked from a real provider result to the final Library V2 URL and rich discovery screen. No artist was bookmarked or otherwise added during the check.

## Next UI pass

- Continue the Library audit from this checkpoint instead of reopening the settled dialog-width, monitoring-bookmark and album-count decisions.
- Recheck intentionally sparse tabs such as Album Quality and Track Info when artists with more varied profile data are available.
- Exercise loading and server-error transitions with controlled responses; the real-data empty and no-match states are covered.
- Continue through destructive confirmation variants using previews only, especially safe on-disk files where the permanent-delete acknowledgement becomes available.
- Keep horizontal scrolling out of Library tables and dialog content; remeasure the affected container after every responsive change.
