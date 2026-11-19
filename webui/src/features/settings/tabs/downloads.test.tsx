/**
 * Downloads > Behaviour and Filtering (tabs/downloads/): the same values, keys
 * and saves the old Downloads tab's sections had, on the new rows. (The frozen
 * markup comparisons went with the markup; the key guard, guard/, holds every
 * control's id, load and save.)
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { addGenre, filteringSummary, resolveDownloads } from './downloads';
import { MusicBehaviour, VideoBehaviour } from './downloads/behaviour-view';
import { FilteringView } from './downloads/filtering-view';
import { SourcesView } from './downloads/sources-view';
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

const part = (name: 'behaviour' | 'retry' | 'filtering' | 'sources') =>
  render(
    name === 'filtering' ? (
      <FilteringView store={store} />
    ) : name === 'sources' ? (
      <SourcesView store={store} opened={0} active={false} />
    ) : (
      <MusicBehaviour store={store} />
    ),
  );
const box = (id: string) => document.getElementById(id) as HTMLInputElement;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

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

describe('the words that name each setting', () => {
  // the frozen markup fixtures pinned these labels; the guard pins the ids and
  // keys, this pins that each control is still called what it is
  it('every control on Behaviour and Filtering is labelled by its name, without a colon', () => {
    render(
      <>
        <MusicBehaviour store={store} />
        <FilteringView store={store} />
      </>,
    );
    const names: Record<string, RegExp> = {
      'stream-source': /^Stream and preview source/,
      'max-concurrent-downloads': /^Concurrent downloads/,
      'source-search-timeout': /^Source search timeout/,
      'retry-next-candidate': /^Retry next-best candidate on mismatch/,
      'retry-exhaustive': /^Exhaustive retry/,
      'retries-per-query': /^Retries per query/,
      'accept-version-mismatch-fallback': /^Accept best version mismatch as last resort/,
      'version-mismatch-min-count': /^Minimum matching mismatches before accepting/,
      'album-atomic-publish': /^Publish albums only when complete/,
      'album-atomic-recover': /^Recover interrupted albums on startup/,
      'allow-explicit': /^Allow explicit content in downloads/,
      'prefer-explicit': /^Prefer explicit versions when matching/,
      'exclude-clean': /^Never download clean versions/,
      'music-max-mb-per-minute': /^Maximum music download size/,
      'preferred-version': /^Prefer a version when one exists/,
      'genre-whitelist-enabled': /^Enable strict genre filtering/,
    };
    for (const [id, name] of Object.entries(names)) {
      const el = document.getElementById(id) as HTMLElement;
      expect(el, id).not.toBeNull();
      const label =
        [...(el as HTMLInputElement).labels!].map((l) => l.textContent).join(' ') ||
        document.getElementById(el.getAttribute('aria-labelledby') ?? '')?.textContent ||
        el.getAttribute('aria-label') ||
        '';
      expect(label.trim(), id).toMatch(name);
      expect(label.trim(), id).not.toMatch(/:\s*$/);
    }
  });
});

describe('Download Behaviour', () => {
  it('saves each of its keys', async () => {
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

describe('Test all sources', () => {
  it('sits beside the kind switch on Sources, whatever kind is showing, and runs the probe', async () => {
    const probe = vi.spyOn(sourcesModel, 'testAll').mockResolvedValue(undefined);
    part('sources');
    for (const kind of ['video', 'audiobooks', 'music']) {
      act(() => sourcesModel.switchChain(kind));
      expect(document.getElementById('test-all-sources-btn')).toBeTruthy();
    }
    fireEvent.click(screen.getByRole('button', { name: 'Test all sources' }));
    expect(probe).toHaveBeenCalledOnce();
    // let the chain reads the switches started settle
    await act(() => new Promise((r) => setTimeout(r, 0)));
  });
});

describe('the video side', () => {
  it('its one rule saves at once through the video downloads config', async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: RequestInit) => {
        if (init?.method === 'POST') calls.push({ url, body: JSON.parse(String(init.body)) });
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ redownload_failed: true, success: true }),
        });
      }),
    );
    render(<VideoBehaviour />);
    await waitFor(() => expect(box('video-redownload-failed').checked).toBe(true));
    fireEvent.click(box('video-redownload-failed'));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toBe('/api/video/downloads/config');
    expect(calls[0].body).toMatchObject({ redownload_failed: false });
    // nothing rides on the page's save
    expect(store.hasPending()).toBe(false);
  });
});

describe('Retry Logic and Album Publishing', () => {
  it('shows each number only while its switch is on', () => {
    part('retry');
    const row = (id: string) => document.getElementById(id) as HTMLElement;
    expect(row('retries-per-query-row').hidden).toBe(true);
    expect(row('version-mismatch-min-count-row').hidden).toBe(true);
    // hidden, not gone: the field keeps its place in the page
    expect(box('retries-per-query')).toBeTruthy();
    fireEvent.click(box('retry-exhaustive'));
    expect(row('retries-per-query-row').hidden).toBe(false);
    fireEvent.click(box('accept-version-mismatch-fallback'));
    expect(row('version-mismatch-min-count-row').hidden).toBe(false);
  });

  it('saves its keys, numbers clamped as the collector did', async () => {
    part('retry');
    fireEvent.click(box('retry-next-candidate'));
    fireEvent.click(box('retry-exhaustive'));
    fireEvent.change(box('retries-per-query'), { target: { value: '0' } });
    fireEvent.click(box('accept-version-mismatch-fallback'));
    fireEvent.change(box('version-mismatch-min-count'), { target: { value: 'x' } });
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
      },
      album_downloads: { atomic_publish: true, atomic_recover_orphans: false },
    });
  });

  it('a row says what it does in one line, and its "i" opens the rest', () => {
    part('retry');
    const row = box('retry-exhaustive').closest('[data-row]') as HTMLElement;
    expect(row.textContent).toContain('Give every source');
    const i = row.querySelector('[aria-label="More about this setting"]') as HTMLElement;
    const rest = document.getElementById(i.getAttribute('aria-controls')!) as HTMLElement;
    expect(rest.hidden).toBe(true);
    fireEvent.click(i);
    expect(rest.hidden).toBe(false);
    expect(rest.textContent).toContain('Needs the option above');
  });

  it('marks a row whose change has not saved yet', async () => {
    const slow = createSettingsStore({
      post: () => Promise.resolve({ success: true }),
      delay: 60_000,
    });
    render(<MusicBehaviour store={slow} />);
    const row = () => box('album-atomic-publish').closest('[data-row]') as HTMLElement;
    expect(row().querySelector('[title="Not saved yet"]')).toBeNull();
    fireEvent.click(box('album-atomic-publish'));
    expect(row().querySelector('[title="Not saved yet"]')).toBeTruthy();
    expect(slow.pendingCount()).toBe(1);
    await act(async () => void (await slow.flush()));
    expect(row().querySelector('[title="Not saved yet"]')).toBeNull();
  });
});

describe('Filtering', () => {
  it('greys out the two explicit preferences while explicit content is blocked', async () => {
    part('filtering');
    expect(box('prefer-explicit').disabled).toBe(false);
    fireEvent.click(box('allow-explicit'));
    for (const id of ['prefer-explicit', 'exclude-clean']) {
      expect(box(id).disabled).toBe(true);
      // and the row says why it is off: it sits under the switch it needs
      expect(box(id).closest('[data-row]')!.textContent).toContain('explicit');
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
    expect(container.hidden).toBe(true);
    fireEvent.click(box('genre-whitelist-enabled'));
    expect(container.hidden).toBe(false);
    await waitFor(() => expect(document.querySelectorAll('[data-genre]')).toHaveLength(2));
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
      [...document.querySelectorAll('[data-genre]')].map((c) => c.getAttribute('data-genre')),
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
      [...document.querySelectorAll('[data-genre]')].map((c) => c.getAttribute('data-genre'));
    expect(chips()).toEqual(['Blues', 'jazz', 'rock']);
    fireEvent.change(search, { target: { value: 'ja' } });
    expect(chips()).toEqual(['jazz']);
    expect(document.getElementById('genre-whitelist-count')!.textContent).toBe('3 genres');
    fireEvent.change(search, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Remove rock' }));
    expect(chips()).toEqual(['Blues', 'jazz']);
    await waitFor(() =>
      expect(saved()).toEqual({ genre_whitelist: { genres: ['Blues', 'jazz'] } }),
    );
  });

  it('an empty, switched-on whitelist says how to fill it', () => {
    store.hydrate({ genre_whitelist: { enabled: true, genres: [] } });
    part('filtering');
    expect(document.getElementById('genre-whitelist-chips')!.textContent).toMatch(/^No genres yet/);
    expect(document.getElementById('genre-whitelist-count')!.textContent).toBe('');
  });

  it('Reset to Defaults that fails says so', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('offline'))),
    );
    part('filtering');
    await act(async () => fireEvent.click(screen.getByText('Reset to defaults')));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Failed to load defaults', 'error'),
    );
  });
});
