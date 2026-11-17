/**
 * The inline results in the sheet: typing shows grouped, highlighted rows;
 * the arrows go from the field into them; Enter runs the first; a member
 * sees no settings; an article opens in the sheet.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { HelpSheet } from '../sheet';
import { useHelpStore } from '../store';
import { loadDocs } from './docs';
import { RECENT_KEY } from './rank';

const DIR = join(process.cwd(), 'static', 'docs-content');
const FILES = readdirSync(DIR).filter((f) => f.endsWith('.js'));

beforeAll(async () => {
  await loadDocs(
    async (u) => readFileSync(join(DIR, u.split('/').pop()!), 'utf8'),
    FILES.map((f) => `/static/docs-content/${f}`),
  );
});

beforeEach(() => {
  useHelpStore.setState(useHelpStore.getInitialState(), true);
  localStorage.removeItem(RECENT_KEY);
  document.body.setAttribute('data-side', 'music');
  const nav = document.createElement('nav');
  nav.className = 'sidebar-nav';
  nav.innerHTML =
    '<a class="nav-button" data-page="search"><span class="nav-text">Search</span></a>' +
    '<a class="nav-button" data-page="watchlist"><span class="nav-text">Watchlist</span></a>' +
    '<a class="nav-button" data-page="settings"><span class="nav-text">Settings</span></a>';
  document.body.appendChild(nav);
});

afterEach(() => {
  document.querySelector('nav.sidebar-nav')?.remove();
  vi.restoreAllMocks();
});

async function openWith(query: string, isAdmin = true) {
  useHelpStore.setState({ isAdmin });
  render(<HelpSheet />);
  act(() => useHelpStore.getState().openSheet());
  const field = await screen.findByRole('searchbox');
  fireEvent.change(field, { target: { value: query } });
  return field;
}

describe('inline results', () => {
  it('groups the rows and marks what matched', async () => {
    await openWith('download path');
    const settings = await screen.findByRole('group', { name: 'Settings' });
    const row = within(settings).getAllByRole('button')[0];
    expect(row).toHaveTextContent('Download path');
    expect(row.querySelectorAll('mark').length).toBeGreaterThan(0);
    expect(screen.getByRole('group', { name: 'Help' })).toBeInTheDocument();
    expect(screen.getByText(/Search music for/)).toBeInTheDocument();
  });

  it('ArrowDown goes from the field to the first row and on; up past the top returns', async () => {
    const field = await openWith('watchlist');
    const rows = () => Array.from(document.querySelectorAll<HTMLElement>('[data-search-row]'));
    field.focus();
    fireEvent.keyDown(field, { key: 'ArrowDown' });
    expect(rows()[0]).toHaveFocus();
    fireEvent.keyDown(rows()[0], { key: 'ArrowDown' });
    expect(rows()[1]).toHaveFocus();
    fireEvent.keyDown(rows()[1], { key: 'ArrowUp' });
    fireEvent.keyDown(rows()[0], { key: 'ArrowUp' });
    expect(field).toHaveFocus();
  });

  it('Enter in the field runs the first row', async () => {
    const field = await openWith('docker deployment');
    await screen.findByRole('group', { name: 'Help' });
    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() =>
      expect(useHelpStore.getState().stack.at(-1)).toEqual({
        view: 'article',
        params: { id: 'gs-docker' },
      }),
    );
    expect(useHelpStore.getState().query).toBe('');
  });

  it('a member sees no settings and no admin actions', async () => {
    await openWith('path', false);
    await screen.findByRole('group', { name: 'Help' });
    expect(screen.queryByText(/Settings ›/)).toBeNull();
    expect(screen.queryByText('Download path')).toBeNull();
    expect(screen.getByRole('searchbox')).toHaveAttribute('placeholder', 'Search help and pages');
  });

  it('says so when nothing matches', async () => {
    await openWith('zqxjv');
    expect(
      await screen.findByText(/Nothing in help, settings or pages matches/),
    ).toBeInTheDocument();
  });
});
