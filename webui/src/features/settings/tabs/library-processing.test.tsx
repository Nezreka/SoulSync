/**
 * Library > Tags and artwork, Music, and Import and files, Music
 * (settings-bic phase 4): the album-art sources on the order list, a
 * service's tags as chips under its row, the counts in words, and
 * every saved key (a source's tags are saved one dotted key at a time, beside
 * the credentials Connections saves).
 */

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import {
  MusicImport,
  MusicTags,
  artList,
  fillEmptyTags,
  postProcessingSummary,
} from './library-processing';

let store: SettingsStore;
let posted: SettingsPayload[];
let available: unknown;

beforeEach(() => {
  const sink: SettingsPayload[] = [];
  posted = sink;
  store = createSettingsStore({
    post: (body) => {
      sink.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  available = { available: [{ id: 'deezer' }, 'spotify', 'nope'] };
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(available) } as Response)),
  );
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
});

const cards = () =>
  render(
    <div>
      <MusicTags store={store} />
      <MusicImport store={store} />
    </div>,
  );
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const box = (path: string) => document.querySelector<HTMLInputElement>(`[data-config="${path}"]`)!;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the rules', () => {
  it('sums the card up, and lists the art sources as loadArtSourceOrder did', () => {
    expect(postProcessingSummary(true, true)).toBe('tagging · artwork');
    expect(postProcessingSummary(false, false)).toBe('nothing enabled');
    expect(artList(['itunes', 'bogus', 'itunes', 'caa'], ['caa', 'deezer'])).toEqual({
      order: ['itunes', 'caa', 'deezer'],
      enabled: { itunes: true, caa: true, deezer: false },
    });
  });
});

describe('Tags and artwork, and Import', () => {
  it('loads every switch as loadSettingsData did; a master off greys its tags', () => {
    act(() =>
      store.hydrate({
        metadata_enhancement: { embed_album_art: false, tags: { genre_merge: false } },
        spotify: { embed_tags: false, tags: { album_id: false } },
        musicbrainz: { use_original_date_for_date: true },
        beatport: { search_all_tracks: true },
        post_processing: { duration_tolerance_seconds: 2.5 },
      }),
    );
    cards();
    expect(input('metadata-enabled').checked).toBe(true);
    expect(input('embed-album-art').checked).toBe(false);
    expect(input('cover-art-download').checked).toBe(true);
    expect(input('prefer-caa-art').checked).toBe(false);
    expect(input('duration-tolerance-seconds').value).toBe('2.5');
    expect(input('embed-spotify').checked).toBe(false);
    expect(box('spotify.tags.album_id').checked).toBe(false);
    expect(box('spotify.tags.track_id').checked).toBe(true);
    expect(box('spotify.tags.track_id').disabled).toBe(true);
    // its row says it in words; a count that is counted, not written down
    const row = (id: string) => input(id).closest('[data-row]')!.textContent;
    expect(row('embed-spotify')).toContain('Off: none of its 3 tags are written');
    expect(row('embed-musicbrainz')).toContain('25 of 25 tags written');
    expect(box('deezer.tags.bpm').disabled).toBe(false);
    expect(box('metadata_enhancement.tags.genre_merge').checked).toBe(false);
    expect(input('mb-use-original-date-for-date').checked).toBe(true);
    expect(input('beatport-search-all').checked).toBe(true);
  });

  it("a service's tags open under its row; its switch does not open them", () => {
    cards();
    const row = input('embed-deezer').closest('[data-row]')!;
    const toggle = row.querySelector<HTMLButtonElement>('[data-tags-toggle="deezer"]')!;
    const chips = document.getElementById(toggle.getAttribute('aria-controls')!)!;
    expect(chips.hidden).toBe(true);
    expect(chips.contains(box('deezer.tags.bpm'))).toBe(true);
    fireEvent.click(input('embed-deezer'));
    expect(chips.hidden).toBe(true);
    fireEvent.click(toggle);
    expect(chips.hidden).toBe(false);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('switching post-processing off hides its options, and its row says so in amber', () => {
    act(() => store.hydrate({}));
    cards();
    const row = () => input('metadata-enabled').closest('[data-row]')!;
    expect(row().textContent).not.toContain('Post-processing is off');
    fireEvent.click(input('metadata-enabled'));
    expect(document.getElementById('post-processing-options')!.hidden).toBe(true);
    expect(row().textContent).toContain(
      'Post-processing is off: downloads get no tags, artwork or lyrics.',
    );
  });

  it('each change saves its own key, a tag as one dotted key of its source', async () => {
    act(() => store.hydrate({ spotify: { client_id: 'abc', tags: { track_id: true } } }));
    cards();
    fireEvent.click(input('lrclib-enabled'));
    fireEvent.click(input('lrclib-cache-player'));
    fireEvent.click(input('replaygain-enabled'));
    fireEvent.change(input('duration-tolerance-seconds'), { target: { value: 'x' } });
    fireEvent.click(input('embed-qobuz'));
    fireEvent.click(box('spotify.tags.artist_id'));
    fireEvent.click(box('musicbrainz.tags.isrc'));
    fireEvent.click(box('metadata_enhancement.tags.quality_tag'));
    fireEvent.click(input('mb-use-original-date-for-date'));
    fireEvent.click(input('beatport-search-all'));
    fireEvent.click(input('import-transfer-permanent'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      metadata_enhancement: {
        lrclib_enabled: false,
        lrclib_cache_player: false,
        'tags.quality_tag': false,
      },
      post_processing: { replaygain_enabled: true, duration_tolerance_seconds: 0 },
      qobuz: { embed_tags: false },
      spotify: { 'tags.artist_id': false },
      musicbrainz: { 'tags.isrc': false, use_original_date_for_date: true },
      beatport: { search_all_tracks: true },
      import: { transfer_is_permanent: false },
    });
    // the source's own settings are untouched in the copy
    expect(store.getState().settings.spotify).toEqual({
      client_id: 'abc',
      tags: { track_id: true, artist_id: false },
    });
  });

  it('the art sources: saved first, connected ones after; moves and switches save the order', async () => {
    act(() => store.hydrate({ metadata_enhancement: { album_art_order: ['caa', 'itunes'] } }));
    cards();
    const rows = () => [...document.querySelectorAll<HTMLElement>('#art-source-list li[data-id]')];
    await waitFor(() =>
      expect(rows().map((r) => r.dataset.id)).toEqual(['caa', 'itunes', 'deezer', 'spotify']),
    );
    const row = (id: string) => rows().find((r) => r.dataset.id === id)!;
    expect(row('itunes').textContent).toContain('Asked 2nd');
    expect(row('deezer').textContent).toContain('Off: not asked');
    // none can be taken out: switched off instead
    fireEvent.click(row('deezer').querySelector('input[type="checkbox"]')!);
    expect(row('deezer').textContent).toContain('Asked 3rd');
    fireEvent.click(document.querySelector('#art-source-list [data-more="deezer"]')!);
    const menu = document.querySelector('#art-source-list [role="menu"]')!;
    const stop = [...menu.querySelectorAll('button')].find((b) =>
      b.textContent!.startsWith('Stop using'),
    )!;
    expect(stop.disabled).toBe(true);
    expect(menu.textContent).toContain('Switch it off instead');
    fireEvent.click(
      [...menu.querySelectorAll('button')].find((b) => b.textContent!.startsWith('Move up'))!,
    );
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().metadata_enhancement).toEqual({ album_art_order: ['caa', 'deezer', 'itunes'] });
  });

  it('says when no art source is connected', async () => {
    available = { available: [] };
    act(() => store.hydrate({}));
    cards();
    await waitFor(() =>
      expect(document.getElementById('art-source-list')!.textContent).toBe(
        'No connected art sources available.',
      ),
    );
  });

  it('the import settings save their keys', async () => {
    act(() => store.hydrate({ import: { default_search_source: 'deezer' } }));
    cards();
    fireEvent.change(input('import-default-search-source'), { target: { value: '' } });
    fireEvent.click(input('import-folder-artist-override'));
    fireEvent.click(input('import-transfer-permanent'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().import).toEqual({
      default_search_source: '',
      folder_artist_override: false,
      transfer_is_permanent: false,
    });
  });

  it('the genre list is drawn here now (DESIGN 3.3: from Filtering)', () => {
    act(() => store.hydrate({ genre_whitelist: { enabled: true, genres: ['jazz'] } }));
    cards();
    expect(input('genre-whitelist-enabled').checked).toBe(true);
    expect(document.querySelector('#genre-whitelist-chips [data-genre="jazz"]')).not.toBeNull();
  });
});

describe('Fill empty tags automatically', () => {
  const select = () => document.getElementById('fill-empty-tags') as HTMLSelectElement;

  it('shows Fill empty tags only when nothing is saved, and keeps a saved choice', () => {
    act(() => store.hydrate({ metadata_enhancement: {} }));
    cards();
    expect(select().value).toBe('fill');
    cleanup();
    act(() => store.hydrate({ metadata_enhancement: { fill_empty_tags: 'update' } }));
    cards();
    expect(select().value).toBe('update');
  });

  it('reads junk as the default', () => {
    expect(fillEmptyTags(undefined)).toBe('fill');
    expect(fillEmptyTags('always')).toBe('fill');
    expect(fillEmptyTags(' Off ')).toBe('off');
  });

  it('saves the choice under metadata_enhancement', async () => {
    act(() => store.hydrate({ metadata_enhancement: {} }));
    cards();
    fireEvent.change(select(), { target: { value: 'off' } });
    await act(() => store.flush());
    expect(saved().metadata_enhancement).toEqual({ fill_empty_tags: 'off' });
  });

  it('lives with post-processing: hidden while it is off', () => {
    act(() => store.hydrate({ metadata_enhancement: { enabled: false } }));
    cards();
    expect(select().closest('#post-processing-options')!.hasAttribute('hidden')).toBe(true);
  });
});
