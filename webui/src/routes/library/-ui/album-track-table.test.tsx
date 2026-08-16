import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2AlbumDetail, LibraryV2TrackFile } from '../-library-v2.types';

import {
  AlbumTrackTable,
  AlbumSizeBadge,
  clampColumnWidth,
  LibraryV2CanWriteContext,
  mergeColumnOrder,
  normalizeColumnWidths,
  resolveResponsiveColumnWidths,
  resizeColumnWidths,
  TrackCheckBadge,
} from './library-v2-page';

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

function trackFile(overrides: Partial<LibraryV2TrackFile> = {}): LibraryV2TrackFile {
  return {
    file_id: 70,
    path: '/music/checked.flac',
    format: 'flac',
    bitrate: 900_000,
    sample_rate: 44_100,
    bit_depth: 16,
    size: 1024,
    quality_tier: 'lossless',
    import_status: 'imported',
    verification_status: null,
    acoustid_status: null,
    pipeline_result: {},
    source: null,
    file_state: 'active',
    ...overrides,
  };
}

describe('library v2 album track table', () => {
  it('shows a release size badge even when the release currently occupies zero bytes', () => {
    const { rerender } = render(<AlbumSizeBadge bytes={0} />);
    expect(screen.getByText('0 B')).toHaveAttribute('title', 'Size on disk');
    rerender(<AlbumSizeBadge bytes={5 * 1024 * 1024} />);
    expect(screen.getByText('5.00 MB')).toHaveAttribute('title', 'Size on disk');
  });

  it('sanitizes restored widths and appends newly introduced columns once', () => {
    expect(clampColumnWidth(-100)).toBe(1);
    expect(clampColumnWidth(9999)).toBe(9999);
    expect(mergeColumnOrder(['duration', 'obsolete'], ['duration', 'file_size'])).toEqual([
      'duration',
      'file_size',
    ]);

    const defaultLayout = normalizeColumnWidths(['number', 'title']);
    expect(defaultLayout).toEqual({ number: 3, title: 97 });

    const normalized = normalizeColumnWidths(['number', 'title', 'file_size'], {
      file_size: 120,
    });
    expect(Object.values(normalized).reduce((sum, value) => sum + value, 0)).toBeCloseTo(100);
    expect(normalized.title).toBeGreaterThan(normalized.file_size);
    expect(normalized.number).toBe(3);

    const restoredLegacyNumber = normalizeColumnWidths(['number', 'title', 'file_size'], {
      number: 320,
      title: 200,
      file_size: 120,
    });
    expect(restoredLegacyNumber.number).toBe(3);
    expect(Object.values(restoredLegacyNumber).reduce((sum, value) => sum + value, 0)).toBe(100);

    const bounded = normalizeColumnWidths(['number', 'title', 'file_size'], {
      number: 1,
      title: 1,
      file_size: 9999,
    });
    expect(Object.values(bounded).reduce((sum, value) => sum + value, 0)).toBe(100);
    expect(Math.min(...Object.values(bounded))).toBeGreaterThanOrEqual(1);
    expect(bounded.file_size).toBeGreaterThan(80);

    const resized = resizeColumnWidths(normalized, ['number', 'title', 'file_size'], 'title', 5);
    expect(resized.title).toBeCloseTo(normalized.title + 5);
    expect(resized.file_size).toBeCloseTo(normalized.file_size - 5);
    expect(Object.values(resized).reduce((sum, value) => sum + value, 0)).toBeCloseTo(100);

    const narrowedNumber = resizeColumnWidths(
      restoredLegacyNumber,
      ['number', 'title', 'file_size'],
      'number',
      -50,
    );
    expect(narrowedNumber.number).toBe(1);
    expect(narrowedNumber.title).toBeGreaterThan(restoredLegacyNumber.title);

    const expandedNumber = resizeColumnWidths(defaultLayout, ['number', 'title'], 'number', 500);
    expect(expandedNumber).toEqual({ number: 99, title: 1 });
  });

  it('keeps relative widths until compact columns need their readable minimum', () => {
    const keys = ['number', 'title', 'duration', 'file_path'];
    const weights = { number: 2, title: 48, duration: 3, file_path: 47 };

    const ultrawide = resolveResponsiveColumnWidths(keys, weights, 4_000);
    expect(ultrawide).toEqual({
      number: 80,
      title: 1_920,
      duration: 120,
      file_path: 1_880,
    });

    const narrower = resolveResponsiveColumnWidths(keys, weights, 1_000);
    expect(Object.values(narrower).reduce((sum, width) => sum + width, 0)).toBeCloseTo(1_000);
    expect(narrower.number).toBeGreaterThan(20);
    expect(narrower.duration).toBeGreaterThan(30);
    expect(narrower.title).toBeLessThan(480);
    expect(narrower.file_path).toBeLessThan(470);
    // Columns that still have room keep the user's relative relationship;
    // only the compact columns opt out of proportional shrinking.
    expect(narrower.title / narrower.file_path).toBeCloseTo(48 / 47, 3);

    const veryNarrow = resolveResponsiveColumnWidths(keys, weights, 180);
    expect(Object.values(veryNarrow).reduce((sum, width) => sum + width, 0)).toBeCloseTo(180);
    expect(Math.min(...Object.values(veryNarrow))).toBeGreaterThan(0);
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

  it('shows media-server recognition next to the imported track title', async () => {
    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({
          success: true,
          album: album([track({ media_server_sources: ['navidrome', 'plex'] })]),
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

    const recognition = await screen.findByLabelText('Recognised by Navidrome and Plex');
    expect(recognition).toHaveAttribute('title', 'Recognised by Navidrome and Plex');
    expect(recognition).toHaveTextContent('✓2');
    expect(screen.queryByText('Navidrome')).not.toBeInTheDocument();
    expect(screen.queryByText('Plex')).not.toBeInTheDocument();
  });

  it('renders the generic Check column separately from verification provenance', async () => {
    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({
          success: true,
          album: album([
            track({
              file_status: 'present',
              file: trackFile({
                verification_status: 'verified',
                acoustid_status: 'pass',
                pipeline_result: { acoustid_message: 'fingerprint matched' },
              }),
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
        HttpResponse.json({
          success: true,
          preferences: {
            track_table: {
              columns: { quality: true, verification: true, acoustid: true },
              column_order: ['quality', 'verification', 'acoustid'],
            },
          },
        }),
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

    expect(await screen.findByRole('columnheader', { name: /^Check/ })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /^Verification/ })).toBeInTheDocument();
    expect(screen.getAllByText('Verified')).toHaveLength(2);
    expect(
      screen
        .getAllByText('Verified')
        .find((element) => element.getAttribute('title')?.includes('fingerprint matched')),
    ).toBeDefined();
  });

  it('summarizes human, skipped and unscanned check outcomes with reasons', () => {
    const { rerender } = render(
      <TrackCheckBadge
        file={trackFile({
          verification_status: 'human_verified',
          acoustid_status: 'skip',
          pipeline_result: { acoustid_message: 'approved after retry review' },
        })}
      />,
    );
    expect(screen.getByText('Human verified')).toHaveAttribute(
      'title',
      expect.stringContaining('approved after retry review'),
    );
    expect(screen.getByText('Human verified').className).toContain('verificationHuman');

    rerender(
      <TrackCheckBadge
        file={trackFile({
          verification_status: 'force_imported',
          acoustid_status: 'skip',
          pipeline_result: { acoustid_message: 'accepted by retry import' },
        })}
      />,
    );
    expect(screen.getByText('Skipped')).toHaveAttribute(
      'title',
      expect.stringContaining('accepted by retry import'),
    );

    rerender(
      <TrackCheckBadge
        file={trackFile({
          pipeline_result: {
            acoustid_message: 'scanner disabled for this run',
          },
        })}
      />,
    );
    expect(screen.getByText('Not scanned')).toHaveAttribute(
      'title',
      expect.stringContaining('scanner disabled for this run'),
    );
  });

  it('does not call a verified file unscanned just because no fingerprint verdict was stored', () => {
    // The reported bug: the AcoustID tool had processed the whole library and
    // Michael Jackson still read "Not scanned". The scan wrote its verdict to
    // verification_status only, so every file it agreed with fell through to
    // the unscanned branch. The scanner records `acoustid_status` now — but
    // the files it verified BEFORE that fix still carry none, and calling them
    // unchecked would be just as wrong today.
    render(<TrackCheckBadge file={trackFile({ verification_status: 'verified' })} />);

    expect(screen.getByText('Verified')).toBeInTheDocument();
    expect(screen.queryByText('Not scanned')).not.toBeInTheDocument();
  });

  it('says a fingerprint mismatch out loud instead of calling it unscanned', () => {
    // A file the fingerprint contradicts is the most-checked file there is.
    // It used to render identically to one nothing had ever looked at.
    render(
      <TrackCheckBadge
        file={trackFile({
          verification_status: 'verified',
          acoustid_status: 'fail',
          pipeline_result: { acoustid_message: 'matches "Smooth Criminal"' },
        })}
      />,
    );

    expect(screen.getByText('Mismatch')).toHaveAttribute(
      'title',
      expect.stringContaining('Smooth Criminal'),
    );
  });

  it('opens one-track table settings in a viewport portal without clipping sections', async () => {
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

    const { container } = render(
      <QueryClientProvider client={createTestQueryClient()}>
        <LibraryV2CanWriteContext.Provider value>
          <AlbumTrackTable albumId={42} onAction={vi.fn()} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );

    await screen.findByRole('table');
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Table options — columns & match providers',
      }),
    );

    const dialog = screen.getByRole('dialog', {
      name: 'Table options — columns & match providers',
    });
    expect(dialog).toBeInTheDocument();
    expect(container).not.toContainElement(dialog);
    expect(document.body).toContainElement(dialog);
    expect(within(dialog).getByText('Visible columns')).toBeInTheDocument();
    expect(within(dialog).getByText('Quality & sizing')).toBeInTheDocument();
    expect(within(dialog).getByText('Match providers')).toBeInTheDocument();
    expect(within(dialog).getByText('Check')).toBeInTheDocument();
  });

  it('keeps table preference writes and column resize fail-closed read-only', async () => {
    let writes = 0;
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
      http.put('/api/library/v2/ui-preferences', () => {
        writes += 1;
        return HttpResponse.json({
          success: true,
          preferences: { track_table: {} },
        });
      }),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumTrackTable albumId={42} onAction={vi.fn()} />
      </QueryClientProvider>,
    );

    await screen.findByRole('table');
    const settings = screen.getByRole('button', {
      name: 'Table options — columns & match providers',
    });
    expect(settings).toBeDisabled();
    expect(screen.queryByRole('separator', { name: /Resize/ })).not.toBeInTheDocument();
    fireEvent.click(settings);
    expect(writes).toBe(0);
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
            track({
              id: 8,
              title: 'Large',
              track_number: 2,
              file: file(5 * 1024 * 1024),
            }),
            track({
              id: 7,
              title: 'Small',
              track_number: 1,
              file: file(1024 * 1024),
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

    const rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      bottom: 400,
      height: 400,
      left: 0,
      right: 1000,
      top: 0,
      width: 1000,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <LibraryV2CanWriteContext.Provider value>
          <AlbumTrackTable albumId={42} onAction={vi.fn()} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );

    const table = await screen.findByRole('table');
    expect(screen.getByText('5.00 MB')).toBeInTheDocument();
    expect(screen.getByText('1.00 MB')).toBeInTheDocument();
    const tableColumns = Array.from(table.querySelectorAll('col'));
    expect(tableColumns[0].style.width).toBe('28px');
    expect(tableColumns[1].style.width).toBe('30px');
    expect(tableColumns.at(-1)?.style.width).toBe('80px');
    expect(tableColumns.slice(2, -1).every((column) => column.style.width.endsWith('px'))).toBe(
      true,
    );

    const numberColumn = tableColumns[2];
    const initialNumberWidth = Number.parseFloat(numberColumn.style.width);
    const numberHandle = screen.getByRole('separator', {
      name: 'Resize number column',
    });
    fireEvent.pointerDown(numberHandle, {
      button: 0,
      pointerId: 6,
      clientX: 100,
    });
    fireEvent.pointerMove(numberHandle, { pointerId: 6, clientX: 125 });
    expect(Number.parseFloat(numberColumn.style.width)).toBeGreaterThan(initialNumberWidth);
    fireEvent.pointerUp(numberHandle, { pointerId: 6, clientX: 125 });

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

    const handle = screen.getByRole('separator', {
      name: 'Resize file_size column',
    });
    fireEvent.pointerDown(handle, { button: 0, pointerId: 7, clientX: 100 });
    fireEvent.pointerMove(handle, { pointerId: 7, clientX: 150 });
    fireEvent.pointerUp(handle, { pointerId: 7, clientX: 150 });
    await waitFor(() => {
      const resizePatch = patches.find(
        (patch) =>
          typeof patch === 'object' &&
          patch !== null &&
          'track_table' in patch &&
          typeof patch.track_table === 'object' &&
          patch.track_table !== null &&
          'column_widths' in patch.track_table &&
          Object.values(patch.track_table.column_widths as Record<string, number | null>).some(
            (value) => typeof value === 'number',
          ),
      ) as { track_table: { column_widths: Record<string, number> } } | undefined;
      expect(resizePatch).toBeDefined();
      expect(resizePatch?.track_table.column_widths.title).toBeGreaterThan(0);
      expect(resizePatch?.track_table.column_widths.file_size).toBeGreaterThan(0);
    });

    fireEvent.doubleClick(handle);
    await waitFor(() => {
      expect(patches).toContainEqual({
        track_table: {
          column_widths: expect.objectContaining({
            file_size: null,
            number: null,
            title: null,
          }),
        },
      });
    });
    rectSpy.mockRestore();
  });
});
