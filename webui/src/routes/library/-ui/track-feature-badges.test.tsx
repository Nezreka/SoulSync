import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2Track } from '../-library-v2.types';

import { lib2Track } from '../-library-v2.test-fixtures';
import {
  LibraryV2CanWriteContext,
  TrackLyricsBadge,
  TrackMetadataGapsCell,
  TrackReplayGainBadge,
} from './library-v2-page';

const track = lib2Track;

function renderWithClient(node: React.ReactElement) {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <LibraryV2CanWriteContext.Provider value>{node}</LibraryV2CanWriteContext.Provider>
    </QueryClientProvider>,
  );
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
  it('keeps unknown references visible after a gap-free read', () => {
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({
          metadata_gaps: [],
          metadata_validation: { status: 'unknown', checks: { edition: 'unknown' } },
        })}
        onOpenTags={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'partly checked' })).toBeInTheDocument();
    expect(screen.queryByText('Metadata ✓')).not.toBeInTheDocument();
  });

  it('opens details for an ambiguous edition without writing guessed numbers', () => {
    const open = vi.fn();
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({
          metadata_gaps: ['track_number'],
          metadata_validation: {
            status: 'issues',
            checks: { edition: 'unknown', track_number: 'missing' },
          },
        })}
        onOpenTags={open}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '1 issues' }));
    expect(open).toHaveBeenCalledOnce();
  });
  beforeEach(() => {
    window.showToast = vi.fn();
  });

  afterEach(() => {
    delete (window as any).showToast;
  });

  it('shows a scan-pending state instead of a false "Metadata ✓" for a never-scanned file', () => {
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

  it('clicking "Metadata ✓" opens the tags tab instead of writing anything', () => {
    const onOpenTags = vi.fn();
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({
          metadata_scan_status: 'scanned',
          metadata_gaps: [],
          metadata_validation: { status: 'correct', checks: {} },
        })}
        onOpenTags={onOpenTags}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Metadata ✓' }));

    expect(onOpenTags).toHaveBeenCalledOnce();
  });

  it('keeps an old presence-only snapshot partly checked', () => {
    renderWithClient(
      <TrackMetadataGapsCell track={track({ metadata_gaps: [] })} onOpenTags={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: 'partly checked' })).toBeInTheDocument();
    expect(screen.queryByText('Metadata ✓')).not.toBeInTheDocument();
  });

  it('shows an exact present-versus-missing tag breakdown on hover or keyboard focus', async () => {
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({
          metadata_scan_status: 'scanned',
          metadata_gaps: ['genre', 'cover'],
        })}
        onOpenTags={vi.fn()}
      />,
    );

    fireEvent.focus(screen.getByRole('button', { name: '2 issues' }));

    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip.parentElement?.className).toContain('metadataTagsTooltipPositioner');
    expect(tooltip).toHaveTextContent('Present tags');
    expect(tooltip).toHaveTextContent('✓ Title');
    expect(tooltip).toHaveTextContent('Metadata issues');
    expect(tooltip).toHaveTextContent('✗ Genre · ✗ Cover Art');
    expect(tooltip).toHaveTextContent('Click to repair the stored metadata findings');
  });

  it('clicking "N issues" re-fetches from providers then writes this track\'s tags, never claiming success optimistically', async () => {
    let requestedTrackId: string | undefined;
    server.use(
      http.post('/api/library/v2/tracks/:trackId/fill-tag-gaps', ({ params }) => {
        requestedTrackId = params.trackId as string;
        return HttpResponse.json({ success: true, job_id: 'retag-job-1' });
      }),
      http.get('/api/library/v2/jobs/status', () =>
        HttpResponse.json({
          running: false,
          result: {
            written: 1,
            skipped: 0,
            failed: 0,
            enriched_from: 'deezer',
          },
        }),
      ),
    );
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({
          metadata_scan_status: 'scanned',
          metadata_gaps: ['cover'],
        })}
        onOpenTags={vi.fn()}
      />,
    );

    const button = screen.getByRole('button', { name: '1 issues' });
    fireEvent.click(button);

    await waitFor(() => expect(requestedTrackId).toBe('7'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Refetched from deezer and wrote tags to file.',
        'success',
      ),
    );
  });

  it('reports a write that changed nothing instead of claiming the gaps were closed', async () => {
    // issues.md T-03: the endpoint answers 200/no-error even when it wrote to
    // no file at all (a genre gap the catalogue cannot fill). Reporting that as
    // "Tags written" is how the same two gaps kept coming back after a click
    // that looked successful.
    server.use(
      http.post('/api/library/v2/tracks/:trackId/fill-tag-gaps', () =>
        HttpResponse.json({ success: true, job_id: 'retag-job-3' }),
      ),
      http.get('/api/library/v2/jobs/status', () =>
        HttpResponse.json({
          running: false,
          result: { written: 0, skipped: 1, failed: 0, enriched_from: null },
        }),
      ),
    );
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({
          metadata_scan_status: 'scanned',
          metadata_gaps: ['genre'],
        })}
        onOpenTags={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '1 issues' }));

    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Nothing to write — no configured provider has these tags yet.',
        'info',
      ),
    );
  });

  it('surfaces a failed tag write in the breakdown without claiming "Metadata ✓"', async () => {
    server.use(
      http.post('/api/library/v2/tracks/:trackId/fill-tag-gaps', () =>
        HttpResponse.json({ success: true, job_id: 'retag-job-2' }),
      ),
      http.get('/api/library/v2/jobs/status', () =>
        HttpResponse.json({ running: false, error: 'File not found on disk' }),
      ),
    );
    renderWithClient(
      <TrackMetadataGapsCell
        track={track({
          metadata_scan_status: 'scanned',
          metadata_gaps: ['cover'],
        })}
        onOpenTags={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '1 issues' }));

    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('File not found on disk', 'error'),
    );
    fireEvent.focus(screen.getByRole('button', { name: '1 issues' }));
    expect(await screen.findByRole('tooltip')).toHaveTextContent('File not found on disk');
    expect(screen.queryByRole('button', { name: 'Metadata ✓' })).not.toBeInTheDocument();
  });
});
