**SoulSync 3.6.2 is out** :musical_note:

Library v2 is here. SoulSync now keeps its own library instead of mirroring your media server: watchlist any artist, wishlist any album or single song, see the songs you're missing, pick a release yourself with interactive search, and watch live download state and one history feed for every artist, album and song. Quality is checked before you grab, your own edits survive refreshes, delete removes the real files with a preview first, and the library works on a phone. Your existing library is upgraded on the first start, in the background. A huge thank you to @nick2000713, who built all of it.

Every artist now opens one artist page, and just browsing an artist no longer writes it into your library. Unmatched files are fingerprinted and matched in the background with a review to confirm them.

Music Assistant joins as a fifth media server (thanks @splitsec2), and Plex and Jellyfin get HD artist photos and wide backgrounds (#1957). Profiles get their own Jellyfin plays and their own ListenBrainz or Last.fm scrobbling, and on the video side every profile linked to its own Plex account keeps its own watch state, with Watched Cleanup waiting for everyone who watches (#1941, thanks @PapaBearDoes).

Open editor works on a playlist before its first sync (#1958), you can export the wishlist as CSV or JSON, and Tidal AAC no longer shows 0:00, with a new Tools job that fixes the files you already have. Bulk imports keep one artist in one folder (#1953, #1954) and the import inbox stops timing out (#1955).

Syncs, Discover and the library are much faster on big libraries. SABnzbd jobs stop downloading twice on a busy SAB (#1964, #1965). Plus fixes from @PapaBearDoes, @SimpleSimonLA and @TaunGadd, and a lot more.

Full notes on GitHub.
