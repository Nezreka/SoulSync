**SoulSync 3.5.7 is out** :musical_note:

Beatport charts are real playlists now. Every chart opens to be identified instead of downloading as a fake "Various Artists" compilation, tracks match by ISRC, mirrored charts refresh and can join the pipeline, and you can save a dated snapshot of a chart. Beatport is also an enrichment source for BPM, key, label and catalog number.

Playlist sync gives Spotify tracks your primary source misses a second pass by their own Spotify id instead of turning them into Wing It, every track of a mirrored playlist shows where it stands, and a stale cached match can't pin the wrong song (thanks @kevin2xk). Scheduled auto-sync regenerates Discovery Shuffle (thanks @Bloopser), and failed Navidrome writes are reported properly (thanks @zikasak).

New automatic album completion finishes incomplete albums in your library (thanks @kiwijar). Chosen spoken-word artists can route to the audiobook library (thanks @SimpleSimonLA).

Video wishlist episodes find releases named by TVDB's episode numbering, and stop rejecting shows with a year in the name. There's a new video stats API endpoint (thanks @hilburn).

Importing albums you already own no longer fails every track: they count as already in library (thanks SeadogsBooty). Plex paths resolve when a folder differs only by case.

Full notes on GitHub.
