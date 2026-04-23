# Plan: Download Quality Flags Refactor

## Background

The current `force_download_all` flag is misleadingly named. It does NOT lower
quality requirements or "force download any quality". It only skips the library
ownership check so every track is treated as missing and re-downloaded regardless
of whether the user already owns it.

This causes two distinct problems:

1. The name implies quality-related behavior that doesn't exist.
2. The discover sync tab currently sends `force_download_all: true`, which means
   every discover playlist sync re-downloads tracks the user already owns.

Additionally, users have expressed a real need for a per-batch quality override:
their main library should stay strict (FLAC preferred, high bitrate) while
rotating/ephemeral playlists (discover) should grab whatever is available for
quantity over quality.

Per-client fallback settings already exist (Soulseek quality profile
`fallback_enabled`, Deezer/Tidal/Qobuz `allow_fallback` chains) but they are
global all-or-nothing flags. There is no way today to say "relax quality just
for this one batch".

## Goals

1. Rename `force_download_all` to reflect what it actually does (skip ownership
   check / re-download owned).
2. Stop discover sync from blindly re-downloading owned tracks.
3. Add a new per-batch "Any Quality" flag that bypasses quality filtering for
   that specific batch only, without touching the user's global quality
   settings.

## Non-Goals

- No changes to the global per-client quality/fallback settings.
- No changes to the matching engine scoring.
- No changes to album consistency / MusicBrainz preflight logic.

## Phase 0 (this PR): UI framework only

Scope is tiny and safe to ship immediately.

- Replace the "Force DL" toggle in the Sync page Discover tab with an "Any
  Quality" toggle.
- Leave the new toggle permanently disabled / greyed out for now.
- Tooltip on the toggle reads something like "Coming soon: download any
  available quality for this batch".
- Remove the `force_download_all: true` body payload from the discover sync
  path. Discover playlists will now always run ownership analysis.
- No backend changes in this phase.

Files touched:
- `webui/static/discover.js` - replace toggle HTML, remove `forceDownload`
  plumbing from `syncDiscoverPlaylistFromTab` / `_doSyncDiscoverPlaylist`.

## Phase 1: Rename `force_download_all`

Rename to `skip_ownership_check` (backend) and surface in the UI as
"Re-download Owned" (or equivalent).

- Backend: add new key `skip_ownership_check` everywhere the flag is used.
  Accept both keys on inbound API payloads for one release (back-compat).
- Frontend: rename the Wishlist / Downloads modal toggles, keep the same
  default behavior (wishlists still skip the library check by default).
- Update `helper.js` tooltip description to match the new name and behavior.

Files touched:
- `web_server.py` (lines ~15663, 24858, 25858, 26065, 29051, 29057, 29062,
  29104, 29135, 33556, 33596)
- `webui/static/downloads.js` (lines ~195, 598, 2152, 2155, 2195, 2436)
- `webui/static/wishlist-tools.js` (line ~6347)
- `webui/static/helper.js` (lines ~663)

## Phase 2: Implement "Any Quality" per-batch override

Introduce a new batch flag `any_quality` that, when set, bypasses quality
filtering for that batch only.

Backend behavior:
- Add `any_quality` to the batch dict alongside `skip_ownership_check`.
- For Soulseek: when `any_quality` is true, skip the call to
  `soulseek_client.filter_results_by_quality_preference()` and pass ranked
  candidates through unchanged.
- For Deezer / Tidal / Qobuz: when `any_quality` is true, temporarily force
  the candidate selection path to treat `allow_fallback=True` AND start from
  the lowest quality tier so downloads succeed fastest.
- The flag is per-batch only. Global quality profile / `allow_fallback`
  settings remain untouched.

Frontend behavior:
- Enable the "Any Quality" toggle added in Phase 0.
- Remove the "Coming soon" tooltip, replace with a real description.
- Wire the toggle into the discover sync POST body as `any_quality: true`.
- Consider exposing the same toggle on the Wishlist / Downloads modal.

Files touched:
- `web_server.py` - batch creation + candidate selection path
- `core/soulseek_client.py` - accept override in filter call
- `core/deezer_download_client.py`, `core/tidal_download_client.py`,
  `core/qobuz_client.py` - accept per-request quality override
- `webui/static/discover.js` - enable toggle, send flag
- `webui/static/downloads.js` - add matching toggle on manual download
  modals

## Risks / Open Questions

- Soulseek quality profile bypass: is it safe to pass all density-filtered
  candidates through without the priority filter? Likely yes, since the
  matching engine already ranks by confidence and peer quality.
- Streaming clients with strict API quality params (Tidal HiRes vs Lossless
  entitlement, Qobuz subscription tiers): forcing lowest tier should be safe
  for all users regardless of subscription.
- Back-compat on the renamed flag: keep the old `force_download_all` key
  accepted for at least one release cycle to avoid breaking any third-party
  callers or stale browser sessions.

## Rollout

- Phase 0 ships with this PR (UI framework + discover fix).
- Phase 1 and Phase 2 can ship as separate PRs on dev.
- No migrations required; all flags are request-scoped and batch-scoped.
