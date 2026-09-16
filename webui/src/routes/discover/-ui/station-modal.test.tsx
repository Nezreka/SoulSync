import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { StationSnapshot } from '../-discover.stations';

import { StationModal } from './station-modal';

/**
 * The station preview dialog.
 *
 * It is the surface the whole S01/S02 request hangs on: something you can look
 * at, select from, download and sync — separately from starting radio.
 */

afterEach(cleanup);

const snapshot = (over: Partial<StationSnapshot> = {}): StationSnapshot => ({
  snapshot_id: '7-r1',
  revision: 1,
  station: { artist_id: 7, name: 'Daft Punk', image_url: '' },
  tracks: [
    {
      id: '1',
      track_id: '1',
      track_name: 'Aerodynamic',
      artist_name: 'Daft Punk',
      album_name: 'Discovery',
      duration_ms: 212000,
      available: true,
    },
    {
      id: '2',
      track_id: '2',
      track_name: 'Digital Love',
      artist_name: 'Daft Punk',
      album_name: 'Discovery',
      duration_ms: 301000,
      available: true,
    },
  ],
  counts: { returned: 2, available: 2, unavailable: 0 },
  actions: ['play', 'download', 'sync'],
  status: 'ok',
  ...over,
});

function renderModal(over: Partial<React.ComponentProps<typeof StationModal>> = {}) {
  const props = {
    snapshot: snapshot(),
    stationName: 'Daft Punk',
    selected: [] as number[],
    onClose: vi.fn(),
    onRefresh: vi.fn(),
    onToggleTrack: vi.fn(),
    onPlayTrack: vi.fn(),
    onSelectAll: vi.fn(),
    onClearSelection: vi.fn(),
    onPlaySelected: vi.fn(),
    onDownloadSelected: vi.fn(),
    onSyncSelected: vi.fn(),
    ...over,
  };
  return { ...render(<StationModal {...props} />), props };
}

describe('StationModal', () => {
  it('is a real dialog with an accessible name', () => {
    const { container } = renderModal();
    const dialog = container.querySelector('[role="dialog"]')!;
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    const labelledBy = dialog.getAttribute('aria-labelledby')!;
    expect(document.getElementById(labelledBy)!.textContent).toBe('Daft Punk Station');
  });

  it('closes on Escape', () => {
    const { props } = renderModal();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(props.onClose).toHaveBeenCalled();
  });

  it('states the FINITE scope, not endless radio', () => {
    renderModal();
    expect(screen.getByText(/2 tracks from your library/)).toBeInTheDocument();
    expect(screen.queryByText(/endless/i)).toBeNull();
  });

  it('labels the sync with the count it will actually sync', () => {
    renderModal({ selected: [0, 1] });
    expect(screen.getByText('Sync these 2')).toBeInTheDocument();
  });

  it('offers play, download and sync on the SELECTION', () => {
    const { props } = renderModal({ selected: [0] });
    fireEvent.click(screen.getByText('▶ Play selected'));
    fireEvent.click(screen.getByText('Download selected'));
    fireEvent.click(screen.getByText('Sync selected (1)'));
    expect(props.onPlaySelected).toHaveBeenCalled();
    expect(props.onDownloadSelected).toHaveBeenCalled();
    expect(props.onSyncSelected).toHaveBeenCalled();
  });

  it('disables every action with nothing selected', () => {
    renderModal({ selected: [] });
    expect(screen.getByText('▶ Play selected')).toBeDisabled();
    expect(screen.getByText('Download selected')).toBeDisabled();
    expect(screen.getByText('Sync selected (0)')).toBeDisabled();
  });

  it('tells the truth about an all-owned selection', () => {
    // station tracks are library rows, so "nothing to acquire" is a legitimate
    // answer and queueing redundant downloads would be worse
    renderModal({ selected: [0, 1] });
    expect(screen.getByText('Everything selected is already in your library.')).toBeInTheDocument();
  });

  it('counts tracks the library references but cannot find on disk', () => {
    renderModal({
      snapshot: snapshot({ counts: { returned: 2, available: 1, unavailable: 1 } }),
    });
    expect(screen.getByText(/1 of 2 are referenced by the library/)).toBeInTheDocument();
  });

  it('refresh is explicit — a preview never swaps itself out', () => {
    const { props } = renderModal();
    fireEvent.click(screen.getByText('Refresh'));
    expect(props.onRefresh).toHaveBeenCalled();
  });

  it('shows an honest reason for an unavailable station', () => {
    renderModal({
      snapshot: snapshot({
        tracks: [],
        status: 'unavailable',
        message: 'No playable Daft Punk tracks in your library.',
        counts: { returned: 0, available: 0, unavailable: 0 },
      }),
    });
    expect(screen.getByText('No playable Daft Punk tracks in your library.')).toBeInTheDocument();
    // and no selection bar over an empty list
    expect(screen.queryByText('Select all')).toBeNull();
  });

  it('surfaces a load failure inside the dialog', () => {
    renderModal({ snapshot: null, error: 'no playable tracks' });
    expect(screen.getByRole('alert')).toHaveTextContent('no playable tracks');
  });

  it('shows a loading state before the first snapshot', () => {
    renderModal({ snapshot: null, loading: true });
    expect(screen.getByText('Building this station…')).toBeInTheDocument();
  });

  it('blocks a second sync while one is running', () => {
    renderModal({ selected: [0], syncing: true, syncStatusBase: 'station-7-r1' });
    expect(screen.getByText('Sync selected (1)')).toBeDisabled();
  });
});

it('shows available rows and a retry explanation for partial snapshots', () => {
  renderModal({
    snapshot: snapshot({
      status: 'partial',
      message: 'Could not load the full station. Refresh to retry.',
    }),
  });
  expect(screen.getByText('Aerodynamic')).toBeInTheDocument();
  expect(screen.getByText(/Could not load the full station/)).toBeInTheDocument();
});
