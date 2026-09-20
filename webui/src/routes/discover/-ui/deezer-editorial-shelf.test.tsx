/**
 * The shelf itself, not its api layer (that is
 * `-discover.deezer-editorial.test.ts`). Three things are the shelf:
 * it shows what the genre answered, a genre click asks a different genre,
 * and typing searches instead of browsing — after the debounce, so a
 * keystroke is not a request at Deezer.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import { DeezerEditorialShelf } from './deezer-editorial-shelf';

const GENRES = [
  { id: 0, name: 'All' },
  { id: 152, name: 'Rock' },
];

function playlist(id: string, title: string) {
  return {
    id,
    title,
    creator: 'Deezer Editors',
    track_count: 50,
    image_url: `https://cdn/${id}.jpg`,
    link: `https://www.deezer.com/playlist/${id}`,
    source: 'deezer',
  };
}

function api({ byGenre = {} as Record<string, string>, search = 'Searched' } = {}) {
  const asked: string[] = [];
  server.use(
    http.get('/api/discover/deezer/genres', () =>
      HttpResponse.json({ success: true, genres: GENRES }),
    ),
    // browse and search are the same endpoint, told apart by `q` — which is
    // exactly the distinction the shelf has to get right
    http.get('/api/discover/deezer/editorial', ({ request }) => {
      const params = new URL(request.url).searchParams;
      const q = params.get('q');
      if (q) {
        asked.push(`search:${q}`);
        return HttpResponse.json({ success: true, playlists: [playlist('p-s', search)] });
      }
      const genre = params.get('genre') ?? '0';
      asked.push(`genre:${genre}`);
      return HttpResponse.json({
        success: true,
        playlists: [playlist(`p-${genre}`, byGenre[genre] ?? `Genre ${genre} Pick`)],
      });
    }),
  );
  return asked;
}

describe('DeezerEditorialShelf', () => {
  it('opens on the everything chart and renders what it answered', async () => {
    const asked = api({ byGenre: { '0': 'Deezer Hits' } });
    render(<DeezerEditorialShelf />);
    expect(await screen.findByText('Deezer Hits')).toBeInTheDocument();
    expect(asked).toContain('genre:0');
  });

  it('a genre tab asks that genre, and marks itself selected', async () => {
    const asked = api({ byGenre: { '152': 'Rock Essentials' } });
    render(<DeezerEditorialShelf />);
    const rock = await screen.findByRole('tab', { name: 'Rock' });

    fireEvent.click(rock);

    expect(await screen.findByText('Rock Essentials')).toBeInTheDocument();
    expect(asked).toContain('genre:152');
    await waitFor(() => expect(rock).toHaveAttribute('aria-selected', 'true'));
  });

  it('typing searches instead of browsing, and debounces to one request', async () => {
    const asked = api({ search: 'Found By Search' });
    render(<DeezerEditorialShelf />);
    await screen.findByText('Genre 0 Pick');
    const box = await screen.findByLabelText('Search Deezer playlists');

    // three keystrokes in a row: the debounce collapses them, and only the
    // query that was still there when it fired reaches Deezer
    fireEvent.change(box, { target: { value: 'r' } });
    fireEvent.change(box, { target: { value: 'ro' } });
    fireEvent.change(box, { target: { value: 'rock' } });

    expect(
      await screen.findByText('Found By Search', undefined, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(asked.filter((a) => a.startsWith('search:'))).toEqual(['search:rock']);
  });
});
