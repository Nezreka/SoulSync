/**
 * player setup: it shows itself to an admin until it has been done, never
 * to anyone else, and what it saves is the libraries as folders.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const NO_SOURCES = { server: null, libraries: [], folders: [], scan: { state: 'idle' } };
const state: { payload: unknown; saved: unknown; sources: unknown; homePuts: unknown[] } = {
  payload: null,
  saved: null,
  sources: NO_SOURCES,
  homePuts: [],
};

vi.mock('@/app/api-client', () => ({
  refusalText: async (_e: unknown, fallback: string) => fallback,
  apiClient: {
    get: (url: string) => ({
      json: async () => (url.includes('home/sources') ? state.sources : state.payload),
    }),
    put: (url: string, opts: { json: unknown }) => {
      if (url.includes('home/sources')) {
        state.homePuts.push(opts.json);
        return Object.assign(Promise.resolve({}), { json: async () => state.sources });
      }
      state.saved = opts.json;
      return Object.assign(Promise.resolve({}), { json: async () => ({}) });
    },
    post: () => ({ json: async () => ({ ok: true, message: 'Found 48 videos.', videos: 48 }) }),
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
  state.sources = NO_SOURCES;
  state.homePuts = [];
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
    fireEvent.change(names[0], { target: { value: 'Kids' } });
    const pick = screen.getByLabelText('Library for /media/kids') as HTMLSelectElement;
    const kids = [...pick.options].find((o) => o.textContent === 'Kids')!;
    fireEvent.change(pick, { target: { value: kids.value } });
    next(); // tv
    next(); // home videos
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
      home_editors: 'admins',
      libraries: [
        { id: 1, kind: 'movie', name: 'Movies', in_guide: true, folders: [] },
        { id: 2, kind: 'show', name: 'TV Shows', in_guide: true, folders: [] },
        { kind: 'movie', name: 'Kids', in_guide: true, folders: ['/media/kids'] },
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
    fireEvent.change(names[0], { target: { value: 'Cam Movies' } });
    next(); // tv
    next(); // home videos
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
    // the main library's name is fixed text; the new one is named after its folder
    expect(names.map((n) => n.value)).toEqual(['Kids']);
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
    next(); // home videos
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

describe('library names and suggestions from folder names', () => {
  it('names a library after its folder, without the word movies', async () => {
    const { libraryNameFor } = await import('./player-setup');
    expect(libraryNameFor('\\\\nas\\PLEX\\4K MOVIES', 'movie')).toBe('4K');
    expect(libraryNameFor('H:\\Media\\Kids Movies', 'movie')).toBe('Kids');
    expect(libraryNameFor('\\\\nas\\PLEX\\CAMERA', 'movie')).toBe('Camera');
    expect(libraryNameFor('/srv/Anime Series', 'show')).toBe('Anime');
    expect(libraryNameFor('/srv/MOVIES', 'movie')).toBe('Movies');
  });

  it('offers a library per kind of folder it recognises, never one that exists', async () => {
    const { librarySuggestions } = await import('./player-setup');
    const folders = [
      { folder: 'a', path: '\\\\nas\\PLEX\\4K MOVIES' },
      { folder: 'b', path: '\\\\nas\\PLEX\\CAMERA' },
      { folder: 'c', path: 'H:\\Kids Movies' },
      { folder: 'd', path: '\\\\nas\\PLEX\\MOVIES' },
    ];
    const ideas = librarySuggestions('movie', folders, ['Movies']);
    // home videos are their own kind, never a movie library ("camera" and
    // "cam" rips are not home videos either)
    expect(ideas.map((i) => [i.name, i.folders])).toEqual([
      ['4K', ['a']],
      ['Kids', ['c']],
    ]);
    expect(librarySuggestions('movie', folders, ['Movies', '4k']).map((i) => i.name)).not.toContain(
      '4K',
    );
    expect(librarySuggestions('show', folders, []).map((i) => i.name)).not.toContain('Home Videos');
  });
});

describe('coming back to it later', () => {
  it('Edit libraries opens a finished setup straight on the movie libraries, with save', async () => {
    state.payload = admin(true);
    const { openLibrarySetup } = await import('./player-setup');
    await mount();
    act(() => openLibrarySetup());
    expect(await screen.findByText('How should movies be grouped?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });
});

describe('home videos in setup', () => {
  const plex = (on: boolean) => ({
    server: 'plex',
    libraries: [
      { title: 'Home Videos', count: 412, on, root: 'plex:home videos', videos: on ? 412 : 0 },
    ],
    folders: [],
    scan: { state: 'idle' },
  });
  const withHome = () => {
    const base = admin(false);
    return {
      ...base,
      libraries: [
        ...base.libraries,
        {
          id: 7,
          kind: 'home',
          name: 'Home videos',
          is_default: true,
          in_guide: false,
          folders: [],
        },
      ],
      folders: {
        ...base.folders,
        home: [
          {
            folder: 'plex:home videos',
            path: 'Home Videos',
            label: 'Home Videos',
            source: 'plex',
            titles: 412,
            sample: [],
            library_id: 7,
          },
          {
            folder: '/media/family',
            path: '/media/family',
            label: 'family',
            source: 'disk',
            titles: 58,
            sample: [],
            library_id: 7,
          },
        ],
      },
    };
  };

  it('offers the server’s home video library and adds it from the wizard', async () => {
    state.payload = admin(false);
    state.sources = plex(false);
    await mount();
    await waitFor(() => expect(screen.getByText('Let’s set up your player')).toBeTruthy());
    next(); // movies
    next(); // tv
    next(); // home videos
    await waitFor(() => expect(screen.getByText('Plex has Home Videos')).toBeTruthy());
    expect(screen.getByText('412 videos')).toBeTruthy();
    // no home library yet: nothing to group
    expect(screen.queryByLabelText(/is in the guide/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add it' }));
    await waitFor(() => expect(state.homePuts).toEqual([{ home_more: ['Home Videos'] }]));
  });

  it('adds a folder once its check says it can be read', async () => {
    state.payload = admin(false);
    await mount();
    await waitFor(() => expect(screen.getByText('Let’s set up your player')).toBeTruthy());
    next();
    next();
    next(); // home videos
    const add = await screen.findByRole('button', { name: 'Add' });
    expect((add as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('A folder of home videos'), {
      target: { value: '/media/family' },
    });
    await waitFor(() => expect(screen.getByText('Found 48 videos.')).toBeTruthy(), {
      timeout: 2000,
    });
    fireEvent.click(add);
    await waitFor(() => expect(state.homePuts).toEqual([{ folders: ['/media/family'] }]));
  });

  it('groups home videos like the others: out of the guide, renamable, in who sees what', async () => {
    state.payload = withHome();
    state.sources = plex(true);
    await mount();
    await waitFor(() => expect(screen.getByText('Let’s set up your player')).toBeTruthy());
    next();
    next();
    next(); // home videos
    await waitFor(() => expect(screen.getByText('Added · 412 videos')).toBeTruthy());
    const guide = screen.getByLabelText('Home videos is in the guide');
    expect(guide.getAttribute('aria-pressed')).toBe('false');
    // the main home library can be renamed
    const name = screen.getAllByLabelText('Library name')[0] as HTMLInputElement;
    expect(name.value).toBe('Home videos');
    fireEvent.change(name, { target: { value: 'Our family' } });
    // the folder goes to its own library
    const pick = screen.getByLabelText('Library for /media/family') as HTMLSelectElement;
    fireEvent.change(pick, { target: { value: '+' } });
    next(); // people
    expect(screen.getByLabelText('Kid can see Our family')).toBeTruthy();
    expect(screen.getByRole('note').textContent).toContain('guests');
    next(); // sidebar
    expect(screen.getByLabelText('Family is in the sidebar')).toBeTruthy();
    next(); // done
    fireEvent.click(screen.getByRole('button', { name: 'Finish' }));
    await waitFor(() => expect(state.saved).not.toBeNull());
    const saved = state.saved as { libraries: Record<string, unknown>[] };
    expect(saved.libraries.filter((l) => l.kind === 'home')).toEqual([
      { id: 7, kind: 'home', name: 'Our family', in_guide: false, folders: [] },
      { kind: 'home', name: 'Family', in_guide: false, folders: ['/media/family'] },
    ]);
  });
});

describe('a finished setup with home videos, as a real server has it', () => {
  it('Edit libraries and Open player setup still open with a home library listed first', async () => {
    const base = admin(true);
    state.payload = {
      ...base,
      server_name: 'ThomasClan',
      libraries: [
        {
          id: 3,
          kind: 'home',
          name: 'Home videos',
          is_default: true,
          in_guide: false,
          folders: [],
        },
        ...base.libraries.map((l) => ({ ...l, in_guide: true })),
      ],
      folders: {
        movie: [
          {
            folder: '/mnt/easystore3/PLEX/Movies',
            path: '/mnt/easystore3/PLEX/Movies',
            titles: 900,
            sample: [1, 2],
            library_id: 1,
          },
          { folder: '', path: '', titles: 4, sample: [], library_id: 1 },
        ],
        show: [
          {
            folder: '/mnt/easystore3/PLEX/TV',
            path: '/mnt/easystore3/PLEX/TV',
            titles: 300,
            sample: [3],
            library_id: 2,
          },
        ],
        home: [
          {
            folder: 'plex:home videos',
            path: 'Home Videos',
            label: 'Home Videos',
            source: 'plex',
            titles: 58,
            sample: [11, 12],
            library_id: 3,
            where: '/mnt/easystore3/PLEX/Home Movies',
          },
        ],
      },
    };
    const { openLibrarySetup, openPlayerSetup } = await import('./player-setup');
    await mount();
    act(() => openLibrarySetup());
    expect(await screen.findByText('How should movies be grouped?')).toBeInTheDocument();
    // the home folder shows on its drive in the movies step
    expect(screen.getByText(/58 home videos/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    act(() => openPlayerSetup());
    expect(await screen.findByRole('button', { name: 'Close' })).toBeInTheDocument();
  });
});

describe('the folder hint knows the step it is on', () => {
  it('sends a home-like movie folder to the home videos step, and never makes it a movie library', async () => {
    const { HOME_LIKE, librarySuggestions } = await import('./player-setup');
    expect(HOME_LIKE.test('Home Movies')).toBe(true);
    expect(HOME_LIKE.test('home videos')).toBe(true);
    expect(HOME_LIKE.test('Camcorder tapes')).toBe(true);
    expect(HOME_LIKE.test('Cam Movies')).toBe(false);
    expect(HOME_LIKE.test('Homeland')).toBe(false);
    const folders = [{ folder: 'h', path: '/mnt/easystore3/PLEX/Home Movies' }];
    expect(librarySuggestions('movie', folders, ['Movies'])).toEqual([]);

    const base = admin(false);
    state.payload = {
      ...base,
      folders: {
        ...base.folders,
        movie: [
          ...base.folders.movie,
          {
            folder: '/mnt/e3/Home Movies',
            path: '/mnt/e3/Home Movies',
            titles: 5,
            sample: [],
            library_id: 1,
          },
        ],
      },
    };
    await mount();
    await waitFor(() => expect(screen.getByText('Let’s set up your player')).toBeTruthy());
    next(); // movies
    expect(screen.getByText(/looks like home/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Set up in Home videos' }));
    expect(await screen.findByText(/Your home videos/)).toBeInTheDocument();
  });
});
