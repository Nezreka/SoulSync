import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { candidateQuality, tagValue } from '../-library-v2.slot-move';
import { lib2Track } from '../-library-v2.test-fixtures';
import { OwnedTrackMoreMenu } from './owned-artist-track-actions';

// #2193: a missing album track takes a track you already have. the file
// moves, nothing is copied, and the move is previewed first.

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={createTestQueryClient()}>{node}</QueryClientProvider>);
}

const song = {
  id: 41,
  track_id: 41,
  file_ids: [77],
  file_id: 77,
  title: 'Punkrocker (radio edit)',
  artist_name: 'Teddybears',
  artist_id: 3,
  album_id: 12,
  album_title: 'Punkrocker (radio edit)',
  track_number: 32,
  duration: 166000,
  bitrate: 246,
  sample_rate: 48000,
  bit_depth: null,
  format: 'MP3',
  file_path: '/music/Teddybears/Punkrocker (radio edit)/32 Punkrocker (radio edit).mp3',
  play_count: 3,
  copies: 1,
};

const preview = {
  success: true,
  source: {
    id: 41,
    title: 'Punkrocker (radio edit)',
    track_number: 32,
    disc_number: 1,
    album_id: 12,
    album_title: 'Punkrocker (radio edit)',
    album_type: 'single',
    artist_name: 'Teddybears',
    files: [{ file_id: 77, path: song.file_path, format: 'MP3' }],
  },
  target: {
    id: 90,
    title: 'Punkrocker',
    track_number: 32,
    disc_number: 1,
    album_id: 50,
    album_title: 'The Crabjoys',
    album_type: 'album',
    artist_name: 'OST',
  },
  tags: {
    changes: [
      { field: 'Title', from: 'Punkrocker (radio edit)', to: 'Punkrocker' },
      { field: 'Album', from: 'Punkrocker (radio edit)', to: 'The Crabjoys' },
    ],
  },
  path: {
    current: 'Teddybears/Punkrocker (radio edit)/32 Punkrocker (radio edit).mp3',
    new: 'OST/The Crabjoys/32 - Punkrocker.mp3',
  },
  organize: true,
  source_album_left_empty: true,
};

function openMove(trackId: number | null = 90) {
  const track = lib2Track({ id: trackId, title: 'Punkrocker', track_number: 32, file: null });
  wrap(
    <OwnedTrackMoreMenu track={track} albumId={50} albumTitle="The Crabjoys" artistName="OST" />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'More for Punkrocker' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Move a track here…' }));
}

describe('move a track here (#2193)', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
  });
  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  it('is offered on a missing row, not on a track with a file', () => {
    const owned = lib2Track({ id: 5, title: 'Roads' });
    wrap(<OwnedTrackMoreMenu track={owned} albumId={9} albumTitle="Dummy" artistName="P" />);
    fireEvent.click(screen.getByRole('button', { name: 'More for Roads' }));
    expect(screen.queryByRole('menuitem', { name: 'Move a track here…' })).not.toBeInTheDocument();
  });

  it('searches the library prefilled with the missing title and shows each candidate', async () => {
    let searched = '';
    server.use(
      http.get('/api/library/v2/songs', ({ request }) => {
        searched = new URL(request.url).searchParams.get('search') ?? '';
        return HttpResponse.json({ success: true, songs: [song] });
      }),
    );
    openMove();
    expect(await screen.findByText(song.file_path)).toBeInTheDocument();
    expect(searched).toBe('Punkrocker');
    expect(screen.getByLabelText('Search your library')).toHaveValue('Punkrocker');
    expect(screen.getByText('Teddybears · Punkrocker (radio edit)')).toBeInTheDocument();
    expect(screen.getByText('MP3 · 246 kbps')).toBeInTheDocument();
    expect(screen.getByText('2:46')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Move here' })).toBeDisabled();
  });

  it('previews what moves and which tags change, then moves on confirm', async () => {
    const posts: { url: string; body: unknown }[] = [];
    const onMove = vi.fn();
    server.use(
      http.get('/api/library/v2/songs', () => HttpResponse.json({ success: true, songs: [song] })),
      http.post('/api/library/v2/tracks/90/move-into/preview', async ({ request }) => {
        posts.push({ url: request.url, body: await request.json() });
        return HttpResponse.json(preview);
      }),
      http.post('/api/library/v2/tracks/90/move-into', async ({ request }) => {
        posts.push({ url: request.url, body: await request.json() });
        onMove();
        return HttpResponse.json({
          success: true,
          track_id: 90,
          removed_track_id: 41,
          moved_file_ids: [77],
          source_album: 'deleted',
          tags: { written: 1 },
          organize: { queued: true },
        });
      }),
    );
    openMove();
    fireEvent.click(await screen.findByText(song.file_path));
    const panel = await screen.findByTestId('move-track-preview');
    expect(panel).toHaveTextContent('OST/The Crabjoys/32 - Punkrocker.mp3');
    expect(panel).toHaveTextContent('Title: Punkrocker (radio edit) → Punkrocker');
    expect(panel).toHaveTextContent('Album: Punkrocker (radio edit) → The Crabjoys');
    expect(panel).toHaveTextContent('leaves your library');
    expect(onMove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Move here' }));
    await waitFor(() => expect(onMove).toHaveBeenCalled());
    expect(posts.map((p) => p.body)).toEqual([{ source_track_id: 41 }, { source_track_id: 41 }]);
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        expect.stringContaining('Moved "Punkrocker (radio edit)" into The Crabjoys'),
        'success',
      ),
    );
    expect(screen.queryByTestId('move-track-preview')).not.toBeInTheDocument();
  });

  it('shows a refusal from the preview and never moves', async () => {
    const onMove = vi.fn();
    server.use(
      http.get('/api/library/v2/songs', () => HttpResponse.json({ success: true, songs: [song] })),
      http.post('/api/library/v2/tracks/90/move-into/preview', () =>
        HttpResponse.json(
          { success: false, error: 'This album is locked. Unlock it to move a track' },
          { status: 423 },
        ),
      ),
      http.post('/api/library/v2/tracks/90/move-into', () => {
        onMove();
        return HttpResponse.json({ success: true });
      }),
    );
    openMove();
    fireEvent.click(await screen.findByText(song.file_path));
    expect(await screen.findByRole('alert')).toHaveTextContent('This album is locked');
    expect(screen.getByRole('button', { name: 'Move here' })).toBeDisabled();
    expect(onMove).not.toHaveBeenCalled();
  });

  it('gives a slot only the tracklist knows a row before moving into it', async () => {
    let materialized: unknown = null;
    let previewed = '';
    server.use(
      http.post('/api/library/v2/albums/50/missing-tracks/materialize', async ({ request }) => {
        materialized = await request.json();
        return HttpResponse.json({ success: true, track_id: 91, created: true });
      }),
      http.get('/api/library/v2/songs', () => HttpResponse.json({ success: true, songs: [song] })),
      http.post('/api/library/v2/tracks/:id/move-into/preview', ({ params }) => {
        previewed = String(params.id);
        return HttpResponse.json(preview);
      }),
    );
    openMove(null);
    fireEvent.click(await screen.findByText(song.file_path));
    await screen.findByTestId('move-track-preview');
    expect(materialized).toEqual(expect.objectContaining({ track_number: 32 }));
    expect(previewed).toBe('91');
  });
});

describe('slot move helpers', () => {
  it('reads quality and tag values', () => {
    expect(candidateQuality({ ...song, format: 'FLAC', bit_depth: 24, sample_rate: 96000 })).toBe(
      'FLAC · 24/96',
    );
    expect(tagValue(['a', 'b'])).toBe('a, b');
    expect(tagValue('')).toBe('—');
  });
});
