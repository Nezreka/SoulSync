/**
 * home draws the rows about libraries (scrap/HOME_LIBRARIES_CONTRACT.md):
 * recently added in one library, continue in it, on this day; it shows a
 * title in Movies and Movies 4K once, and the editor offers library rows.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { HomeLayout, LayoutCatalog } from '../-lib/home-layout';
import type { CatalogMovie, ProgressRow, VideoCatalog } from '../-player.types';

const q = (k: string, data: unknown) => ({ queryKey: ['t', k], queryFn: async () => data });

const movie = (id: number, patch: Partial<CatalogMovie>): CatalogMovie => ({
  id,
  title: `Film ${id}`,
  year: 2010,
  runtime_seconds: 6000,
  content_rating: 'PG',
  rating: 7,
  genres: ['Drama'],
  added_at: '2026-01-01',
  has_poster: false,
  has_backdrop: false,
  studio: null,
  tmdb_id: null,
  library_id: 1,
  ...patch,
});

const CATALOG: VideoCatalog = {
  libraries: [
    { id: 1, kind: 'movie', name: 'Movies', own_page: false, main: true },
    { id: 2, kind: 'show', name: 'TV Shows', own_page: false, main: true },
    { id: 7, kind: 'movie', name: 'Movies 4K', own_page: true, main: false, uhd: true },
    { id: 9, kind: 'home', name: 'Home videos', own_page: true, main: true },
  ],
  movies: [
    movie(10, { title: 'Dune', tmdb_id: 438631, added_at: '2025-01-01' }),
    movie(20, { title: 'Dune', tmdb_id: 438631, library_id: 7, added_at: '2026-06-01' }),
    movie(30, { title: 'Heat', tmdb_id: 949, added_at: '2025-05-01' }),
    movie(40, { title: 'Arrival', tmdb_id: 329865, library_id: 7, added_at: '2026-02-01' }),
  ],
  shows: [],
  recent_episodes: [],
  youtube: [],
  kinds: { movies: true, shows: true, youtube: false },
};

const PROGRESS: ProgressRow[] = [
  {
    item_type: 'movie',
    item_id: '40',
    position_seconds: 1200,
    duration_seconds: 6000,
    finished: false,
    meta: { title: 'Arrival', href: '/player/movies/40' },
    updated_at: '2026-11-20T10:00:00',
  } as ProgressRow,
];

const LAYOUT: HomeLayout = {
  billboard: false,
  rows: [
    { id: 'recent-movies', style: 'standard', on: true },
    { id: 'lib-recent:7', style: 'standard', on: true },
    { id: 'lib-continue:7', style: 'wide', on: true },
    { id: 'home-memories', style: 'wide', on: true },
    { id: 'lib-recent:1', style: 'standard', on: false },
  ],
};

const CATALOG_ROWS: LayoutCatalog = {
  version: 1,
  styles: [
    { id: 'standard', label: 'Cards' },
    { id: 'wide', label: 'Wide stills' },
    { id: 'large', label: 'Large cards' },
    { id: 'ranked', label: 'Top 10' },
  ],
  rows: [
    {
      id: 'recent-movies',
      label: 'Recently added movies',
      hint: 'Newest films first',
      styles: ['standard', 'large', 'wide', 'ranked'],
      style: 'standard',
      on: true,
    },
    {
      id: 'home-memories',
      label: 'On this day',
      hint: 'Home videos filmed on this day in years past',
      styles: ['wide', 'large'],
      style: 'wide',
      on: true,
    },
    {
      id: 'lib-recent:7',
      label: 'Recently added in Movies 4K',
      hint: 'The newest in Movies 4K first',
      styles: ['standard', 'large', 'wide', 'ranked'],
      style: 'standard',
      on: false,
    },
    {
      id: 'lib-continue:7',
      label: 'Continue in Movies 4K',
      hint: 'What you started in Movies 4K',
      styles: ['wide', 'large'],
      style: 'wide',
      on: false,
    },
    {
      id: 'lib-recent:1',
      label: 'Recently added in Movies',
      hint: 'The newest in Movies first',
      styles: ['standard', 'large', 'wide', 'ranked'],
      style: 'standard',
      on: false,
    },
  ],
};

const MEMORIES = [
  {
    id: 5,
    title: 'Thanksgiving at Grandma’s',
    filmed_at: '2023-11-26T15:00:00',
    duration_s: 95,
    width: 1920,
    height: 1080,
    album: 'Holidays',
    album_key: 'k',
    thumb_url: null,
    progress: { position_s: 0, watched: false },
    missing: false,
    library: 9,
  },
];

const played = vi.fn();

vi.mock('../-player.api', async (orig) => ({
  ...(await orig<typeof import('../-player.api')>()),
  continueQuery: () => q('continue', PROGRESS),
  serverResumeQuery: () => q('resume', []),
  videoCatalogQuery: () => q('catalog', CATALOG),
  studioCollectionsQuery: () => q('studio', []),
  discoverPlaylistsQuery: () => q('discover', []),
  albumsQuery: () => q('albums', { albums: [] }),
  booksQuery: () => q('books', []),
  podcastSubsQuery: () => q('podcasts', []),
  homeLayoutQuery: () => q('layout', { layout: LAYOUT, scope: 'all', catalog: CATALOG_ROWS }),
  guideCatalogQuery: () => q('guide', null),
  guideCommercialsQuery: () => q('commercials', null),
  guidePremieresQuery: () => q('premieres', []),
  discoverListQuery: (k: string) => q(`list-${k}`, []),
  discoverMoreLikeQuery: () => q('morelike', []),
  playerSettingsQuery: () => q('settings', null),
}));
vi.mock('../-lib/home-videos', async (orig) => ({
  ...(await orig<typeof import('../-lib/home-videos')>()),
  homeMemoriesQuery: () => q('memories', MEMORIES),
}));
vi.mock('./sections', async (orig) => ({
  ...(await orig<typeof import('./sections')>()),
  useSections: () => ({
    music: false,
    movies: true,
    tv: true,
    video: true,
    podcasts: false,
    audiobooks: false,
    isAdmin: true,
  }),
}));
vi.mock('./playback/use-player', async (orig) => ({
  ...(await orig<typeof import('./playback/use-player')>()),
  playItems: (items: unknown) => played(items),
}));
vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (opts: Record<string, unknown>) => ({
    ...opts,
    useSearch: () => ({}),
  }),
  useNavigate: () => vi.fn(),
  useRouter: () => ({ navigate: vi.fn() }),
  Link: ({ to, children, ...rest }: { to: string; children: React.ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

async function mount() {
  const { Route } = await import('../index');
  const Home = (Route as unknown as { component: () => React.ReactElement }).component;
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Home />
    </QueryClientProvider>,
  );
}

const rail = (title: string) => screen.getByRole('region', { name: title });
const links = (title: string) =>
  within(rail(title))
    .getAllByRole('link')
    .map((a) => a.getAttribute('href'))
    .filter((h) => h?.startsWith('/player/movies/'));

describe('home and libraries', () => {
  it('draws the library rows with the library’s name, and on this day', async () => {
    await mount();
    await waitFor(() => expect(screen.getByText('On this day')).toBeTruthy());
    const titles = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual([
      'Recently added movies',
      'Recently added in Movies 4K',
      'Continue in Movies 4K',
      'On this day',
    ]);
    // that library's own copies, newest first
    expect(links('Recently added in Movies 4K')).toEqual([
      '/player/movies/20',
      '/player/movies/40',
    ]);
    expect(links('Continue in Movies 4K')).toEqual(['/player/movies/40']);
    // a memory plays here
    const memory = within(rail('On this day')).getByRole('button', {
      name: 'Thanksgiving at Grandma’s',
    });
    expect(within(rail('On this day')).getByText('3 years ago · Nov 26, 2023')).toBeTruthy();
    fireEvent.click(memory);
    expect(played).toHaveBeenCalledWith([
      expect.objectContaining({ type: 'home', id: '5', href: '/player/home-videos?library=9' }),
    ]);
  });

  it('shows a film in Movies and Movies 4K once, on the main copy, with a 4K chip', async () => {
    await mount();
    await waitFor(() => expect(screen.getByText('Recently added movies')).toBeTruthy());
    // newest first: the 4K copy's place, the main copy's card
    expect(links('Recently added movies')).toEqual([
      '/player/movies/10',
      '/player/movies/40',
      '/player/movies/30',
    ]);
    const recent = within(rail('Recently added movies'));
    const dune = recent
      .getAllByRole('link')
      .find((a) => a.getAttribute('href') === '/player/movies/10');
    expect(within(dune as HTMLElement).getByText('4K')).toBeTruthy();
    // Heat and Arrival are in one library each: no chip
    expect(recent.getAllByText('4K')).toHaveLength(1);
  });

  it('offers the library rows in the editor', async () => {
    await mount();
    await waitFor(() => expect(screen.getByText('Recently added in Movies 4K')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Customize home' }));
    const editor = within(screen.getByRole('dialog', { name: 'Customize home' }));
    const off = editor.getByRole('checkbox', { name: 'Show Recently added in Movies' });
    expect((off as HTMLInputElement).checked).toBe(false);
    expect(
      (editor.getByRole('checkbox', { name: 'Show On this day' }) as HTMLInputElement).checked,
    ).toBe(true);
    expect(editor.getByText('What you started in Movies 4K')).toBeTruthy();
    // switching it on draws it
    fireEvent.click(off);
    await waitFor(() => expect(screen.getAllByText('Recently added in Movies').length).toBe(2));
  });
});
