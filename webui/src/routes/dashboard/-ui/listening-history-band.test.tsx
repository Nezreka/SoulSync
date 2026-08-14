import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ListeningHistoryBand } from './listening-history-band';

const playTrackByMetadata = vi.hoisted(() =>
  vi.fn(async (_bridge: unknown, _title: string, _artist: string, _album: string) => {}),
);
vi.mock('../../../features/playback/play-track', () => ({ playTrackByMetadata }));

const openArtistFromRail = vi.hoisted(() => vi.fn(async () => {}));
vi.mock('../-dash.content', () => ({ openArtistFromRail }));

const ROW = {
  title: 'Windowlicker',
  artist: 'Aphex Twin',
  album: 'Windowlicker EP',
  played_at: '2026-08-12 11:00:00',
  server_source: 'plex',
  image_url: '/art/1',
  artist_db_id: 'art_9',
};

function serve(tracks: unknown[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ json: async () => ({ success: true, tracks }) })) as never,
  );
}

const card = () => document.querySelector('.dash-rail .ya-card') as HTMLElement;

beforeEach(() => {
  playTrackByMetadata.mockClear();
  openArtistFromRail.mockClear();
  vi.unstubAllGlobals();
  window.navigateToPage = vi.fn();
});

describe('the Recently Played card plays the song again', () => {
  it('plays on a click anywhere on the card body', async () => {
    // Boulder: the most obvious gesture on a rail of songs used to do the one
    // thing you cannot do to a song — open the stats page.
    serve([ROW]);
    render(<ListeningHistoryBand />);
    await waitFor(() => expect(card()).toBeTruthy());

    fireEvent.click(card());
    expect(playTrackByMetadata).toHaveBeenCalledTimes(1);
    const [, title, artist, album] = playTrackByMetadata.mock.calls[0];
    expect([title, artist, album]).toEqual(['Windowlicker', 'Aphex Twin', 'Windowlicker EP']);
    // And it does NOT also navigate away mid-play.
    expect(window.navigateToPage).not.toHaveBeenCalled();
  });

  it('still sends the artist line to the artist, and only there', async () => {
    serve([ROW]);
    render(<ListeningHistoryBand />);
    await waitFor(() => expect(card()).toBeTruthy());

    fireEvent.click(screen.getByTitle('Open Aphex Twin'));
    expect(openArtistFromRail).toHaveBeenCalledWith({
      name: 'Aphex Twin',
      libraryArtistId: 'art_9',
    });
    // The click must not bubble into the card's play handler.
    expect(playTrackByMetadata).not.toHaveBeenCalled();
  });

  it('keeps the header as the way to the full ledger', async () => {
    serve([ROW]);
    render(<ListeningHistoryBand />);
    await waitFor(() => expect(card()).toBeTruthy());

    fireEvent.click(screen.getByText('Recently Played'));
    expect(window.navigateToPage).toHaveBeenCalledWith('stats');
    expect(playTrackByMetadata).not.toHaveBeenCalled();
  });

  it('passes an empty album rather than undefined when the ledger has none', async () => {
    // playTrackByMetadata's album parameter feeds the streaming search; a row
    // that never recorded an album must not turn into the string "undefined".
    serve([{ ...ROW, album: null }]);
    render(<ListeningHistoryBand />);
    await waitFor(() => expect(card()).toBeTruthy());

    fireEvent.click(card());
    expect(playTrackByMetadata.mock.calls[0][3]).toBe('');
  });

  it('renders nothing at all when there is no history', async () => {
    serve([]);
    const { container } = render(<ListeningHistoryBand />);
    await waitFor(() => expect(container.querySelector('.dash-card')).toBeNull());
  });
});
