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

  it('wires the five actions, and Delete CLOSES first (1148)', () => {
    const h = renderModal({ name: 'A', source: 'spotify', tracks: [] });
    fireEvent.click(screen.getByText('Discover'));
    expect(h.onDiscover).toHaveBeenCalled();
    fireEvent.click(screen.getByText('Edit Source'));
    expect(h.onEditSource).toHaveBeenCalled();
    fireEvent.click(screen.getByText('Auto-Sync'));
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
