import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { OwnedArtistHeroExtras } from './owned-artist-hero-extras';

describe('owned artist hero extras', () => {
  it('shows the db record button, last.fm tag chips and format tags', async () => {
    server.use(
      http.get('/api/artist/:id/record', () =>
        HttpResponse.json({
          success: true,
          formats: ['FLAC', 'MP3'],
          counts: { albums: 1, tracks: 2 },
          record: {
            id: 3,
            name: 'Portishead',
            enrichment: { lastfm: { tags: ['trip-hop', 'Electronic', 'bristol'] } },
          },
        }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <OwnedArtistHeroExtras artistId={3} artistName="Portishead" genres={['electronic']} />
      </QueryClientProvider>,
    );

    expect(screen.getByRole('button', { name: /DB Record/ })).toBeInTheDocument();
    expect(await screen.findByText('FLAC')).toBeInTheDocument();
    expect(screen.getByText('MP3')).toBeInTheDocument();
    expect(screen.getByText('trip-hop')).toBeInTheDocument();
    expect(screen.getByText('bristol')).toBeInTheDocument();
    // a tag that repeats a genre is not shown twice
    expect(screen.queryByText('Electronic')).not.toBeInTheDocument();
  });
});
