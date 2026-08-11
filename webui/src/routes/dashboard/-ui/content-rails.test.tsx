import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ContentBand } from './content-rails';

// Recently Added plays through the shell bridge; mock it at the module seam.
const playLibraryTrack = vi.fn();
vi.mock('@/platform/shell/bridge', () => ({
  getShellBridge: () => ({ playLibraryTrack }),
}));

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  playLibraryTrack.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ok = (body: unknown) =>
  Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response);

/** First call = recently-added, later calls = the fresh-release ladder. */
const feeds = (albums: unknown[], releases: unknown[]) => {
  fetchMock.mockImplementation((url: string) => {
    if (String(url).includes('recently-added')) return ok({ albums });
    if (String(url).includes('watchlist/recent-releases')) return ok({ releases });
    return ok({ albums: [] }); // discover fallback, unused unless releases=[]
  });
};

const anAlbum = {
  artist_name: 'Ado',
  album_name: 'Kyougen',
  thumb_url: 'k.jpg',
  added_at: '2026-08-10 09:00:00',
  track_count: 12,
  quality: 'FLAC',
  download_source: 'soulseek',
  play_title: 'Vivarium',
  play_file_path: '/m/v.flac',
};

const aRelease = {
  album_name: 'Brand New',
  artist_name: 'Watched Artist',
  release_date: '2026-08-01',
  album_spotify_id: 'alb1',
  source: 'spotify',
};

describe('ContentBand', () => {
  it('renders nothing at all while both feeds are empty', async () => {
    feeds([], []);
    const { container } = render(<ContentBand />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
  });

  it('is ONE dash-card with both tabs when both feeds have rows', async () => {
    feeds([anAlbum], [aRelease]);
    const { container } = render(<ContentBand />);
    expect(await screen.findByText('Kyougen')).toBeTruthy();
    expect(container.querySelectorAll('article.dash-card--rail')).toHaveLength(1);
    expect(screen.getByRole('tab', { name: 'Recently Added' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Fresh Releases' })).toBeTruthy();
    // Recently Added leads; the fresh strip is not mounted until switched.
    expect(screen.getByText('FLAC · soulseek')).toBeTruthy();
    expect(screen.queryByText('Brand New')).toBeNull();
  });

  it('switching tabs swaps the strip and the subtitle', async () => {
    feeds([anAlbum], [aRelease]);
    render(<ContentBand />);
    await screen.findByText('Kyougen');
    fireEvent.click(screen.getByRole('tab', { name: 'Fresh Releases' }));
    expect(screen.getByText('Brand New')).toBeTruthy();
    expect(screen.getByText('new music from artists you watch')).toBeTruthy();
    expect(screen.queryByText('Kyougen')).toBeNull();
  });

  it('a feed with no rows has no tab, and the other is auto-selected', async () => {
    feeds([], [aRelease]);
    render(<ContentBand />);
    expect(await screen.findByText('Brand New')).toBeTruthy();
    // display:none removes the empty feed's tab from the accessibility tree —
    // which is the point: there is nothing to switch to.
    expect(screen.queryByRole('tab', { name: 'Recently Added' })).toBeNull();
    expect(screen.getByRole('tab', { name: 'Fresh Releases' })).toBeTruthy();
  });

  it('a Recently Added card click hands the newest landed track to the player', async () => {
    feeds([anAlbum], []);
    render(<ContentBand />);
    (await screen.findByText('Kyougen'))
      .closest('.ya-card')
      ?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(playLibraryTrack).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Vivarium', file_path: '/m/v.flac' }),
      'Kyougen',
      'Ado',
    );
  });

  it('an owned release wears the library check badge; unowned does not', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('recently-added')) return ok({ albums: [] });
      if (String(url).includes('watchlist/recent-releases'))
        return ok({
          releases: [
            { album_name: 'Have It', artist_name: 'A', owned: true },
            { album_name: 'Missing It', artist_name: 'B', owned: false },
          ],
        });
      return ok({ albums: [] });
    });
    render(<ContentBand />);
    const ownedCard = (await screen.findByText('Have It')).closest('.ya-card')!;
    expect(ownedCard.querySelector('.discover-album-badge.owned')).toBeTruthy();
    const missingCard = screen.getByText('Missing It').closest('.ya-card')!;
    expect(missingCard.querySelector('.discover-album-badge')).toBeNull();
  });

  it('the discover fallback keeps its honest subtitle', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('recently-added')) return ok({ albums: [] });
      if (String(url).includes('watchlist/recent-releases')) return ok({ releases: [] });
      return ok({ albums: [{ album_name: 'Adjacent Album', artist_name: 'Similar' }] });
    });
    render(<ContentBand />);
    expect(await screen.findByText('Adjacent Album')).toBeTruthy();
    expect(screen.getByText(/follow artists to tune this/)).toBeTruthy();
  });
});
