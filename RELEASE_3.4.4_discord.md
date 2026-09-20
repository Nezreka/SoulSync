**SoulSync 3.4.4**

Personal music libraries and server accounts, safer library scans, and fixes for sync, downloads and chat.

**A library for each profile**
Profiles can have their own music folder and server library (#1199). Downloads, scans, watchlists and ownership checks stay with the right profile. Folder validation prevents overlapping libraries, and the admin continues to use the shared library.

**Playlists on your account**
Choose a Plex Home user or personal Navidrome login for your profile. Jellyfin user selection is fixed, concurrent syncs keep their own profile, and failed Jellyfin connections can be retried. Additional admins now have the same music-side access.

**Safer deep scans**
A server timeout no longer makes an album look deleted. Cancelled scans preserve unseen records, and Jellyfin fetches all pages of large artists and albums.

**Clearer sync and download status**
Sync explains when multiple playlist entries map to the same library file. Cancelled and completed batches close their sync history, and slow post-processing no longer holds up status updates.

**Automations and cleanup**
Optional weekly quarantine and recycle-bin cleanup ships disabled. Recycle-bin retention applies; enabling cleanup with Keep forever selected empties it. Missed interval jobs catch up after restart, and Last.fm notifications finish correctly.

**Chat fixes**
Messages stay in the selected room, multiline plain-text messages arrive correctly, and dismissed polls stay dismissed after refresh.
