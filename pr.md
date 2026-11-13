# soulsync 3.6.5: `dev` → `main`

a design release with a lot of fixes under it. both dashboards rebuilt, the tools, automations, stats and notifications redone, video detail pages as one page, a review lock for albums and artists, every artist merge recorded and undoable, and a new install that no longer trips on a library upgrade it never needed.

## new installs

- **a new install is not sent into a library upgrade (#2062).** pressing import library on a fresh install raised "legacy table is missing required columns" and the page then said the upgrade had failed and everything was paused. a new install has no older library, so it says that now and points at search and import. there is a test that walks a brand new install end to end: nothing paused, every library request answers, music arrives by a scan and by a download.

## the dashboards

- **the music dashboard, rebuilt.** a hero without a frame, the worker orbs on a wider orbit, mixes under the hero, and the system as a board: this week bar by bar with the album each day wore and what was on repeat, counts that say what they are made of, playlist sync split into complete and missing with each playlist's share, and up next as a line of the next hour with what just ran.
- **the video dashboard joins it.** next up and continue watching under the hero, this week day by day with the posters that aired, the watchlist and wishlist counts with what is in them, up next, and make it yours beside it.
- **hover a point on the up next line** for the whole run: when it fires, its schedule, when it last ran and what it did.
- **the worker orbs answer at once.** hovering used to wait for the orbs to fly home before any button appeared (about 0.9s to the first, longer on an idle dashboard). the buttons bloom in as the orbs move now, and the animation runs on a clock so it behaves the same asleep, in reduce effects, or on a fast screen.

## notifications

- **the bell, rebuilt.** notes stay new until you close the panel, and a reload no longer empties it. filters have names and counts, running jobs are rows that fold past three, a file path is cut to its name with copy path beside it, and a note can be dismissed. toasts stack up to three with the time left as a line that stops while you point at it, and a problem stays twice as long as good news. history is grouped by day with search, and system health is a row per check with the worst first.

## tools

- **the tools page, redone.** one page with four pills, artwork leading every finding, findings in plain words with before and after, and re-tag keeps history and can undo.
- **artist merge review (thanks SeadogsBooty, #2073).** two different bands were merged because they shared one catalogue id. that pass needs real evidence now, every merge is written down and can be undone, doubtful pairs are asked about instead of merged, and artists merged by mistake before any of this are found by their folders and tags and split.
- **use file tags, for one album or the whole library (thanks wolf39us, #2070, #2074).** the library takes its titles, numbers, year, genre and album from your files, with a preview.
- **compilation songs to original albums (thanks wolf39us, #2077, #2053).** a job that finds where each song on a best-of first came out, the studio album when the same recording is on one, otherwise the single, and files it there once approved. off by default.
- **album tag consistency finds a split compilation (thanks Storm, #2072).** one album filed as one per track artist is reported from a shared folder, from artist folders whose track numbers fit together, or from the release id on the tags.
- **a library emptied on purpose can be cleared (#2005)**, the acoustid scanner runs by hand with verification off (#2042), and a tag-scan finding writes only the fields it flagged.

## library

- **review lock (#2046).** a padlock on an album or artist keeps what you reviewed: enrichment, repairs and re-matching leave it alone.
- **automatic quality upgrades that work (#2060).** a few at a time, with a growing wait, and no download that cannot be better. a song you already have is no longer put back on the wishlist every hour.
- **live, compilations, soundtracks, remixes, demos and spoken word get their own sections on the artist page (#2055).**
- **manage workers is fast.** the numbers for every worker come from one pass instead of one per worker and kind, and the window opens and switches without waiting.
- **the musicbrainz worker finds a release the artist search misses (thanks Storm, #2071)**, asking by title alone and taking only an answer the title pins down.
- **one release is one album however many servers listed it (#1985)**, and an artist two servers listed is one artist.
- view settings and artist settings redrawn, and it is the Library now, not library v2.

## music

- **search all sources at once (#2051)**, the same release shown once.
- **daily jams is a rolling playlist like weekly jams (thanks mpaugam, #2076).**
- **rotating playlists can let go of songs you played (#2047)**, and two source tracks that are one song on the server are marked (#2059).
- the automations page and the stats page redone, and the auto-sync manager redrawn around covers.
- **$explicit works on downloads and imports (#2068)**, and $discalways names the disc on every album (#2044).
- **prowlarr search pacing is a setting (#2061)**, tidal download requests are spaced (#2069), a spotify id is never sent to deezer (#2066), a soulseek search that finishes between two reads no longer comes back empty (#2065), a finished torrent is not imported before its files are collected (#2064), and a tag in front of the artist is no longer an artist mismatch (#2063).
- a spotify track is matched onto musicbrainz by isrc (#2067), a new album keeps the cover its download came with (#2058), and an itunes m3u reads as title and artist.

## video

- **movie and show pages are one page.** no tabs, files as cards, and the library tab fixes what it reports.
- **video automations, watchlist, search and discover redrawn**, and video automations live on the redesigned automations page.
- **two shows with one name never share a folder (thanks SeadogsBooty, #2052)**, and a download in flight belongs to its show, not its title, so grabbing the office (2001) is not answered "already downloading" by the office (2005) (#2075).
- an unreadable video database is set aside at startup instead of stopping the app.
