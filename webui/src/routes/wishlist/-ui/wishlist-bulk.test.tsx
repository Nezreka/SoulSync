/** Bulk queue actions in the list view — selection, bulk bar, per-row outcomes. */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type {
  ParsedWishlistTrack,
  WishlistArtistGroup,
  WishlistBulkAction,
  WishlistBulkResponse,
} from '../-wishlist.types';

import { openWishlistInspector } from '../../../features/downloads/inspector-modal';
import { WishlistList } from './wishlist-list';

vi.mock('../../../features/downloads/inspector-modal', () => ({
  openWishlistInspector: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.mocked(openWishlistInspector).mockClear();
});

function track(over: Partial<ParsedWishlistTrack>): ParsedWishlistTrack {
  return {
    track: 'Xtal',
    artist: 'Aphex Twin',
    album: 'SAW 85-92',
    image: '',
    type: 'single',
    id: 't1',
    retry: 0,
    failing: false,
    lastTried: '',
    failReason: '',
    ...over,
  };
}

function group(name: string, tracks: ParsedWishlistTrack[]): WishlistArtistGroup {
  return { name, albums: [], singles: tracks, total: tracks.length, failingCount: 0 };
}

const GROUPS: WishlistArtistGroup[] = [
  group('Aphex Twin', [
    track({ id: 't1', track: 'Xtal' }),
    track({ id: 't2', track: 'Pulsewidth' }),
  ]),
];

const PROPS = {
  groups: GROUPS,
  artistImages: new Map<string, string>(),
  onRemoveAlbum: () => {},
  onRemoveTrack: () => {},
};

describe('WishlistList bulk actions', () => {
  it('shows the checkbox column and bulk bar only when onBulkAction is present', () => {
    render(<WishlistList {...PROPS} />);
    fireEvent.click(screen.getByText('Expand all'));
    expect(screen.queryByTestId('wl-bulkbar')).toBeNull();
    expect(screen.queryByTestId('wl-select-t1')).toBeNull();

    cleanup();
    render(<WishlistList {...PROPS} onBulkAction={async () => ({ success: true })} />);
    fireEvent.click(screen.getByText('Expand all'));
    expect(screen.getByTestId('wl-bulkbar')).toBeInTheDocument();
    expect(screen.getByTestId('wl-select-t1')).toBeInTheDocument();
  });

  it('grabs the selected tracks and shows per-row outcomes', async () => {
    const onBulkAction = vi.fn(
      async (action: WishlistBulkAction, ids: string[]): Promise<WishlistBulkResponse> => ({
        success: true,
        batch_id: 'batch-1',
        results: ids.map((id) => ({ id, ok: true, message: 'Queued for download' })),
      }),
    );
    render(<WishlistList {...PROPS} onBulkAction={onBulkAction} />);
    fireEvent.click(screen.getByText('Expand all'));

    fireEvent.click(screen.getByTestId('wl-select-t1'));
    fireEvent.click(screen.getByTestId('wl-select-t2'));
    expect(screen.getByTestId('wl-bulkbar-count')).toHaveTextContent('2 selected');

    fireEvent.click(screen.getByText('Grab'));
    await waitFor(() => {
      expect(onBulkAction).toHaveBeenCalledWith('grab', ['t1', 't2']);
    });
    const oks = await screen.findAllByText('✓ Queued for download');
    expect(oks).toHaveLength(2);
  });

  it('shows a ✗ outcome for the failed item on partial failure', async () => {
    const onBulkAction = vi.fn(
      async (): Promise<WishlistBulkResponse> => ({
        success: false,
        results: [
          { id: 't1', ok: true, message: 'Skipped' },
          { id: 't2', ok: false, message: 'Not in wishlist' },
        ],
      }),
    );
    render(<WishlistList {...PROPS} onBulkAction={onBulkAction} />);
    fireEvent.click(screen.getByText('Expand all'));
    fireEvent.click(screen.getByTestId('wl-select-t1'));
    fireEvent.click(screen.getByTestId('wl-select-t2'));

    fireEvent.click(screen.getByText('Skip'));
    await waitFor(() => {
      expect(onBulkAction).toHaveBeenCalledWith('skip', ['t1', 't2']);
    });
    expect(await screen.findByText('✓ Skipped')).toBeInTheDocument();
    const fail = await screen.findByText('✗ Not in wishlist');
    expect(fail.title).toBe('Not in wishlist');
  });

  it('clears the selection without calling the action', () => {
    const onBulkAction = vi.fn(async () => ({ success: true }));
    render(<WishlistList {...PROPS} onBulkAction={onBulkAction} />);
    fireEvent.click(screen.getByText('Expand all'));
    fireEvent.click(screen.getByTestId('wl-select-t1'));
    expect(screen.getByTestId('wl-bulkbar-count')).toHaveTextContent('1 selected');

    fireEvent.click(screen.getByText('Clear'));
    expect(screen.getByTestId('wl-bulkbar-count')).toHaveTextContent('0 selected');
    expect(onBulkAction).not.toHaveBeenCalled();
  });
});
