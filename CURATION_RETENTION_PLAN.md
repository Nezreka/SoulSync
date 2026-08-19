# keep what people actually like (Cremonies)

> "Do all automated song downloads stay forever or is there a rating/favorite
> system to keep the ones you like? I like this idea of a discovery playlist
> that updates but that doesn't mean I want to keep all the songs forever."

his design: keep a track if any user favorited it, rated it above 2 stars, or
put it in a playlist. favorite beats rating (people star songs they keep out
of their mixes). new downloads get marked deletable; if the db is wiped and
rebuilt everything is treated as existing and stays safe.

## the governing rule

**this job deletes the user's files. every failure must fail toward KEEP.**

server unreachable, credentials wrong, signal sync stale, column missing,
path unresolvable, user list empty — every one of those must result in the
track being kept, never deleted. no exceptions. where that's ambiguous,
choose the conservative branch and write a test that pins it.

## what already exists (verified, not assumed)

- `core/library/expired_cleanup.py` — pure decision core, 99 lines, no I/O.
  `is_expired()` keeps a track when: `protected` is truthy, OR
  `play_count >= min_plays` (default 2), OR the origin's retention is 'off',
  OR `created_at` is unparseable. clean seam to extend.
- `core/repair_jobs/expired_download_cleaner.py` — the job. dry-run default
  True, `default_enabled` False, both retentions default 'off'. inert today.
- origin provenance: `core/downloads/origin.py` → `library_history.origin` /
  `origin_context`. only 'watchlist' and 'playlist' are candidates; manual
  downloads are never classified, so never touched. correct already.
- `core/credentials/store.py` — named credential sets per service, schema
  ALREADY covers `navidrome: (base_url, username, password)`, plus plex and
  jellyfin, with per-profile selection. dormant: no client path reads it.
- `core/listening_stats_worker.py` — the shape to copy for a signal sync
  worker (poll interval, per-server optional methods, contract in
  `core/media_server/contract.py`).

## what does not exist

- **no favorite or rating is read from any server, anywhere.** `TrackInfo.rating`
  is populated in two places and read by nothing. navidrome's `star`/`setRating`
  appear only in a `_WRITE_ENDPOINTS` routing table and are never called.
  `getStarred2` / `getTopRated` appear nowhere in the repo.
- **no multi-user.** one navidrome account, one globally-chosen jellyfin user,
  one plex token. `getPlaylists` is called with no `username` param.
- **no track-level keep flag** of any kind in the schema.

## two live bugs to fix before making this job attractive

**B1 — the play-count protection probably never fires on docker/nas.**
`get_origin_cleanup_candidates()` joins `library_history.file_path` to
`tracks.file_path` by exact string match. those are SoulSync's post-processed
path and the media server's reported path — the mismatch
`core/library/path_resolver.py` exists to bridge. the DELETE step resolves it;
the PROTECTION query does not. so on those installs play_count reads 0 for
every candidate and "you played it" protects nothing.

**B2 — a database rebuild makes the whole library deletable.**
`clear_server_data` drops tracks/albums/artists but leaves `library_history`
intact. so `created_at` survives (retention clock keeps running) while
`play_count` resets to 0 and only returns on the next stats poll (up to 30
min, and never at all on standalone installs). in that window every
origin-tracked download past its window looks "old and never played",
including ones played fifty times. this is exactly the disaster Cremonies'
grandfather marker is designed to prevent.

secondary: mirror/watch exemptions match by NAME string (renaming a playlist
drops protection) and hardcode `profile_id=1`.

## per-server reality (shapes the whole design)

| | favorites / ratings | other users' playlists |
|---|---|---|
| jellyfin | admin api key can read any user's `UserData` — iterate users, one credential | same |
| navidrome | `getStarred2` is scoped to the AUTHENTICATED user, no admin impersonation → needs each user's own password | `getPlaylists?username=` is admin-capable → one credential |
| plex | owner account only; home/managed users need account switching | messy |

so Cremonies is right that "each user's credentials are needed" — but only for
navidrome stars/ratings. jellyfin needs one admin key. plex is best-effort.

**assumption to verify against a live server before shipping phase 2:** that
subsonic exposes no admin path to another user's starred items. if it does,
navidrome collapses to one credential too.

## phases

each phase is independently committable, independently valuable, and leaves
the system no more dangerous than it found it.

### phase 0 — safety (do first, ship alone if you like)

0a. protection query resolves paths through `core/library/path_resolver.py`,
    the same way the delete step already does.
0b. grandfather marker: stamp cleanup eligibility on the track at download
    time; a track with no mark is pre-existing and never deletable. the mark
    must live somewhere `clear_server_data` does not wipe, or be re-derivable
    as "safe" — a rebuild must produce KEEP, not DELETE.

verify: a test that seeds a played track, simulates the wipe+rescan
(`play_count` → 0, history intact), runs the decision, and asserts KEEP. that
test fails today — write it first and watch it fail.

### phase 1 — the decision, with no server calls yet

1a. table for curation signals: (server, user, track key, favorite, rating,
    in_playlist, synced_at). track key must survive a rebuild — prefer the
    library path or soul_id over the server item id.
1b. `is_expired()` gains a `curated` check next to `protected`. favorite beats
    rating. keep the play_count check as a fallback, don't replace it.
1c. the job populates `curated` from the table, and records WHY a track was
    kept so the dry-run finding can say "kept: favorited by alice".
1d. stale-signal guard: if the last successful sync is older than N hours,
    treat every track as curated (keep everything). fail toward keep.

verify: pure-core tests for each keep reason and the favorite-beats-rating
rule; job tests with the table empty (must keep, not delete) and stale.

### phase 2 — read the signals (per server, behind a flag)

2a. jellyfin first — cheapest and needs no new credential story: admin key,
    list users, read `UserData.IsFavorite` / `Rating` per user.
2b. navidrome — `getStarred2` + ratings per user credential set;
    `getPlaylists?username=` for playlist membership via the admin account.
2c. plex — best-effort, owner account only, documented as such.

each server implements an optional method declared in
`core/media_server/contract.py`, exactly like `get_track_play_counts` already
is, so a server that doesn't implement it degrades to "no signals" — which,
per the stale guard, means keep.

verify: stubbed clients, no network. explicitly test the unreachable-server
and empty-response cases assert KEEP.

### phase 3 — multi-user credentials

wire `core/credentials/store.py` for the read path: iterate every saved
credential set for a service.

**do not** follow `_apply_profile_library`'s pattern — it sets `client.user_id`
by mutating the process-wide singleton in place, and that override leaks to
every other caller until something resets it. build a short-lived client per
credential set, or pass identity per call. a leak here means one user's
favorites protecting (or failing to protect) another's library.

verify: a test asserting the shared client's state is unchanged after a full
signal sweep.

### phase 4 — make it findable

Boulder's own note from july: this job is buried among ~20 repair jobs and
default-off, which is why users keep asking for a feature that exists. link
it from the Download Origins modal and playlist-sync settings, and surface
the kept-because reason in the findings UI.

## how we know we didn't break anything

- the job ships default-off and dry-run; phases 0-3 do not change that.
- every phase adds tests before code where the bug is provable today (phase 0
  especially — the rebuild test must fail first).
- `expired_cleanup.py` is pure, so the whole decision matrix is unit-testable
  without a database.
- full backend suite green before each commit.
- the existing tests (`tests/test_expired_download_cleaner.py`,
  `tests/library/test_expired_cleanup.py`) must keep passing unchanged — if a
  change forces one to be edited, that's a behaviour change and needs saying
  out loud.
