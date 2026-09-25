import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { RetagModal } from './retag-modal';

describe('Library v2 retag preview', () => {
  it('groups by stable album id and labels release types even when rows are interleaved', async () => {
    const previewTrack = (trackId: number, albumId: number, albumType: string, title: string) => ({
      track_id: trackId,
      title,
      track_number: trackId,
      album_id: albumId,
      album_title: 'Shared title',
      album_type: albumType,
      file_path: `/music/${trackId}.flac`,
      diff: [{ field: 'title', file_value: 'old', db_value: title, changed: true }],
      has_changes: true,
    });

    server.use(
      http.get('/api/library/v2/artists/7/tag-preview', () =>
        HttpResponse.json({
          success: true,
          tracks: [
            previewTrack(1, 10, 'album', 'Album track one'),
            previewTrack(2, 11, 'single', 'Single track'),
            previewTrack(3, 10, 'album', 'Album track two'),
          ],
          changed_count: 3,
          truncated: false,
        }),
      ),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <RetagModal entity="artists" id={7} title="Artist" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findAllByText('Shared title')).toHaveLength(2);
    expect(screen.getByText('2 of 2 changing')).toBeInTheDocument();
    expect(screen.getByText('1 of 1 changing')).toBeInTheDocument();
    expect(screen.getByText('Album')).toBeInTheDocument();
    expect(screen.getByText('Single')).toBeInTheDocument();
  });

  it('offers a hand-set field as a choice instead of deciding for the user', async () => {
    // lib2's override layer means a title someone corrected IS the library's
    // title, so a re-tag keeps it. The preview has to SHOW that, with what the
    // catalogue wanted, or "keep mine" is a decision made behind their back.
    server.use(
      http.get('/api/library/v2/albums/5/tag-preview', () =>
        HttpResponse.json({
          success: true,
          tracks: [
            {
              track_id: 1,
              title: 'One Dance',
              track_number: 1,
              album_id: 10,
              album_title: 'Views',
              album_type: 'album',
              file_path: '/music/1.flac',
              diff: [
                {
                  field: 'Title',
                  file_value: 'One Dance',
                  db_value: 'One Dance (Radio Edit)',
                  changed: true,
                  manual: true,
                  manual_key: 'title',
                  provider_value: 'One Dance',
                },
              ],
              has_changes: true,
              has_manual_conflict: true,
            },
          ],
          changed_count: 1,
          truncated: false,
        }),
      ),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <RetagModal entity="albums" id={5} title="Views" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/set by hand/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Keep mine \(/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByText('Current file')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Use "One Dance"/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(screen.getByRole('button', { name: /Use "One Dance"/ })).toHaveTextContent(
      'Discovery / provider',
    );
  });

  it.each(['keep', 'release-title', 'keep-all'])(
    'preserves manual edits unless explicitly released (release title: %s)',
    async (choice) => {
      let body: Record<string, unknown> = {};
      server.use(
        http.get('/api/library/v2/albums/5/tag-preview', () =>
          HttpResponse.json({
            success: true,
            tracks: [
              {
                track_id: 1,
                title: 'One Dance',
                track_number: 1,
                album_id: 10,
                album_title: 'Views',
                album_type: 'album',
                file_path: '/music/1.flac',
                diff: [
                  {
                    field: 'Title',
                    file_value: 'a',
                    db_value: 'b',
                    changed: true,
                    manual: true,
                    manual_key: 'title',
                    provider_value: 'c',
                  },
                  {
                    field: 'Album',
                    file_value: 'd',
                    db_value: 'e',
                    changed: true,
                    manual: true,
                    manual_key: 'album_title',
                    provider_value: 'f',
                  },
                ],
                has_changes: true,
                has_manual_conflict: true,
              },
            ],
            changed_count: 1,
            truncated: false,
          }),
        ),
        http.post('/api/library/v2/tags/write', async ({ request }) => {
          body = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ success: true, job_id: 'j-1' });
        }),
        http.get('/api/library/v2/jobs/status', () =>
          HttpResponse.json({
            success: true,
            running: false,
            current: 1,
            total: 1,
            result: { written: 1, skipped: 0, failed: 0 },
          }),
        ),
      );

      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <RetagModal entity="albums" id={5} title="Views" onClose={vi.fn()} />
        </QueryClientProvider>,
      );

      // Release the title only; the album title keeps the hand-set value.
      const useProvider = await screen.findByRole('button', { name: /Use "c"/ });
      if (choice !== 'keep') fireEvent.click(useProvider);
      if (choice === 'keep-all') {
        fireEvent.click(screen.getByRole('button', { name: /Use "f"/ }));
        fireEvent.click(screen.getByRole('button', { name: /Keep mine for all/ }));
        for (const button of screen.getAllByRole('button', { name: /Keep mine \(/ })) {
          expect(button).toHaveAttribute('aria-pressed', 'true');
        }
      }
      fireEvent.click(screen.getByRole('button', { name: /Write tags/ }));
      await screen.findByText(/Done: 1 written/);

      // Per (track, field), not a blanket flag: settling the title must not hand
      // the album title over with it.
      expect(body.overwrite_manual).toEqual(
        choice === 'release-title' ? [[1, 'title']] : undefined,
      );
    },
  );
});
