import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import {
  type UseFileTagsPlan,
  UseFileTagsModal,
  describeApplied,
  releaseConfirmMessage,
  releaseLibraryFileTagValues,
} from './use-file-tags-modal';

/**
 * #2070: the Library takes what the files themselves say. The window always
 * shows what would change before anything is saved.
 */

const PLAN: UseFileTagsPlan = {
  success: true,
  tracks: [
    {
      track_id: 1,
      title: 'Pink',
      album_title: 'The Very Best of Aerosmith',
      changes: [{ field: 'track_number', label: 'Track #', library: 1, file: 7 }],
      move: {
        album_title: 'Nine Lives',
        album_changes: [{ field: 'year', label: 'Year', library: null, file: 1997 }],
      },
      notes: [],
    },
    {
      track_id: 2,
      title: 'Dream On',
      album_title: 'The Very Best of Aerosmith',
      changes: [
        { field: 'title', label: 'Title', library: 'Dream On', file: 'Dream On (Remaster)' },
      ],
      move: null,
      notes: ["The file's album artist is Various Artists; the Library keeps Aerosmith."],
    },
  ],
  albums: [
    {
      album_id: 9,
      title: 'The Very Best of Aerosmith',
      rename: null,
      changes: [{ field: 'year', label: 'Year', library: 2022, file: 2005 }],
    },
  ],
  counts: { tracks: 2, changed_tracks: 2, moves: 1, albums: 1, unreadable: 1 },
};

function mount(onClose = vi.fn()) {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <UseFileTagsModal
        entity="albums"
        id={9}
        title="The Very Best of Aerosmith"
        onClose={onClose}
      />
    </QueryClientProvider>,
  );
  return onClose;
}

afterEach(() => {
  delete window.showToast;
  delete window.showConfirmDialog;
});

describe('UseFileTagsModal', () => {
  it('shows the album change, the move and the track change before saving anything', async () => {
    let posted = 0;
    server.use(
      http.get('/api/library/v2/albums/9/use-file-tags', () => HttpResponse.json(PLAN)),
      http.post('/api/library/v2/albums/9/use-file-tags', () => {
        posted += 1;
        return HttpResponse.json({});
      }),
    );
    mount();

    expect(await screen.findByText('1 track moves to the album its tag names')).toBeInTheDocument();
    expect(screen.getByText('Nine Lives')).toBeInTheDocument();
    expect(screen.getByText('2005')).toBeInTheDocument();
    expect(screen.getByText('Dream On (Remaster)')).toBeInTheDocument();
    // what it will not touch is said, not hidden
    expect(screen.getByText(/album artist is Various Artists/)).toBeInTheDocument();
    expect(screen.getByText(/1 file could not be read/)).toBeInTheDocument();
    expect(posted).toBe(0);
  });

  it('saves on the button, says what was done, and closes', async () => {
    window.showToast = vi.fn();
    server.use(
      http.get('/api/library/v2/albums/9/use-file-tags', () => HttpResponse.json(PLAN)),
      http.post('/api/library/v2/albums/9/use-file-tags', () =>
        HttpResponse.json({
          success: true,
          tracks_changed: 1,
          albums_changed: 1,
          albums_renamed: 0,
          moved: 1,
          albums_created: 1,
          failed: [],
        }),
      ),
    );
    const onClose = mount();

    // the button is there while the files are still being read, but off
    const button = await screen.findByRole('button', { name: 'Use these tags' });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    await waitFor(() => expect(window.showToast).toHaveBeenCalled());
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(window.showToast).toHaveBeenCalledWith(
      '1 album updated, 1 track updated, 1 track moved to another album',
      'success',
    );
  });

  it('says so when the Library already matches, and offers nothing to save', async () => {
    server.use(
      http.get('/api/library/v2/albums/9/use-file-tags', () =>
        HttpResponse.json({
          success: true,
          tracks: [],
          albums: [],
          counts: { tracks: 3, changed_tracks: 0, moves: 0, albums: 0, unreadable: 0 },
        }),
      ),
    );
    mount();

    expect(
      await screen.findByText(/The Library already says what these files say/),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Use these tags' })).not.toBeInTheDocument();
    // the header's close, and the footer's in place of Cancel
    expect(screen.getAllByRole('button', { name: 'Close' })).toHaveLength(2);
  });
});

describe('describeApplied', () => {
  it('reports a failed move beside what worked', () => {
    expect(
      describeApplied({
        tracks_changed: 0,
        albums_changed: 0,
        albums_renamed: 1,
        moved: 2,
        albums_created: 0,
        failed: [{ track_id: 1, title: 'x', error: 'no' }],
      }),
    ).toBe('1 album updated, 2 tracks moved to another album. 1 move failed');
  });

  it('says when there was nothing to do', () => {
    expect(
      describeApplied({
        tracks_changed: 0,
        albums_changed: 0,
        albums_renamed: 0,
        moved: 0,
        albums_created: 0,
        failed: [],
      }),
    ).toBe('Nothing needed changing');
  });
});

/**
 * #2084 (wolf39us): values taken from file tags are kept as edits, and there
 * was no way to let go of them again short of clearing each field by hand.
 */
describe('releasing file tag values', () => {
  const held = (n: number): UseFileTagsPlan => ({
    ...PLAN,
    counts: { ...PLAN.counts, held: n },
  });

  it('says nothing about held values when there are none', async () => {
    server.use(http.get('/api/library/v2/albums/9/use-file-tags', () => HttpResponse.json(PLAN)));
    mount();
    await screen.findByText('1 track moves to the album its tag names');
    expect(screen.queryByRole('button', { name: 'Release' })).toBeNull();
  });

  it('counts what is held and releases it only after a yes', async () => {
    let released = 0;
    server.use(
      http.get('/api/library/v2/albums/9/use-file-tags', () => HttpResponse.json(held(3))),
      http.delete('/api/library/v2/albums/9/use-file-tags', () => {
        released += 1;
        return HttpResponse.json({ success: true, released: 3 });
      }),
    );
    const toast = vi.fn();
    window.showToast = toast;
    let answer = false;
    const confirm = vi.fn(async () => answer);
    window.showConfirmDialog = confirm as never;
    const onClose = mount();

    expect(await screen.findByText(/3 values here were already taken from file tags/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Release' }));
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(released).toBe(0);
    expect(onClose).not.toHaveBeenCalled();

    answer = true;
    fireEvent.click(screen.getByRole('button', { name: 'Release' }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('3 values released', 'success'));
    expect(released).toBe(1);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('the confirm says what stays: hand edits and the files', () => {
    const message = releaseConfirmMessage(1, 'your library');
    expect(message).toContain('1 value in your library was taken from file tags');
    expect(message).toContain('Edits you typed in yourself stay, and no file is changed');
    expect(releaseConfirmMessage(2, 'Nine Lives')).toContain('2 values in Nine Lives were taken');
  });

  it('the library-wide release says so when nothing is held, and asks before it deletes', async () => {
    let heldNow = 0;
    let released = 0;
    server.use(
      http.get('/api/library/v2/use-file-tags', () =>
        HttpResponse.json({ success: true, held: heldNow }),
      ),
      http.delete('/api/library/v2/use-file-tags', () => {
        released += 1;
        return HttpResponse.json({ success: true, released: heldNow });
      }),
    );
    const toast = vi.fn();
    window.showToast = toast;
    let answer = false;
    window.showConfirmDialog = vi.fn(async () => answer) as never;
    const queryClient = createTestQueryClient();

    await releaseLibraryFileTagValues(queryClient);
    expect(toast).toHaveBeenCalledWith('Nothing in your library is held from file tags', 'info');
    expect(window.showConfirmDialog).not.toHaveBeenCalled();

    heldNow = 1204;
    await releaseLibraryFileTagValues(queryClient);
    expect(window.showConfirmDialog).toHaveBeenCalledTimes(1);
    expect(released).toBe(0);

    answer = true;
    await releaseLibraryFileTagValues(queryClient);
    expect(released).toBe(1);
    expect(toast).toHaveBeenCalledWith('1,204 values released', 'success');
  });
});
