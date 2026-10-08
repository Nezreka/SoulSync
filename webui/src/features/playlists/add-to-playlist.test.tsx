import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  AddToPlaylistButton,
  AddToPlaylistHost,
  cleanTracks,
  closeAddToPlaylist,
  openAddToPlaylist,
} from './add-to-playlist';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string, method: string, body: unknown) => unknown = () => ({});

const MINE = [
  { id: 4, name: 'Late night', track_count: 2 },
  { id: 9, name: 'Gym', track_count: 0 },
];

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ url, method, body });
      return new Response(JSON.stringify(responder(url, method, body)));
    }),
  );
  window.showToast = vi.fn();
});

afterEach(() => {
  act(() => closeAddToPlaylist());
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.showConfirmDialog;
});

const SONG = { track_name: 'Alright', artist_name: 'Kendrick Lamar' };

function renderWithHost(ui: React.ReactNode) {
  return render(
    <>
      {ui}
      <AddToPlaylistHost />
    </>,
  );
}

describe('cleanTracks', () => {
  it('keeps rows with both an artist and a title, trimmed', () => {
    expect(
      cleanTracks([
        { track_name: ' A ', artist_name: ' X ' },
        { track_name: 'no artist', artist_name: '' },
        { track_name: '', artist_name: 'no title' },
      ]),
    ).toEqual([{ track_name: 'A', artist_name: 'X' }]);
    expect(cleanTracks(null)).toEqual([]);
  });
});

describe('AddToPlaylistButton', () => {
  it('renders nothing for a row with no artist', () => {
    const { container } = render(
      <AddToPlaylistButton track={{ track_name: 'x', artist_name: '' }} />,
    );
    expect(container.innerHTML).toBe('');
  });

  it("doesn't fire the row's own click", () => {
    responder = () => ({ playlists: MINE });
    const rowClick = vi.fn();
    renderWithHost(
      <div onClick={rowClick}>
        <AddToPlaylistButton track={SONG} />
      </div>,
    );
    fireEvent.click(screen.getByLabelText('Add Alright to a playlist'));
    expect(rowClick).not.toHaveBeenCalled();
  });
});

describe('the picker', () => {
  it('lists your playlists and adds the song to the one you pick', async () => {
    responder = (url, method) =>
      url === '/api/user-playlists' && method === 'GET'
        ? { playlists: MINE }
        : { success: true, added: 1, duplicates: [], track_count: 3 };
    renderWithHost(<AddToPlaylistButton track={SONG} />);
    fireEvent.click(screen.getByLabelText('Add Alright to a playlist'));
    expect(screen.getByRole('dialog', { name: 'Add to playlist' })).toBeInTheDocument();
    expect(screen.getByText('"Alright"')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Late night')).toBeInTheDocument());
    expect(screen.getByText('2 songs')).toBeInTheDocument();
    expect(screen.getByText('0 songs')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Late night'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Added to Late night', 'success'),
    );
    const post = calls.find((c) => c.method === 'POST');
    expect(post).toEqual({
      url: '/api/user-playlists/4/tracks',
      method: 'POST',
      body: { tracks: [SONG], allow_duplicates: false },
    });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('asks before adding a song that is already there, and only re-sends that one', async () => {
    const dupe = { track_name: 'Alright', artist_name: 'Kendrick Lamar' };
    responder = (url, method, body) => {
      if (method === 'GET') return { playlists: MINE };
      const allow = (body as { allow_duplicates?: boolean }).allow_duplicates;
      return allow
        ? { added: 1, duplicates: [], track_count: 4 }
        : { added: 1, duplicates: [dupe], track_count: 3 };
    };
    const confirm = vi.fn(async () => true);
    window.showConfirmDialog = confirm;
    act(() =>
      openAddToPlaylist([dupe, { track_name: 'DNA.', artist_name: 'Kendrick Lamar' }], null),
    );
    renderWithHost(null);
    await waitFor(() => expect(screen.getByText('Gym')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Gym'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Added 2 songs to Gym', 'success'),
    );
    expect(confirm).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Already added',
        message: '"Alright" is already in Gym.',
        confirmText: 'Add anyway',
      }),
    );
    const posts = calls.filter((c) => c.method === 'POST');
    expect(posts).toHaveLength(2);
    expect(posts[1].body).toEqual({ tracks: [dupe], allow_duplicates: true });
  });

  it('saying no to the duplicate sends nothing more and says nothing was added', async () => {
    responder = (_url, method) =>
      method === 'GET' ? { playlists: MINE } : { added: 0, duplicates: [SONG], track_count: 2 };
    window.showConfirmDialog = vi.fn(async () => false);
    renderWithHost(<AddToPlaylistButton track={SONG} />);
    fireEvent.click(screen.getByLabelText('Add Alright to a playlist'));
    await waitFor(() => expect(screen.getByText('Late night')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Late night'));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    expect(window.showToast).not.toHaveBeenCalled();
  });

  it('New playlist makes one with the song already in it', async () => {
    responder = (_url, method) =>
      method === 'GET' ? { playlists: [] } : { success: true, id: 12, added: 1 };
    renderWithHost(<AddToPlaylistButton track={SONG} />);
    fireEvent.click(screen.getByLabelText('Add Alright to a playlist'));
    await waitFor(() =>
      expect(screen.getByText('No playlists yet. Make one above.')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('New playlist'));
    fireEvent.change(screen.getByLabelText('New playlist name'), {
      target: { value: 'Road trip' },
    });
    fireEvent.click(screen.getByText('Create'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Made Road trip with "Alright" in it',
        'success',
      ),
    );
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      name: 'Road trip',
      tracks: [SONG],
    });
  });

  it('the same button again closes it, and so does escape', async () => {
    responder = () => ({ playlists: MINE });
    renderWithHost(<AddToPlaylistButton track={SONG} />);
    const button = screen.getByLabelText('Add Alright to a playlist');
    fireEvent.click(button);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(button);
    expect(screen.queryByRole('dialog')).toBeNull();

    fireEvent.click(button);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('filters by name once there are enough playlists to need it', async () => {
    const many = Array.from({ length: 7 }, (_, i) => ({
      id: i + 1,
      name: i === 3 ? 'Sunday morning' : `List ${i}`,
      track_count: 1,
    }));
    responder = () => ({ playlists: many });
    renderWithHost(<AddToPlaylistButton track={SONG} />);
    fireEvent.click(screen.getByLabelText('Add Alright to a playlist'));
    await waitFor(() => expect(screen.getByText('Sunday morning')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Find a playlist'), { target: { value: 'sun' } });
    expect(screen.getByText('Sunday morning')).toBeInTheDocument();
    expect(screen.queryByText('List 0')).toBeNull();
  });

  it('a track with nothing to identify it by says so instead of opening', () => {
    renderWithHost(null);
    act(() => openAddToPlaylist({ track_name: 'x', artist_name: '' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(window.showToast).toHaveBeenCalledWith(
      "Can't add this one, it has no artist or title",
      'error',
    );
  });
});
