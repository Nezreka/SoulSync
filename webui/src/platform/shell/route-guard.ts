import { redirect } from '@tanstack/react-router';

import type { ShellBridge, ShellPageId } from './bridge';

import { getProfileHomePath } from './bridge';
import { getShellRouteByPageId } from './route-manifest';

/**
 * The permission gate every React route runs in beforeLoad.
 *
 * A denied page bounces to the profile's home page. In production a profile's
 * home is always allowed, so the bounce terminates. But if the home path IS
 * the denied page (only reachable when every page is denied — a state real
 * profiles can't get into, but a blanket `isPageAllowed: () => false` test
 * bridge can), redirecting would re-enter this guard forever: the router
 * loops, allocating ~1MB/s until the worker dies. That exact loop wedged CI
 * for hours once /discover — the test bridge's home page — became a gated
 * React route. Rendering the denied home page is the only loop-free option.
 */
export function guardPageAccess(bridge: ShellBridge, pageId: ShellPageId): void {
  if (bridge.isPageAllowed(pageId)) return;

  const home = getProfileHomePath(bridge);
  if (home === getShellRouteByPageId(pageId)?.path) return;

  throw redirect({ href: home, replace: true });
}
