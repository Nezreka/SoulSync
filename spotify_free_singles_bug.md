# spotify free search-source bug (Boulder, Aug 4) — ROOT-CAUSED + FIXED

## symptoms (search page, source = Spotify Free; other sources fine)
1. ALL singles missing for ALL artists; EPs/singles shown typed as "album".
2. Cover art correct (discography scrape works; only click-through breaks).
3. Clicking any release → completely WRONG tracklist (Katy Perry EP → 10-track
   trance compilation).

## root causes (all three confirmed in code)

1. **Wrong tracklist — the allow_fallback gate** (core/spotify_client.py).
   FIVE free branches (get_track_details:1874, get_album:1972,
   get_album_tracks:2056, get_artist_albums:2174, get_artist:2239) were gated
   `if allow_fallback and self._free_active() ...`. The exact-source layer
   (core/metadata/album_tracks.py get_album_for_source → client.get_album(id,
   allow_fallback=False); source chain from _get_source_chain_for_lookup with
   frontend mapping spotify_free→'spotify') got None from the spotify hop for
   every free user, then walked the chain to Deezer/iTunes WITH THE SAME
   SPOTIFY BASE-62 ID → id-space collision → random other release's tracks.
   FIX: free branch no longer requires allow_fallback (free IS Spotify — same
   catalog, same ids; allow_fallback governs switching to OTHER sources). The
   `not _is_itunes_id(id)` guard stays.

2. **Singles missing** (core/spotify_free_metadata.py get_artist_albums_list).
   SpotipyFree's `artist_albums(artistId, ..., include_groups="album")`
   DEFAULTS to the albums section only — our adapter passed no include_groups,
   so singles/compilations were never fetched. FIX: fetch 'album', 'single',
   'compilation' sections explicitly.

3. **Everything typed "album"** (spotipyfree package, Formatter.formatAlbum:127
   hardcodes `album["album_type"] = "album"`). FIX: adapter re-tags album_type
   per requested section (can't/shouldn't patch site-packages).

## fix + tests
- core/spotify_client.py: 5 gates swapped to `if self._free_active() and not
  self._is_itunes_id(...)` with explanatory comment at get_track_details.
- core/spotify_free_metadata.py: get_artist_albums_list fetches 3 sections +
  re-tags album_type.
- tests/test_spotify_free_metadata.py: 4 new tests — exact-source get_album +
  get_album_tracks serve via free (verified FAIL pre-fix), plain-spotify
  no-free exact-source still None (unchanged behavior), artist_albums fetches
  all 3 sections + re-tags (verified FAIL pre-fix).

## notes / residuals
- SpotipyFree's artist_albums does a FULL PublicAlbum scrape per release
  (N+1); with 3 sections it's 3 section walks. Slow but correct; perf pass
  possible later (the package's own design).
- Boulder should live-verify: free source → artist singles present + typed
  right, clicking a release shows ITS tracks.
- Related earlier fix: get_client_for_source availability gate (debae5028).
  These free-adapter bugs were pre-existing; the resolver fix made free
  actually serve these paths, exposing them.

## REMAINING (Aug 4 evening): free discography too slow → frontend "network error"
Status: search via free WORKS post-289dc4acf (spotify results, base-62 ids).
Artist detail: request starts (log 16:44:29) and never completes — SpotipyFree's
artist_albums scrapes EVERY release individually (spotapi.PublicAlbum per id,
sequential) and our 3-section fetch triples it. Kendrick ≈ minutes; the React
fetch times out → "Failed to load artist details: network error".

FIX DESIGN (next session): stop using SpotipyFree.artist_albums for listings.
In core/spotify_free_metadata.get_artist_albums_list, call the underlying
spotapi directly: spotapi.Artist().get_artist(artist_id)["data"]["artistUnion"]
["discography"] — the sections (albums/singles/compilations) ALREADY carry
id/name/date/coverArt per release (see site-packages/spotipyfree/Spotify.py
artist_albums: it iterates section items and needlessly re-fetches each via
self.album()). Normalize in OUR adapter (id from uri, name, images from
coverArt.sources, release_date from date, album_type per section, total_tracks
if present) = ONE network call for the whole discography. Track listings stay
per-album on click (get_album/get_album_tracks — already work). Add tests with
a recorded discography fixture shape.
