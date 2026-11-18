thanks for the screenshot, it showed both problems.

the path: qbittorrent says a torrent is 100% while it's still moving the finished file out of its temp folder. even with "keep incomplete torrents in" off, the file can sit in an incomplete path for a moment (a category path, or the arr-style /incomplete layout). soulsync started the import from that path right then, qbit finished its move, the file was gone, and the row got stuck pointing at /incomplete/ for good.

what changed:
- soulsync waits while qbit says it's moving the file, and reads the path again once it's done
- if the file moves while the import is running, it doesn't fail anymore, it asks qbit again and imports from wherever the file ended up
- retry on a stuck row like yours now asks qbit where the file is and re-imports it, instead of grabbing a different release
- manual import does the same, so it finds the file instead of saying it's gone

the wrong show: when you grab from a show's page, it sent your library's own id for the show and didn't say it was a library id. the downloads card treated it as a tmdb id, and your library's Last Week Tonight id happens to be tmdb's id for Seven of One. your metadata isn't corrupted, only that card was looking up the wrong thing. grabs now say where the id came from, and the card won't guess anymore.

once you're on the update, hit retry on that download and it should import from where qbit put it.
