/**
 * The Ctrl K palette (plan 6.3): opens from anywhere, combobox and listbox
 * a11y, arrows, Enter, Esc, groups read out, the Search hand-off, and a
 * member's list.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { installHelpKeys, uninstallHelpKeys } from '../host';
import { useHelpStore } from '../store';
import { loadDocs } from './docs';
import { Palette, closePalette, isPaletteOpen, openPalette, usePalette } from './palette';
import { RECENT_KEY } from './rank';

const DIR = join(process.cwd(), 'static', 'docs-content');
const FILES = readdirSync(DIR).filter((f) => f.endsWith('.js'));

type G = Window & { isPageAllowed?: (id: string) => boolean };
const g = window as unknown as G;

function sidebar() {
  const nav = document.createElement('nav');
  nav.className = 'sidebar-nav';
  nav.innerHTML = ['dashboard', 'search', 'library', 'watchlist', 'tools', 'settings', 'help']
    .map(
      (id) =>
        `<a class="nav-button" data-page="${id}"><span class="nav-text">${id[0].toUpperCase()}${id.slice(1)}</span></a>`,
    )
    .join('');
  document.body.appendChild(nav);
}

beforeAll(async () => {
  await loadDocs(
    async (u) => readFileSync(join(DIR, u.split('/').pop()!), 'utf8'),
    FILES.map((f) => `/static/docs-content/${f}`),
  );
});

beforeEach(() => {
  useHelpStore.setState(useHelpStore.getInitialState(), true);
  usePalette.setState({ open: false, query: '' });
  localStorage.removeItem(RECENT_KEY);
  document.body.setAttribute('data-side', 'music');
  sidebar();
  installHelpKeys();
});

afterEach(() => {
  uninstallHelpKeys();
  document
    .querySelectorAll('nav.sidebar-nav, [data-other-modal], #soulsync-palette-root')
    .forEach((n) => n.remove());
  delete g.isPageAllowed;
  delete window.navigateToPage;
  delete window.SoulSyncWebRouter;
  delete window.showToast;
  vi.restoreAllMocks();
});

function mount(isAdmin = true) {
  useHelpStore.setState({ isAdmin });
  // the root openPalette() would make: it finds this one and renders nothing more
  const host = document.createElement('div');
  host.id = 'soulsync-palette-root';
  document.body.appendChild(host);
  return render(<Palette />, { container: host });
}

const combo = () => screen.getByRole('combobox');
const options = () => screen.queryAllByRole('option');
const activeOption = () => document.getElementById(combo().getAttribute('aria-activedescendant')!);

describe('opening and closing', () => {
  it('Ctrl K opens it with the caret in the field, Ctrl K again closes it', async () => {
    mount();
    const before = document.createElement('button');
    document.body.appendChild(before);
    before.focus();
    fireEvent.keyDown(document.body, { key: 'k', ctrlKey: true });
    const dialog = await screen.findByRole('dialog', { name: 'Search everything' });
    await waitFor(() => expect(within(dialog).getByRole('combobox')).toHaveFocus());
    fireEvent.keyDown(document.body, { key: 'k', ctrlKey: true });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(isPaletteOpen()).toBe(false);
    before.remove();
  });

  it('Cmd K on a Mac does the same', async () => {
    mount();
    fireEvent.keyDown(document.body, { key: 'k', metaKey: true });
    expect(await screen.findByRole('dialog', { name: 'Search everything' })).toBeInTheDocument();
  });

  it('Esc closes it and focus goes back where it was', async () => {
    mount();
    const before = document.createElement('button');
    before.textContent = 'page button';
    document.body.appendChild(before);
    before.focus();
    act(() => openPalette());
    await waitFor(() => expect(combo()).toHaveFocus());
    fireEvent.keyDown(combo(), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(before).toHaveFocus());
    before.remove();
  });

  it('not over another modal', () => {
    mount();
    const other = document.createElement('div');
    other.setAttribute('aria-modal', 'true');
    other.setAttribute('data-other-modal', '');
    document.body.appendChild(other);
    fireEvent.keyDown(document.body, { key: 'k', ctrlKey: true });
    expect(isPaletteOpen()).toBe(false);
  });

  it('with the help sheet open, Ctrl K goes to the sheet’s own field', () => {
    mount();
    act(() => useHelpStore.getState().openSheet());
    const asked = useHelpStore.getState().focusSearch;
    fireEvent.keyDown(document.body, { key: 'k', ctrlKey: true });
    expect(isPaletteOpen()).toBe(false);
    expect(useHelpStore.getState().focusSearch).toBe(asked + 1);
  });
});

describe('the combobox', () => {
  it('is wired as a combobox over a listbox, with labelled groups', async () => {
    mount();
    act(() => openPalette());
    fireEvent.change(combo(), { target: { value: 'scan' } });
    const list = screen.getByRole('listbox', { name: 'Results' });
    expect(combo()).toHaveAttribute('aria-controls', list.id);
    expect(combo()).toHaveAttribute('aria-expanded', 'true');
    expect(combo()).toHaveAttribute('aria-autocomplete', 'list');
    const groups = within(list).getAllByRole('group');
    expect(
      groups.map(
        (gr) =>
          gr.getAttribute('aria-labelledby') &&
          document.getElementById(gr.getAttribute('aria-labelledby')!)?.textContent,
      ),
    ).toEqual(expect.arrayContaining(['Actions', 'Help', 'Music']));
    // the first row is the active one, and says Enter
    expect(activeOption()).toBe(options()[0]);
    expect(options()[0]).toHaveAttribute('aria-selected', 'true');
    expect(options()[0]).toHaveTextContent('Enter');
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/results?$/));
  });

  it('arrows move the active row and wrap', () => {
    mount();
    act(() => openPalette());
    fireEvent.change(combo(), { target: { value: 'scan' } });
    const all = options();
    fireEvent.keyDown(combo(), { key: 'ArrowDown' });
    expect(activeOption()).toBe(all[1]);
    fireEvent.keyDown(combo(), { key: 'ArrowUp' });
    fireEvent.keyDown(combo(), { key: 'ArrowUp' });
    expect(activeOption()).toBe(all[all.length - 1]);
    fireEvent.keyDown(combo(), { key: 'ArrowDown' });
    expect(activeOption()).toBe(all[0]);
  });

  it('Enter on an article opens it in the help sheet', async () => {
    mount();
    act(() => openPalette());
    fireEvent.change(combo(), { target: { value: 'docker deployment' } });
    const help = options().find((o) => o.textContent?.startsWith('Docker & Deployment'))!;
    const i = options().indexOf(help);
    for (let n = 0; n < i; n += 1) fireEvent.keyDown(combo(), { key: 'ArrowDown' });
    fireEvent.keyDown(combo(), { key: 'Enter' });
    expect(isPaletteOpen()).toBe(false);
    const st = useHelpStore.getState();
    expect(st.open).toBe(true);
    expect(st.stack.at(-1)).toEqual({ view: 'article', params: { id: 'gs-docker' } });
  });

  it('the last row hands the words to the Search page', async () => {
    const navigateToPage = vi.fn();
    const navigateToHref = vi.fn(async () => true);
    window.navigateToPage = navigateToPage;
    window.SoulSyncWebRouter = { navigateToHref } as unknown as typeof window.SoulSyncWebRouter;
    mount();
    act(() => openPalette());
    fireEvent.change(combo(), { target: { value: 'boards of canada' } });
    const last = options().at(-1)!;
    expect(last).toHaveTextContent('Search music for “boards of canada”');
    fireEvent.click(last);
    expect(isPaletteOpen()).toBe(false);
    await waitFor(() =>
      expect(navigateToHref).toHaveBeenCalledWith('/search?q=boards%20of%20canada'),
    );
    expect(navigateToPage).toHaveBeenCalledWith('search');
  });

  it('says so when nothing matches, and still offers Search', () => {
    mount();
    act(() => openPalette());
    fireEvent.change(combo(), { target: { value: 'zqxjv' } });
    expect(screen.getByText('Nothing matches “zqxjv”')).toBeInTheDocument();
    expect(options()).toHaveLength(1);
  });

  it('opens on suggestions with nothing typed', () => {
    mount();
    act(() => openPalette());
    expect(screen.getByText('Actions')).toBeInTheDocument();
    expect(options().length).toBeGreaterThan(0);
  });
});

describe('a member', () => {
  it('gets the member placeholder and no admin rows', () => {
    mount(false);
    act(() => openPalette());
    expect(combo()).toHaveAttribute('placeholder', 'Search help and pages');
    fireEvent.change(combo(), { target: { value: 'scan' } });
    const text = options()
      .map((o) => o.textContent)
      .join('\n');
    expect(text).not.toMatch(/Quick scan the library|Deep scan the library/);
    fireEvent.change(combo(), { target: { value: 'download path' } });
    expect(
      options()
        .map((o) => o.textContent)
        .join('\n'),
    ).not.toMatch(/Settings ›/);
    fireEvent.change(combo(), { target: { value: 'health' } });
    expect(
      options()
        .map((o) => o.textContent)
        .join('\n'),
    ).not.toMatch(/Check folders and services/);
    act(() => closePalette());
  });

  it('a page they cannot open is not offered, nor is the Search hand-off', () => {
    g.isPageAllowed = (id) => id !== 'tools' && id !== 'search';
    mount(false);
    act(() => openPalette());
    fireEvent.change(combo(), { target: { value: 'tools' } });
    const text = options()
      .map((o) => o.textContent)
      .join('\n');
    expect(screen.queryByText('Pages')).toBeNull();
    expect(text).not.toMatch(/Search music for/);
  });
});
