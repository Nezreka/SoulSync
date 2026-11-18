/**
 * #2174: Help & Docs, its search and the help sheet show a member only the
 * guides for what their profile can use. the admin's guides (setup, Docker,
 * settings, the API, troubleshooting, tools) and every part of SoulSync the
 * member's profile doesn't have are gone, not greyed out. runs the real
 * docs.js and docs-content files.
 */

import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import { loadDocs, resetDocs, type DocsSection } from './docs';
import { installRealDocs, readAsAdmin, readAsMember } from './docs.test-utils';

type Rows = Record<string, unknown>;

/** a member's permission map, as window.SoulSyncPerms answers it */
function signInMember(rows: Rows = {}): void {
  readAsMember();
  const map: Rows = {
    'music.access': true,
    'music.get': 'ask',
    'music.search': true,
    'music.library': true,
    'music.discover': true,
    'music.wishlist': true,
    'music.watchlist': true,
    'music.playlist_sync': true,
    'movies.access': true,
    'tv.access': true,
    'video.search': true,
    'video.library': true,
    'chat.use': true,
    'account.connect': true,
    ...rows,
  };
  window.SoulSyncPerms = {
    on: (id: string) => map[id] === true,
    mode: (media: string) =>
      map[`${media}.access`] === false ? 'none' : ((map[`${media}.get`] as string) ?? 'none'),
    get: () => ({ live: true, isAdmin: false }),
  } as never;
}

const sections = (): DocsSection[] => window.SoulSyncDocs!.sections();
const sectionIds = () => sections().map((s) => s.id);
const pageIds = () => sections().flatMap((s) => s.pages.map((p) => p.id));

beforeAll(() => installRealDocs());
afterEach(() => {
  delete window.SoulSyncPerms;
  delete window.getCurrentProfileContext;
  resetDocs();
});

describe('who each guide is for', () => {
  it('every section says who it is for, in words docs.js knows', () => {
    readAsAdmin();
    const known = /^(everyone|admin|video|[a-z_]+\.[a-z_]+(=(ask|download))?)$/;
    for (const s of sections()) {
      expect(s.needs, s.id).toBeDefined();
      for (const n of [s.needs].flat()) expect(String(n), s.id).toMatch(known);
      for (const p of s.pages) {
        const own = (p as { needs?: unknown }).needs;
        if (own !== undefined) for (const n of [own].flat()) expect(String(n), p.id).toMatch(known);
      }
    }
  });

  it('the admin reads every guide', () => {
    readAsAdmin();
    expect(sectionIds()).toEqual(expect.arrayContaining(['settings', 'api', 'troubleshooting']));
    expect(pageIds()).toEqual(expect.arrayContaining(['gs-docker', 'ts-logs', 'ts-debug']));
  });

  it('nobody signed in reads the admin guides as nobody: only the open ones', () => {
    const ids = pageIds();
    expect(ids).toContain('gs-overview');
    expect(ids).not.toContain('gs-docker');
  });
});

describe('a member', () => {
  it('never sees setup, Docker, settings, the API, tools or diagnostics', () => {
    signInMember();
    const secs = sectionIds();
    for (const id of [
      'settings',
      'api',
      'video-api',
      'video-settings',
      'video-tools',
      'hydrabase',
      'metasync',
    ]) {
      expect(secs, id).not.toContain(id);
    }
    const pages = pageIds();
    for (const id of [
      'gs-first-setup',
      'gs-connecting',
      'gs-folders',
      'gs-docker',
      'ts-health',
      'ts-logs',
      'ts-debug',
      'ts-common',
      'search-sources',
      'search-quality',
      'dash-repair',
      'dash-backup',
      'auto-system',
      'prof-policy',
      'prof-templates',
    ]) {
      expect(pages, id).not.toContain(id);
    }
  });

  it('keeps the guides for what they use', () => {
    signInMember();
    const pages = pageIds();
    for (const id of [
      'gs-overview',
      'gs-interface',
      'ts-help',
      'ts-search',
      'search-enhanced',
      'disc-hero',
      'art-wishlist',
      'prof-requests',
      'prof-accounts',
      'vsearch-search',
    ]) {
      expect(pages, id).toContain(id);
    }
  });

  it('loses a part of SoulSync with the switch for it', () => {
    signInMember({ 'music.playlist_sync': false, 'chat.use': false });
    expect(sectionIds()).not.toContain('sync');
    expect(sectionIds()).not.toContain('chat-jukebox');
    // the mirrored playlists guide goes, the import-from-text guide with it
    expect(pageIds()).not.toContain('imp-textfile');
  });

  it('a video-only profile reads no music guides, and the other way round', () => {
    signInMember({ 'music.access': false });
    expect(sectionIds()).not.toEqual(expect.arrayContaining(['search']));
    expect(pageIds()).not.toContain('disc-hero');
    expect(pageIds()).toContain('vsearch-search');
    signInMember({ 'movies.access': false, 'tv.access': false });
    expect(sectionIds().some((id) => id.startsWith('video-'))).toBe(false);
    expect(pageIds()).toContain('disc-hero');
  });

  it('reads how to download only when they may download right away', () => {
    signInMember();
    expect(pageIds()).not.toContain('lib-missing');
    signInMember({ 'music.get': 'download' });
    expect(pageIds()).toContain('lib-missing');
  });

  it('search finds only what they may read', async () => {
    signInMember();
    const ids = (await loadDocs()).map((a) => a.id);
    expect(ids).toContain('gs-overview');
    expect(ids).not.toContain('gs-docker');
    expect(ids).not.toContain('ts-logs');
  });

  it('an admin guide is hidden, not missing, so the sheet can say whose it is', () => {
    signInMember();
    expect(window.SoulSyncDocs!.hidden!('gs-docker')).toBe(true);
    expect(window.SoulSyncDocs!.visible!('gs-docker')).toBe(false);
    expect(window.SoulSyncDocs!.hidden!('gs-overview')).toBe(false);
    expect(window.SoulSyncDocs!.hidden!('no-such-guide')).toBe(false);
  });

  it('search follows an admin change without a reload', async () => {
    signInMember({ 'music.playlist_sync': false });
    expect((await loadDocs()).some((a) => a.id === 'sync-overview')).toBe(false);
    signInMember({ 'music.playlist_sync': true });
    expect((await loadDocs()).some((a) => a.id === 'sync-overview')).toBe(true);
  });
});
