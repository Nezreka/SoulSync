import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The video tools page is a shared page, like the wishlist: the React /tools
 * route on ?media=video. The video sidebar hands /video-tools off to it, the
 * shell side never flips, and a profile gets in with the VIDEO tools
 * permission. These run the real functions out of video-side.js and init.js.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const SIDE = read('static/video/video-side.js');
const SIDE_CSS = read('static/video/video-side.css');
const INIT = read('static/init.js');
const INDEX = read('index.html');
const CORE = read('static/core.js');
const SETTINGS = read('static/video/video-settings.js');
const ISSUES = read('static/video/video-issues.js');

function between(src: string, start: string, end: string): string {
  const a = src.indexOf(start);
  expect(a, `marker not found: ${start}`).toBeGreaterThan(-1);
  const b = src.indexOf(end, a);
  expect(b, `end marker not found: ${end}`).toBeGreaterThan(-1);
  return src.slice(a, b);
}

function parseSharedPath() {
  const body = [
    'var VIDEO_WISHLIST_TABS = { movies: 1, tv: 1, youtube: 1 };',
    extractFunction('parseSharedPath', SIDE),
  ].join('\n');
  const session = { getItem: () => null, setItem: () => {} };
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function('sessionStorage', `${body}\nreturn parseSharedPath;`)(session) as (
    path: string,
    search: string,
  ) => string | null;
}

describe('the sidebar hands video tools off to the React page', () => {
  it('navigate() hands video-tools off before any subpage is shown', () => {
    const nav = between(SIDE, 'function navigate(', 'function applySide(');
    expect(nav).toContain("if (pageId === 'video-tools') { gotoUnifiedTools(mode, job); return; }");
    expect(nav.indexOf('gotoUnifiedTools(mode, job)')).toBeLessThan(
      nav.indexOf('showPage(pageId)'),
    );
  });

  it('routes to /tools?media=video, marks the nav and never flips the side', () => {
    const handoff = between(SIDE, 'function gotoUnifiedTools(', 'function navigate(');
    expect(handoff).toContain("setActiveNav('video-tools')");
    expect(handoff).toContain("document.body.setAttribute('data-video-page', 'video-tools')");
    expect(handoff).toContain("var query = { media: 'video' };");
    expect(handoff).toContain("navigateToPage('tools', { query: query, replace: replace })");
    expect(handoff).not.toContain('_switchAppSide(');
    expect(handoff).not.toContain('applySide(');
  });

  it('a job deep link rides the hand-off (settings, issues)', () => {
    const handoff = between(SIDE, 'function gotoUnifiedTools(', 'function navigate(');
    expect(handoff).toContain('if (job) query.job = job;');
    const listener = between(SIDE, "addEventListener('soulsync:video-navigate'", '});');
    expect(listener).toContain('navigate(pageId, undefined, job || undefined)');
    expect(SETTINGS).toContain("{ detail: { page: 'video-tools', job: MISSING_SUBS_JOB } }");
    expect(ISSUES).toContain("{ detail: { page: 'video-tools', job: 'duplicate_movies' } }");
  });

  it('is handed off, so the boot never re-asserts /video-tools over the React url', () => {
    expect(SIDE).toContain(
      "var HANDED_OFF = { 'video-wishlist': 1, 'video-tools': 1, 'video-calendar': 1, 'video-dashboard': 1 };",
    );
  });

  it('the css reveals the React host and hides the video host for it', () => {
    expect(SIDE_CSS).toContain(
      'body[data-side="video"][data-video-page="video-tools"] #webui-react-root',
    );
    expect(SIDE_CSS).toContain(
      'body[data-side="video"][data-video-page="video-tools"] #video-page-host',
    );
    // the old vanilla subpage, its scripts and its css hook are gone
    expect(INDEX).not.toContain('data-video-subpage="video-tools"');
    expect(INDEX).not.toContain('video/video-repair.js');
    expect(INDEX).not.toContain('video/video-server-scan.js');
    expect(SIDE_CSS).not.toContain('[data-video-subpage="video-tools"]');
  });

  it('the progress frame reaches React over the ss: seam', () => {
    expect(CORE).toContain(
      "socket.on('video:repair:progress', (data) => { window.dispatchEvent(new CustomEvent('ss:video-repair-progress', { detail: data })); });",
    );
    expect(CORE).not.toContain('updateVideoRepairProgressFromData');
  });
});

describe('a reload onto the video tools page stays on the video side', () => {
  it('reads /tools?media=video as the video tools page', () => {
    const parse = parseSharedPath();
    expect(parse('/tools', '?media=video')).toBe('video-tools');
    expect(parse('/tools/', '?media=video&job=broken_files')).toBe('video-tools');
  });

  it('leaves the music tools page alone', () => {
    const parse = parseSharedPath();
    expect(parse('/tools', '')).toBeNull();
    expect(parse('/tools', '?job=orphan_file_detector')).toBeNull();
    expect(parse('/tools', '?media=music')).toBeNull();
    // and the wishlist still works the same
    expect(parse('/wishlist', '?media=tv')).toBe('video-wishlist');
  });
});

describe('permissions follow the side the page is showing', () => {
  function access(profile: { allowedPages: string[] | null; sides: 'music' | 'video' | 'both' }) {
    const views = between(INIT, 'const SHARED_VIDEO_VIEWS = {', '};') + '};';
    const body = [
      views.replace('const ', 'var '),
      extractFunction('isPageAccessAllowed', INIT),
    ].join('\n');
    const isPageAllowed = (id: string) =>
      !profile.allowedPages || profile.allowedPages.includes(id);
    const profileAllowedSides = () => profile.sides;
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    return new Function(
      'isPageAllowed',
      'profileAllowedSides',
      `${body}\nreturn isPageAccessAllowed;`,
    )(isPageAllowed, profileAllowedSides) as (page: string, search: string) => boolean;
  }

  it('a video-only profile reaches video tools without the music tools page', () => {
    const can = access({ allowedPages: ['video-tools'], sides: 'video' });
    expect(can('tools', '?media=video')).toBe(true);
    expect(can('tools', '')).toBe(false);
  });

  it('a music-only profile is kept off video tools even with every page allowed', () => {
    const can = access({ allowedPages: null, sides: 'music' });
    expect(can('tools', '?media=video')).toBe(false);
    expect(can('tools', '')).toBe(true);
  });

  it('music tools asks what it always asked', () => {
    const can = access({ allowedPages: ['tools'], sides: 'both' });
    expect(can('tools', '')).toBe(true);
    expect(can('tools', '?media=video')).toBe(false); // video-tools is not in the list
  });
});
