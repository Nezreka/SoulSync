import { cleanup, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SettingsPage } from '@/features/settings/settings-page';
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
 * Runs the real page and its save button, and the real settings store.
 */

/** the page's save button (settings-page.tsx, SETTINGS_PORT.md step V4c),
 *  clicked on one side; what it posted */
async function click(side: 'music' | 'video') {
  document.body.setAttribute('data-side', side);
  const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => new Response('{}'));
  vi.stubGlobal('fetch', fetcher);
  const w = window as unknown as Record<string, unknown>;
  w.SoulSyncSettings = { flush: async () => true };
  render(<SettingsPage tabs={{}} />);
  const button = document.getElementById('save-settings');
  expect(button, 'the Save Settings button is gone').not.toBeNull();
  const event = new MouseEvent('click', { bubbles: true, cancelable: true });
  button!.dispatchEvent(event);
  await new Promise((r) => setTimeout(r, 20));
  const posts = fetcher.mock.calls
    .filter(([, init]) => init?.method === 'POST')
    .map(([url]) => url);
  cleanup();
  vi.unstubAllGlobals();
  delete w.SoulSyncSettings;
  return { posts, event };
}

describe('the Save Settings button on the video side', () => {
  it('lets the music save run too; the video settings save themselves', async () => {
    const { posts, event } = await click('video');
    expect(event.defaultPrevented).toBe(false);
    // the page's own save, which every shared and System field goes through
    expect(posts.filter((u) => u === '/api/settings')).toHaveLength(1);
    // and the video sections' own (each adds its save to the same capture-phase
    // click, video-shown.ts; their tests press it too)
    expect(posts.some((u) => u.startsWith('/api/video/'))).toBe(true);
  });

  it('leaves the music side alone', async () => {
    const { posts } = await click('music');
    expect(posts).toEqual(['/api/settings']);
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
