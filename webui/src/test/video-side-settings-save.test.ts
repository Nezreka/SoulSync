import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore } from '@/features/settings/settings-store';

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

  it('lets the music save run too; the video settings save themselves', () => {
    const { musicSave, posts, event } = click('video');
    expect(musicSave).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(false);
    // every video section is React now and adds its own save to the same
    // capture-phase click (video-shown.ts); their tests press it:
    // connections-video-server.test.tsx, connections-video-api.test.tsx,
    // video-quality.test.tsx, library-video-folders.test.tsx,
    // library-video-prefs.test.tsx. this file has nothing of its own to post
    expect(posts).toEqual([]);
  });

  it('leaves the music side alone', () => {
    const { musicSave, posts } = click('music');
    expect(musicSave).toHaveBeenCalledTimes(1);
    expect(posts).toEqual([]);
  });
});

describe('the music auto-save on the video side', () => {
  // every control is React and saves through the settings store, on both
  // sides; the page's own autosave, which stood down for video-owned fields,
  // went with settings.js (SETTINGS_PORT.md step V4b)
  function store(side: 'music' | 'video') {
    document.body.setAttribute('data-side', side);
    const post = vi.fn(() => Promise.resolve({ success: true }));
    const s = createSettingsStore({ post, delay: 0 });
    const saved = async (section: string, key: string, value: unknown) => {
      post.mockClear();
      s.set(section, key, value);
      await new Promise((r) => setTimeout(r, 10));
      return post.mock.calls.length > 0;
    };
    return { saved };
  }

  afterEach(() => document.body.removeAttribute('data-side'));

  it('saves a security field changed on the video side', async () => {
    expect(await store('video').saved('security', 'oidc_only', true)).toBe(true);
  });

  it('saves a field on a shared tab changed on the video side', async () => {
    expect(await store('video').saved('soulseek', 'slskd_url', 'http://slskd')).toBe(true);
  });

  it('is unchanged on the music side', async () => {
    expect(await store('music').saved('security', 'oidc_only', true)).toBe(true);
  });

  it('a video section never saves through it: each posts to /api/video/* itself', () => {
    const tabs = resolve(process.cwd(), 'src/features/settings/tabs');
    for (const f of [
      'video-downloads.ts',
      'video-organization.ts',
      'video-prefs.ts',
      'video-keys.ts',
      'video-server.ts',
      'video-row-lists.tsx',
      'video-quality.tsx',
    ]) {
      const src = readFileSync(resolve(tabs, f), 'utf8');
      expect(src, f).not.toContain('settingsStore');
      expect(src, f).toContain('/api/video/');
    }
  });
});
