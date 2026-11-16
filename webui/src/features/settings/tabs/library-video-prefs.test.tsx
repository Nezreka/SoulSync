/**
 * Library > Video Preferences (SETTINGS_PORT.md, video pass V2c): the markup
 * it replaced, the summary, autoplay and the region (read from the enrichment
 * config, saved together), and Monitor Specials (read from the video
 * downloads config, posted alone).
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { LibraryTab } from './library-folders';
import { videoDownloads } from './video-downloads';
import { videoPrefs } from './video-prefs';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;
let store: SettingsStore;
let calls: Array<{ url: string; method: string; body?: Record<string, unknown> }>;
let answers: Record<string, unknown>;

beforeEach(() => {
  videoPrefs.reset();
  videoDownloads.reset();
  store = createSettingsStore({ post: () => Promise.resolve({ success: true }), delay: 0 });
  calls = [];
  answers = {
    'GET /api/video/enrichment/config': {
      billboard_autoplay: true,
      watch_region: 'JP',
      tmdb_api_key: 'k',
    },
    'GET /api/video/downloads/config': { monitor_specials: false, movies_path: '/m' },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body =
        typeof init?.body === 'string'
          ? (JSON.parse(init.body) as Record<string, unknown>)
          : undefined;
      calls.push({ url, method, body });
      const data = answers[`${method} ${url}`] ?? { success: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete shell.showToast;
  document.body.removeAttribute('data-side');
});

const el = (id: string) => document.getElementById(id) as HTMLInputElement;
const section = () =>
  render(<LibraryTab store={store} part="video-prefs" opened={0} active={false} />);
const posts = (url: string) =>
  calls.filter((c) => c.method === 'POST' && c.url === url).map((c) => c.body);
const summary = () => document.querySelector('[data-stg-summary="library-video-prefs"]')!;

describe('Video Preferences', () => {
  it('is the markup it replaced', () => {
    const { container } = section();
    expect(shape(container)).toEqual(shape(vanilla('video-prefs.vanilla.html')));
  });

  it('reads both configs the first time the Library tab is shown, and the summary', async () => {
    section();
    expect(summary().textContent).toBe('Trailers & where-to-watch region');
    expect(el('video-monitor-specials').checked).toBe(true);
    act(() => {
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
    });
    await waitFor(() => expect(el('video-watch-region').value).toBe('JP'));
    expect(el('video-billboard-autoplay').checked).toBe(true);
    await waitFor(() => expect(el('video-monitor-specials').checked).toBe(false));
    // the summary goes live with the settings, as refreshLibrarySummaries did
    act(() => store.hydrate({}));
    expect(summary().textContent).toBe('where to watch: Japan');
    expect(summary().classList.contains('is-live')).toBe(true);
  });

  it('autoplay and the region save together, with a toast', async () => {
    section();
    fireEvent.change(el('video-watch-region'), { target: { value: 'DE' } });
    await waitFor(() => expect(posts('/api/video/enrichment/config')).toHaveLength(1));
    expect(posts('/api/video/enrichment/config')[0]).toEqual({
      billboard_autoplay: false,
      watch_region: 'DE',
    });
    fireEvent.click(el('video-billboard-autoplay'));
    await waitFor(() => expect(posts('/api/video/enrichment/config')).toHaveLength(2));
    expect(posts('/api/video/enrichment/config')[1]).toEqual({
      billboard_autoplay: true,
      watch_region: 'DE',
    });
    expect(shell.showToast).toHaveBeenCalledWith('Preferences saved', 'success');
  });

  it('Monitor Specials posts its one key to the downloads config', async () => {
    section();
    await act(() => videoDownloads.load());
    fireEvent.click(el('video-monitor-specials'));
    await waitFor(() => expect(posts('/api/video/downloads/config')).toHaveLength(1));
    expect(posts('/api/video/downloads/config')[0]).toEqual({ monitor_specials: true });
    expect(el('video-monitor-specials').checked).toBe(true);
    expect(shell.showToast).toHaveBeenCalledWith('Specials monitored', 'success');
  });

  it('the video side save button posts the preferences quietly', async () => {
    section();
    const button = document.createElement('button');
    button.id = 'save-settings';
    document.body.append(button);
    document.body.setAttribute('data-side', 'video');
    fireEvent.click(button);
    await waitFor(() => expect(posts('/api/video/enrichment/config')).toHaveLength(1));
    expect(shell.showToast).not.toHaveBeenCalledWith('Preferences saved', 'success');
    button.remove();
  });
});
