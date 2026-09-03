import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AlbumPlayButton, ArtistPlayButton } from './library-v2-page';

/**
 * The play buttons Library v2 was missing.
 *
 * Upstream added album and artist play in 84c9871cb / f5908f07e, both on the
 * legacy release card and library artist card this branch deleted — so the
 * features never reached the V2 page even though every sync merged them
 * cleanly. These pin the wiring end to end: a click has to reach the shared
 * Legacy player with rows it can actually play.
 */

function renderWithQuery(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  window.playTrackList = vi.fn();
  window.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('AlbumPlayButton', () => {
  it('queues the album through the shared player', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              success: true,
              album: {
                id: 5,
                title: 'Selected Ambient Works',
                image_url: '/art/saw.jpg',
                tracks: [
                  {
                    id: 42,
                    title: 'Xtal',
                    track_number: 1,
                    disc_number: 1,
                    artists: [{ id: 7, name: 'Aphex Twin', role: 'primary' }],
                    file: { file_id: 1, path: '/music/xtal.flac', file_state: 'active' },
                  },
                ],
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    );

    renderWithQuery(
      <AlbumPlayButton
        albumId={5}
        albumTitle="Selected Ambient Works"
        artistName="Aphex Twin"
        tracksPresent={1}
      />,
    );

    fireEvent.click(screen.getByTitle('Play Selected Ambient Works'));

    await waitFor(() => expect(window.playTrackList).toHaveBeenCalledTimes(1));
    const [rows, context] = (window.playTrackList as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(context).toBe('Selected Ambient Works');
    expect(rows).toEqual([
      expect.objectContaining({
        title: 'Xtal',
        file_path: '/music/xtal.flac',
        is_library: true,
        lib2_track_id: 42,
      }),
    ]);
  });

  it('is disabled for a release with nothing on disk', () => {
    renderWithQuery(
      <AlbumPlayButton albumId={5} albumTitle="Wishlist Only" artistName="X" tracksPresent={0} />,
    );
    expect(screen.getByTitle('No files in the library for this release')).toBeDisabled();
  });

  it('says so rather than opening a silent empty queue', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ success: true, album: { id: 5, title: 'A', tracks: [] } }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    );
    renderWithQuery(
      <AlbumPlayButton albumId={5} albumTitle="A" artistName="X" tracksPresent={2} />,
    );
    fireEvent.click(screen.getByTitle('Play A'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Nothing on this release is on disk yet',
        'info',
      ),
    );
    expect(window.playTrackList).not.toHaveBeenCalled();
  });
});

describe('ArtistPlayButton', () => {
  it('walks the track-file pages and queues them in one go', async () => {
    const pages = [
      {
        success: true,
        files: [
          {
            file_id: 1,
            track_id: 1,
            track_title: 'Xtal',
            track_number: 1,
            disc_number: 1,
            album_id: 5,
            album_title: 'SAW',
            path: '/music/1.flac',
            file_state: 'active',
            is_primary: true,
          },
        ],
        pagination: { page: 1, limit: 100, total_count: 2, total_pages: 2 },
      },
      {
        success: true,
        files: [
          {
            file_id: 2,
            track_id: 2,
            track_title: 'Tha',
            track_number: 2,
            disc_number: 1,
            album_id: 5,
            album_title: 'SAW',
            path: '/music/2.flac',
            file_state: 'active',
            is_primary: true,
          },
        ],
        pagination: { page: 2, limit: 100, total_count: 2, total_pages: 2 },
      },
    ];
    let call = 0;
    const asked: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        asked.push(String((input as Request).url ?? input));
        return new Response(JSON.stringify(pages[call++] ?? pages[1]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );

    renderWithQuery(<ArtistPlayButton artistId={7} artistName="Aphex Twin" />);
    fireEvent.click(screen.getByTitle('Play everything by Aphex Twin'));

    await waitFor(() => expect(window.playTrackList).toHaveBeenCalledTimes(1));
    const [rows, context] = (window.playTrackList as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(context).toBe('Aphex Twin');
    expect((rows as Array<{ title: string }>).map((r) => r.title)).toEqual(['Xtal', 'Tha']);
    // Not the Manage-Track-Files endpoint: that one is scoped to albums whose
    // PRIMARY artist is this one, so it drops every guest credit the page shows.
    expect(asked.every((url) => url.includes('/play-queue'))).toBe(true);
  });

  it('reports an empty library instead of an empty queue', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              success: true,
              files: [],
              pagination: { page: 1, limit: 100, total_count: 0, total_pages: 0 },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    );
    renderWithQuery(<ArtistPlayButton artistId={7} artistName="Aphex Twin" />);
    fireEvent.click(screen.getByTitle('Play everything by Aphex Twin'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Nothing by Aphex Twin is on disk yet',
        'info',
      ),
    );
    expect(window.playTrackList).not.toHaveBeenCalled();
  });
});
