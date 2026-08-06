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

let payload: unknown = {};

function stubFetch(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(payload))),
  );
}

beforeEach(() => {
  payload = {
    success: true,
    server_type: 'plex',
    server_track_count: 2,
    source_track_count: 2,
    tracks: TRACKS,
  };
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
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
    const onFindAndAdd = vi.fn();
    renderEditor({ onFindAndAdd });
    await waitFor(() => expect(screen.getByText('No source track')).toBeInTheDocument());

    // 521-528: the extra's source side has no handler at all.
    expect(document.querySelector('.extra-gap')).not.toBeNull();
    fireEvent.click(document.querySelector('.extra-gap') as Element);
    expect(onFindAndAdd).not.toHaveBeenCalled();

    // 567-577: the missing row's whole slot IS the button.
    expect(screen.getByText('Find & add')).toBeInTheDocument();
    expect(screen.getByText('Frank Ocean — Nights')).toBeInTheDocument();
    fireEvent.click(document.querySelector('.empty-slot-wrap') as Element);
    expect(onFindAndAdd).toHaveBeenCalledWith(1);
  });

  it('offers Swap on a matched row only, and Remove on both (555-560)', async () => {
    const onSwap = vi.fn();
    const onRemove = vi.fn();
    renderEditor({ onSwap, onRemove });
    await waitFor(() => expect(screen.getAllByText('Alright').length).toBeGreaterThan(0));

    expect(document.querySelectorAll('.server-track-swap-btn')).toHaveLength(1);
    expect(document.querySelectorAll('.server-track-remove-btn')).toHaveLength(2);

    fireEvent.click(document.querySelector('.server-track-swap-btn') as Element);
    expect(onSwap).toHaveBeenCalledWith(0);
    fireEvent.click(document.querySelectorAll('.server-track-remove-btn')[0]);
    expect(onRemove).toHaveBeenCalledWith(0, 's1');
  });

  it('an action button never selects the row behind it (555, 558)', async () => {
    renderEditor({ onSwap: vi.fn() });
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

    const onShowOrder = vi.fn();
    payload = {
      success: true,
      server_type: 'plex',
      server_track_count: 2,
      order_status: { out_of_order: true },
      tracks: TRACKS,
    };
    render(
      <ServerCompareEditor
        playlist={PLAYLIST}
        mirrored={MIRRORED}
        onBack={vi.fn()}
        onShowOrder={onShowOrder}
      />,
    );
    await waitFor(() => expect(document.querySelector('.server-order-badge')).not.toBeNull());
    const badge = document.querySelector('.server-order-badge') as HTMLElement;
    expect(badge.textContent).toBe('⚠ out of order');
    expect(badge.title).toContain('different order on Plex');
    fireEvent.click(badge);
    expect(onShowOrder).toHaveBeenCalled();
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
