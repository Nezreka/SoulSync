/**
 * Settings > People, the People pill: the hero counts, each person's row
 * from their effective map (GET /api/profiles/<id>/permissions), the
 * filters, the "Needs a look" profiles the migration flagged, the existing
 * add and invite flows, and that a member never sees any of it.
 */

import { configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsPage } from '../../settings-page';
import { settingsStore } from '../../settings-store';
import { servePeople, signInAs } from './__fixtures__/people-msw';
import { PeopleTab } from './people-tab';

const editorPresent = Object.keys(import.meta.glob('./editor/index.ts')).length > 0;

function show() {
  return render(<PeopleTab store={settingsStore} opened={1} active />);
}

const rowOf = (name: string) =>
  screen.getAllByRole('row').find((r) => within(r).queryByText(name, { selector: 'div' }))!;

// the impact line waits for a dry run; give it room on a busy machine
configure({ asyncUtilTimeout: 5000 });

beforeEach(() => signInAs(true));
afterEach(() => {
  const w = window as unknown as Record<string, unknown>;
  delete w.getCurrentProfileContext;
  delete w.openProfileEditor;
  delete w.openInviteCreator;
  delete w.showToast;
});

describe('who sees the tab', () => {
  it('draws nothing for a member, and hides the tab button', async () => {
    servePeople();
    signInAs(false);
    const { container } = show();
    await new Promise((r) => setTimeout(r, 30));
    expect(container).toBeEmptyDOMElement();
    const page = render(<SettingsPage tabs={{}} />);
    expect(page.container.querySelector('[data-tab="people"]')).toHaveAttribute('hidden');
  });

  it('shows the tab button to an admin', () => {
    const page = render(<SettingsPage tabs={{}} />);
    expect(page.container.querySelector('[data-tab="people"]')).not.toHaveAttribute('hidden');
    expect(page.container.querySelector('[data-tab="people"]')).toHaveTextContent('People');
  });

  it('server-renders with the tab hidden (nobody signed in yet)', () => {
    const html = renderToStaticMarkup(<PeopleTab store={settingsStore} opened={1} active />);
    expect(html).toBe('');
  });

  it('draws nothing when the API says admins only', async () => {
    servePeople({ forbid: true });
    const { container } = show();
    await new Promise((r) => setTimeout(r, 60));
    expect(container).toBeEmptyDOMElement();
  });
});

describe('the people home', () => {
  it('says who is here in the hero, with the waiting requests', async () => {
    servePeople();
    show();
    const hero = await screen.findByText(/people/, { selector: 'em' });
    const line = hero.closest('p')!;
    expect(line).toHaveTextContent(
      'Eight people use SoulSync here. Five follow a template and three have changes of their own. One needs a look. Three requests are waiting for you.',
    );
    expect(screen.getByText('Members', { selector: 'div' }).previousSibling).toHaveTextContent('6');
    expect(screen.getByText('Admins', { selector: 'div' }).previousSibling).toHaveTextContent('2');
    expect(screen.getByText('Waiting', { selector: 'div' }).previousSibling).toHaveTextContent('3');
  });

  it("draws each person's summary from their effective permissions", async () => {
    servePeople();
    show();
    await screen.findByText('Maya', { selector: 'div' });
    const maya = rowOf('Maya');
    expect(within(maya).getByText(/Teen/)).toHaveTextContent('Teen· 3 changes');
    const cells = within(maya).getAllByRole('cell');
    // music downloads (her own change) and is clean; movies ask, up to PG-13
    expect(cells[2]).toHaveTextContent('Downloads');
    expect(cells[2]).toHaveTextContent('Clean');
    expect(cells[3]).toHaveTextContent('Asks first');
    expect(cells[3]).toHaveTextContent('PG-13');
    expect(cells[4]).toHaveTextContent('TV-14');
    expect(cells[5]).toHaveTextContent('Listens');
    expect(cells[6]).toHaveTextContent('5 asks');
    expect(cells[6]).toHaveTextContent('a week');

    const liam = within(rowOf('Liam')).getAllByRole('cell');
    expect(liam[1]).toHaveTextContent('Guest· 1 change');
    expect(liam[2]).toHaveTextContent('Asks first');
    expect(liam[3]).toHaveTextContent('Off');

    expect(rowOf('Boulder')).toHaveTextContent('Everything, including settings and people');
    expect(within(rowOf('Boulder')).getByText('Admin', { selector: 'span' })).toBeInTheDocument();
    expect(rowOf('Carlos')).toHaveTextContent('Turned off. Everything of theirs is kept.');
    expect(rowOf('Priya')).toHaveTextContent('No limit');
  });

  it('explains the profiles the migration left on their own settings', async () => {
    servePeople();
    show();
    const note = await screen.findByRole('note');
    expect(note).toHaveTextContent('Odd needs a look.');
    expect(note).toHaveTextContent(
      "couldn't be said exactly with a template, so nothing was changed",
    );
    expect(note).toHaveTextContent(
      "A rating limit of NC-17, which SoulSync can't match to a rating here.",
    );
    expect(within(rowOf('Odd')).getByText('Needs a look')).toBeInTheDocument();
  });

  it('filters by chip and by name', async () => {
    servePeople();
    show();
    await screen.findByText('Maya', { selector: 'div' });
    const names = () =>
      screen
        .getAllByRole('row')
        .slice(1)
        .map((r) => r.querySelector('[class*="t1"]')?.firstChild?.textContent);
    fireEvent.click(screen.getByRole('button', { name: 'Admins' }));
    expect(names()).toEqual(['Boulder', 'Sam']);
    fireEvent.click(screen.getByRole('button', { name: 'Has changes' }));
    expect(names()).toEqual(['Maya', 'Jonah', 'Liam']);
    fireEvent.click(screen.getByRole('button', { name: 'Turned off' }));
    expect(names()).toEqual(['Carlos']);
    fireEvent.click(screen.getByRole('button', { name: /Needs a look/ }));
    expect(names()).toEqual(['Odd']);
    fireEvent.click(screen.getByRole('button', { name: 'Everyone' }));
    fireEvent.change(screen.getByPlaceholderText('Find someone'), { target: { value: 'pri' } });
    expect(names()).toEqual(['Priya']);
    fireEvent.change(screen.getByPlaceholderText('Find someone'), { target: { value: 'zed' } });
    expect(screen.getByText('Nobody here matches "zed".')).toBeInTheDocument();
  });

  it('opens a person, and reuses the existing add and invite flows', async () => {
    servePeople();
    const openProfileEditor = vi.fn();
    const openInviteCreator = vi.fn();
    Object.assign(window, { openProfileEditor, openInviteCreator });
    show();
    await screen.findByText('Maya', { selector: 'div' });
    fireEvent.click(screen.getByRole('button', { name: 'Add person' }));
    expect(openProfileEditor).toHaveBeenCalledWith({ mode: 'create' });
    fireEvent.click(screen.getByRole('button', { name: 'Invite' }));
    expect(openInviteCreator).toHaveBeenCalled();
    if (!editorPresent) {
      fireEvent.keyDown(rowOf('Maya'), { key: 'Enter' });
      await waitFor(() =>
        expect(openProfileEditor).toHaveBeenLastCalledWith({
          mode: 'edit',
          profile: expect.objectContaining({ id: 3, name: 'Maya' }),
        }),
      );
      fireEvent.click(rowOf('Boulder'));
      await waitFor(() =>
        expect(openProfileEditor).toHaveBeenLastCalledWith({
          mode: 'self',
          profile: expect.objectContaining({ id: 1 }),
        }),
      );
    }
  });

  it('lists again when a profile modal closes or the editor saves', async () => {
    servePeople();
    show();
    await screen.findByText('Maya', { selector: 'div' });
    const spy = vi.spyOn(window, 'fetch');
    const reads = () =>
      spy.mock.calls.filter((c) => {
        const u = c[0];
        const url = typeof u === 'string' ? u : u instanceof Request ? u.url : u.toString();
        return url.endsWith('/api/profiles');
      }).length;
    document.body.classList.add('pf-modal-open');
    await new Promise((r) => setTimeout(r, 0));
    document.body.classList.remove('pf-modal-open');
    await waitFor(() => expect(reads()).toBe(1));
    window.dispatchEvent(new CustomEvent('ss:person-permissions-saved'));
    await waitFor(() => expect(reads()).toBe(2));
    spy.mockRestore();
  });
});
