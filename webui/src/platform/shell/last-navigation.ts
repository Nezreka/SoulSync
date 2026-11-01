/**
 * Where navigations came from, for the artist pages' back button.
 *
 * the router's resolvedLocation is the previous location only while a new
 * route is still resolving. a search-param hop on one route (/library to
 * /library?artist=1) has already resolved by the time the page mounts, so a
 * page asking "where did i come from" there got its own url back.
 *
 * An artist origin is the step from outside the artist views into one (search,
 * discover, the library grid -> an artist or album). It survives redirects,
 * header changes, and album hops. Both artist page implementations read it,
 * so it cannot be a one-shot handoff.
 */

export interface NavigationFrom {
  pathname: string;
  searchStr: string;
}

interface Loc {
  pathname: string;
  searchStr?: string;
}

let lastFrom: NavigationFrom | null = null;
let artistOrigin: NavigationFrom | null = null;

/** an artist or album page: the library's artist/album views, or artist-detail */
export function isArtistView(loc: Loc | null | undefined): boolean {
  if (!loc) return false;
  if (loc.pathname.includes('/artist-detail')) return true;
  if (!loc.pathname.replace(/\/+$/, '').endsWith('/library')) return false;
  const params = new URLSearchParams(loc.searchStr ?? '');
  return params.has('artist') || params.has('album') || params.has('discover');
}

export function noteNavigation(from: Loc | undefined, to?: Loc) {
  if (!from) {
    lastFrom = null;
    return;
  }
  const previous = { pathname: from.pathname, searchStr: from.searchStr ?? '' };
  lastFrom = previous;
  if (!to) return;
  if (isArtistView(to)) {
    if (!isArtistView(from)) artistOrigin = previous;
  } else {
    artistOrigin = null;
  }
}

/** The source of the current router transition, if there is one. */
export function lastNavigationFrom(): NavigationFrom | null {
  return lastFrom;
}

/** The page this artist navigation chain started on. Reading it does not
 * consume it: route redirects and both artist page implementations need the
 * same answer until the chain is left. */
export function artistNavigationOrigin(): NavigationFrom | null {
  return artistOrigin;
}

/** a new router starts with no history (tests make one per render) */
export function resetNavigationHistory() {
  lastFrom = null;
  artistOrigin = null;
}
