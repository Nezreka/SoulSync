# soulsync 3.4.4: `dev` → `main`

separate music libraries and server accounts per profile, safer deep scans, and fixes for sync reporting, downloads, automations and chat. scope: the 23 commits since the 3.4.3 release commit (`7bc2650d3`).

## a library of your own

- each profile can use its own music folder and server library (#1199), with its downloads, scans, ownership checks, watchlist and playlist folders scoped to that library. the admin stays on the shared library; another person's copy does not count as yours.
- the folder is prefilled, checked when saved, and rejected if it overlaps the shared library or another profile's folder. docker compose includes a default mount path for personal libraries.
- scans and download workers carry the owning profile through background work. jellyfin artists shared across server libraries keep their albums in the right library, and ownership filtering keeps indexed searches fast.

## your playlists on your server account

- choose a plex home user or a personal navidrome login for your profile so synced playlists land on your account. jellyfin uses a profile-specific client view instead of changing the shared client's user.
- two profiles syncing at once keep their own identity. personal settings shows the active server's account controls, the jellyfin user picker loads correctly, and user-library lookups run in parallel and cache their results.
- a failed jellyfin connection can be retried instead of being remembered permanently. a second admin gets the same music-side admin access as the first.

## deep scans keep records when the server fails

- a timeout used to look like an empty artist or album, leaving its tracks eligible for deletion. unverified listings now preserve the affected rows and report the failure; cancelling a scan skips stale-row removal.
- jellyfin artist and album listings now page beyond their old 200-album and 100-track limits. incomplete bulk listings fall back to individual fetches. genuinely removed items are still cleaned up.

## sync and download reporting

- when several source entries match the same library file, sync reports what was folded together, names the entries in the log, and includes the folded count in the finished card. the review data retains the library artist and album, and deezer's album-loading progress explains its two passes.
- cancelling the last track, cancelling a whole batch, and batch healing now close sync history instead of leaving it in progress forever. slow tag, playlist-folder and repair work runs outside the task lock so status updates and other batches can continue.

## automations and cleanup

- weekly cleanup can clear the download quarantine and recycle bin. it ships disabled, with separate controls for each part. the recycle bin follows its retention window; enabling cleanup with keep forever selected empties it.
- interval automations that missed a scheduled run catch up soon after restart. the last.fm listens notification closes when its work finishes.

## chat

- messages sent from #bugs stay in #bugs, and multiline plain-text messages reach the selected room.
- dismissed closed polls stay dismissed after refresh.

## validation

- fixed the nine CI failures caused by outdated ownership-schema fixtures and a source-lookup fake missing its scope method.
- all 51 tests in the four affected modules and the own-library profile suite passed. the supplied full-suite run had 19,086 passes and those nine failures; a new full-suite green run has not been verified.
- release checks: 162 helper, script-loading and web-asset tests passed; helper.js passed node syntax checking; the discord draft is 1,519 characters.
