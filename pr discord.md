**SoulSync 3.5.5 is out** :musical_note:

Your own playlists grow up: play, shuffle and queue on every playlist, a Liked Songs heart in the add-to-playlist picker, ticks for playlists that already have the song, add songs and sort from inside a playlist, and a made-over detail view. Mirrors can append or reconcile per playlist or per pipeline instead of one global mode (thanks @Tacobell444).

Songs that aren't in your library can play their Deezer 30s preview instead (thanks @cremonies). Dashboard rail cards get play, queue and add-to-playlist buttons, and the mix modal is redesigned.

A big speed pass: stale SQLite planner stats made a watchlist scan pay 1.4 s per missing track (now 32 ms), plus missing indexes on file paths and lowercase titles, faster enrichment workers and stats.

Read-only API keys (thanks @splitsec2). Subtitles reach Bazarr parity with a missing subtitles job on the tools page.

Downloads can't land untagged anymore, and a repair job flags the ones that did. The next quality tier gets a shot when the top one won't start (thanks @javiavid). Standalone full refresh stops dropping tracks to id collisions (thanks @rijndael86).

Matching: featuring-credit titles match on MusicBrainz (thanks @pm-melo), AcoustID accepts a recording under another release's title (thanks @zikasak), reorganize settles where it placed an album (thanks @rhyst), and discography backfill rotates through the whole library (thanks @QT3496).

Also: pipeline syncs respect wishlist skip (thanks @Tacobell444), every connection gets a test button, similar artists stop storing namesakes, and another phone layout batch.

Full notes on GitHub.
