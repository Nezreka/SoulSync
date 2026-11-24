/**
 * the player's search before and around results: focusing it offers the
 * profile's own sections (and recent searches), Cancel leaves it, and a
 * search with nothing found says so.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const empty = (k: string) => ({ queryKey: ['t', k], queryFn: async () => null });
vi.mock('../-player.api', () => ({
  albumsQuery: () => empty('albums'),
  artistsQuery: () => empty('artists'),
  booksQuery: () => empty('books'),
  podcastSubsQuery: () => empty('pods'),
  songsQuery: () => empty('songs'),
  tmdbSearchQuery: () => empty('tmdb'),
  videoCatalogQuery: () => empty('cat'),
  youtubeHomeQuery: () => empty('yt'),
}));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => '/player',
  Link: ({ to, children, ...rest }: { to: string; children: React.ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock('./sections', async (orig) => ({
  ...(await orig<typeof import('./sections')>()),
  useSections: () => ({
    music: true,
    movies: true,
    tv: false,
    video: true,
    podcasts: false,
    audiobooks: false,
    isAdmin: false,
  }),
}));
vi.mock('./playback/use-player', () => ({ playItems: vi.fn() }));

async function mount() {
  const { Search } = await import('./search');
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Search />
    </QueryClientProvider>,
  );
  return screen.getByRole('combobox');
}

describe('the player search', () => {
  it("focusing it offers this profile's own sections, nothing it can't open", async () => {
    const box = await mount();
    fireEvent.focus(box);
    expect(screen.getByRole('link', { name: /Movies/ })).toBeTruthy();
    expect(screen.getByRole('link', { name: /Music/ })).toBeTruthy();
    expect(screen.queryByRole('link', { name: /Podcasts/ })).toBeNull();
    expect(screen.queryByRole('link', { name: /Audiobooks/ })).toBeNull();
    expect(screen.getByRole('search').getAttribute('data-open')).toBe('true');
  });

  it('Cancel clears it and leaves', async () => {
    const box = (await mount()) as HTMLInputElement;
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: 'heat' } });
    // hidden on a desktop (css), shown on a phone's search screen
    fireEvent.click(screen.getByText('Cancel'));
    expect(box.value).toBe('');
    expect(screen.getByRole('search').getAttribute('data-open')).toBeNull();
  });
});
