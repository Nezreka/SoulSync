**SoulSync 3.5.8 is out** :musical_note:

Request Lists are a new tab on the Sync page for playlists that only download. Copy any playlist there and syncing it grabs what's missing without building a playlist on your media server. Spotify lists can opt in to removing tracks you now own from the Spotify playlist, and only ID-matched tracks are ever removed.

Playlists now re-sync on their own once their downloads land and the database update runs, so new tracks reach your server playlist right away. The Sync Playlist automation can also sync every mirrored playlist at once.

The dashboard shows your Discover quick mixes, chat uploads get a preview step with captions and inline players, and audiobooks fire automation triggers like Import Complete (thanks @SimpleSimonLA).

Fixes: members stop getting banned by CrowdSec on their first visit, and deleting a profile no longer strands its browsers (thanks @PapaBearDoes). Downloads > Review stops freezing on a big queue, and artist letter folders stop scattering (thanks @QT3496). "No track picked" imports use the file's own tags (thanks @edy80), re-identify fixes and lost tracks come back (thanks @kevin2xk), and the watchlist opens the right artist (thanks @splitsec2).

Under the hood: one descriptor per media server (thanks @splitsec2), about 12,000 lines of dead CSS gone (thanks @Thundernerd) and the replaced vanilla pages retired (thanks @AnonymousMorris).

Full notes on GitHub.
