**SoulSync 3.5.0 is out** :musical_note:

Video Discover got the full music-side treatment: story blocks, trailers, genre art, a hero badge, story tickers, and tiles that fade in with your posters. Video detail pages now have an "I have this" button for when auto-matching misses, and rematching a video actually refreshes its artwork.

Sync went per-profile: server playlists show whose is whose, mirrors with the same name stop overwriting each other, and deleted tracks get noticed on the next sync. Non-admin automations re-arm after a restart instead of running once and dying quietly.

Under the hood: providers stop false rate-limiting when an ID contains 429 or 503, canonical lookups send each provider its own artist ID (no more 400 invalid mbid from MusicBrainz), singles stop merging into same-named album folders, your custom single path template is honored, lossy copies keep native tags and cover art, and the companion extension gets a chat tab plus video badges.

Since those notes were drafted there's more: a big clarity pass from discussion #1289 — safer defaults (sync defaults to Reconcile so Navidrome edits survive, "Transfer is my permanent library" on by default), clearer language across the app (Discovery → Identify), and an honesty overhaul of the import inbox (partial imports, stale rows, match stealing). Repair jobs now run on the automation engine, there's a new BPM backfill job, and manual match opens with a worklist of everything unmatched.

Also in: a community fix batch (wishlist album scope, discography substring matching, working AudioDB key, Audible marketplace setting), playlist sync no longer re-downloads what you deleted unless you ask (Sync vs Sync + download), video calendar cards show their full status, Deezer reissue years stop marking owned albums missing, and new v1 API endpoints powering the extension's mini player.

Full notes on GitHub. Enjoy! :tada:
