# soulsync 3.5.8: `dev` → `main`

request lists, playlists that sync themselves once their downloads land, discover mixes on the dashboard, a new chat upload flow, audiobook automations, and a stack of fixes for crowdsec bans, a frozen review page and scattered artist folders. scope: everything on dev since 3.5.7.

## playlists

- request lists. a new tab on the sync page for playlists that only download. copy any playlist there (or start an empty one) and syncing it wishlists what's missing without building a playlist on your media server. the original keeps syncing like before.
- spotify auto-remove for request lists. opt in per list and spotify tracks you now own come off the spotify playlist. it asks before turning on, uses your own spotify login, and only removes a track the library holds under the same spotify id, so a fuzzy match can never delete the wrong song.
- playlists sync themselves after their downloads land. when a wishlist batch from a playlist finishes and the database update runs, that playlist re-syncs so the new tracks reach the server playlist without waiting for the next schedule. it's a new system automation, "Auto-Sync Playlists After Database Update".
- the sync playlist automation can sync every mirrored playlist in one go.
- a track your server lost actually comes back. the sync wishlisted it, but its library row stayed behind and the auto-wishlist sweep saw it as owned and pulled it straight back off. the dead row is dropped now, and review's delete drops the row for the file it removes (thanks @kevin2xk) (#1754, #1756).

## dashboard

- the listen band shows your discover quick mixes instead of a static tile. click a mix to preview it, hit play to start it.

## chat

- uploading a file opens a preview first, with a caption and a progress bar. shared files get inline audio and video players, images open in a lightbox and start small with an expand toggle, and the emoji picker opens in one click. movie night is now watch together.

## automations

- audiobooks fire automation triggers now: import complete, grabs, failed downloads, wishlist, follows, new releases and scans. so an "import complete" ntfy works for books too (thanks @SimpleSimonLA) (#1774, #1753).
- system automations are split into operations (wishlist, watchlist, scans, syncs) and maintenance (tools, cleanups, repair jobs, backups) in the sidebar.

## imports and library

- artist letter folders stopped scattering. `$artistletter` took the track artist, so a song with a guest landed under the guest's letter and one artist's albums spread across several letters. it follows the album artist now, and there's a new `$albumartistletter` (thanks @QT3496) (#1785).
- singles reuse an artist folder that differs only by case instead of making a second one, same as albums already did (thanks irishcal on discord) (#1788).
- a manual import with "no track picked" imports from the file's own tags instead of rematching to an unrelated itunes track (thanks @edy80) (#1772, #1773).
- re-identify fixes: tracks re-identified out of one bad album import one by one instead of sitting in the inbox, the kept library file can't be deleted as the "old" track, and a poisoned album finding opens the album instead of a track search (thanks @kevin2xk) (#1567).
- albums routed to the audiobook library count as owned in discographies, and audiobook search filters routed audio plays too (thanks @SimpleSimonLA) (#1748, #1749).

## downloads

- downloads > review stopped freezing the browser. a big quarantine was loaded and drawn all at once, with a library check per entry. the list is paginated on the server and in the page now, so a big queue can actually be worked down (thanks @QT3496) (#1775, #1777).
- picking candidates for not-found tracks no longer dies with "task not found". finished batches were dropped 5 minutes after completion even with the modal open; the clock now runs from the last time anyone looked.
- a torrent waiting in qbittorrent's own queue isn't a stall. soulsync gave up on it after 10 minutes (music) or 30 (video), and forcing it in qbit finished a download nothing imported.

## security and profiles

- members stop getting banned by crowdsec and fail2ban on their first visit. a normal page load sent a burst of calls the server refused (admin-only settings, video calls for a music-only profile, idle sync probes, a mix route that never existed). the page now asks only for what the profile can have, idle probes answer 200, and failed image cache serves redirect instead of 404 (thanks @PapaBearDoes) (#1763).
- deleting a profile no longer strands its signed-in browsers on a blank app in login mode. the whole session is cleared and the sign-in screen comes back (thanks @PapaBearDoes) (#1786).
- watchlist "view discography" opens the right artist when it was added from a source artist page, and its photo comes from that source (thanks @splitsec2) (#1761, #1762).

## api

- `/api/v1` library responses carry the enriched metadata (discogs, bandcamp, amazon, jiosaavn ids and data, aliases, release dates, canonical release picks, locks, server paths), ids can be opaque text, and lookup takes discogs, amazon, jiosaavn and bandcamp ids (#1789).

## under the hood

- media servers are described in one place: a descriptor per server read by settings, detection and the wizard, shared capabilities and hooks, one item model and one per-profile client resolver (thanks @splitsec2) (#1750, #1751, #1752, #1757, #1758, #1759, #1760).
- about 12,000 lines of dead css are gone, a stray brace in video-side.css that dropped its phone layout is fixed, and a test now checks every sheet for stray braces (thanks @Thundernerd) (#1747, #1766 to #1771, #1778 to #1784).
- the vanilla code the react sync, beatport and dashboard pages replaced is retired, about 7,500 lines (thanks @AnonymousMorris) (#1730 to #1746).

## validation

- every fix shipped with regression tests, the request list fixes and #1567 were negative-checked (break it, watch the tests fail), and the css cleanup was checked against today's dev with the class checker, a postcss cascade diff and the full webui suite.
