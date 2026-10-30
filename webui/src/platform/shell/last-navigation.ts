/**
 * Where the last navigation came from.
 *
 * the router's resolvedLocation is the previous location only while a new
 * route is still resolving. a search-param hop on one route (/library to
 * /library?artist=1) has already resolved by the time the page mounts, so a
 * page asking "where did i come from" there got its own url back.
 */

export interface NavigationFrom {
  pathname: string;
  searchStr: string;
}

let lastFrom: NavigationFrom | null = null;

export function noteNavigationFrom(from: { pathname: string; searchStr?: string } | undefined) {
  lastFrom = from ? { pathname: from.pathname, searchStr: from.searchStr ?? '' } : null;
}

export function lastNavigationFrom(): NavigationFrom | null {
  return lastFrom;
}
