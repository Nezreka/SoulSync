import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ArtistFixMatch } from './artist-matches-modal';

/**
 * The "Wrong match?" button and its panel. Pins the admin/library gate, that
 * the rows come from the artist RECORD (the page payload has no statuses),
 * and that each action (pick, clear, auto) hits the right endpoint, re-reads
 * the record and tells the page.
 */

const RECORD = {
  success: true,
  record: {
    name: 'Aphex Twin',
    spotify_artist_id: 'sp-old',
    spotify_match_status: 'matched',
    spotify_last_attempted: '2026-09-15 10:00:00',
    deezer_match_status: 'not_found',
  },
};

let calls: { url: string; method: string; body: Record<string, unknown> | null }[] = [];
let record: unknown = RECORD;
let searchResults: unknown[] = [];

function stubFetch() {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        method: init?.method || 'GET',
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : null,
      });
      const json = (body: unknown) =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      if (url.endsWith('/record')) return json(record);
      if (url.endsWith('search-service')) return json({ success: true, results: searchResults });
      if (url.endsWith('/enrich'))
        return json({ success: true, results: { deezer: { success: true } } });
      return json({ success: true, updated_data: null });
    }),
  );
}

function mount(overrides: Partial<Parameters<typeof ArtistFixMatch>[0]> = {}) {
  const onChanged = vi.fn();
  render(
    <ArtistFixMatch
      artist={{ id: 42, name: 'Aphex Twin' }}
      isSourceArtist={false}
      isAdmin={true}
      onChanged={onChanged}
      {...overrides}
    />,
  );
  return { onChanged };
}

const button = () => document.getElementById('artist-fix-match-btn') as HTMLElement;
const overlay = () => document.getElementById('artist-matches-overlay');
const row = (svc: string) => document.querySelector(`.amx-row[data-svc="${svc}"]`) as HTMLElement;

async function open() {
  fireEvent.click(button());
  await waitFor(() => expect(row('spotify')).not.toBeNull());
}

beforeEach(() => {
  window.showToast = vi.fn();
  window.showConfirmDialog = vi.fn(async () => true) as never;
  record = RECORD;
  searchResults = [];
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
  Reflect.deleteProperty(window, 'showConfirmDialog');
  // portal-rendered: cleanup() unmounts the tree, never wipe body.innerHTML
  cleanup();
});

describe('the Wrong match? button', () => {
  it('shows for an admin on a library artist only', () => {
    mount();
    expect(button()).not.toBeNull();
    cleanup();
    mount({ isAdmin: false });
    expect(button()).toBeNull();
    cleanup();
    mount({ isSourceArtist: true });
    expect(button()).toBeNull();
    cleanup();
    mount({ artist: { name: 'No id' } });
    expect(button()).toBeNull();
  });

  it('opens a panel on the body built from the artist record, one row per source', async () => {
    mount();
    await open();
    expect(overlay()?.parentElement).toBe(document.body);
    expect(calls[0].url).toBe('/api/artist/42/record');

    expect(document.querySelectorAll('.amx-row')).toHaveLength(11);
    expect(screen.getByText('1 of 11 sources matched')).toBeTruthy();

    const spotify = within(row('spotify'));
    expect(spotify.getByText('Matched')).toBeTruthy();
    expect(spotify.getByRole('link').getAttribute('href')).toBe(
      'https://open.spotify.com/artist/sp-old',
    );
    expect(spotify.getByText('Change')).toBeTruthy();
    expect(spotify.getByText('Clear')).toBeTruthy();
    // a matched source is kept by every worker, so re-running it does nothing
    expect(spotify.queryByText('Auto')).toBeNull();

    const deezer = within(row('deezer'));
    expect(deezer.getByText('Not found')).toBeTruthy();
    expect(deezer.getByText('Find match')).toBeTruthy();
    expect(deezer.getByText('Auto')).toBeTruthy();
    expect(deezer.queryByText('Clear')).toBeNull();
  });

  it('closes on the backdrop and on Escape, one step at a time', async () => {
    mount();
    await open();
    fireEvent.click(within(row('deezer')).getByText('Find match'));
    expect(row('deezer').querySelector('.amx-search')).not.toBeNull();

    // first Escape closes the search, second the panel
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(row('deezer').querySelector('.amx-search')).toBeNull();
    expect(overlay()).not.toBeNull();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(overlay()).toBeNull());

    await open();
    fireEvent.click(overlay() as HTMLElement);
    await waitFor(() => expect(overlay()).toBeNull());
  });
});

describe('changing a match', () => {
  it('searches the source with the artist name, applies the pick and re-reads the record', async () => {
    searchResults = [
      { id: 'sp-old', name: 'Aphex Twin (wrong)', image: '' },
      { id: 'sp-new', name: 'Aphex Twin', extra: '2.1M followers' },
    ];
    const { onChanged } = mount();
    await open();
    fireEvent.click(within(row('spotify')).getByText('Change'));

    await screen.findByText('2.1M followers');
    const search = calls.find((c) => c.url.endsWith('search-service'));
    expect(search?.body).toEqual({
      service: 'spotify',
      entity_type: 'artist',
      query: 'Aphex Twin',
    });
    // the stored match is marked, not offered again
    expect(screen.getByText('Current')).toBeTruthy();
    expect(screen.getAllByText('Use this')).toHaveLength(1);

    record = {
      success: true,
      record: { ...RECORD.record, spotify_artist_id: 'sp-new' },
    };
    fireEvent.click(screen.getByText('Use this'));

    const put = await waitFor(() => {
      const found = calls.find((c) => c.url.endsWith('manual-match'));
      expect(found).toBeTruthy();
      return found!;
    });
    expect(put.method).toBe('PUT');
    expect(put.body).toEqual({
      entity_type: 'artist',
      entity_id: 42,
      service: 'spotify',
      service_id: 'sp-new',
      artist_id: 42,
    });
    await waitFor(() =>
      expect(within(row('spotify')).getByRole('link').getAttribute('href')).toBe(
        'https://open.spotify.com/artist/sp-new',
      ),
    );
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(row('spotify').querySelector('.amx-search')).toBeNull();
    expect(calls.filter((c) => c.url.endsWith('/record'))).toHaveLength(2);
  });

  it('matches a proxied result through its real provider', async () => {
    searchResults = [{ id: 'hb1', name: 'Via hydrabase', provider: 'hydrabase' }];
    mount();
    await open();
    fireEvent.click(within(row('spotify')).getByText('Change'));
    fireEvent.click(await screen.findByText('Use this'));
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('manual-match'))?.body?.service).toBe('hydrabase'),
    );
  });

  it('searches again with what was typed, on Enter and on the button', async () => {
    mount();
    await open();
    fireEvent.click(within(row('deezer')).getByText('Find match'));
    await screen.findByText('Nothing found. Try another spelling, or paste an id or link.');

    const input = row('deezer').querySelector('.amx-search-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'AFX' } });
    fireEvent.keyDown(input, { target: { value: 'AFX' }, key: 'Enter' });
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith('search-service')).at(-1)?.body?.query).toBe('AFX'),
    );
  });

  it('keeps a failed pick on screen with the error', async () => {
    searchResults = [{ id: 'sp-new', name: 'Aphex Twin' }];
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('manual-match')) {
        return new Response(JSON.stringify({ success: false, error: 'Entity not found' }));
      }
      if (url.endsWith('search-service')) {
        return new Response(JSON.stringify({ success: true, results: searchResults }));
      }
      return new Response(JSON.stringify(record));
    });
    const { onChanged } = mount();
    await open();
    fireEvent.click(within(row('spotify')).getByText('Change'));
    fireEvent.click(await screen.findByText('Use this'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Match failed: Entity not found', 'error'),
    );
    expect(onChanged).not.toHaveBeenCalled();
    expect(row('spotify').querySelector('.amx-search')).not.toBeNull();
  });
});

describe('clearing and auto lookup', () => {
  it('clears through the shared confirm dialog and re-reads', async () => {
    const { onChanged } = mount();
    await open();
    record = { success: true, record: { ...RECORD.record, spotify_artist_id: null } };
    fireEvent.click(within(row('spotify')).getByText('Clear'));

    await waitFor(() => expect(calls.some((c) => c.url.endsWith('clear-match'))).toBe(true));
    expect(window.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ destructive: true, confirmText: 'Clear match' }),
    );
    expect(calls.find((c) => c.url.endsWith('clear-match'))?.body).toEqual({
      entity_type: 'artist',
      entity_id: 42,
      service: 'spotify',
      artist_id: 42,
    });
    await waitFor(() => expect(within(row('spotify')).getByText('Not tried yet')).toBeTruthy());
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('does nothing when the confirm is declined', async () => {
    window.showConfirmDialog = vi.fn(async () => false) as never;
    const { onChanged } = mount();
    await open();
    fireEvent.click(within(row('spotify')).getByText('Clear'));
    await new Promise((r) => setTimeout(r, 0));
    expect(calls.some((c) => c.url.endsWith('clear-match'))).toBe(false);
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('Auto runs the source lookup for the artist and re-reads', async () => {
    const { onChanged } = mount();
    await open();
    fireEvent.click(within(row('deezer')).getByText('Auto'));
    const enrich = await waitFor(() => {
      const found = calls.find((c) => c.url.endsWith('/enrich'));
      expect(found).toBeTruthy();
      return found!;
    });
    expect(enrich.body).toEqual({
      entity_type: 'artist',
      entity_id: 42,
      service: 'deezer',
      name: 'Aphex Twin',
      artist_name: 'Aphex Twin',
      artist_id: 42,
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  });
});

describe('a record that fails to load', () => {
  it('shows the error instead of an empty list', async () => {
    record = { success: false, error: 'Artist not found in library' };
    mount();
    fireEvent.click(button());
    await screen.findByText('Artist not found in library');
    expect(document.querySelectorAll('.amx-row')).toHaveLength(0);
  });
});
