/**
 * the settings page: one section at a time, the open one in ?section=, the
 * server half only for admins, and on a phone the list first with a way back.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const nav = vi.hoisted(() => ({
  search: {} as Record<string, unknown>,
  listeners: new Set<() => void>(),
  admin: true,
  set(next: Record<string, unknown>) {
    nav.search = next;
    nav.listeners.forEach((fn) => fn());
  },
}));

vi.mock('@tanstack/react-router', async () => {
  const { useSyncExternalStore } = await import('react');
  const useSearch = () =>
    useSyncExternalStore(
      (fn) => {
        nav.listeners.add(fn);
        return () => void nav.listeners.delete(fn);
      },
      () => nav.search,
    );
  return {
    createFileRoute: () => (options: Record<string, unknown>) => ({ options, useSearch }),
    useRouteContext: () => ({
      shell: { profile: { profileId: 1, isAdmin: nav.admin, name: 'Boulder' } },
    }),
    Link: ({
      to,
      search,
      children,
      ...rest
    }: {
      to: string;
      search?: { section?: string };
      children: React.ReactNode;
    }) => (
      <a
        href={`${to}${search?.section ? `?section=${search.section}` : ''}`}
        {...rest}
        onClick={(e) => {
          e.preventDefault();
          nav.set(search ?? {});
        }}
      >
        {children}
      </a>
    ),
  };
});

vi.mock('@/app/api-client', () => {
  const body = (url: string) => {
    if (url === 'player/settings') return { settings: { autoplay_next: true } };
    if (url === 'player/commercials/config') return { enabled: false, can_edit: nav.admin };
    if (url === 'player/commercials/status') return { total_count: 0 };
    if (url === 'player/issues')
      return {
        success: true,
        can_manage: nav.admin,
        open_count: 3,
        issues: [
          {
            id: 1,
            title: 'Dune',
            item_type: 'movie',
            category: 'playback',
            status: 'open',
            note: 'stops at 40 minutes',
            profile_name: 'Kid',
            file_path: '/media/dune.mkv',
          },
        ],
      };
    if (url === 'player/libraries') return { libraries: [{ id: 1, name: 'Movies' }] };
    if (url === 'player/setup')
      return {
        done: true,
        server_name: 'The Den',
        is_admin: nav.admin,
        libraries: [{ id: 2, kind: 'movie', name: 'Movies 4K', is_default: false, folders: [] }],
        hidden: { '2': [3] },
        profiles: [{ id: 3, name: 'Kid', is_admin: false }],
      };
    return {};
  };
  const run = (url: string) => ({ json: async () => body(url) });
  return {
    refusalText: async (_e: unknown, fallback: string) => fallback,
    apiClient: { get: run, put: run, post: run, delete: run },
  };
});

vi.mock('../sidebar-settings', () => ({ SidebarSettings: () => <div>sidebar editor</div> }));
vi.mock('../shell', () => ({
  ACCENT_PALETTE: Object.fromEntries(
    ['emerald', 'violet', 'ocean', 'amber', 'rose', 'crimson'].map((id) => [
      id,
      { rgb: '1, 2, 3', lightRgb: '1, 2, 3' },
    ]),
  ),
}));

const { Route } = await import('../../settings');
const Page = (Route as unknown as { options: { component: () => React.ReactElement } }).options
  .component;
const validate = (
  Route as unknown as {
    options: { validateSearch: (s: Record<string, unknown>) => Record<string, unknown> };
  }
).options.validateSearch;

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Page />
    </QueryClientProvider>,
  );
}

const view = (container: HTMLElement) =>
  container.querySelector('[data-view]')?.getAttribute('data-view');
const sectionTitle = () => screen.getByRole('heading', { level: 2 });
const list = () => screen.getByRole('navigation', { name: 'Settings sections' });

afterEach(() => {
  nav.search = {};
  nav.admin = true;
});

describe('player settings', () => {
  it('opens on Playback with the list as the first screen', async () => {
    const { container } = mount();
    expect(sectionTitle()).toHaveTextContent('Playback');
    expect(view(container)).toBe('list');
    const playback = within(list()).getByRole('link', { name: /Playback/ });
    expect(playback).toHaveAttribute('aria-current', 'page');
    expect(await screen.findByRole('switch', { name: 'Autoplay next episode' })).toBeTruthy();
  });

  it('picking a section puts it in the url and shows only it', async () => {
    const { container } = mount();
    fireEvent.click(within(list()).getByRole('link', { name: /Subtitles/ }));
    expect(nav.search).toEqual({ section: 'subtitles' });
    await waitFor(() => expect(sectionTitle()).toHaveTextContent('Subtitles'));
    expect(view(container)).toBe('section');
    expect(screen.getByRole('radiogroup', { name: 'Subtitle size' })).toBeTruthy();
    expect(screen.queryByRole('switch', { name: 'Autoplay next episode' })).toBeNull();
    expect(screen.getByText('This screen')).toBeTruthy();
    const link = within(list()).getByRole('link', { name: /Subtitles/ });
    expect(link).toHaveAttribute('aria-current', 'page');
    expect(link).toHaveAttribute('href', '/player/settings?section=subtitles');
  });

  it('reads the section from the url, and ignores one it does not know', () => {
    expect(validate({ section: 'picture' })).toEqual({ section: 'picture' });
    expect(validate({ section: 'nope' })).toEqual({});
    expect(validate({})).toEqual({});
    nav.search = { section: 'look' };
    mount();
    expect(sectionTitle()).toHaveTextContent('Look & feel');
    expect(screen.getByText('All screens')).toBeTruthy();
  });

  it('an admin sees the server half, with the open issue count', async () => {
    mount();
    expect(screen.getByText('This server · admin')).toBeTruthy();
    for (const name of [/Setup & libraries/, /Downloads/, /Live guide/, /Reported issues/]) {
      expect(within(list()).getByRole('link', { name })).toBeTruthy();
    }
    expect(await within(list()).findByLabelText('3 open')).toHaveTextContent('3');
    act(() => nav.set({ section: 'setup' }));
    expect(sectionTitle()).toHaveTextContent('Setup & libraries');
    expect(screen.getByRole('button', { name: /Edit libraries/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Open player setup/ })).toBeTruthy();
    expect(await screen.findByText("Kid can't see Movies 4K.")).toBeTruthy();
    expect(await screen.findByText('The Den')).toBeTruthy();
  });

  it('a member never sees the server half, even from a link', async () => {
    nav.admin = false;
    nav.search = { section: 'downloads' };
    const { container } = mount();
    await waitFor(() => expect(screen.queryByText('This server · admin')).toBeNull());
    expect(sectionTitle()).toHaveTextContent('Playback');
    expect(view(container)).toBe('list');
    for (const name of [/Setup & libraries/, /Downloads/, /Live guide/]) {
      expect(within(list()).queryByRole('link', { name })).toBeNull();
    }
    // their own reports stay, with their things
    expect(within(list()).getByRole('link', { name: /What you flagged/ })).toBeTruthy();
  });

  it('on a phone a section has a way back to the list', async () => {
    nav.search = { section: 'picture' };
    const { container } = mount();
    expect(view(container)).toBe('section');
    fireEvent.click(screen.getByText('All settings').closest('a')!);
    expect(nav.search).toEqual({});
    await waitFor(() => expect(view(container)).toBe('list'));
  });

  it('arrow keys move through the list', () => {
    mount();
    const links = within(list()).getAllByRole('link');
    links[0].focus();
    fireEvent.keyDown(links[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(links[1]);
    fireEvent.keyDown(links[1], { key: 'End' });
    expect(document.activeElement).toBe(links[links.length - 1]);
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(document.activeElement).toBe(links[0]);
    fireEvent.keyDown(links[0], { key: 'ArrowUp' });
    expect(document.activeElement).toBe(links[0]);
  });
});
