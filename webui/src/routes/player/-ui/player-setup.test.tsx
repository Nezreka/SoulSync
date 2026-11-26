/**
 * player setup: it shows itself to an admin until it has been done, never
 * to anyone else, and what it saves is the libraries as folders.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state: { payload: unknown; saved: unknown } = { payload: null, saved: null };

vi.mock('@/app/api-client', () => ({
  apiClient: {
    get: () => ({ json: async () => state.payload }),
    put: (_url: string, opts: { json: unknown }) => {
      state.saved = opts.json;
      return Promise.resolve({});
    },
  },
}));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));

const admin = (done: boolean) => ({
  done,
  server_name: '',
  is_admin: true,
  sidebar_order: [],
  sidebar_hidden: [],
  libraries: [
    { id: 1, kind: 'movie', name: 'Movies', is_default: true, folders: [] },
    { id: 2, kind: 'show', name: 'TV Shows', is_default: true, folders: [] },
  ],
  folders: {
    movie: [
      { folder: '/media/movies', path: '/media/movies', titles: 40, sample: [], library_id: 1 },
      { folder: '/media/kids', path: '/media/kids', titles: 9, sample: [], library_id: 1 },
    ],
    show: [{ folder: '/media/tv', path: '/media/tv', titles: 12, sample: [], library_id: 2 }],
  },
  hidden: {},
  profiles: [
    { id: 1, name: 'Boss', is_admin: true },
    { id: 3, name: 'Kid', is_admin: false },
  ],
});

async function mount() {
  const { PlayerSetupHost } = await import('./player-setup');
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <PlayerSetupHost />
    </QueryClientProvider>,
  );
}
const next = () => fireEvent.click(screen.getByRole('button', { name: /^Next/ }));

afterEach(() => {
  state.payload = null;
  state.saved = null;
});

describe('player setup', () => {
  it('opens for an admin when it has not been done, with a one-press default', async () => {
    state.payload = admin(false);
    await mount();
    await waitFor(() => expect(screen.getByText('Let’s set up your player')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Use the defaults' }));
    await waitFor(() => expect(state.saved).toEqual({ done: true }));
  });

  it('stays away once done, and from anyone who is not an admin', async () => {
    state.payload = admin(true);
    await mount();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole('dialog')).toBeNull();
    state.payload = { done: false, server_name: '', is_admin: false };
    await mount();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('saves a new library with the folder moved into it, hidden from who was unticked', async () => {
    state.payload = admin(false);
    await mount();
    await waitFor(() => expect(screen.getByText('Let’s set up your player')).toBeTruthy());
    fireEvent.change(screen.getByPlaceholderText('SoulSync'), { target: { value: 'The Den' } });
    next(); // movies
    fireEvent.click(screen.getByRole('button', { name: 'Add a library' }));
    const names = screen.getAllByLabelText('Library name') as HTMLInputElement[];
    fireEvent.change(names[1], { target: { value: 'Kids' } });
    const pick = screen.getByLabelText('Library for /media/kids') as HTMLSelectElement;
    const kids = [...pick.options].find((o) => o.textContent === 'Kids')!;
    fireEvent.change(pick, { target: { value: kids.value } });
    next(); // tv
    next(); // people
    fireEvent.click(screen.getByLabelText('Kid can see Movies'));
    next(); // sidebar
    fireEvent.click(screen.getByLabelText('Podcasts is in the sidebar'));
    next(); // done
    fireEvent.click(screen.getByRole('button', { name: 'Finish' }));
    await waitFor(() => expect(state.saved).not.toBeNull());
    expect(state.saved).toEqual({
      done: true,
      server_name: 'The Den',
      sidebar_order: [],
      sidebar_hidden: ['/player/podcasts'],
      libraries: [
        { id: 1, kind: 'movie', name: 'Movies', folders: [] },
        { id: 2, kind: 'show', name: 'TV Shows', folders: [] },
        { kind: 'movie', name: 'Kids', folders: ['/media/kids'] },
      ],
      hidden: { '1': [3], '2': [], 'new:2': [] },
    });
  });

  it('lists a library made in setup on the sidebar step, and saves where it was put', async () => {
    state.payload = admin(false);
    await mount();
    await waitFor(() => expect(screen.getByText('Let’s set up your player')).toBeTruthy());
    next(); // movies
    fireEvent.click(screen.getByRole('button', { name: 'Add a library' }));
    const names = screen.getAllByLabelText('Library name') as HTMLInputElement[];
    fireEvent.change(names[1], { target: { value: 'Cam Movies' } });
    next(); // tv
    next(); // people
    next(); // sidebar
    // it is there, by its name, and can be switched off like any other place
    fireEvent.click(screen.getByLabelText('Cam Movies is in the sidebar'));
    next(); // done
    fireEvent.click(screen.getByRole('button', { name: 'Finish' }));
    await waitFor(() => expect(state.saved).not.toBeNull());
    const saved = state.saved as { sidebar_hidden: string[]; libraries: { name: string }[] };
    // named by its place in the libraries list, which the server turns into its id
    expect(saved.libraries[2].name).toBe('Cam Movies');
    expect(saved.sidebar_hidden).toEqual(['lib:new:2']);
  });
});

describe('the setup screen itself', () => {
  it('moves a folder by dropping it on Add a library, naming the library after it', async () => {
    state.payload = admin(false);
    await mount();
    await waitFor(() => expect(screen.getByText('Let’s set up your player')).toBeTruthy());
    next(); // movies
    const kids = screen.getByTitle('/media/kids').closest('[draggable]')!;
    const transfer = { setData: () => {}, effectAllowed: '' };
    fireEvent.dragStart(kids, { dataTransfer: transfer });
    const add = screen.getByRole('button', { name: 'Add a library' });
    fireEvent.dragOver(add, { dataTransfer: transfer });
    fireEvent.drop(add, { dataTransfer: transfer });
    const names = screen.getAllByLabelText('Library name') as HTMLInputElement[];
    expect(names.map((n) => n.value)).toEqual(['Movies', 'Kids']);
    expect(
      (screen.getByLabelText('Library for /media/kids') as HTMLSelectElement).selectedOptions[0]
        .textContent,
    ).toBe('Kids');
  });

  it('previews the sidebar a person gets', async () => {
    state.payload = admin(false);
    await mount();
    await waitFor(() => expect(screen.getByText('Let’s set up your player')).toBeTruthy());
    next(); // movies
    next(); // tv
    next(); // people
    fireEvent.click(screen.getByLabelText('Kid can see TV Shows'));
    const preview = screen.getByRole('complementary', { name: 'Live preview' });
    const within = (text: string) =>
      [...preview.querySelectorAll('div')].some((d) => d.textContent === text);
    expect(within('TV')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Kid' }));
    expect(within('TV')).toBe(false);
    expect(within('Movies')).toBe(true);
  });
});

describe('a folder path, split for reading', () => {
  it('a network share: the server, then the folders', async () => {
    const { splitFolderPath } = await import('./player-setup');
    expect(splitFolderPath('\\\\192.168.86.36\\plex_20tb_2_share\\PLEX\\4K MOVIES')).toEqual({
      root: '192.168.86.36',
      parts: ['plex_20tb_2_share', 'PLEX', '4K MOVIES'],
    });
    expect(splitFolderPath('//nas/media/movies')).toEqual({
      root: 'nas',
      parts: ['media', 'movies'],
    });
  });

  it('a drive, a unix path, and nothing', async () => {
    const { splitFolderPath } = await import('./player-setup');
    expect(splitFolderPath('H:\\Music\\Cam')).toEqual({ root: 'H:', parts: ['Music', 'Cam'] });
    expect(splitFolderPath('/mnt/media/movies/')).toEqual({
      root: '',
      parts: ['mnt', 'media', 'movies'],
    });
    expect(splitFolderPath('')).toEqual({ root: '', parts: [] });
  });
});
