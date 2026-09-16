**SoulSync 3.4.0**

Two new sides to the app, podcasts and audiobooks, and a settings page rebuilt from the ground up.

**Podcasts.** A full section: iTunes search, or paste an RSS URL straight into the search bar. Custom, Patreon and private feeds, plus OPML 2.0 import and export. Browse, show detail with the full episode tracklist, and a player. Episodes download through the existing downloads page. The watchlist auto-downloads new episodes with retention settings, library paths and organization templates are yours, and a static MP4 conversion option covers media servers that only handle video.

**Audiobooks.** Audible's catalog as the metadata spine: search by keyword, title, author or narrator, series in reading order, genre charts and samples. Acquisition through Prowlarr category 3030, the shared torrent and usenet clients, and Soulseek, with releases explained before a download is spent. Quality profile, blocklist, library scan, recycle bin and author watchlist. Isolated from music by construction: its own database, its own source chain.

**Settings.** 22 services lived in two "API Configuration" groups as nested accordions. They are tiles now, with media tabs, and each one says whether it is configured without being opened. Every download source is on one Sources tab behind the tile it belongs to, including the torrent client, usenet client, Prowlarr and yt-dlp. Library and Quality were merged the same way.

**YouTube cookies.** The probe behind the Test button was hardcoded to return true, so the dot was green no matter what. With a real probe, browser cookie mode now reports when it cannot work on your setup and points at Paste cookies.txt.

**Also.** Reverse proxy URL base paths. Podcasts and audiobooks under the profile system. Discover stations and Because You Listen To rebuilt.

**Reported fixes:** #1226, #1227, #1228, #1229, #1230, #1231, #1232, #1233, #1234, #1235. Thanks to everyone who filed them.
