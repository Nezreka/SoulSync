/**
 * Row expansion + live detail (#1156, wishx).
 *
 * "It's a mystery going from Pending to Searching to Downloading to
 * Processing" — rows are now clickable: in-flight rows show the live
 * narration off the poll payload, terminal rows fetch the merged task
 * detail, history rows render the fields they already carry.
 */
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AdlDownload } from '../-adl.types';

import { liveDetailLines } from '../-adl.helpers';
import { AdlRow } from './adl-row';

vi.mock('../-adl.api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../-adl.api')>();
  return {
    ...mod,
    fetchTaskDetail: vi.fn(async () => ({
      task_id: 't1',
      status: 'completed',
      status_kind: 'completed',
      title: 'Xtal',
      artist: 'Aphex Twin',
      album: 'SAW',
      source: 'some_peer',
      reason: '',
      quarantine_entry_id: '',
      file_path: '/music/xtal.flac',
      quality: 'FLAC 16bit/44.1kHz',
      acoustid_result: 'match',
      thumb_url: '',
      expected: {},
      downloaded: {},
    })),
  };
});

afterEach(cleanup);

const row = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({
    task_id: 't1',
    title: 'Xtal',
    artist: 'Aphex Twin',
    album: 'SAW',
    artwork: '',
    status: 'downloading',
    progress: 50,
    error: null,
    verification_status: null,
    batch_id: '',
    batch_name: '',
    batch_source: '',
    playlist_id: 'p1',
    track_index: 0,
    batch_total: 1,
    timestamp: 0,
    priority: 0,
    quality: '',
    is_persistent_history: false,
    ...over,
  }) as AdlDownload;

// ── liveDetailLines: the pure narration ─────────────────────────────────────

describe('liveDetailLines', () => {
  it('is empty without a live_detail', () => {
    expect(liveDetailLines(row())).toEqual([]);
  });

  it('narrates a search: chain, ladder position, counts, per-source split', () => {
    const lines = liveDetailLines(
      row({
        status: 'searching',
        live_detail: {
          source: 'soulseek → youtube',
          query: 'xo john mayer',
          query_index: 1,
          query_count: 4,
          responses: 7,
          results: 12,
          by_source: { soulseek: 9, youtube: 3 },
        },
      }),
    );
    expect(lines).toEqual([
      ['Searching', 'soulseek → youtube'],
      ['Query', '"xo john mayer" (2/4)'],
      ['Found', '12 results from 7 peers'],
      ['By source', 'soulseek 9 · youtube 3'],
    ]);
  });

  it('narrates a download: peer, file, candidate walk, raw queue state', () => {
    const lines = liveDetailLines(
      row({
        live_detail: {
          source: 'Soulseek',
          username: 'some_peer',
          filename: '03 - XO.flac',
          candidate_index: 2,
          candidate_count: 14,
          picked: { quality: 'flac', bitrate: 1024, size: 31457280, confidence: 0.93 },
          slskd_state: 'Queued, Remotely',
          size: 31457280,
          bytes: 1048576,
        },
      }),
    );
    expect(lines).toEqual([
      ['Source', 'Soulseek · peer some_peer'],
      ['File', '03 - XO.flac'],
      ['Candidate', '3 of 14'],
      ['Picked', 'FLAC · 1024 kbps · 30 MB · confidence 0.93'],
      ['Queue state', 'Queued, Remotely'],
      // formatBytes's decimal rule: one decimal only under 10 per unit
      ['Size', '1.0 MB / 30 MB'],
    ]);
  });

  it('narrates the wait: peer stats, remote-queue clock, journey so far', () => {
    const lines = liveDetailLines(
      row({
        live_detail: {
          source: 'Soulseek',
          username: 'slow_peer',
          filename: 'a.flac',
          picked: {
            quality: 'flac',
            queue_length: 40,
            free_upload_slots: 0,
            upload_speed: 2097152,
          },
          slskd_state: 'Queued, Remotely',
          queued_seconds: 45,
          tried_sources: 3,
          exhausted_sources: ['youtube'],
        },
      }),
    );
    expect(lines).toContainEqual(['Peer stats', '0 free slots · queue 40 · 2.0 MB/s avg']);
    expect(lines).toContainEqual(['Waited', '45s in the remote queue']);
    expect(lines).toContainEqual(['Tried', '3 peer/file pairs so far']);
    expect(lines).toContainEqual(['Exhausted', 'youtube']);
  });

  it('a streaming source names the service, not a fake peer', () => {
    const lines = liveDetailLines(
      row({ live_detail: { source: 'YouTube', username: 'youtube', filename: 'x.opus' } }),
    );
    expect(lines[0]).toEqual(['Source', 'YouTube']);
  });
});

// ── the row expansion ───────────────────────────────────────────────────────

describe('AdlRow expansion (#1156)', () => {
  it('an in-flight row expands to the live narration on click', () => {
    const { container } = render(
      <AdlRow
        dl={row({
          status: 'searching',
          live_detail: { source: 'soulseek', query: 'xo', query_index: 0, query_count: 3 },
        })}
      />,
    );
    expect(container.querySelector('.verif-quar-details')).toBeNull();
    fireEvent.click(container.querySelector('.adl-row') as HTMLElement);
    const panel = container.querySelector('.verif-quar-details') as HTMLElement;
    expect(panel.textContent).toContain('soulseek');
    expect(panel.textContent).toContain('"xo" (1/3)');
    // second click collapses
    fireEvent.click(container.querySelector('.adl-row') as HTMLElement);
    expect(container.querySelector('.verif-quar-details')).toBeNull();
  });

  it('a completed row fetches and renders the merged detail', async () => {
    const { container } = render(<AdlRow dl={row({ status: 'completed' })} />);
    await act(async () => {
      fireEvent.click(container.querySelector('.adl-row') as HTMLElement);
    });
    await waitFor(() => {
      const panel = container.querySelector('.verif-quar-details') as HTMLElement;
      expect(panel.textContent).toContain('FLAC 16bit/44.1kHz');
      expect(panel.textContent).toContain('/music/xtal.flac');
    });
  });

  it('a history row renders its own fields without fetching', () => {
    const { container } = render(
      <AdlRow
        dl={row({
          task_id: 'history-9',
          status: 'completed',
          is_persistent_history: true,
          download_source: 'Tidal',
          quality: 'FLAC 16bit/44.1kHz',
          file_path: '/music/a.flac',
          created_at: '2026-08-19 10:00:00',
        })}
      />,
    );
    fireEvent.click(container.querySelector('.adl-row') as HTMLElement);
    const panel = container.querySelector('.verif-quar-details') as HTMLElement;
    expect(panel.textContent).toContain('Tidal');
    expect(panel.textContent).toContain('/music/a.flac');
  });

  it('a live row shows its source label on the batch line (#1156 gap)', () => {
    const { container } = render(
      <AdlRow dl={row({ status: 'completed', batch_name: 'My PL', download_source: 'YouTube' })} />,
    );
    const line = container.querySelector('.adl-row-batch') as HTMLElement;
    expect(line.textContent).toBe('My PL · YouTube');
  });

  it('a history row does not repeat its source (batch_name IS the source)', () => {
    const { container } = render(
      <AdlRow
        dl={row({
          status: 'completed',
          is_persistent_history: true,
          batch_name: 'Tidal',
          download_source: 'Tidal',
        })}
      />,
    );
    const line = container.querySelector('.adl-row-batch') as HTMLElement;
    expect(line.textContent).toBe('Tidal');
  });

  it('the cancel button still cancels without toggling the row open', async () => {
    const onCancel = vi.fn();
    const { container } = render(<AdlRow dl={row()} onCancel={onCancel} />);
    await act(async () => {
      fireEvent.click(container.querySelector('.adl-row-cancel') as HTMLElement);
    });
    expect(onCancel).toHaveBeenCalled();
    expect(container.querySelector('.verif-quar-details')).toBeNull();
  });
});
