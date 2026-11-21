/**
 * The Connections page's server sheets (settings-bic phase 3): the music
 * server's panel in the sheet on the parts, one Test in its foot that tests
 * the server being looked at, and the video side's server card with its own
 * sheet. Server Activity is no longer here (System > Server Activity).
 */

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { nav } from '../../nav';
import { createSettingsStore, type SettingsStore } from '../../settings-store';
import { testResults } from '../connections-servers';
import { videoServer } from '../video-server';
import { videoShown } from '../video-shown';
import { ConnectionsPage } from './connections-page';

let store: SettingsStore;
let calls: Array<{ url: string; body?: unknown }>;

beforeEach(() => {
  store = createSettingsStore({ post: () => Promise.resolve({ success: true }), delay: 0 });
  calls = [];
  testResults.reset();
  videoServer.reset();
  nav.reset();
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({
        url,
        body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined,
      });
      const data =
        url === '/api/test-connection'
          ? { success: false, error: 'refused' }
          : url === '/api/video/server'
            ? { server: 'plex' }
            : { success: true };
      return Promise.resolve(
        new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } }),
      );
    }),
  );
  (window as Window & { showToast?: unknown }).showToast = vi.fn();
});

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
  document.body.removeAttribute('data-side');
  vi.unstubAllGlobals();
});

function page() {
  const root = document.createElement('div');
  root.id = 'settings-page';
  document.body.append(root);
  render(<ConnectionsPage store={store} part="page" opened={1} active />, { container: root });
  return root;
}
const button = (within: ParentNode, words: string) =>
  [...within.querySelectorAll('button')].find((b) => b.textContent === words)!;

describe('the music server sheet', () => {
  it('holds the server being looked at, on the parts, with no colon in a label', () => {
    const root = page();
    act(() => store.hydrate({ active_media_server: 'jellyfin', jellyfin: {} }));
    fireEvent.click(button(root, 'View configuration'));
    const sheet = document.getElementById('server-config-sheet')!;
    expect(sheet.hidden).toBe(false);
    expect(document.getElementById('server-sheet-title')!.textContent).toBe('Jellyfin');
    const body = document.getElementById('server-sheet-body')!;
    expect(body.querySelector('#jellyfin-container')!.closest('[hidden]')).toBeNull();
    expect(body.querySelector('.form-group, .test-button, .detect-button')).toBeNull();
    for (const l of body.querySelectorAll('label'))
      expect(l.textContent, l.textContent ?? '').not.toMatch(/:\s*$/);
    // the Jellyfin timeout is rare: behind More, still there
    expect(document.getElementById('jellyfin-timeout')!.closest('[data-more-body]')).not.toBeNull();
  });

  it('has one Test, in its foot, for the server it shows; its answer is beside it', async () => {
    const root = page();
    act(() => store.hydrate({ active_media_server: 'navidrome', navidrome: {} }));
    fireEvent.click(button(root, 'View configuration'));
    const sheet = document.getElementById('server-config-sheet')!;
    const tests = [...sheet.querySelectorAll('button')].filter((b) =>
      /^(Test|Verify)/.test(b.textContent ?? ''),
    );
    expect(tests.map((b) => b.id)).toEqual(['server-sheet-test']);
    fireEvent.click(tests[0]);
    await waitFor(() =>
      expect(calls.find((c) => c.url === '/api/test-connection')?.body).toEqual({
        service: 'navidrome',
      }),
    );
    await waitFor(() =>
      expect(document.getElementById('server-sheet-result')!.textContent).toBe('refused'),
    );
  });

  it('Standalone verifies the music library folder', () => {
    const root = page();
    act(() => store.hydrate({ active_media_server: 'soulsync' }));
    fireEvent.click(button(root, 'View configuration'));
    expect(document.getElementById('server-sheet-test')!.textContent).toBe(
      'Verify music library folder',
    );
  });

  it('Server Activity is not on Connections any more', () => {
    const root = page();
    expect(root.querySelector('[data-server-activity-settings]')).toBeNull();
    expect(root.textContent).not.toContain('Server Activity');
  });
});

describe('the video server', () => {
  it('is one card with the same pattern, and View configuration opens its own sheet', async () => {
    document.body.setAttribute('data-side', 'video');
    const root = page();
    act(() => videoServer.load());
    await waitFor(() =>
      expect(
        document.querySelector('[data-video-server-toggle="plex"]')!.getAttribute('aria-checked'),
      ).toBe('true'),
    );
    const card = root.querySelector('#stg-video-server-card')!;
    // the label above it and the card's title: the old group's own heading went
    expect([...root.querySelectorAll('h3')].map((h) => h.textContent)).not.toContain(
      'Server Connection',
    );
    fireEvent.click(button(card, 'View configuration'));
    const sheet = document.getElementById('video-server-sheet')!;
    expect(sheet.hidden).toBe(false);
    expect(document.getElementById('video-server-sheet-title')!.textContent).toBe('Plex');
    expect(sheet.querySelector('[data-video-conn="plex-url"]')).not.toBeNull();
    for (const l of sheet.querySelectorAll('label')) expect(l.textContent).not.toMatch(/:\s*$/);
  });
});

describe('Notifications', () => {
  it('its pill says what it is for; opening it on the music side loads them', () => {
    page();
    act(() => nav.go('connections', 'notifications'));
    const panel = document.getElementById('stg-view-connections-notifications')!;
    expect(panel.hidden).toBe(false);
    // loaded at least once (the music side never shows the video settings page)
    expect(videoShown.get()).toBeGreaterThan(0);
    expect(panel.querySelector('[data-card="Notifications"]')).not.toBeNull();
    expect(panel.querySelector('[data-about]')!.textContent).toBe(
      'Where SoulSync sends a message when something happens.',
    );
    // no folded section left on the pill
    expect(panel.querySelector('.settings-section-header, .form-group')).toBeNull();
  });
});
