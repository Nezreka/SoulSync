import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2MatchService } from '../-library-v2.types';

import { MatchChips } from './library-v2-page';

function service(overrides: Partial<LibraryV2MatchService> = {}): LibraryV2MatchService {
  return {
    service: 'spotify',
    label: 'Spotify',
    status: 'matched',
    external_id: 'sp1',
    last_attempted: null,
    legacy_entity_id: 5,
    available: true,
    match_origin: 'manual',
    ...overrides,
  };
}

function renderWithClient(node: React.ReactElement) {
  server.use(
    http.get('/api/library/v2/ui-preferences', () =>
      HttpResponse.json({
        success: true,
        preferences: { track_table: { visible_match_providers: {} } },
      }),
    ),
    http.post('/api/library/match-artist-releases', async ({ request }) => {
      const body = (await request.json()) as { artist_id?: string };
      return body.artist_id === 'sp2'
        ? HttpResponse.json({
            success: true,
            supported: true,
            albums: [
              {
                id: 'a1',
                title: 'Take Care',
                image: 'https://img.example/take-care.jpg',
                release_date: '2011-11-15',
                album_type: 'album',
              },
            ],
          })
        : HttpResponse.json({ success: true, supported: false, albums: [] });
    }),
  );
  const queryClient = createTestQueryClient();
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

describe('library v2 match chips (deep-dive A8)', () => {
  it('hides chips flagged unavailable', () => {
    renderWithClient(
      <MatchChips
        entityType="artist"
        entityName="Drake"
        services={[
          service({ service: 'spotify', label: 'Spotify', available: true }),
          service({ service: 'tidal', label: 'Tidal', available: false }),
        ]}
      />,
    );

    expect(screen.getByText('Spotify')).toBeInTheDocument();
    expect(screen.queryByText(/Tidal/)).not.toBeInTheDocument();
  });

  it('treats a missing `available` field as available (older cached responses)', () => {
    const { available: _unused, ...withoutAvailable } = service();
    renderWithClient(
      <MatchChips entityType="artist" entityName="Drake" services={[withoutAvailable]} />,
    );

    expect(screen.getByText('Spotify')).toBeInTheDocument();
  });

  it('keeps manual matching enabled for lib2-native artists', async () => {
    server.use(
      http.post('/api/library/search-service', () =>
        HttpResponse.json({
          success: true,
          results: [{ id: 'sp-native', name: 'Native Artist', provider: 'spotify' }],
        }),
      ),
      http.put('/api/library/v2/artists/77/manual-match', () =>
        HttpResponse.json({ success: true }),
      ),
    );
    renderWithClient(
      <MatchChips
        entityType="artist"
        entityName="Native Artist"
        services={[service({ legacy_entity_id: null, library_v2_entity_id: 77 })]}
      />,
    );

    const chip = screen.getByText('Spotify');
    expect(chip.closest('button')).not.toBeDisabled();
    fireEvent.click(chip);
    fireEvent.click(await screen.findByRole('button', { name: 'Search' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use this match' }));
    await waitFor(() => expect(screen.queryByText('Manual match')).not.toBeInTheDocument());
  });

  it('renders nothing when every chip is unavailable', () => {
    const { container } = renderWithClient(
      <MatchChips
        entityType="artist"
        entityName="Drake"
        services={[service({ available: false })]}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('§52.5: shows follower/popularity stats a manual-match candidate carries', async () => {
    server.use(
      http.post('/api/library/search-service', () =>
        HttpResponse.json({
          success: true,
          results: [
            {
              id: 'sp1',
              name: 'Drake',
              provider: 'spotify',
              followers: 54_000_000,
              popularity: 97,
            },
          ],
        }),
      ),
    );
    renderWithClient(<MatchChips entityType="artist" entityName="Drake" services={[service()]} />);

    fireEvent.click(screen.getByText('Spotify'));
    fireEvent.click(await screen.findByRole('button', { name: 'Search' }));

    expect(await screen.findByText('54M followers · 97 popularity')).toBeInTheDocument();
  });

  it('§52.5: omits the stats line when a candidate carries none', async () => {
    server.use(
      http.post('/api/library/search-service', () =>
        HttpResponse.json({
          success: true,
          results: [{ id: 'it1', name: 'Drake', provider: 'itunes' }],
        }),
      ),
    );
    renderWithClient(<MatchChips entityType="artist" entityName="Drake" services={[service()]} />);

    fireEvent.click(screen.getByText('Spotify'));
    fireEvent.click(await screen.findByRole('button', { name: 'Search' }));

    await waitFor(() => expect(screen.getByText('ID: it1')).toBeInTheDocument());
    expect(screen.getByText('itunes')).toBeInTheDocument();
    expect(screen.queryByText(/followers|popularity/)).not.toBeInTheDocument();
  });

  it('§56.2: renders large provider artwork, provenance and candidate album context', async () => {
    server.use(
      http.post('/api/library/search-service', () =>
        HttpResponse.json({
          success: true,
          results: [
            {
              id: 'sp2',
              name: 'Drake candidate',
              provider: 'spotify',
              image: 'https://img.example/drake-candidate.jpg?token=signed',
            },
          ],
        }),
      ),
      http.post('/api/library/match-artist-releases', () =>
        HttpResponse.json({
          success: true,
          supported: true,
          albums: [
            {
              id: 'a1',
              title: 'Take Care',
              image: 'https://img.example/take-care.jpg',
              release_date: '2011-11-15',
              album_type: 'album',
            },
          ],
        }),
      ),
    );
    renderWithClient(
      <MatchChips
        entityType="artist"
        entityName="Drake"
        entityImage="https://img.example/current.jpg?token=signed"
        services={[service()]}
      />,
    );

    fireEvent.click(screen.getByText('Spotify'));
    expect(await screen.findByText('Manual match')).toBeInTheDocument();
    expect(screen.getByAltText('Drake')).toHaveAttribute(
      'src',
      'https://img.example/current.jpg?token=signed',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText('Take Care')).toBeInTheDocument();
    expect(screen.getByAltText('Drake candidate')).toHaveAttribute(
      'src',
      'https://img.example/drake-candidate.jpg?token=signed',
    );
  });

  it('uses the candidate provider for fallback matches and syncs its watchlist id', async () => {
    let submitted: Record<string, unknown> | null = null;
    server.use(
      http.post('/api/library/search-service', () =>
        HttpResponse.json({
          success: true,
          results: [{ id: 'it-artist-1', name: 'Drake', provider: 'itunes' }],
        }),
      ),
      http.put('/api/library/manual-match', async ({ request }) => {
        submitted = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true });
      }),
    );
    renderWithClient(
      <MatchChips
        entityType="artist"
        entityName="Drake"
        watchlistRowId={11}
        services={[service()]}
      />,
    );

    fireEvent.click(screen.getByText('Spotify'));
    fireEvent.click(await screen.findByRole('button', { name: 'Search' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use this match' }));

    await waitFor(() =>
      expect(submitted).toMatchObject({
        entity_type: 'artist',
        entity_id: 5,
        service: 'itunes',
        service_id: 'it-artist-1',
        watchlist_row_id: 11,
      }),
    );
  });
});
