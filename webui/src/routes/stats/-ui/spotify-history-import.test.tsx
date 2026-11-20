import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { SpotifyHistoryImport } from './spotify-history-import';

const NOTHING = {
  success: true,
  imported: false,
  include_in_stats: true,
  plays: 0,
  first_played: null,
  last_played: null,
  artists: 0,
  matched: 0,
  linked: 0,
  max_batch: 5000,
};

const IMPORTED = {
  ...NOTHING,
  imported: true,
  plays: 2,
  first_played: '2015-06-01 10:00:00',
  last_played: '2024-06-01 10:00:00',
  artists: 1,
  matched: 1,
  linked: 1,
};

const PLAYS = [
  {
    ts: '2024-01-15T08:23:45Z',
    ms_played: 180000,
    master_metadata_track_name: 'Song',
    master_metadata_album_artist_name: 'Band',
    master_metadata_album_album_name: 'Album',
    spotify_track_uri: 'spotify:track:4uLU6hMCjMI75M1A2tKUQC',
    incognito_mode: false,
  },
  {
    ts: '2024-01-15T09:00:00Z',
    ms_played: 200000,
    master_metadata_track_name: 'Secret',
    master_metadata_album_artist_name: 'Band',
    incognito_mode: true,
  },
  {
    ts: '2024-01-15T10:00:00Z',
    ms_played: 5000,
    master_metadata_track_name: 'Skipped',
    master_metadata_album_artist_name: 'Band',
  },
];

function renderImport() {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <SpotifyHistoryImport onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

afterEach(() => {
  delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
});

describe('SpotifyHistoryImport', () => {
  it('sends only the music plays, then says what came in', async () => {
    const sent: unknown[] = [];
    server.use(
      http.get('/api/listening-import/spotify', () => HttpResponse.json(NOTHING)),
      http.post('/api/listening-import/spotify/batch', async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({ success: true, received: 1, kept: 1, added: 1, merged: 0 });
      }),
      http.post('/api/listening-import/spotify/finish', () => HttpResponse.json(IMPORTED)),
    );
    renderImport();

    const input = screen.getByLabelText(/drop the zip or json files here/i);
    fireEvent.change(input, {
      target: { files: [new File([JSON.stringify(PLAYS)], 'Streaming_History_Audio_2024.json')] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));

    expect(
      await screen.findByText('2 plays from 2015 to 2024, 1 artist, 1 matched to your library'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Left out: 1 play under 30 seconds, 1 private session play/),
    ).toBeVisible();
    expect(sent).toEqual([
      {
        include_in_stats: true,
        rows: [
          {
            ended_at: '2024-01-15T08:23:45Z',
            ms_played: 180000,
            track: 'Song',
            artist: 'Band',
            album: 'Album',
            uri: 'spotify:track:4uLU6hMCjMI75M1A2tKUQC',
          },
        ],
      },
    ]);
  });

  it('taste only goes with the import', async () => {
    const sent: { include_in_stats: boolean }[] = [];
    server.use(
      http.get('/api/listening-import/spotify', () => HttpResponse.json(NOTHING)),
      http.post('/api/listening-import/spotify/batch', async ({ request }) => {
        sent.push((await request.json()) as { include_in_stats: boolean });
        return HttpResponse.json({ success: true, received: 1, kept: 1, added: 1, merged: 0 });
      }),
      http.post('/api/listening-import/spotify/finish', () =>
        HttpResponse.json({ ...IMPORTED, include_in_stats: false }),
      ),
    );
    renderImport();

    fireEvent.click(screen.getByRole('checkbox', { name: /count these plays in stats/i }));
    fireEvent.change(screen.getByLabelText(/drop the zip or json files here/i), {
      target: { files: [new File([JSON.stringify(PLAYS)], 'endsong_0.json')] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));

    await screen.findByText(/matched to your library/);
    expect(sent[0].include_in_stats).toBe(false);
  });

  it('a file with no music plays sends nothing', async () => {
    const batch = vi.fn();
    server.use(
      http.get('/api/listening-import/spotify', () => HttpResponse.json(NOTHING)),
      http.post('/api/listening-import/spotify/batch', () => {
        batch();
        return HttpResponse.json({ success: true });
      }),
    );
    renderImport();

    fireEvent.change(screen.getByLabelText(/drop the zip or json files here/i), {
      target: { files: [new File([JSON.stringify([PLAYS[1]])], 'endsong_0.json')] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/No music plays to import/);
    expect(batch).not.toHaveBeenCalled();
  });

  it('removes the import only after the confirm', async () => {
    const removed = vi.fn();
    server.use(
      http.get('/api/listening-import/spotify', () => HttpResponse.json(IMPORTED)),
      http.delete('/api/listening-import/spotify', () => {
        removed();
        return HttpResponse.json({ success: true, removed: 2 });
      }),
    );
    const ask = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    (window as { showConfirmDialog?: unknown }).showConfirmDialog = ask;
    renderImport();

    const button = await screen.findByRole('button', { name: 'Remove this import' });
    fireEvent.click(button);
    await waitFor(() => expect(ask).toHaveBeenCalledTimes(1));
    expect(removed).not.toHaveBeenCalled();

    fireEvent.click(button);
    await waitFor(() => expect(removed).toHaveBeenCalledTimes(1));
    expect(ask.mock.calls[1][0]).toMatchObject({ destructive: true, confirmText: 'Remove' });
  });

  it('switching taste only on an existing import saves it right away', async () => {
    const saved: unknown[] = [];
    server.use(
      http.get('/api/listening-import/spotify', () => HttpResponse.json(IMPORTED)),
      http.post('/api/listening-import/spotify/settings', async ({ request }) => {
        saved.push(await request.json());
        return HttpResponse.json({ ...IMPORTED, include_in_stats: false });
      }),
    );
    renderImport();

    await screen.findByText(/matched to your library/);
    fireEvent.click(screen.getByRole('checkbox', { name: /count these plays in stats/i }));
    await waitFor(() => expect(saved).toEqual([{ include_in_stats: false }]));
  });
});
