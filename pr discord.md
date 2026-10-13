**SoulSync 3.5.4 is out** :musical_note:

Subtitles grow up to replace Bazarr: providers are pluggable, a wanted loop retries misses with hash-first scoring, per-title language overrides, proper hi/forced naming for Plex and Jellyfin, an upgrade loop, manual search — and optional local Whisper transcription as the last resort. Fetching is on by default for new imports.

Audiobooks: follow a whole series (backfill every volume or new books only), an optional m4b merge on import, and imports are staged so Plex only ever sees the finished book. Complete books stop being held for having a different narration than the wishlisted edition (thanks SeadogsBooty).

One downloads page for music and video, video wishlist downloads capped per run (thanks @c5pie), and Usenet can pull a requested song out of an album release — with an option to keep the album's other tracks too.

Your own playlists get covers, descriptions and a duplicate button. Imports skip tracks you already own instead of reprocessing them (thanks SeadogsBooty), and the inbox stays honest while a scan is running.

Big phone pass: 22 screenshots' worth of layout fixes. The player button is round and the EQ is reachable, settings titles and popups clear the menu button, the watchlist scrolls, and the dashboard cards stack instead of clipping (thanks @Thibs, @c5pie, gzetk).

Scrobbling no longer dies with the listening stats toggle, and the web player scrobbles by Last.fm's rule (thanks @cin). Mirror playlists are safe from empty refreshes and empty payloads (thanks @splitsec2). Quarantine tells you when a song already arrived another way, and slowed/sped-up versions stop landing as the original (thanks @nick2000713).

Also: the subtitle chain is a settings widget with a backfill for your existing library, the audiobook wishlist matches the music design, scrollbars fade as you scroll, standalone library scans work, Deezer re-logins when its session dies, AcoustID keys apply without a restart, the UI follows your accent color, dead CSS deleted by the thousand lines (thanks @Thundernerd), the watchlist only wishlists tracks credited to the artist you watch, playlist sync matches again, and a stack of library and import fixes.

Full notes on GitHub.
