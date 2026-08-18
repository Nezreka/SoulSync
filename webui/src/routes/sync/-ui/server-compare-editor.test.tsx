/**
 * The compare editor, against pages-extra.js 247-628. The row markup is the
 * CSS contract for this region, so ids, classes and data attributes are
 * asserted as literals.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ServerCompareEditor } from './server-compare-editor';

const PLAYLIST = { id: '7', name: 'Road Trip', track_count: 3 };
const MIRRORED = { id: 9, name: 'Road Trip', source: 'tidal' };

const TRACKS = [
  {
    match_status: 'matched',
    confidence: 0.95,
    source_track: { position: 1, name: 'Alright', artist: 'Kendrick', duration_ms: 219000 },
    server_track: { id: 's1', title: 'Alright', artist: 'Kendrick', duration: 219000 },
  },
  {
    match_status: 'missing',
    source_track: { position: 2, name: 'Nights', artist: 'Frank Ocean' },
    server_track: null,
  },
  {
    match_status: 'extra',
    source_track: null,
    server_track: { id: 's3', title: 'Bonus', artist: 'Someone' },
  },
];

/** The out-of-order variant, which is what unlocks the order modal. */
const ORDERED_PAYLOAD = {
  success: true,
  server_type: 'plex',
  server_track_count: 2,
  order_status: { out_of_order: true },
  server_order: [
    { title: 'Bonus', artist: 'Someone' },
    { title: 'Alright', artist: 'Kendrick' },
  ],
  tracks: TRACKS,
};

let payload: unknown = {};
let searchPayload: unknown = {};
let writePayload: unknown = {};
let calls: { url: string; body: unknown }[] = [];

/**
 * One stub for four endpoints. Routing by URL matters here: slice C's overlay
 * hits /api/library/search-tracks while the editor behind it is still holding
 * the compare payload, and the two shapes are nothing alike.
 */
function stubFetch(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(init.body as string) : undefined });
      if (url.includes('/api/library/search-tracks')) {
        return new Response(JSON.stringify(searchPayload));
      }
      if (init?.method === 'POST') return new Response(JSON.stringify(writePayload));
      return new Response(JSON.stringify(payload));
    }),
  );
}

/**
 * jsdom implements no scrollIntoView, and the pair-click handler calls it. Left
 * undefined it throws inside a React event handler, which React rethrows
 * asynchronously — the assertions still pass while the behaviour under test
 * never runs. Defined here so the call can be asserted, and deleted afterwards
 * so it cannot leak into another test file.
 */
const scrollIntoView = vi.fn();

beforeEach(() => {
  scrollIntoView.mockClear();
  (Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = scrollIntoView;
  calls = [];
  payload = {
    success: true,
    server_type: 'plex',
    server_track_count: 2,
    source_track_count: 2,
    tracks: TRACKS,
  };
  searchPayload = {
    success: true,
    tracks: [
      {
        id: 42,
        title: 'Alright (Remaster)',
        artist_name: 'Kendrick Lamar',
        album_title: 'TPAB',
        file_path: '/music/a.flac',
        bitrate: 1411,
        duration: 220000,
        album_thumb_url: 'http://art/1.jpg',
      },
    ],
  };
  writePayload = { success: true, message: 'Track added' };
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});

function renderEditor(props = {}) {
  return render(
    <ServerCompareEditor playlist={PLAYLIST} mirrored={MIRRORED} onBack={vi.fn()} {...props} />,
  );
}

describe('ServerCompareEditor', () => {
  it('renders both columns paired by index, with the vanilla data attributes', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));

    const sourceRows = document.querySelectorAll('#server-col-source-scroll .server-track-item');
    const serverRows = document.querySelectorAll('#server-col-server-scroll .server-track-item');
    expect(sourceRows).toHaveLength(3);
    expect(serverRows).toHaveLength(3);
    // Both sides carry the SAME pair id at the same index — that is the pairing.
    expect(sourceRows[0].getAttribute('data-pair-id')).toBe('pair-0');
    expect(serverRows[0].getAttribute('data-pair-id')).toBe('pair-0');
    expect(sourceRows[1].getAttribute('data-status')).toBe('missing');
  });

  it('numbers the source column by POSITION and the server column ordinally', async () => {
    payload = {
      success: true,
      tracks: [
        {
          match_status: 'matched',
          source_track: { position: 12, name: 'A' },
          server_track: { id: 's1', title: 'A' },
        },
      ],
    };
    renderEditor();
    await waitFor(() => expect(screen.getAllByText('A').length).toBeGreaterThan(0));
    const nums = document.querySelectorAll('.server-track-num');
    expect(nums[0].textContent).toBe('12');
    expect(nums[1].textContent).toBe('1');
  });

  it('an extra has a static source slot; a missing has a clickable server slot', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getByText('No source track')).toBeInTheDocument());

    // 521-528: the extra's source side has no handler at all.
    expect(document.querySelector('.extra-gap')).not.toBeNull();
    fireEvent.click(document.querySelector('.extra-gap') as Element);
    expect(document.querySelector('#server-search-overlay')).toBeNull();

    // 567-577: the missing row's whole slot IS the button.
    expect(screen.getByText('Find & add')).toBeInTheDocument();
    expect(screen.getByText('Frank Ocean — Nights')).toBeInTheDocument();
    fireEvent.click(document.querySelector('.empty-slot-wrap') as Element);
    await waitFor(() => expect(screen.getByText('Add Track to Server')).toBeInTheDocument());
  });

  it('offers Swap on a matched row only, and Remove on both (555-560)', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));

    expect(document.querySelectorAll('.server-track-swap-btn')).toHaveLength(1);
    expect(document.querySelectorAll('.server-track-remove-btn')).toHaveLength(2);

    fireEvent.click(document.querySelector('.server-track-swap-btn') as Element);
    await waitFor(() => expect(screen.getByText('Swap Track')).toBeInTheDocument());
  });

  it('an action button never selects the row behind it (555, 558)', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(document.querySelector('.server-track-swap-btn') as Element);
    expect(document.querySelector('.server-track-item.highlighted')).toBeNull();
  });

  it('shows the confidence badge only for matched, banded by percent', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getByText('95%')).toBeInTheDocument());
    expect(document.querySelector('.server-track-conf')?.className).toBe('server-track-conf high');
    expect(document.querySelectorAll('.server-track-conf')).toHaveLength(1);
  });

  it('clicking a row highlights BOTH sides of that pair (619-621)', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(document.querySelectorAll('#server-col-source-scroll .server-track-item')[0]);
    const highlighted = document.querySelectorAll('.server-track-item.highlighted');
    expect(highlighted).toHaveLength(2);
    expect(highlighted[0].getAttribute('data-pair-id')).toBe('pair-0');
    expect(highlighted[1].getAttribute('data-pair-id')).toBe('pair-0');
    // 624-627: and the OTHER column is scrolled to the twin, centred.
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' });
    expect(scrollIntoView.mock.instances[0]).toBe(
      document.querySelector('#server-col-server-scroll [data-pair-id="pair-0"]'),
    );
  });

  it('the filter HIDES rows on both sides — it never drops them (715-721)', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByText('Missing (1)'));

    const sourceRows = document.querySelectorAll('#server-col-source-scroll .server-track-item');
    // Still three rows on each side — pairing by index depends on it.
    expect(sourceRows).toHaveLength(3);
    expect((sourceRows[0] as HTMLElement).style.display).toBe('none');
    expect((sourceRows[1] as HTMLElement).style.display).toBe('');
    expect((sourceRows[2] as HTMLElement).style.display).toBe('none');
    const serverRows = document.querySelectorAll('#server-col-server-scroll .server-track-item');
    expect((serverRows[1] as HTMLElement).style.display).toBe('');
  });

  it('paints the stats, the pills and the footer from one count (356-382)', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getByText('Matched')).toBeInTheDocument());
    expect(document.querySelector('.server-editor-stat-num.matched')?.textContent).toBe('1');
    expect(document.querySelector('.server-editor-stat-num.missing')?.textContent).toBe('1');
    expect(document.querySelector('.server-editor-stat-num.extra')?.textContent).toBe('1');
    expect(screen.getByText('All (3)')).toBeInTheDocument();
    expect(document.querySelector('#server-editor-footer')?.textContent).toBe(
      '1/2 matched · 1 extra on server',
    );
  });

  it('hides the Extra tile when there are none (366)', async () => {
    payload = { success: true, tracks: [TRACKS[0]] };
    renderEditor();
    await waitFor(() => expect(screen.getByText('Matched')).toBeInTheDocument());
    expect(document.querySelector('.server-editor-stat-num.extra')).toBeNull();
    expect(document.querySelector('#server-editor-footer')?.textContent).toBe('1/1 matched');
  });

  it('shows the out-of-order badge only when the backend flags it (330-339)', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getByText('Matched')).toBeInTheDocument());
    expect(document.querySelector('.server-order-badge')).toBeNull();

    payload = ORDERED_PAYLOAD;
    render(<ServerCompareEditor playlist={PLAYLIST} mirrored={MIRRORED} onBack={vi.fn()} />);
    await waitFor(() => expect(document.querySelector('.server-order-badge')).not.toBeNull());
    const badge = document.querySelector('.server-order-badge') as HTMLElement;
    expect(badge.textContent).toBe('⚠ out of order');
    expect(badge.title).toContain('different order on Plex');
    fireEvent.click(badge);
    await waitFor(() => expect(document.querySelector('#server-order-modal')).not.toBeNull());
  });

  it('shows the no-source banner only without a mirrored playlist (304-306)', async () => {
    const { unmount } = renderEditor();
    await waitFor(() => expect(screen.getByText('Matched')).toBeInTheDocument());
    expect(document.querySelector('#server-no-source-banner')).toBeNull();
    unmount();

    render(<ServerCompareEditor playlist={PLAYLIST} mirrored={null} onBack={vi.fn()} />);
    await waitFor(() => expect(document.querySelector('#server-no-source-banner')).not.toBeNull());
    // 312 + 323: no mirror means the generic label and the clipboard.
    expect(document.querySelector('#server-col-source-label')?.textContent).toBe('Source');
    expect(document.querySelector('#server-col-source-icon')?.textContent).toBe('📋');
  });

  it('reports a failed load in the meta line, not as a crash (284-286)', async () => {
    payload = { success: false, error: 'Playlist vanished' };
    renderEditor();
    await waitFor(() =>
      expect(document.querySelector('#server-editor-meta')?.textContent).toBe('Playlist vanished'),
    );
  });

  /* ── Slice C: the three writes ──────────────────────────────────────────── */

  async function openFindAndAdd() {
    renderEditor();
    await waitFor(() => expect(screen.getByText('Find & add')).toBeInTheDocument());
    fireEvent.click(document.querySelector('.empty-slot-wrap') as Element);
    await waitFor(() => expect(screen.getByText('Alright (Remaster)')).toBeInTheDocument());
  }

  it('Find & Add posts the server-side position and the durable-match fields (908-931)', async () => {
    await openFindAndAdd();
    fireEvent.click(document.querySelector('.server-search-result') as Element);

    await waitFor(() => expect(calls.some((c) => c.url.includes('/add-track'))).toBe(true));
    const add = calls.find((c) => c.url.includes('/add-track'));
    expect(add?.url).toBe('/api/server/playlist/7/add-track');
    expect(add?.body).toEqual({
      track_id: '42',
      playlist_name: 'Road Trip',
      // Row 1 is the missing one; exactly ONE row before it has a server
      // track, so the server-side position is 1 — not the row index.
      position: 1,
      source_track_id: '',
      source_title: 'Nights',
      source_artist: 'Frank Ocean',
      // The source track carries no provider, so the mirrored playlist's wins.
      source: 'tidal',
    });
  });

  it('patches the pair in place instead of re-fetching the playlist (#1005, 941-967)', async () => {
    await openFindAndAdd();
    const comparesBefore = calls.filter((c) => c.url.includes('/tracks?name=')).length;
    fireEvent.click(document.querySelector('.server-search-result') as Element);

    await waitFor(() => expect(document.querySelector('#server-search-overlay')).toBeNull());
    // The formerly-missing row now carries the picked track, at 100%.
    const serverRows = document.querySelectorAll('#server-col-server-scroll .server-track-item');
    expect(serverRows[1].getAttribute('data-status')).toBe('matched');
    expect(serverRows[1].textContent).toContain('Alright (Remaster)');
    expect(serverRows[1].textContent).toContain('100%');
    // …and no second compare fetch was made.
    expect(calls.filter((c) => c.url.includes('/tracks?name=')).length).toBe(comparesBefore);
  });

  it('drops the extra row the backend linked rather than duplicated (960-966)', async () => {
    payload = {
      success: true,
      tracks: [
        TRACKS[1],
        { match_status: 'extra', source_track: null, server_track: { id: '42', title: 'Bonus' } },
      ],
    };
    renderEditor();
    await waitFor(() => expect(screen.getByText('Find & add')).toBeInTheDocument());
    expect(document.querySelectorAll('#server-col-server-scroll .server-track-item')).toHaveLength(
      2,
    );

    fireEvent.click(document.querySelector('.empty-slot-wrap') as Element);
    await waitFor(() => expect(screen.getByText('Alright (Remaster)')).toBeInTheDocument());
    fireEvent.click(document.querySelector('.server-search-result') as Element);

    // The picked track already sat in the list as an extra; leaving it would
    // show the same track twice until the next full load.
    await waitFor(() =>
      expect(
        document.querySelectorAll('#server-col-server-scroll .server-track-item'),
      ).toHaveLength(1),
    );
    expect(screen.queryByText('Bonus')).not.toBeInTheDocument();
  });

  it('follows the playlist id when Plex recreates it (939)', async () => {
    writePayload = { success: true, message: 'Track added', new_playlist_id: '99' };
    vi.stubGlobal(
      'showConfirmDialog',
      vi.fn(async () => true),
    );
    await openFindAndAdd();
    fireEvent.click(document.querySelector('.server-search-result') as Element);
    await waitFor(() => expect(document.querySelector('#server-search-overlay')).toBeNull());

    // The NEXT write must go to the recreated playlist, not the dead one.
    fireEvent.click(document.querySelectorAll('.server-track-remove-btn')[0]);
    await waitFor(() => expect(calls.some((c) => c.url.includes('/remove-track'))).toBe(true));
    expect(calls.find((c) => c.url.includes('/remove-track'))?.url).toBe(
      '/api/server/playlist/99/remove-track',
    );
  });

  it('Swap posts the OLD server track id alongside the new one (896-904)', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(document.querySelector('.server-track-swap-btn') as Element);
    await waitFor(() => expect(screen.getByText('Alright (Remaster)')).toBeInTheDocument());
    fireEvent.click(document.querySelector('.server-search-result') as Element);

    await waitFor(() => expect(calls.some((c) => c.url.includes('/replace-track'))).toBe(true));
    expect(calls.find((c) => c.url.includes('/replace-track'))?.body).toEqual({
      old_track_id: 's1',
      new_track_id: '42',
      playlist_name: 'Road Trip',
      // #1159: the row's source identity rides along so the backend can
      // persist the correction; `source` falls back to the mirrored provider.
      source_track_id: '',
      source_title: 'Alright',
      source_artist: 'Kendrick',
      source: 'tidal',
    });
  });

  it('a rejected write keeps the overlay open and restores the button (972-975)', async () => {
    writePayload = { success: false, error: 'Plex said no' };
    const toast = vi.fn();
    vi.stubGlobal('showToast', toast);
    await openFindAndAdd();
    fireEvent.click(document.querySelector('.server-search-result') as Element);

    await waitFor(() => expect(toast).toHaveBeenCalledWith('Plex said no', 'error'));
    expect(document.querySelector('#server-search-overlay')).not.toBeNull();
    expect(
      (document.querySelector('.server-search-select-btn') as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('falls back to a full reload when the pick cannot be identified (946, 968-971)', async () => {
    // The pick is looked up AFTER the write returns, so a second search landing
    // while the write is in flight replaces the list it is looked up in. That
    // race is the only way the vanilla's fallback is ever reached.
    let releaseWrite = () => {};
    const held = new Promise<void>((resolve) => {
      releaseWrite = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, body: init?.body ? JSON.parse(init.body as string) : undefined });
        if (url.includes('/api/library/search-tracks')) {
          return new Response(JSON.stringify(searchPayload));
        }
        if (init?.method === 'POST') {
          await held;
          return new Response(JSON.stringify(writePayload));
        }
        return new Response(JSON.stringify(payload));
      }),
    );

    await openFindAndAdd();
    const comparesBefore = calls.filter((c) => c.url.includes('/tracks?name=')).length;
    fireEvent.click(document.querySelector('.server-search-result') as Element);
    await waitFor(() => expect(calls.some((c) => c.url.includes('/add-track'))).toBe(true));

    // A second search lands while the write is still open, and it holds a
    // different track entirely.
    searchPayload = { success: true, tracks: [{ id: 7, title: 'Something Else' }] };
    fireEvent.keyDown(document.querySelector('#server-search-input') as Element, {
      key: 'Enter',
    });
    await waitFor(() => expect(screen.getByText('Something Else')).toBeInTheDocument());

    releaseWrite();
    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/tracks?name=')).length).toBe(comparesBefore + 1),
    );
  });

  it('Remove confirms with the track title, then turns a matched row missing (988-1012)', async () => {
    const confirm = vi.fn(async () => true);
    const toast = vi.fn();
    vi.stubGlobal('showConfirmDialog', confirm);
    vi.stubGlobal('showToast', toast);
    writePayload = { success: true, message: 'Track removed' };

    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(document.querySelectorAll('.server-track-remove-btn')[0]);

    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalledWith({
      title: 'Remove Track',
      message: 'Remove "Alright" from this playlist?',
      confirmText: 'Remove',
      destructive: true,
    });

    // 1006-1009: the row KEEPS its source side and becomes missing, so the two
    // columns stay paired — three rows on each side, still.
    await waitFor(() =>
      expect(
        document
          .querySelectorAll('#server-col-server-scroll .server-track-item')[0]
          .getAttribute('data-status'),
      ).toBe('missing'),
    );
    const serverRows = document.querySelectorAll('#server-col-server-scroll .server-track-item');
    expect(serverRows).toHaveLength(3);
    expect(document.querySelectorAll('#server-col-source-scroll .server-track-item')).toHaveLength(
      3,
    );
    expect(screen.getAllByText('Find & add')).toHaveLength(2);
    expect(toast).toHaveBeenCalledWith('Track removed', 'success');
  });

  it('Remove drops an extra row entirely (1010-1011)', async () => {
    vi.stubGlobal(
      'showConfirmDialog',
      vi.fn(async () => true),
    );
    renderEditor();
    await waitFor(() => expect(screen.getByText('Bonus')).toBeInTheDocument());
    fireEvent.click(document.querySelectorAll('.server-track-remove-btn')[1]);
    await waitFor(() => expect(screen.queryByText('Bonus')).not.toBeInTheDocument());
    expect(document.querySelectorAll('#server-col-server-scroll .server-track-item')).toHaveLength(
      2,
    );
  });

  it('an id-less server row is not removable — no confirm, no write (983)', async () => {
    const confirm = vi.fn(async () => true);
    vi.stubGlobal('showConfirmDialog', confirm);
    payload = {
      success: true,
      tracks: [{ match_status: 'extra', source_track: null, server_track: { title: 'No id' } }],
    };
    renderEditor();
    await waitFor(() => expect(screen.getByText('No id')).toBeInTheDocument());
    fireEvent.click(document.querySelector('.server-track-remove-btn') as Element);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(confirm).not.toHaveBeenCalled();
    expect(calls.some((c) => c.url.includes('/remove-track'))).toBe(false);
  });

  it('keeps the current playlist id when a remove names no new one (1003)', async () => {
    vi.stubGlobal(
      'showConfirmDialog',
      vi.fn(async () => true),
    );
    // Neither Jellyfin nor Navidrome recreates the playlist, so the response
    // carries no new_playlist_id and ours must survive the write.
    writePayload = { success: true, message: 'Track removed' };
    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));

    fireEvent.click(document.querySelectorAll('.server-track-remove-btn')[0]);
    await waitFor(() => expect(calls.some((c) => c.url.includes('/remove-track'))).toBe(true));
    // The extra row is still removable, and still on playlist 7.
    fireEvent.click(document.querySelectorAll('.server-track-remove-btn')[0]);
    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/remove-track'))).toHaveLength(2),
    );
    for (const call of calls.filter((c) => c.url.includes('/remove-track'))) {
      expect(call.url).toBe('/api/server/playlist/7/remove-track');
    }
  });

  /* ── Slice D: the order view + align ────────────────────────────────────── */

  async function openOrderModal() {
    payload = ORDERED_PAYLOAD;
    renderEditor();
    await waitFor(() => expect(document.querySelector('.server-order-badge')).not.toBeNull());
    fireEvent.click(document.querySelector('.server-order-badge') as Element);
    await waitFor(() => expect(document.querySelector('#server-order-modal')).not.toBeNull());
  }

  it('the order modal lists the SERVER order, not the source order (394-406)', async () => {
    await openOrderModal();
    const rows = document.querySelectorAll('.server-order-row');
    expect(rows).toHaveLength(2);
    // The compare columns show Alright first; the server really has Bonus first,
    // and seeing that difference is the entire purpose of this view.
    expect(rows[0].querySelector('.server-order-title')?.textContent).toBe('Bonus');
    expect(rows[1].querySelector('.server-order-title')?.textContent).toBe('Alright');
    expect(rows[0].querySelector('.server-order-num')?.textContent).toBe('1');
  });

  it('Mirror source sends the matched ids in SOURCE order, dropping extras (417, 454-468)', async () => {
    await openOrderModal();
    fireEvent.click(screen.getByText('Mirror source'));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/align'))).toBe(true));

    const align = calls.find((c) => c.url.includes('/align'));
    expect(align?.url).toBe('/api/server/playlist/7/align');
    expect(align?.body).toEqual({
      playlist_name: 'Road Trip',
      // Only the matched row — the missing row has no server track to name and
      // the extra is governed by keep_extras instead.
      matched_ids: ['s1'],
      keep_extras: false,
    });
  });

  it('Keep extras sends the same ids with the flag flipped (421)', async () => {
    await openOrderModal();
    fireEvent.click(screen.getByText('Keep extras'));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/align'))).toBe(true));
    const align = calls.find((c) => c.url.includes('/align'));
    expect(align?.body).toEqual({
      playlist_name: 'Road Trip',
      matched_ids: ['s1'],
      keep_extras: true,
    });
  });

  it('a successful align closes the modal and RELOADS the comparison (472-475)', async () => {
    const toast = vi.fn();
    vi.stubGlobal('showToast', toast);
    writePayload = { success: true, track_count: 12 };
    await openOrderModal();
    const comparesBefore = calls.filter((c) => c.url.includes('/tracks?name=')).length;

    fireEvent.click(screen.getByText('Mirror source'));
    await waitFor(() => expect(document.querySelector('#server-order-modal')).toBeNull());
    expect(toast).toHaveBeenCalledWith('Playlist order aligned (12 tracks)', 'success');
    // A reorder invalidates order_status and the whole server column, so unlike
    // the row writes this one really does re-fetch.
    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/tracks?name=')).length).toBe(comparesBefore + 1),
    );
  });

  it('a failed align keeps the modal open so the user can retry (476-478)', async () => {
    const toast = vi.fn();
    vi.stubGlobal('showToast', toast);
    writePayload = { success: false, error: 'Playlist changed on the server' };
    await openOrderModal();
    fireEvent.click(screen.getByText('Mirror source'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Playlist changed on the server', 'error'),
    );
    expect(document.querySelector('#server-order-modal')).not.toBeNull();
  });

  it('warns rather than posting when there is nothing to align (457-459)', async () => {
    const toast = vi.fn();
    vi.stubGlobal('showToast', toast);
    payload = {
      ...ORDERED_PAYLOAD,
      // No matched row at all — an order-only rewrite has nothing to act on.
      tracks: [TRACKS[1], TRACKS[2]],
    };
    renderEditor();
    await waitFor(() => expect(document.querySelector('.server-order-badge')).not.toBeNull());
    fireEvent.click(document.querySelector('.server-order-badge') as Element);
    await waitFor(() => expect(screen.getByText('Mirror source')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Mirror source'));

    await waitFor(() => expect(toast).toHaveBeenCalledWith('Nothing to align', 'warning'));
    expect(calls.some((c) => c.url.includes('/align'))).toBe(false);
  });

  /* ── Slice E: M3U export ────────────────────────────────────────────────── */

  it('Export M3U sends the server tracks and downloads the file (642-690)', async () => {
    const toast = vi.fn();
    vi.stubGlobal('showToast', toast);
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    writePayload = { success: true, m3u_content: '#EXTM3U', stats: { found: 2 } };

    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByText('📋 Export M3U'));

    await waitFor(() => expect(calls.some((c) => c.url.includes('m3u'))).toBe(true));
    const body = calls.find((c) => c.url === '/api/generate-playlist-m3u')?.body as {
      tracks: unknown[];
    };
    // Matched + extra are on the server; the missing row is not.
    expect(body.tracks).toEqual([
      { name: 'Alright', artist: 'Kendrick', duration_ms: 219000 },
      { name: 'Bonus', artist: 'Someone', duration_ms: 0 },
    ]);
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Exported M3U: Road Trip (2 tracks)', 'success'),
    );
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });

  it('reports the shortfall when the library could not resolve every track (688-689)', async () => {
    const toast = vi.fn();
    vi.stubGlobal('showToast', toast);
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    writePayload = { success: true, m3u_content: '#EXTM3U', stats: { found: 1 } };

    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByText('📋 Export M3U'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Exported M3U: Road Trip (1/2 in library)', 'success'),
    );
    click.mockRestore();
  });

  it('reports a found count of ZERO rather than falling back to the total (688)', async () => {
    // The guard is `!= null`, not truthiness: a server that resolved NONE of
    // the tracks must read '0/2 in library', not a cheerful '2 tracks'.
    const toast = vi.fn();
    vi.stubGlobal('showToast', toast);
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    writePayload = { success: true, m3u_content: '', stats: { found: 0 } };

    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByText('📋 Export M3U'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Exported M3U: Road Trip (0/2 in library)', 'success'),
    );
    click.mockRestore();
  });

  it('shows the exporting state on the button while the write runs (657, 694)', async () => {
    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === 'POST') {
          await held;
          return new Response(JSON.stringify({ success: true, m3u_content: '' }));
        }
        return new Response(JSON.stringify(payload));
      }),
    );
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByText('📋 Export M3U'));

    await waitFor(() => expect(screen.getByText('⏳ Exporting…')).toBeInTheDocument());
    expect((screen.getByText('⏳ Exporting…') as HTMLButtonElement).disabled).toBe(true);

    release();
    await waitFor(() => expect(screen.getByText('📋 Export M3U')).toBeInTheDocument());
    expect((screen.getByText('📋 Export M3U') as HTMLButtonElement).disabled).toBe(false);
    click.mockRestore();
  });

  it('warns without exporting when nothing is on the server (652-655)', async () => {
    const toast = vi.fn();
    vi.stubGlobal('showToast', toast);
    payload = { success: true, tracks: [TRACKS[1]] };
    renderEditor();
    await waitFor(() => expect(screen.getByText('Find & add')).toBeInTheDocument());
    fireEvent.click(screen.getByText('📋 Export M3U'));

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('No server tracks to export', 'warning'),
    );
    expect(calls.some((c) => c.url.includes('m3u'))).toBe(false);
    // The guard runs before the button is touched, so it never shows the
    // exporting state.
    expect(screen.getByText('📋 Export M3U')).toBeInTheDocument();
  });

  it('restores the button after a failed export (691-695)', async () => {
    const toast = vi.fn();
    vi.stubGlobal('showToast', toast);
    writePayload = { success: false, error: 'no writer configured' };

    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByText('📋 Export M3U'));

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('M3U export failed: no writer configured', 'error'),
    );
    const button = screen.getByText('📋 Export M3U') as HTMLButtonElement;
    expect(button.disabled).toBe(false);
  });

  it('a declined confirm writes nothing (988)', async () => {
    vi.stubGlobal(
      'showConfirmDialog',
      vi.fn(async () => false),
    );
    renderEditor();
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(document.querySelectorAll('.server-track-remove-btn')[0]);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(calls.some((c) => c.url.includes('/remove-track'))).toBe(false);
  });

  it('starts on the loading copy in both columns (262-265)', () => {
    renderEditor();
    expect(document.querySelector('#server-editor-meta')?.textContent).toBe(
      'Loading comparison...',
    );
    expect(document.querySelectorAll('#server-col-source-scroll')[0].textContent).toBe(
      'Loading...',
    );
  });
});

// ── #1128: a saved manual match that can't pair here must SAY so ────────────
//
// The durable match points at a library track that isn't in this server
// playlist yet, so pass-0 can't apply it and the row renders as plain
// "missing" — identical to a track never matched. diegocade1 therefore
// re-matched the same track on every sync with nothing telling him it had
// already taken. The real sync DOES honour the match; only this preview was
// silent about it.

describe('#1128 manual-match note on missing rows', () => {
  const row = (extra = {}) => ({
    match_status: 'missing',
    source_track: {
      position: 1,
      name: "Wavin' Flag (Coca-Cola Spanish Celebration Mix)",
      artist: "K'naan",
      source_track_id: 'deezer-wavin',
    },
    server_track: null,
    ...extra,
  });

  it('shows the note when a manual match exists', async () => {
    payload = { success: true, tracks: [row({ has_manual_match: true })] };
    renderEditor();
    await waitFor(() => expect(screen.getByText(/Already matched/i)).toBeInTheDocument());
  });

  it('stays silent on a genuinely unmatched row', async () => {
    payload = { success: true, tracks: [row()] };
    renderEditor();
    await waitFor(() => expect(screen.getByText('Find & add')).toBeInTheDocument());
    expect(screen.queryByText(/Already matched/i)).toBeNull();
  });

  it('still offers Find & add — the note explains, it does not disable', async () => {
    payload = { success: true, tracks: [row({ has_manual_match: true })] };
    renderEditor();
    await waitFor(() => expect(screen.getByText('Find & add')).toBeInTheDocument());
  });
});

describe('Remove from Server (whole-playlist delete)', () => {
  it('confirms destructively, POSTs the delete, toasts, and closes via onBack', async () => {
    const confirm = vi.fn(async () => true);
    const toast = vi.fn();
    const onBack = vi.fn();
    vi.stubGlobal('showConfirmDialog', confirm);
    vi.stubGlobal('showToast', toast);
    writePayload = { success: true, message: 'Playlist deleted' };

    renderEditor({ onBack });
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(document.querySelector('#server-editor-delete-btn') as Element);

    await waitFor(() => expect(onBack).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Remove from Server', destructive: true }),
    );
    const del = calls.find((c) => c.url.includes('/delete'));
    expect(del?.url).toBe('/api/server/playlist/7/delete');
    // the name rides along so a stale id can be re-resolved server-side
    expect(del?.body).toEqual({ playlist_name: 'Road Trip' });
    expect(toast).toHaveBeenCalledWith('Deleted from server: Road Trip', 'success');
  });

  it('a declined confirm writes nothing and stays open', async () => {
    const onBack = vi.fn();
    vi.stubGlobal(
      'showConfirmDialog',
      vi.fn(async () => false),
    );
    renderEditor({ onBack });
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(document.querySelector('#server-editor-delete-btn') as Element);
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.url.includes('/delete'))).toBe(false);
    expect(onBack).not.toHaveBeenCalled();
  });

  it('a failed delete toasts the error and does NOT close the editor', async () => {
    const toast = vi.fn();
    const onBack = vi.fn();
    vi.stubGlobal(
      'showConfirmDialog',
      vi.fn(async () => true),
    );
    vi.stubGlobal('showToast', toast);
    writePayload = { success: false, error: 'Playlist not found' };

    renderEditor({ onBack });
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));
    fireEvent.click(document.querySelector('#server-editor-delete-btn') as Element);

    await waitFor(() => expect(toast).toHaveBeenCalledWith('Playlist not found', 'error'));
    expect(onBack).not.toHaveBeenCalled();
  });
});
