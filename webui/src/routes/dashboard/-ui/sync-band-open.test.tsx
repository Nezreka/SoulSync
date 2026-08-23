/**
 * A sync-band row must open something when you click it.
 *
 * Boulder, Aug 2026: Discover Weekly and two ListenBrainz playlists showed "no
 * runs yet" and did nothing when clicked, while Release Radar and Hot Hits USA
 * opened normally. The row opened only the sync-DETAIL modal, keyed on a
 * history entry, so a playlist whose latest run was not in the fetched window
 * was inert — no id, no click handler, no role="button".
 *
 * The schedule's board key IS the mirrored playlist id, so a scheduled row can
 * always fall back to opening its playlist.
 */

import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SyncBandRow } from '../-dash.syncband';

import { Row } from './sync-band';

const schedule = {
  key: '3',
  automationId: 12,
  name: 'Discover Weekly',
  sourceKey: 'spotify',
  source: 'Spotify',
  logo: null,
  imageUrl: null,
  cadence: 'Every 6 hours',
  enabled: true,
  nextRun: null,
  coverage: null,
  lastRun: null,
  running: null,
};

function row(over: Partial<SyncBandRow> = {}): SyncBandRow {
  return {
    rowKey: 's-3-12',
    kind: 'scheduled',
    name: 'Discover Weekly',
    schedule,
    last: null,
    coverage: null,
    thumbUrl: null,
    logo: null,
    sourceKey: 'spotify',
    sourceLabel: 'Spotify',
    ...over,
  } as SyncBandRow;
}

afterEach(() => {
  cleanup();
  delete window.openSyncDetailModal;
  delete window.openMirroredPlaylistModal;
});

describe('clicking a sync-band row', () => {
  it('opens the playlist when the schedule has no run in the window', () => {
    const openPlaylist = vi.fn();
    window.openMirroredPlaylistModal = openPlaylist;

    const { container } = render(
      <Row
        row={row()}
        busy={false}
        fading={false}
        live={null}
        onRun={vi.fn()}
        onSyncAgain={vi.fn()}
        onListen={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    const el = container.querySelector('.syncband-row')!;
    expect(el.getAttribute('role')).toBe('button');

    fireEvent.click(el);
    expect(openPlaylist).toHaveBeenCalledWith(3);
  });

  it('still prefers the run detail when there IS a run', () => {
    const openDetail = vi.fn();
    const openPlaylist = vi.fn();
    window.openSyncDetailModal = openDetail;
    window.openMirroredPlaylistModal = openPlaylist;

    const { container } = render(
      <Row
        row={row({ last: { id: 65 } as SyncBandRow['last'] })}
        busy={false}
        fading={false}
        live={null}
        onRun={vi.fn()}
        onSyncAgain={vi.fn()}
        onListen={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    fireEvent.click(container.querySelector('.syncband-row')!);

    expect(openDetail).toHaveBeenCalledWith(65);
    expect(openPlaylist).not.toHaveBeenCalled();
  });

  it('is inert for a manual row with neither a run nor a playlist', () => {
    const { container } = render(
      <Row
        row={row({ kind: 'manual', schedule: null })}
        busy={false}
        fading={false}
        live={null}
        onRun={vi.fn()}
        onSyncAgain={vi.fn()}
        onListen={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(container.querySelector('.syncband-row')!.getAttribute('role')).toBeNull();
  });

  it('uses live sync progress for manual/history rows', () => {
    const { container, getByText } = render(
      <Row
        row={row({ kind: 'manual', schedule: null, last: { id: 77 } as SyncBandRow['last'] })}
        busy={false}
        fading={false}
        live={{
          playlistId: 'history_77',
          playlistName: 'Discover Weekly',
          phase: 'Matching · Track 4',
          progress: 42,
          updatedAt: Date.now(),
        }}
        onRun={vi.fn()}
        onSyncAgain={vi.fn()}
        onListen={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(container.querySelector('.syncband-row--live')).not.toBeNull();
    expect(getByText('Matching · Track 4')).toBeTruthy();
    expect(getByText('42%')).toBeTruthy();
  });
});
