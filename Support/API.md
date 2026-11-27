# SoulSync REST API

SoulSync includes a full REST API at `/api/v1/` that lets you control everything from external apps, scripts, Discord bots, Home Assistant, or anything that can make HTTP requests.

## Quick Start

### 1. Generate an API Key

Go to **Settings** in the SoulSync web UI and open **System › API**. Under **New API key**, give it a label and click **Generate key**, then copy the key immediately — it's only shown once.

Alternatively, if no keys exist yet, use the bootstrap endpoint. It needs no key, but only the admin may call it: on a single-profile install with no login or launch PIN that's any request, otherwise send your signed-in admin session cookie (or just use Settings).

```bash
curl -X POST http://localhost:8008/api/v1/api-keys/bootstrap \
  -H "Content-Type: application/json" \
  -d '{"label": "My First Key"}'
```

### 2. Make Requests

Pass your key via the `Authorization` header:

```bash
curl -H "Authorization: Bearer sk_your_key_here" \
  http://localhost:8008/api/v1/system/status
```

Or as a query parameter:

```
http://localhost:8008/api/v1/system/status?api_key=sk_your_key_here
```

### 3. Response Format

Every response follows this envelope:

```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "pagination": null
}
```

Error responses:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "NOT_FOUND",
    "message": "Artist 999 not found."
  },
  "pagination": null
}
```

Paginated responses include:

```json
{
  "pagination": {
    "page": 1,
    "limit": 50,
    "total": 347,
    "total_pages": 7,
    "has_next": true,
    "has_prev": false
  }
}
```

---

## Authentication

All `/api/v1/` endpoints require an API key (except the bootstrap endpoint).

| Method | Details |
|--------|---------|
| Header | `Authorization: Bearer sk_...` |
| Query  | `?api_key=sk_...` |

Keys are generated as `sk_` followed by a random token. Only the SHA-256 hash is stored — the raw key is shown once at creation.

### Error Codes

| Status | Code | Meaning |
|--------|------|---------|
| 401 | `AUTH_REQUIRED` | No API key provided |
| 403 | `INVALID_KEY` | API key is wrong or revoked |

---

## Rate Limiting

Requests with a valid API key are rate-limited to **600 per minute**, counted per key. Requests with no key or an invalid key are rate-limited to **60 per minute** per IP address.

Exceeding the limit returns `429 RATE_LIMITED`.

---

## Global Query Parameters

These optional parameters work on all endpoints that return entity data:

| Param | Type | Description |
|-------|------|-------------|
| `fields` | string | Comma-separated list of fields to return (e.g. `?fields=id,name,thumb_url`). Omit to return all fields. |

---

## Multi-Profile Support

SoulSync supports multiple user profiles. Profile-scoped endpoints (watchlist, wishlist, discovery) accept a profile identifier:

| Method | Details |
|--------|---------|
| Header | `X-Profile-Id: 2` |
| Query  | `?profile_id=2` |

If omitted, defaults to profile 1 (admin). Profile scoping applies to: watchlist, wishlist, and discovery endpoints.

### What a member's session gets (web UI routes)

What the server is doing is the admin's. A member's session is refused (`403`) these, and the web UI never asks:

- `GET /api/system/stats` (downloads across the server, speed, uptime, memory) and its push, `dashboard:stats`, which only the admins' sockets get
- `GET /api/repair/progress`, `GET /api/video/health`, `GET /api/video/bulk/status`
- the enrichment workers, music and video: every `/api/enrichment/*` and `/api/video/enrichment/*` route (status, breakdown, unmatched, priority, pause, resume, retry), `GET /api/soulid/status`, `GET /api/hydrabase-worker/status`, and the `enrichment:<worker>` pushes, which only the admins' sockets get (`enrichment:repair` stays everyone's, the Tools page reads it)
- the diagnostics: `GET /api/debug-info`, `GET /api/logs/tail`, `/api/settings/log-level`, `/api/help/health`, `/api/help/bundle`, `/api/help/logs*`
- the spotify ban and the api rates: `GET /api/spotify/rate-limit-status`, `GET /api/rate-monitor/history/<service>`, and the `rate-monitor:update` push, which only the admins' sockets get

Some answer a member with less instead of a `403`:

- `GET /status` and the `status:update` push: a member's copy has no spotify ban (`rate_limited` false, `rate_limit` null, `post_ban_cooldown` 0) and no `enrichment`. The admins' sockets get the whole push, every member socket the trimmed one
- `GET /api/database/stats` gives a member `artists`, `albums` and `tracks` of the library they browse, nothing else; the `dashboard:db_stats` push (size, path, scan times) only the admins' sockets get
- `GET /api/video/dashboard` leaves `library.size_bytes` out for a member

`GET /api/automations/progress` and the `automation:progress` push give a member the runs of their own automations only, never a system one.

In the music library, `POST /api/library/v2/<entity>/<id>/search` (Automatic Search) and `/enhance` (Enhance Quality, Upgrade) download right away, so a profile that asks first is refused them; its way in is the Wishlist. `GET /api/library/v2/enabled` says what the caller may do: `can_wish`, `can_download`, and `can_grab` (an Interactive Search pick for a library row).

Under a quality profile with `upgrade_whole_albums` on, Automatic Search and Enhance Quality never fetch one track of an album you own: the album is upgraded as one release in the background (the job result's `album_upgrades` counts them), or left as it is. `GET /api/quality-profile/album-upgrades?limit=` lists what those upgrades did, newest first: `{album_id, album_title, artist_name, outcome: upgraded|no_complete_release|skipped|failed, source, release, reason, tried, created_at}`.

### Approved requests that wait for a release pick (web UI routes)

Each kind (music, movies, TV, audiobooks) can be set to have an admin pick the release instead of grabbing the best match. Everything starts on automatic. Music videos, YouTube videos and podcast episodes always download right away.

- `GET /api/requests/acquisition`: `{modes: {music, movie, tv, audiobook}}`, each `auto` or `manual`, plus `may_change`. `PUT` (admin only) with `{modes: {movie: "manual"}}` changes the kinds it names; an unknown kind or mode is a `400`.
- Every approve route takes `{"acquisition": "auto" | "manual"}` for that one request: `POST /api/requests/music/approve`, `POST /api/requests/media/<id>/approve` (books only, an episode is never held), `POST /api/video/requests/<id>/approve`. Approve all always uses the settings.
- A held music request keeps its wishlist rows with `request_status = 'awaiting_selection'` and its history row in that status. A held book is a `media_requests` row in `awaiting_selection` and isn't on the wishlist yet. A held movie or episode is on the requester's wishlist with `awaiting_selection = 1`, its request row says `acquisition: "manual"`, and a held show doesn't join their watchlist. No automatic path takes a held row: the wishlist runs, retries, rss, search all and the download monitor's retries all leave it alone.
- `GET /api/requests/awaiting` (admins, moderators): `{music: [groups], media: [books]}`, what the caller may pick (a moderator never their own). `GET /api/video/requests/awaiting`: `{titles: [{id, kind, tmdb_id, title, requesters, approved_at, seasons, downloading}]}`.
- Music picks: `POST /api/requests/music/pick/inspect` `{profile_id, track_id}` streams every source's hits for one held track (searched as the requester), and `POST /api/requests/music/pick/download` `{profile_id, track_id, candidate, override?}` downloads that file into their library. The row stays held until the file lands, so a failed pick can be picked again.
- Books: the release picker grabs with `POST /api/audiobooks/grab`, then `POST /api/requests/media/<id>/picked` moves the request on. Movies and TV: the video release picker's own `POST /api/video/downloads/grab`.
- Download automatically instead: `POST /api/requests/music/awaiting/auto` `{profile_id, key}`, `POST /api/requests/media/<id>/auto`, `POST /api/video/requests/<id>/auto` (a show joins the watchlist then).
- The requester's list says it too: music and book rows in status `awaiting_selection`, video rows with `awaiting_selection: true` (nothing picked yet) or `picking: true` (a picked release is downloading).

### Listening history and your plays (web UI routes)

Each works on the caller's own listening history: the admin's is the shared one, a member's is their own.

- Spotify streaming history, from Spotify's privacy export. The browser reads the export and sends only music plays; the file never comes to the server. All five need **Listening stats** (`music.stats`).
  - `GET /api/listening-import/spotify`: `{imported, include_in_stats, plays, first_played, last_played, artists, matched, linked, updated_at, max_batch}`. `plays` are the plays the import added, `linked` the ones already in the history from ListenBrainz or Last.fm, `matched` the added plays linked to a library track.
  - `POST /api/listening-import/spotify/batch` `{rows: [{ended_at, ms_played, track, artist, album?, uri?, coarse?}], include_in_stats?}`: at most `max_batch` (5000) rows. `ended_at` is the stream's end in utc (`2024-01-15T08:23:45Z`, or `2019-03-02 21:14` with `coarse: true` for account data). Plays under 30 seconds, podcast or audiobook rows and rows marked `incognito` or `episode` are dropped. Answers `{received, kept, added, merged, dropped: {short, podcast, private, invalid}}`. A member's first import makes their listening history their own.
  - `POST /api/listening-import/spotify/finish`: refreshes the stats and returns the summary.
  - `POST /api/listening-import/spotify/settings` `{include_in_stats}`: off keeps the import out of Stats, Your Year, the dashboard's week and the artist page; recommendations and mixes still use it. `404` when nothing is imported.
  - `DELETE /api/listening-import/spotify`: removes every play the import added (`{removed}`); plays from other sources stay.
- `GET /api/library/v2/artists/<id>/your-plays` (Library): `{listening: {play_count, first_played, last_played, plays_30d, rank, artists_played, months: [{month, plays}], top_tracks: [{title, album, play_count, last_played, track_id, album_id, thumb_url}]}}`, or `listening: null` when the caller never played them. A play counts when it names the artist or one of its aliases, or is linked to one of its library tracks.
- Album tracks (`GET /api/library/v2/albums/<id>`) carry `popular_source`, `lastfm` or `listenbrainz`, on the tracks marked `popular`.

### A new profile's Discover (web UI routes)

A profile with fewer than five taste seeds (artists played at least twice, watchlist artists, artists it picked) is new. Everything here is per profile.

- `GET /api/discover/starter`: `{seeds, threshold, needs_seeds, dismissed, show_banner, picked: [{name, deezer_id, image_url}], connections: {listenbrainz, lastfm}, spotify_import: {available, href}, build: {state, done, total, stored}}`. `build.state` is `idle`, `running`, `done` or `failed`.
- `POST /api/discover/starter/dismiss`: hide the banner for this profile.
- `GET /api/discover/starter/artists?genre=<deezer genre id>` or `?q=<search>`: `{artists: [{name, deezer_id, image_url, popularity}], genres: [{id, name}]}` for the picker. Blocked artists are left out.
- `POST /api/discover/starter/picks` `{artists: [{name, deezer_id, image_url}]}`: keep them as this profile's picked artists and fetch their similar artists in the background (`build` reports progress). An empty list is a `400`.
- While a profile is new and has no recommendations, `GET /api/discover/similar-artists` answers chart picks with `starter: true`, each with `explanation: {kind: "trending", seeds: [{name: "Deezer's Rock chart"}]}`, and the hero does the same. Recommendations from picked artists carry `explanation.kind: "picked"`.

### Your Year extras (web UI routes)

Everything reads the same twelve-month window and listening pile as `GET /api/stats/year`.

- `GET /api/stats/year` now also has `days` (`[{date, plays, minutes}]`, every day with music), `streak` (`{days, start, end}`, the longest run), `top_genres` (`[{genre, plays, share}]`, from the library's artists) and `peak_day.minutes` / `peak_day.top_artist`. A year cached before these arrives without them until the worker rebuilds it.
- `POST /api/stats/year/playlist` `{size: 50 | 100}`: the year's most played songs into this profile's user playlist "Your <year>". Saving again updates the same playlist. `{id, name, track_count, created}`; `400` when nothing was played.
- `GET /api/stats/year/soundtrack`: `{tracks}`, up to three of the year's top songs that are in the library with a file, in the player's queue shape plus `start_ms` and `duration_ms`.
- `GET /api/stats/year/banner`: `{show, year}`, true from Dec 1 to Jan 15 when there is a year to show and this profile hasn't hidden it. The first time it shows, the profile gets one notification. `POST /api/stats/year/banner/dismiss` hides it until next season.

### Update file locations (web UI routes, admins only)

After the music library folder moves, these point the library's file records at where the files are now. Files are never touched, and the media server's own path for a file is never changed. One job runs at a time, in the background.

- `POST /api/library/file-locations/preview`: check every record, write nothing. The finished result (on `/status` as `preview`) has `counts` per outcome (`repoint`, `present`, `not_found`, `ambiguous`, `different_file`, `unverified`, `outside_library`, `partial_match`, `claimed`, `old_root_live`) and `groups`: `[{old_root, new_root, count, examples}]`.
- `POST /api/library/file-locations/apply` `{groups?: [{old_root, new_root}]}`: move the records, every one checked again; `groups` limits it to those folder pairs. An interrupted apply continues in the same run.
- `GET /api/library/file-locations/status`: `{status, action, checked, total, result, preview, error, runs}`. `runs` lists recent runs, manual, from library scans and from folder watching (`kind`: `manual`, `scan`, `watch`), with `id`, `kind`, `status`, `repointed` and `groups`.
- `POST /api/library/file-locations/runs/<id>/undo`: put a run's records back. A record changed since is left as it is. `POST /api/library/file-locations/stop` stops the running job.

### Live folder watching (web UI routes, admins only)

- `GET /api/library/folder-watch`: `{enabled, running, live_available, watching, last_change, last_change_folder, pending, poll_minutes, roots}`. Each root: `{path, kind: "library"|"import", setting: "auto"|"live"|"polling"|"off", mode: "live"|"polling"|"off", reason, note, reachable, poll_minutes, last_change, last_check, changes, pending}`. Times are Unix seconds. `live_available` is false when the `watchdog` package is missing (every folder is then polled).
- `POST /api/library/folder-watch/refresh`: read the settings again now (after a save), then the same answer.

Settings: `library.watch_folders` (default true), `library.watch_poll_minutes` (15), `library.watch_root_modes` (`{folder: mode}`), `library.watch_max_live_folders` (100000). A change reaches open pages as the `library:changed` socket event.

### Library coverage and folder overrides (web UI routes, admins only)

How much of the media server's music SoulSync can read directly, folder by folder. Each folder is sampled (up to 40 real files, opened and size-checked), never scanned whole.

- `GET /api/library/coverage`: the last check, or a new one when there is none. `?refresh=1` always runs a new one. `{server, folders_from: "server"|"inferred", relative_paths, total_tracks, outside_folders, reachable_tracks, reachable_pct, docker, folders}`. Each folder: `{library, server_folder, tracks, local_folder, found_by: "override"|"same"|"learned"|"suffix"|null, status: "covered"|"partly_covered"|"not_library"|"not_mounted", library_folder, library_parts, volume_example (Docker, not mounted), sampled, readable, readable_pct, reasons: {reason: count}, examples: [{server_path, local_path, reason, why}], reachable_tracks}`. Reasons: `ok`, `not_mounted`, `missing`, `permission`, `size`, `unreadable`, `not_a_file`, `not_library`. Navidrome doesn't say which folders it uses, so its folders are worked out from its track paths (`folders_from: "inferred"`); `relative_paths: true` means Report Real Path is off.
- `POST /api/library/coverage/folder` `{server_folder, local_folder?, library?}`: one folder, checked now. With `local_folder` it is checked as if SoulSync saw it there; nothing is saved.
- `POST /api/library/coverage/check-path` `{path}`: `{exists, is_dir, readable, entries, sample, library_folder}` for a typed folder.
- `GET /api/library/path-mappings` `?media_type=music|video`: folder overrides, `[{id, media_type, server_kind, server_prefix, local_prefix, enabled, verified_at, verify}]`.
- `POST /api/library/path-mappings` `{server_prefix, local_prefix, media_type?: "music", server_kind?: "", this_server?: bool}` adds one (the same server folder saves over itself); `PUT /api/library/path-mappings/<id>` changes one; `DELETE` removes it. `this_server: true` limits it to the active media server.
- `POST /api/library/path-mappings/<id>/verify`: samples the files under it and stores `verify` and `verified_at`.

An override renames a folder: "the server's `/data/music` is `/music` here". It never gives SoulSync access to a drive. Every server path SoulSync turns into its own asks the overrides first (the longest matching server folder wins, Windows paths compare without case), then the mounts it learned itself, then the trailing-folder match against its library folders. An override whose folder is missing is skipped, so it never hides a file.

### AcoustID account linking (web UI routes, admins only)

From 1 January 2027 an AcoustID lookup needs the person's own AcoustID key with SoulSync's application key (error 21 without it). These link that key. No answer ever carries a key.

- `GET /api/acoustid/account`: `{has_user_key, source: ""|"linked"|"pasted", state: ""|"pending"|"approved"|"declined"|"expired"|"revoked"|"unknown", code, url, expires, has_app_key, builtin_app_key, can_link, link_unavailable_message, required_from, required_now, needs_link, message}`. `?check=1` first asks AcoustID about a pending link; a declined, expired, revoked or unknown key is discarded.
- `POST /api/acoustid/link` `{return_url?}`: asks AcoustID for a link (`/v2/user/request_link`), stores the user key at once and answers as above with `state: "pending"`, the `code` and the `url` to open. `return_url` (http or https) is where AcoustID sends the person after they approve or decline. Linking needs SoulSync's own application key (built in, or the `ACOUSTID_APP_KEY` env var), as a link only works with the key that asked for it; without one it answers `409` with "Linking with one click arrives in a coming update; paste your key from acoustid.org for now." and `can_link` is false.
- `POST /api/acoustid/unlink`: forgets the user key.

Settings: `acoustid.user_key` (a secret, redacted like the others), `acoustid.api_key` (an application key the install registered itself; SoulSync's own is used when it has one, or the `ACOUSTID_APP_KEY` env var). Every lookup sends the user key in the `Acoustid-User-Key` header.

### Give back to MusicBrainz and AcoustID (web UI routes, admins only)

Settings, Connections, Give back (#2335). Three switches, all off by default: `giveback.acoustid`, `giveback.isrc`, `giveback.releases`. No answer ever carries a token.

- `GET /api/giveback`: `{switches: {acoustid, isrc, releases}, acoustid: {ready, message}, musicbrainz: {available, connected, username, message}, counts: {acoustid: {submitted, present, skipped, rejected}, isrc: {...}}, running, last, last_at}`.
- `PUT /api/giveback/settings` `{acoustid?: bool, isrc?: bool, releases?: bool}`: saves the switches; answers as above. Anything but a bool is `400`.
- `POST /api/giveback/run`: runs a round now in the background (`409` while both sending switches are off).
- `POST /api/giveback/musicbrainz/connect`: `{url}` to open. OAuth2 with PKCE (S256), installed-application redirect `urn:ietf:wg:oauth:2.0:oob`, scopes `submit_isrc profile`. `409` while SoulSync has no registered MusicBrainz application.
- `POST /api/giveback/musicbrainz/code` `{code}`: trades the code MusicBrainz showed for tokens (kept in the encrypted `giveback.musicbrainz_auth`); a link older than an hour must be started again.
- `POST /api/giveback/musicbrainz/disconnect`: revokes the refresh token at MusicBrainz and forgets it.
- `GET /api/giveback/releases`: `{releases: [{id, title, artist, year, source, source_url}]}`, albums the MusicBrainz worker didn't find that have a Spotify, Deezer, Apple Music, Tidal, Qobuz or Bandcamp id. Empty while `giveback.releases` is off.
- `GET /api/giveback/releases/<album_id>/seed`: `{action: "https://musicbrainz.org/release/add", fields: [[name, value], ...], source}`, the release editor seeding fields (`name`, `artist_credit.names.0.*`, `type`, `status`, `events.0.date.*`, `labels.0.name`, `barcode`, `mediums.N.format`, `mediums.N.track.M.{name,number,length,artist_credit.names.0.name}`, `urls.0.{url,link_type}`, `edit_note`). The page POSTs them as a form; the person submits the edit. `404` without a source.

What is sent: AcoustID `POST /v2/submit` (gzipped; `client`, `clientversion`, `duration.N`, `fingerprint.N`, `mbid.N`, `fileformat.N`; the user key in the `Acoustid-User-Key` header), at most 20 per request. MusicBrainz `GET /ws/2/recording/<mbid>?inc=isrcs` then `POST /ws/2/recording/?client=soulsync-<version>` with the `mmd-2.0` `<recording-list>` / `<isrc-list>` XML and a Bearer token, one request a second, User-Agent `SoulSync/<version> ( https://github.com/Nezreka/SoulSync )`. Every outcome is kept in `giveback_submissions` (unique on kind, subject, recording), so nothing is sent twice.

A MusicBrainz application can be supplied with the `MUSICBRAINZ_OAUTH_CLIENT_ID` / `MUSICBRAINZ_OAUTH_CLIENT_SECRET` env vars or the `giveback.musicbrainz_client_id` / `giveback.musicbrainz_client_secret` settings until SoulSync ships its own.

### Paired devices: listen anywhere (web UI and app routes)

Settings > Devices pairs music apps with this server. A device gets its own token, never an API key. The admin routes are `@admin_only`; the device's own routes take the token in `X-Device-Token: sdt_...` (or `Authorization: Bearer sdt_...`) and no cookie. A request may carry a device token or an API key, not both (`400 device_one_credential`).

A device token reaches only routes that opt in. Everything else answers `403 device_not_allowed` before it runs; an unknown or unpaired token gets `401 device_token_invalid`, one that missed its pick window `401 device_pick_expired`, and an unprofiled one on anything but its pick `403 device_pick_first`. A device never acts as an admin, even when its profile is one.

Admin:

- `GET /api/devices/identity`: `{lookup_id, fingerprint, public_key}`. The server fingerprint is the lookup ID's first 12 characters.
- `GET /api/devices`: `{devices: [{id, name, platform, app, kind, profile_id, profile_name, scope, created_at, last_seen_at, last_ip, state, pick_seconds_left}]}`. `state` is `live` or `unprofiled` (waiting for its pick; `pick_seconds_left` says how long). Unpaired and expired ones are left off.
- `PATCH /api/devices/<id>` `{name}`: rename. `DELETE /api/devices/<id>`: unpair now.
- `POST /api/devices/profiles/<profile_id>/revoke`: unpair every device of a profile, `{revoked}`. Sign out everywhere, a new password and turning a profile off do the same; deleting a profile removes its devices.
- `GET /api/devices/codes`: `{codes: [{id, note, created_at, expires_at, seconds_left, wrong_tries}]}`, the codes still waiting. Never the codes themselves.
- `POST /api/devices/codes` `{note?}`: `201 {id, code, code_hash, expires_at, seconds_left, payload, payload_text, warnings}`. `code` is shown this once, as `XXXX-XXXX-XXXX-XXXX` (16 characters of Crockford base32, about 80 bits). It works once, for 10 minutes. `code_hash` is the SHA-256 hex of the code's 16 characters (uppercase, no dashes). `payload` is `{v, lookup_id, fingerprint, addresses, code}` and `payload_text` its compact JSON, what the QR carries. `409` when 20 codes already wait.
- `DELETE /api/devices/codes/<id>`: cancel a waiting code.
- `GET /api/devices/server`: `{lookup_id, fingerprint, public_key, addresses: [{kind, url}], remote_url, custom_urls, warnings, probe, guidance}`. `addresses` lists `local` ones first, then `remote`, then `custom`. `warnings` says so for a plain http address outside the home network.
- `PUT /api/devices/addresses` `{remote_url, custom_urls}`: save the remote address (blank clears it) and up to 5 extra ones. `400` with the reason for an address that won't work. Answers like `GET /api/devices/server`.
- `POST /api/devices/reachability`: a new one-time probe, and the server fetching its own remote address with it. `{result: {ok, code, words} | null, probe: {nonce, path, url, seconds_left, hits}, guidance}`. `result` is null with no remote address. `code` is `ok`, `not_this_server`, `blocked`, `certificate`, `timeout`, `dns` or `unreachable`.
- `GET /api/devices/reachability`: `{probe}`, the newest probe and its `hits: [{ip, at, via, from_home}]` (`via` is `self` for the server's own fetch, `outside` for anything else, like a phone on mobile data).

Open, no sign-in (these pass the login, launch PIN and profile gates):

- `POST /api/devices/redeem` `{code, name, platform?, app?}`: `201 {token, device_id, pick_seconds, server: {lookup_id, fingerprint, public_key}}`. Burns the code; the token is shown this once and starts unprofiled. Every bad code answers `404 unknown_or_expired_code` the same way. Wrong codes from one address are held to 10 a minute (`429`, `Retry-After`), and 5 wrong tries on one code lock it. Plain http only works from this machine or the home network; anything else gets `403 https_required`.
- `GET /api/devices/reachability/<nonce>`: the one-time probe URL. `{soulsync_probe: <nonce>}` (a small page for a browser) while the probe lives, else `404`.

The device itself:

- `GET /api/devices/me`: `{device, server}`: its name, `state`, profile and how long it has left to pick.
- `GET /api/devices/me/profiles`: `{profiles: [{id, name, avatar_color, avatar_url, needs}]}`, the profiles it may pick. `needs` is `password` in login mode, `pin` for a profile with a PIN, else null. A music app only sees profiles with **Use music apps** (`music.apps`). `409` once it has picked.
- `POST /api/devices/me/profile` `{profile_id, pin?, password?}`: the pick, once. The same checks as `POST /api/profiles/select` (wrong tries throttled the same way); a profile with a PIN always asks for it here. `404` for a profile that isn't on its list, `409` once picked or past its 15 minutes.

Import Plex users (#2319) is Settings > People > Import from Plex, admin only. `GET /api/profiles/plex-import` lists everyone the Plex server is shared with (friends and home users who can see this server): `{users: [{id, username, title, name, email, thumb, home, managed, profile, match}], signin_enabled, signup_template_id}`. `match` is `linked` for the profile already linked to that Plex account, `home` or `name` for one that only looks like them, else null. `POST /api/profiles/plex-import` takes `{users: [{id, profile_id?}], template_id?}`: each `id` gets a new profile linked to the Plex account on that template (the sign-up template when left out), or with `profile_id` that profile gets the link (never an admin, never one linked to another Plex account). Users are read from Plex again, so nothing but the ids is taken from the request. It answers `{results: [{plex_id, name, status, profile_id, message}]}`, `status` one of `created`, `linked`, `exists`, `skipped`, `failed`; importing again answers `exists`. `503` when Plex can't be reached, `404` for a missing template. Sign in with Plex then lands on that profile, and the Seerr API lists profiles linked to Plex with `userType` 1 and their `plexUsername`.

Person invites are the same as Settings > People > Invites: `GET/POST /api/profiles/invites` (admin, or an admin API key). `POST` takes `{preset: {template_id}, note?, expires_hours?}`; a new one lasts a week (168 hours, 1 to 720) unless `expires_hours` says otherwise, works once, and whoever opens it makes their own profile following that template.

### Music apps: the Subsonic / OpenSubsonic API (`/rest`)

SoulSync answers the Subsonic API (version 1.16.1) with the OpenSubsonic additions at `/rest/<method>` and `/rest/<method>.view`, GET or POST (form fields count like query ones). `f=json`, `f=jsonp&callback=<name>` or XML (the default). Every answer is HTTP 200 with `subsonic-response`: `{status, version, type: "soulsync", serverVersion, openSubsonic: true}`, and on a refusal `error: {code, message}`.

Signing in is always a paired music app, never an API key and never a profile's own password:

- `apiKey=<device token>` (OpenSubsonic `apiKeyAuthentication`), on its own.
- `u=<profile name>` with `t=md5(app password + s)` and `s=<salt>`, or `p=<app password>` / `p=enc:<hex>`.

Error codes: `10` a required parameter is missing, `40` wrong username or password (also after 10 wrong tries a minute from one address), `43` an API key with a username or password, `44` an unknown or unpaired API key, `50` not allowed (no profile picked yet, the profile lost **Use music apps**, or something a music app may not do), `70` not found (also a song this profile can't play, or a file SoulSync can't read with the media server switch off), `0` anything else. A device token in `X-Device-Token` works on `/rest` too, with the device gate's JSON refusals. `getOpenSubsonicExtensions` answers without signing in: `apiKeyAuthentication`, `formPost`, `songLyrics`, `transcodeOffset`.

Ids are typed: `ar-<id>` artist, `al-<id>` album, `tr-<id>` song (a bare number is a song), `pl-<id>` playlist, `mx-<n>` a Daily Mix. One music folder, id `1`.

Methods: `ping`, `getLicense`, `getOpenSubsonicExtensions`, `tokenInfo`, `getUser` (its own profile only), `getScanStatus`, `getMusicFolders`, `getIndexes`, `getArtists`, `getArtist`, `getAlbum`, `getSong`, `getMusicDirectory`, `getGenres`, `getAlbumList` / `getAlbumList2` (`random`, `newest`, `highest`, `frequent`, `recent`, `alphabeticalByName`, `alphabeticalByArtist`, `starred`, `byYear`, `byGenre`; `size` up to 500), `getRandomSongs`, `getSongsByGenre`, `search2` / `search3` (an empty query pages through the whole library), `getStarred` / `getStarred2`, `star` / `unstar` (`id`, `albumId`, `artistId`, each repeatable), `setRating`, `scrobble` (`submission=true` records the play, `false` is now playing), `getNowPlaying` (this profile's), `getPlaylists`, `getPlaylist`, `createPlaylist`, `updatePlaylist`, `deletePlaylist`, `getCoverArt` (`size`), `getLyrics`, `getLyricsBySongId`, `stream`, `download`, `getSimilarSongs` / `getSimilarSongs2`, `getTopSongs`, `getArtistInfo` / `getArtistInfo2`, `getAlbumInfo` / `getAlbumInfo2`, `getPlayQueue`, `savePlayQueue`. `getBookmarks`, `getPodcasts`, `getNewestPodcasts`, `getInternetRadioStations` and `getShares` answer empty; `startScan` answers `50`.

What a request sees is its profile's: its library (own-library rules), less explicit music when the profile hides it, its playlists, stars, ratings, play queue (one per app) and plays. A starred song is a Liked Song. Starring a song the profile has no file for adds it to the wishlist (may download) or makes a request (may only ask), else `50`. Mirrored playlists, Liked Songs and Daily Mixes are `readonly: true`.

`stream`: the file with `Range` / `206`, unless `format` (`mp3`, `opus`) or `maxBitRate` asks for less, then converted by ffmpeg on the fly (`timeOffset` seconds to start later, `estimateContentLength=true` for a `Content-Length`). `format=raw` always sends the file. At most 4 conversions run at once; past that, and without ffmpeg, the file goes out as it is. A song whose file SoulSync can't read is `70`, unless its media server's switch is on: then SoulSync proxies that server's stream, the response carries `soulsyncStreamVia: <server>` and the song in every list carries the field `soulsyncStreamVia`.

Admin (Settings > Devices > Set up a music app):

- `GET /api/devices/music-apps`: `{profiles: [{id, name}], addresses, bridge: [{id, name, on, active}], transcode_format, transcode_slots, slots_range: [min, max], formats, ffmpeg}`. `profiles` are the ones with Use music apps.
- `POST /api/devices/music-apps` `{profile_id, name?}`: `201 {device_id, name, server, addresses, username, password, api_key, qr_text}`. A paired device for that profile with its own app password (kept encrypted) and API key, both shown this once. Unpair it like any device; its password goes with it. `400` for a profile without Use music apps.
- `PUT /api/devices/music-apps/settings` `{bridge?: {plex|jellyfin|navidrome: bool}, transcode_format?: "mp3"|"opus", transcode_slots?: 1-16}`: answers like the GET. `transcode_slots` is how many songs convert at once (4 by default); a stream past that waits up to 20 s for a slot, in arrival order, then gets Subsonic error 0 ("busy converting, try again"), never the original file. Each switch is "Stream files SoulSync can't read through <server>", off by default.

---

## MCP for AI clients

SoulSync can act as an MCP server, so an AI app like Claude can search for music, check what you already own, and download things for you.

### Turn it on

Go to **Settings > System > API > MCP for AI clients** and tick **Answer MCP requests at /mcp**. While it's off, `/mcp` answers `404`.

### Connect a client

Add `http://<host>:8008/mcp` as a remote (streamable HTTP) MCP server and send a SoulSync API key as a Bearer token. For Claude Code:

```bash
claude mcp add --transport http soulsync http://localhost:8008/mcp --header "Authorization: Bearer sk_..."
```

Both protocol styles work: `2026-07-28` (no handshake) and `2025-03-26` through `2025-11-25` (initialize first). Requests from a browser page on another site are refused, so a key that leaks into a web page can't be used from there.

### What a key can do

Every tool call runs as the matching `/api/v1` request with your key, so the usual rules apply: the same scope checks, the same profile, and the same 60 requests a minute.

- An **admin** key gets all 35 tools.
- A **read** key only gets the tools that look things up. A write tool still answers `403` if one is ever called with a read key.
- Settings, API keys, profiles, cancel-all and blocklist clears are never tools, whatever the key.

### Tools

| Group | Tools |
|-------|-------|
| Status | `get_server_status`, `get_recent_activity`, `get_library_stats` |
| Your library | `search_library_artists`, `get_library_artist`, `list_artist_albums`, `search_library_albums`, `get_album_tracks`, `search_library_tracks`, `recently_added`, `recently_played` |
| Queues | `list_wishlist`, `list_watchlist`, `list_downloads`, `list_playlists`, `get_request_status` |
| Discovery | `recent_releases`, `similar_artists` |
| Video | `video_search`, `video_library`, `video_wishlist`, `video_downloads`, `video_calendar` |
| Getting music | `search_music`, `find_album`, `request_album`, `request_track`, `process_wishlist`, `remove_from_wishlist`, `watch_artist`, `unwatch_artist`, `scan_watchlist`, `cancel_download` |
| Getting video | `video_wishlist_add`, `video_follow` |

To get a whole album, the client calls `find_album` with "Artist - Album" and then `request_album` with the id it returns. Every track goes on the wishlist and tracks you already own are skipped. For one track, `request_track` takes "Artist - Title".

---

## Lidarr compatible API

Some request apps only know how to talk to Lidarr. SoulSync can answer as Lidarr, so those apps send their album requests to SoulSync instead. Built for **Singarr** and **MusicSeerr 1.x**. MusicSeerr 2.0 and newer dropped Lidarr support, so it can't connect.

### Turn it on

Go to **Settings > System > API > Lidarr compatible API**:

1. Tick **Answer as Lidarr at /lidarr**. While it's off, every `/lidarr` route answers `404`.
2. Pick **Requests go to profile**. Requested albums land on that profile's wishlist.

### Connect the app

In the request app, set the Lidarr URL to `http://<host>:8008/lidarr` and paste a SoulSync **admin** API key as the Lidarr API key. A read key gets `401`. The app talks to `/lidarr/api/v1` like it would to a real Lidarr.

### What happens

- Artist and album lookups come from MusicBrainz.
- When the app asks for an album to be searched (on add, or with an album or artist search), every track of that album goes on the profile's wishlist and downloads like any other wishlist item. Tracks you already own are skipped.
- If the profile can't download on its own, every request waits for approval on the **Requests** page first.
- The app's "do we have it" view checks your SoulSync library.
- Deleting an artist or album in the app only removes it from SoulSync's Lidarr list. Files on disk are never touched.

### Supported routes

All under `/lidarr/api/v1`: `system/status`, `qualityprofile`, `metadataprofile`, `rootfolder`, `tag`, `artist` (list, lookup, add, edit, editor, delete), `album` (list, lookup, add, edit, monitor, delete), `track`, `trackfile`, `command` (`AlbumSearch`, `ArtistSearch`, `RefreshArtist`; other commands are accepted and do nothing), `queue`, `history` and `wanted/missing`.

---

## Seerr compatible API

Apps built for Seerr (Jellyseerr, Overseerr) can use SoulSync's video side instead. They browse and search through SoulSync's TMDB data, see what's already in your library, and their movie and show requests land on SoulSync's video wishlist. Built for **Litefin** and its Jellyfin plugin.

### Turn it on

Go to **Settings > System > API > Seerr compatible API**:

1. Tick **Answer as Seerr at /seerr**. While it's off, every `/seerr` route answers `404`.
2. Pick **Jellyfin users with no profile request as**.

### Connect the app

In the app, set the Seerr URL to `http://<host>:8008/seerr` and paste a SoulSync **admin** API key as the Seerr API key. For Litefin that's the **Seerr Integration** on the Litefin plugin's page in your Jellyfin dashboard. A read key gets `403`. The app talks to `/seerr/api/v1` like it would to a real Seerr.

### Who asks

Each Jellyfin user requests as the SoulSync profile that picked that Jellyfin user under **Settings > Connections**, else the profile with the same name, else the profile picked above. A profile without the video side can't request, and a kids profile can't request a title over its rating cap.

### What happens

- A profile that can download gets it straight away: a movie goes on its video wishlist, and for a show every aired episode of the seasons asked for that isn't in the library yet. Asking for the newest season also follows the show, so its next episodes come too.
- A profile that can't download files a video request that waits on the **Requests** page. SoulSync requests pick a whole-show policy, so when the seasons asked for aren't all, the first or the newest, the request asks for every season and the note says which ones were picked. The admin can change it when approving.
- Request limits apply like they do in SoulSync.
- Cards show **Available** when it's in your library, **Partially available** for a show with episodes missing, **Processing** when it's on a wishlist, followed, approved or downloading, and **Pending** while a request waits. It's the same status the video pages show on their posters.
- Asking for something already on its way answers `409`, like Seerr does for a duplicate.
- Downloads use your normal quality profiles, and a 4K request uses the profile's 4K quality profile. There are no Radarr or Sonarr servers to pick.

### Seerr in front, SoulSync in place of Radarr and Sonarr

Keep Seerr as the place people ask, and let SoulSync fetch instead of Radarr and Sonarr (#2324). Seerr needs no Radarr or Sonarr for this.

1. In Seerr, go to **Settings > Notifications > Webhook**. Set the webhook URL to `http://<host>:8008/seerr/api/v1/webhook`, the authorization header to `Bearer <admin API key>` (a SoulSync admin key), keep the default JSON payload and tick the request events (pending, auto-approved, approved, declined).
2. Optional, for the full integration: in SoulSync, under **Settings > System > API > Seerr compatible API**, fill in **Your Seerr URL** (for example `http://seerr:5055`) and the **Seerr API key** (Seerr's Settings > General). **Test** checks both. The key is stored encrypted and never sent back to the browser.

What SoulSync does with each request:

- Each Seerr request id is handled once. The same webhook sent again, and the events Seerr sends because of what SoulSync told it, change nothing.
- With the Seerr URL set, SoulSync reads the full request from Seerr (`GET /api/v1/request/{id}`): who asked, the seasons asked for, and 4K. Without it, it uses what the webhook carries (the requester's name and the requested seasons).
- Who asked is matched to a profile by their Plex account (Sign in with Plex and profiles imported from Plex are linked to it), then their Jellyfin user, then their Plex name, then a profile with their name. Nobody matched acts as `X-Api-User`, then the fallback profile, or is refused when unknown users are.
- The request then follows that profile's rules, like any other: movies and TV separately, Download right away or Ask first, the rating limit, the request limit and the quality. A 4K request gets the profile's 4K quality profile, or is refused when it has none.
- With the Seerr URL set, Seerr hears back: a request SoulSync takes right away is approved in Seerr (`POST /api/v1/request/{id}/approve`), one its rules refuse is declined (`/decline`), and one waiting on SoulSync's **Requests** page is approved or declined there when you decide. A request declined in Seerr withdraws the one still waiting in SoulSync. When the files land, Seerr's media is marked available (`POST /api/v1/media/{id}/available`), or partially available while seasons asked for, or the rest of the show, are still missing. 4K requests set Seerr's 4K status. Each of these is sent once.

`POST /seerr/api/v1/request` takes the same Seerr fields when a webhook template posts there: `seerrRequestId` (or `request_id`), `notification_type` and `requestedBy` (`{plexId, plexUsername, username, jellyfinUserId}`). A body without them is a plain request, as before. Both answer `201` with the request when something was filed, `200` with `outcome` (`exists`, `refused`) or `duplicate` / `ignored` otherwise.

### Supported routes

All under `/seerr/api/v1`: `status`, `auth/me`, `user`, `discover/trending`, `discover/movies` (plus `upcoming`, `studio`, `genre`), `discover/tv` (plus `upcoming`, `network`, `genre`), `discover/genreslider`, `search`, `movie` and `tv` (details, `similar`, `recommendations`), `person` (with `combined_credits`), `collection`, `request` (list, create, cancel), `webhook` (Seerr's webhook agent) and `media`. Seerr's watch-later list, outside ratings and Radarr/Sonarr service lists answer empty, since SoulSync doesn't keep them.

---

## Player home layout

The player's home page is laid out per profile and per screen, so a TV and a phone on one profile can each look different. Every player app (web, phone, TV) reads the same layout and draws it the same way. The contract lives in `core/player/home_layout.py`.

These routes use the signed-in session (the player's own), not an API key. Each profile only ever sees and changes its own.

### The layout

```json
{
  "version": 1,
  "billboard": true,
  "rows": [
    {"id": "continue", "style": "wide", "on": true},
    {"id": "top-movies", "style": "ranked", "on": true},
    {"id": "genre:movie:Horror", "style": "large", "on": true}
  ]
}
```

- `billboard`: the big rotating banner of what's new at the top.
- `rows`: drawn top to bottom. Skip a row that's `on: false`, one your app doesn't know, or one with nothing to show.
- `id`: a row from the catalog, or `genre:<movie|show>:<genre name>` for a genre row someone added.
- Library rows: `lib-recent:<library id>` ("Recently added in Movies 4K", posters, newest first, that library's titles only) and `lib-continue:<library id>` ("Continue in Movies 4K", what the profile started whose title is in that library) for each movie and TV library the profile can see, and `home-memories` ("On this day", the profile's home videos filmed on this day in earlier years, from `GET /api/player/home/memories`; skip it when there are none). A library's rows come at the end, off; "On this day" comes in on, after continue, once there is a home library. A row for a library that's gone or hidden from the profile is dropped. Titles use the library's name as it is now.
- One card per title: the rows built from every title (recently added, top 10, because you have, top rated, rediscover, genre rows) show a title once when it is in two libraries (same tmdb id, else same title and year). The card opens the copy in the main library and wears a small 4K chip when another copy is in a 4K library (`uhd` on the library). Library rows and library pages show their own copy.
- `style`: one of the four below. A row only takes the styles the catalog lists for it.

| Style | Draw it as |
|---|---|
| `standard` | the row's own cards: posters, squares or stills |
| `large` | the same cards, bigger |
| `wide` | 16:9 stills (a title's backdrop) |
| `ranked` | the first ten as posters, numbered 1 to 10 |

Every row on home is from the profile's own library.

### Routes

- `GET /api/player/home-layout?device=<id>`: `{layout, scope, catalog}`. `scope` is `device` when this screen has its own layout, `all` when it uses the profile's shared one, and `null` for the default. The layout is always clean: rows the server no longer has are dropped, a style a row can't take goes back to its default, and rows added since it was saved come back at the end, turned off.
- `PUT /api/player/home-layout` with `{layout, device}`: saves it for this screen, or for every screen with `device: "*"`. Add `from_device: "<id>"` with `"*"` to make this screen follow the shared layout from then on. A layout that isn't this shape answers `400`.
- `DELETE /api/player/home-layout?device=<id>`: this screen goes back to the shared layout. Without `device`, every screen of the profile goes back to the default.

`device` is an id your app makes once and keeps: 8 to 64 letters, numbers, `-` or `_`.

The Movies, TV, Music and YouTube pages have the same contract, one layout per page:

- `GET /api/player/page-layout/<page>?device=<id>`: `{layout, scope, inherited, catalog}`. `<page>` is `movies`, `tv`, `music` or `youtube`, or one library's page `movies:lib:<id>` / `tv:lib:<id>`. `layout.rows` is `[{id, style, on}]` in order. `catalog.rows` lists every row the page can show with its `label`, `hint`, the `styles` it takes and whether it is `on` by default; the rows that are off by default are the extra ones a person can switch on. `catalog.genre_row` is the pattern for a genre row (`genre:movie:<name>`, `genre:show:<name>`), or `null` where the page has none. A library's page with no layout of its own follows its page's (`inherited: true`).
- `PUT /api/player/page-layout/<page>` with `{layout, device}` and `DELETE /api/player/page-layout/<page>?device=<id>`: as for home. A row that isn't the page's, or a style that isn't one, is `400`.
- Draw the rows that are on, in order, in their style. Skip a row you don't know or have nothing to show for.
- `GET /api/player/music/listening`: the Music page's listening rows, `{recent, heavy_rotation, top_albums, top_tracks, all_time_artists}`. Plays that matched a library track, as that library's albums (`album_id`), artists (`artist_id`) and tracks (`track_id`), each with `image_url`. Only what the profile may see.

Libraries:

- `GET /api/player/libraries`: the video libraries this profile can see, `[{id, kind, name, own_page, main, in_guide}]`. `kind` is `movie`, `show` or `home` (home videos). A library with `own_page` gets its own entry and page in the player; the rest are part of Movies / TV. Every home library has its own page. `in_guide` says whether the guide schedules its titles (home libraries start off). A library an admin hid from the profile is never sent, and its titles are refused everywhere the video API answers.
- `GET /api/player/video-catalog` also carries `libraries`, and every movie and show row ends with `library_id` (read it by name from `fields`). A title in no library folder is in its kind's main library.
- `GET /api/player/admin/libraries` (admin): `{libraries: [{id, kind, name, main, own_page, titles, hidden_from}], profiles}`.
- `PUT /api/player/admin/libraries/<id>` (admin) with `{own_page?: bool, hidden_from?: [profile id]}`. The main library can't have a second page, and an admin profile is dropped from `hidden_from` (admins see every library).

Home videos (clips from a camera or a phone, never looked up on TMDB; `scrap/HOME_VIDEOS_CONTRACT.md`). Every route takes the player session or a paired device's token, and answers only for home libraries the profile can see (a hidden one is `404`, like a clip that isn't there):

- `GET /api/player/home/overview?library=<id>`: `{library: {id, name, in_guide}, counts: {videos, first_year, last_year}, on_this_day: [Video], albums: [{key, name, path, count, first, last, covers: [video id]}], months: [{month: "2026-11", count}], sources, can_edit, scanning}`. No `library` is the first home library.
- `GET /api/player/home/memories?date=YYYY-MM-DD`: `{videos: [Video + library]}`, the clips filmed on this month and day in earlier years across every home library the profile can see, newest first, each with the home library it is in. `date` is the viewer's day (the server's when left out). No home library, or nothing that day: an empty list.
- `GET /api/player/home/videos?library=<id>&month=YYYY-MM | album=<key> | q=<text>&offset=&limit=`: `{videos: [Video], total}`. The timeline is newest first, an album oldest first; `limit` is at most 200. A missing clip stays in its album (`missing: true`) and leaves the timeline.
- `GET /api/player/home/video/<id>`: a Video plus `{frames: [{at_s, url}], file_name, auto_title, auto_filmed_at, filmed_source, notes, thumb_at_s, edited, library, can_edit}`.
- `PUT /api/player/home/video/<id>` with any of `{title, filmed_at, thumb_at_s, notes}`; `null` puts a field back to automatic. Admins, or everyone who can see the clip when player setup's `home_editors` is `everyone`. Edits survive every rescan.
- `GET /api/player/home/thumb/<id>`: the clip's picture (jpeg). `GET /api/player/home/frame/<id>?at=<seconds>`: a frame to pick from.
- Admin: `GET /api/player/home/sources` (`{server, libraries: [{title, count, on, root, videos}], folders: [{path, root, videos}], scan}`), `PUT /api/player/home/sources` with `{home_more?: [library name], folders?: [path]}` (saved, then what's new is read at once), `GET` / `POST /api/player/home/scan` (`{root?}`), `POST /api/player/home/folder-check` with `{path}`.
- A Video is `{id, title, filmed_at, duration_s, width, height, album, album_key, thumb_url, progress: {position_s, watched}, missing}`. It plays through the playback routes as item type `home`, and downloads as `home` or a whole album as `home_album`.

The sidebar's order is two of the profile's player settings (`GET` / `PUT /api/player/settings`): `sidebar_order` and `sidebar_hidden`, each a list of places, a page (`/player/movies`) or a library with its own page (`lib:<id>`). Places stay in their section; one the order doesn't name keeps its usual spot after the ones it does. Home can't be hidden.

### Playback

How the player, and paired apps, play something. Both routes take the signed-in session or a paired device's token (`X-Device-Token`), and both go through one check (`core/player/playable.py`):

1. A profile. Signed out, or a device that hasn't picked a profile, answers `401`.
2. The section: the profile's permission for that kind of item. A paired app only plays what its kind of app is for; a music app plays tracks.
3. The item: in the profile's library and inside its limits (a kids rating cap, hidden explicit music), with a file to play.

`item_type` is `track` (library v2 id), `movie` or `episode` (library id), `youtube` (a downloaded video's id) or `audiobook` (its library key). `?part=` picks a movie or episode file version (a media file id), or an audiobook's part (0 based).

- `GET /api/player/playable/<item_type>/<item_id>`: `{mode, url, mime, duration, parts, chapters, verdict}`, how to play it, never where the file is. `parts` and `chapters` are an audiobook's timeline, read from its files: each part's `start` and `duration` in seconds on the book's clock, and each chapter's `start` and `title`. `verdict` is a video file's direct-play verdict (`yes`, `maybe` or `no`, with `reasons`).
- `GET /api/player/stream/<item_type>/<item_id>`: the bytes, with `Range` support (`206`), so a player can seek. A file SoulSync can't reach is relayed from your media server.

Refusals are `{error, message}`: `403 restricted` for something over the profile's limits, `404` for something not in the library or without a file. Podcast episodes don't come here: they stream from the feed's host through the podcast side's audio proxy.

#### Converting video for a browser

A video a browser can't play as it is (an MKV, HEVC, AC3 or DTS sound) is converted by ffmpeg as it streams. Only what has to change is converted: a stream a browser already plays is copied untouched, so a common H.264 file with AC3 sound only has its sound converted. Video is converted to H.264 (on the GPU when there's one that works: NVIDIA, Intel Quick Sync, VA-API or Apple; the CPU otherwise), at 1080p at most, and HDR is tone mapped so it doesn't come out washed out. Sound becomes stereo AAC.

- `playable` for a movie, episode or YouTube video adds, when it's converted, `transcode: {video, audio, reasons, quality, tonemap, encoder}` (`video` and `audio` are `copy` or what they become; `reasons` reads like "AC3 audio to AAC"), and `url` points at the converted stream. A file that may play (`verdict: maybe`) keeps its direct `url` and adds `fallback_url`, the converted stream to switch to if the browser can't play it. `?q=1080p|720p|480p` asks for a smaller picture, converted. `transcode_unavailable` says why a file that needs converting can't be (no ffmpeg).
- `GET /api/player/transcode/<item_type>/<item_id>?q=&part=&t=`: the converted stream, fragmented MP4, from `t` seconds. It can't seek by `Range`: to seek, start a new one at the new `t`. A few run at once; past that it answers `503 busy`.
- `GET /api/player/transcode/<item_type>/<item_id>/start?t=`: where a stream asked to start at `t` really starts, `{start, seek}`. Copied video can only start at a keyframe, so request `t=seek` and count the stream's time from `start`.

#### Downloads

Phones, tablets and the web player keep downloads; the TV app sends things to another of the profile's devices. Every route is under `/api/player/offline/`, takes the session or a paired device's token, and only ever sees the caller's own devices. A profile the admin switched downloads off for gets `403 downloads_disabled` for anything new (admins always may). The routes:

- `GET plan?type=&id=&device=`: a movie, episode, `season` (`<show_id>:<n>`), show, youtube video, track, album, artist, playlist (`user:<id>`, `mirror:<id>`, `discover:<key>`), audiobook or podcast (`count` newest, 5 by default) as `{kind, title, subtitle, artwork, items, qualities}`. Each quality has a `total_bytes` and `per_item_bytes`; original is the real size, the others an estimate. Never above the source.
- `POST prepare` with `{item_type, item_id, quality}`: `{id, status, progress, url, size_bytes, error, filename}`. The original is `ready` at once; a smaller copy is made by the server once per file and quality (`queued`, `converting`, `ready`), kept in the player cache. `GET prepare/<id>` polls it. `GET file/<id>` is the file with `Range`, `ETag` and a clean name.
- `POST devices/report` with the device's whole list; `GET devices` lists the profile's devices with their items, smart rules and pending adds/removes.
- `POST wishes` with `{device_id, action: add|remove, type, id, quality}` and `POST rules` with `{device_id, type: show|playlist|podcast, id, quality, keep_next, delete_watched, follow}` (`DELETE rules/<rule_id>`). `GET devices/<device_id>/wanted` is what that device should add and remove now.
- `POST sync` with `{device_id, events}`: positions, watched marks and plays made offline. A position only lands when it's newer than the server's; a play counts once.
- `GET settings` (anyone) and `PUT settings` (admin): `allow_conversion_for_members`, `max_conversions`, `cache_gb`.

#### Playlists (music)

- `GET /api/player/playlists`: this profile's playlists as `{yours, mirrored}`. Yours (Liked Songs first) are the ones made in SoulSync; mirrored ones follow Spotify, Deezer and the rest. Request lists stay out. Each is `{id, name, description, kind, liked, source, owner, track_count, image_url, cover_tiles, updated_at}`.
- `GET /api/player/playlists/<id>`: one of them with `songs`, each `{position, title, artist, album, album_id, artist_id, duration_ms, image_url, added_at, track_id}`. `track_id` is the library track it plays (the match identify stamped, else the library's own match by name, the same one Subsonic apps get), or null when this profile can't play it (not in its library, or explicit on a profile that hides it). Also `duration_ms` and `playable_count`.
- Changing your playlists uses the playlist routes the music side already has (`/api/user-playlists`, `/api/mirrored-playlists/<id>/custom-name`, `DELETE /api/mirrored-playlists/<id>`).

#### Discovery playlists

- `GET /api/player/discover-playlists`: what Discover made for this profile, as playlists in `sections`, each `{id, label, playlists}`, in this order (an empty one is left out): `made` "Made for you" (Flow, the daily mixes, On Repeat, Repeat Rewind and blends, the mood mixes), `fresh` "New for you" (Fresh Tape, The Archives, Your Listening Mix, the season's mix), `picks` "Picks" (Hidden Gems, Popular Picks, Discovery Shuffle and the other stored picks, like Time Machine decades), `listenbrainz` "ListenBrainz" (its playlists and Last.fm radio, once cached). Each is `{key, name, blurb, section, owned, track_count, cover_tiles, updated_at}`; `owned` is true when every song is already in the library. The list only reads what's stored: nothing is built or fetched for it. Flow is made when it's opened, so its `track_count` is null. On Repeat and the blends show once Discover (or the player) has made them recently. Artists the profile blocked are left out, the same as on Discover. All three need Discover (`music.discover`), the same as the manager's Discover page: a profile with it off gets `403`.
- `GET /api/player/discover-playlists/<key>`: one of them with `songs`, each `{position, title, artist, album, duration_ms, image_url, track_id, album_id, artist_id, unplayable, wanted}`, plus `playable_count` and `hides_explicit`. Songs match the library the same way playlist songs do (the library track a mix carries, else by title and artist); `track_id` is the track it plays, or null with `unplayable` saying why (`missing` or `explicit`). `wanted` is true when the song is already on this profile's wishlist. An unknown key is `404 {error: "not_found"}`.
- `POST /api/player/discover-playlists/<key>/get` with `{positions?: [int]}`: puts the songs you don't have (`unplayable: "missing"`; all of them, or just those positions) on this profile's wishlist, through the same add as the download dialog's Add to Wishlist. Answers `{added, already, skipped}`: `already` were on the wishlist, `skipped` had nothing to key a wishlist row by or were refused. A profile that can't request music gets the usual `403`.

#### An artist's popular songs

- `GET /api/player/music/artist/<artist_id>/top?limit=10`: the artist's most played songs this profile can play (play count, then newest), their own albums and the songs they're credited on. A song on two releases shows once, its most played copy. `[{track_id, title, artist, duration_ms, album_id, album, plays, explicit}]`.

#### Lyrics

- `GET /api/player/lyrics/track/<track_id>`: a library track's lyrics, `{success, synced, plain, source}`, the same answer as `POST /api/lyrics/fetch` (the `.lrc` beside the file and the file's own tags first, then LRCLib). Asked by track id: the server finds the file itself, so a client never sends a path. A track this profile can't play is refused the same way playback refuses it.

#### Player settings

- `GET /api/player/settings`: this profile's player settings and what each does: `share_activity` (others see what you play on Watching now; default on), `autoplay_next` (the next episode plays on its own), `still_watching_after` (episodes in a row nobody touched before asking; 0 never asks). Kept in player.db, the same on every screen of the profile.
- `PUT /api/player/settings` with `{key: value}`: all saved or none (an unknown key or a bad value is a 400). Turning `share_activity` off takes the profile off Watching now at once.

#### Notifications

What's new for this profile (new episodes of shows it watches, well-rated films, watchlist titles that arrived, requests, season premieres, invites, admin messages, new albums, memories). Made by the **Player Notifications** automation from what changed since its last run, and by event hooks (a request answered, a party scheduled, a Server Activity message). Hidden libraries and rating limits apply. A device token with playback scope may call all of these.

- `GET /api/player/notifications?since=<id>&limit=50`: `{notifications: [{id, kind, title, body, image, link, created_at, read, group_key}], unread, latest_id}`, newest first. `since` returns only ids above it: a device keeps `latest_id` and asks from there. `link` is a `/player/...` path; `group_key` is stable for one subject (a later "3 new episodes of X" replaces "New episode of X" under a new id, so a phone can replace its notification by that tag). `limit` up to 200.
- `POST /api/player/notifications/read` with `{ids: [..]}` or `{all: true}`: marks them read; `{marked}`.
- `DELETE /api/player/notifications/<id>`: dismisses one of yours (404 for anyone else's).
- `GET /api/player/notifications/prefs`: `{prefs, kinds}`. `prefs` is `{kinds: {<kind>: bool}, featured_not_in_library, memories_time, quiet_hours, quiet_start, quiet_end, digest, digest_time, timezone}` (times are `HH:MM` on the profile's clock; `timezone` an IANA name, empty for the server's). `kinds` is `[{id, label, help, default, locked?, device?}]`: `admin_message` is `locked` (always on), `download_ready` has `device: "phone"` (each phone can also turn it off for itself).
- `PUT /api/player/notifications/prefs` with any of those keys: `kinds` merges (`{kinds: {new_music: true}}` leaves the rest). An unknown key, a bad time, an unknown zone or turning off `admin_message` is a 400 and nothing is saved. Quiet hours hold a notification until they end; the digest holds them for one `digest` notification a day. A held one gets its id when it goes out, so `since` never skips it.
- `POST /api/player/notifications/test`: a test notification to the caller right away (quiet hours and the digest don't hold it). Once every 5 seconds; 429 sooner.

#### Watching now

- `POST /api/player/presence` with `{session, item_type, item_id, position, duration, playing, live, channel?, meta?}`: a screen is playing something. Sent every 15 seconds and on a start, pause or change; forgotten 75 seconds after the last one. Kept in memory only. A profile that doesn't share gets `{shared: false}` and nothing is kept.
- `DELETE /api/player/presence?session=`: the screen stopped.
- `GET /api/player/presence`: what's playing right now, `{sessions: [{profile, item_type, item_id, position, duration, playing, live, channel, meta, started, seen, you}], sharing}`. Your own screens come first with `you: true` (kept only while you share); then others, only profiles that share and only what this profile may see (its media access and its rating cap).
- `GET /api/player/presence/together`: titles you and others (that share) both started in the last two months, your place and theirs. A show counts as one title, whichever episode each of you is on.

#### Comments and marks

- `GET /api/player/notes/<item_type>/<item_id>`: a title's notes: comments (no time; newest first) then marks (a moment in seconds; in time order). Yours, and what others shared. `item_type` is movie, show, episode, track, album, audiobook, youtube or podcast_episode. A title this profile may not see (its media access, its rating cap) is a 404.
- `POST /api/player/notes/<item_type>/<item_id>` with `{body}` posts a comment (always shared, can't be empty), or with `{at, body?, shared?}` keeps a mark (a note is optional; yours alone unless `shared`). Up to 1000 characters.
- `PATCH /api/player/note/<id>` with `{body?, shared?}`: your own note. `DELETE /api/player/note/<id>`: your own note, or for an admin anyone's shared one.

#### Guide channels and your lineup

- `GET /api/player/guide/channels`: the channels people made: `{server: [...], mine: [...], can_edit_server}`. A server channel is an admin's, for everyone; a profile's own only it sees. Each is `{id, scope, spec}`.
- `POST /api/player/guide/channels` with `{scope: mine|server, spec}`: make one (a server one needs an admin). `PUT /api/player/guide/channels/<id>` with `{spec}` changes it, `DELETE` removes it: your own, or for an admin a server one.
- A spec: `{name, kinds: [movies, tv], genres, decades, studios, networks, collection, min_score, kids, added_days, show, people: [{id, name}], order: shuffle|in_order}`. `people` (up to five) makes a channel of their films and shows. Lists match any of their values; `show` makes a marathon (one show, start to finish). The guide schedules it from its catalog like the channels it makes itself.
- `GET /api/player/guide/lineup`: this profile's arrangement, `[{channel_id, position, hidden, name}]`, for any channel (the guide's own too). `PUT` with `{entries: [{channel_id, hidden?, name?}]}` in order replaces it; an empty list puts the guide's order back. Kept in player.db, the same on every screen.

#### What the guide knows beyond its catalog

- `GET /api/player/guide/watched`: `{movies: [ids], episodes: [ids]}` this profile has seen (the library's watch state for whoever's it is, plus what the player saw played through). Channels air the unseen first; a marathon starts at the next unseen episode.
- `GET /api/player/guide/people?q=`: people in the library by name, the ones in most owned titles first: `[{id, name, photo_url, titles, job}]`. `GET /api/player/guide/people/<id>`: `{name, movies: [ids], shows: [ids]}`, their owned films and their shows.
- `GET /api/player/guide/reminders`: what this profile asked to be told about, still to come. `POST` with `{channel_id, starts_at (epoch seconds, within two weeks), program_key, title, subtitle?, channel_name?}` adds one; `DELETE ?channel_id=&starts_at=` takes it off. Gone an hour after the start. Whichever screen of the profile is open rings it.

#### Watched

- `GET /api/player/guide/premieres`: what is new and when it airs on the Live Guide, `{now, premieres: [{key, kind, id, show_id, title, subtitle, seconds, at, homes, has_poster}]}`. `kind` is `episode` or `movie`; `at` is the slot in epoch milliseconds; `homes` lists the channel ids it could air on, best first (`network-hbo`, `genre-drama`, ..., `type-tv`). A device airs it on the first of those it has, cutting whatever was on, so every device shows the same thing. The server gives each slot once and never moves it: 8pm for an episode and 9pm for a film on the day it arrived when that is at least half an hour away, else the next top of the hour that is (up to 11pm), else the next evening. A show's new episodes air three a night in order; an evening takes at most 12 shows and 6 films. A profile only gets the kinds and ratings it may see.
- `POST /api/player/watched` with `{item_type, item_id}`: a movie or episode is into its credits (92% of the way): it counts as watched, in SoulSync's video library and on the media server account, for the profile whose watch state it is (the same rules as the detail page's watched toggle). Returns `{watched}`: false for anything else, or a profile that keeps no watch state of its own. `POST /api/player/progress/finish` does the same for a movie or episode played to the end.

#### Audio and subtitle tracks

- `GET /api/player/tracks/<item_type>/<item_id>?part=`: `{audio, subtitles}`, read from the file (with ffprobe, its header only) and the subtitle files beside it (`Movie.en.srt`, `Movie.es.forced.srt`). An audio track is `{index, language, title, codec, channels, layout, default, plays}`; a subtitle is `{id, language, title, codec, kind, forced, sdh, default, external}`, where `id` is `s<n>` (in the file) or `x<n>` (a file beside it), and `kind` is `text` or `image` (PGS, VobSub).
- `GET /api/player/subtitle/<item_type>/<item_id>?sub=s0`: a text subtitle as WebVTT. Made once and cached: an embedded one means reading the whole file the first time.
- To play another audio track, ask `playable` (or `transcode`) with `&audio=<index>`; to draw an image subtitle into the picture, `&burn=<n>` (its number among the file's subtitles). Both go through the converter: a browser only plays a file's first audio track.


### Watchlist

Each profile keeps a watchlist of things to watch later, shared by all its screens, in `player.db`.

- `GET /api/player/watchlist`: `{items: [{item_type, item_id, meta, added_at}]}`, newest first.
- `PUT /api/player/watchlist` with `{item_type, item_id, meta?}`: adds it, or keeps its place and refreshes `meta` if it's already there. `item_type` is `movie` or `show` (by library id) or `youtube` (by video id). `meta` takes `title`, `subtitle`, `artwork`, `backdrop` and `href`, mostly for YouTube; links from SoulSync's image cache aren't kept, since they expire.
- `DELETE /api/player/watchlist?item_type=&item_id=`: takes it off.

A watchlist holds up to 1000 titles. Draw only what your catalog has: a title that left the library, or one over a kids profile's rating cap, stays on the list but isn't shown. The home layout's `watchlist` row is this list.

### The catalog

`catalog` comes with every `GET`, so an app never has to hard-code the rows: `{version, styles: [{id, label}], rows: [{id, label, hint, styles, style, on}], genre_row, max_rows}`. Use `label` and `hint` in your editor, `styles` for the style picker, and `style` and `on` as each row's defaults.

---

## Endpoints

### System

#### `GET /api/v1/system/status`

Server status, uptime, and service connectivity.

```json
{
  "data": {
    "uptime": "2h 15m 30s",
    "uptime_seconds": 8130,
    "services": {
      "spotify": true,
      "soulseek": true,
      "hydrabase": false
    }
  }
}
```

#### `GET /api/v1/system/activity`

Recent activity feed.

```json
{
  "data": {
    "activities": [
      { "type": "download", "message": "Downloaded Track Name", "timestamp": "..." },
      ...
    ]
  }
}
```

#### `GET /api/v1/system/stats`

Combined library and download statistics.

```json
{
  "data": {
    "library": {
      "artists": 1250,
      "albums": 4830,
      "tracks": 52100
    },
    "database": {
      "size_mb": 145.2,
      "last_update": "2026-03-04T09:00:00"
    },
    "downloads": {
      "active": 3
    }
  }
}
```

---

### Library — Artists

#### `GET /api/v1/library/artists`

List library artists with search, letter filtering, and pagination.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `search` | string | | Filter by name |
| `letter` | string | `all` | Filter by first letter (a-z, `#` for non-alpha) |
| `page` | int | 1 | Page number |
| `limit` | int | 50 | Items per page (max 200) |
| `watchlist` | string | `all` | `all`, `watched`, or `unwatched` |
| `fields` | string | | Comma-separated field list |

**Response:**

```json
{
  "data": {
    "artists": [
      {
        "id": "42",
        "name": "Radiohead",
        "thumb_url": null,
        "banner_url": null,
        "genres": ["alternative rock", "art rock"],
        "summary": null,
        "style": null,
        "mood": null,
        "label": null,
        "server_source": null,
        "created_at": null,
        "updated_at": null,
        "musicbrainz_id": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
        "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb",
        "itunes_artist_id": "657515",
        "audiodb_id": "111239",
        "deezer_id": "399",
        "musicbrainz_match_status": null,
        "spotify_match_status": null,
        "itunes_match_status": null,
        "audiodb_match_status": null,
        "deezer_match_status": null,
        "musicbrainz_last_attempted": null,
        "spotify_last_attempted": null,
        "itunes_last_attempted": null,
        "audiodb_last_attempted": null,
        "deezer_last_attempted": null,
        "album_count": 9,
        "track_count": 101,
        "is_watched": true,
        "image_url": "https://..."
      }
    ]
  },
  "pagination": { "page": 1, "limit": 50, "total": 1250, "total_pages": 25, "has_next": true, "has_prev": false }
}
```

> **Note:** The list endpoint returns a subset of metadata fields. Some fields like `summary`, `style`, `mood`, `label`, `banner_url`, and most `*_match_status` / `*_last_attempted` timestamps may be `null` in list view (the Discogs, Amazon, and JioSaavn statuses and timestamps are included). Use the detail endpoint below for the complete record.

#### `GET /api/v1/library/artists/<artist_id>`

Get a single artist by ID with **all metadata** and their album list.

```json
{
  "data": {
    "artist": {
      "id": "42",
      "name": "Radiohead",
      "thumb_url": "https://i.scdn.co/image/abc123...",
      "banner_url": "https://www.theaudiodb.com/images/media/artist/fanart/...",
      "genres": ["alternative rock", "art rock", "experimental"],
      "summary": "Radiohead are an English rock band formed in Abingdon...",
      "style": "Alternative/Indie",
      "mood": "Melancholy",
      "label": "XL Recordings",
      "server_source": "plex",
      "created_at": "2025-12-01T14:30:00",
      "updated_at": "2026-02-15T09:12:00",
      "musicbrainz_id": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
      "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb",
      "itunes_artist_id": "657515",
      "audiodb_id": "111239",
      "deezer_id": "399",
      "musicbrainz_match_status": "matched",
      "spotify_match_status": "matched",
      "itunes_match_status": "matched",
      "audiodb_match_status": "matched",
      "deezer_match_status": "matched",
      "musicbrainz_last_attempted": "2026-01-10T08:00:00",
      "spotify_last_attempted": "2026-01-10T08:00:00",
      "itunes_last_attempted": "2026-01-10T08:00:00",
      "audiodb_last_attempted": "2026-01-10T08:00:00",
      "deezer_last_attempted": "2026-01-10T08:00:00"
    },
    "albums": [
      {
        "id": "87",
        "artist_id": "42",
        "title": "OK Computer",
        "year": 1997,
        "...": "..."
      }
    ]
  }
}
```

**Artist fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Internal database ID (text; usually numeric, standalone libraries may use opaque IDs containing `::soulsync`) |
| `name` | string | Artist name |
| `thumb_url` | string? | Artist thumbnail/profile image URL |
| `banner_url` | string? | Artist banner/fanart image URL (from AudioDB) |
| `genres` | string[] | List of genre tags |
| `summary` | string? | Artist biography/description |
| `style` | string? | Musical style (from AudioDB) |
| `mood` | string? | Musical mood (from AudioDB) |
| `label` | string? | Record label (from AudioDB) |
| `server_source` | string? | Media server source (`plex`, `jellyfin`, `navidrome`) |
| `created_at` | string? | ISO 8601 timestamp when added to library |
| `updated_at` | string? | ISO 8601 timestamp of last update |
| `musicbrainz_id` | string? | MusicBrainz artist MBID |
| `spotify_artist_id` | string? | Spotify artist ID |
| `itunes_artist_id` | string? | Apple Music / iTunes artist ID |
| `audiodb_id` | string? | TheAudioDB artist ID |
| `deezer_id` | string? | Deezer artist ID |
| `musicbrainz_match_status` | string? | MusicBrainz enrichment status (`matched`, `not_found`, `error`) |
| `spotify_match_status` | string? | Spotify enrichment status |
| `itunes_match_status` | string? | iTunes enrichment status |
| `audiodb_match_status` | string? | AudioDB enrichment status |
| `deezer_match_status` | string? | Deezer enrichment status |
| `musicbrainz_last_attempted` | string? | ISO 8601 timestamp of last MusicBrainz lookup |
| `spotify_last_attempted` | string? | ISO 8601 timestamp of last Spotify lookup |
| `itunes_last_attempted` | string? | ISO 8601 timestamp of last iTunes lookup |
| `audiodb_last_attempted` | string? | ISO 8601 timestamp of last AudioDB lookup |
| `deezer_last_attempted` | string? | ISO 8601 timestamp of last Deezer lookup |
| `soul_id` | string? | Cross-install identity used by the MetaSync network |
| `soul_id_path` | string? | How the `soul_id` was derived (`canonical`, `album`, or `name`) |
| `aliases` | string[] | Alternate artist names (from MusicBrainz) |
| `discogs_id` | string? | Discogs artist ID |
| `discogs_bio` | string? | Artist biography (from Discogs) |
| `discogs_members` | string[] | Band member names (from Discogs) |
| `discogs_urls` | string[] | External links (from Discogs) |
| `discogs_match_status` | string? | Discogs enrichment status |
| `discogs_last_attempted` | string? | ISO 8601 timestamp of last Discogs lookup |
| `amazon_id` | string? | Amazon artist ID |
| `amazon_match_status` | string? | Amazon enrichment status |
| `amazon_last_attempted` | string? | ISO 8601 timestamp of last Amazon lookup |
| `jiosaavn_id` | string? | JioSaavn artist ID |
| `jiosaavn_match_status` | string? | JioSaavn enrichment status |
| `jiosaavn_last_attempted` | string? | ISO 8601 timestamp of last JioSaavn lookup |
| `art_locked` | bool? | Custom artwork lock: scans keep the current artwork |
| `genres_locked` | bool? | Genre lock: scans keep the current genres |

> Fields marked `?` may be `null` if the data hasn't been enriched from that provider yet.

#### `GET /api/v1/library/artists/<artist_id>/albums`

List all albums for a specific artist.

---

### Library — Albums

#### `GET /api/v1/library/albums`

List/search all albums with pagination and optional filters.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `search` | string | | Filter by album title |
| `artist_id` | string | | Filter by artist ID (text; may be opaque). An empty value means no filter |
| `year` | int | | Filter by release year |
| `page` | int | 1 | Page number |
| `limit` | int | 50 | Items per page (max 200) |
| `fields` | string | | Comma-separated field list |

#### `GET /api/v1/library/albums/<album_id>`

Get a single album by ID with **all metadata** and embedded track list.

```json
{
  "data": {
    "album": {
      "id": "87",
      "artist_id": "42",
      "title": "OK Computer",
      "year": 1997,
      "thumb_url": "https://i.scdn.co/image/...",
      "genres": ["alternative rock"],
      "track_count": 12,
      "duration": 3198000,
      "style": "Art Rock",
      "mood": "Anxious",
      "label": "Parlophone",
      "explicit": false,
      "record_type": "album",
      "server_source": "plex",
      "created_at": "2025-12-01T14:30:00",
      "updated_at": "2026-02-15T09:12:00",
      "musicbrainz_release_id": "a1c35a51-d102-4ce7-b7b0-8a4f68385bb2",
      "spotify_album_id": "6dVIqQ8qmQ5GBnJ9shOYGE",
      "itunes_album_id": "1097862703",
      "audiodb_id": "2110483",
      "deezer_id": "6575789",
      "musicbrainz_match_status": "matched",
      "spotify_match_status": "matched",
      "itunes_match_status": "matched",
      "audiodb_match_status": "matched",
      "deezer_match_status": "matched",
      "musicbrainz_last_attempted": "2026-01-10T08:00:00",
      "spotify_last_attempted": "2026-01-10T08:00:00",
      "itunes_last_attempted": "2026-01-10T08:00:00",
      "audiodb_last_attempted": "2026-01-10T08:00:00",
      "deezer_last_attempted": "2026-01-10T08:00:00"
    },
    "tracks": [
      {
        "id": "510",
        "title": "Airbag",
        "track_number": 1,
        "...": "..."
      }
    ]
  }
}
```

**Album fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Internal database ID (text; may be opaque) |
| `artist_id` | string | Parent artist ID |
| `title` | string | Album title |
| `year` | int? | Release year |
| `thumb_url` | string? | Album cover art URL |
| `genres` | string[] | Genre tags |
| `track_count` | int? | Number of tracks |
| `duration` | int? | Total duration in milliseconds |
| `style` | string? | Musical style (from AudioDB) |
| `mood` | string? | Musical mood (from AudioDB) |
| `label` | string? | Record label |
| `explicit` | bool? | Whether album contains explicit content |
| `record_type` | string? | Album type (`album`, `single`, `ep`, `compilation`) |
| `server_source` | string? | Media server source |
| `created_at` | string? | ISO 8601 timestamp |
| `updated_at` | string? | ISO 8601 timestamp |
| `musicbrainz_release_id` | string? | MusicBrainz release MBID |
| `spotify_album_id` | string? | Spotify album ID |
| `itunes_album_id` | string? | Apple Music / iTunes album ID |
| `audiodb_id` | string? | TheAudioDB album ID |
| `deezer_id` | string? | Deezer album ID |
| `musicbrainz_match_status` | string? | MusicBrainz enrichment status |
| `spotify_match_status` | string? | Spotify enrichment status |
| `itunes_match_status` | string? | iTunes enrichment status |
| `audiodb_match_status` | string? | AudioDB enrichment status |
| `deezer_match_status` | string? | Deezer enrichment status |
| `musicbrainz_last_attempted` | string? | ISO 8601 timestamp |
| `spotify_last_attempted` | string? | ISO 8601 timestamp |
| `itunes_last_attempted` | string? | ISO 8601 timestamp |
| `audiodb_last_attempted` | string? | ISO 8601 timestamp |
| `deezer_last_attempted` | string? | ISO 8601 timestamp |
| `release_date` | string? | Full release date when known (alongside the extracted `year`) |
| `soul_id` | string? | Cross-install identity used by the MetaSync network |
| `discogs_id` | string? | Discogs release ID |
| `discogs_genres` | string[] | Genres (from Discogs) |
| `discogs_styles` | string[] | Styles (from Discogs) |
| `discogs_label` | string? | Label (from Discogs) |
| `discogs_catno` | string? | Catalog number (from Discogs) |
| `discogs_country` | string? | Release country (from Discogs) |
| `discogs_rating` | float? | Community rating (from Discogs) |
| `discogs_rating_count` | int? | Number of community ratings (from Discogs) |
| `discogs_match_status` | string? | Discogs enrichment status |
| `discogs_last_attempted` | string? | ISO 8601 timestamp |
| `amazon_id` | string? | Amazon album ID |
| `amazon_match_status` | string? | Amazon enrichment status |
| `amazon_last_attempted` | string? | ISO 8601 timestamp |
| `jiosaavn_id` | string? | JioSaavn album ID |
| `jiosaavn_match_status` | string? | JioSaavn enrichment status |
| `jiosaavn_last_attempted` | string? | ISO 8601 timestamp |
| `bandcamp_id` | string? | Bandcamp release ID |
| `bandcamp_url` | string? | Bandcamp release URL |
| `bandcamp_tags` | string[] | Tags (from Bandcamp) |
| `bandcamp_label` | string? | Label (from Bandcamp) |
| `bandcamp_match_status` | string? | Bandcamp enrichment status |
| `bandcamp_last_attempted` | string? | ISO 8601 timestamp |
| `api_track_count` | int? | Track count of the canonical release, when resolved |
| `canonical_track_count` | int? | Track count fetched for the selected canonical release |
| `canonical_source` | string? | Provider that supplied the canonical release |
| `canonical_album_id` | string? | Provider ID of the canonical release |
| `canonical_score` | float? | Confidence score of the canonical match |
| `canonical_resolved_at` | string? | ISO 8601 timestamp of canonical resolution |
| `canonical_locked` | bool? | Canonical selection was locked by hand |
| `art_locked` | bool? | Custom artwork lock: scans keep the current artwork |
| `genres_locked` | bool? | Genre lock: scans keep the current genres |
| `metadata_locked` | bool? | Metadata lock: the row was hand-tagged and scans keep it |

#### `GET /api/v1/library/albums/<album_id>/tracks`

List all tracks in an album with full metadata.

---

### Library — Tracks

#### `GET /api/v1/library/tracks/<track_id>`

Get a single track by ID with **all metadata**.

```json
{
  "data": {
    "track": {
      "id": "512",
      "album_id": "87",
      "artist_id": "42",
      "title": "Paranoid Android",
      "artist_name": "Radiohead",
      "album_title": "OK Computer",
      "track_number": 2,
      "duration": 383000,
      "file_path": "/music/Radiohead/OK Computer/02 - Paranoid Android.flac",
      "bitrate": 1024,
      "bpm": 82.5,
      "explicit": false,
      "style": "Art Rock",
      "mood": "Anxious",
      "repair_status": null,
      "repair_last_checked": null,
      "server_source": "plex",
      "created_at": "2025-12-01T14:30:00",
      "updated_at": "2026-02-15T09:12:00",
      "musicbrainz_recording_id": "b3e2b7e0-a147-4b3c-8eab-fd90bfff7e74",
      "spotify_track_id": "6LgJvl0Xdtc73RJ1mN1a7Z",
      "itunes_track_id": "1097863011",
      "audiodb_id": null,
      "deezer_id": "119606528",
      "musicbrainz_match_status": "matched",
      "spotify_match_status": "matched",
      "itunes_match_status": "matched",
      "audiodb_match_status": null,
      "deezer_match_status": "matched",
      "musicbrainz_last_attempted": "2026-01-10T08:00:00",
      "spotify_last_attempted": "2026-01-10T08:00:00",
      "itunes_last_attempted": "2026-01-10T08:00:00",
      "audiodb_last_attempted": null,
      "deezer_last_attempted": "2026-01-10T08:00:00"
    }
  }
}
```

**Track fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Internal database ID (text; may be opaque) |
| `album_id` | string | Parent album ID |
| `artist_id` | string | Parent artist ID |
| `title` | string | Track title |
| `artist_name` | string? | Artist name (joined from artists table) |
| `album_title` | string? | Album title (joined from albums table) |
| `track_number` | int? | Track number on the album |
| `duration` | int? | Duration in milliseconds |
| `file_path` | string? | SoulSync's local file path for the track |
| `bitrate` | int? | Audio bitrate in kbps |
| `bpm` | float? | Beats per minute |
| `explicit` | bool? | Whether track contains explicit content |
| `style` | string? | Musical style (from AudioDB) |
| `mood` | string? | Musical mood (from AudioDB) |
| `repair_status` | string? | Track repair status |
| `repair_last_checked` | string? | ISO 8601 timestamp of last repair check |
| `server_source` | string? | Media server source |
| `created_at` | string? | ISO 8601 timestamp |
| `updated_at` | string? | ISO 8601 timestamp |
| `musicbrainz_recording_id` | string? | MusicBrainz recording MBID |
| `spotify_track_id` | string? | Spotify track ID |
| `itunes_track_id` | string? | Apple Music / iTunes track ID |
| `audiodb_id` | string? | TheAudioDB track ID |
| `deezer_id` | string? | Deezer track ID |
| `musicbrainz_match_status` | string? | MusicBrainz enrichment status |
| `spotify_match_status` | string? | Spotify enrichment status |
| `itunes_match_status` | string? | iTunes enrichment status |
| `audiodb_match_status` | string? | AudioDB enrichment status |
| `deezer_match_status` | string? | Deezer enrichment status |
| `musicbrainz_last_attempted` | string? | ISO 8601 timestamp |
| `spotify_last_attempted` | string? | ISO 8601 timestamp |
| `itunes_last_attempted` | string? | ISO 8601 timestamp |
| `audiodb_last_attempted` | string? | ISO 8601 timestamp |
| `deezer_last_attempted` | string? | ISO 8601 timestamp |
| `disc_number` | int? | Disc number for multi-disc releases |
| `year` | int? | Per-track release year from file tags |
| `track_artist` | string? | Track performer credit (e.g. on compilations) |
| `recording_disambiguation` | string? | MusicBrainz recording comment distinguishing versions (e.g. live, acoustic) |
| `server_path` | string? | File path as reported by the media server |
| `file_size` | int? | File size in bytes |
| `soul_id` | string? | Cross-install song identity used by the MetaSync network |
| `album_soul_id` | string? | Cross-install identity of this track's specific release |
| `amazon_id` | string? | Amazon track ID |
| `amazon_match_status` | string? | Amazon enrichment status |
| `amazon_last_attempted` | string? | ISO 8601 timestamp |
| `jiosaavn_id` | string? | JioSaavn track ID |
| `jiosaavn_match_status` | string? | JioSaavn enrichment status |
| `jiosaavn_last_attempted` | string? | ISO 8601 timestamp |
| `bandcamp_id` | string? | Bandcamp track ID |
| `bandcamp_url` | string? | Bandcamp track URL |
| `bandcamp_tags` | string[] | Tags (from Bandcamp) |
| `bandcamp_label` | string? | Label (from Bandcamp) |
| `bandcamp_match_status` | string? | Bandcamp enrichment status |
| `bandcamp_last_attempted` | string? | ISO 8601 timestamp |
| `metadata_locked` | bool? | Metadata lock: the row was hand-tagged and scans keep it |

#### `GET /api/v1/library/tracks`

Search tracks by title and/or artist. At least one of `title` or `artist` is required.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | string | | Track title to search |
| `artist` | string | | Artist name to search |
| `limit` | int | 50 | Max results (max 200) |
| `fields` | string | | Comma-separated field list |

---

### Library — Genres

#### `GET /api/v1/library/genres`

List all genres in the library with occurrence counts.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | `artists` | Table to aggregate from: `artists` or `albums` |

```json
{
  "data": {
    "genres": [
      { "name": "rock", "count": 234 },
      { "name": "alternative rock", "count": 189 },
      { "name": "indie rock", "count": 156 },
      { "name": "electronic", "count": 98 },
      { "name": "pop", "count": 87 }
    ],
    "source": "artists"
  }
}
```

---

### Library — Recently Added

#### `GET /api/v1/library/recently-added`

Get recently added content, ordered by creation date.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | `albums` | Entity type: `albums`, `artists`, or `tracks` |
| `limit` | int | 50 | Max items (max 200) |
| `fields` | string | | Comma-separated field list |

```json
{
  "data": {
    "items": [
      {
        "id": "4831",
        "artist_id": "42",
        "title": "A Moon Shaped Pool",
        "year": 2016,
        "thumb_url": "https://...",
        "genres": ["art rock"],
        "...": "..."
      }
    ],
    "type": "albums"
  }
}
```

---

### Library — External ID Lookup

#### `GET /api/v1/library/lookup`

Look up a library entity by its external provider ID. Useful for cross-referencing with Spotify, MusicBrainz, iTunes, Deezer, AudioDB, Tidal, Qobuz, Genius, Discogs, Amazon, JioSaavn, or Bandcamp.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | `artist`, `album`, or `track` |
| `provider` | string | Yes | `spotify`, `musicbrainz`, `itunes`, `deezer`, `audiodb`, `tidal`, `qobuz`, `genius`, `discogs`, `amazon`, `jiosaavn`, or `bandcamp` |
| `id` | string | Yes | The external ID value |
| `fields` | string | No | Comma-separated field list |

Not every provider applies to every type: Genius IDs are not stored for albums, Bandcamp IDs are not stored for artists, and Discogs IDs are not stored for tracks. Those combinations return `400 BAD_REQUEST`.

**Example — find an artist by Spotify ID:**

```
GET /api/v1/library/lookup?type=artist&provider=spotify&id=4Z8W4fKeB5YxbusRsdQVPb
```

```json
{
  "data": {
    "artist": {
      "id": "42",
      "name": "Radiohead",
      "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb",
      "...": "..."
    }
  }
}
```

**Example — find a track by MusicBrainz recording ID:**

```
GET /api/v1/library/lookup?type=track&provider=musicbrainz&id=b3e2b7e0-a147-4b3c-8eab-fd90bfff7e74
```

Returns `404 NOT_FOUND` if no matching entity exists in the library.

---

### Library — Batch Artist Check

#### `POST /api/v1/library/check`

A page of artist names in one call: is each one in the library, its library id, and whether this profile watches it. Built for badge overlays (the SoulSync Companion browser extension) that would otherwise search once per artist. Exact matching only: the name as written, then the same name with case, accents and punctuation folded (`ACDC` finds `AC/DC`, `Bjork` finds `Björk`). A name that only appears inside a longer one never matches. Nothing is fetched from a metadata provider.

**Body:**
```json
{"artists": ["Low", {"name": "Radiohead", "provider_id": "4Z8W4fKeB5YxbusRsdQVPb", "albums": true}]}
```

| Field | Type | Description |
|-------|------|-------------|
| `artists` | array | Up to 200 names, each a string or `{name, provider_id?, albums?}`. Duplicates are answered once. |
| `provider_id` | string | A provider artist id the page knows; a watch stored under it counts. |
| `albums` | boolean | Also return the artist's albums (`[{id, title}]`). |

**Response** (`data.artists`, in the order asked):
```json
{"name": "Low", "in_library": true, "artist_id": "1", "library_name": "Low",
 "watching": true, "watch_id": "spotify-id-the-watch-was-stored-under",
 "albums": [{"id": "100", "title": "Things We Lost in the Fire"}]}
```

`in_library` follows the Library page: an artist with something of their own on disk, a watch, or a wanted release. `watching` matches the watchlist by the provider id given, the provider ids the library artist carries, or the watched name; `watch_id` is the id to pass to `POST /api/watchlist/remove`. `400 BAD_REQUEST` when `artists` is missing or empty.

---

### Library — Stats

#### `GET /api/v1/library/stats`

Library statistics (counts and database info).

```json
{
  "data": {
    "artists": 1250,
    "albums": 4830,
    "tracks": 52100,
    "database_size_mb": 145.2,
    "last_update": "2026-03-04T09:00:00"
  }
}
```

---

### Library — Download Activity

#### `GET /api/v1/library/download-activity`

Per-album completed-download aggregates, used by the MetaSync node to build its download-based signals. One row per library album that has at least one completed download, keyed by the album's MusicBrainz release ID (an opaque text identifier). Only IDs, counts, and timestamps are returned — no titles or file paths. Albums without a MusicBrainz release ID do not appear.

**Parameters:**
- `since` (optional): ISO-8601 timestamp. Each row's `completed_since` counts only downloads completed at or after this instant.
- `until` (optional): ISO-8601 timestamp. `completed_since` counts only downloads completed before this instant, so `since` + `until` count one closed window. Without either, `completed_since` equals `completed_downloads`.

```json
{
  "data": {
    "albums": [
      {
        "musicbrainz_release_id": "b1392450-e666-3926-a536-22c65f834433",
        "completed_downloads": 12,
        "completed_since": 3,
        "first_downloaded_at": "2026-08-02 14:03:11",
        "last_downloaded_at": "2026-10-21 09:30:00"
      }
    ]
  }
}
```

#### `GET /api/v1/library/listening-activity`

Per-album listening aggregates for one time window, used by the MetaSync node for its opt-in listening signal. One row per library album played in `[since, until)`, keyed by the album's MusicBrainz release ID: how many plays, how many distinct tracks, and on how many distinct (UTC) days. Only IDs and counts are returned — no titles, no individual play times, no profile or media-server details. Albums without a MusicBrainz release ID, and plays that matched no library track, do not appear.

Only the **shared** listening history is read — the plays the admin's own stats show. A profile with its own ListenBrainz, Last.fm, or media-server account keeps its own history, and that history is never included here.

**Parameters:**
- `since` (required): ISO-8601 timestamp, window start (inclusive).
- `until` (required): ISO-8601 timestamp, window end (exclusive). At most 31 days after `since`.

```json
{
  "data": {
    "albums": [
      {
        "musicbrainz_release_id": "b1392450-e666-3926-a536-22c65f834433",
        "plays": 14,
        "distinct_tracks": 9,
        "distinct_days": 3
      }
    ]
  }
}
```

---

### Search

Search external music sources (Spotify, iTunes, Hydrabase). These endpoints search **external services**, not your local library (use `/library/tracks` or `/library/lookup` for that).

#### `POST /api/v1/search/tracks`

```json
{
  "query": "Daft Punk Around the World",
  "source": "auto",
  "limit": 20
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | *required* | Search query |
| `source` | string | `auto` | `auto` (Hydrabase > Spotify > iTunes), `spotify`, or `itunes` |
| `limit` | int | 20 | Max results (max 50) |

**Response:**

```json
{
  "data": {
    "tracks": [
      {
        "id": "2cGxRwrMyEAp8dEbuZaVv6",
        "name": "Around the World",
        "artists": ["Daft Punk"],
        "album": "Homework",
        "duration_ms": 428000,
        "popularity": 78,
        "preview_url": "https://...",
        "image_url": "https://i.scdn.co/image/...",
        "release_date": "1997-01-17"
      }
    ],
    "source": "spotify"
  }
}
```

#### `POST /api/v1/search/albums`

```json
{
  "query": "Discovery",
  "limit": 10
}
```

**Response:**

```json
{
  "data": {
    "albums": [
      {
        "id": "2noRn2Aes5aoNVsU6iWThc",
        "name": "Discovery",
        "artists": ["Daft Punk"],
        "release_date": "2001-03-12",
        "total_tracks": 14,
        "album_type": "album",
        "image_url": "https://..."
      }
    ],
    "source": "spotify"
  }
}
```

#### `POST /api/v1/search/artists`

```json
{
  "query": "Daft Punk",
  "limit": 10
}
```

**Response:**

```json
{
  "data": {
    "artists": [
      {
        "id": "4tZwfgrHOc3mvqYlEYSvnL",
        "name": "Daft Punk",
        "popularity": 82,
        "genres": ["electro", "french house"],
        "followers": 21000000,
        "image_url": "https://..."
      }
    ],
    "source": "spotify"
  }
}
```

---

### Downloads

#### `GET /api/v1/downloads`

List active and recent download tasks.

```json
{
  "data": {
    "downloads": [
      {
        "id": "task_abc123",
        "status": "downloading",
        "track_name": "Paranoid Android",
        "artist_name": "Radiohead",
        "album_name": "OK Computer",
        "username": "soulseek_user_42",
        "filename": "02 - Paranoid Android.flac",
        "progress": 67,
        "size": 45000000,
        "error": null,
        "batch_id": "batch_xyz",
        "track_index": 2,
        "retry_count": 0,
        "metadata_enhanced": false,
        "status_change_time": 1709550000.123
      }
    ]
  }
}
```

**Download fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique task identifier |
| `status` | string | `pending`, `searching`, `downloading`, `completed`, `failed` |
| `track_name` | string? | Track being downloaded |
| `artist_name` | string? | Artist name |
| `album_name` | string? | Album name |
| `username` | string? | Soulseek peer username |
| `filename` | string? | Remote filename |
| `progress` | int | Download progress percentage (0-100) |
| `size` | int? | File size in bytes |
| `error` | string? | Error message if failed |
| `batch_id` | string? | Batch download group ID |
| `track_index` | int? | Track position in batch |
| `retry_count` | int | Number of retry attempts |
| `metadata_enhanced` | bool | Whether metadata was enhanced post-download |
| `status_change_time` | float? | Unix timestamp of last status change |

#### `POST /api/v1/downloads/<download_id>/cancel`

Cancel a specific download.

```json
{
  "username": "soulseek_username"
}
```

#### `POST /api/v1/downloads/cancel-all`

Cancel all active downloads and clear completed ones.

---

### Wishlist

Tracks that failed to download, queued for retry. Profile-scoped via `X-Profile-Id`.

#### `GET /api/v1/wishlist`

List wishlist tracks with standardized format.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `category` | string | | `singles` or `albums` |
| `page` | int | 1 | Page number |
| `limit` | int | 50 | Items per page (max 200) |
| `fields` | string | | Comma-separated field list |

```json
{
  "data": {
    "tracks": [
      {
        "id": 15,
        "spotify_track_id": "6LgJvl0Xdtc73RJ1mN1a7Z",
        "track_name": "Paranoid Android",
        "artist_name": "Radiohead",
        "album_name": "OK Computer",
        "spotify_data": { "...full Spotify track object..." },
        "failure_reason": "No sources found",
        "retry_count": 3,
        "last_attempted": "2026-03-03T15:30:00",
        "date_added": "2026-03-01T10:00:00",
        "source_type": "playlist",
        "source_info": { "playlist_name": "My Playlist", "playlist_id": "..." },
        "profile_id": 1
      }
    ]
  },
  "pagination": { "..." }
}
```

**Wishlist track fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Internal database ID |
| `spotify_track_id` | string | Spotify track ID |
| `track_name` | string | Extracted track name |
| `artist_name` | string | Extracted artist name(s) |
| `album_name` | string? | Extracted album name |
| `spotify_data` | object | Full Spotify track metadata object |
| `failure_reason` | string? | Why the download failed |
| `retry_count` | int | Number of retry attempts |
| `last_attempted` | string? | ISO 8601 timestamp of last attempt |
| `date_added` | string? | ISO 8601 timestamp when added |
| `source_type` | string? | How it was added: `playlist`, `album`, `manual`, `api` |
| `source_info` | object? | Context about the source (playlist name, etc.) |
| `profile_id` | int? | Profile this track belongs to |

#### `POST /api/v1/wishlist`

Add a track to the wishlist.

```json
{
  "spotify_track_data": {
    "id": "6LgJvl0Xdtc73RJ1mN1a7Z",
    "name": "Paranoid Android",
    "artists": [{ "name": "Radiohead" }],
    "album": { "name": "OK Computer", "album_type": "album" }
  },
  "failure_reason": "No sources found",
  "source_type": "api"
}
```

#### `DELETE /api/v1/wishlist/<spotify_track_id>`

Remove a track from the wishlist by its Spotify track ID.

#### `POST /api/v1/wishlist/process`

Trigger wishlist download processing (retries all failed tracks). `409 CONFLICT` while a run is already in progress.

---

### Watchlist

Artists being monitored for new releases. Profile-scoped via `X-Profile-Id`.

#### `GET /api/v1/watchlist`

List all watched artists for the current profile.

```json
{
  "data": {
    "artists": [
      {
        "id": 5,
        "spotify_artist_id": "4tZwfgrHOc3mvqYlEYSvnL",
        "itunes_artist_id": "5468295",
        "artist_name": "Daft Punk",
        "image_url": "https://i.scdn.co/image/...",
        "date_added": "2026-01-15T10:00:00",
        "last_scan_timestamp": "2026-03-04T06:00:00",
        "created_at": "2026-01-15T10:00:00",
        "updated_at": "2026-03-04T06:00:00",
        "profile_id": 1,
        "include_albums": true,
        "include_eps": true,
        "include_singles": true,
        "include_live": false,
        "include_remixes": false,
        "include_acoustic": false,
        "include_compilations": false,
        "follow_defaults": true
      }
    ]
  }
}
```

**Watchlist artist fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Internal database ID |
| `spotify_artist_id` | string? | Spotify artist ID |
| `itunes_artist_id` | string? | iTunes artist ID |
| `artist_name` | string | Artist name |
| `image_url` | string? | Artist image URL |
| `date_added` | string? | ISO 8601 timestamp |
| `last_scan_timestamp` | string? | ISO 8601 timestamp of last scan |
| `created_at` | string? | ISO 8601 timestamp |
| `updated_at` | string? | ISO 8601 timestamp |
| `profile_id` | int? | Profile this entry belongs to |
| `include_albums` | bool | Monitor for new albums |
| `include_eps` | bool | Monitor for new EPs |
| `include_singles` | bool | Monitor for new singles |
| `include_live` | bool | Include live albums (concert recordings, unplugged sets) |
| `include_live_tracks` | bool | Include a live version on any other release, like a live bonus track. Follows `include_live` until it is set once |
| `include_remixes` | bool | Include remixes |
| `include_acoustic` | bool | Include acoustic versions |
| `include_compilations` | bool | Include compilations |
| `follow_defaults` | bool | The artist uses the watchlist defaults. The `include_*` fields then show the defaults, which are the rules a scan applies |
| `release_statuses` | string[]? | The MusicBrainz release statuses this artist lets through (`official`, `promotion`, `bootleg`, `pseudo-release`, `withdrawn`, `expunged`, `cancelled`). `null` = the library-wide list from the watchlist defaults |

#### `POST /api/v1/watchlist`

Add an artist to the watchlist.

```json
{
  "artist_id": "4tZwfgrHOc3mvqYlEYSvnL",
  "artist_name": "Daft Punk"
}
```

#### `PATCH /api/v1/watchlist/<artist_id>`

Update content type filters for a watched artist without having to remove and re-add them. Only the fields you include in the body will be updated.

```json
{
  "include_live": true,
  "include_remixes": true,
  "include_compilations": false
}
```

Accepts any combination of: `include_albums`, `include_eps`, `include_singles`, `include_live`, `include_live_tracks`, `include_remixes`, `include_acoustic`, `include_compilations`, `follow_defaults`, `release_statuses`.

`release_statuses` is a list of MusicBrainz statuses the artist lets through, or `null` to follow the library-wide list. A release group passes when any of its releases has one of them; a release from another source, or with no status, always passes. An empty list or an unknown status is a 400.

Send `"follow_defaults": true` to put an artist back on the watchlist defaults. Sending release rules for an artist that follows the defaults gives it its own rules, starting from the defaults it was getting, so the fields you leave out keep the values a scan was already using.

**Response:**

```json
{
  "data": {
    "message": "Watchlist filters updated.",
    "updated": {
      "include_live": true,
      "include_remixes": true,
      "include_compilations": false
    }
  }
}
```

#### `DELETE /api/v1/watchlist/<artist_id>`

Remove an artist from the watchlist. `artist_id` can be a Spotify or iTunes artist ID.

#### `POST /api/v1/watchlist/scan`

Trigger a watchlist scan for new releases. Returns `409 CONFLICT` if a scan is already running.

---

### Discovery

Browse discovery pool, similar artists, and recent releases. Profile-scoped via `X-Profile-Id`.

#### `GET /api/v1/discover/pool`

List discovery pool tracks with pagination and optional filters.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `new_releases_only` | string | `false` | Set to `true` to filter to new releases only |
| `source` | string | | `spotify` or `itunes` (omit for all) |
| `page` | int | 1 | Page number |
| `limit` | int | 100 | Items per page (max 500) |
| `fields` | string | | Comma-separated field list |

```json
{
  "data": {
    "tracks": [
      {
        "id": 1024,
        "spotify_track_id": "3n3Ppam7vgaVa1iaRUc9Lp",
        "spotify_album_id": "2noRn2Aes5aoNVsU6iWThc",
        "spotify_artist_id": "4tZwfgrHOc3mvqYlEYSvnL",
        "itunes_track_id": null,
        "itunes_album_id": null,
        "itunes_artist_id": null,
        "source": "spotify",
        "track_name": "Something About Us",
        "artist_name": "Daft Punk",
        "album_name": "Discovery",
        "album_cover_url": "https://i.scdn.co/image/...",
        "duration_ms": 232000,
        "popularity": 76,
        "release_date": "2001-03-12",
        "is_new_release": false,
        "artist_genres": ["electro", "french house"],
        "added_date": "2026-03-01T12:00:00"
      }
    ]
  },
  "pagination": { "page": 1, "limit": 100, "total": 450, "..." }
}
```

#### `GET /api/v1/discover/similar-artists`

List top similar artists discovered from watchlist analysis.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max artists (max 200) |
| `fields` | string | | Comma-separated field list |

```json
{
  "data": {
    "artists": [
      {
        "id": 88,
        "source_artist_id": "4tZwfgrHOc3mvqYlEYSvnL",
        "similar_artist_spotify_id": "12Chz98pHFMPJEknJQMWvI",
        "similar_artist_itunes_id": null,
        "similar_artist_name": "Justice",
        "similarity_rank": 1,
        "occurrence_count": 5,
        "last_updated": "2026-03-01T12:00:00",
        "last_featured": "2026-03-03T08:00:00"
      }
    ]
  }
}
```

#### `GET /api/v1/discover/recent-releases`

List recent releases from watched artists.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max releases (max 200) |
| `fields` | string | | Comma-separated field list |

```json
{
  "data": {
    "releases": [
      {
        "id": 12,
        "watchlist_artist_id": 5,
        "album_spotify_id": "2noRn2Aes5aoNVsU6iWThc",
        "album_itunes_id": null,
        "source": "spotify",
        "album_name": "Random Access Memories (10th Anniversary Edition)",
        "release_date": "2023-05-12",
        "album_cover_url": "https://...",
        "track_count": 22,
        "added_date": "2026-03-01T06:00:00"
      }
    ]
  }
}
```

#### `GET /api/v1/discover/pool/metadata`

Get discovery pool metadata (when it was last populated, track count).

```json
{
  "data": {
    "last_populated": "2026-03-04T06:00:00",
    "track_count": 450,
    "updated_at": "2026-03-04T06:00:00"
  }
}
```

#### `GET /api/v1/discover/bubbles`

List all bubble snapshots for the current profile. Returns snapshots for three types: `artist_bubbles`, `search_bubbles`, `discover_downloads`. Each snapshot is `null` if it hasn't been created yet.

```json
{
  "data": {
    "snapshots": {
      "artist_bubbles": {
        "data": {
          "bubbles": [
            { "name": "Radiohead", "value": 45, "genre": "alternative rock" },
            { "name": "Daft Punk", "value": 32, "genre": "electronic" },
            { "name": "Portishead", "value": 18, "genre": "trip hop" }
          ],
          "total_artists": 3,
          "generated_at": "2026-03-04T06:00:00"
        },
        "timestamp": "2026-03-04T06:00:00"
      },
      "search_bubbles": {
        "data": {
          "bubbles": [
            { "query": "ambient electronic", "count": 12 },
            { "query": "shoegaze", "count": 8 }
          ],
          "generated_at": "2026-03-03T14:00:00"
        },
        "timestamp": "2026-03-03T14:00:00"
      },
      "discover_downloads": null
    }
  }
}
```

#### `GET /api/v1/discover/bubbles/<snapshot_type>`

Get a specific bubble snapshot by type.

| Param | Type | Description |
|-------|------|-------------|
| `snapshot_type` | path | `artist_bubbles`, `search_bubbles`, or `discover_downloads` |

```json
{
  "data": {
    "snapshot": {
      "data": {
        "bubbles": [
          { "name": "Radiohead", "value": 45, "genre": "alternative rock" },
          { "name": "Daft Punk", "value": 32, "genre": "electronic" },
          { "name": "Portishead", "value": 18, "genre": "trip hop" }
        ],
        "total_artists": 3,
        "generated_at": "2026-03-04T06:00:00"
      },
      "timestamp": "2026-03-04T06:00:00"
    }
  }
}
```

**Bubble snapshot fields:**

| Field | Type | Description |
|-------|------|-------------|
| `data` | object | Parsed snapshot data (structure varies by type — contains bubble arrays and metadata) |
| `timestamp` | string | ISO 8601 timestamp when the snapshot was created/updated |

> **Snapshot types:** `artist_bubbles` — artist listening weight visualization data. `search_bubbles` — search frequency visualization data. `discover_downloads` — discovery download activity visualization data. Returns `404` if the requested snapshot type doesn't exist for this profile.

---

### Playlists

#### `GET /api/v1/playlists`

List user playlists from Spotify or Tidal.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | `spotify` | `spotify` or `tidal` |

```json
{
  "data": {
    "playlists": [
      {
        "id": "37i9dQZF1DXcBWIGoYBM5M",
        "name": "Today's Top Hits",
        "owner": "spotify",
        "track_count": 50,
        "image_url": "https://..."
      }
    ],
    "source": "spotify"
  }
}
```

#### `GET /api/v1/playlists/<playlist_id>`

Get playlist details with full track list.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | `spotify` | Currently only `spotify` supported for detail view |

```json
{
  "data": {
    "playlist": {
      "id": "37i9dQZF1DXcBWIGoYBM5M",
      "name": "Today's Top Hits",
      "owner": "Spotify",
      "total_tracks": 50,
      "tracks": [
        {
          "id": "2cGxRwrMyEAp8dEbuZaVv6",
          "name": "Around the World",
          "artists": ["Daft Punk"],
          "album": "Homework",
          "duration_ms": 428000,
          "image_url": "https://..."
        }
      ]
    },
    "source": "spotify"
  }
}
```

#### `POST /api/v1/playlists/<playlist_id>/sync`

Trigger playlist sync/download.

```json
{
  "playlist_name": "My Playlist",
  "tracks": [
    {
      "id": "2cGxRwrMyEAp8dEbuZaVv6",
      "name": "Around the World",
      "artists": [{ "name": "Daft Punk" }]
    }
  ]
}
```

---

### Profiles

Manage user profiles. Profiles scope watchlist, wishlist, and discovery data.

#### `GET /api/v1/profiles`

List all profiles.

```json
{
  "data": {
    "profiles": [
      {
        "id": 1,
        "name": "Admin",
        "avatar_color": "#6366f1",
        "avatar_url": null,
        "is_admin": true,
        "has_pin": false,
        "created_at": "2026-01-15T10:00:00",
        "updated_at": "2026-02-20T14:30:00"
      },
      {
        "id": 2,
        "name": "Family",
        "avatar_color": "#10b981",
        "avatar_url": "https://example.com/avatar.jpg",
        "is_admin": false,
        "has_pin": true,
        "created_at": "2026-02-01T15:00:00",
        "updated_at": "2026-03-01T09:45:00"
      }
    ]
  }
}
```

#### `GET /api/v1/profiles/<profile_id>`

Get a single profile by ID.

```json
{
  "data": {
    "profile": {
      "id": 2,
      "name": "Family",
      "avatar_color": "#10b981",
      "avatar_url": "https://example.com/avatar.jpg",
      "is_admin": false,
      "has_pin": true,
      "created_at": "2026-02-01T15:00:00",
      "updated_at": "2026-03-01T09:45:00"
    }
  }
}
```

**Profile fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Profile ID (auto-increment) |
| `name` | string | Display name (unique) |
| `avatar_color` | string | Hex color for the default avatar circle (default `#6366f1`) |
| `avatar_url` | string? | Custom avatar image URL |
| `is_admin` | bool | Whether this profile has admin privileges |
| `has_pin` | bool | Whether a PIN is set (the PIN hash itself is never exposed) |
| `created_at` | string | ISO 8601 timestamp when profile was created |
| `updated_at` | string | ISO 8601 timestamp of last profile update |

> **Note:** The `pin_hash` column exists in the database but is never returned by the API. Only the computed `has_pin` boolean is exposed.

#### `POST /api/v1/profiles`

Create a new profile.

**Request body:**

```json
{
  "name": "Family",
  "avatar_color": "#10b981",
  "avatar_url": "https://example.com/avatar.jpg",
  "is_admin": false,
  "pin": "1234"
}
```

All fields except `name` are optional. The `pin` field accepts a raw PIN string — it is hashed with PBKDF2-SHA256 before storage.

**Response (201 Created):**

```json
{
  "data": {
    "profile": {
      "id": 3,
      "name": "Family",
      "avatar_color": "#10b981",
      "avatar_url": "https://example.com/avatar.jpg",
      "is_admin": false,
      "has_pin": true,
      "created_at": "2026-03-04T12:00:00",
      "updated_at": "2026-03-04T12:00:00"
    }
  }
}
```

Returns `409 CONFLICT` if the profile name already exists.

#### `PUT /api/v1/profiles/<profile_id>`

Update a profile. Only include the fields you want to change.

**Request body:**

```json
{
  "name": "New Name",
  "avatar_color": "#ef4444",
  "pin": "5678"
}
```

Set `"pin": ""` or `"pin": null` to remove a PIN.

**Response:**

```json
{
  "data": {
    "profile": {
      "id": 2,
      "name": "New Name",
      "avatar_color": "#ef4444",
      "avatar_url": "https://example.com/avatar.jpg",
      "is_admin": false,
      "has_pin": true,
      "created_at": "2026-02-01T15:00:00",
      "updated_at": "2026-03-04T12:05:00"
    }
  }
}
```

#### `DELETE /api/v1/profiles/<profile_id>`

Delete a profile and all its per-profile data (watchlist, wishlist, discovery pool, bubble snapshots). Cannot delete profile 1 (admin) — returns `403 FORBIDDEN`.

**Response:**

```json
{
  "data": {
    "message": "Profile 3 deleted."
  }
}
```

#### Own library on Navidrome: `GET /api/profiles/me/navidrome-libraries`

Session route (#2031). For the signed-in profile: `own_library` (the admin gave it a library of its own), `supported` (the active server can: Plex, Jellyfin, or Navidrome 0.58+), `message` (why not, or what is missing, e.g. no Navidrome login yet), `selected` (the picked library id) and `folders`: the Navidrome libraries the profile's own login sees (`getMusicFolders` as that user), each `{id, name, shared}`. `folders` is empty unless the profile has an own library on a supported server.

The pick is saved with `POST /api/profiles/me/server-library` and `{"server_type": "navidrome", "library_id": "2"}`. The id must be one the profile's own login sees and not the shared library's folder; otherwise `400`. For Jellyfin, `{"server_type": "jellyfin", "user_id": "..."}` must name a user the Jellyfin server lists (`400` otherwise, `503` when it can't be checked) that no other profile already is (`409`). `POST /api/profiles/me/plex-home-user` likewise answers `409` for a Plex home user another profile is linked to.

`GET /api/profiles` also returns `own_library_unsupported_reason` (null when own libraries work on the active server). Turning a profile to `library_mode: "own"` on Navidrome needs Navidrome 0.58+ and the shared library's music folder picked in Settings.

#### Who sees your listening: `GET` / `POST /api/profiles/me/listening-privacy`

Session route, the signed-in profile's own (an admin can't answer for anyone else). `GET` returns `share_listening` (blends may use your plays), `asked` (you've answered the one-time ask), `ask` (Discover should ask now: not answered yet and someone else is on the server), `private_session` and `private_until` (UTC, when it turns itself off), and `own_pile`.

`POST` takes any of `{"share_listening": true|false}` (also counts as answered), `{"asked": true}` ("not now": stops the asking, changes nothing else) and `{"private_session": true|false}` (on for 6 hours). It answers with the same object.

Both are off for every profile until that profile turns them on. A blend only exists when both sides' listening is shared, and the shared history (owner 1) only counts as shared when every profile playing into it has `share_listening` on. During a private session `POST /api/library/log-play` answers `{"success": true, "skipped": true, "private": true}` and records nothing, so there is no play to scrobble.

#### What a profile shows: `GET` / `POST /api/profiles/me/features`

Session routes (the signed-in browser, not an API key) behind **Customize** in the sidebar. Each profile hides the parts of SoulSync it doesn't use. This is a preference, not access: hidden pages still open from a link and their background jobs keep running. Page access stays with `allowed_pages`.

`GET` returns `chosen` (false until this profile saves anything, which is when the sidebar offers the picker once), the list of features that can be hidden (`features`, each with `id`, `label`, `desc`, `group` and the page ids it owns), the `groups`, the `presets`, what is `off` for this profile, which `preset` that matches (`full`, `simple` or `custom`) and the resulting `hidden_pages`.

`POST` takes one of:

| Body | Effect |
|------|--------|
| `{"off": ["podcasts", "video"]}` | exactly these off, unknown ids dropped |
| `{"preset": "simple"}` | `simple` (music, Discover, playlist sync and import) or `full` (everything) |
| `{"purposes": ["music", "podcasts"]}` | what first-run setup asks; hides podcasts, audiobooks or video when they weren't picked |

and answers with the same shape as `GET`. Admins also get `background`: for `podcasts` and `audiobooks`, whether their jobs are `paused` and whether `everyone_hid` them.

`POST /api/profiles/me/features/background` (admin only) takes `{"feature": "podcasts", "paused": true}`. A paused part's scheduled and event automations are skipped only while every profile has it hidden (a profile that can't open the page counts as hidden); turning it back on for anyone resumes them. Run now still runs. The video side's jobs have their own master switch on the video Automations page. Feature ids: `discover`, `playlists`, `explorer`, `import`, `stats`, `music_videos`, `sample_studio`, `podcasts`, `audiobooks`, `video`, `video_discover`, `video_calendar`, `chat`, `automations`, `tools`. Search, library, watchlist, wishlist, downloads and settings can't be hidden, on either side.

---

### Retag Queue

Browse and manage the retag queue — albums/tracks pending metadata corrections.
The queue is the Library Re-tag job's pending findings: a group is an album with
pending findings (`id` = the album's id), a track is one finding (`id` = the
finding's id), and deleting a group dismisses its findings.

#### `GET /api/v1/retag/groups`

List all retag groups with track counts. Groups are ordered by artist name (ascending) then creation date (descending).

```json
{
  "data": {
    "groups": [
      {
        "id": 1,
        "group_type": "album",
        "artist_name": "Radiohead",
        "album_name": "OK Computer",
        "image_url": "https://i.scdn.co/image/ab67616d0000b273c8b444df094c596ea9da41f6",
        "spotify_album_id": "6dVIqQ8qmQ5GBnJ9shOYGE",
        "itunes_album_id": "1097862703",
        "total_tracks": 12,
        "release_date": "1997-06-16",
        "created_at": "2026-03-04T10:00:00",
        "track_count": 12
      },
      {
        "id": 2,
        "group_type": "album",
        "artist_name": "Radiohead",
        "album_name": "In Rainbows",
        "image_url": "https://i.scdn.co/image/ab67616d0000b2737de1fcc2ebeab8b6e22a1b8a",
        "spotify_album_id": "7eyQXxuf2nGj9d2367Gi5f",
        "itunes_album_id": "1109731429",
        "total_tracks": 10,
        "release_date": "2007-10-10",
        "created_at": "2026-03-04T10:05:00",
        "track_count": 10
      }
    ]
  }
}
```

**Retag group fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Group ID (the album's id) |
| `group_type` | string | Type of retag group (currently always `album`) |
| `artist_name` | string | Artist name for this retag group |
| `album_name` | string | Album name for this retag group |
| `image_url` | string? | Album cover image URL |
| `spotify_album_id` | string? | Spotify album ID for metadata lookup |
| `itunes_album_id` | string? | iTunes/Apple Music album ID for metadata lookup |
| `total_tracks` | int | Expected total tracks for the album |
| `release_date` | string? | Album release date (YYYY-MM-DD) |
| `created_at` | string | ISO 8601 timestamp when group was created |
| `track_count` | int | Computed: number of tracks currently in this group |

#### `GET /api/v1/retag/groups/<group_id>`

Get a retag group with its full track list. Tracks are ordered by disc number then track number.

```json
{
  "data": {
    "group": {
      "id": 1,
      "group_type": "album",
      "artist_name": "Radiohead",
      "album_name": "OK Computer",
      "image_url": "https://i.scdn.co/image/ab67616d0000b273c8b444df094c596ea9da41f6",
      "spotify_album_id": "6dVIqQ8qmQ5GBnJ9shOYGE",
      "itunes_album_id": "1097862703",
      "total_tracks": 12,
      "release_date": "1997-06-16",
      "created_at": "2026-03-04T10:00:00",
      "track_count": 12
    },
    "tracks": [
      {
        "id": 1,
        "group_id": 1,
        "track_number": 1,
        "disc_number": 1,
        "title": "Airbag",
        "file_path": "/downloads/Radiohead/OK Computer/01 - Airbag.flac",
        "file_format": "flac",
        "spotify_track_id": "6LgJvl0Xdtc73RJ1mN1a7Z",
        "itunes_track_id": null,
        "created_at": "2026-03-04T10:00:00"
      },
      {
        "id": 2,
        "group_id": 1,
        "track_number": 2,
        "disc_number": 1,
        "title": "Paranoid Android",
        "file_path": "/downloads/Radiohead/OK Computer/02 - Paranoid Android.flac",
        "file_format": "flac",
        "spotify_track_id": "6LgJvl0Xdtc73RJ1mN1a7Z",
        "itunes_track_id": "1097863062",
        "created_at": "2026-03-04T10:00:00"
      },
      {
        "id": 3,
        "group_id": 1,
        "track_number": 3,
        "disc_number": 1,
        "title": "Subterranean Homesick Alien",
        "file_path": "/downloads/Radiohead/OK Computer/03 - Subterranean Homesick Alien.flac",
        "file_format": "flac",
        "spotify_track_id": "3sFhbVuZGMAsmFSIFGVPgS",
        "itunes_track_id": null,
        "created_at": "2026-03-04T10:00:00"
      }
    ]
  }
}
```

**Retag track fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Track ID (the Re-tag finding's id) |
| `group_id` | int | Parent retag group ID (foreign key) |
| `track_number` | int? | Track number on disc |
| `disc_number` | int | Disc number (default 1) |
| `title` | string | Track title |
| `file_path` | string | Full file path to the downloaded audio file |
| `file_format` | string? | Audio format (`flac`, `mp3`, `opus`, etc.) |
| `spotify_track_id` | string? | Spotify track ID for metadata lookup |
| `itunes_track_id` | string? | iTunes/Apple Music track ID for metadata lookup |
| `created_at` | string | When the Re-tag finding was raised |

#### `DELETE /api/v1/retag/groups/<group_id>`

Dismiss a retag group: every pending Re-tag finding of that album.

```json
{
  "data": {
    "message": "Retag group 1 deleted."
  }
}
```

#### `DELETE /api/v1/retag/groups`

Dismiss every pending Re-tag finding; the message counts the groups (albums) cleared.

```json
{
  "data": {
    "message": "Cleared 5 retag groups."
  }
}
```

#### `GET /api/v1/retag/stats`

Get retag queue statistics.

```json
{
  "data": {
    "groups": 5,
    "tracks": 47,
    "artists": 3
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `groups` | int | Total number of retag groups in queue |
| `tracks` | int | Total number of tracks across all groups |
| `artists` | int | Number of distinct artists in the queue |

---


### Filled tags (web UI routes)

With **Fill empty tags automatically** on (`metadata_enhancement.fill_empty_tags`: `off`, `fill` (the default while post-processing writes tags) or `update`), SoulSync writes what the library knows into the tags a file left blank. These routes back the Tools page's **Filled Tags** card. Reading needs `admin.tools`; the POST routes are admin only.

| Route | What it does |
|-------|--------------|
| `GET /api/repair/tag-fills?days=7&limit=20` | `{mode, days, files, fields, covers, runs: [{batch_id, source, at, files, fields, covers, undone}], undo}`: what was filled over `days`, and the latest runs. `undo` is the state of a run being undone |
| `GET /api/repair/tag-fills/files?run=<batch_id>` | Every file one run filled: `{id, file_id, path, mode, status, fields: [{key, label, before, after}], cover, filled_at, undone_at, undo}` |
| `POST /api/repair/tag-fills/<id>/undo` | Puts one file's filled tags back to blank (a tag changed since is kept and named in `kept`). Undone tags are never filled again |
| `POST /api/repair/tag-fills/undo-run` | `{"run": "<batch_id>"}`. Undoes a whole run in the background (`202`); progress shows in `undo` |
| `POST /api/repair/tag-fills/run` | Runs a fill pass on the repair worker's next idle moment (`202`); `400` when the setting is off |

### ListenBrainz

Browse cached ListenBrainz playlists and their tracks. Playlists are cached locally when SoulSync pulls recommendations from ListenBrainz.

#### `GET /api/v1/listenbrainz/playlists`

List cached ListenBrainz playlists with optional type filtering and pagination.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | | Filter by playlist_type (e.g. `weekly-jams`, `weekly-exploration`) |
| `page` | int | 1 | Page number |
| `limit` | int | 50 | Items per page (max 200) |

```json
{
  "data": {
    "playlists": [
      {
        "id": 1,
        "playlist_mbid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "title": "Weekly Jams for user123",
        "creator": "listenbrainz",
        "playlist_type": "weekly-jams",
        "track_count": 50,
        "annotation_data": {
          "algorithm": "collaborative-filtering",
          "source_patch": "weekly-jams"
        },
        "last_updated": "2026-03-03T06:00:00",
        "cached_date": "2026-03-03T06:05:00"
      },
      {
        "id": 2,
        "playlist_mbid": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
        "title": "Weekly Exploration for user123",
        "creator": "listenbrainz",
        "playlist_type": "weekly-exploration",
        "track_count": 50,
        "annotation_data": {
          "algorithm": "collaborative-filtering",
          "source_patch": "weekly-exploration"
        },
        "last_updated": "2026-03-03T06:00:00",
        "cached_date": "2026-03-03T06:08:00"
      }
    ]
  },
  "pagination": { "page": 1, "limit": 50, "total": 8, "total_pages": 1, "has_next": false, "has_prev": false }
}
```

**ListenBrainz playlist fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Internal database ID |
| `playlist_mbid` | string | MusicBrainz playlist MBID (unique) |
| `title` | string | Playlist title as given by ListenBrainz |
| `creator` | string? | Playlist creator (usually `listenbrainz`) |
| `playlist_type` | string | Type: `weekly-jams`, `weekly-exploration`, etc. |
| `track_count` | int | Number of tracks in the playlist |
| `annotation_data` | object? | Parsed JSON annotation metadata from ListenBrainz |
| `last_updated` | string | ISO 8601 timestamp of last update from ListenBrainz |
| `cached_date` | string | ISO 8601 timestamp when SoulSync cached this playlist |

#### `GET /api/v1/listenbrainz/playlists/<playlist_id>`

Get a ListenBrainz playlist with its full track list. `playlist_id` can be the internal database ID (integer) or the MusicBrainz playlist MBID (UUID string). Tracks are returned in playlist order (by position).

```json
{
  "data": {
    "playlist": {
      "id": 1,
      "playlist_mbid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "title": "Weekly Jams for user123",
      "creator": "listenbrainz",
      "playlist_type": "weekly-jams",
      "track_count": 50,
      "annotation_data": {
        "algorithm": "collaborative-filtering",
        "source_patch": "weekly-jams"
      },
      "last_updated": "2026-03-03T06:00:00",
      "cached_date": "2026-03-03T06:05:00"
    },
    "tracks": [
      {
        "id": 1,
        "playlist_id": 1,
        "position": 1,
        "track_name": "Paranoid Android",
        "artist_name": "Radiohead",
        "album_name": "OK Computer",
        "duration_ms": 383000,
        "recording_mbid": "b3e2b7e0-a147-4b3c-8eab-fd90bfff7e74",
        "release_mbid": "a1c35a51-d102-4ce7-b7b0-8a4f68385bb2",
        "album_cover_url": "https://coverartarchive.org/release/a1c35a51-d102-4ce7-b7b0-8a4f68385bb2/front-250.jpg",
        "additional_metadata": {
          "artist_mbids": ["a74b1b7f-71a5-4011-9441-d0b5e4122711"],
          "caa_id": 12345678901,
          "caa_release_mbid": "a1c35a51-d102-4ce7-b7b0-8a4f68385bb2"
        }
      },
      {
        "id": 2,
        "playlist_id": 1,
        "position": 2,
        "track_name": "All I Need",
        "artist_name": "Radiohead",
        "album_name": "In Rainbows",
        "duration_ms": 226000,
        "recording_mbid": "c7d9e0f1-2345-6789-abcd-ef0123456789",
        "release_mbid": "d4e5f6a7-8901-2345-bcde-f67890123456",
        "album_cover_url": "https://coverartarchive.org/release/d4e5f6a7-8901-2345-bcde-f67890123456/front-250.jpg",
        "additional_metadata": {
          "artist_mbids": ["a74b1b7f-71a5-4011-9441-d0b5e4122711"],
          "caa_id": 23456789012,
          "caa_release_mbid": "d4e5f6a7-8901-2345-bcde-f67890123456"
        }
      },
      {
        "id": 3,
        "playlist_id": 1,
        "position": 3,
        "track_name": "Something About Us",
        "artist_name": "Daft Punk",
        "album_name": "Discovery",
        "duration_ms": 232000,
        "recording_mbid": "e8f9a0b1-c2d3-e4f5-6789-012345678901",
        "release_mbid": "f0a1b2c3-d4e5-f6a7-8901-234567890123",
        "album_cover_url": "https://coverartarchive.org/release/f0a1b2c3-d4e5-f6a7-8901-234567890123/front-250.jpg",
        "additional_metadata": {
          "artist_mbids": ["056e4f3e-d505-4dad-8ec1-d04f521cbb56"],
          "caa_id": 34567890123,
          "caa_release_mbid": "f0a1b2c3-d4e5-f6a7-8901-234567890123"
        }
      }
    ]
  }
}
```

**ListenBrainz track fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Internal track ID |
| `playlist_id` | int | Parent playlist ID (foreign key) |
| `position` | int | Position in the playlist (1-based) |
| `track_name` | string | Track title |
| `artist_name` | string | Artist name |
| `album_name` | string | Album name |
| `duration_ms` | int | Track duration in milliseconds |
| `recording_mbid` | string? | MusicBrainz recording MBID |
| `release_mbid` | string? | MusicBrainz release MBID |
| `album_cover_url` | string? | Album cover art URL (usually from Cover Art Archive) |
| `additional_metadata` | object? | Parsed JSON with extra MusicBrainz data (artist MBIDs, CAA info, etc.) |

---

### Cache

Browse internal enrichment caches. Useful for debugging enrichment issues, understanding match quality, or auditing what metadata has been resolved.

#### `GET /api/v1/cache/musicbrainz`

List cached MusicBrainz lookups with optional filtering and pagination. Results ordered by `last_updated` descending.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `entity_type` | string | | Filter by type: `artist`, `album`, `track` |
| `search` | string | | Filter by entity name (case-insensitive partial match) |
| `page` | int | 1 | Page number |
| `limit` | int | 50 | Items per page (max 200) |

```json
{
  "data": {
    "entries": [
      {
        "id": 42,
        "entity_type": "artist",
        "entity_name": "Radiohead",
        "artist_name": null,
        "musicbrainz_id": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
        "spotify_id": "4Z8W4fKeB5YxbusRsdQVPb",
        "itunes_id": "657515",
        "metadata_json": {
          "name": "Radiohead",
          "type": "Group",
          "country": "GB",
          "disambiguation": "English rock band",
          "begin_date": "1985",
          "end_date": null,
          "tags": ["alternative rock", "art rock", "experimental"]
        },
        "match_confidence": 95,
        "last_updated": "2026-03-01T12:00:00"
      },
      {
        "id": 88,
        "entity_type": "album",
        "entity_name": "OK Computer",
        "artist_name": "Radiohead",
        "musicbrainz_id": "b1f5a82e-5cfa-36f8-b084-4a93d7e7e608",
        "spotify_id": "6dVIqQ8qmQ5GBnJ9shOYGE",
        "itunes_id": "1097862703",
        "metadata_json": {
          "title": "OK Computer",
          "date": "1997-06-16",
          "country": "XE",
          "status": "Official",
          "packaging": "Jewel Case",
          "barcode": "724385522925"
        },
        "match_confidence": 100,
        "last_updated": "2026-02-28T08:30:00"
      },
      {
        "id": 215,
        "entity_type": "track",
        "entity_name": "Paranoid Android",
        "artist_name": "Radiohead",
        "musicbrainz_id": "b3e2b7e0-a147-4b3c-8eab-fd90bfff7e74",
        "spotify_id": "6LgJvl0Xdtc73RJ1mN1a7Z",
        "itunes_id": "1097863062",
        "metadata_json": {
          "title": "Paranoid Android",
          "length": 383000,
          "isrcs": ["GBAYE9700074"]
        },
        "match_confidence": 98,
        "last_updated": "2026-02-28T08:35:00"
      }
    ]
  },
  "pagination": { "page": 1, "limit": 50, "total": 1250, "total_pages": 25, "has_next": true, "has_prev": false }
}
```

**MusicBrainz cache fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Cache entry ID |
| `entity_type` | string | Entity type: `artist`, `album`, or `track` |
| `entity_name` | string | Name that was looked up |
| `artist_name` | string? | Associated artist name (for album/track lookups) |
| `musicbrainz_id` | string? | Resolved MusicBrainz MBID (`null` if no match) |
| `spotify_id` | string? | Cross-referenced Spotify ID |
| `itunes_id` | string? | Cross-referenced iTunes/Apple Music ID |
| `metadata_json` | object? | Parsed JSON with full MusicBrainz metadata (tags, dates, ISRCs, etc.) |
| `match_confidence` | int? | Match confidence score (0-100) |
| `last_updated` | string | ISO 8601 timestamp of last cache update |

> **Unique constraint:** `(entity_type, entity_name, artist_name)` — each entity is cached once per type+name combination.

#### `GET /api/v1/cache/musicbrainz/stats`

Get MusicBrainz cache statistics — total entries, matched vs unmatched, and breakdown by entity type.

```json
{
  "data": {
    "total": 1250,
    "matched": 1100,
    "unmatched": 150,
    "by_type": {
      "artist": 500,
      "album": 450,
      "track": 300
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `total` | int | Total cache entries |
| `matched` | int | Entries with a resolved `musicbrainz_id` |
| `unmatched` | int | Entries where no MusicBrainz match was found |
| `by_type` | object | Entry counts keyed by `entity_type` |

#### `GET /api/v1/cache/discovery-matches`

List cached discovery provider matches. When SoulSync discovers tracks from external sources (Tidal playlists, YouTube Music, ListenBrainz recommendations, Beatport), it resolves them against Spotify or iTunes to get downloadable metadata. These resolved matches are cached here to avoid redundant API calls. Results ordered by `last_used_at` descending.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | | Filter by target provider: `spotify` or `itunes` |
| `search` | string | | Filter by title or artist (case-insensitive partial match on both) |
| `page` | int | 1 | Page number |
| `limit` | int | 50 | Items per page (max 200) |

**Example — Spotify-resolved match** (e.g. a Tidal track matched to Spotify):

```json
{
  "data": {
    "entries": [
      {
        "id": 100,
        "normalized_title": "paranoid android",
        "normalized_artist": "radiohead",
        "provider": "spotify",
        "match_confidence": 0.95,
        "matched_data_json": {
          "id": "6LgJvl0Xdtc73RJ1mN1a7Z",
          "name": "Paranoid Android",
          "artists": ["Radiohead"],
          "album": {
            "id": "6dVIqQ8qmQ5GBnJ9shOYGE",
            "name": "OK Computer",
            "album_type": "album",
            "release_date": "1997-06-16",
            "total_tracks": 12,
            "images": [
              { "url": "https://i.scdn.co/image/ab67616d0000b273c8b444df094c596ea9da41f6", "height": 640, "width": 640 },
              { "url": "https://i.scdn.co/image/ab67616d00001e02c8b444df094c596ea9da41f6", "height": 300, "width": 300 },
              { "url": "https://i.scdn.co/image/ab67616d00004851c8b444df094c596ea9da41f6", "height": 64, "width": 64 }
            ]
          },
          "duration_ms": 383000,
          "external_urls": {
            "spotify": "https://open.spotify.com/track/6LgJvl0Xdtc73RJ1mN1a7Z"
          },
          "source": "spotify"
        },
        "original_title": "Paranoid Android",
        "original_artist": "Radiohead",
        "created_at": "2026-02-15T10:00:00",
        "last_used_at": "2026-03-04T09:00:00",
        "use_count": 12
      },
      {
        "id": 205,
        "normalized_title": "something about us",
        "normalized_artist": "daft punk",
        "provider": "itunes",
        "match_confidence": 0.92,
        "matched_data_json": {
          "id": "724633277",
          "name": "Something About Us",
          "artists": ["Daft Punk"],
          "album": {
            "name": "Discovery",
            "album_type": "album",
            "images": [
              { "url": "https://is1-ssl.mzstatic.com/image/thumb/Music125/v4/a1/b2/c3/disc-cover.jpg/300x300bb.jpg", "height": 300, "width": 300 }
            ]
          },
          "source": "itunes"
        },
        "original_title": "Something About Us",
        "original_artist": "Daft Punk",
        "created_at": "2026-02-20T14:30:00",
        "last_used_at": "2026-03-03T18:00:00",
        "use_count": 5
      }
    ]
  },
  "pagination": { "page": 1, "limit": 50, "total": 3200, "total_pages": 64, "has_next": true, "has_prev": false }
}
```

**Discovery match cache fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Cache entry ID |
| `normalized_title` | string | Lowercase normalized track title used for matching |
| `normalized_artist` | string | Lowercase normalized artist name used for matching |
| `provider` | string | Target provider the track was matched against (`spotify` or `itunes`) |
| `match_confidence` | float | Match confidence score (0.0-1.0). Only matches ≥ 0.70 are cached. |
| `matched_data_json` | object | Parsed JSON with full resolved track data (structure varies by provider — see below) |
| `original_title` | string? | Original (un-normalized) track title from the discovery source |
| `original_artist` | string? | Original (un-normalized) artist name from the discovery source |
| `created_at` | string | ISO 8601 timestamp when match was first cached |
| `last_used_at` | string | ISO 8601 timestamp when match was last reused |
| `use_count` | int | Number of times this cached match has been reused |

**`matched_data_json` structure by provider:**

| Field | Spotify | iTunes | Description |
|-------|---------|--------|-------------|
| `id` | string | string | Spotify track ID or iTunes track ID |
| `name` | string | string | Resolved track name |
| `artists` | string[] | string[] | Artist names (normalized to string array before caching) |
| `album` | object | object | Album data (see below) |
| `album.id` | string | — | Spotify album ID (not present for iTunes) |
| `album.name` | string | string | Album name |
| `album.album_type` | string | string | Album type (`album`, `single`, `compilation`) |
| `album.release_date` | string | — | Release date (Spotify only) |
| `album.total_tracks` | int | — | Track count (Spotify only) |
| `album.images` | array | array | Cover art: `[{url, height, width}]` (Spotify provides 3 sizes; iTunes provides 1) |
| `duration_ms` | int | — | Track duration in milliseconds (Spotify only) |
| `external_urls` | object | — | External URLs e.g. `{"spotify": "https://..."}` (Spotify only) |
| `source` | `"spotify"` | `"itunes"` | Which provider this match came from |

> **Unique constraint:** `(normalized_title, normalized_artist, provider)` — each title+artist+provider combination is cached once. Discovery sources (Tidal, YouTube, ListenBrainz, Beatport) are the input — the `provider` field records which catalog they were matched against.

#### `GET /api/v1/cache/discovery-matches/stats`

Get discovery match cache statistics — total entries, total reuses, average confidence, and breakdown by provider.

```json
{
  "data": {
    "total": 3200,
    "total_uses": 15000,
    "avg_confidence": 0.872,
    "by_provider": {
      "spotify": 2800,
      "itunes": 400
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `total` | int | Total cached match entries |
| `total_uses` | int | Sum of all `use_count` values across entries |
| `avg_confidence` | float? | Average match confidence (rounded to 3 decimals, `null` if empty) |
| `by_provider` | object | Entry counts keyed by provider name |

---

### Settings

#### `GET /api/v1/settings`

Get current settings. Sensitive values (passwords, tokens, secrets) are redacted.

#### `PATCH /api/v1/settings`

Update settings (partial update). Uses dot-notation keys.

```json
{
  "soulseek.search_timeout": 90,
  "logging.level": "DEBUG"
}
```

**Response:**

```json
{
  "data": {
    "message": "Settings updated.",
    "updated_keys": ["soulseek.search_timeout", "logging.level"]
  }
}
```

---

### Video

The movies and TV side. Every endpoint runs the same handler the web UI uses and wraps it in the v1 envelope. Returns `503 NOT_AVAILABLE` on a server with the video side disabled.

#### `GET /api/v1/video/library`

Titles in the video library. `kind=movies|shows` (default `movies`), plus `search`, `letter`, `sort`, `status`, `genre`, `resolution`, `page`, `limit`. When `kind=shows`, the response also includes `total_episodes` — the global episode count across all shows.

#### `GET /api/v1/video/library/genres`

#### `GET /api/v1/video/<kind>/<id>/library`

What you own of one library movie or show (`kind=movie|show`, `id` is the library id). Every file with its resolution, quality, codecs, audio, size, runtime and path, whether SoulSync can reach it, the subtitles beside it and whether its name matches your naming template; the title's match (`tmdb_id`, `imdb_id`, `tvdb_id`, `fixed`); and the quality verdict against its profile. For a show, every season with `owned`, `missing`, `upcoming`, `size_bytes`, `res_mix` and `monitored`, plus the folders the show lives in (each with `root`, `name`, `files`, `size_bytes` and `main` on the fullest). `extra_copies` (`titles`, `files`, `size_bytes`) counts the copies beyond the best one of each movie or episode, a file SoulSync downloaded carries `source` (`release`, `via`, `indexer`, `at`), and `below_cutoff` marks a file under the profile's target. `?season=<n>` adds the disk checks (reachability, subtitles, naming) for that season's files; without it a show is a database read.

#### `POST /api/v1/video/<kind>/<id>/identify`

Fix match. Body: `{"tmdb_id": 841}`. Re-points the library title to that TMDB entry, fills the imdb / tvdb ids TMDB gives for it, takes TMDB's title and year when your server named it differently, and locks the ids so a scan can't switch it back. Returns the new `tmdb_id`, `title` and `year`. `409` (with nothing changed) when TMDB doesn't answer for that id.

#### `POST /api/v1/video/check`

Badge status for a page of titles by TMDB id, in one call. Body: `{"items": [{"kind": "movie", "tmdb_id": 603}, {"kind": "show", "tmdb_id": 95396, "season": 1, "episode": 2}]}` (up to 300). Answers `data.items` in the order asked, each `{kind, tmdb_id, in_library, on_wishlist, following}`; `following` is the show watchlist as the Watchlist page shows it (your follows plus airing library shows you haven't muted), `null` for a movie. An item with `season` and `episode` adds `episode_in_library` and `episode_on_wishlist`. A kids profile gets `null` for titles above its rating cap. No TMDB calls. `400 BAD_REQUEST` when no item is usable.

#### `GET /api/v1/video/search?q=`

TMDB multi-search over movies, shows and people.

#### `GET /api/v1/video/trending`

#### `GET /api/v1/video/wishlist`

`kind=movie|show` for a page of items (`search`, `sort`, `page`, `limit`); no `kind` returns counts only.

#### `GET /api/v1/video/wishlist/counts`

#### `POST /api/v1/video/wishlist`

Add a movie, or a set of a show's episodes:

```json
{"movie": {"tmdb_id": 603, "title": "The Matrix", "year": 1999}}
```

```json
{"show": {"tmdb_id": 1396, "title": "Breaking Bad"},
 "episodes": [{"season_number": 1, "episode_number": 1}, {"season_number": 1, "episode_number": 2}]}
```

#### `DELETE /api/v1/video/wishlist`

Body: `{"scope": "movie|show|season|episode", "tmdb_id": 603, "season_number"?: 1, "episode_number"?: 1}`.

#### `GET /api/v1/video/watchlist`

Followed shows, people and studios.

#### `POST /api/v1/video/watchlist`

Body: `{"kind": "show|person|studio", "tmdb_id": 1396, "title": "Breaking Bad"}`.

#### `DELETE /api/v1/video/watchlist`

Body: `{"kind": "show", "tmdb_id": 1396}`.

#### `POST /api/v1/video/scan`

Ask for a library scan. Body: `{"mode"?: "incremental|deep|full"}`. `409 CONFLICT` while one is running.

#### `GET /api/v1/video/scan/status`

#### `GET /api/v1/video/stats`

One call for a dashboard widget. `library` (`movies`, `shows`, `episodes` on disk, `episodes_missing` = monitored and aired with no file, `size_bytes`), scoped to the video server; `database` (`size_mb`, `last_update`, `last_scan`, ISO UTC); `downloads` (`active`, `failed`, `completed`, `cancelled`, `by_status`, `speed_bps`, `completed_all_time`); `wishlist` (`movies`, `episodes`, `videos`, `total`).

#### `GET /api/v1/video/downloads`

Video downloads, active rows first then newest (the latest 100). `status` filters by one status or a comma list: `queued`, `searching`, `downloading`, `importing`, `failed`, `import_failed`, `completed`, `cancelled`, plus `active` and `failed` as shorthand for their groups. A filtered answer adds `count`. An unknown status is `400 BAD_REQUEST`.

#### `GET /api/v1/video/downloads/status`

#### `GET /api/v1/video/downloads/history`

#### Who may do what on the video downloads (web UI session API)

- `GET /api/video/downloads/active`, `/downloads/history` and `/downloads/status?id=`: a member gets the grabs they started, an admin everyone's. Queue rows carry `can_cancel`.
- `POST /api/video/downloads/cancel`: an admin any download, a member only one they started (`403` otherwise). An automatic grab has no owner, so it's the admin's.
- `POST /downloads/clear`, `/downloads/remove`, `/downloads/recycle/restore`, `/downloads/recycle/purge`, `/downloads/history/clear`, `DELETE /downloads/history/{id}`, `POST /scan/request`, `/scan/stop`, `/scan/server`, and writes to `/discover/languages` and `/discover/providers-pref`: admin only.
- `POST /youtube/follow`, `/youtube/playlist/follow`, `/youtube/subscriptions/import` and `/youtube/channel/{id}/settings`: need download rights.
- `GET /youtube/library/{channel_id}`: a channel's downloaded files (size, folder, quality), totals, retention, and what needs attention. `POST /youtube/library/{channel_id}/delete` `{video_ids: [...] | all: true, forget?: bool}` deletes through the recycle bin (kept known, or forgotten so it downloads again) and returns undo items; `POST .../undo` `{undo: [...]}` puts them back; `POST .../clean` applies the channel's retention now. All admin only.
- A kids profile's rating cap holds on requests, wishlist and watchlist adds, manual search and grab, and the title reads (acquisition, history, title status, posters).
- The video request limit counts every ask made in its window, so withdrawing or clearing one doesn't hand it back.

#### Release ranking (web UI session API)

The video search the web UI runs (`POST /api/video/downloads/search`, `/downloads/search/start` and `GET /downloads/search/poll`) returns each result with:

- `breakdown`: every factor that scored it, `[{group, label, points}]`. `group` is `quality`, `formats` or `tiebreak`.
- `rank`: the values it's compared on, best first: `[accepted, quality, formats, tiebreak, size_fit, availability, size_bytes]`. Results come back sorted by it, and the wishlist, RSS sync, upgrades and download retries sort by the same values.
- `score` is still the total of the three groups, and `format_score` / `formats` are unchanged.

A `query` in the search body (`/downloads/search/start`, `/downloads/search`) is run as typed on every source: one text search, no ids or structured search added. Leave it out for the usual search.

A show's search (#2233) takes `scope` `series` (complete packs), `season` (with `season`) or `episode` (with `season` and `episode`):

- `with_episodes: true` on a `season` search (a `with_episodes=1` query parameter on the poll) judges that season's single episodes as themselves instead of rejecting them as not a season pack, so packs and episodes come back together.
- each result of a show in your library carries `cover: {have, total}`: the episodes it covers by the library's episode list and how many of them you already have. A Soulseek pack's `files` each get `have: true` for the ones you have.
- an `episode` search fills in the show's `series_type`, `air_date`, `absolute` and scene numbering (`alt_season`, `alt_episode`) from the library when the body doesn't send them, and searches and judges by them like the wishlist does.

The quality profile (`GET/POST /api/video/downloads/quality` and the named profiles) adds `preferred_movie_gb` and `preferred_episode_gb`: a tie-break, closer ranks higher, in tenths of a GB, `0` = off (the default), clamped to the max size.

#### Video libraries (web UI session API, admin)

Named movie and TV libraries, each with its own folder (#2029). The main and 4K folders are libraries too (`builtin` `main` / `4k`); their folders stay in the folder settings (`movies_path`, `movies_4k_path` + `movies_4k_enabled`, ...).

- `GET /api/video/libraries/folders`: `{libraries: [...], genres: [...]}`. Each library: `id`, `kind` (`movie` / `show`), `name`, `path`, `builtin` (`main`, `4k` or null), `enabled`, `usable` (on and has a folder), `is_default`, `rules` (`{genres: [], keywords: []}`) and `options`. `genres` are the genre names your library knows, for the rule picker.
- `POST /api/video/libraries/folders`: add one. Body: `{kind, name, path, rules?, enabled?, is_default?}`. `400` with a message for a missing name or folder, or a folder another library (or the anime / YouTube folder) already uses.
- `PUT /api/video/libraries/folders/<id>`: change `name`, `rules`, `options`, and for a custom library `path` and `enabled`. A builtin's folder is changed in its own setting (`400`). `is_default: true` makes it the default.
- `POST /api/video/libraries/folders/<id>/default`: the default for its kind (needs a folder and to be on).
- `DELETE /api/video/libraries/folders/<id>`: remove a custom library. Its files stay; titles picked for it go back to the rules and the default; a removed default hands the kind to the main library. The builtins can't be removed (`400`).

**The library manager** (admin, the web UI's own routes under `/api/video/manager`):

- `GET /titles?kind=movies|shows&page=&limit=&sort=&dir=` and `GET /files`: one page of rows. Filters: `search`, `needs` (comma list of `missing`, `below_cutoff`, `copies`, `unmatched`, `no_subtitles`, `unprobed`, `unmonitored`, `unwatched`, `not_owned`), `status`, `monitored`, `locked`, `resolution`, `codec`, `hdr`, `audio_language`, `subtitle_language`, `drive`, `genre`, `video_library`, `quality_profile`, `year_from`, `year_to`. `GET /ids` returns every id the filters match, `GET /facets` what there is to filter by.
- `GET /rename-preview`, `GET /delete-preview`, `GET /move-preview?kind=&ids=&library_id=`: what the operation would do. Nothing is changed.
- `POST /api/video/bulk/start` body `{kind, ids, action, params}` runs one in the background; `GET /api/video/bulk/status` follows it. Actions: `monitored`, `watched`, `content_rating`, `genre_add`, `genre_remove`, `refresh_art`, `quality_profile`, `rename`, `wish` (`mode`: `missing` / `upgrade`), `subtitles`, `rematch`, `series_type`, `lock` (`value`: true / false), `move` (`library_id`), `delete` (`confirm`: the number of ids). The status carries `held` (padlocked titles left alone) and `batch` (the file changes of a rename or move, for undo).
- `GET /history?kind=&id=&limit=&offset=`: file changes, newest first, for one title or all. `POST /history/<id>/undo` puts one back; `POST /history/undo-batch` body `{batch}` puts back a whole run. `409` with a plain reason when it can't be (the file moved on, the old path is taken, the recycle folder was emptied).
- `GET /views` and `PUT /views` body `{views: [{name, state}]}`: the saved views.
- `GET /libraries?kind=`: each usable library with `folders: [{path, primary, free_bytes}]`. A library can sit on many drives: the main library is its own folder plus the additional folders and the mapped server folders of its kind, its own folder first. `GET /move-preview` and the `move` action take `folder` (one of that library's folders, `409` otherwise) to move to that drive; without it the library's own folder is used.
- `GET /move-preview` takes `by_hand=1`, and the `move` action takes `params.by_hand: true`, for one title moved from its own page: a padlock holds a sweep, not that.
- `GET /episodes/<show_id>`: seasons, episodes and the files under each. `POST /renumber/<show_id>` body `{file_ids, season, start_episode, apply?}`: files the chosen files under consecutive episodes of that season. Without `apply` it answers with the plan.

**Keeping a title current** (any profile that can see the title; `force` is admin only):

- `GET /api/video/libraries`: the server's `movies`, `tv` and `home` libraries (`[{title}]`; `home` are plex "other videos" / jellyfin "home videos" libraries, with `count`) and `selected`: `{movies, movies_more, tv, tv_more, anime, home_more}`. `movies` / `tv` are the main picks; `movies_more` / `tv_more` the other libraries every scan reads too; `anime` the TV libraries whose shows are anime; `home_more` the home video libraries read into the player's Home videos (never matched). `POST /api/video/libraries` saves the same shape; a list left out keeps what was saved.
- `GET /api/video/server`: `{server, plex, jellyfin}`. `server` is `plex`, `jellyfin`, `soulsync` (Standalone: SoulSync scans its own Movies and TV folders, no media server) or null. With no server configured, folders set and no server-built library in the database it is `soulsync` on its own. `POST /api/video/server` body `{server: plex|jellyfin|soulsync}` saves the pick.
- Rows SoulSync makes itself (a download that just landed, a Standalone scan) have a `server_id` starting `ss:`, made from the file's place on disk. A media server scan that reports the same title (by TMDB, TVDB or IMDb id) takes the row over, keeping its id. `POST .../sync` on such a row the server doesn't list yet answers `waiting: true` and removes nothing while its file is on disk. A title a Standalone scan couldn't identify for sure has `tmdb_match_status` `review`: it is never auto-matched. A library file carries `edition` (`"Director's Cut"`, read off the file name) when it states one. A `poster_url` starting `local:` is a poster file in the title's own folder; `/api/video/poster/...` serves it only when it is an image inside a video library folder.
- `GET /api/video/server/links`: Plex and Jellyfin as one library. `{enabled, server, other, movies, movies_total, shows, shows_total, last}`: `other` is the connected server that isn't the video server (null with one server), the counts are how much of the library that server has too, `last` is the last pass (`state` `idle|running|done|error`, with `movies`, `shows`, `episodes`, `unmatched` titles, `unmatched_count` and `watched: {to_library, to_other, failed}` when done). `POST /api/video/server/links` body `{enabled?, run?}` switches it and/or reads the other server now in the background (`started`). With linking on, `POST /api/video/server` answers `moved: {movies, shows, episodes, skipped}`: the linked titles that kept their rows under the new server.
- `GET /api/video/detail/<movie|show>/<id>/library` carries `extras`: the trailers, featurettes and other extras in the title's own folder, `[{key, kind, name, filename, size_bytes}]` (`kind` is `Trailer`, `Featurette`, `Behind the Scenes`, `Deleted Scene`, `Interview`, `Scene`, `Short` or `Other`). `GET /api/video/detail/<movie|show>/<id>/extras/<key>` streams one (Range requests answered, so it seeks). Only a key that listing gave is served; a kids profile over the title's rating cap is refused.
- `POST /api/video/detail/<movie|show>/<id>/freshen` body `{force?}`: asks the sources whose answer has gone stale and returns `{changed, asked: [source...], notes: {source: reason}, gone, moved_to, sources: {server|metadata|ratings: {age_seconds, ttl_seconds, stale}}}`. `server` re-reads that one title from the media server (files, watch state), `metadata` asks the provider for what changes after a match, `ratings` asks for the scores. `changed` is true only when something the page shows moved. `gone` means the media server no longer has the title; nothing is removed (`POST .../sync` does that). `moved_to` is the title's new row when the server re-keyed it. A second call while one is running answers `running: true` and asks nothing.
- `GET` on the same path: `{sources}` only.
- Clocks: the server every 10 minutes; metadata daily and ratings every 3 days for a show that has not ended or a movie within 90 days of its release, otherwise 14 and 30 days. A source that could not answer waits out its clock, unless a library scan or a spent ratings quota was in the way.

**Managing files from a title's page** (admin):

- `GET /api/video/files/rename-preview?file_ids=1,2,3`: `{files, to_rename, already_named, unreachable, changes: [{file_id, current, expected}], more}`. Nothing is renamed.
- `GET /api/video/files/delete-preview?file_ids=1,2,3`: `{files, size_bytes, unreachable, emptied, recycle, items: [{file_id, label, filename, size_bytes, reachable}], more}`. `emptied` is how many episodes or movies would be left with no copy. A file SoulSync can't reach is listed and is not counted as going.
- `POST /api/video/files/bulk` body `{action: rename|delete, file_ids, kind, id?, stop_hunting?}`: answers with `batch` (what `POST /api/video/manager/history/undo-batch` puts back). With `stop_hunting` on a delete, the episodes left with no copy stop being monitored, and `unmonitored` says how many.
- `POST /api/video/files/<id>/rename`, `/delete` (body `{stop_hunting?}`) and `/reassign` answer with `batch` too, when something changed that can be put back.
- `POST /api/video/detail/show/<id>/episodes/monitor` body `{episode_ids, monitored}`: hunt, or stop hunting, a picked set of that show's episodes. `404` when none of them are that show's.
- `POST /api/video/detail/<movie|show>/<id>/padlock` body `{locked}`: the padlock on one title. `GET /api/video/detail/<kind>/<id>/library` carries `locked`.

Which library a new title files into: a library picked for the grab (`video_library_id` on `POST /api/video/downloads/grab` and the manual import), else one picked for the title, else the 4K library when its quality profile saves to 4K, else the first library whose rules match its genres or TMDB keywords (a keyword also matches inside its name), else the kind's default. The anime folder still takes anime shows unless a library was picked for the title.

- `GET /api/video/downloads/quality/for` adds `libraries` (`[{id, name, builtin}]`, the usable ones of the title's kind), `library` (`{id, name, why}`, where it files now; `why` is `picked`, `assigned`, `profile`, `rule: ...` or `default`) and `library_assigned` (the title's own pick, `0` = none).
- A library's `sources` (ordered, any of `soulseek`, `usenet`, `torrent`, `extto`; `[]` = the global order), `torrent_category` and `usenet_category` (`''` = the client's own; letters, digits, space, `.`, `_`, `-`, up to 64) set where its automatic searches look and the category its client jobs get.
- A library's `indexer_ids` (Prowlarr indexer ids, `[]` = every one) limit its searches: the wishlist, search now, retries, manual search and RSS sync.
- A library's `quality_profile_id` (on create / `PUT`, `0` = none) is the profile titles in it are judged by when they have none of their own, before the kind's default.
- `POST /api/video/downloads/quality/copies` (admin): want a second copy of a movie in another library. Body: `{media_id, media_source, tmdb_id, library_id, title, year?, poster_url?, quality_profile_id?}`. Adds its own wishlist row there, judged by that library's default profile. `400` for the library the title already files into. `DELETE` with `{media_id, media_source, tmdb_id, library_id}` stops wanting it (`404` when there's no such copy). `GET /downloads/quality/for` lists a movie's `copies` as `[{library_id, name, wished, owned}]`.
- Wishlist movie rows (`GET /api/video/wishlist?kind=movie`) carry `video_library_id` (null = the title's own row) and `video_library_name`. `POST /api/video/wishlist/remove`, `/wishlist/search` and `/wishlist/retry` take an optional `video_library_id` for a movie (`0` = the title's own row only); removing a copy's row stops wanting that copy. Left out, they act on every row as before.
- A library's `auto_search` (default `true`; `false` = the drain and RSS sync skip its titles) and `upgrades` (default `true`; `false` = owned titles there aren't upgraded).
- `GET /api/video/library/video-libraries`: `{libraries: [{id, name, kind}]}`, the usable ones, readable by any profile with video access (for the filters). `GET /api/video/library?kind=movies|shows&video_library=<id>` lists only titles whose files sit in that library. `GET /api/video/wishlist?kind=movie|show&video_library=<id>` lists only wishes that file there (a copy's library, else the title's pick, else where its last search filed it). `GET /api/video/downloads/active` rows carry `video_library_id` / `video_library_name` (by target folder).
- `POST /api/video/downloads/quality/library` (admin): pick a title's library. Body: `{kind, media_id, media_source, tmdb_id, library_id}`, `library_id: 0` clears the pick. Answers with the same three fields.

**Shelf Life** (admin only, every route under `/api/video/repair` is):

- `POST /api/video/repair/shelf-life/plan` body `{target_gb?, ids?}`: the titles a cleanup would delete, lowest score first. With `target_gb` it stops once the total reaches it and never reaches over the line to make the number; with `ids` (finding ids) it plans exactly those. Every open finding is checked again as it plans (gathered and scored with the job's settings as they are now), and one that a gate holds or that no longer scores under today's threshold is left out. Answers `{items: [{id, kind, item_id, title, year, score, size_bytes}], total_bytes, target_bytes, short_bytes, candidates, skipped: [{id, title, year, reason}], threshold, enabled, recycling, keep_days, phrase}`. `score` is the fresh one and `size_bytes` is every copy the delete takes. While the job is switched off, `items` is empty and `error` says so. `400` for a `target_gb` that isn't a number over 0, or `ids` that isn't a list.
- `POST /api/video/repair/shelf-life/delete` body `{ids, phrase}`: delete a batch in the background, each title through Delete Title (to the recycle bin). `phrase` must be exactly `delete N titles` (`delete 1 title` for one) with N the number of ids. Every id must be an open Shelf Life finding, the job must be on, and every title is checked again first: if any no longer holds, nothing starts and the `400` names each one and why. Answers `{started, total}`; follow it on `GET /api/video/repair/bulk-fix/status`. Right before each delete the server is asked again whether anyone is playing it.
- `GET /api/video/repair/shelf-life/<movie|show>/<id>`: one title's stored result for its page: `{scored, score, gate, gate_text, reasons: [{signal, text}], scored_at, threshold, enabled, keep, can_keep}`. `404` when it isn't in the library.
- `POST /api/video/repair/shelf-life/<movie|show>/<id>/keep` body `{keep: true|false}`: mark the title Keep (held by its TMDB id, so it survives a rescan) or take it off. Keep retires its open finding at once. `400` when `keep` isn't a boolean or the title has no TMDB match yet.
- A single finding is approved with `POST /api/video/repair/findings/<id>/fix` body `{fix_action: "<the title, typed exactly>"}`. The generic bulk fix routes refuse a single `fix_action` for Shelf Life findings: one typed title never confirms more than one title.

#### `GET /api/v1/video/calendar`

Upcoming and recent episodes and releases. `start` and `end` as ISO dates.

For a calendar app, use the iCal feed instead: `GET /api/video/calendar/feed/<token>.ics`. The token is per profile and is the credential, so the feed needs no session or API key. Get the link from the calendar page (⋯ → Subscribe), or `GET /api/video/calendar/feed-url` from a signed-in session; `POST` to the same path makes a new token and retires the old one. The feed takes `days` (1–60, default 14), `scope=watchlist|all`, `movies=0` and `specials=1`, and applies that profile's video access and rating limits.

#### `GET /api/v1/video/requests`

#### `POST /api/v1/video/requests`

File a request for an admin to approve. Body: `{"kind": "movie|show", "tmdb_id": 27205, "title": "Inception", "year"?: 2010, "note"?: "..."}`.

#### `POST /api/v1/video/requests/<id>/approve`

Optional body: `{"acquisition": "auto" | "manual"}` decides how this one gets its release. Left out, the kind's setting decides (see below). `manual` holds the request until someone picks a release. The answer says which way it went: `"acquisition": "auto" | "manual"`.

#### `POST /api/v1/video/requests/<id>/deny`

---

### API Key Management

#### `GET /api/v1/api-keys`

List all API keys (shows prefix and label only, never the full key).

```json
{
  "data": {
    "keys": [
      {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "label": "Discord Bot",
        "key_prefix": "sk_a3Bf9x2",
        "created_at": "2026-03-01T12:00:00",
        "last_used_at": "2026-03-04T09:15:00"
      }
    ]
  }
}
```

#### `POST /api/v1/api-keys`

Generate a new API key. The raw key is returned **once** — save it immediately.

```json
{
  "label": "Discord Bot"
}
```

**Response:**

```json
{
  "data": {
    "key": "sk_a3Bf9x2Kp7Qm4Rn8Yt6Wv0Xz1Cb5Dj9Fg",
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "label": "Discord Bot",
    "key_prefix": "sk_a3Bf9x2",
    "created_at": "2026-03-04T10:00:00"
  }
}
```

#### `DELETE /api/v1/api-keys/<key_id>`

Revoke an API key by its UUID.

#### `POST /api/v1/api-keys/bootstrap`

Generate the first API key when none exist. No API key needed, but the caller must be the admin: a signed-in admin session, or any request on a single-profile install with no login or launch PIN. Returns `403` if keys already exist or the caller isn't the admin.

```json
{
  "label": "My First Key"
}
```

---

## Field Filtering

All library, watchlist, wishlist, and discovery endpoints support the `?fields=` parameter to request only specific fields. This reduces response size when you only need a few fields.

```
GET /api/v1/library/artists/42?fields=id,name,genres,spotify_artist_id
```

```json
{
  "data": {
    "artist": {
      "id": 42,
      "name": "Radiohead",
      "genres": ["alternative rock", "art rock"],
      "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb"
    }
  }
}
```

---

## Examples

### Python

```python
import requests

API_URL = "http://localhost:8008/api/v1"
API_KEY = "sk_your_key_here"

headers = {"Authorization": f"Bearer {API_KEY}"}

# Get full artist details with all enrichment metadata
artist = requests.get(f"{API_URL}/library/artists/42", headers=headers).json()
print(f"Artist: {artist['data']['artist']['name']}")
print(f"Spotify: {artist['data']['artist']['spotify_artist_id']}")
print(f"MusicBrainz: {artist['data']['artist']['musicbrainz_id']}")
print(f"Albums: {len(artist['data']['albums'])}")

# Get a specific album with tracks
album = requests.get(f"{API_URL}/library/albums/87", headers=headers).json()
for track in album["data"]["tracks"]:
    print(f"  {track['track_number']}. {track['title']} ({track['duration']}ms)")

# Look up by Spotify ID
result = requests.get(f"{API_URL}/library/lookup",
    headers=headers,
    params={"type": "artist", "provider": "spotify", "id": "4Z8W4fKeB5YxbusRsdQVPb"}
).json()

# Browse genres
genres = requests.get(f"{API_URL}/library/genres", headers=headers).json()
for g in genres["data"]["genres"][:10]:
    print(f"  {g['name']}: {g['count']} artists")

# Recently added albums
recent = requests.get(f"{API_URL}/library/recently-added?type=albums&limit=10",
    headers=headers).json()

# Search external sources
search = requests.post(f"{API_URL}/search/tracks",
    headers=headers,
    json={"query": "Daft Punk", "limit": 5})

# Add to watchlist (as profile 2)
requests.post(f"{API_URL}/watchlist",
    headers={**headers, "X-Profile-Id": "2"},
    json={"artist_id": "4tZwfgrHOc3mvqYlEYSvnL", "artist_name": "Daft Punk"})

# Update watchlist filters
requests.patch(f"{API_URL}/watchlist/4tZwfgrHOc3mvqYlEYSvnL",
    headers=headers,
    json={"include_live": True, "include_remixes": True})

# Get discovery pool
pool = requests.get(f"{API_URL}/discover/pool?limit=50", headers=headers).json()

# Get only specific fields to reduce payload
minimal = requests.get(
    f"{API_URL}/library/artists?fields=id,name,thumb_url&limit=100",
    headers=headers).json()
```

### JavaScript

```javascript
const API_URL = 'http://localhost:8008/api/v1';
const API_KEY = 'sk_your_key_here';

const headers = {
  'Authorization': `Bearer ${API_KEY}`,
  'Content-Type': 'application/json'
};

// Browse library artists
const artists = await fetch(`${API_URL}/library/artists?page=1&limit=25`, { headers })
  .then(r => r.json());

// Get album with full metadata and tracks
const album = await fetch(`${API_URL}/library/albums/87`, { headers })
  .then(r => r.json());

// Look up by external ID
const lookup = await fetch(
  `${API_URL}/library/lookup?type=track&provider=spotify&id=6LgJvl0Xdtc73RJ1mN1a7Z`,
  { headers }
).then(r => r.json());

// Trigger watchlist scan
await fetch(`${API_URL}/watchlist/scan`, { method: 'POST', headers });

// Get discovery similar artists for profile 2
const similar = await fetch(`${API_URL}/discover/similar-artists?limit=20`, {
  headers: { ...headers, 'X-Profile-Id': '2' }
}).then(r => r.json());
```

### curl

```bash
# System status
curl -H "Authorization: Bearer sk_..." http://localhost:8008/api/v1/system/status

# Get artist with full metadata
curl -H "Authorization: Bearer sk_..." \
  http://localhost:8008/api/v1/library/artists/42

# Get album with tracks
curl -H "Authorization: Bearer sk_..." \
  http://localhost:8008/api/v1/library/albums/87

# Get single track
curl -H "Authorization: Bearer sk_..." \
  http://localhost:8008/api/v1/library/tracks/512

# Look up by Spotify ID
curl -H "Authorization: Bearer sk_..." \
  "http://localhost:8008/api/v1/library/lookup?type=artist&provider=spotify&id=4Z8W4fKeB5YxbusRsdQVPb"

# Browse genres
curl -H "Authorization: Bearer sk_..." \
  http://localhost:8008/api/v1/library/genres

# Recently added albums
curl -H "Authorization: Bearer sk_..." \
  "http://localhost:8008/api/v1/library/recently-added?type=albums&limit=10"

# Search external tracks
curl -X POST http://localhost:8008/api/v1/search/tracks \
  -H "Authorization: Bearer sk_..." \
  -H "Content-Type: application/json" \
  -d '{"query": "Boards of Canada", "limit": 5}'

# Watchlist with profile
curl -H "Authorization: Bearer sk_..." \
  -H "X-Profile-Id: 2" \
  http://localhost:8008/api/v1/watchlist

# Update watchlist filters
curl -X PATCH http://localhost:8008/api/v1/watchlist/4tZwfgrHOc3mvqYlEYSvnL \
  -H "Authorization: Bearer sk_..." \
  -H "Content-Type: application/json" \
  -d '{"include_live": true, "include_remixes": true}'

# Discovery pool
curl -H "Authorization: Bearer sk_..." \
  "http://localhost:8008/api/v1/discover/pool?limit=50&new_releases_only=true"

# Field filtering — only get id, name, and Spotify ID
curl -H "Authorization: Bearer sk_..." \
  "http://localhost:8008/api/v1/library/artists?fields=id,name,spotify_artist_id&limit=100"
```

---

## Endpoint Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| **System** | | |
| GET | `/system/status` | Server status and service connectivity |
| GET | `/system/activity` | Recent activity feed |
| GET | `/system/stats` | Combined library + download stats |
| **Library — Artists** | | |
| GET | `/library/artists` | List/search artists (paginated) |
| GET | `/library/artists/<id>` | Artist detail + albums |
| GET | `/library/artists/<id>/albums` | Albums for an artist |
| **Library — Albums** | | |
| GET | `/library/albums` | List/search albums (paginated) |
| GET | `/library/albums/<id>` | Album detail + tracks |
| GET | `/library/albums/<id>/tracks` | Tracks in an album |
| **Library — Tracks** | | |
| GET | `/library/tracks/<id>` | Track detail |
| GET | `/library/tracks` | Search tracks by title/artist |
| **Library — Browse** | | |
| GET | `/library/genres` | Genre listing with counts |
| GET | `/library/recently-added` | Recently added content |
| GET | `/library/lookup` | External ID lookup |
| GET | `/library/stats` | Library statistics |
| **Search** | | |
| POST | `/search/tracks` | Search external track sources |
| POST | `/search/albums` | Search external album sources |
| POST | `/search/artists` | Search external artist sources |
| **Downloads** | | |
| GET | `/downloads` | List download tasks |
| POST | `/downloads/<id>/cancel` | Cancel a download |
| POST | `/downloads/cancel-all` | Cancel all downloads |
| **Wishlist** | | |
| GET | `/wishlist` | List wishlist tracks |
| POST | `/wishlist` | Add to wishlist |
| DELETE | `/wishlist/<track_id>` | Remove from wishlist |
| POST | `/wishlist/process` | Trigger processing |
| **Watchlist** | | |
| GET | `/watchlist` | List watched artists |
| POST | `/watchlist` | Add artist to watchlist |
| PATCH | `/watchlist/<artist_id>` | Update content filters |
| DELETE | `/watchlist/<artist_id>` | Remove from watchlist |
| POST | `/watchlist/scan` | Trigger scan |
| **Discovery** | | |
| GET | `/discover/pool` | Discovery pool tracks |
| GET | `/discover/similar-artists` | Similar artists |
| GET | `/discover/recent-releases` | Recent releases |
| GET | `/discover/pool/metadata` | Pool metadata |
| GET | `/discover/bubbles` | All bubble snapshots |
| GET | `/discover/bubbles/<type>` | Specific bubble snapshot |
| **Profiles** | | |
| GET | `/profiles` | List all profiles |
| GET | `/profiles/<id>` | Profile detail |
| POST | `/profiles` | Create profile |
| PUT | `/profiles/<id>` | Update profile |
| DELETE | `/profiles/<id>` | Delete profile |
| **Retag Queue** | | |
| GET | `/retag/groups` | List retag groups |
| GET | `/retag/groups/<id>` | Retag group detail + tracks |
| DELETE | `/retag/groups/<id>` | Delete retag group |
| DELETE | `/retag/groups` | Clear all retag groups |
| GET | `/retag/stats` | Retag queue statistics |
| **ListenBrainz** | | |
| GET | `/listenbrainz/playlists` | List cached playlists |
| GET | `/listenbrainz/playlists/<id>` | Playlist detail + tracks |
| **Cache** | | |
| GET | `/cache/musicbrainz` | Browse MusicBrainz cache |
| GET | `/cache/musicbrainz/stats` | MusicBrainz cache statistics |
| GET | `/cache/discovery-matches` | Browse discovery match cache |
| GET | `/cache/discovery-matches/stats` | Discovery match cache statistics |
| **Playlists** | | |
| GET | `/playlists` | List playlists |
| GET | `/playlists/<id>` | Playlist detail + tracks |
| POST | `/playlists/<id>/sync` | Trigger playlist sync |
| **Video** | | |
| GET | `/video/library` | Movies or shows in the library |
| GET | `/video/library/genres` | Video genres |
| GET | `/video/search` | TMDB multi-search |
| GET | `/video/trending` | Trending titles |
| GET | `/video/wishlist` | Wishlist page or counts |
| GET | `/video/wishlist/counts` | Wishlist counts |
| POST | `/video/wishlist` | Add a movie or episodes |
| DELETE | `/video/wishlist` | Remove at any scope |
| GET | `/video/watchlist` | Followed shows / people / studios |
| POST | `/video/watchlist` | Follow |
| DELETE | `/video/watchlist` | Unfollow |
| POST | `/video/scan` | Ask for a library scan |
| GET | `/video/scan/status` | Scan progress |
| GET | `/video/stats` | Library, database, download and wishlist counts |
| GET | `/video/downloads` | Downloads (`?status=` filter) |
| GET | `/video/downloads/status` | Download status |
| GET | `/video/downloads/history` | Download history |
| GET | `/video/calendar` | Upcoming / recent releases |
| GET | `/video/requests` | Requests |
| POST | `/video/requests` | File a request |
| POST | `/video/requests/<id>/approve` | Approve |
| POST | `/video/requests/<id>/deny` | Deny |
| **Settings** | | |
| GET | `/settings` | Get settings (redacted) |
| PATCH | `/settings` | Update settings |
| **API Keys** | | |
| GET | `/api-keys` | List API keys |
| POST | `/api-keys` | Generate new key |
| DELETE | `/api-keys/<id>` | Revoke key |
| POST | `/api-keys/bootstrap` | Bootstrap first key (no key, admin only) |

### Public playlist search

`GET /api/playlists/public-search?q=<name>&source=deezer|spotify&limit=15`

Public playlists by name, from the services that can be searched without anyone's account. `source` narrows it to one. Each result has `id`, `title`, `creator`, `track_count`, `image_url`, `source` and `link`; adding one is the same as pasting its `link` into Add Playlist. A service that fails or is not set up is left out. Needs the playlist sync permission.

`GET /api/playlists/public-preview?source=deezer|spotify&id=<id>` returns the first 50 songs of one of those playlists as `{songs: [{title, artist}], total}`, so you can look before adding it.

### Video chapters

`GET /api/player/tracks/<type>/<id>` also returns `chapters`: the file's own chapter marks as `[{start, end, title}]` in seconds (empty when the file has fewer than two). The web player lists them in its chapter menu and offers Skip intro, Skip recap or Skip credits while a chapter with that name plays.
