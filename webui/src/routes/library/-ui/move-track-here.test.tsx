import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { candidateQuality, tagValue } from '../-library-v2.slot-move';
import { lib2Track } from '../-library-v2.test-fixtures';
import pageStyles from './library-v2-page.module.css';
import { isIdTag } from './move-track-here-modal';
import modalCss from './move-track-here-modal.module.css?raw';
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

function tagRows(panel: HTMLElement): string[][] {
  const table = within(panel).getByRole('table', { name: 'Tags that change' });
  return [...table.querySelectorAll('tbody tr')].map((row) =>
    [...row.children].map((cell) => cell.textContent ?? ''),
  );
}

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
    expect(within(panel).getByTestId('move-track-file')).toHaveTextContent(
      'From' + preview.path.current + 'To' + preview.path.new,
    );
    expect(tagRows(panel)).toEqual([
      ['Title', 'Punkrocker (radio edit)→Punkrocker'],
      ['Album', 'Punkrocker (radio edit)→The Crabjoys'],
    ]);
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

// #2241: the dialog mounted inside the album table's actions cell and took
// its right align and nowrap and the missing row's italic, and the What
// happens block used a class no sheet defined. long lines ran off the edge.
describe('move a track here layout (#2241)', () => {
  const longPath =
    '/app/Transfer/Compilations/Mercury - Acts 1 & 2/Disc 1/01 - Enemy (from the series Arcane League of Legends).flac';
  const withIds = {
    ...preview,
    tags: {
      changes: [
        ...preview.tags.changes,
        { field: 'DEEZER TRACK ID', from: '919319', to: '3984403641' },
        { field: 'MUSICBRAINZ RELEASE ID', from: '9760e4be', to: 'b81c17a4' },
      ],
    },
  };

  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
    server.use(
      http.get('/api/library/v2/songs', () =>
        HttpResponse.json({ success: true, songs: [{ ...song, file_path: longPath }] }),
      ),
      http.post('/api/library/v2/tracks/90/move-into/preview', () => HttpResponse.json(withIds)),
    );
  });
  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  function openInTable() {
    const track = lib2Track({ id: 90, title: 'Punkrocker', track_number: 32, file: null });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <table>
          <tbody>
            <tr className={pageStyles.missingRow}>
              <td className={pageStyles.trackActions} data-testid="actions-cell">
                <OwnedTrackMoreMenu
                  track={track}
                  albumId={50}
                  albumTitle="The Crabjoys"
                  artistName="OST"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More for Punkrocker' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Move a track here…' }));
  }

  it('renders outside the album table, so the row and cell styles cannot reach it', async () => {
    openInTable();
    const dialog = await screen.findByRole('dialog', { name: 'Move a track here' });
    expect(dialog.closest('td, tr, table')).toBeNull();
    expect(screen.getByTestId('actions-cell')).not.toContainElement(dialog);
  });

  it('sets its own left aligned, upright, wrapping text', async () => {
    openInTable();
    const dialog = await screen.findByRole('dialog', { name: 'Move a track here' });
    const style = getComputedStyle(dialog);
    expect(style.textAlign).toBe('left');
    expect(style.fontStyle).toBe('normal');
    expect(style.whiteSpace).toBe('normal');
    // and the sheet says so, whatever jsdom computes
    const block = modalCss.match(/\.dialog\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(block).toMatch(/text-align:\s*left/);
    expect(block).toMatch(/font-style:\s*normal/);
    expect(block).toMatch(/white-space:\s*normal/);
  });

  it('cuts long paths and titles with an ellipsis and keeps the full text in a tooltip', async () => {
    openInTable();
    const path = await screen.findByText(longPath);
    expect(path).toHaveAttribute('title', longPath);
    for (const el of [path, screen.getByText(song.title)]) {
      const style = getComputedStyle(el);
      expect(style.overflow).toBe('hidden');
      expect(style.textOverflow).toBe('ellipsis');
      expect(style.whiteSpace).toBe('nowrap');
    }
    // the move's From and To wrap instead of running off the edge
    expect(modalCss).toMatch(/\.move dd\s*\{[^}]*overflow-wrap:\s*anywhere/);
    expect(modalCss).toMatch(/\.tags td\s*\{[^}]*overflow-wrap:\s*anywhere/);
  });

  it('every class it uses is defined, so nothing falls back to a parent', async () => {
    openInTable();
    fireEvent.click(await screen.findByText(longPath));
    const dialog = await screen.findByRole('dialog', { name: 'Move a track here' });
    await within(dialog).findByTestId('move-track-preview');
    const used = new Set(
      [dialog, ...dialog.querySelectorAll('*')].flatMap((el) => [...el.classList]),
    );
    const globals = new Set(['btn', 'btn--sm', 'btn--primary', 'btn--secondary']);
    for (const name of used) {
      if (globals.has(name)) continue;
      // css modules hash each class as _<name>_<hash>
      const local = /^_([A-Za-z]+)_[a-z0-9]+$/.exec(name)?.[1];
      expect(local, `class ${name} is not from the dialog's module`).toBeTruthy();
      expect(modalCss).toMatch(new RegExp(`\\.${local}\\b`));
    }
  });

  it('shows the tag changes as a table and keeps the ids behind "and N ids"', async () => {
    openInTable();
    fireEvent.click(await screen.findByText(longPath));
    const panel = await screen.findByTestId('move-track-preview');
    expect(tagRows(panel).map(([field]) => field)).toEqual(['Title', 'Album']);
    fireEvent.click(within(panel).getByRole('button', { name: 'and 2 ids' }));
    expect(tagRows(panel).map(([field]) => field)).toEqual([
      'Title',
      'Album',
      'DEEZER TRACK ID',
      'MUSICBRAINZ RELEASE ID',
    ]);
    fireEvent.click(within(panel).getByRole('button', { name: 'Hide the ids' }));
    expect(tagRows(panel)).toHaveLength(2);
  });

  it('tells an id tag from a named one', () => {
    expect(isIdTag('MUSICBRAINZ ARTIST ID')).toBe(true);
    expect(isIdTag('Deezer track id')).toBe(true);
    expect(isIdTag('Title')).toBe(false);
    expect(isIdTag('Track #')).toBe(false);
    expect(isIdTag('Lyrics')).toBe(false);
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
