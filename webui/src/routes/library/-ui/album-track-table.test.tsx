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
  mergeTrackColumnOrder,
  normalizeColumnWidths,
  resolveResponsiveColumnWidths,
  resizeColumnWidths,
  resizeResponsiveColumnWidths,
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
    metadata_gaps: [],
    ...overrides,
    // A row that carries a file record is a present track unless the test says
    // otherwise. Defaulting every fixture to 'missing' while handing it a file
    // produced a shape the server cannot emit, and tests built on it asserted
    // present-file behaviour on a row the UI is entitled to treat as gone.
    file_status:
      overrides.file_status ?? (overrides.file ? ('present' as const) : ('missing' as const)),
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
    expect(mergeTrackColumnOrder(['disc'], ['title', 'disc', 'duration'])).toEqual([
      'title',
      'disc',
      'duration',
    ]);
    expect(mergeTrackColumnOrder(['disc', 'title'], ['title', 'disc', 'duration'])).toEqual([
      'disc',
      'title',
      'duration',
    ]);

    const defaultLayout = normalizeColumnWidths(['number', 'title']);
    expect(defaultLayout).toEqual({ number: 2.532, title: 97.468 });

    const normalized = normalizeColumnWidths(['number', 'title', 'file_size'], {
      file_size: 120,
    });
    expect(Object.values(normalized).reduce((sum, value) => sum + value, 0)).toBeCloseTo(100);
    expect(normalized.title).toBeGreaterThan(normalized.file_size);
    expect(normalized.number).toBe(2.532);

    const restoredLegacyNumber = normalizeColumnWidths(['number', 'title', 'file_size'], {
      number: 320,
      title: 200,
      file_size: 120,
    });
    expect(restoredLegacyNumber.number).toBeCloseTo(2.532, 2);
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
    expect(veryNarrow).toEqual({
      number: 42,
      title: 120,
      duration: 82,
      file_path: 128,
    });
    expect(Object.values(veryNarrow).reduce((sum, width) => sum + width, 0)).toBe(372);
  });

  it('keeps unrelated responsive columns still while resizing one divider', () => {
    const keys = ['number', 'title', 'duration', 'file_path'];
    const weights = { number: 2, title: 48, duration: 10, file_path: 40 };
    const before = resolveResponsiveColumnWidths(keys, weights, 800);

    const resizedWeights = resizeResponsiveColumnWidths(weights, keys, 'duration', 40, 800);
    const after = resolveResponsiveColumnWidths(keys, resizedWeights, 800);

    expect(after.number).toBeCloseTo(before.number, 2);
    expect(after.title).toBeCloseTo(before.title, 2);
    expect(after.duration).toBeCloseTo(before.duration + 40, 2);
    expect(after.file_path).toBeCloseTo(before.file_path - 40, 2);

    const blockedWeights = resizeResponsiveColumnWidths(weights, keys, 'duration', -500, 800);
    const blocked = resolveResponsiveColumnWidths(keys, blockedWeights, 800);
    expect(blocked.duration).toBe(82);
    expect(blocked.number).toBeCloseTo(before.number, 2);
    expect(blocked.title).toBeCloseTo(before.title, 2);

    const narrowMatch = resolveResponsiveColumnWidths(
      ['title', 'match'],
      { title: 80, match: 20 },
      200,
    );
    expect(narrowMatch).toEqual({ title: 120, match: 264 });
    expect(
      resolveResponsiveColumnWidths(['title', 'match'], { title: 80, match: 20 }, 200, {
        title: 120,
        match: 260,
      }),
    ).toEqual({ title: 120, match: 260 });

    const matchResize = resizeResponsiveColumnWidths(
      { title: 50, match: 50 },
      ['title', 'match'],
      'title',
      500,
      600,
    );
    expect(resolveResponsiveColumnWidths(['title', 'match'], matchResize, 600).match).toBe(264);
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

  it('shows media-server recognition in its own column', async () => {
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
        HttpResponse.json({
          success: true,
          preferences: { track_table: { columns: { media_server: true } } },
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

    const recognition = await screen.findByLabelText('Recognised by Navidrome and Plex');
    expect(recognition).toHaveAttribute('title', 'Recognised by Navidrome and Plex');
    expect(recognition).toHaveTextContent('✓2');
    expect(screen.getByRole('columnheader', { name: 'Media server' })).toBeInTheDocument();
    expect(recognition.closest('td')).not.toContainElement(screen.getByText('Track Seven'));
    expect(screen.queryByText('Navidrome')).not.toBeInTheDocument();
    expect(screen.queryByText('Plex')).not.toBeInTheDocument();
  });

  it('shows the quality profile in its own column', async () => {
    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({
          success: true,
          album: album([
            track({
              file_status: 'present',
              file: trackFile(),
              quality_profile_source: 'album',
              meets_profile: true,
            }),
          ]),
        }),
      ),
      http.get('/api/library/v2/albums/42/match-status', () =>
        HttpResponse.json({ success: true, album: [], tracks: {} }),
      ),
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({ success: true, profiles: [{ id: 1, name: 'Lossless' }] }),
      ),
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: true,
          preferences: { track_table: { columns: { profile: true, quality: true } } },
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

    const profile = await screen.findByText('Lossless (Album)');
    const quality = screen.getByText('FLAC · 16bit/44.1kHz · 900 kbps');
    expect(screen.getByRole('columnheader', { name: 'Profile' })).toBeInTheDocument();
    expect(profile.closest('td')).not.toBe(quality.closest('td'));
    expect(screen.queryByText('900 kbps')).not.toBeInTheDocument();
  });

  it('distinguishes acquired quality from an intentional retained output', async () => {
    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({
          success: true,
          album: album([
            track({
              file_status: 'present',
              file: trackFile({
                acquired_quality_json: JSON.stringify({
                  format: 'flac',
                  sample_rate: 96_000,
                  bit_depth: 24,
                  bitrate: null,
                }),
                retention_json: JSON.stringify([
                  {
                    type: 'downsample_hires_flac',
                    source_replaced: true,
                    target_bit_depth: 16,
                    target_sample_rate: 44_100,
                  },
                ]),
              }),
              meets_profile: true,
              upgrade_candidate: false,
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
          preferences: { track_table: { columns: { quality: true } } },
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

    expect(await screen.findByText('FLAC · 16bit/44.1kHz · 900 kbps')).toBeInTheDocument();
    const acquired = screen.getByText('acquired FLAC · 24bit · 96kHz');
    expect(acquired).toHaveAttribute('title', expect.stringContaining('Upgrade cutoff'));
  });

  it('renders one Check column and no separate verification column', async () => {
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
              // A stale saved preference from before the two columns were
              // merged must not resurrect the removed one.
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
    expect(screen.queryByRole('columnheader', { name: /^Verification/ })).not.toBeInTheDocument();
    expect(screen.getAllByText('Verified')).toHaveLength(1);
    expect(screen.getByText('Verified')).toHaveAttribute(
      'title',
      expect.stringContaining('fingerprint matched'),
    );
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

  it('calls a genuine no-match "Unverified", not "Skipped" — the check DID run', () => {
    // Reported: collapsing "AcoustID ran and found nothing confident" into
    // the same "Skipped" word as "the check never ran at all" (force/retry
    // bypass) lost the exact distinction the old Verification column drew as
    // "Unverified" vs "Bypassed". "Skipped" implies nothing happened; here
    // something did, it just couldn't confirm the file.
    render(
      <TrackCheckBadge
        file={trackFile({
          verification_status: null,
          acoustid_status: 'skip',
          pipeline_result: { acoustid_message: 'No match in AcoustID database' },
        })}
      />,
    );

    expect(screen.getByText('Unverified')).toHaveAttribute(
      'title',
      expect.stringContaining('No match in AcoustID database'),
    );
    expect(screen.queryByText('Skipped')).not.toBeInTheDocument();
  });

  it('explains an unverified file even with no captured message', () => {
    render(
      <TrackCheckBadge file={trackFile({ verification_status: null, acoustid_status: 'skip' })} />,
    );

    expect(screen.getByText('Unverified')).toHaveAttribute(
      'title',
      expect.stringContaining('found no confident match'),
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

  it('says the file is gone rather than blaming the scanner for not checking it', () => {
    // "Not scanned" on a row whose file no longer exists reads like the tool
    // skipped it. Nothing can fingerprint a file that is not there.
    render(
      <TrackCheckBadge
        file={trackFile({ verification_status: null, file_state: 'missing_confirmed' })}
      />,
    );

    expect(screen.getByText('File missing')).toBeInTheDocument();
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
    expect(within(dialog).getByRole('checkbox', { name: /Title/ })).toBeChecked();
    expect(within(dialog).getByRole('checkbox', { name: /Title/ })).toBeDisabled();
    expect(within(dialog).getByLabelText('Drag to reorder Title')).toBeInTheDocument();
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
    expect(tableColumns[0].style.width).toBe('24px');
    expect(tableColumns[1].style.width).toBe('28px');
    expect(tableColumns[2].style.width).toBe('32px');
    expect(tableColumns.at(-1)?.style.width).toBe('80px');
    expect(tableColumns.slice(3, -1).every((column) => column.style.width.endsWith('px'))).toBe(
      true,
    );
    expect(
      screen.queryByRole('separator', { name: 'Resize number column' }),
    ).not.toBeInTheDocument();

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
            title: null,
          }),
        },
      });
    });
    rectSpy.mockRestore();
  });

  it('empties the file columns of a track whose file is gone', async () => {
    // The row still carries the quality and size the file HAD — that history is
    // worth keeping in the database, but printing it in the table made a
    // missing track indistinguishable from a present one at a glance.
    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({
          success: true,
          album: album([
            track({
              title: 'You See Big Girl',
              file_status: 'missing',
              file: {
                file_id: 1703,
                path: 'Sawano Hiroyuki/OST/01-03 - You See Big Girl.flac',
                format: 'flac',
                bitrate: 929_000,
                sample_rate: 44_100,
                bit_depth: 16,
                size: 42_155_831,
                quality_tier: 'lossless',
                import_status: null,
                verification_status: null,
                source: null,
                file_state: 'missing_confirmed',
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

    // Neither the measured quality nor the byte size may be presented as
    // current state; the stored path stays, because it says where it was.
    expect(await screen.findByText('You See Big Girl')).toBeInTheDocument();
    expect(screen.queryByText(/FLAC/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/40\.2 MB|42155831/)).not.toBeInTheDocument();
  });
});

describe('a track whose file lives on another release', () => {
  it('names the release the file actually sits on', async () => {
    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({
          success: true,
          album: album([
            track({
              title: 'Vogel Im Kafig',
              file_status: 'linked',
              linked_from: {
                track_id: 8960,
                album_id: 4023,
                album_title: 'Vogel Im Kafig',
                album_type: 'single',
                file_id: 1865,
                path: 'Sawano Hiroyuki/Vogel Im Kafig/01-07 - Vogel Im Kafig.flac',
              },
              file: {
                file_id: 1865,
                path: 'Sawano Hiroyuki/Vogel Im Kafig/01-07 - Vogel Im Kafig.flac',
                format: 'flac',
                bitrate: null,
                sample_rate: null,
                bit_depth: null,
                size: null,
                quality_tier: 'lossless',
                import_status: 'imported',
                verification_status: null,
                source: null,
                file_state: 'active',
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

    const badge = await screen.findByText('on “Vogel Im Kafig”');
    expect(badge).toHaveAttribute(
      'title',
      expect.stringContaining('Sawano Hiroyuki/Vogel Im Kafig/01-07 - Vogel Im Kafig.flac'),
    );
  });
});
