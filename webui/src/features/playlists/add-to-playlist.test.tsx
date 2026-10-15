import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  AddToPlaylistButton,
  AddToPlaylistHost,
  cleanTracks,
  closeAddToPlaylist,
  duplicateMessage,
  openAddToPlaylist,
  memberState,
  PlaylistCover,
  subjectLines,
} from './add-to-playlist';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string, method: string, body: unknown) => unknown = () => ({});
/** what the picker's tick check gets back. kept out of `calls`: the tests
 *  below count the writes, and this is a read. */
let membership: unknown = { success: true, total: 1, playlists: {} };
let membershipCalls: unknown[] = [];

const MINE = [
  { id: 4, name: 'Late night', track_count: 2 },
  { id: 9, name: 'Gym', track_count: 0 },
];

beforeEach(() => {
  calls = [];
  membership = { success: true, total: 1, playlists: {} };
  membershipCalls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      if (String(url).endsWith('/membership')) {
        membershipCalls.push(body);
        return new Response(JSON.stringify(membership));
      }
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

describe('duplicateMessage', () => {
  it('says plainly when it is the same title, and names the other spelling when not', () => {
    expect(
      duplicateMessage(
        [{ track_name: 'Alright', artist_name: 'K', existing_track_name: 'alright' }],
        'Gym',
      ),
    ).toBe('"Alright" is already in Gym.');
    expect(
      duplicateMessage(
        [{ track_name: 'Alright (Remastered)', artist_name: 'K', existing_track_name: 'Alright' }],
        'Gym',
      ),
    ).toBe('"Alright (Remastered)" looks like "Alright", which is already in Gym.');
    expect(
      duplicateMessage(
        [
          { track_name: 'A', artist_name: 'K' },
          { track_name: 'B', artist_name: 'K' },
        ],
        'Gym',
      ),
    ).toBe("2 of these songs look like they're already in Gym.");
  });
});

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
    // the header names the song and who it's by
    expect(screen.getByText('Alright')).toBeInTheDocument();
    expect(screen.getByText('Kendrick Lamar')).toBeInTheDocument();
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

describe('the playlist art in the picker', () => {
  const pl = (over: Record<string, unknown> = {}) => ({
    id: 1,
    name: 'vibes',
    track_count: 6,
    ...over,
  });
  const srcs = (c: HTMLElement) => [...c.querySelectorAll('img')].map((i) => i.getAttribute('src'));

  it('uses the collage the playlists page draws when there is no cover', () => {
    const { container } = render(
      <PlaylistCover
        playlist={pl({ cover_tiles: ['/a.jpg', '/b.jpg', '/c.jpg', '/d.jpg', '/e.jpg'] })}
      />,
    );
    expect(srcs(container)).toHaveLength(4);
    expect(srcs(container).every((s) => s?.includes('.jpg'))).toBe(true);
  });

  it('shows one cover from fewer than four tiles, never a part-filled grid', () => {
    const { container } = render(
      <PlaylistCover playlist={pl({ cover_tiles: ['/a.jpg', '/b.jpg'] })} />,
    );
    expect(srcs(container)).toHaveLength(1);
    expect(srcs(container)[0]).toContain('/a.jpg');
  });

  it('lets its own cover beat the collage', () => {
    const { container } = render(
      <PlaylistCover
        playlist={pl({
          image_url: '/own.jpg',
          cover_tiles: ['/a.jpg', '/b.jpg', '/c.jpg', '/d.jpg'],
        })}
      />,
    );
    expect(srcs(container)).toHaveLength(1);
    expect(srcs(container)[0]).toContain('/own.jpg');
  });

  it('falls to the first letter with no art, or when the art will not load', () => {
    const { container, rerender } = render(<PlaylistCover playlist={pl()} />);
    expect(container.textContent).toBe('V');
    rerender(<PlaylistCover playlist={pl({ cover_tiles: ['/a.jpg'] })} />);
    fireEvent.error(container.querySelector('img')!);
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toBe('V');
  });
});

describe('the ticks', () => {
  const LISTS = [
    { id: 1, name: 'Liked Songs', track_count: 5, liked: true },
    { id: 4, name: 'Late night', track_count: 2 },
    { id: 9, name: 'Gym', track_count: 0 },
  ];

  it('asks which playlists already have the song, once, on open', async () => {
    responder = () => ({ playlists: MINE });
    renderWithHost(<AddToPlaylistButton track={SONG} />);
    fireEvent.click(screen.getByLabelText('Add Alright to a playlist'));
    await waitFor(() => expect(membershipCalls).toHaveLength(1));
    expect(membershipCalls[0]).toEqual({ tracks: [SONG] });
  });

  it('ticks a playlist that has it, and a click there takes it out', async () => {
    membership = { success: true, total: 1, playlists: { '4': 1 } };
    responder = (url, method) =>
      method === 'GET' ? { playlists: MINE } : { success: true, removed: 1, track_count: 1 };
    renderWithHost(<AddToPlaylistButton track={SONG} />);
    fireEvent.click(screen.getByLabelText('Add Alright to a playlist'));
    await waitFor(() =>
      expect(screen.getByLabelText('Already in this playlist')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('Late night'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Removed from Late night', 'success'),
    );
    const post = calls.find((c) => c.method === 'POST');
    expect(post?.url).toBe('/api/user-playlists/4/tracks/remove');
    expect(post?.body).toEqual({ tracks: [SONG] });
  });

  it('says how many of a batch a playlist already has, and still adds', async () => {
    membership = { success: true, total: 3, playlists: { '9': 1 } };
    responder = () => ({ playlists: MINE });
    renderWithHost(
      <AddToPlaylistButton
        track={[SONG, { ...SONG, track_name: 'm.A.A.d city' }, { ...SONG, track_name: 'XXX.' }]}
      />,
    );
    fireEvent.click(screen.getByLabelText('Add to a playlist'));
    await waitFor(() => expect(screen.getByText('1 of 3 already in')).toBeInTheDocument());
    expect(screen.queryByLabelText('Already in this playlist')).toBeNull();
  });

  it('puts Liked Songs behind the heart, not in the list', async () => {
    responder = () => ({ playlists: LISTS });
    renderWithHost(<AddToPlaylistButton track={SONG} />);
    fireEvent.click(screen.getByLabelText('Add Alright to a playlist'));
    await waitFor(() => expect(screen.getByText('Late night')).toBeInTheDocument());
    expect(screen.queryByText('Liked Songs')).toBeNull();
    expect(screen.getByLabelText('Add to Liked Songs')).toHaveAttribute('aria-pressed', 'false');
  });

  it('the heart likes the song and stays open, and fills in', async () => {
    responder = (url, method) =>
      method === 'GET' ? { playlists: LISTS } : { success: true, id: 1, added: 1, track_count: 6 };
    renderWithHost(<AddToPlaylistButton track={SONG} />);
    fireEvent.click(screen.getByLabelText('Add Alright to a playlist'));
    const heart = await screen.findByLabelText('Add to Liked Songs');
    await waitFor(() => expect(heart).not.toBeDisabled());
    // the server now says Liked Songs has it
    membership = { success: true, total: 1, playlists: { '1': 1 } };
    fireEvent.click(heart);
    await waitFor(() =>
      expect(screen.getByLabelText('Remove from Liked Songs')).toHaveAttribute(
        'aria-pressed',
        'true',
      ),
    );
    expect(calls.find((c) => c.method === 'POST')?.url).toBe('/api/user-playlists/liked');
    expect(screen.getByRole('dialog', { name: 'Add to playlist' })).toBeInTheDocument();
  });

  it('a filled heart unlikes', async () => {
    membership = { success: true, total: 1, playlists: { '1': 1 } };
    responder = (url, method) =>
      method === 'GET'
        ? { playlists: LISTS }
        : { success: true, id: 1, removed: 1, track_count: 4 };
    renderWithHost(<AddToPlaylistButton track={SONG} />);
    fireEvent.click(screen.getByLabelText('Add Alright to a playlist'));
    fireEvent.click(await screen.findByLabelText('Remove from Liked Songs'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Removed from Liked Songs', 'success'),
    );
    expect(calls.find((c) => c.method === 'POST')?.url).toBe('/api/user-playlists/liked/remove');
  });

  it('memberState reads all, some and none', () => {
    const m = { total: 2, playlists: { '1': 2, '2': 1 } };
    expect(memberState(1, m)).toBe('all');
    expect(memberState(2, m)).toBe('some');
    expect(memberState(3, m)).toBe('none');
    expect(memberState(1, null)).toBe('none');
  });

  it('subjectLines names one song by its artist, a batch by its album', () => {
    expect(subjectLines([SONG])).toEqual({ title: 'Alright', by: 'Kendrick Lamar' });
    const album = [
      { ...SONG, album_name: 'TPAB' },
      { ...SONG, track_name: 'u', album_name: 'TPAB' },
    ];
    expect(subjectLines(album)).toEqual({ title: '2 songs', by: 'TPAB · Kendrick Lamar' });
    expect(subjectLines([SONG, { track_name: 'x', artist_name: 'SZA' }]).by).toBe('2 artists');
  });
});
