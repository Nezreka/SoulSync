/**
 * Import Plex users (#2319): the dialog lists who the Plex server is shared
 * with, greys out people already in SoulSync, offers to link a likely match,
 * and posts the picks with the chosen template.
 */

import { configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { settingsStore } from '../../settings-store';
import { FIXTURE, servePeople, signInAs } from './__fixtures__/people-msw';
import { PeopleTab } from './people-tab';

configure({ asyncUtilTimeout: 5000 });

const USERS = [
  {
    id: '101',
    username: 'alreadyhere',
    title: 'alreadyhere',
    name: 'alreadyhere',
    email: '',
    thumb: '',
    home: false,
    managed: false,
    profile: { id: 2, name: 'Maya', is_admin: false },
    match: 'linked',
  },
  {
    id: '102',
    username: 'liam',
    title: 'liam',
    name: 'liam',
    email: 'liam@example.com',
    thumb: 'https://plex.tv/users/abc/avatar',
    home: false,
    managed: false,
    profile: { id: 3, name: 'Liam', is_admin: false },
    match: 'name',
  },
  {
    id: '103',
    username: 'newfriend',
    title: 'newfriend',
    name: 'newfriend',
    email: '',
    thumb: '',
    home: true,
    managed: true,
    profile: null,
    match: null,
  },
];

function servePlex(opts: { signin?: boolean; fail?: boolean } = {}) {
  const posts: unknown[] = [];
  server.use(
    http.get('*/api/profiles/plex-import', () =>
      opts.fail
        ? HttpResponse.json(
            { success: false, error: 'plex_unavailable', message: 'Plex is not connected' },
            { status: 503 },
          )
        : HttpResponse.json({
            success: true,
            users: USERS,
            signin_enabled: opts.signin ?? true,
            signup_template_id: FIXTURE.templates.signup_template_id,
          }),
    ),
    http.post('*/api/profiles/plex-import', async ({ request }) => {
      posts.push(await request.json());
      return HttpResponse.json({
        success: true,
        signin_enabled: true,
        results: [
          {
            plex_id: '102',
            name: 'liam',
            status: 'linked',
            profile_id: 3,
            message: 'Linked to Liam',
          },
          {
            plex_id: '103',
            name: 'newfriend',
            status: 'created',
            profile_id: 9,
            message: 'Added as newfriend',
          },
        ],
      });
    }),
  );
  return posts;
}

beforeEach(() => signInAs(true));
afterEach(() => {
  const w = window as unknown as Record<string, unknown>;
  delete w.getCurrentProfileContext;
  delete w.showToast;
});

async function openDialog() {
  render(<PeopleTab store={settingsStore} opened={1} active />);
  fireEvent.click(await screen.findByRole('button', { name: 'Import from Plex' }));
  return screen.findByRole('dialog', { name: /Import Plex users/ });
}

describe('import plex users', () => {
  it('lists plex users, links a likely match, and imports with the picked template', async () => {
    servePeople();
    const posts = servePlex();
    const toast = vi.fn();
    window.showToast = toast;
    const dialog = await openDialog();
    const list = await within(dialog).findByRole('list', { name: 'Plex users' });
    const [here, liam, fresh] = within(list).getAllByRole('listitem');
    expect(here).toHaveTextContent('In SoulSync as Maya');
    expect(within(here).getByRole('checkbox')).toBeDisabled();
    expect(within(liam).getByRole('combobox', { name: 'What to do with liam' })).toHaveValue(
      'link',
    );
    expect(fresh).toHaveTextContent('Managed home user');
    expect(fresh).toHaveTextContent('New');

    const importBtn = within(dialog).getByRole('button', { name: 'Import' });
    expect(importBtn).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('checkbox', { name: /Pick everyone new/ }));
    expect(within(dialog).getByRole('button', { name: 'Import 2' })).toBeEnabled();

    const other = FIXTURE.templates.templates.find(
      (t) => t.id !== FIXTURE.templates.signup_template_id,
    )!;
    const tpl = within(dialog).getByRole('combobox', { name: 'New profiles follow' });
    expect(tpl).toHaveValue(String(FIXTURE.templates.signup_template_id));
    fireEvent.change(tpl, { target: { value: String(other.id) } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Import 2' }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      users: [{ id: '102', profile_id: 3 }, { id: '103' }],
      template_id: other.id,
    });
    const results = await within(dialog).findByRole('list', { name: 'Import results' });
    expect(results).toHaveTextContent('Linked to Liam');
    expect(results).toHaveTextContent('Added as newfriend');
    expect(toast).toHaveBeenCalledWith('2 people imported from Plex', 'success');
  });

  it('can make a new profile instead of linking the likely match', async () => {
    servePeople();
    const posts = servePlex();
    const dialog = await openDialog();
    const list = await within(dialog).findByRole('list', { name: 'Plex users' });
    const liam = within(list).getAllByRole('listitem')[1];
    fireEvent.click(within(liam).getByRole('checkbox', { name: 'Import liam' }));
    fireEvent.change(within(liam).getByRole('combobox'), { target: { value: 'new' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Import 1' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect((posts[0] as { users: unknown[] }).users).toEqual([{ id: '102' }]);
  });

  it('says when plex sign-in is off, and when plex cannot be reached', async () => {
    servePeople();
    servePlex({ signin: false });
    const dialog = await openDialog();
    expect(await within(dialog).findByRole('note')).toHaveTextContent('Sign in with Plex is off');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    servePlex({ fail: true });
    fireEvent.click(screen.getByRole('button', { name: 'Import from Plex' }));
    const again = await screen.findByRole('dialog', { name: /Import Plex users/ });
    expect(await within(again).findByRole('alert')).toHaveTextContent('Plex is not connected');
  });
});
