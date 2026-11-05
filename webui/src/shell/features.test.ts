/**
 * #1909 (neonmaus): each profile can hide the parts of soulsync it doesn't
 * use. These pin the sidebar filter, the pre-paint replay in index.html and
 * the load/save round trip.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import {
  applyFeatures,
  FEATURES_CACHE_KEY,
  FEATURES_CHANGED_EVENT,
  getFeatures,
  installFeatures,
  isFeatureOn,
  loadFeatures,
  saveFeatures,
} from './features';

const SIDEBAR = `
  <div class="side-toggle"></div>
  <nav class="sidebar-nav">
    <a class="nav-button" data-page="dashboard"></a>
    <button class="nav-section-label" data-section="find"></button>
    <a class="nav-button" data-page="search"></a>
    <a class="nav-button" data-page="podcasts"></a>
    <a class="nav-button" data-page="audiobooks"></a>
    <button class="nav-section-label" data-section="system"></button>
    <a class="nav-button" data-page="chat"></a>
    <a class="nav-button" data-page="tools"></a>
  </nav>
  <nav class="sidebar-nav video-nav">
    <a class="nav-button" data-video-page="video-dashboard"></a>
    <button class="nav-section-label" data-section="video-system"></button>
    <a class="nav-button" data-video-page="video-chat"></a>
  </nav>`;

const hidden = (sel: string) =>
  document.querySelector(sel)!.classList.contains('nav-feature-hidden');

function payload(off: string[], pages: string[]) {
  return {
    success: true,
    features: [],
    groups: [],
    presets: [],
    off,
    preset: off.length ? 'custom' : 'full',
    hidden_pages: pages,
  };
}

beforeEach(() => {
  document.body.innerHTML = SIDEBAR;
  localStorage.clear();
});

afterEach(() => {
  server.resetHandlers();
  document.body.innerHTML = '';
  document.documentElement.removeAttribute('data-features-off');
});

describe('the sidebar filter', () => {
  it('hides the pages of what is off and nothing else', () => {
    applyFeatures(['podcasts'], ['podcasts']);
    expect(hidden('[data-page="podcasts"]')).toBe(true);
    expect(hidden('[data-page="search"]')).toBe(false);
    expect(hidden('[data-page="audiobooks"]')).toBe(false);
    expect(document.documentElement.getAttribute('data-features-off')).toBe('podcasts');
  });

  it('drops a section title once nothing under it shows', () => {
    applyFeatures(['chat', 'tools'], ['chat', 'tools', 'video-chat']);
    expect(hidden('[data-section="system"]')).toBe(true);
    expect(hidden('[data-section="find"]')).toBe(false);
    expect(hidden('[data-section="video-system"]')).toBe(true);
  });

  it('counts a page the profile may not open as gone too', () => {
    // init.js hides pages a profile isn't given with style.display
    document.querySelector<HTMLElement>('[data-page="tools"]')!.style.display = 'none';
    applyFeatures(['chat'], ['chat']);
    expect(hidden('[data-section="system"]')).toBe(true);
  });

  it('puts everything back when a feature comes back on', () => {
    applyFeatures(['chat', 'tools'], ['chat', 'tools']);
    applyFeatures([], []);
    expect(hidden('[data-page="chat"]')).toBe(false);
    expect(hidden('[data-section="system"]')).toBe(false);
  });

  it('hides the audio/video switch with the video side', () => {
    applyFeatures(['video'], ['video-dashboard', 'video-chat']);
    expect(hidden('.side-toggle')).toBe(true);
    applyFeatures([], []);
    expect(hidden('.side-toggle')).toBe(false);
  });

  it('moves someone parked on the video side back to music', () => {
    const calls: unknown[][] = [];
    (window as { _switchAppSide?: unknown })._switchAppSide = (...a: unknown[]) => calls.push(a);
    document.body.setAttribute('data-side', 'video');
    applyFeatures(['video'], ['video-dashboard']);
    expect(calls).toEqual([['music', { force: true }]]);
    delete (window as { _switchAppSide?: unknown })._switchAppSide;
    document.body.removeAttribute('data-side');
  });
});

describe('the pre-paint replay in index.html', () => {
  // the inline script under the sidebar hides the cached answer before the
  // shell bundle loads. it has to land on exactly what applyFeatures does,
  // or the sidebar jumps when the bundle takes over.
  const html = readFileSync(resolve(__dirname, '../../index.html'), 'utf8');
  const start = html.indexOf("JSON.parse(localStorage.getItem('ss_features')");
  const open = html.lastIndexOf('<script>', start);
  const close = html.indexOf('</script>', start);
  const source = html.slice(open + '<script>'.length, close);

  it('is there and reads the same cache key', () => {
    expect(start).toBeGreaterThan(0);
    expect(FEATURES_CACHE_KEY).toBe('ss_features');
  });

  it('matches applyFeatures', () => {
    const off = ['chat', 'tools', 'video', 'podcasts'];
    const pages = ['chat', 'tools', 'podcasts', 'video-dashboard', 'video-chat'];
    localStorage.setItem('ss_features', JSON.stringify({ off, pages }));
    new Function(source)();
    const replay = [...document.querySelectorAll('*')].map((el) =>
      el.classList.contains('nav-feature-hidden'),
    );
    const attr = document.documentElement.getAttribute('data-features-off');

    document.body.innerHTML = SIDEBAR;
    applyFeatures(off, pages);
    const live = [...document.querySelectorAll('*')].map((el) =>
      el.classList.contains('nav-feature-hidden'),
    );
    expect(replay).toEqual(live);
    expect(replay.some(Boolean)).toBe(true);
    expect(attr).toBe(document.documentElement.getAttribute('data-features-off'));
  });

  it('does nothing without a cache, or with a broken one', () => {
    new Function(source)();
    localStorage.setItem('ss_features', '{nope');
    new Function(source)();
    expect(document.querySelectorAll('.nav-feature-hidden')).toHaveLength(0);
  });
});

describe('load and save', () => {
  it('loads the server answer, caches it and tells React', async () => {
    server.use(
      http.get('*/api/profiles/me/features', () =>
        HttpResponse.json(payload(['chat'], ['chat', 'video-chat'])),
      ),
    );
    let told = 0;
    const onChange = () => told++;
    window.addEventListener(FEATURES_CHANGED_EVENT, onChange);
    await loadFeatures();
    window.removeEventListener(FEATURES_CHANGED_EVENT, onChange);

    expect(told).toBe(1);
    expect(isFeatureOn('chat')).toBe(false);
    expect(isFeatureOn('podcasts')).toBe(true);
    expect(hidden('[data-page="chat"]')).toBe(true);
    expect(JSON.parse(localStorage.getItem(FEATURES_CACHE_KEY)!)).toEqual({
      off: ['chat'],
      pages: ['chat', 'video-chat'],
    });
  });

  it('keeps what it had when the server does not answer', async () => {
    server.use(
      http.get('*/api/profiles/me/features', () => HttpResponse.json(payload(['tools'], ['tools']))),
    );
    await loadFeatures();
    server.use(http.get('*/api/profiles/me/features', () => HttpResponse.error()));
    await loadFeatures();
    expect(getFeatures().off).toEqual(['tools']);
    expect(hidden('[data-page="tools"]')).toBe(true);
  });

  it('a failed save throws and changes nothing', async () => {
    server.use(
      http.get('*/api/profiles/me/features', () => HttpResponse.json(payload([], []))),
      http.post('*/api/profiles/me/features', () =>
        HttpResponse.json({ success: false, error: 'disk full' }, { status: 500 }),
      ),
    );
    await loadFeatures();
    await expect(saveFeatures({ off: ['chat'] })).rejects.toThrow('disk full');
    expect(isFeatureOn('chat')).toBe(true);
    expect(hidden('[data-page="chat"]')).toBe(false);
  });

  it('a save posts the choice and applies the answer', async () => {
    let body: unknown = null;
    server.use(
      http.post('*/api/profiles/me/features', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(payload(['podcasts', 'audiobooks'], ['podcasts', 'audiobooks']));
      }),
    );
    await saveFeatures({ preset: 'simple' });
    expect(body).toEqual({ preset: 'simple' });
    expect(hidden('[data-page="audiobooks"]')).toBe(true);
  });

  it('install primes from the cache and exposes the api', () => {
    localStorage.setItem(FEATURES_CACHE_KEY, JSON.stringify({ off: ['stats'], pages: ['stats'] }));
    installFeatures();
    expect(window.SoulSyncFeatures?.isOn('stats')).toBe(false);
    expect(document.documentElement.getAttribute('data-features-off')).toBe('stats');
  });
});
