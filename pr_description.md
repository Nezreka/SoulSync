# soulsync 3.3.3: `dev` → `main`

chat that reaches people outside your install, smarter release parsing, self-hosted musicbrainz, and a discover pass.

## chat, beyond soulsync

messages go both ways with people who aren't running soulsync, and the history sticks around. overlay templates can be shared straight into a room, picked from a modal.

## release parsing

reads the actual bitrate, sample rate and codec first and only falls back to the uploader's title, the way lidarr does. a repack wins the tie as the corrected copy. quality now survives the whole source pipeline, so a complete album can't fall back to a worse copy than one already found. lossless preview clips get caught on import. you can say which indexer you trust, and usenet results show the post age. built on #1224 from nick2000713.

## self-hosted musicbrainz

point soulsync at your own server, in settings under Connections.

## concerts on the artist page

upcoming dates and real setlists, via ticketmaster.

## video

detail page polish and movie sync fixes. the dashboard shows what's downloading right now with posters. continue watching reads your real resume position. torrent grabs track properly, a refused grab says why, seed removal is honoured in client mode, and EXT.to magnets resolve after selection.

## discover

play buttons on mix cards and track rows now play. playback is confirmed by the player before it's reported, so you know when audio actually started. a mix resolves against your library in one query instead of one per track, so it starts in about a second.

hero controls sit in their own row on both pages, cards and dialogs work by keyboard and touch, and the taste dial is a real slider.

on video: browsing keeps the newest search's results, a failed request says so instead of showing an empty shelf, and Not Interested removes every copy of a title with an Undo.

## tools

split into Tools and Operations. artist views are named Discography and Your library. "cleanup recommended" says what it means.

## notifications

ntfy and gotify are real actions now.

## also

- an error page shows the actual error with a copy button, so a report has something in it
- play the album you own instead of downloading it
- the upgrade backlog is sorted worst-first and folded to albums
- self-service profile credentials, and switching login profiles asks for the password
- less idle polling and fewer broad soulseek searches
