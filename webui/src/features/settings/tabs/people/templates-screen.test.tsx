/**
 * Templates: cards with who follows each, the selected one's rows under
 * the policy's locks, its Sidebar & look (where "What new profiles see"
 * went), Rename, Duplicate, New, Delete with somewhere to move followers
 * (and the server's 409s), and "New people start as".
 */

import { configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse } from '@/test/msw';

import { settingsStore } from '../../settings-store';
import { FIXTURE, servePeople, signInAs } from './__fixtures__/people-msw';
import { PeopleTab } from './people-tab';

async function openTemplates() {
  render(<PeopleTab store={settingsStore} opened={1} active />);
  fireEvent.click(await screen.findByRole('tab', { name: /Templates/ }));
  return screen.findByRole('button', { name: /^Adult/ });
}
const card = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}`) });
const pane = () =>
  screen.getByRole('tabpanel', {
    name: /Movies|TV|Music|General|Sidebar|Audiobooks|Requests|Administration/,
  });

let toast: ReturnType<typeof vi.fn<(message: string, type?: string) => void>>;
// the impact line waits for a dry run; give it room on a busy machine
configure({ asyncUtilTimeout: 5000 });

beforeEach(() => {
  signInAs(true);
  toast = vi.fn<(message: string, type?: string) => void>();
  window.showToast = toast;
});
afterEach(() => {
  const w = window as unknown as Record<string, unknown>;
  delete w.getCurrentProfileContext;
  delete w.showToast;
  delete w.showConfirmDialog;
});

describe('templates', () => {
  it('shows a card per template with who follows it, and the sign-up one', async () => {
    servePeople();
    await openTemplates();
    expect(card('Adult')).toHaveTextContent('2 people');
    expect(card('Teen')).toHaveTextContent('1 person');
    expect(card('Guest')).toHaveTextContent('Sign-up');
    expect(card('Adult')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Changes reach Priya and Carlos now.')).toBeInTheDocument();
    fireEvent.click(card('Teen'));
    expect(
      screen.getByText('Changes reach Maya now. 3 personal changes stay as they are.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: /New people start as/ })).toHaveValue(
      String(FIXTURE.templates.signup_template_id),
    );
  });

  it("edits a template's rows, shows the impact on its followers, and saves", async () => {
    const { calls } = servePeople();
    await openTemplates();
    fireEvent.click(card('Teen'));
    const seg = within(pane()).getByRole('group', { name: 'Movie requests' });
    expect(within(seg).getByText('Require approval')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(seg).getByText('Auto-approve'));
    await waitFor(() =>
      expect(pane()).toHaveTextContent(
        'Saving reaches one person now. Maya: Movie requests goes from Require approval to Auto-approve.',
      ),
    );
    expect(screen.getByRole('tab', { name: /^Movies/ })).toHaveTextContent('Not saved yet');
    fireEvent.click(screen.getByRole('button', { name: 'Save Teen' }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Teen saved', 'success'));
    const put = calls.filter((c) => c.method === 'PUT' && c.url.endsWith('/templates/2'));
    expect(put).toHaveLength(1);
    expect(put[0].body).toEqual({ perms: { 'movies.get': 'download' } });
  });

  it('locks what the global policy holds down', async () => {
    servePeople({ policy: { 'movies.get': 'ask', 'movies.quality': [0, 2], 'chat.use': false } });
    await openTemplates();
    // Adult downloads movies, but the policy keeps it at Require approval: the
    // control shows Require approval, the value in force, and says apart what the
    // template itself is set to (#2189)
    const rows = pane();
    const seg = within(rows).getByRole('group', { name: 'Movie requests' });
    expect(within(seg).getByText('Require approval')).toHaveAttribute('aria-pressed', 'true');
    expect(within(seg).getByText('Auto-approve')).toHaveAttribute('aria-pressed', 'false');
    expect(within(seg).getByText('Auto-approve')).toBeDisabled();
    expect(rows).toHaveTextContent(
      'Global policy keeps this at Require approval. The template is set to Auto-approve, which comes back if the policy allows it. Change it there.',
    );
    expect(rows).toHaveTextContent('Require approval: what they pick lands in your Requests.');
    expect(within(rows).getByRole('checkbox', { name: 'Movie 4K Quality' })).toBeDisabled();
    expect(rows).toHaveTextContent('Global policy: admins only');
    fireEvent.click(screen.getByRole('tab', { name: /General & account/ }));
    const chat = within(pane()).getByRole('switch', { name: 'Chat & Arcade' });
    expect(chat).toBeDisabled();
    expect(chat).toHaveAttribute('aria-checked', 'false');
  });

  it('explains a row that needs another first', async () => {
    servePeople();
    await openTemplates();
    fireEvent.click(card('Guest'));
    expect(pane()).toHaveTextContent('Turn on Movies first.');
    expect(
      within(pane()).getByRole('group', { name: 'Movie requests' }).querySelector('button'),
    ).toBeDisabled();
  });

  it('renames, duplicates and makes a new template', async () => {
    const { calls } = servePeople();
    await openTemplates();
    fireEvent.click(card('Teen'));
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    const name = screen.getByRole('textbox', { name: 'Template name' });
    fireEvent.change(name, { target: { value: 'Teenagers' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save name' }));
    await waitFor(() => expect(card('Teenagers')).toBeInTheDocument());
    expect(
      calls.find((c) => c.method === 'PUT' && c.url.endsWith('/templates/2'))!.body,
    ).toMatchObject({
      name: 'Teenagers',
    });

    fireEvent.click(screen.getByRole('button', { name: 'Duplicate' }));
    await waitFor(() => expect(card('Teenagers copy')).toBeInTheDocument());
    expect(calls.find((c) => c.method === 'POST')!.body).toMatchObject({
      name: 'Teenagers copy',
      from_template_id: 2,
    });
    // a new template opens straight into naming it
    expect(screen.getByRole('textbox', { name: 'Template name' })).toHaveValue('Teenagers copy');
  });

  it('refuses a name another template has', async () => {
    servePeople({
      onTemplate: (_id, body) =>
        body.name
          ? HttpResponse.json(
              { success: false, error: 'duplicate_name', message: 'taken' },
              { status: 409 },
            )
          : undefined,
    });
    await openTemplates();
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Template name' }), {
      target: { value: 'Kids' },
    });
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Template name' }), { key: 'Enter' });
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "There's already a template called Kids.",
    );
  });

  it('deletes a followed template only after moving its people', async () => {
    const { calls } = servePeople();
    await openTemplates();
    fireEvent.click(card('Teen'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const panel = screen.getByRole('alert');
    expect(panel).toHaveTextContent('Maya follows Teen. Move them to another template first');
    fireEvent.change(within(panel).getByRole('combobox', { name: 'Move them to' }), {
      target: { value: '3' },
    });
    fireEvent.click(within(panel).getByRole('button', { name: 'Move and delete' }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Deleted Teen. Maya moved.', 'success'));
    const del = calls.find((c) => c.method === 'DELETE')!;
    expect(del.url).toMatch(/\/templates\/2\?reassign_to=3$/);
  });

  it('asks for somewhere to move people when the server says someone follows it (409)', async () => {
    const templates = JSON.parse(JSON.stringify(FIXTURE.templates)) as typeof FIXTURE.templates;
    // the list is stale: it shows nobody on Kids, the server knows better
    templates.templates[2].followers = [];
    servePeople({
      templates,
      onDelete: (_id, reassign) =>
        reassign
          ? undefined
          : HttpResponse.json(
              { success: false, error: 'has_followers', message: 'x' },
              { status: 409 },
            ),
    });
    window.showConfirmDialog = vi.fn(async () => true);
    await openTemplates();
    fireEvent.click(card('Kids'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(window.showConfirmDialog).toHaveBeenCalled();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'People still follow it. Pick where they go first.',
    );
  });

  it("won't delete the sign-up template", async () => {
    const { calls } = servePeople();
    await openTemplates();
    fireEvent.click(card('Guest'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(screen.getByRole('alert')).toHaveTextContent(
      'New people start as Guest. Pick another sign-up template first.',
    );
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
  });

  it('sets the template new people start as', async () => {
    const { calls } = servePeople();
    await openTemplates();
    fireEvent.change(screen.getByRole('combobox', { name: /New people start as/ }), {
      target: { value: '2' },
    });
    await waitFor(() => expect(toast).toHaveBeenCalledWith('New people start as Teen', 'success'));
    expect(calls.find((c) => c.url.endsWith('/signup-template'))!.body).toEqual({ template_id: 2 });
    await waitFor(() => expect(card('Teen')).toHaveTextContent('Sign-up'));
  });

  it("edits a template's Sidebar & look, which replaced What new profiles see", async () => {
    const { calls } = servePeople();
    await openTemplates();
    fireEvent.click(card('Guest'));
    fireEvent.click(screen.getByRole('tab', { name: /Sidebar & look/ }));
    const look = await screen.findByRole('tabpanel', { name: /Sidebar & look/ });
    expect(look).toHaveTextContent('It never grants anything.');
    expect(look).toHaveTextContent('This replaces "What new profiles see"');
    // Guest can't use movies, so that part never shows
    await waitFor(() =>
      expect(within(look).getByRole('switch', { name: 'Show Movies & TV' })).toBeDisabled(),
    );
    expect(look).toHaveTextContent('Not part of Guest, so it never shows.');
    fireEvent.click(within(look).getByRole('switch', { name: 'Show Listening stats' }));
    fireEvent.click(within(look).getByRole('switch', { name: 'Let them change it' }));
    const save = await screen.findByRole('button', { name: 'Save Guest' });
    // Save waits for the dry run, so it never stores what wasn't shown
    expect(save).toBeDisabled();
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Guest saved', 'success'));
    const put = calls.filter((c) => c.method === 'PUT' && c.url.endsWith('/templates/4'));
    expect(put[0].body).toEqual({ sidebar: { off: ['stats'], locked: true } });
  });

  it('keeps an unsaved template when the user stays', async () => {
    servePeople();
    window.showConfirmDialog = vi.fn(async () => false);
    await openTemplates();
    fireEvent.click(
      within(within(pane()).getByRole('group', { name: 'Movie requests' })).getByText(
        'Require approval',
      ),
    );
    fireEvent.click(card('Kids'));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(card('Adult')).toHaveAttribute('aria-pressed', 'true');
  });
});
