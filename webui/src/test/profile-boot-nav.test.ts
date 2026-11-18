import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * #2154 and #2156 (SeadogsBooty): what a member sees while the app starts.
 *
 * #2156: the profile arrived over fetch after the sidebar painted, so a
 * member saw the admin's whole nav and then watched it shrink. The server
 * now renders the profile in and profile-nav.js cuts the nav down under the
 * sidebar, before first paint. With nobody known yet the nav stays hidden.
 *
 * #2154: a video member without the video dashboard got a blank page after
 * signing in. The video side fell back to the dashboard for everyone, handed
 * it to the React route, the route refused it and bounced to a music page
 * the video side doesn't show. The fallback is now their landing page.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const NAV = read('static/profile-nav.js');
const SIDE = read('static/video/video-side.js');
const HTML = read('index.html');
const CSS = read('static/style.css');

type Profile = {
  id: number;
  is_admin: boolean;
  can_download?: boolean;
  allowed_pages?: string[] | null;
  allowed_sides?: string;
  home_page?: string | null;
};

// the reporter's shape: a video member with search, discover and library
const VIDEO_MEMBER: Profile = {
  id: 3,
  is_admin: false,
  can_download: true,
  allowed_sides: 'video',
  allowed_pages: ['video-search', 'video-discover', 'video-library'],
  home_page: null,
};
const MUSIC_MEMBER: Profile = {
  id: 4,
  is_admin: false,
  can_download: false,
  allowed_sides: 'music',
  allowed_pages: ['discover', 'search', 'library'],
  home_page: null,
};
const ADMIN: Profile = { id: 1, is_admin: true, can_download: true, allowed_pages: null };

const SIDEBAR = `
  <div class="side-toggle"></div>
  <div class="sidebar-header"><span class="app-subtitle"></span></div>
  <nav class="sidebar-nav">
    <a class="nav-button" data-page="dashboard"></a>
    <button class="nav-section-label" data-section="find"></button>
    <a class="nav-button" data-page="search"></a>
    <a class="nav-button" data-page="discover"></a>
    <button class="nav-section-label" data-section="music"></button>
    <a class="nav-button" data-page="automations"></a>
    <a class="nav-button" data-page="import"></a>
    <button class="nav-section-label" data-section="system"></button>
    <a class="nav-button" data-page="settings"></a>
    <a class="nav-button" data-page="help"></a>
  </nav>
  <nav class="sidebar-nav video-nav">
    <a class="nav-button" data-video-page="video-dashboard"></a>
    <button class="nav-section-label" data-section="video-find"></button>
    <a class="nav-button" data-video-page="video-search"></a>
    <a class="nav-button" data-video-page="video-discover"></a>
    <button class="nav-section-label" data-section="video-content"></button>
    <a class="nav-button" data-video-page="video-library"></a>
    <a class="nav-button" data-video-page="video-import"></a>
    <button class="nav-section-label" data-section="video-system"></button>
    <a class="nav-button" data-video-page="video-settings"></a>
    <a class="nav-button" data-video-page="video-help"></a>
  </nav>
  <div id="video-page-host">
    <div class="video-subpage" data-video-subpage="video-discover"></div>
    <div class="video-subpage" data-video-subpage="video-search"></div>
  </div>
  <div id="video-placeholder-slot"></div>`;

type G = typeof globalThis & {
  currentProfile?: Profile | null;
  SOULSYNC_BOOT_PROFILE?: Profile | null;
  _soulsyncAppStarted?: boolean;
};
const g = globalThis as G;

const shown = (sel: string) => {
  const el = document.querySelector<HTMLElement>(sel)!;
  return el.style.display !== 'none' && !el.classList.contains('nav-feature-hidden');
};

// what the head script does with the profile the server rendered in
function runHead(boot: Profile | null) {
  const start = HTML.indexOf('window.SOULSYNC_BOOT_PROFILE =');
  const head = HTML.slice(start, HTML.indexOf('</script>', start));
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function(head.replace('{{ boot_profile | tojson }}', JSON.stringify(boot)))();
}

const PENDING_RULE =
  /html\.nav-pending \.sidebar-nav \.nav-button,\nhtml\.nav-pending \.sidebar-nav \.nav-section-label,\nhtml\.nav-pending \.side-toggle \{ visibility: hidden; \}/;

function loadNav(boot: Profile | null) {
  g.SOULSYNC_BOOT_PROFILE = boot;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function(NAV)();
}

beforeEach(() => {
  document.body.innerHTML = SIDEBAR;
  document.body.removeAttribute('data-side');
  document.body.removeAttribute('data-video-page');
  document.documentElement.className = '';
  document.documentElement.removeAttribute('data-features-off');
  localStorage.clear();
  // the video side reads the boot url, and the last test left its own
  window.history.replaceState(null, '', '/');
  g.currentProfile = null;
  g._soulsyncAppStarted = false;
});

afterEach(() => {
  delete g.SOULSYNC_BOOT_PROFILE;
  delete g.currentProfile;
  delete g._soulsyncAppStarted;
  delete window.SoulSyncProfileNav;
  delete (window as { SoulSyncWebRouter?: unknown }).SoulSyncWebRouter;
  vi.useRealTimers();
});

describe('#2156 the nav before first paint', () => {
  it('a member never sees the admin sections, from the first frame', () => {
    loadNav(VIDEO_MEMBER);
    for (const page of ['dashboard', 'automations', 'import', 'settings', 'search', 'discover']) {
      expect(shown(`[data-page="${page}"]`), page).toBe(false);
    }
    for (const page of ['video-dashboard', 'video-import', 'video-settings']) {
      expect(shown(`[data-video-page="${page}"]`), page).toBe(false);
    }
    expect(shown('[data-video-page="video-discover"]')).toBe(true);
    expect(shown('[data-video-page="video-help"]')).toBe(true);
  });

  it('a section with nothing left in it loses its title too', () => {
    loadNav(VIDEO_MEMBER);
    // the video system section keeps help, the music ones are all gone
    expect(shown('[data-section="video-system"]')).toBe(true);
    expect(shown('[data-section="video-content"]')).toBe(true);
    expect(shown('[data-section="music"]')).toBe(false);
  });

  it('a one-side member is on their side from the first frame', () => {
    loadNav(VIDEO_MEMBER);
    expect(document.body.getAttribute('data-side')).toBe('video');
    expect(document.documentElement.classList.contains('side-locked')).toBe(true);
  });

  it('the admin keeps every item and both sides', () => {
    loadNav(ADMIN);
    expect(document.querySelectorAll('.sidebar-nav .nav-button[style*="none"]')).toHaveLength(0);
    expect(document.body.hasAttribute('data-side')).toBe(false);
    expect(document.documentElement.classList.contains('side-locked')).toBe(false);
  });

  it('with nobody known the nav stays hidden until init.js applies a profile', () => {
    const head = HTML.slice(
      HTML.indexOf('window.SOULSYNC_BOOT_PROFILE ='),
      HTML.indexOf('</script>', HTML.indexOf('window.SOULSYNC_BOOT_PROFILE =')),
    );
    // the jinja value as the server renders it with nobody signed in
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    new Function(head.replace('{{ boot_profile | tojson }}', 'null'))();
    expect(document.documentElement.classList.contains('nav-pending')).toBe(true);
    loadNav(null);
    // nothing applied, nothing revealed
    expect(document.documentElement.classList.contains('nav-pending')).toBe(true);
    expect(CSS).toMatch(PENDING_RULE);
    window.SoulSyncProfileNav!.apply(MUSIC_MEMBER);
    expect(document.documentElement.classList.contains('nav-pending')).toBe(false);
    expect(shown('[data-page="discover"]')).toBe(true);
    expect(shown('[data-page="settings"]')).toBe(false);
  });

  it('a signed-in admin skips the wait', () => {
    runHead(ADMIN);
    expect(document.documentElement.classList.contains('nav-pending')).toBe(false);
  });

  // the follow-up: signed out, refresh, sign back in. the page reloads with
  // the member rendered in, but profile-nav.js is its own file under the nav,
  // and chrome paints the half-loaded page while it fetches: the whole admin
  // nav showed for a frame. a member's nav now starts hidden in <head> and
  // only the real profile shows it again.
  it('a signed-in member starts hidden too, until their profile has cut the nav down', () => {
    runHead(MUSIC_MEMBER);
    // the frame chrome can paint before profile-nav.js has run
    expect(document.documentElement.classList.contains('nav-pending')).toBe(true);
    expect(CSS).toMatch(PENDING_RULE);
    loadNav(MUSIC_MEMBER);
    expect(document.documentElement.classList.contains('nav-pending')).toBe(false);
    expect(shown('[data-page="discover"]')).toBe(true);
    expect(shown('[data-page="dashboard"]')).toBe(false);
    expect(shown('[data-page="settings"]')).toBe(false);
  });

  it('a member that is admin by flag, not id 1, skips the wait like any admin', () => {
    runHead({ id: 7, is_admin: true, allowed_pages: null });
    expect(document.documentElement.classList.contains('nav-pending')).toBe(false);
  });

  it('the side switch waits with the nav, a one-side member never gets it', () => {
    runHead(null);
    expect(CSS).toMatch(/html\.nav-pending \.side-toggle \{ visibility: hidden; \}/);
    expect(document.documentElement.classList.contains('nav-pending')).toBe(true);
  });

  it('init.js runs the same rule on every profile apply, and starts the nav on failure', () => {
    const init = read('static/init.js');
    expect(init).toContain('window.SoulSyncProfileNav.apply(currentProfile);');
    const initApp = init.slice(init.indexOf('function initApp() {'));
    expect(initApp.slice(0, 1200)).toContain("classList.remove('nav-pending')");
  });
});

describe('#2154 the landing page', () => {
  const land = (profile: Profile, hidden: string[] = []) => {
    loadNav(null);
    const nav = window.SoulSyncProfileNav!;
    return nav.landingPage({
      home: profile.home_page || '',
      isAdmin: profile.is_admin,
      adminPage: 'dashboard',
      order: nav.MUSIC_LANDING,
      allowed: (id) => nav.navButtonVisible(profile, id, false),
      hidden,
      fallback: 'help',
    });
  };

  it('a member with no home page lands on discover', () => {
    expect(land(MUSIC_MEMBER)).toBe('discover');
  });

  it('their own home page wins', () => {
    expect(land({ ...MUSIC_MEMBER, home_page: 'library' })).toBe('library');
  });

  it('a home page they lost goes to discover', () => {
    expect(land({ ...MUSIC_MEMBER, home_page: 'stats' })).toBe('discover');
  });

  it('discover hidden or not given: the first page they have', () => {
    expect(land(MUSIC_MEMBER, ['discover'])).toBe('search');
    expect(land({ ...MUSIC_MEMBER, allowed_pages: ['library'] })).toBe('library');
  });

  it('everything hidden still lands somewhere they can open', () => {
    expect(land(MUSIC_MEMBER, ['discover', 'search', 'library'])).toBe('discover');
  });

  it('nothing at all: help, which everyone has', () => {
    expect(land({ ...MUSIC_MEMBER, allowed_pages: [] })).toBe('help');
  });

  it('the admin still lands on the dashboard', () => {
    expect(land(ADMIN)).toBe('dashboard');
    expect(land({ ...ADMIN, home_page: 'stats' })).toBe('stats');
  });
});

describe('#2154 the video side lands on a page the member has', () => {
  function bootVideoSide(router: { navigateToPage: ReturnType<typeof vi.fn> }) {
    (window as { SoulSyncWebRouter?: unknown }).SoulSyncWebRouter = router;
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    new Function(SIDE)();
  }

  it('signed in: video discover, not the dashboard they do not have', () => {
    vi.useFakeTimers();
    loadNav(VIDEO_MEMBER);
    const router = { navigateToPage: vi.fn() };
    bootVideoSide(router);
    vi.runAllTimers();
    expect(document.body.getAttribute('data-side')).toBe('video');
    expect(document.body.getAttribute('data-video-page')).toBe('video-discover');
    expect(router.navigateToPage).not.toHaveBeenCalled();
    expect(
      document
        .querySelector('.video-nav [data-video-page="video-discover"]')!
        .classList.contains('active'),
    ).toBe(true);
    expect(
      document.querySelector<HTMLElement>('[data-video-subpage="video-discover"]')!.hidden,
    ).toBe(false);
  });

  it('a refused video page bounces to their landing page too', () => {
    vi.useFakeTimers();
    loadNav(VIDEO_MEMBER);
    const router = { navigateToPage: vi.fn() };
    bootVideoSide(router);
    vi.runAllTimers();
    document.dispatchEvent(
      new CustomEvent('soulsync:video-navigate', { detail: { page: 'video-dashboard' } }),
    );
    expect(document.body.getAttribute('data-video-page')).toBe('video-discover');
    expect(router.navigateToPage).not.toHaveBeenCalled();
  });

  it('their video home wins', () => {
    vi.useFakeTimers();
    loadNav({ ...VIDEO_MEMBER, home_page: 'video-search' });
    bootVideoSide({ navigateToPage: vi.fn() });
    vi.runAllTimers();
    expect(document.body.getAttribute('data-video-page')).toBe('video-search');
  });

  it('nobody known yet: it waits for the app to start instead of guessing', () => {
    vi.useFakeTimers();
    localStorage.setItem('soulsync_side', 'video');
    loadNav(null);
    const router = { navigateToPage: vi.fn() };
    bootVideoSide(router);
    vi.runAllTimers();
    // no page picked while the profile is unknown
    expect(document.body.hasAttribute('data-video-page')).toBe(false);
    expect(router.navigateToPage).not.toHaveBeenCalled();
    g.currentProfile = VIDEO_MEMBER;
    g._soulsyncAppStarted = true;
    window.dispatchEvent(new CustomEvent('ss:app-started'));
    vi.runAllTimers();
    expect(document.body.getAttribute('data-video-page')).toBe('video-discover');
    expect(router.navigateToPage).not.toHaveBeenCalled();
  });

  it('the admin still opens the video dashboard', () => {
    vi.useFakeTimers();
    localStorage.setItem('soulsync_side', 'video');
    loadNav(ADMIN);
    const router = { navigateToPage: vi.fn() };
    bootVideoSide(router);
    vi.runAllTimers();
    expect(router.navigateToPage).toHaveBeenCalledWith('dashboard', {
      query: { media: 'video' },
      replace: true,
    });
  });
});
