import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2AlbumDetail } from '../-library-v2.types';

import { AlbumTrackTable, clampColumnWidth, mergeColumnOrder } from './library-v2-page';

function album(tracks: LibraryV2AlbumDetail['tracks'] = []): LibraryV2AlbumDetail {
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
    tracks,
    track_count: tracks.length,
    tracks_present: tracks.length,
    tracks_missing: 0,
    total_size_bytes: 0,
    user_overrides: {},
  };
}

function track(overrides: Partial<LibraryV2AlbumDetail['tracks'][number]> = {}) {
  return {
    id: 7,
    title: 'Track Seven',
    track_number: 1,
    disc_number: null,
    duration: null,
    bpm: null,
    explicit: null,
    style: null,
    mood: null,
    isrc: null,
    monitored: false,
    quality_profile_id: 1,
    canonical_track_id: null,
    artists: [],
    file: null,
    file_status: 'missing' as const,
    metadata_gaps: [],
    ...overrides,
  };
}

describe('library v2 album track table', () => {
  it('sanitizes restored widths and appends newly introduced columns once', () => {
    expect(clampColumnWidth(-100)).toBe(48);
    expect(clampColumnWidth(9999)).toBe(640);
    expect(mergeColumnOrder(['duration', 'obsolete'], ['duration', 'file_size'])).toEqual([
      'duration',
      'file_size',
    ]);
  });

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
      http.get('/api/library/v2/albums/42/queue-status', () =>
        HttpResponse.json({ tracks: {}, albums: {} }),
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

  it('shows a live queue-status badge next to a track currently downloading', async () => {
    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({ success: true, album: album([track()]) }),
      ),
      http.get('/api/library/v2/albums/42/match-status', () =>
        HttpResponse.json({ success: true, album: [], tracks: {} }),
      ),
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({ success: true, profiles: [] }),
      ),
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({ success: true, preferences: { track_table: {} } }),
      ),
      http.get('/api/library/v2/albums/42/queue-status', () =>
        HttpResponse.json({
          tracks: { 7: { status: 'downloading', progress_pct: 55 } },
          albums: { 42: 1 },
        }),
      ),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumTrackTable albumId={42} onAction={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Downloading 55%')).toBeInTheDocument();
  });

  it('shows no queue-status badge once the track has no in-flight entry', async () => {
    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({ success: true, album: album([track()]) }),
      ),
      http.get('/api/library/v2/albums/42/match-status', () =>
        HttpResponse.json({ success: true, album: [], tracks: {} }),
      ),
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({ success: true, profiles: [] }),
      ),
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({ success: true, preferences: { track_table: {} } }),
      ),
      http.get('/api/library/v2/albums/42/queue-status', () =>
        HttpResponse.json({ tracks: {}, albums: {} }),
      ),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumTrackTable albumId={42} onAction={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(screen.queryByText(/Downloading|Queued|Searching|Processing/)).not.toBeInTheDocument();
  });

  it('shows the first physical miss as pending confirmation', async () => {
    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({
          success: true,
          album: album([
            track({
              file_status: 'missing_suspected',
              file: {
                file_id: 17,
                path: '/music/temporarily-unreachable.flac',
                format: 'flac',
                bitrate: null,
                sample_rate: null,
                bit_depth: null,
                size: null,
                quality_tier: 'unknown',
                import_status: null,
                verification_status: null,
                source: null,
                file_state: 'missing_suspected',
              },
            }),
          ]),
        }),
      ),
      http.get('/api/library/v2/albums/42/match-status', () =>
        HttpResponse.json({ success: true, album: [], tracks: {} }),
      ),
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({ success: true, profiles: [] }),
      ),
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({ success: true, preferences: { track_table: {} } }),
      ),
      http.get('/api/library/v2/albums/42/queue-status', () =>
        HttpResponse.json({ tracks: {}, albums: {} }),
      ),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumTrackTable albumId={42} onAction={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('checking missing')).toHaveAttribute(
      'title',
      expect.stringContaining('second scan'),
    );
  });

  it('shows, sorts and resizes the physical file-size column while restoring old orders', async () => {
    const patches: unknown[] = [];
    const file = (size: number) => ({
      file_id: size,
      path: `/music/${size}.flac`,
      format: 'flac',
      bitrate: 900_000,
      sample_rate: 44_100,
      bit_depth: 16,
      size,
      quality_tier: 'lossless',
      import_status: 'imported',
      verification_status: 'verified',
      source: null,
      file_state: 'active',
    });
    const preferences = {
      track_table: {
        columns: { file_size: true },
        // Simulates an older stored preference list written before file_size
        // existed. The client must append every new default column.
        column_order: ['duration'],
        column_widths: { file_size: 120 },
        show_all_match_providers: false,
        visible_match_providers: {},
        quality_show_format: true,
        quality_show_resolution: true,
        quality_show_bitrate: true,
      },
    };

    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({
          success: true,
          album: album([
            track({ id: 8, title: 'Large', track_number: 2, file: file(5 * 1024 * 1024) }),
            track({ id: 7, title: 'Small', track_number: 1, file: file(1024 * 1024) }),
          ]),
        }),
      ),
      http.get('/api/library/v2/albums/42/match-status', () =>
        HttpResponse.json({ success: true, album: [], tracks: {} }),
      ),
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({ success: true, profiles: [] }),
      ),
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({ success: true, preferences }),
      ),
      http.put('/api/library/v2/ui-preferences', async ({ request }) => {
        patches.push(await request.json());
        return HttpResponse.json({ success: true, preferences });
      }),
      http.get('/api/library/v2/albums/42/queue-status', () =>
        HttpResponse.json({ tracks: {}, albums: {} }),
      ),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumTrackTable albumId={42} onAction={vi.fn()} />
      </QueryClientProvider>,
    );

    const table = await screen.findByRole('table');
    expect(screen.getByText('5.00 MB')).toBeInTheDocument();
    expect(screen.getByText('1.00 MB')).toBeInTheDocument();

    const visibleTitles = () =>
      within(table)
        .getAllByRole('row')
        .slice(1)
        .map((row) => within(row).getByText(/^(Large|Small)$/).textContent);
    expect(visibleTitles()).toEqual(['Large', 'Small']);

    fireEvent.click(screen.getByRole('button', { name: 'File size' }));
    expect(visibleTitles()).toEqual(['Small', 'Large']);
    fireEvent.click(screen.getByRole('button', { name: 'File size' }));
    expect(visibleTitles()).toEqual(['Large', 'Small']);

    const handle = screen.getByRole('separator', { name: 'Resize file_size column' });
    expect(handle.closest('th')).toHaveStyle({ width: '120px' });
    fireEvent.pointerDown(handle, { button: 0, pointerId: 7, clientX: 100 });
    fireEvent.pointerMove(handle, { pointerId: 7, clientX: 150 });
    fireEvent.pointerUp(handle, { pointerId: 7, clientX: 150 });
    await waitFor(() =>
      expect(patches).toContainEqual({
        track_table: {
          column_widths: {
            file_size: 170,
          },
        },
      }),
    );

    fireEvent.doubleClick(handle);
    await waitFor(() =>
      expect(patches).toContainEqual({
        track_table: {
          column_widths: {
            file_size: null,
          },
        },
      }),
    );
  });
});
