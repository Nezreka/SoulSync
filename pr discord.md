**SoulSync 3.4.10 is out** :musical_note:

Video Discover got the full music-side treatment: story blocks, trailers, genre art, a hero badge, story tickers, and tiles that fade in with your posters. Video detail pages now have an "I have this" button for when auto-matching misses, and rematching a video actually refreshes its artwork.

Sync went per-profile: server playlists show whose is whose, mirrors with the same name stop overwriting each other, and deleted tracks get noticed on the next sync. Non-admin automations re-arm after a restart instead of running once and dying quietly.

Under the hood: providers stop false rate-limiting when an ID contains 429 or 503, canonical lookups send each provider its own artist ID (no more 400 invalid mbid from MusicBrainz), singles stop merging into same-named album folders, your custom single path template is honored, lossy copies keep native tags and cover art, and the companion extension gets a chat tab plus video badges.

Full notes on GitHub. Enjoy! :tada:
