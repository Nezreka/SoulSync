/**
 * The person editor (#2166-#2168, plan 8.2) against the real P0 payloads:
 * __fixtures__/permissions.json is built by core.permissions itself
 * (catalogue_payload, explain, summarize) for Maya, who follows Teen with
 * four changes under a policy that keeps 4K, Import and Sample Studio for
 * admins (scrap/pe/make_fixture.py).
 */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import type { ProfilePermissions } from './types';

import FIX from './__fixtures__/permissions.json';
import { PersonEditorHost, closePersonEditor, openPersonEditor } from './index';

type Body = Record<string, unknown>;
let puts: Body[] = [];
let profilePuts: Body[] = [];
let featurePosts: Body[] = [];
let permGets = 0;
let perms: ProfilePermissions;

const FEATURES = {
  success: true,
  features: [
    { id: 'discover', label: 'Discover', desc: 'Picks.', group: 'music', pages: ['discover'] },
    { id: 'stats', label: 'Stats', desc: 'Charts.', group: 'music', pages: ['stats'] },
  ],
  groups: [{ id: 'music', label: 'Music' }],
  presets: [
    { id: 'full', label: 'Everything', desc: 'All of it.', off: [] },
    { id: 'simple', label: 'Simple', desc: 'Less.', off: ['stats'] },
  ],
  off: [],
  set_off: [],
  locked: false,
  admin_set: false,
  follows_default: true,
  own_off: null,
  default: { off: [], locked: false },
};

function useApi(payload: ProfilePermissions = FIX.maya as unknown as ProfilePermissions) {
  perms = payload;
  server.use(
    http.get('/api/permissions/catalogue', () => HttpResponse.json(FIX.catalogue)),
    http.get('/api/permissions/templates', () => HttpResponse.json(FIX.templates)),
    http.get('/api/permissions/quality-options', () => HttpResponse.json(FIX.quality)),
    http.get('/api/profiles', () =>
      HttpResponse.json({
        success: true,
        profiles: [
          { id: 1, name: 'Boulder', is_admin: true },
          { id: 3, name: 'Sam', is_admin: true },
          { id: 7, name: 'Maya', is_admin: false, has_password: true, avatar_color: '#ec4899' },
          { id: 9, name: 'Odd', is_admin: false },
        ],
      }),
    ),
    http.get('/api/profiles/:id/permissions', () => {
      permGets += 1;
      return HttpResponse.json(perms);
    }),
    http.put('/api/profiles/:id/permissions', async ({ request }) => {
      const body = (await request.json()) as Body;
      puts.push(body);
      return HttpResponse.json({
        ...perms,
        changes: [{ id: 'movies.get', before: 'ask', after: 'download' }],
      });
    }),
    http.put('/api/profiles/:id', async ({ request }) => {
      profilePuts.push((await request.json()) as Body);
      return HttpResponse.json({ success: true });
    }),
    http.get('/api/profiles/:id/features', () => HttpResponse.json(FEATURES)),
    http.post('/api/profiles/:id/features', async ({ request }) => {
      featurePosts.push((await request.json()) as Body);
      return HttpResponse.json(FEATURES);
    }),
  );
}

async function open(id = 7, category?: Parameters<typeof openPersonEditor>[1]) {
  render(<PersonEditorHost />);
  act(() => {
    openPersonEditor(id, category);
  });
  await screen.findByRole('tablist', { name: 'Categories' });
}

const tab = (name: RegExp | string) => screen.getByRole('tab', { name });
const row = (id: string) => document.querySelector(`[data-row="${id}"]`) as HTMLElement;
const count = () => screen.getByTestId('change-count').textContent;
const footStatus = () => document.querySelector('[data-state]') as HTMLElement;
const sentence = () => screen.getByTestId('summary-sentence').textContent;

async function save() {
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(footStatus()).toHaveTextContent(/^Saved\./));
}

beforeEach(() => {
  puts = [];
  profilePuts = [];
  featurePosts = [];
  permGets = 0;
  window.SoulSyncWebShellBridge = undefined;
  useApi();
});

afterEach(() => {
  act(() => closePersonEditor());
  cleanup();
});

describe('where each value comes from', () => {
  it('marks template rows "From Teen" and changes with the template value and a reset', async () => {
    await open();
    expect(screen.getByRole('dialog', { name: /Maya/ })).toBeInTheDocument();
    expect(count()).toBe('4 changes from Teen');
    // the rail's green dots: movies, music, requests have changes
    for (const c of ['movies', 'music', 'requests']) {
      expect(document.querySelector(`[data-dot="${c}"]`)).not.toBeNull();
    }
    expect(document.querySelector('[data-dot="tv"]')).toBeNull();

    fireEvent.click(tab(/^Music/));
    const music = row('music.get');
    expect(music).toHaveAttribute('data-changed');
    expect(within(music).getByText('Changed for Maya')).toBeInTheDocument();
    expect(within(music).getByText('Teen: Ask first')).toBeInTheDocument();
    expect(
      within(music).getByRole('button', { name: 'Reset Getting music to Teen' }),
    ).toBeInTheDocument();
    expect(within(row('music.hide_explicit')).getByText('From Teen')).toBeInTheDocument();
  });

  it('shows the policy lock on capped rows and keeps them from being turned on', async () => {
    await open();
    fireEvent.click(tab(/^Music/));
    const imp = row('music.import');
    expect(imp).toHaveAttribute('data-capped');
    expect(within(imp).getByText(/Global policy keeps this for admins\./)).toBeInTheDocument();
    expect(within(imp).getByRole('switch')).toHaveAttribute('aria-checked', 'false');
    expect(within(imp).getByRole('switch')).toHaveAttribute('data-disabled');

    fireEvent.click(tab(/^Movies/));
    const k4 = screen.getByRole('checkbox', { name: 'Movie 4K Quality' });
    expect(k4).toBeDisabled();
    expect(k4).not.toBeChecked();
    const q4 = document.querySelector('[data-q="3"]') as HTMLElement;
    expect(within(q4).getByText(/Global policy keeps this for admins\./)).toBeInTheDocument();
    // the one allowed profile is the default and the menu goes away
    expect(screen.getByRole('checkbox', { name: 'Movie 1080p Quality' })).toBeChecked();
    expect(
      within(document.querySelector('[data-q="2"]') as HTMLElement).getByText('Starts here'),
    ).toBeInTheDocument();
    expect(screen.getByText(/always use/)).toHaveTextContent(
      "Maya's movie requests always use Movie 1080p Quality.",
    );
  });

  it('a mode row capped by the policy only offers what the policy allows', async () => {
    const maya = structuredClone(FIX.maya) as unknown as ProfilePermissions;
    maya.rows = maya.rows.map((r) => (r.id === 'tv.get' ? { ...r, ceiling: 'ask' } : r));
    useApi(maya);
    await open(7, { category: 'tv' });
    const tv = row('tv.get');
    expect(within(tv).getByRole('button', { name: 'Download right away' })).toBeDisabled();
    expect(within(tv).getByRole('button', { name: 'Ask first' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(tv).getByText(/Global policy: up to Ask first\./)).toBeInTheDocument();
  });
});

describe('editing', () => {
  it('a change marks the row, bumps the count, lights the dot and updates the summary', async () => {
    await open(7, { category: 'tv' });
    expect(document.querySelector('[data-dot="tv"]')).toBeNull();
    expect(sentence()).toMatch(/Asks you for movies and shows, up to PG-13, 5 asks a week\./);
    const tv = row('tv.get');
    fireEvent.click(within(tv).getByRole('button', { name: 'Download right away' }));
    expect(tv).toHaveAttribute('data-changed');
    expect(within(tv).getByText('Teen: Ask first')).toBeInTheDocument();
    expect(count()).toBe('5 changes from Teen');
    expect(document.querySelector('[data-dot="tv"]')).not.toBeNull();
    expect(sentence()).toMatch(/Movies: asks first, shows: downloads/);
    const tvLine = document.querySelector('[data-sum="tv"]') as HTMLElement;
    expect(tvLine).toHaveTextContent('Downloads');
    expect(screen.getByTestId('announce')).toHaveTextContent(
      'Getting shows: Download right away. 5 changes from Teen.',
    );
    // setting it back to the template value is no change at all
    fireEvent.click(within(tv).getByRole('button', { name: 'Ask first' }));
    expect(tv).not.toHaveAttribute('data-changed');
    expect(count()).toBe('4 changes from Teen');
  });

  it('resets one row, one category and everything', async () => {
    await open(7, { category: 'movies' });
    // category: movies.quality + quality_default go back
    fireEvent.click(screen.getByRole('button', { name: 'Reset Movies' }));
    expect(count()).toBe('2 changes from Teen');
    expect(document.querySelector('[data-dot="movies"]')).toBeNull();
    expect(screen.getByRole('checkbox', { name: 'Movie 4K Quality' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Default' })).toBeChecked();

    // row
    fireEvent.click(tab(/^Music/));
    fireEvent.click(screen.getByRole('button', { name: 'Reset Getting music to Teen' }));
    expect(count()).toBe('1 change from Teen');
    expect(within(row('music.get')).getByRole('button', { name: 'Ask first' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    // all
    fireEvent.click(screen.getByRole('button', { name: /Back to Teen/ }));
    expect(count()).toBe('Follows Teen exactly');
    expect(document.querySelector('[data-dot]')).toBeNull();
    expect(screen.getByRole('button', { name: /Back to Teen/ })).toBeDisabled();
  });

  it('explains a dependent row instead of hiding it', async () => {
    await open(7, { category: 'books' });
    const books = row('books.get');
    fireEvent.click(within(row('books.access')).getByRole('switch'));
    expect(within(books).getByText('Turn on Audiobooks first.')).toBeInTheDocument();
    for (const b of within(books).getAllByRole('button')) expect(b).toBeDisabled();
    expect(books).toHaveAttribute('data-dep');
    // the video pages need movies OR tv
    fireEvent.click(tab(/^Movies/));
    fireEvent.click(within(row('movies.access')).getByRole('switch'));
    expect(within(row('video.discover')).queryByText(/first\./)).toBeNull();
    fireEvent.click(tab(/^TV shows/));
    fireEvent.click(within(row('tv.access')).getByRole('switch'));
    expect(
      within(row('video.discover')).getByText('Turn on Movies or TV shows first.'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('summary-sentence')).toHaveTextContent(
      'Listens and downloads music right away.',
    );
  });

  it('keeps movies and tv access apart, and the shared pages say so', async () => {
    await open(7, { category: 'movies' });
    expect(within(row('movies.access')).getByText('Movies')).toBeInTheDocument();
    expect(within(row('video.calendar')).getByText(/Shared with TV shows\./)).toBeInTheDocument();
    fireEvent.click(within(row('movies.access')).getByRole('switch'));
    expect(document.querySelector('[data-sum="movies"]')).toHaveTextContent('Off');
    expect(document.querySelector('[data-sum="tv"]')).not.toHaveTextContent('Off');
    fireEvent.click(tab(/^TV shows/));
    expect(row('tv.access')).not.toBeNull();
    expect(within(row('video.calendar')).getByText(/Shared with Movies\./)).toBeInTheDocument();
    expect(within(row('tv.get')).queryByText(/first\./)).toBeNull();
  });

  it('has the moderator row, and the header and summary say it', async () => {
    await open(7, { category: 'requests' });
    const mod = row('requests.moderate');
    expect(within(mod).getByText('Moderator')).toBeInTheDocument();
    expect(
      within(mod).getByText(/Approve, decline and manage other people's requests/),
    ).toBeInTheDocument();
    fireEvent.click(within(mod).getByRole('switch'));
    expect(screen.getByText(/Member · Moderator/)).toBeInTheDocument();
    expect(document.querySelector('[data-sum="requests"]')).toHaveTextContent('moderator');
    expect(within(row('requests.cancel_own')).getByText('Always')).toBeInTheDocument();
  });

  it('the Admin switch hides every other category', async () => {
    await open(7);
    fireEvent.click(tab(/^General/));
    expect(screen.getAllByRole('tab')).toHaveLength(8);
    fireEvent.click(within(row('account.admin')).getByRole('switch'));
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['General & account']);
    expect(
      screen.getByText('Admins can use everything, including settings and people.'),
    ).toBeInTheDocument();
    expect(sentence()).toBe('Can use everything, including settings and people.');
    expect(screen.queryByLabelText('Follows')).toBeNull();
    await save();
    expect(profilePuts).toEqual([{ is_admin: true }]);
    expect(puts).toEqual([]);
  });

  it('switching template keeps the changes that still differ', async () => {
    await open(7, { category: 'music' });
    fireEvent.change(screen.getByLabelText('Follows'), { target: { value: '1' } });
    // Adult already downloads music, so that change folds away; the rest stay
    expect(count()).toBe('3 changes from Adult');
    expect(row('music.get')).not.toHaveAttribute('data-changed');
    expect(within(row('music.get')).getByText('From Adult')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Follows'), { target: { value: '3' } });
    expect(count()).toBe('3 changes from Kids');
    // Kids asks for music and hides explicit: Maya's music change came back? no, it folded at Adult
    expect(within(row('music.hide_explicit')).getByRole('switch')).toHaveAttribute(
      'aria-checked',
      'true',
    );
    await save();
    expect(puts).toEqual([{ template_id: 3, set: {}, reset: ['music.get'] }]);
  });
});

describe('saving', () => {
  it('sends exactly the differences and says what changed', async () => {
    await open(7, { category: 'movies' });
    fireEvent.click(within(row('movies.get')).getByRole('button', { name: 'Download right away' }));
    fireEvent.click(tab(/^Music/));
    fireEvent.click(screen.getByRole('button', { name: 'Reset Getting music to Teen' }));
    expect(screen.getByText(/2 unsaved changes/)).toBeInTheDocument();
    await save();
    expect(puts).toEqual([{ set: { 'movies.get': 'download' }, reset: ['music.get'] }]);
    expect(footStatus()).toHaveTextContent(
      'Saved. Getting movies: Ask first to Download right away.',
    );
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('Back to Teen saves as reset all', async () => {
    await open();
    fireEvent.click(screen.getByRole('button', { name: /Back to Teen/ }));
    await save();
    expect(puts).toEqual([{ set: {}, reset: 'all' }]);
  });

  it('a quality pick and Needs my OK save as set values', async () => {
    await open(7, { category: 'tv' });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Default' }));
    fireEvent.click(
      screen.getByRole('switch', { name: 'TV Show 720p/1080p EXTENDED needs my OK' }),
    );
    expect(screen.getByText(/2 allowed, so Maya picks from a short menu\./)).toHaveTextContent(
      'TV Show 720p/1080p EXTENDED always comes to you first.',
    );
    await save();
    expect(puts).toEqual([{ set: { 'tv.quality': [5, 6], 'tv.quality_ask': [6] }, reset: [] }]);
  });

  it('sidebar & look saves through the features call and the home page', async () => {
    await open(7, { category: 'look' });
    await screen.findByText('Let Maya change it');
    fireEvent.click(screen.getByRole('button', { name: 'Simple' }));
    fireEvent.click(screen.getByRole('switch', { name: 'Let Maya change it' }));
    await save();
    expect(featurePosts).toEqual([{ off: ['stats'], locked: true }]);
    expect(puts).toEqual([]);
  });

  it('asks before throwing away unsaved changes', async () => {
    await open(7, { category: 'tv' });
    fireEvent.click(within(row('tv.get')).getByRole('button', { name: 'Download right away' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Leave without saving 1 change?');
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(puts).toEqual([]);
  });
});

describe('who gets it', () => {
  it("a member can't open it: nothing drawn, nothing fetched", async () => {
    window.SoulSyncWebShellBridge = {
      getCurrentProfileContext: () => ({ profileId: 7, isAdmin: false }),
    } as unknown as typeof window.SoulSyncWebShellBridge;
    render(<PersonEditorHost />);
    let opened = true;
    act(() => {
      opened = openPersonEditor(9);
    });
    expect(opened).toBe(false);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(permGets).toBe(0);
  });

  it('a refusal from the server is a calm message, not a broken sheet', async () => {
    server.use(
      http.get('/api/profiles/:id/permissions', () =>
        HttpResponse.json({ success: false, error: 'Admin access required' }, { status: 403 }),
      ),
    );
    render(<PersonEditorHost />);
    act(() => {
      openPersonEditor(7);
    });
    expect(await screen.findByText('Admins only')).toBeInTheDocument();
    expect(screen.getByText('Only admins can change what people can do.')).toBeInTheDocument();
  });

  it('an admin profile shows only General & account', async () => {
    useApi(FIX.admin as unknown as ProfilePermissions);
    await open(3);
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['General & account']);
    expect(within(row('account.admin')).getByRole('switch')).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });

  it('turning an admin into a member starts them on the sign-up template', async () => {
    useApi(FIX.admin as unknown as ProfilePermissions);
    await open(3);
    fireEvent.click(within(row('account.admin')).getByRole('switch'));
    expect(screen.getAllByRole('tab')).toHaveLength(8);
    expect((screen.getByLabelText('Follows') as HTMLSelectElement).value).toBe('4');
    await save();
    expect(profilePuts).toEqual([{ is_admin: false }]);
    expect(puts).toEqual([{ template_id: 4, set: {}, reset: 'all' }]);
  });

  it('a profile on its own settings needs a look, and picking a template carries them over', async () => {
    useApi(FIX.legacy as unknown as ProfilePermissions);
    await open(9);
    expect(screen.getByText('Needs a look.')).toBeInTheDocument();
    expect(screen.getByText('On their own settings')).toBeInTheDocument();
    fireEvent.click(tab(/^Music/));
    expect(within(row('music.get')).getByText('Their own setting')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Follows'), { target: { value: '2' } });
    expect(screen.queryByText('Needs a look.')).toBeNull();
    await save();
    expect(puts[0].template_id).toBe(2);
    expect(puts[0].reset).toBe('all');
  });
});

describe('keyboard and roles', () => {
  it('the rail is a tablist the arrow keys walk, and the panel is labelled by its tab', async () => {
    await open(7, { category: 'general' });
    const list = screen.getByRole('tablist', { name: 'Categories' });
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    expect(tab(/^Movies/)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName(/Movies/);
    fireEvent.keyDown(list, { key: 'End' });
    expect(tab(/^Sidebar & look/)).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(list, { key: 'Home' });
    expect(tab(/^General/)).toHaveAttribute('aria-selected', 'true');
  });
});
