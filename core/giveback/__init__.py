"""give back to musicbrainz and acoustid from what soulsync identified (#2335).

three opt-in parts, all off by default (Settings, Connections, Give back):

- acoustid: fingerprints of files whose recording soulsync is sure of go to
  acoustid with the musicbrainz recording id, under the person's own acoustid
  key. a standard submission, the same one picard makes.
- isrcs: an isrc in a file's tags that its musicbrainz recording lacks goes to
  musicbrainz under the person's own musicbrainz account (oauth2). also a
  standard submission picard makes.
- missing releases: an album soulsync found on spotify, deezer and so on that
  musicbrainz doesn't have opens musicbrainz's add release form already
  filled in. the person checks it and submits it themselves.

nothing edits musicbrainz on its own: musicbrainz only takes isrcs, barcodes,
tags and ratings from an app (https://musicbrainz.org/doc/MusicBrainz_API),
and anything more is a human's edit. everything goes slowly: musicbrainz
allows one request a second, and the worker sends a small batch every few
minutes at most. nothing is ever sent twice (core.giveback.store).
"""
