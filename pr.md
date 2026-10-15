# soulsync 3.5.6: `dev` → `main`

a quick follow-up to 3.5.5. playlist sync stops grabbing the wrong song and stops letting one missing song block a whole playlist, reorganize picks up where it stopped, and auto-import skips what you already own. scope: everything on dev since 3.5.5.

## playlist sync

- the album fallback needs the artist to match. when a track wasn't found under its own artist, sync fell back to "same title on a same-named album" and never checked who the artist was, so a totally different song you already owned stood in for the real one and the real one never downloaded (thanks @kevin2xk) (#1715, #1717).
- one dead song no longer blocks a whole playlist. a song navidrome doesn't have anymore (deleted from review, say) kept its row in the library, and the playlist guard refused the entire write over that one id. so every sync matched 110 tracks and wrote none. now that id is skipped and logged and the rest get written. a write that would also drop something already on the playlist still refuses, since the dead id could be that song under a new key (#1715).

## reorganize

- a stopped scan resumes where it left off. library reorganize started at the first album every run, and a stopped live run lost every move it had planned since they were only queued at the end. moves queue in batches now, the last finished album is saved, the next run starts right after it, and it goes back to the top after a full pass. a different mode, naming or server starts it over (thanks @QT3496) (#1716).

## imports and downloads

- auto-import skips what you own. the library pre-check only ran on the manual import page, so staging drops went through the whole pipeline. now they ask the same gate, the inbox shows "3 imported · 9 already in library" with a per-track list of what happened, and skipped files count as remnants so a half-imported folder isn't picked up again every scan.
- new opt-in "clear finished folders" removes a folder from staging once every track was imported or already owned. never the staging root, never a folder with music or unknown files left in it.
- the album modal knows what you own the moment it opens. opened from a discography, every track sat on pending until begin analysis, which goes straight on to downloading. the ownership check runs on open now.

## validation

- every fix shipped with regression tests and green neighboring suites, and the guard fix was negative-checked (break it, watch the tests fail).
