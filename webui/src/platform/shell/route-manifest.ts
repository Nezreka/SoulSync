export const shellPageIds = [
  'dashboard',
  'sync',
  'search',
  'discover',
  'playlist-explorer',
  'watchlist',
  'wishlist',
  'automations',
  'active-downloads',
  'library',
  'tools',
  'artist-detail',
  'stats',
  'import',
  'settings',
  'issues',
  'help',
  'hydrabase',
] as const;

export type ShellPageId = (typeof shellPageIds)[number];
export type ShellRouteKind = 'legacy' | 'react';

export interface ShellRouteDefinition {
  pageId: ShellPageId;
  path: `/${string}`;
  kind: ShellRouteKind;
}

export const shellRouteManifest: readonly ShellRouteDefinition[] = [
  { pageId: 'dashboard', path: '/dashboard', kind: 'legacy' },
  { pageId: 'sync', path: '/sync', kind: 'legacy' },
  { pageId: 'search', path: '/search', kind: 'legacy' },
  { pageId: 'discover', path: '/discover', kind: 'legacy' },
  { pageId: 'playlist-explorer', path: '/playlist-explorer', kind: 'legacy' },
  { pageId: 'watchlist', path: '/watchlist', kind: 'legacy' },
  { pageId: 'wishlist', path: '/wishlist', kind: 'legacy' },
  { pageId: 'automations', path: '/automations', kind: 'legacy' },
  { pageId: 'active-downloads', path: '/active-downloads', kind: 'legacy' },
  { pageId: 'library', path: '/library', kind: 'legacy' },
  { pageId: 'tools', path: '/tools', kind: 'legacy' },
  { pageId: 'artist-detail', path: '/artist-detail', kind: 'legacy' },
  { pageId: 'stats', path: '/stats', kind: 'legacy' },
  { pageId: 'import', path: '/import', kind: 'legacy' },
  { pageId: 'settings', path: '/settings', kind: 'legacy' },
  { pageId: 'issues', path: '/issues', kind: 'react' },
  { pageId: 'help', path: '/help', kind: 'legacy' },
  { pageId: 'hydrabase', path: '/hydrabase', kind: 'legacy' },
] as const;

const routeByPageId = new Map(shellRouteManifest.map((route) => [route.pageId, route]));
const routeByPath = new Map(shellRouteManifest.map((route) => [route.path, route]));

export const reactShellRoutes = shellRouteManifest.filter((route) => route.kind === 'react');
export const legacyShellRoutes = shellRouteManifest.filter((route) => route.kind === 'legacy');

export function normalizeShellPath(pathname: string): string {
  if (!pathname) return '/';
  if (pathname === '/') return '/';
  const normalized = pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
  return normalized || '/';
}

export function getShellRouteByPageId(pageId: ShellPageId): ShellRouteDefinition | undefined {
  return routeByPageId.get(pageId);
}

export function getShellRouteByPath(pathname: string): ShellRouteDefinition | undefined {
  return routeByPath.get(normalizeShellPath(pathname) as `/${string}`);
}

export function resolveShellPageFromPath(pathname: string): ShellPageId | null {
  return getShellRouteByPath(pathname)?.pageId ?? null;
}

export function resolveLegacyShellPageFromPath(pathname: string): ShellPageId | null {
  const route = getShellRouteByPath(pathname);
  return route?.kind === 'legacy' ? route.pageId : null;
}
