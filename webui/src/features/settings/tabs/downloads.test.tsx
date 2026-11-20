/**
 * The Downloads tab (without the download chain), ported from the vanilla
 * Settings page (SETTINGS_PORT.md, step 5): the same markup as the frozen
 * fixtures after settings.js's help lines, and the same values and saves.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { addGenre, filteringSummary, resolveDownloads } from './downloads';
import { DownloadsTab } from './downloads-tab';
import { sourcesModel } from './sources-model';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];

beforeEach(() => {
  // each store saves into its own list: a save the last test left queued
  // must not land in this one's
  const sink: SettingsPayload[] = [];
  posted = sink;
  store = createSettingsStore({
    post: (body) => {
      sink.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  shell.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const part = (name: string) => render(<DownloadsTab store={store} part={name} opened={1} active />);
const box = (id: string) => document.getElementById(id) as HTMLInputElement;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the Downloads tab is the same page it replaced', () => {
  it.each([
    ['behaviour', 'downloads-behaviour.vanilla.html'],
    ['retry', 'downloads-retry.vanilla.html'],
    ['filtering', 'downloads-filtering.vanilla.html'],
  ])('%s: the same elements, ids, classes, help lines and words', (name, fixture) => {
    const { container } = part(name);
    const frozen = vanilla(fixture, { afterInit: true });
    if (name === 'behaviour') {
      // the #dlchain-behaviour wrapper the slot sat in is React now too (step 7a)
      const wrapper = document.createElement('div');
      wrapper.id = 'dlchain-behaviour';
      wrapper.setAttribute('data-stg', 'downloads');
      wrapper.append(...frozen.childNodes);
      frozen.append(wrapper);
    }
    const want = shape(frozen);
    const got = shape(container);
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
  });
});

describe('the values', () => {
  it('reads saved settings the way loadSettingsData did', () => {
    expect(resolveDownloads({})).toMatchObject({
      streamSource: 'youtube',
      maxConcurrent: '3',
      searchTimeout: '',
      retryNext: true,
      retryExhaustive: false,
      retriesPerQuery: '5',
      versionFallback: false,
      versionMinCount: '2',
      clearNearMisses: true,
      reviewRejectDays: '0',
      atomicPublish: false,
      atomicRecover: true,
      allowExplicit: true,
      maxMbPerMinute: '0',
      preferredVersion: '',
      genreWhitelist: false,
      genres: [],
    });
    expect(resolveDownloads({ download_source: { source_search_timeout: 0 } }).searchTimeout).toBe(
      '',
    );
    expect(resolveDownloads({ soulseek: { preferred_version: 'live' } }).preferredVersion).toBe(
      'live',
    );
  });

  it('summarises Filtering as settings.js did', () => {
    const base = resolveDownloads({});
    expect(filteringSummary(base)).toBe('explicit allowed');
    expect(filteringSummary({ ...base, allowExplicit: false, genreWhitelist: true })).toBe(
      'explicit blocked · genre whitelist on',
    );
  });

  it('adds a genre once, whatever its case, in alphabetical order', () => {
    expect(addGenre(['rock', 'Jazz'], ' blues ')).toEqual(['blues', 'Jazz', 'rock']);
    const same = ['rock'];
    expect(addGenre(same, 'ROCK')).toBe(same);
    expect(addGenre(same, '  ')).toBe(same);
  });
});

describe('Download Behaviour', () => {
  it('hides itself, header and all, under the video and audiobook chains', async () => {
    part('behaviour');
    const wrapper = document.getElementById('dlchain-behaviour')!;
    expect(wrapper.hidden).toBe(false);
    act(() => sourcesModel.switchChain('video'));
    expect(wrapper.hidden).toBe(true);
    act(() => sourcesModel.switchChain('audiobooks'));
    expect(wrapper.hidden).toBe(true);
    act(() => sourcesModel.switchChain('music'));
    expect(wrapper.hidden).toBe(false);
    // let the chain reads the switches started settle
    await act(() => new Promise((r) => setTimeout(r, 0)));
  });

  it('saves each of its keys, and Test all sources runs the Sources probe', async () => {
    const probe = vi.spyOn(sourcesModel, 'testAll').mockResolvedValue(undefined);
    part('behaviour');
    fireEvent.change(document.getElementById('stream-source')!, {
      target: { value: 'deezer_preview' },
    });
    fireEvent.change(document.getElementById('max-concurrent-downloads')!, {
      target: { value: '8' },
    });
    // typed, then emptied: blank saves 0, each source's own default
    fireEvent.change(box('source-search-timeout'), { target: { value: '20' } });
    fireEvent.change(box('source-search-timeout'), { target: { value: '' } });
    fireEvent.click(screen.getByText('Test all sources'));
    expect(probe).toHaveBeenCalledOnce();
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      download_source: {
        stream_source: 'deezer_preview',
        max_concurrent: 8,
        source_search_timeout: 0,
      },
    });
  });
});

describe('Retry Logic and Album Publishing', () => {
  it('shows each number only while its switch is on', () => {
    part('retry');
    const row = (id: string) => document.getElementById(id) as HTMLElement;
    expect(row('retries-per-query-row').style.display).toBe('none');
    expect(row('version-mismatch-min-count-row').style.display).toBe('none');
    fireEvent.click(box('retry-exhaustive'));
    expect(row('retries-per-query-row').style.display).toBe('');
    fireEvent.click(box('accept-version-mismatch-fallback'));
    expect(row('version-mismatch-min-count-row').style.display).toBe('');
  });

  it('saves its keys, numbers clamped as the collector did', async () => {
    part('retry');
    fireEvent.click(box('retry-next-candidate'));
    fireEvent.click(box('retry-exhaustive'));
    fireEvent.change(box('retries-per-query'), { target: { value: '0' } });
    fireEvent.click(box('accept-version-mismatch-fallback'));
    fireEvent.change(box('version-mismatch-min-count'), { target: { value: 'x' } });
    fireEvent.click(box('clear-near-misses-on-success'));
    fireEvent.change(box('review-auto-reject-days'), { target: { value: '-3' } });
    fireEvent.change(box('review-auto-reject-days'), { target: { value: '14' } });
    fireEvent.click(box('album-atomic-publish'));
    fireEvent.click(box('album-atomic-recover'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      post_processing: {
        retry_next_candidate_on_mismatch: false,
        retry_exhaustive: true,
        retries_per_query: 5,
        accept_version_mismatch_fallback: true,
        version_mismatch_min_count: 2,
        clear_near_misses_on_success: false,
        review_auto_reject_days: 14,
      },
      album_downloads: { atomic_publish: true, atomic_recover_orphans: false },
    });
  });

  it('a setting-row "i" opens the help under the row', () => {
    part('retry');
    const group = box('retry-exhaustive').closest('.form-group')!;
    const body = group.querySelector('.setting-help-body') as HTMLElement;
    expect(body.hidden).toBe(true);
    fireEvent.click(group.querySelector('.stg-help-line .info-icon')!);
    expect(body.hidden).toBe(false);
  });
});

describe('Filtering', () => {
  it('greys out the two explicit preferences while explicit content is blocked', async () => {
    part('filtering');
    expect(box('prefer-explicit').disabled).toBe(false);
    fireEvent.click(box('allow-explicit'));
    for (const id of ['prefer-explicit', 'exclude-clean']) {
      expect(box(id).disabled).toBe(true);
      expect((box(id).closest('.checkbox-label') as HTMLElement).style.opacity).toBe('0.45');
    }
    await waitFor(() => expect(saved()).toEqual({ content_filter: { allow_explicit: false } }));
  });

  it('saves the size cap, the version and the explicit preferences', async () => {
    part('filtering');
    fireEvent.click(box('prefer-explicit'));
    fireEvent.click(box('exclude-clean'));
    fireEvent.change(box('music-max-mb-per-minute'), { target: { value: '-3' } });
    fireEvent.change(document.getElementById('preferred-version')!, {
      target: { value: 'extended' },
    });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      content_filter: { prefer_explicit: true, exclude_clean: true },
      download_source: { max_mb_per_minute: 0 },
      soulseek: { preferred_version: 'extended' },
    });
  });

  it('switching the whitelist on with nothing in it loads the defaults', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ genres: ['pop', 'rock'] }) })),
    );
    part('filtering');
    const container = document.getElementById('genre-whitelist-container') as HTMLElement;
    expect(container.style.display).toBe('none');
    fireEvent.click(box('genre-whitelist-enabled'));
    expect(container.style.display).toBe('');
    await waitFor(() => expect(document.querySelectorAll('.genre-chip')).toHaveLength(2));
    expect(document.getElementById('genre-whitelist-count')!.textContent).toBe('2 genres');
    expect(shell.showToast).toHaveBeenCalledWith('Loaded 2 default genres', 'success');
    await waitFor(() =>
      expect(saved()).toEqual({ genre_whitelist: { enabled: true, genres: ['pop', 'rock'] } }),
    );
  });

  it('switching it on keeps a saved list instead of replacing it with the defaults', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    store.hydrate({ genre_whitelist: { enabled: false, genres: ['shoegaze'] } });
    part('filtering');
    fireEvent.click(box('genre-whitelist-enabled'));
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(
      [...document.querySelectorAll('.genre-chip')].map((c) => c.firstChild?.textContent),
    ).toEqual(['shoegaze']);
  });

  it('adds on Enter, filters as you type, and removes with ×', async () => {
    store.hydrate({ genre_whitelist: { enabled: true, genres: ['jazz', 'rock'] } });
    part('filtering');
    const search = box('genre-whitelist-search');
    fireEvent.change(search, { target: { value: 'Blues' } });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(search.value).toBe('');
    const chips = () =>
      [...document.querySelectorAll('.genre-chip')].map((c) => c.firstChild?.textContent);
    expect(chips()).toEqual(['Blues', 'jazz', 'rock']);
    fireEvent.change(search, { target: { value: 'ja' } });
    expect(chips()).toEqual(['jazz']);
    expect(document.getElementById('genre-whitelist-count')!.textContent).toBe('3 genres');
    fireEvent.change(search, { target: { value: '' } });
    fireEvent.click(document.querySelector('.genre-chip-x[data-genre="rock"]')!);
    expect(chips()).toEqual(['Blues', 'jazz']);
    await waitFor(() =>
      expect(saved()).toEqual({ genre_whitelist: { genres: ['Blues', 'jazz'] } }),
    );
  });

  it('an empty, switched-on whitelist says how to fill it', () => {
    store.hydrate({ genre_whitelist: { enabled: true, genres: [] } });
    part('filtering');
    expect(document.getElementById('genre-whitelist-chips')!.textContent).toMatch(
      /^No genres configured/,
    );
    expect(document.getElementById('genre-whitelist-count')!.textContent).toBe('');
  });

  it('Reset to Defaults that fails says so', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('offline'))),
    );
    part('filtering');
    await act(async () => fireEvent.click(screen.getByText('Reset to Defaults')));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Failed to load defaults', 'error'),
    );
  });
});
