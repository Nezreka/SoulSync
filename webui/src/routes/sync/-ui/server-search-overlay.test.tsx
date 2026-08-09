/**
 * The library-search overlay, against pages-extra.js 746-884. Classes and ids
 * are the CSS contract for this popover, so they are asserted as literals.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CompareTrack, LibrarySearchTrack } from '../-sync.server';

import { ServerSearchOverlay } from './server-search-overlay';

/** The onSelect seam, typed so the recorded calls are typed too. */
type SelectFn = (
  newTrackId: string,
  resolvePicked: () => LibrarySearchTrack | undefined,
) => Promise<boolean>;

const selectStub = (impl: SelectFn = async () => true) => vi.fn<SelectFn>(impl);

const TRACK: CompareTrack = {
  match_status: 'missing',
  source_track: { name: 'Nights', artist: 'Frank Ocean' },
  server_track: null,
};

let searchPayload: unknown = {};
let urls: string[] = [];

beforeEach(() => {
  urls = [];
  searchPayload = {
    success: true,
    tracks: [
      {
        id: 42,
        title: 'Nights',
        artist_name: 'Frank Ocean',
        album_title: 'Blonde',
        file_path: '/music/a.m4a',
        bitrate: 256,
        duration: 307000,
        album_thumb_url: 'http://art/1.jpg',
      },
      { id: 43, title: 'Nights (Live)', artist_name: 'Frank Ocean', file_path: '/music/b.wma' },
    ],
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify(searchPayload));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderOverlay(props: Record<string, unknown> = {}) {
  const onClose = vi.fn();
  const onSelect = selectStub();
  render(
    <ServerSearchOverlay
      track={TRACK}
      mode="add"
      onClose={onClose}
      onSelect={onSelect}
      {...props}
    />,
  );
  return { onClose, onSelect };
}

describe('ServerSearchOverlay', () => {
  it('titles itself by mode (771)', async () => {
    renderOverlay({ mode: 'replace' });
    expect(screen.getByText('Swap Track')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Blonde', { exact: false })).toBeInTheDocument());
  });

  it('seeds the input with the TITLE ONLY and searches on mount (753, 815)', async () => {
    renderOverlay();
    const input = document.querySelector('#server-search-input') as HTMLInputElement;
    expect(input.value).toBe('Nights');
    // 837-838: and the artist rides along as a relevance hint, not in the query.
    await waitFor(() =>
      expect(urls[0]).toBe('/api/library/search-tracks?q=Nights&limit=20&artist=Frank%20Ocean'),
    );
  });

  it('focuses and SELECTS the seed so typing replaces it (812-813)', async () => {
    renderOverlay();
    const input = document.querySelector('#server-search-input') as HTMLInputElement;
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe('Nights'.length);
    await waitFor(() => expect(urls).toHaveLength(1));
  });

  it('shows the source context line, and omits it when nothing is named (772)', async () => {
    renderOverlay();
    await waitFor(() => expect(document.querySelector('.server-search-context')).not.toBeNull());
    expect(document.querySelector('.server-search-context-artist')?.textContent).toBe(
      'Frank Ocean',
    );
    expect(document.querySelector('.server-search-context-name')?.textContent).toBe('Nights');

    render(
      <ServerSearchOverlay
        track={{ match_status: 'missing' }}
        mode="add"
        onClose={vi.fn()}
        onSelect={selectStub()}
      />,
    );
    await waitFor(() =>
      expect(document.querySelectorAll('.server-search-popover')).toHaveLength(2),
    );
    expect(document.querySelectorAll('.server-search-context')).toHaveLength(1);
  });

  it('renders each result with its format, bitrate and duration (857-878)', async () => {
    renderOverlay();
    await waitFor(() => expect(document.querySelectorAll('.server-search-result')).toHaveLength(2));

    const first = document.querySelectorAll('.server-search-result')[0];
    // M4A reads AAC; the duration uses the ROUNDING formatter.
    expect(first.querySelector('.server-search-format')?.textContent).toBe('AAC');
    expect(first.querySelector('.server-search-bitrate')?.textContent).toBe('256k');
    expect(first.querySelector('.server-search-dur')?.textContent).toBe('5:07');
    expect(first.querySelector('.server-search-result-meta')?.textContent).toBe(
      'Frank Ocean · Blonde',
    );
    expect(first.querySelector('img')?.getAttribute('src')).toBe('http://art/1.jpg');
    // 863: the cascade delay is per index.
    expect((first as HTMLElement).style.animationDelay).toBe('0s');
    expect(
      (document.querySelectorAll('.server-search-result')[1] as HTMLElement).style.animationDelay,
    ).toBe('0.03s');

    // The second names no album, no bitrate, no duration, and .wma is unlisted.
    const second = document.querySelectorAll('.server-search-result')[1];
    expect(second.querySelector('.server-search-format')).toBeNull();
    expect(second.querySelector('.server-search-bitrate')).toBeNull();
    expect(second.querySelector('.server-search-dur')).toBeNull();
    expect(second.querySelector('.server-search-result-meta')?.textContent).toBe('Frank Ocean');
    expect(second.querySelector('.server-search-result-art-empty')).not.toBeNull();
  });

  it('counts the results in the header, only when there are results (855)', async () => {
    renderOverlay();
    await waitFor(() =>
      expect(document.querySelector('#server-search-results-header')?.textContent).toBe(
        '2 results',
      ),
    );
  });

  it('shows the no-results body for a failed OR an empty search (841-846)', async () => {
    searchPayload = { success: true, tracks: [] };
    renderOverlay();
    await waitFor(() => expect(screen.getByText('No results found')).toBeInTheDocument());
    expect(document.querySelector('#server-search-results-header')?.textContent).toBe('');

    // success:false is checked on its OWN — a failed response carrying rows
    // must still not render them.
    searchPayload = { success: false, error: 'boom', tracks: [{ id: 1, title: 'Ghost' }] };
    render(
      <ServerSearchOverlay track={TRACK} mode="add" onClose={vi.fn()} onSelect={selectStub()} />,
    );
    // 841: an unsuccessful response is NOT distinguished from an empty one.
    await waitFor(() => expect(screen.getAllByText('No results found')).toHaveLength(2));
    expect(screen.queryByText('Ghost')).not.toBeInTheDocument();
  });

  it('reports a thrown search in the results pane (881-883)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down');
      }),
    );
    renderOverlay();
    await waitFor(() => expect(screen.getByText(/network down/)).toBeInTheDocument());
  });

  it('an emptied query asks for one and issues no request (826-830)', async () => {
    renderOverlay();
    await waitFor(() => expect(urls).toHaveLength(1));
    const input = document.querySelector('#server-search-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('Type a search query')).toBeInTheDocument());
    expect(urls).toHaveLength(1);
  });

  it('searches on Enter only — typing alone never fires a request (785)', async () => {
    renderOverlay();
    await waitFor(() => expect(urls).toHaveLength(1));
    const input = document.querySelector('#server-search-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'other song' } });
    expect(urls).toHaveLength(1);
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(urls).toHaveLength(2));
    expect(urls[1]).toContain('q=other%20song');
    // 759: the hint stays the one captured when the overlay opened.
    expect(urls[1]).toContain('artist=Frank%20Ocean');
  });

  it('closes on the backdrop, on Escape and on the × — but not inside (779, 797-799)', async () => {
    const { onClose } = renderOverlay();
    await waitFor(() => expect(document.querySelectorAll('.server-search-result')).toHaveLength(2));

    fireEvent.click(document.querySelector('.server-search-popover') as Element);
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(document.querySelector('#server-search-overlay') as Element);
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);

    fireEvent.click(document.querySelector('.server-search-close') as Element);
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('drops the Escape listener when it unmounts (800-804)', async () => {
    const onClose = vi.fn();
    const { unmount } = render(
      <ServerSearchOverlay track={TRACK} mode="add" onClose={onClose} onSelect={selectStub()} />,
    );
    await waitFor(() => expect(document.querySelectorAll('.server-search-result')).toHaveLength(2));
    unmount();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('fades in on a later frame (811)', async () => {
    renderOverlay();
    await waitFor(() =>
      expect(document.querySelector('#server-search-overlay')?.className).toBe(
        'server-search-overlay visible',
      ),
    );
  });

  it('a Select reports the id as a STRING and resolves the pick lazily (863, 946)', async () => {
    const onSelect = selectStub();
    renderOverlay({ onSelect });
    await waitFor(() => expect(document.querySelectorAll('.server-search-result')).toHaveLength(2));
    fireEvent.click(document.querySelectorAll('.server-search-result')[0]);

    await waitFor(() => expect(onSelect).toHaveBeenCalled());
    // The row id is a number over the wire; every consumer wants the string.
    expect(onSelect.mock.calls[0][0]).toBe('42');
    const resolve = onSelect.mock.calls[0][1];
    expect(resolve()?.title).toBe('Nights');
  });

  it('the resolver reads the CURRENT results, so a later search changes it (946)', async () => {
    const onSelect = selectStub();
    renderOverlay({ onSelect });
    await waitFor(() => expect(document.querySelectorAll('.server-search-result')).toHaveLength(2));
    fireEvent.click(document.querySelectorAll('.server-search-result')[0]);
    await waitFor(() => expect(onSelect).toHaveBeenCalled());
    const resolve = onSelect.mock.calls[0][1];

    // A second search replaces the list the pick is looked up in — the race the
    // vanilla's full-reload fallback exists for.
    searchPayload = { success: true, tracks: [{ id: 99, title: 'Something Else' }] };
    fireEvent.keyDown(document.querySelector('#server-search-input') as Element, { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('Something Else')).toBeInTheDocument());
    expect(resolve()).toBeUndefined();
  });

  it('disables the picked row while the write runs, and restores it on refusal (891, 974)', async () => {
    let allow = () => {};
    const held = new Promise<void>((resolve) => {
      allow = resolve;
    });
    const onSelect = selectStub(async () => {
      await held;
      return false;
    });
    renderOverlay({ onSelect });
    await waitFor(() => expect(document.querySelectorAll('.server-search-result')).toHaveLength(2));
    fireEvent.click(document.querySelectorAll('.server-search-result')[0]);

    const buttons = () =>
      [...document.querySelectorAll('.server-search-select-btn')] as HTMLButtonElement[];
    await waitFor(() => expect(buttons()[0].disabled).toBe(true));
    expect(buttons()[0].textContent).toBe('...');
    // Only the clicked row is disabled.
    expect(buttons()[1].disabled).toBe(false);

    allow();
    await waitFor(() => expect(buttons()[0].disabled).toBe(false));
    expect(buttons()[0].textContent).toBe('Select');
  });
});
