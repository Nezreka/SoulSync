/**
 * Global policy: the rail by category, a ceiling per row, the impact line
 * from PUT ?dry_run=1 before anything is stored, then Save or Discard.
 */

import { configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse } from '@/test/msw';

import { settingsStore } from '../../settings-store';
import { servePeople, signInAs } from './__fixtures__/people-msw';
import { PeopleTab } from './people-tab';

async function openPolicy() {
  render(<PeopleTab store={settingsStore} opened={1} active />);
  fireEvent.click(await screen.findByRole('tab', { name: 'Global policy' }));
  return screen.findByRole('tabpanel', { name: /Movies/ });
}

// the impact line waits for a dry run; give it room on a busy machine
configure({ asyncUtilTimeout: 5000 });

beforeEach(() => signInAs(true));
afterEach(() => {
  const w = window as unknown as Record<string, unknown>;
  delete w.getCurrentProfileContext;
  delete w.showToast;
});

describe('global policy', () => {
  it('opens on Movies, says admins are never limited, and has no Sidebar & look', async () => {
    servePeople();
    const pane = await openPolicy();
    expect(pane).toHaveTextContent('The most any member can ever have.');
    expect(pane).toHaveTextContent('Admins are never limited.');
    const rail = screen.getByRole('tablist', { name: 'Policy categories' });
    const tabs = within(rail)
      .getAllByRole('tab')
      .map((t) => t.textContent);
    expect(tabs).toEqual([
      'General & account',
      'Movies',
      'TV shows',
      'Music',
      'Audiobooks & podcasts',
      'Requests & moderation',
      'Administration',
    ]);
    expect(rail).toHaveTextContent("Sidebar and look aren't here");
    // a mode row is a ceiling: Admins only instead of Can't request
    const seg = within(pane).getByRole('group', { name: 'Getting movies' });
    expect(
      within(seg)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Download right away', 'Ask first', 'Admins only']);
  });

  it('says who a change reaches before saving, then saves only what moved', async () => {
    const toast = vi.fn();
    window.showToast = toast;
    const { calls } = servePeople();
    const pane = await openPolicy();
    expect(screen.queryByRole('button', { name: 'Save policy' })).toBeNull();
    fireEvent.click(
      within(within(pane).getByRole('group', { name: 'Getting movies' })).getByText('Ask first'),
    );
    await waitFor(() =>
      expect(pane).toHaveTextContent(
        'Saving reaches all 5 members now. Priya: Getting movies goes from Download right away to Ask first',
      ),
    );
    expect(pane).toHaveTextContent('Odd runs on old settings.');
    const dry = calls.filter((c) => c.method === 'PUT' && c.url.includes('dry_run=1'));
    expect(dry.at(-1)!.body).toEqual({ set: { 'movies.get': 'ask' } });
    // the impact line is announced
    expect(pane.querySelector('[aria-live="polite"]')).toHaveTextContent('Saving reaches all 5');

    fireEvent.click(screen.getByRole('button', { name: 'Save policy' }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Policy saved', 'success'));
    const saved = calls.filter((c) => c.method === 'PUT' && !c.url.includes('dry_run'));
    expect(saved).toHaveLength(1);
    expect(saved[0].body).toEqual({ set: { 'movies.get': 'ask' } });
  });

  it('marks a category with limits, and Discard puts the row back', async () => {
    servePeople({ policy: { 'chat.use': false } });
    const pane = await openPolicy();
    expect(screen.getByRole('tab', { name: /General & account/ })).toHaveTextContent(
      'Limited here',
    );
    expect(screen.getByRole('tab', { name: /^Movies/ })).not.toHaveTextContent('Limited here');
    const seg = within(pane).getByRole('group', { name: 'Getting movies' });
    fireEvent.click(within(seg).getByText('Admins only'));
    expect(within(seg).getByText('Admins only')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    expect(within(seg).getByText('Download right away')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: 'Save policy' })).toBeNull();
  });

  it('turns on 4K always asking, and edits the quality members may pick', async () => {
    const { calls } = servePeople();
    const pane = await openPolicy();
    fireEvent.click(within(pane).getByRole('switch', { name: /4K always asks/ }));
    fireEvent.click(within(pane).getByRole('checkbox', { name: 'Movie 4K Quality' }));
    expect(within(pane).getByRole('checkbox', { name: 'Movie 4K Quality' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
    expect(within(pane).getAllByText('Admins only').length).toBeGreaterThan(0);
    await waitFor(() => {
      const dry = calls.filter((c) => c.url.includes('dry_run=1')).at(-1);
      expect(dry?.body).toEqual({
        set: { 'movies.quality_ask': ['4k'], 'movies.quality': [0, 2] },
      });
    });
  });

  it('says why a save was refused and keeps the change', async () => {
    servePeople({
      onPolicy: (_body, dry) =>
        dry
          ? undefined
          : HttpResponse.json(
              {
                success: false,
                error: 'permissions_unavailable',
                message: "Permissions can't be read right now.",
              },
              { status: 503 },
            ),
    });
    const pane = await openPolicy();
    fireEvent.click(
      within(within(pane).getByRole('group', { name: 'Getting movies' })).getByText('Ask first'),
    );
    const save = await screen.findByRole('button', { name: 'Save policy' });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() => expect(pane).toHaveTextContent("Permissions can't be read right now."));
    expect(
      within(within(pane).getByRole('group', { name: 'Getting movies' })).getByText('Ask first'),
    ).toHaveAttribute('aria-pressed', 'true');
  });

  it('moves along the rail with the arrow keys', async () => {
    servePeople();
    await openPolicy();
    const movies = screen.getByRole('tab', { name: /^Movies/ });
    fireEvent.keyDown(movies, { key: 'ArrowDown' });
    expect(screen.getByRole('tab', { name: /^TV shows/ })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByRole('tabpanel', { name: /TV shows/ })).toHaveTextContent(
      'What any member may do with shows, at most.',
    );
  });
});
