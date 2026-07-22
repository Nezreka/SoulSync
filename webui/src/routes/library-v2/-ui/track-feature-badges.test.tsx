import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2Track } from '../-library-v2.types';

import { TrackLyricsBadge, TrackMetadataGapsCell, TrackReplayGainBadge } from './library-v2-page';

function track(overrides: Partial<LibraryV2Track> = {}): LibraryV2Track {
  return {
    id: 7,
    title: 'Track',
    track_number: 1,
    disc_number: 1,
    duration: null,
    bpm: null,
    explicit: null,
    style: null,
    mood: null,
    isrc: null,
    monitored: true,
    quality_profile_id: 1,
    canonical_track_id: null,
    artists: [],
    file: {
      file_id: 1,
      path: '/music/track.flac',
      size: null,
      bitrate: null,
      sample_rate: null,
      bit_depth: null,
      format: null,
      quality_tier: 'unknown',
      verification_status: null,
      import_status: null,
      source: null,
      file_state: null,
      has_replaygain: false,
      has_lyrics: false,
    },
    file_status: 'present',
    metadata_gaps: [],
    meets_profile: null,
    upgrade_candidate: null,
    ...overrides,
  };
}

function renderWithClient(node: React.ReactElement) {
  const queryClient = createTestQueryClient();
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

describe('library v2 RG badge (deep-dive B3)', () => {
  beforeEach(() => {
    window.showToast = vi.fn();
  });

  afterEach(() => {
    delete (window as any).showToast;
  });

  it('shows a green badge when present, with no action', () => {
    renderWithClient(
      <TrackReplayGainBadge track={track({ file: { ...track().file!, has_replaygain: true } })} />,
    );
    const badge = screen.getByText('RG');
    expect(badge.tagName).toBe('SPAN');
  });

  it('clicking the grey badge analyzes and writes ReplayGain', async () => {
    let called = false;
    server.use(
      http.post('/api/library/v2/tracks/7/replaygain', () => {
        called = true;
        return HttpResponse.json({ success: true, track_gain_db: -3.1 });
      }),
    );
    renderWithClient(<TrackReplayGainBadge track={track()} />);

    fireEvent.click(screen.getByRole('button', { name: 'RG' }));

    await waitFor(() => expect(called).toBe(true));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'ReplayGain analyzed and written (-3.1 dB).',
        'success',
      ),
    );
  });

  it('surfaces a failed analysis as the badge title', async () => {
    server.use(
      http.post('/api/library/v2/tracks/7/replaygain', () =>
        HttpResponse.json({ success: false, error: 'ffmpeg not found on PATH' }, { status: 500 }),
      ),
    );
    renderWithClient(<TrackReplayGainBadge track={track()} />);

    fireEvent.click(screen.getByRole('button', { name: 'RG' }));

    await waitFor(() =>
      expect(screen.getByRole('button')).toHaveAttribute('title', 'ffmpeg not found on PATH'),
    );
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('ffmpeg not found on PATH', 'error'),
    );
  });
});

describe('library v2 LR badge (deep-dive B3)', () => {
  beforeEach(() => {
    window.showToast = vi.fn();
  });

  afterEach(() => {
    delete (window as any).showToast;
  });

  it('clicking the green badge opens the lyrics tab instead of fetching', () => {
    const onOpenLyrics = vi.fn();
    renderWithClient(
      <TrackLyricsBadge
        track={track({ file: { ...track().file!, has_lyrics: true } })}
        onOpenLyrics={onOpenLyrics}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'LR' }));

    expect(onOpenLyrics).toHaveBeenCalledOnce();
  });

  it('clicking the grey badge fetches lyrics from LRClib', async () => {
    let called = false;
    server.use(
      http.post('/api/library/v2/tracks/7/fetch-lyrics', () => {
        called = true;
        return HttpResponse.json({ success: true, fetched: true });
      }),
    );
    const onOpenLyrics = vi.fn();
    renderWithClient(<TrackLyricsBadge track={track()} onOpenLyrics={onOpenLyrics} />);

    fireEvent.click(screen.getByRole('button', { name: 'LR' }));

    await waitFor(() => expect(called).toBe(true));
    expect(onOpenLyrics).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Lyrics fetched and embedded.', 'success'),
    );
  });

  it('surfaces an unavailable-lyrics error as the badge title', async () => {
    server.use(
      http.post('/api/library/v2/tracks/7/fetch-lyrics', () =>
        HttpResponse.json(
          { success: false, error: 'No lyrics available for this track' },
          { status: 400 },
        ),
      ),
    );
    renderWithClient(<TrackLyricsBadge track={track()} onOpenLyrics={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'LR' }));

    await waitFor(() =>
      expect(screen.getByRole('button')).toHaveAttribute(
        'title',
        'No lyrics available for this track',
      ),
    );
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('No lyrics available for this track', 'error'),
    );
  });
});

describe('library v2 metadata-gaps cell (docs §79 LV2-TAG-STATUS-01/02)', () => {
  beforeEach(() => {
    window.showToast = vi.fn();
  });

  afterEach(() => {
    delete (window as any).showToast;
  });

  it('shows a scan-pending state instead of a false "tags ✓" for a never-scanned file', () => {
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({ metadata_scan_status: 'pending', metadata_gaps: [] })}
        onOpenTags={vi.fn()}
      />,
    );
    expect(screen.getByText('scan pending').tagName).toBe('SPAN');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('shows an unreadable state when the last tag read failed', () => {
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({ metadata_scan_status: 'unreadable', metadata_gaps: [] })}
        onOpenTags={vi.fn()}
      />,
    );
    expect(screen.getByText('unreadable').tagName).toBe('SPAN');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('clicking "tags ✓" opens the tags tab instead of writing anything', () => {
    const onOpenTags = vi.fn();
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({ metadata_scan_status: 'scanned', metadata_gaps: [] })}
        onOpenTags={onOpenTags}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'tags ✓' }));

    expect(onOpenTags).toHaveBeenCalledOnce();
  });

  it('clicking "N tag gaps" writes this track\'s tags and never claims success optimistically', async () => {
    let submittedIds: unknown = null;
    server.use(
      http.post('/api/library/v2/tags/write', async ({ request }) => {
        submittedIds = ((await request.json()) as { track_ids: unknown }).track_ids;
        return HttpResponse.json({ success: true, job_id: 'retag-job-1' });
      }),
      http.get('/api/library/v2/jobs/status', () =>
        HttpResponse.json({ running: false, result: { written: 1, skipped: 0, failed: 0 } }),
      ),
    );
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({ metadata_scan_status: 'scanned', metadata_gaps: ['cover'] })}
        onOpenTags={vi.fn()}
      />,
    );

    const button = screen.getByRole('button', { name: '1 tag gaps' });
    fireEvent.click(button);

    await waitFor(() => expect(submittedIds).toEqual([7]));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Tags written to file.', 'success'),
    );
  });

  it('surfaces a failed tag write as the button title without claiming "tags ✓"', async () => {
    server.use(
      http.post('/api/library/v2/tags/write', () =>
        HttpResponse.json({ success: true, job_id: 'retag-job-2' }),
      ),
      http.get('/api/library/v2/jobs/status', () =>
        HttpResponse.json({ running: false, error: 'File not found on disk' }),
      ),
    );
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({ metadata_scan_status: 'scanned', metadata_gaps: ['cover'] })}
        onOpenTags={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '1 tag gaps' }));

    await waitFor(() =>
      expect(screen.getByRole('button')).toHaveAttribute('title', 'File not found on disk'),
    );
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('File not found on disk', 'error'),
    );
    expect(screen.queryByRole('button', { name: 'tags ✓' })).not.toBeInTheDocument();
  });
});
