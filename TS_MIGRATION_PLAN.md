# js -> typescript migration plan

standing goal (boulder, aug 25): leave nothing as .js eventually. this is the map, drafted during the web_server decomposition night. same discipline as the react migration: one module at a time, parity tests before the flip, npm run build after src changes, vanilla-reachability checks before deleting anything.

## what actually loads (the real surface)

the music shell (index.html) still loads ~40 vanilla scripts. total vanilla weight: ~93k lines music side + ~28k video side. everything in webui/static that index.html does NOT load is a deletion candidate, not a migration candidate - verify reachability first.

## phases

### phase 0: foundations (do first, everything depends on them)
- shared-helpers.js (4.7k) and core.js (1.9k) - the shell. every page script leans on these. port to webui/src as typed modules re-exported onto window for the remaining vanilla consumers (the same globals-rehomed pattern the library migration used).
- helper.js (4.3k) - carries WHATS_NEW / VERSION_MODAL_SECTIONS release data plus the tour engine. the release data wants to become a typed module the release process edits; the tour engine ports straight.
- fetch-dedupe.js, init.js (3.8k), particles.js, worker-orbs.js - small, port or fold in.

### phase 1: leaf modules (small, low-risk, build the muscle)
blocklist.js, service-switch.js, my-accounts.js, origin-history.js, watchlist-history.js, track-detail.js, server-activity.js, api-monitor.js (1.2k), setup-wizard.js (1.1k), enrichment.js, enrichment-manager.js (1.3k), library-globals.js, manual-library-match.js, discover-section-controller.js.

### phase 2: the four big arcs (one at a time, each its own campaign)
- settings.js (6.9k) - self-contained page, clear seams, probably first.
- sync-services.js (11.5k) + sync-spotify.js (2.6k) + sync-listenbrainz.js + sync-lastfm.js + sync-soulsync-discovery.js + auto-sync.js (2.5k) - the sync-page vanilla underlay. note the react sync page already exists; these are the download-engine-side scripts that deliberately stayed vanilla. migrating them is typing, not rebuilding.
- wishlist-tools.js (7.5k) + downloads.js (6.8k) - downloads-page underlay, same situation.
- chat.js (7.3k) + chat-protocol.js + chat-hash.js + chat-games.js (1.3k) + chess-engine.js - chat has known seam-bug history (green fold tests hiding seam breaks), so this one gets the diff-read method, not a transcription pass.

### phase 3: stats-automations.js (6.3k), beatport-ui.js (3.6k), media-player.js (3.3k), docs.js (3.5k), pages-extra.js (1.2k)

### phase 4: the video side (~28.5k)
video-side.js and friends - biggest single chunk, its own multi-night campaign, after the music side proves the method.

### also worth fixing on the way
- the three CDN script tags (graphology x2, sigma) are the only external script dependencies left - vendor them alongside vendor/socket.io.min.js so an offline install renders the artist map.

## status

- **pipeline: PROVEN** (aug 26). src/shell/* builds as a synchronous IIFE via vite.shell.config.ts -> static/dist/shell.js (fixed name, v=static_v cache-busting), loaded as a classic script in the ported file's original slot. `npm run build` = react build then shell build (react empties dist - order matters). the window-export list in src/shell/index.ts is the compatibility contract; a census test pins it.
- **ported (9 files, ~2,780 vanilla lines) + 1 dead file deleted**: blocklist (1f10da278); origin-history + watchlist-history + my-accounts + service-switch (9d5a3e489); library-globals + track-detail + manual-library-match + server-activity, and discover-section-controller.js deleted as dead code (004b733b8). 41 window globals flow through the pinned SHELL_WINDOW_EXPORTS contract; the python onclick-coverage guard parses that contract. shared src/shell/html.ts escaper (fails closed). native confirm() kept 1:1 in origin-history/my-accounts - showConfirmDialog conversion is a flagged follow-up. library-globals proves the shared-state pattern: state objects self-assign onto window and bare reads in remaining classic scripts fall through the scope chain to the SAME objects; in-place clears (stack.length = 0, set.clear()) preserved because react holds the same references.
- **critical finding for phase 0**: core.js is the shared-lexical-state spine - its 83 top-level `let`s are read as BARE NAMES by other classic scripts through the global lexical environment, which an IIFE cannot reproduce (window props resolve for bare reads, but code paths exist that distinguish let-vs-window, e.g. sync-listenbrainz's dormant window-fallback branch). core.js therefore ports LAST in phase 0, with a per-binding consumer audit. shared-helpers.js is the right big first target: 121 top-level functions (window-semantics already), only 11 state bindings of which 4 are shared (SOURCE_LABELS, SOURCE_ORDER, EXPERIMENTAL_SOURCES, _MB_UUID_RE - no duplicate declarations elsewhere). also noted: escapeHtml is declared in BOTH shared-helpers.js and downloads.js (downloads wins today by load order) - resolve during the downloads arc.

## method per module
1. read the module end to end; list every window.* it defines and who consumes each (rglob the repo, not just index.html).
2. port to webui/src as .ts with real types; re-export the window globals from the shell entry until the last consumer is migrated.
3. artefact/behavior parity tests against the vanilla (constant pins, dom-shape pins) BEFORE flipping the script tag.
4. flip index.html to the built bundle, delete the .js only after a reachability check shows nothing else loads it.
5. tests/static/*.mjs pin some vanilla files by source - repoint them when files move.
