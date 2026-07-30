/**
 * "Do I already own this?" for the albums and tracks on screen.
 *
 * Ported from _checkSearchResultsLibraryOwnership (search.js:568-648), with one
 * behaviour deliberately corrected: the answers are carried back to cards by
 * IDENTITY rather than by list position. See albumOwnershipByIdentity for why the
 * positional version was wrong.
 *
 * The check is best-effort. A failure leaves every card unbadged, which is what
 * the vanilla's swallowed catch did — an unbadged owned album is a small loss,
 * an error state over the whole result list is not.
 */

import { useEffect, useRef, useState } from 'react';

import type { LibraryCheckTrack, SearchAlbum, SearchTrack } from './-search.types';
import type { OwnershipState } from './-ui/search-results';

import { fetchLibraryCheck } from './-search.api';
import { albumIdentity, trackIdentity } from './-search.helpers';
import { EMPTY_OWNERSHIP } from './-ui/search-results';

/**
 * Fold the response into ownership sets.
 *
 * A track gets EITHER the library badge or the wishlist one, never both — the
 * vanilla's else-if, and not an accident: on an album card both badges are
 * positioned at top-left (style.css:40405/40436), so two would sit exactly on
 * top of each other.
 */
export function ownershipFromResponse(
  albums: SearchAlbum[],
  tracks: SearchTrack[],
  response: { albums?: boolean[]; tracks?: LibraryCheckTrack[] } | null,
): OwnershipState {
  if (!response) return EMPTY_OWNERSHIP;

  const ownedAlbums = new Set<string>();
  (response.albums ?? []).forEach((owned, index) => {
    const album = albums[index];
    if (owned && album) ownedAlbums.add(albumIdentity(album));
  });

  const ownedTracks = new Set<string>();
  const wishlistTracks = new Set<string>();
  const libraryTracks = new Map<string, LibraryCheckTrack>();
  (response.tracks ?? []).forEach((row, index) => {
    const track = tracks[index];
    if (!row || !track) return;
    const identity = trackIdentity(track);
    if (row.in_library) {
      ownedTracks.add(identity);
      // Only rows with a path are playable; the rest are owned but unreachable
      // (a Plex-only entry with no local file).
      if (row.file_path) libraryTracks.set(identity, row);
    } else if (row.in_wishlist) {
      wishlistTracks.add(identity);
    }
  });

  return { ownedAlbums, ownedTracks, wishlistTracks, libraryTracks };
}

/**
 * Ownership for the current result set, refreshed whenever it changes.
 *
 * Keyed on the row identities rather than on the arrays: a caller handing over a
 * fresh array of the same rows on every render — which is what
 * emptySourceResults() does for a source with no results — would otherwise re-ask
 * the server on every render.
 */
export function useLibraryCheck(albums: SearchAlbum[], tracks: SearchTrack[]): OwnershipState {
  const [ownership, setOwnership] = useState<OwnershipState>(EMPTY_OWNERSHIP);

  const key = [
    ...albums.map((album) => albumIdentity(album)),
    ...tracks.map((track) => trackIdentity(track)),
  ].join('|');
  const rowsRef = useRef({ albums, tracks });
  rowsRef.current = { albums, tracks };

  useEffect(() => {
    const { albums: askAlbums, tracks: askTracks } = rowsRef.current;
    if (!askAlbums.length && !askTracks.length) {
      setOwnership(EMPTY_OWNERSHIP);
      return;
    }

    const controller = new AbortController();
    let live = true;
    // fetchLibraryCheck swallows its own failures and answers {}, so the only
    // job here is to ignore an answer for a result set that is already gone.
    //
    // Honest about `live`: the abort below already handles the ordinary case,
    // and a mutant that drops this flag SURVIVES the suite because of that. It
    // stays for the ordering the abort cannot catch — a response already parsed
    // when the results changed, whose `.then` is queued behind the re-render —
    // but no test pins that, and saying otherwise would be worse than admitting
    // it.
    void fetchLibraryCheck(askAlbums, askTracks, controller.signal).then((response) => {
      if (live) setOwnership(ownershipFromResponse(askAlbums, askTracks, response));
    });
    return () => {
      live = false;
      controller.abort();
    };
  }, [key]);

  return ownership;
}
