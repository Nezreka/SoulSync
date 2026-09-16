**SoulSync 3.4.1**

This update focuses on long waits, clearer pages and more reliable audiobook handling.

**Speed and reliability.** Lyrics requests now time out, manual imports run in the background with progress updates, and download recovery no longer blocks status updates. Metadata searches have bounded waits and worker capacity. Offline Plex connections are cached, with shorter interactive checks and refreshed scan status. These address the blocking paths reported in #1245.

**Discover, Watchlist and Settings.** Refreshed layouts and controls across all three pages. Discover adds Deezer editorial playlists, all 28 genres and playlist search. Watchlist shows source-matching progress and lets you cancel during a long artist match (#1240). Music and video share Folders and Organization cards.

**Audiobooks.** Scan books already on disk, browse the rebuilt library and review catalogue edition matches. Downloads report actual client progress, cancellation persists, Soulseek transfer matching is corrected, and wishlist rows move beyond “sent to downloads.”

**Also fixed.** SoundCloud import and tagging (#1239), Last.fm username corrections (#1241), Deezer track identity, and Soulseek chat connection checks with unsent drafts preserved. Podcast fetching is hardened and watchlists respect profiles. Video libraries can span additional paths across drives.

Thanks to everyone who sent reports and logs.
