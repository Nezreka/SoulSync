import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { FindingsAlbumGrid, qualityRange, trackLabel } from './findings-album-grid';

/**
 * The upgrade backlog as albums, not as forty thousand rows.
 *
 * "An 'album' or 'artist' view would also be nice if I would like to fix an
 * album." - Lil-Uzi-Chimp, Aug 26 2026.
 */

const ARROW = '→';

const GROUP = {
  group_by: 'album' as const,
  key: 'Aphex Twin SAW',
  artist: 'Aphex Twin',
  album: 'SAW',
  count: 12,
  worst_score: 50.128,
  best_score: 50.32,
  worst_quality: 'MP3 128kbps',
  best_quality: 'MP3 320kbps',
  album_thumb_url: '/art/saw.jpg',
  artist_thumb_url: '/art/aphex.jpg',
  artist_id: 'a-1',
  first_seen: '2026-01-01',
  last_seen: '2026-01-02',
};

function stub(groups: unknown[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ groups }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    ),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('the quality range label', () => {
  it('shows a range only when the album is actually mixed', () => {
    expect(qualityRange(GROUP)).toBe(`MP3 128kbps ${ARROW} MP3 320kbps`);
  });

  it('says it once when every track is the same', () => {
    // "128 to 128" is noise dressed as information
    expect(qualityRange({ ...GROUP, best_quality: 'MP3 128kbps' })).toBe('MP3 128kbps');
  });

  it('degrades to whatever it has rather than showing an empty arrow', () => {
    expect(qualityRange({ ...GROUP, best_quality: '' })).toBe('MP3 128kbps');
    expect(qualityRange({ ...GROUP, worst_quality: '', best_quality: '' })).toBe('');
  });
});

describe('the track count label', () => {
  it('does not say "1 tracks"', () => {
    expect(trackLabel(1)).toBe('1 track');
    expect(trackLabel(12)).toBe('12 tracks');
  });
});

describe('the grid', () => {
  it('leads with the album, its artist, the count and the worst quality', async () => {
    stub([GROUP]);
    render(<FindingsAlbumGrid groupBy="album" onOpen={vi.fn()} />);

    await screen.findByText('SAW');
    expect(screen.getByText('Aphex Twin')).toBeTruthy();
    expect(screen.getByText('12')).toBeTruthy();
    expect(screen.getByText('12 tracks')).toBeTruthy();
    expect(screen.getByText(`MP3 128kbps ${ARROW} MP3 320kbps`)).toBeTruthy();
    expect(document.querySelector('img')?.getAttribute('src')).toBe('/art/saw.jpg');
  });

  it('falls back to the artist image, then to a letter, never a broken image', async () => {
    // a grid of missing-image icons reads as a broken page, not as "no art"
    stub([{ ...GROUP, album_thumb_url: null }]);
    const { unmount } = render(<FindingsAlbumGrid groupBy="album" onOpen={vi.fn()} />);
    await screen.findByText('SAW');
    expect(document.querySelector('img')?.getAttribute('src')).toBe('/art/aphex.jpg');
    unmount();

    stub([{ ...GROUP, album_thumb_url: null, artist_thumb_url: null }]);
    render(<FindingsAlbumGrid groupBy="album" onOpen={vi.fn()} />);
    await screen.findByText('SAW');
    expect(document.querySelector('img')).toBeNull();
    expect(screen.getByText('S')).toBeTruthy();
  });

  it('uses the artist image and hides the redundant artist line in artist view', async () => {
    stub([{ ...GROUP, group_by: 'artist' as const, album: null }]);
    render(<FindingsAlbumGrid groupBy="artist" onOpen={vi.fn()} />);

    await screen.findByText('Aphex Twin');
    expect(document.querySelector('img')?.getAttribute('src')).toBe('/art/aphex.jpg');
    // the name IS the artist here, so it must not be printed twice
    expect(screen.getAllByText('Aphex Twin')).toHaveLength(1);
  });

  it('hands the whole group back when a card is clicked', async () => {
    const onOpen = vi.fn();
    stub([GROUP]);
    render(<FindingsAlbumGrid groupBy="album" onOpen={onOpen} />);

    fireEvent.click(await screen.findByRole('listitem'));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ album: 'SAW' }));
  });

  it('says what is missing instead of showing a blank panel', async () => {
    stub([]);
    render(<FindingsAlbumGrid groupBy="album" onOpen={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/Nothing to group here/)).toBeTruthy());
  });

  it('sends the filters so the grouping matches what the list is showing', async () => {
    stub([GROUP]);
    render(
      <FindingsAlbumGrid
        groupBy="album"
        status="dismissed"
        findingType="quality_upgrade"
        onOpen={vi.fn()}
      />,
    );
    await screen.findByText('SAW');
    const url = String((fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls[0][0]);
    expect(url).toContain('group_by=album');
    expect(url).toContain('status=dismissed');
    expect(url).toContain('finding_type=quality_upgrade');
  });
});
