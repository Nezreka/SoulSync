/**
 * The tracks detail modal, transcribed from openMirroredPlaylistModal
 * (stats-automations.js 1120-1157). Chrome and copy are pinned as literals;
 * the numeric helpers have their own differential coverage in
 * -sync.mirrored.test.ts.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { MirroredDetailModal } from './mirrored-detail-modal';

const NOW = Date.UTC(2026, 7, 5, 12, 0, 0);

function renderModal(data: Parameters<typeof MirroredDetailModal>[0]['data'], on = {}) {
  const handlers = {
    onClose: vi.fn(),
    onDelete: vi.fn(),
    onEditSource: vi.fn(),
    onRunPipeline: vi.fn(),
    onDiscover: vi.fn(),
    ...on,
  };
  render(<MirroredDetailModal playlistId={3} data={data} now={NOW} {...handlers} />);
  return handlers;
}

describe('MirroredDetailModal', () => {
  it('renders the hero, the meta line and the track rows (1120-1146)', () => {
    renderModal({
      name: 'Road Trip',
      source: 'spotify',
      owner: 'boulder',
      tracks: [
        {
          position: 1,
          track_name: 'Alright',
          artist_name: 'Kendrick Lamar',
          album_name: 'TPAB',
          duration_ms: 219000,
        },
      ],
    });
    expect(document.querySelector('#mirrored-track-modal')).not.toBeNull();
    expect(screen.getByText('Mirrored Playlist')).toBeInTheDocument();
    expect(screen.getByText('Road Trip')).toBeInTheDocument();
    expect(screen.getByText('Spotify')).toBeInTheDocument();
    expect(screen.getByText('boulder')).toBeInTheDocument();
    expect(screen.getByText('1 tracks')).toBeInTheDocument();
    expect(screen.getByText('Alright')).toBeInTheDocument();
    expect(screen.getByText('3:39')).toBeInTheDocument();
    // The column head, exactly as at 1143.
    expect(document.querySelector('.mm-col-dur')?.textContent).toBe('Time');
  });

  it("#1613: says what the source couldn't load", () => {
    renderModal({
      name: "Cam's playlist",
      source: 'tidal',
      source_skipped: { videos: 1, unavailable: 29 },
      tracks: [],
    });
    expect(document.querySelector('.mm-skipped-note')?.textContent).toBe(
      "29 tracks couldn't be loaded from Tidal (removed or not available in your region) · 1 video skipped",
    );
  });

  it('#1613: no note when nothing was skipped', () => {
    renderModal({ name: 'Whole', source: 'tidal', source_skipped: null, tracks: [] });
    expect(document.querySelector('.mm-skipped-note')).toBeNull();
  });

  it('shows the empty line rather than an empty list (1145)', () => {
    renderModal({ name: 'Empty', source: 'spotify', tracks: [] });
    expect(screen.getByText('No tracks in this mirror yet.')).toBeInTheDocument();
    expect(screen.getByText('0 tracks')).toBeInTheDocument();
  });

  it('an unknown source keeps its raw name and the clipboard tile (1088-1089)', () => {
    renderModal({ name: 'Odd', source: 'navidrome', tracks: [] });
    expect(screen.getByText('navidrome')).toBeInTheDocument();
    expect(document.querySelector('.mm-cover-empty')?.textContent).toBe('📋');
  });

  it('uses the hero cover when there is art, and the tile when there is not', () => {
    const { unmount } = render(
      <MirroredDetailModal
        playlistId={3}
        data={{ name: 'A', source: 'tidal', image_url: 'http://cover', tracks: [] }}
        now={NOW}
        onClose={vi.fn()}
        onDelete={vi.fn()}
        onEditSource={vi.fn()}
        onRunPipeline={vi.fn()}
        onDiscover={vi.fn()}
      />,
    );
    expect(document.querySelector('.mm-cover-empty')).toBeNull();
    expect(document.querySelector('.mm-hero-bg')).not.toBeNull();
    unmount();
    renderModal({ name: 'A', source: 'tidal', tracks: [] });
    expect(document.querySelector('.mm-cover-empty')).not.toBeNull();
    expect(document.querySelector('.mm-hero-bg')).toBeNull();
  });

  it('omits the runtime segment entirely when nothing has a duration (1133)', () => {
    renderModal({ name: 'A', source: 'spotify', tracks: [{ track_name: 'x' }] });
    expect(screen.queryByText('0 min')).toBeNull();
  });

  it('Refresh from {source} shows when wired and calls its handler (#1413)', () => {
    const onRefreshFromSource = vi.fn();
    renderModal({ name: 'A', source: 'youtube', tracks: [] }, { onRefreshFromSource });
    // #1289: the button names the source now ("Refresh from YouTube").
    const btn = screen.getByText('Refresh from YouTube');
    expect(btn.getAttribute('title')).toMatch(/nothing is pushed to your server or downloaded/);
    fireEvent.click(btn);
    expect(onRefreshFromSource).toHaveBeenCalled();
  });

  it('no handler, no refresh button', () => {
    renderModal({ name: 'A', source: 'youtube', tracks: [] });
    expect(screen.queryByText('Refresh from YouTube')).toBeNull();
  });

  it('labels the buttons by direction with a one-line explainer (#1289)', () => {
    renderModal({ name: 'A', source: 'spotify', tracks: [] }, { onRefreshFromSource: vi.fn() });
    expect(screen.getByText('Refresh from Spotify')).toBeTruthy();
    expect(screen.getByText('Pull only — nothing pushed or downloaded')).toBeTruthy();
    expect(screen.getByText('Sync & download')).toBeTruthy();
    expect(screen.getByText("Push the playlist and download what's missing")).toBeTruthy();
  });

  it('wires the five actions, and Delete CLOSES first (1148)', () => {
    const h = renderModal({ name: 'A', source: 'spotify', tracks: [] });
    fireEvent.click(screen.getByText('Identify'));
    expect(h.onDiscover).toHaveBeenCalled();
    fireEvent.click(screen.getByText('Edit Source'));
    expect(h.onEditSource).toHaveBeenCalled();
    fireEvent.click(screen.getByText('Sync & download'));
    expect(h.onRunPipeline).toHaveBeenCalled();
    fireEvent.click(screen.getByText('Close'));
    expect(h.onClose).toHaveBeenCalled();

    h.onClose.mockClear();
    fireEvent.click(screen.getByText('Delete Mirror'));
    expect(h.onClose).toHaveBeenCalled();
    expect(h.onDelete).toHaveBeenCalled();
  });

  it('closes on the backdrop but NOT on the panel (1159)', () => {
    const h = renderModal({ name: 'A', source: 'spotify', tracks: [] });
    fireEvent.click(document.querySelector('.mirrored-modal') as Element);
    expect(h.onClose).not.toHaveBeenCalled();
    fireEvent.click(document.querySelector('#mirrored-track-modal') as Element);
    expect(h.onClose).toHaveBeenCalled();
  });
});

describe('MirroredDetailModal — a user playlist', () => {
  const MINE = {
    name: 'Late night',
    source: 'soulsync',
    tracks: [
      { position: 1, track_name: 'One', artist_name: 'A' },
      { position: 2, track_name: 'Two', artist_name: 'B' },
      { position: 3, track_name: 'Three', artist_name: 'C' },
    ],
  };

  it('reads as yours: no source pill, no Edit Source, no refresh', () => {
    renderModal(MINE, {
      onRefreshFromSource: vi.fn(),
      onRemoveTrack: vi.fn(),
      onReorder: vi.fn(),
    });
    expect(screen.getByText('Your Playlist')).toBeInTheDocument();
    expect(screen.queryByText('SoulSync')).toBeNull();
    expect(screen.queryByText('Edit Source')).toBeNull();
    expect(screen.queryByText(/^Refresh from/)).toBeNull();
    expect(screen.getByText('Delete playlist')).toBeInTheDocument();
    expect(screen.getByText('Sync & download')).toBeInTheDocument();
  });

  it('removes by position, and locks the other rows until the refetch lands', () => {
    const onRemoveTrack = vi.fn();
    renderModal(MINE, { onRemoveTrack, onReorder: vi.fn() });
    fireEvent.click(screen.getByLabelText('Remove Two from this playlist'));
    expect(onRemoveTrack).toHaveBeenCalledWith(2);
    // a stale position could hit the wrong track
    expect(
      (screen.getByLabelText('Remove One from this playlist') as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('drag and drop hands over the whole new order', () => {
    const onReorder = vi.fn();
    renderModal(MINE, { onRemoveTrack: vi.fn(), onReorder });
    const rows = document.querySelectorAll('.mm-row');
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
    fireEvent.dragStart(rows[2], { dataTransfer });
    fireEvent.dragOver(rows[0], { dataTransfer });
    fireEvent.drop(rows[0], { dataTransfer });
    expect(onReorder).toHaveBeenCalledWith([3, 1, 2]);
  });

  it('dropping a row on itself does nothing', () => {
    const onReorder = vi.fn();
    renderModal(MINE, { onRemoveTrack: vi.fn(), onReorder });
    const row = document.querySelectorAll('.mm-row')[1];
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
    fireEvent.dragStart(row, { dataTransfer });
    fireEvent.drop(row, { dataTransfer });
    expect(onReorder).not.toHaveBeenCalled();
  });

  it('a synced mirror stays read-only even if edit handlers are passed', () => {
    renderModal({ ...MINE, source: 'spotify' }, { onRemoveTrack: vi.fn(), onReorder: vi.fn() });
    expect(document.querySelector('.mm-row-remove')).toBeNull();
    expect(document.querySelector('.mm-row')?.getAttribute('draggable')).toBeNull();
  });

  it('an empty one says how to fill it', () => {
    renderModal({ ...MINE, tracks: [] }, { onRemoveTrack: vi.fn(), onReorder: vi.fn() });
    expect(
      screen.getByText('Nothing in here yet. Add songs with the + on any track.'),
    ).toBeInTheDocument();
  });
});

describe('MirroredDetailModal — add to playlist', () => {
  it('every row of a synced mirror can be copied onto your playlist', () => {
    renderModal({
      name: 'Release Radar',
      source: 'spotify',
      tracks: [
        { position: 1, track_name: 'One', artist_name: 'A' },
        { position: 2, track_name: 'Two', artist_name: 'B' },
      ],
    });
    expect(screen.getByLabelText('Add One to a playlist')).toHaveClass('mm-row-add');
    expect(screen.getByLabelText('Add Two to a playlist')).toBeInTheDocument();
    // still read-only: copying is not editing
    expect(document.querySelector('.mm-row-remove')).toBeNull();
  });

  it('your own playlist gets it beside remove, so a song can go to another list', () => {
    renderModal(
      {
        name: 'Mine',
        source: 'soulsync',
        tracks: [{ position: 1, track_name: 'One', artist_name: 'A' }],
      },
      { onRemoveTrack: vi.fn(), onReorder: vi.fn() },
    );
    expect(screen.getByLabelText('Add One to a playlist')).toBeInTheDocument();
    expect(screen.getByLabelText('Remove One from this playlist')).toBeInTheDocument();
  });
});
