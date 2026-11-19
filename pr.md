# soulsync 3.6.8: `dev` → `main`

last.fm api keys need a paid pro subscription from 1 december 2026, so everything soulsync used last.fm for now falls back to free sources when the key stops working, and keeps what it already knew. phones get a pass over the worker status, menus, dialogs and pages. audiobooks and podcast episodes can require approval, the permission screens show what's really in force, and one set of words covers requests everywhere. plus track numbers stop beating titles, and the transfer folder is called the music library folder everywhere you read it.

## last.fm without pro

- **from 1 december 2026 a last.fm api key needs last.fm pro.** soulsync users each enter their own key, so most keys will stop working that day. with a working key nothing changes: last.fm stays first. without one:
  - artist page top tracks come from listenbrainz, then deezer, and the heading says which.
  - similar artists come from listenbrainz or deezer, similar tracks and radio from deezer, and your artists on discover from listenbrainz or your own plays.
  - chat and seasonal vibes find artists through deezer genres when last.fm tags aren't there.
  - artist bios come from theaudiodb, then wikipedia (linked), tags from musicbrainz, and listener numbers from listenbrainz with deezer fans shown apart. a quiet line under the bio says where it came from. last.fm data you already have is never overwritten.
- **a refused key is noticed and not hammered.** soulsync remembers it for a few hours and tries again after, or as soon as the key changes, so subscribing brings last.fm back on its own. the last.fm card says what happened in plain words.
- **answers are cached**, 30 days for catalogue data and a few hours for your own, so a key that lapses doesn't empty pages overnight.
- **scrobbles wait.** while the key is refused nothing is posted and nothing is lost, they go out once it works. history import has no free stand-in, so it says to use the listenbrainz import, and listenbrainz is marked the recommended scrobbler.
- we can't know the exact error last.fm will send a refused key until 1 december, so soulsync treats the likely ones (invalid or suspended key, 401/403) as a refusal.

## phones

- **worker status opens on a tap (thanks Thibs, #2180).** status only showed on hover, so a tap paused a worker or did nothing. a tap now opens a sheet with its status and pause or resume, on the music and video dashboards.
- **the menu button stays on an open playlist (thanks c5pie, #2181).** a rule from when playlists opened as a popup hid it, so the x was the only way out.
- **every page reaches its end (thanks wolf39us, #2182).** pages were sized to the screen with the browser's bars hidden, so the bottom sat under the bottom bar.
- **the album menu stays on screen (thanks wolf39us, #2183)** and scrolls when it's tall, and enrich opens on a tap instead of closing again.
- **get show draws its arrow (thanks Thibs, #2184).** it was a character android's fonts don't have. every download arrow is drawn now.
- **import cards fit (thanks Thibs, #2185)** and **long confirm boxes keep their buttons (thanks c5pie, #2186).** the confirm box is shared, so every long one is fixed, keep best's too.
- **settings says saved only after a save (thanks c5pie, #2187)**, and the status no longer sits on the section title.

## people and permissions

- **audiobook and podcast episode requests can require approval (thanks SeadogsBooty, #2188).** there was no request flow for them at all. a request now lands on the requests page next to music and video ones, and approving it gets the book or the episode. auto-approve stays the default, so nothing changes on upgrade.
- **controls show the value in force (thanks SeadogsBooty, #2189).** a template held by the global policy showed its own saved value. it shows what the policy allows now, and says separately what the template has.
- **back to template only shows with personal changes to drop (thanks SeadogsBooty, #2190).**
- **a member's notifications (thanks SeadogsBooty, #2191).** live chat messages went to every browser, including members without chat, which is what kept showing "chat & arcade isn't part of your profile". chat now only reaches people allowed to chat. refusals and progress notes don't pile up in a member's notifications, and switching profile clears the panel.
- **one set of words (thanks SeadogsBooty, #2192).** movie requests, tv show requests, music requests, audiobook requests, podcast episode requests, with auto-approve, require approval and requests off everywhere. maximum content rating, maximum request limit, allowed quality profiles, default quality profile. nothing behaves differently.
- **the admin menu no longer flashes after signing back in (thanks SeadogsBooty, #2156).** the page knew who you were, so it skipped the wait, and the script that trims the menu loads after it. a member's menu starts hidden now until their own profile trims it.

## library

- **titles beat track numbers (thanks wolf39us).** other versions and reassign lined tracks up by number first, so "1. lost stars" landed on "1. maps". the title wins now, with length as the tiebreak, and a tracklist refresh follows the same rule.
- **a song's number heals from its file.** a track that came over from a single kept the single's number. when two different songs share a number, the one whose file says otherwise moves to its own, on refresh & scan, update discography and the other versions merge. a number set by hand stays.
- **a deleted file is gone.** when a file went missing, the lookup found the same song title in another album folder and treated it as the deleted one. a file elsewhere only counts now if it has the same file name or the same track number, so a moved album still resolves.
- **rename / organize keeps $quality (thanks Maegfea, #2195).** the planner never read the file's quality, so the rename dropped it. it reads it the way a download does, only when a template uses $quality.
- **preview rename skips an album you removed (thanks wolf39us, #2194)** instead of failing every album with "no tracks found".

## also

- **the music library folder.** what was called the transfer folder reads as the music library folder everywhere: settings search, help, docs, health and tools. in docker it's still the `/app/Transfer` mount and the default is still `./Transfer`, so nothing about your setup changes.
