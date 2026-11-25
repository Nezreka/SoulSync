/**
 * Invites and Activity, moved here from the Profiles modal: the same
 * routes and invite maker; the admin log says permission changes in words.
 */

import { configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { settingsStore } from '../../settings-store';
import { servePeople, signInAs } from './__fixtures__/people-msw';
import { openPeopleSettings, PeopleTab } from './people-tab';

// the impact line waits for a dry run; give it room on a busy machine
configure({ asyncUtilTimeout: 5000 });

beforeEach(() => signInAs(true));
afterEach(() => {
  const w = window as unknown as Record<string, unknown>;
  delete w.getCurrentProfileContext;
  delete w.showConfirmDialog;
  delete w.showToast;
  delete w.openInviteCreator;
  delete w.switchSettingsTab;
});

describe('invites', () => {
  it('lists invite links, counts the open ones, and revokes one', async () => {
    const { calls } = servePeople();
    window.showConfirmDialog = vi.fn(async () => true);
    const openInviteCreator = vi.fn();
    Object.assign(window, { openInviteCreator });
    render(<PeopleTab store={settingsStore} opened={1} active />);
    const pill = await screen.findByRole('tab', { name: /Invites/ });
    await waitFor(() => expect(pill).toHaveTextContent('Invites1'));
    fireEvent.click(pill);
    const list = await screen.findByRole('list', { name: 'Invites' });
    const [open, used] = within(list).getAllByRole('listitem');
    expect(open).toHaveTextContent('For Kim');
    expect(open).toHaveTextContent('Music & movies · asks first · up to PG-13 · 5 asks a week');
    expect(open).toHaveTextContent('Open');
    expect(used).toHaveTextContent('used by Liam');
    expect(within(used).queryByRole('button', { name: 'Revoke' })).toBeNull();
    fireEvent.click(within(open).getByRole('button', { name: 'Revoke' }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/invites/3'))).toBe(true),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Create invite link' }));
    expect(openInviteCreator).toHaveBeenCalled();
  });
});

describe('activity', () => {
  it('reads the admin log and says access changes in words', async () => {
    servePeople();
    render(<PeopleTab store={settingsStore} opened={1} active />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Activity' }));
    const list = await screen.findByRole('list', { name: 'Activity' });
    const items = within(list)
      .getAllByRole('listitem')
      .map((li) => li.textContent);
    expect(items[0]).toContain(
      "Boulder changed Maya's access: Music requests and Hide explicit music",
    );
    expect(items[1]).toContain('Boulder changed the global policy: Allowed quality profiles');
    expect(items[3]).toContain('Liam joined with an invite link');
    fireEvent.click(screen.getByRole('button', { name: 'Access changes' }));
    expect(
      within(screen.getByRole('list', { name: 'Activity' })).getAllByRole('listitem'),
    ).toHaveLength(2);
  });
});

describe('openPeopleSettings', () => {
  it('opens Settings on People, on the pill asked for', async () => {
    servePeople();
    const switchSettingsTab = vi.fn();
    Object.assign(window, { switchSettingsTab });
    render(<PeopleTab store={settingsStore} opened={1} active />);
    await screen.findByRole('tab', { name: 'Activity' });
    openPeopleSettings('templates');
    expect(switchSettingsTab).toHaveBeenCalledWith('people');
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Templates/ })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    );
  });
});
