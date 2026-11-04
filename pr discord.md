**SoulSync 3.6.3 is out** :musical_note:

A hot fix for 3.6.2. On a big library the Library page could time out with "Could not load your library". The artist list's membership check walked every album for every artist; it uses its indexes now and loads in a second or two, and the first visit after a restart is warmed in the background.

When SABnzbd or the torrent client fails a movie or episode, or a torrent stalls, SoulSync now blocklists that release and grabs the next one your quality profile ranks right away, instead of waiting for the next wishlist pass. You only get the failed notification when nothing else could be taken. It's on by default, with a switch on the usenet client card (thanks @SeadogsBooty). Cancel also takes a torrent or usenet download out of the client now, and the Downloads menu has Block and retry failed to replace every failed release at once (thanks c5pie).

After downloads, Plex and Jellyfin now scan only the album folders that changed instead of the whole library (#1978). Jellyfin on localhost no longer costs two seconds per connection on Windows, Discover's decades, labels and undiscovered albums shelves are much faster, and MusicBrainz lookups stop paying a wasted request per album.

Fixes: a failing wishlist sync waits before retrying instead of using up its tries in a second, Bandcamp's bot check no longer marks albums as not found, a blocked audiobook release stays out of that profile's searches, and Settings > MetaSync says whether your install is being counted.

Full notes on GitHub.
