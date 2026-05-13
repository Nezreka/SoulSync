import { describe, expect, it } from 'vitest';

import {
  getShellRouteByPageId,
  legacyShellRoutes,
  normalizeShellPath,
  reactShellRoutes,
  resolveLegacyShellPageFromPath,
  resolveShellPageFromPath,
  shellRouteManifest,
} from './route-manifest';

describe('shellRouteManifest', () => {
  it('resolves page ids from explicit paths', () => {
    expect(resolveShellPageFromPath('/issues')).toBe('issues');
    expect(resolveShellPageFromPath('/discover')).toBe('discover');
    expect(resolveShellPageFromPath('/watchlist')).toBe('watchlist');
    expect(resolveShellPageFromPath('/active-downloads')).toBe('active-downloads');
    expect(resolveShellPageFromPath('/artist-detail')).toBe('artist-detail');
    expect(resolveShellPageFromPath('/artists')).toBeNull();
  });

  it('treats the root path as unresolved so the shell can redirect to the profile home', () => {
    expect(resolveShellPageFromPath('/')).toBeNull();
  });

  it('normalizes trailing slashes before resolving', () => {
    expect(normalizeShellPath('/issues/')).toBe('/issues');
    expect(resolveShellPageFromPath('/issues/')).toBe('issues');
  });

  it('keeps a route entry for every manifest page id', () => {
    expect(shellRouteManifest).not.toHaveLength(0);
    expect(getShellRouteByPageId('dashboard')?.path).toBe('/dashboard');
    expect(getShellRouteByPageId('hydrabase')?.path).toBe('/hydrabase');
    expect(getShellRouteByPageId('watchlist')?.path).toBe('/watchlist');
    expect(getShellRouteByPageId('tools')?.path).toBe('/tools');
    expect(getShellRouteByPageId('artist-detail')?.path).toBe('/artist-detail');
  });

  it('tracks whether a route is rendered by React or the legacy shell', () => {
    expect(getShellRouteByPageId('issues')?.kind).toBe('react');
    expect(getShellRouteByPageId('discover')?.kind).toBe('legacy');
    expect(reactShellRoutes.map((route) => route.pageId)).toEqual(['issues']);
    expect(legacyShellRoutes.some((route) => route.pageId === 'dashboard')).toBe(true);
  });

  it('only resolves legacy page ids for legacy-owned paths', () => {
    expect(resolveLegacyShellPageFromPath('/search')).toBe('search');
    expect(resolveLegacyShellPageFromPath('/active-downloads')).toBe('active-downloads');
    expect(resolveLegacyShellPageFromPath('/tools')).toBe('tools');
    expect(resolveLegacyShellPageFromPath('/issues')).toBeNull();
    expect(resolveLegacyShellPageFromPath('/does-not-exist')).toBeNull();
  });
});
