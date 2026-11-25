/**
 * the movies page draws the rows its layout names: in the layout's order,
 * a row that is off left out, an extra row that was switched on shown, and
 * the page's defaults while the layout hasn't loaded.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CatalogMovie } from '../-player.types';

const state: { layout: unknown } = { layout: undefined };
const empty = (k: string) => ({ queryKey: ['t', k], queryFn: async () => null });

vi.mock('../-player.api', async (orig) => ({
  ...(await orig<typeof import('../-player.api')>()),
  pageLayoutQuery: (page: string) => ({
    queryKey: ['t', 'layout', page],
    queryFn: async () => state.layout ?? null,
  }),
  continueQuery: () => empty('continue'),
  serverResumeQuery: () => empty('server-resume'),
  playerSettingsQuery: () => empty('settings'),
  discoverForYouQuery: () => ({ queryKey: ['t', 'foryou'], queryFn: async () => [] }),
  discoverMoreLikeQuery: () => ({ queryKey: ['t', 'morelike'], queryFn: async () => [] }),
  discoverListQuery: (k: string) => ({ queryKey: ['t', 'list', k], queryFn: async () => [] }),
  studioCollectionsQuery: () => ({ queryKey: ['t', 'studio'], queryFn: async () => [] }),
}));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}));

const movie = (id: number, patch: Partial<CatalogMovie>): CatalogMovie => ({
  id,
  title: `Film ${id}`,
  year: 2010,
  runtime_seconds: 110 * 60,
  content_rating: 'R',
  rating: 7,
  genres: ['Drama'],
  added_at: '2026-11-01T00:00:00',
  has_poster: false,
  has_backdrop: false,
  studio: null,
  tmdb_id: null,
  ...patch,
});
const MOVIES = [
  ...[1, 2, 3, 4].map((i) => movie(i, { runtime_seconds: 85 * 60 })), // quick watches
  ...[5, 6, 7, 8].map((i) => movie(i, { runtime_seconds: 170 * 60, rating: 8.6 })), // epics, top rated
];

async function mount() {
  const { VideoForYou } = await import('./browse');
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <VideoForYou kind="movie" movies={MOVIES} shows={[]} />
    </QueryClientProvider>,
  );
}
const titles = () => screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);

afterEach(() => {
  state.layout = undefined;
});

describe('the movies page rows', () => {
  it('shows its default rows while there is no layout', async () => {
    await mount();
    await waitFor(() => expect(titles()).toContain('Recently added'));
    expect(titles()).toContain('Top rated in your library');
    expect(titles()).not.toContain('Quick watches');
    expect(screen.getByRole('button', { name: 'Customize this page' })).toBeTruthy();
  });

  it("follows the layout: its order, its switched-on extras, and not what's off", async () => {
    state.layout = {
      layout: {
        rows: [
          { id: 'epics', style: 'standard', on: true },
          { id: 'quick-watches', style: 'standard', on: true },
          { id: 'recent', style: 'standard', on: false },
          { id: 'top-rated', style: 'standard', on: true },
        ],
      },
      scope: 'all',
    };
    await mount();
    await waitFor(() => expect(titles()).toContain('Epic cinema'));
    expect(titles()).toEqual(['Epic cinema', 'Quick watches', 'Top rated in your library']);
  });

  it('a row with nothing to show is left out, not drawn empty', async () => {
    state.layout = {
      layout: {
        rows: [
          { id: 'family', style: 'standard', on: true },
          { id: 'epics', style: 'standard', on: true },
        ],
      },
      scope: null,
    };
    await mount();
    await waitFor(() => expect(titles()).toEqual(['Epic cinema']));
  });
});
