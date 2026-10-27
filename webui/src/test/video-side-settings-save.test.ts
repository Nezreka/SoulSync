import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The Settings page is shared, so on the video side it shows every music field
 * on Sources, Downloads, Library and Quality, and all of System including
 * Security & Access. Those fields have no saver but the music one.
 *
 * The video side used to stop the Save Settings click in its capture listener
 * (so the music save never ran) and toast "Settings saved" anyway, and the music
 * auto-save stood down for every field on that side. Reported live: tick OIDC
 * only on the video side, Save, reload - unticked.
 *
 * Runs the real video-settings.js and the real auto-save against the real page.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const HTML = read('index.html');
const VIDEO = read('static/video/video-settings.js');
const SETTINGS = read('static/settings.js');

function page(side: 'music' | 'video') {
  const doc = new DOMParser().parseFromString(HTML, 'text/html');
  doc.body.setAttribute('data-side', side);
  return doc;
}

describe('the Save Settings button on the video side', () => {
  function click(side: 'music' | 'video') {
    const doc = page(side);
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => new Response('{}'));
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    new Function('document', 'window', 'fetch', VIDEO)(doc, {}, fetcher);
    doc.dispatchEvent(new Event('DOMContentLoaded'));
    // stands in for settings.js handleManualSaveClick, which is bound to the
    // button itself (bubble phase) by initializeSettings
    const musicSave = vi.fn();
    const button = doc.getElementById('save-settings');
    expect(button, 'the Save Settings button is gone').not.toBeNull();
    button!.addEventListener('click', musicSave);
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    button!.dispatchEvent(event);
    const posts = fetcher.mock.calls
      .filter(([, init]) => init?.method === 'POST')
      .map(([url]) => url);
    return { musicSave, posts, event };
  }

  it('saves the video settings and lets the music save run too', () => {
    const { musicSave, posts, event } = click('video');
    expect(musicSave).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(false);
    for (const url of [
      '/api/video/server-config',
      '/api/video/libraries',
      '/api/video/enrichment/config',
      '/api/video/downloads/config',
    ]) {
      expect(posts, `${url} was not saved`).toContain(url);
    }
  });

  it('leaves the music side alone', () => {
    const { musicSave, posts } = click('music');
    expect(musicSave).toHaveBeenCalledTimes(1);
    expect(posts).toEqual([]);
  });
});

describe('the music auto-save on the video side', () => {
  // the real function and its helper, with the page's globals stubbed around them
  function autosave(side: 'music' | 'video') {
    const doc = page(side);
    const timers: unknown[] = [];
    const deps = `let settingsAutoSaveTimer = null;
const _qpHandleProfileControlChange = () => false;
${extractFunction('_isVideoOwnedField', SETTINGS)}
${extractFunction('debouncedAutoSaveSettings', SETTINGS)}
return debouncedAutoSaveSettings;`;
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const fn = new Function(
      'document',
      'window',
      'setTimeout',
      'clearTimeout',
      'saveSettings',
      deps,
    )(
      doc,
      {},
      (cb: unknown) => {
        timers.push(cb);
        return timers.length;
      },
      () => undefined,
      () => undefined,
    ) as (event?: { target: Element | null }) => void;
    const scheduled = (id: string | null) => {
      timers.length = 0;
      fn(id === null ? undefined : { target: doc.getElementById(id) });
      return timers.length > 0;
    };
    return { doc, scheduled };
  }

  it('saves a security field changed on the video side', () => {
    const { scheduled } = autosave('video');
    expect(scheduled('security-oidc-only')).toBe(true);
  });

  it('saves a field on a shared tab changed on the video side', () => {
    const { scheduled } = autosave('video');
    expect(scheduled('soulseek-url')).toBe(true);
  });

  it('saves a music write made from code (no event) on the video side', () => {
    const { scheduled } = autosave('video');
    expect(scheduled(null)).toBe(true);
  });

  it('skips a field inside a video-only block - it saves itself', () => {
    const { scheduled } = autosave('video');
    expect(scheduled('tmdb-api-key')).toBe(false);
  });

  it('skips a field wired by a data-video-* hook - it saves itself', () => {
    const { doc, scheduled } = autosave('video');
    // on a shared tab, so no data-video-only block above it: only the hook says
    // it is video's own
    const host = doc.querySelector('#settings-page [data-stg="library"]');
    expect(host).not.toBeNull();
    const probe = doc.createElement('input');
    probe.id = 'probe-video-field';
    probe.setAttribute('data-video-probe', '');
    host!.appendChild(probe);
    expect(probe.closest('[data-video-only]')).toBeNull();
    expect(scheduled('probe-video-field')).toBe(false);
  });

  it('is unchanged on the music side', () => {
    const { scheduled } = autosave('music');
    expect(scheduled('security-oidc-only')).toBe(true);
    expect(scheduled('tmdb-api-key')).toBe(true);
  });
});
