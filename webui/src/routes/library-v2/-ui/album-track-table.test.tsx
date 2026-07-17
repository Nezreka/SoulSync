import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2AlbumDetail } from '../-library-v2.types';

import { AlbumTrackTable } from './library-v2-page';

function album(): LibraryV2AlbumDetail {
  return {
    id: 42,
    title: 'Uncached Album',
    album_type: 'album',
    release_date: null,
    year: null,
    image_url: null,
    genres: [],
    explicit: null,
    label: null,
    style: null,
    mood: null,
    monitored: false,
    origin: 'library',
    quality_profile: null,
    primary_artist: null,
    tracks: [],
    track_count: 0,
    tracks_present: 0,
    tracks_missing: 0,
    user_overrides: {},
  };
}

describe('library v2 album track table', () => {
  it('expands an uncached album after its first request completes', async () => {
    let finishRequest: (() => void) | undefined;
    const requestGate = new Promise<void>((resolve) => {
      finishRequest = resolve;
    });

    server.use(
      http.get('/api/library/v2/albums/42', async () => {
        await requestGate;
        return HttpResponse.json({ success: true, album: album() });
      }),
      http.get('/api/library/v2/albums/42/match-status', () =>
        HttpResponse.json({ success: true, album: [], tracks: {} }),
      ),
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({ success: true, profiles: [] }),
      ),
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({ success: true, preferences: { track_table: {} } }),
      ),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumTrackTable albumId={42} onAction={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(screen.getByText('Loading tracks…')).toBeInTheDocument();
    finishRequest?.();

    expect(await screen.findByRole('table')).toBeInTheDocument();
  });
});
