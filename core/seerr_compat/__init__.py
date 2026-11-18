"""seerr (jellyseerr / overseerr) compatible api for the video side (#2151).

request apps built for seerr, like litefin's jellyfin plugin, point their seerr
url at soulsync. discover and search come from soulsync's tmdb engine, media
status from the video library, wishlist and requests, and a request lands on
the asking profile's video wishlist (or waits for approval like any other
video request).
"""
