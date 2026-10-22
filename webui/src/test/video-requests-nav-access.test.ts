import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Video Requests is how a profile without download rights asks for a movie
 * or show, so it follows the music Requests rule: admins answer, profiles
 * without download rights ask. It is not a page toggle. `video-requests` is
 * not in VALID_PAGE_IDS or the profile editor's page picker, so gating it on
 * allowed_pages hid it from every member with a page list (every Plex or
 * OIDC sign-up): they could file a request from a card but never open the
 * page to see or withdraw it.
 *
 * These run the real sidebar loop (init.js) and route guard (video-side.js)
 * against stub profiles.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');

function between(src: string, start: string, end: string): string {
  const a = src.indexOf(start);
  expect(a, `marker not found: ${start}`).toBeGreaterThan(-1);
  const b = src.indexOf(end, a);
  expect(b, `end marker not found: ${end}`).toBeGreaterThan(-1);
  return src.slice(a, b);
}

const INIT = read('static/init.js');
const SIDE = read('static/video/video-side.js');

type Profile = {
  id: number;
  is_admin: boolean;
  can_download: boolean;
  allowed_pages: string[] | null;
  allowed_sides?: string;
};

// a Plex / OIDC sign-up after an admin gives it the video side
const MEMBER: Profile = {
  id: 3,
  is_admin: false,
  can_download: false,
  allowed_sides: 'both',
  allowed_pages: ['discover', 'search', 'library', 'video-search', 'video-discover'],
};
const DOWNLOADER: Profile = { ...MEMBER, id: 4, can_download: true };
const ADMIN: Profile = { id: 1, is_admin: true, can_download: true, allowed_pages: null };

const canDownloadFor = (p: Profile) => () => p.id === 1 || p.can_download;

/** the sidebar loop's display value for one video nav button */
function navDisplay(profile: Profile, page: string): string {
  const loop = between(
    INIT,
    'const VIDEO_ADMIN_ONLY',
    "document.body.classList.toggle('video-admin'",
  );
  const btn = { style: { display: 'unset' }, getAttribute: () => page };
  const document = { querySelectorAll: () => [btn] };
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function('document', 'currentProfile', 'canDownload', loop)(
    document,
    profile,
    canDownloadFor(profile),
  );
  return btn.style.display;
}

/** the route guard's answer for one video page */
function routeAllowed(profile: Profile, page: string): boolean {
  const sideAllowed = between(SIDE, 'function sideAllowed(', 'function videoPageAllowed(');
  const guard = between(
    SIDE,
    'function videoPageAllowed(',
    '// Hand off to the unified React downloads page',
  );
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const make = new Function(
    'currentProfile',
    'canDownload',
    'DETAIL_PAGES',
    `${sideAllowed}\n${guard}\nreturn videoPageAllowed;`,
  );
  return make(profile, canDownloadFor(profile), {})(page);
}

describe('video Requests follows the requests rule, not the page list', () => {
  it('a member without download rights sees the Requests nav', () => {
    expect(navDisplay(MEMBER, 'video-requests')).toBe('');
  });

  it('a member without download rights can open the Requests page', () => {
    expect(routeAllowed(MEMBER, 'video-requests')).toBe(true);
  });

  it('a profile that can download has no Requests nav or page', () => {
    expect(navDisplay(DOWNLOADER, 'video-requests')).toBe('none');
    expect(routeAllowed(DOWNLOADER, 'video-requests')).toBe(false);
  });

  it('the admin keeps it', () => {
    expect(navDisplay(ADMIN, 'video-requests')).toBe('');
    expect(routeAllowed(ADMIN, 'video-requests')).toBe(true);
  });

  it('a music-only member still cannot open any video page', () => {
    expect(routeAllowed({ ...MEMBER, allowed_sides: 'music' }, 'video-requests')).toBe(false);
  });

  it('other video pages still follow the page list', () => {
    expect(navDisplay(MEMBER, 'video-search')).toBe('');
    expect(navDisplay(MEMBER, 'video-library')).toBe('none');
    expect(routeAllowed(MEMBER, 'video-library')).toBe(false);
  });
});
