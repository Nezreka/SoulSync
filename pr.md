# soulsync 3.4.8: `dev` → `main`

premier overhauls of the wishlist, dashboard and automations pages, library album views with sorting, chat p2p sharing and badges, and a long tail of filing, scan and import correctness fixes. scope: the 48 commits since the 3.4.7 release commit (`0a8a6911d`).

## the wishlist, dashboard and automations overhauls

- the music wishlist got the premier treatment (#1336): a new after-hours record-store design with a glass hero, live stats, a failing-track triage banner with retry-all, per-artist and per-album grab, shift-click range selection, nebula sorting, and bulk calls chunked at the server's 200-id cap so grabbing a big artist no longer 400s. removing an artist now targets exact track ids instead of name-matching, so a shared "Greatest Hits" title can't nuke another artist's tracks.
- the music dashboard was reskinned (#1355) with an editorial hero, composed rail headers with live counts and a feed switcher, larger album cards with timestamp chips, and a wider right column — zero behavior changes, all pinned test contracts kept.
- the automations page was rebuilt around sidebar navigation (#1347): an overview dashboard with a health hero, upcoming-runs timeline and attention queue, smart collections, URL-backed navigation, and a proper empty state for fresh installs. a follow-up (#1354) fixed the 880–1150px responsive squeeze where the sidebar refused to collapse and clipped the hero.

## library views, stats and the tools studio

- the library page grew an albums grid (#1307, sarab97): flip between artists and albums, with search, A–Z and metadata-source filters living in the URL. a follow-up pass (#1338) added sorting (recently added, year), a denser album grid, hover-prefetch that covers the ~3s cold load, and fixed the wrong "loading artists" copy; clicking an album now deep-links into the artist page instead of landing on the plain view (#1337).
- the stats dashboard was overhauled with a Your Year keepsake studio, and the tools page became a Simple Studio vs Advanced workstation with a smart-action triage center, 1-click safe bulk fixes and a live mission-control HUD. sarab97's hidden-file handling standardizes library walking so quarantine, staging and AppleDouble junk are filtered consistently (#1285).
- the settings page could fail to load entirely when a referenced checkbox didn't exist in the markup; the missing verify-flac-decode checkbox was added and a drift-guard test now fails CI if JS and HTML disagree again (#1339).

## chat

- wanted requests can now be fulfilled peer-to-peer (#1333): a Share Now action lets someone who owns the release broadcast their files over the room protocol, and the requester enqueues the download in one click.
- user flair badges arrived (#1344) with hardened anti-impersonation on both sides, per-room history retention (default 30 days), and notification read-markers that persist; a follow-up added the same obfuscation-proof profanity filter to badges (#1345). the soulsync tag next to soulsync users is now the logo instead of text (#1369).

## discovery and search

- the discovery/search wrap-up (#1334) closed the remaining competitive gaps: named interactive/automatic search modes, provenance on every download decision, a policy facet in the inspector, cross-source dedupe in best-quality mode, bulk queue ops, retry profiles, a failed-download blocklist, per-profile discover layouts, recommendation explanations and broader recipes. the artist-news inbox is provider-hook scaffolding only — it does nothing yet.
- the discover endpoints could pin the CPU for minutes on large pools; exclude-owned checks are now index-backed (#1350, #1356).

## filing and import correctness

- EPs stop filing as singles two ways: the import no longer falls back to per-track counts when the album total is unknown (#1365), and the artist-detail download modal reconciles the real album count from the track fetch instead of the fabricated card count (#1367).
- a standard edition no longer reuses a deluxe/anniversary folder when its track total is known and smaller than the existing roster; the shared edition-upgrade bonus used by download analysis was left alone (#1362). the file finder now matches extensionless `id||artist - title` dispatch keys against the on-disk stem, so downloaded songs like `PRYVT - ANGEL.flac` are recognized for import (#1366, #1368).
- the duplicate detector strips ` - from ...` provenance tails before comparing, so "Rabbit Run" finally matches 'Rabbit Run - From "8 Mile" Soundtrack' (#1361). AcoustID verification no longer false-fails bracket-only titles like "[untitled]" (#1353, #1360).
- file organization grew an auto-disambiguation toggle: off means the template is authoritative and the importer stops injecting the MusicBrainz release disambiguation into folder names (#1352, #1359). the $albumtype fix stops a bare "album" from Deezer/Spotify being trusted as an explicit type signal, and Deezer's raw "compile" now maps to "compilation" (#1340); mandos21's $atypes brings beets-compatible release-type labels like [EP][Live] (#1302).
- three profile drops fixed (#1351, #1358): post-processing stamps the batch profile, auto-wishlist stamps each track's owner, and Jellyfin's default scan refreshes every music library.
- the "already in library" check no longer flags different songs as owned — "Solve" vs "Solace", "Snowball" vs "Snowblind" — and the player can't play the wrong file through a bad match anymore (#1292, #1363). Specialmed's staging folder stops being deleted by repair cleanups, orphan "move to staging" is no longer a dead end, and the AcoustID mismatch modal surfaces the per-track fix button (#1364).

## scans and library health

- Navidrome scans stop lying: the incremental scan's stale album index is reset so new albums aren't filtered out, deep scans skip stale-row removal while Navidrome itself is rescanning, and Deezer playlist imports use the album's own track count (#1346). the backend review extended the mid-rescan deletion fence to every server type and made failing probes fail closed for large stale sets (#1348).
- the corrupt FLAC detector is now practical on big libraries: remembered passes by path/size/mtime, parallel decoding, quarantine-first repair, and a run-maintenance-job automation action (#1332, curiousmoose24). a suspect-album-tag detector flags "Now 45"-style mistags with a one-click re-identify repair flow (#1335).

## audiobooks on soulseek

- Soulseek audiobook searches find single-file `.m4b` books instead of dropping them, the audiobook database lives in the persisted volume, and wishlist persistence was fixed (#1330, curiousmoose24).

## validation

- the dev test baseline was cleared: all 16 pre-existing failures reproduced on clean dev and fixed, so real regressions can't hide in the noise (#1357).
- fixes in scope shipped with regression tests and green neighboring suites, per their PRs; the backend-review batch re-verified each finding on current code before fixing (#1348).
- a full green full-suite run on the release head has not been verified.
