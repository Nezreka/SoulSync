import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { EnhancedAlbum, EnhancedTrack } from '../-artist-detail.enhanced';

import { stopRedownloadProgress } from '../-artist-detail.redownload';
import { MissingTrackManageModal } from './missing-track-modals';
import { RedownloadModal } from './redownload-modal';

/**
 * The redownload 3-step modal and the missing-track flows, end to end against
 * stubbed endpoints.
 */

const TRACK = {
  id: 1,
  title: 'Xtal',
  file_path: '/music/xtal.flac',
  bitrate: 1411,
} as unknown as EnhancedTrack;
const ALBUM = { id: 7, title: 'SAW 85-92', tracks: [{ id: 1 }] } as unknown as EnhancedAlbum;
const ARTIST = { id: 42, name: 'Aphex Twin', imageUrl: 'a.jpg' };

function ndjson(lines: string[]) {
  const encoder = new TextEncoder();
  let i = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (i < lines.length) controller.enqueue(encoder.encode(lines[i++]));
        else controller.close();
      },
    }),
  );
}

afterEach(() => {
  stopRedownloadProgress();
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.openAddToWishlistModal;
  cleanup();
});

describe('RedownloadModal', () => {
  it('walks metadata → streamed sources → start, with the vanilla payloads', async () => {
    const calls: { url: string; body: Record<string, unknown> | null }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
        if (url.endsWith('search-metadata')) {
          return new Response(
            JSON.stringify({
              success: true,
              best_match: { source: 'itunes' },
              current_track: { thumb_url: 'cover.jpg' },
              metadata_results: {
                spotify: [{ name: 'Xtal', artist: 'Aphex Twin', match_score: 0.8 }],
                itunes: [
                  { name: 'Xtal', artist: 'Aphex Twin', match_score: 0.97, is_current_match: true },
                ],
              },
            }),
          );
        }
        if (url.endsWith('search-sources')) {
          return ndjson([
            '{"source":"soulseek","candidates":[{"display_name":"xtal.flac","confidence":0.95,"username":"peer1","size_display":"30 MB","source_service":"soulseek"}]}\n',
            '{"source":"youtube","candidates":[{"display_name":"xtal.opus","confidence":0.6,"blacklisted":true,"size_display":"5 MB"}]}\n',
          ]);
        }
        return new Response(JSON.stringify({ success: true, task_id: 'task-9' }));
      }),
    );
    render(
      <RedownloadModal
        track={TRACK}
        album={ALBUM}
        artistName="Aphex Twin"
        onReload={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText('Redownload Track')).toBeTruthy();
    // The best-match source's first result arrives pre-selected.
    await screen.findByText('Apple Music');
    const checked = document.querySelector(
      'input[name="metadata-choice"]:checked',
    ) as HTMLInputElement;
    expect(checked.value).toBe('itunes|0');
    expect(screen.getByText('current')).toBeTruthy();

    fireEvent.click(screen.getByText('Search Download Sources →'));
    await screen.findByText('Soulseek');
    await screen.findByText('YouTube');
    // The chosen metadata went out with its source stamped on it.
    const sources = calls.find((c) => c.url.endsWith('search-sources'));
    expect(sources?.body?.metadata).toMatchObject({ _source: 'itunes', match_score: 0.97 });
    // Best (non-blacklisted) candidate is recommended + selected.
    expect(screen.getByText('Best')).toBeTruthy();
    expect(screen.getByText('Blacklisted')).toBeTruthy();
    const start = document.getElementById('redownload-start-btn') as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));

    fireEvent.click(start);
    await screen.findByText('Downloading: xtal.flac');
    expect(document.querySelector('.redownload-progress-from')?.textContent).toBe('from peer1');
    const startCall = calls.find((c) => c.url.endsWith('/redownload/start'));
    expect(startCall?.body).toMatchObject({
      delete_old_file: true,
      candidate: { display_name: 'xtal.flac' },
      metadata: { _source: 'itunes' },
    });
  });

  it('reports a metadata search failure in place', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (_i: RequestInfo | URL, _init?: RequestInit) =>
          new Response(JSON.stringify({ success: false, error: 'no sources configured' })),
      ),
    );
    render(
      <RedownloadModal
        track={TRACK}
        album={ALBUM}
        artistName="Aphex Twin"
        onReload={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    await screen.findByText('Error: no sources configured');
  });
});

describe('MissingTrackManageModal', () => {
  const MISSING = {
    id: 'm1',
    title: 'Green Calx',
    track_number: 8,
    _missingExpected: true,
    _hasActionableContext: true,
  } as unknown as EnhancedTrack;

  it('"Add to Library" routes into the shared wishlist modal and closes', async () => {
    const open = vi.fn(async () => {});
    window.openAddToWishlistModal = open as never;
    const onClose = vi.fn();
    render(
      <MissingTrackManageModal
        track={MISSING}
        album={ALBUM}
        artist={ARTIST}
        onImported={vi.fn()}
        onClose={onClose}
      />,
    );
    expect(screen.getByText('#8 Green Calx')).toBeTruthy();
    fireEvent.click(screen.getByText('Add to Library'));
    expect(onClose).toHaveBeenCalled();
    await waitFor(() => expect(open).toHaveBeenCalledTimes(1));
  });

  it('"I Have This" searches the library, imports the pick, and reports back', async () => {
    const calls: { url: string; body: Record<string, unknown> | null }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
        if (url.includes('search-tracks')) {
          return new Response(
            JSON.stringify({
              success: true,
              tracks: [
                {
                  id: 55,
                  title: 'Green Calx (Live)',
                  artist_name: 'Aphex Twin',
                  album_title: 'Peel Session',
                  file_path: '/music/green-calx.flac',
                },
              ],
            }),
          );
        }
        return new Response(
          JSON.stringify({ success: true, updated_data: { success: true, marker: 1 } }),
        );
      }),
    );
    window.showToast = vi.fn() as never;
    const onImported = vi.fn();
    render(
      <MissingTrackManageModal
        track={MISSING}
        album={ALBUM}
        artist={ARTIST}
        onImported={onImported}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('I Have This'));
    // Auto-search seeded "title artist".
    expect(calls[0].url).toContain('q=Green%20Calx%20Aphex%20Twin');
    await screen.findByText('Green Calx (Live)');

    const confirm = document.getElementById('enhanced-have-confirm') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(screen.getByText('Green Calx (Live)'));
    expect(confirm.disabled).toBe(false);

    fireEvent.click(confirm);
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Track imported. Original file was left untouched.',
        'success',
      ),
    );
    const importCall = calls.find((c) => c.url.endsWith('import-existing-track'));
    expect(importCall?.body?.source_track_id).toBe('55');
    expect(onImported).toHaveBeenCalledWith(expect.objectContaining({ marker: 1 }));
  });
});
