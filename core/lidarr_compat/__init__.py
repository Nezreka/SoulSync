"""lidarr compatible api: soulsync pretends to be lidarr (#1799).

request apps like singarr and musicseerr 1.x only know how to talk to lidarr.
this lets them point at soulsync instead. an album they ask for lands on the
wishlist of the profile picked in settings and downloads the normal way.

lidarr clients hold on to integer ids for artists and albums, so the artists
and albums they add are kept in two small tables (store.py). everything else
comes from musicbrainz, the same ids lidarr uses.
"""
