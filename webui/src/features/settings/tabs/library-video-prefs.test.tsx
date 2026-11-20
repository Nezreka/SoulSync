/**
 * What was Library > Video Preferences (settings-bic phase 4): autoplay and
 * the region on Appearance, "Video pages" (read from the enrichment config,
 * saved together), and Monitor Specials on Downloads > Behaviour, Video (read
 * from the video downloads config, posted alone).
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore } from '../settings-store';
import { AppearanceTab } from './appearance-tab';
import { MonitorSpecialsRow, VideoPagesCard } from './library-video-prefs';
import { videoDownloads } from './video-downloads';
import { videoPrefs } from './video-prefs';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;
let calls: Array<{ url: string; method: string; body?: Record<string, unknown> }>;
let answers: Record<string, unknown>;

beforeEach(() => {
  videoPrefs.reset();
  videoDownloads.reset();
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
  render(
    <>
      <VideoPagesCard />
      <MonitorSpecialsRow />
    </>,
  );
const posts = (url: string) =>
  calls.filter((c) => c.method === 'POST' && c.url === url).map((c) => c.body);

describe('Video pages and Monitor Specials', () => {
  it('Appearance draws the video pages card, on the video side only', () => {
    const store = createSettingsStore({ post: () => Promise.resolve({ success: true }) });
    render(<AppearanceTab store={store} part="page" opened={0} active={false} />);
    // settings-bic phase 5: a card of the Appearance page
    expect(el('video-watch-region').closest('[data-stg-new="appearance"]')).not.toBeNull();
    const card = el('video-watch-region').closest('[data-video-only]')!;
    expect(card.textContent).toContain('Video pages');
  });

  it('reads both configs the first time the Library tab is shown', async () => {
    section();
    expect(el('video-monitor-specials').checked).toBe(true);
    act(() => {
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
    });
    await waitFor(() => expect(el('video-watch-region').value).toBe('JP'));
    expect(el('video-billboard-autoplay').checked).toBe(true);
    await waitFor(() => expect(el('video-monitor-specials').checked).toBe(false));
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
