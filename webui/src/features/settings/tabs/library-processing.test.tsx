/**
 * Library > Post-Processing and Import (SETTINGS_PORT.md, step 8d): the markup
 * they replaced, the summary, the album-art sources, the tag groups and every
 * saved key (a source's tags are saved one dotted key at a time, beside the
 * credentials the vanilla Connections tab saves).
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { artList, postProcessingSummary, ProcessingCards } from './library-processing';

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
  render(<ProcessingCards store={store} part="processing" opened={0} active={false} />);
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const box = (path: string) => document.querySelector<HTMLInputElement>(`[data-config="${path}"]`)!;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the cards are the markup they replaced', () => {
  it('Processing, Post-Processing and Import', () => {
    const { container } = cards();
    const old = vanilla('library-processing.vanilla.html', { afterInit: true });
    expect(shape(container)).toEqual(shape(old));
    const words = (root: Element) =>
      [
        ...root.querySelectorAll('.setting-help-body, .stg-help-text, .settings-hint, .help-text'),
      ].map((el) => (el.textContent || '').replace(/\s+/g, ' ').trim());
    expect(words(container)).toEqual(words(old));
    // the toggle row's words keep their space after the box
    const row = (root: Element) =>
      (root.querySelector('.toggle-row')!.textContent || '').replace(/\s+/g, ' ').trimEnd();
    expect(row(container)).toBe(row(old));
  });
});

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

describe('Post-Processing', () => {
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
    expect(box('spotify.tags.track_id').closest('label')!.className).toBe(
      'checkbox-label disabled-tag',
    );
    expect(box('deezer.tags.bpm').disabled).toBe(false);
    expect(box('metadata_enhancement.tags.genre_merge').checked).toBe(false);
    expect(input('mb-use-original-date-for-date').checked).toBe(true);
    expect(input('beatport-search-all').checked).toBe(true);
    expect(
      document.querySelector('[data-stg-summary="library-post-processing"]')!.textContent,
    ).toBe('tagging');
  });

  it('a group opens from its header; its master switch does not open it', () => {
    cards();
    const group = input('embed-deezer').closest('.tag-service-group')!;
    const body = group.querySelector<HTMLElement>('.tag-service-body')!;
    expect(body.style.display).toBe('none');
    fireEvent.click(input('embed-deezer'));
    expect(body.style.display).toBe('none');
    fireEvent.click(group.querySelector('.tag-service-header')!);
    expect(body.style.display).toBe('block');
    expect(group.querySelector('.tag-group-arrow')!.className).toBe('tag-group-arrow open');
  });

  it('switching post-processing off hides its options', () => {
    act(() => store.hydrate({}));
    cards();
    fireEvent.click(input('metadata-enabled'));
    expect(document.getElementById('post-processing-options')!.style.display).toBe('none');
    expect(
      document.querySelector('[data-stg-summary="library-post-processing"]')!.textContent,
    ).toBe('artwork');
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
    const names = () =>
      [...document.querySelectorAll('#art-source-list .hybrid-source-name')].map(
        (n) => n.textContent,
      );
    await waitFor(() =>
      expect(names()).toEqual(['Cover Art Archive', 'iTunes', 'Deezer', 'Spotify']),
    );
    const item = (id: string) =>
      document.querySelector<HTMLElement>(`#art-source-list [data-source-id="${id}"]`)!;
    expect(item('deezer').className).toBe('hybrid-source-item disabled');
    expect(item('itunes').querySelector('.hybrid-source-priority')!.textContent).toBe('2');
    fireEvent.click(item('deezer').querySelector('input')!);
    fireEvent.click(item('deezer').querySelector('[title="Move up"]')!);
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
});
