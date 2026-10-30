/**
 * Where navigations came from, for the artist pages' back button.
 *
 * the router's resolvedLocation is the previous location only while a new
 * route is still resolving. a search-param hop on one route (/library to
 * /library?artist=1) has already resolved by the time the page mounts, so a
 * page asking "where did i come from" there got its own url back.
 *
 * an ARRIVAL is a step from outside the artist views into one (search,
 * discover, the library grid -> an artist or album). it's recorded the moment
 * it happens, so the url tweaks the artist page makes right after it opens
 * can't overwrite where you really came from, and it's handed out once.
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
let arrival: NavigationFrom | null = null;

/** an artist or album page: the library's artist/album views, or artist-detail */
export function isArtistView(loc: Loc | null | undefined): boolean {
  if (!loc) return false;
  if (loc.pathname.includes('/artist-detail')) return true;
  if (!loc.pathname.replace(/\/+$/, '').endsWith('/library')) return false;
  const params = new URLSearchParams(loc.searchStr ?? '');
  return params.has('artist') || params.has('album') || params.has('discover');
}

export function noteNavigation(from: Loc | undefined, to?: Loc | undefined) {
  if (!from) {
    lastFrom = null;
    return;
  }
  lastFrom = { pathname: from.pathname, searchStr: from.searchStr ?? '' };
  if (to && isArtistView(to) && !isArtistView(from)) arrival = lastFrom;
}

export function lastNavigationFrom(): NavigationFrom | null {
  return lastFrom;
}

/** the page an arrival at an artist view came from, once; null when there
 *  was none since the last time it was taken (a cold load, or a hop inside) */
export function takeArtistArrival(): NavigationFrom | null {
  const taken = arrival;
  arrival = null;
  return taken;
}

/** a new router starts with no history (tests make one per render) */
export function resetNavigationHistory() {
  lastFrom = null;
  arrival = null;
}
