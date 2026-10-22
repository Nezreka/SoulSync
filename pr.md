# soulsync 3.5.9: `dev` → `main`

soulsync can answer as lidarr and as an mcp server now, first-run setup is rebuilt as a guided flow, the downloads page and the wishlist are rebuilt on both sides, you can choose what new sign-ups start with, anime gets its own library folder, and a stack of matching, library and video fixes. scope: everything on dev since 3.5.8.

## api

- lidarr compatible api. singarr and musicseerr 1.x only talk to lidarr, so point their lidarr url at `http://<host>:8008/lidarr` with an admin api key and they run against soulsync. requested albums land on the wishlist of the profile picked in settings, through the same add the album modal uses, and a profile without download rights means every request waits for approval. off until turned on in settings > advanced > soulsync api (thanks @Topaze77 for the request) (#1799).
- mcp endpoint at `/mcp`, so an ai client can drive soulsync. 35 tools, almost all one `/api/v1` call each, replayed with the caller's own key, so scopes, gates and rate limits are the rest api's. settings, keys, profiles and blocklist clears are not tools. also off until turned on.

## sign-ups

- choose what new sign-ups start with. settings > security has a new sign-ups block: the side a profile made by plex sign-in starts on (music, movies and tv, or both) and an optional request limit. nothing set means exactly what sign-up made before (thanks @PapaBearDoes) (#1818).
- members can open video requests. a request-only profile could file a request from a card but never open the page to see or withdraw it. it follows the music rule now: admins see it, and so does anyone without download rights (thanks @PapaBearDoes) (#1819).

## first-run setup

- the setup wizard is rebuilt as a guided react flow. it looks around first (docker or not, and plex, jellyfin, navidrome, slskd, prowlarr, qbittorrent and sabnzbd on the network), then walks admin and unlock, media server with plex pin sign-in, folders checked live, ordered download sources and a quality preset, accounts, movies and tv, artists to follow and a playlist to mirror.
- nothing saves until the last step, which saves and then checks every part for real. finish later picks up where it stopped, and the dashboard shows a "finish setting up" checklist.

## downloads page

- rebuilt for music and video on one row grammar. status lives on the art (a ring while it downloads, a spinner while it searches, play once it's done), one visible action per row with the rest in ⋯, and clicking a row opens a sheet that says what happened step by step, with asked for vs got.
- album batches read like a tracklist, playlist batches get a cover mosaic, history is grouped by day, and failed music tracks open the candidate inspector from the row.
- rows get their artist photo and cover from the library when the task didn't carry one, and a video that's downloading actually reads as downloading instead of a lone "0%".

## wishlist

- the music nebula groups artists by what's wrong, worst first: downloaded then rejected, no match found, download keeps failing, still hunting. each zone has a retry that only touches its own tracks, and clicking an artist opens a panel beside the field that says why in plain words ("only ~30s of 3:54 actually played") with grab, retry and open artist.
- the video wishlist moved onto the same page: movies, tv and youtube tabs with the same hero, zones and side panel. every action it had is still there.

## matching

- acoustid judges a file by its real length. pyacoustid dropped the duration acoustid sends, so every duration guard read nothing. with lengths in hand, a remix filed under the plain title or a different length cut stops failing a correct file, and a match that's a wildly different length stops passing (thanks @QT3496) (#1804).
- duration quarantines name the duration tolerance setting so you can actually find it, and the help text says how auto behaves (thanks @QT3496) (#1805).
- youtube music results get scored on their own title and artist. the matcher read `videoId||title` like a soulseek path, so the id swamped the title and the artist was never found, and an exact match like sugar by maroon 5 got rejected (thanks @andreamassaro9) (#1817).
- k/da and other slashed names stop getting skipped as "by other artists". names compare case and punctuation blind, and a slash only splits inside each credit (thanks chung man on discord).

## sync and review

- append drops an old wrong match once the right track is on the playlist, so the el puffin "seville" from a broken sync stops sitting next to the right one. navidrome only for now (thanks @kevin2xk) (#1801).
- deleting a wrong download in review blacklists its source, so the next sync doesn't pull the exact same file again (thanks @kevin2xk) (#1801).

## library

- redownload with "delete old file" no longer leaves two library rows when the new file lands at a different path. the original row gets repointed instead (thanks @glebosotov) (#1810).
- alt-J and friends stop splitting into two artists when one source uses a unicode hyphen (thanks @glebosotov) (#1797).
- batch tag writing only downloads covers for the tracks it actually writes (thanks @glebosotov) (#1798).
- dead file findings close once the track is gone or the file is back, and fix refuses one whose file is on disk (thanks @splitsec2) (#1802, #1803).

## video

- collections stop wishlisting things nobody asked for. the franchise and universe presets started with "wishlist the movies i'm missing" ticked, and a removed movie came straight back on the nightly sync. the box starts off now, says how many titles it'll grab, and each collection feeds a movie to the wishlist once (thanks @hilburn) (#1807).
- an optional anime library folder. episodes of anime-type shows (set by hand, or tagged with the anime genre) import there instead of the tv folder, and scans, renames and subtitles cover it. empty means off (thanks @PapaBearDoes) (#1820).
- a usenet grab's empty release folder is removed after import (thanks @PapaBearDoes) (#1815).

## audiobooks

- german release names match: eszett and umlauts fold, numbers glued to titles split, and series words count as evidence when they're not already in the title (thanks @SimpleSimonLA) (#1796).
- an mp3 whose header understates its length is measured from its bytes, so a complete 71 minute book stops reading as 7 minutes (thanks @SimpleSimonLA) (#1800).
- a terminal album bundle failure stamps its tracks, so the wishlist stops refetching the same dead release every cycle (thanks @SimpleSimonLA) (#1795).

## performance

- the gpu stops pegging while a sync or wishlist download runs. the download modal stacked four blurs and the browser redid all of them every frame the rows pulse, but three sat on near-opaque surfaces and never showed. those are gone with no visible change, about half the gpu in that modal, and the wishlist hero lost one the same way (thanks @OlivierMartinez) (#1816).

## under the hood

- the css token system is filled in across the legacy sheets, exact literals swapped for tokens, declarations a later copy always overrides are dropped, and six latent css bugs are fixed (thanks @Thundernerd) (#1792, #1793, #1809, #1811, #1812).
- react modules share Spinner, Skeleton and EmptyState primitives, and hand-rolled overlays moved onto DialogFrame (thanks @Thundernerd) (#1794, #1808, #1813).

## validation

- every fix shipped with regression tests, the matching and blur guards were negative-checked against the old code, the gpu fix was pixel-diffed and measured in real chromium on a real gpu, and the downloads, wishlist and onboarding rebuilds ran through chromium end to end.
