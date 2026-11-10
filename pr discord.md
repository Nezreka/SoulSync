**SoulSync 3.6.4 is out** :musical_note:

Video now supports your own movie and TV libraries. Each library has its own quality profile, download sources, client category, Prowlarr indexers and automatic search, and the same movie can live in two libraries as two copies. Releases are ranked one way everywhere (quality, then custom formats, then tie-breaks), and every search result says why it ranked where it did. SABnzbd and NZBGet now receive the NZB file itself, and a season grab checks it has the right show (thanks SeadogsBooty and Thibs). Admins can now delete a show or movie and have it stay deleted: nothing grabs it again, and its folder goes to the recycle bin as one entry you can restore (thanks PapaBearDoes).

Own-library profiles now work on Navidrome 0.58+ (thanks mirlooun). Every profile can hide the pages and jobs it doesn't use, and the Playlists and Watchlist pages have been redesigned.

Download Discography now uses the artist's quality profile (thanks Foxxify). You can follow a YouTube channel without downloading its new uploads (thanks JuanVqz), Library Health has per-artist cleanup checks (thanks QT3496), and mirrored playlists can be exported as M3U (thanks TaunGadd).

Everything Library v2 touched was reviewed five times end to end. Every finding was fixed with a test, covering the 3.6.1 upgrade, household and own-library separation, tags, artwork, repair jobs and downloads. The library pages, reports and startup are much faster on large libraries.

Playlists can now be rotating: a song the playlist downloaded is removed once it leaves the playlist, unless you played it, favourited it or still have it in another playlist (#2039).

Library scans no longer get flagged as stuck while an own-library profile's scan is still working (#2036).

Thanks also to nick2000713, splitsec2, lozog, Eric-sson, cremonies and jdfrye2 for fixes and reports.

Full notes on GitHub.
