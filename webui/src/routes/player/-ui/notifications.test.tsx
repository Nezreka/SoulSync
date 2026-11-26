/**
 * the header bell: the unread count, the list, a row opens its link and is
 * read, mark all read, dismiss, and an empty list says what will show.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  list: { notifications: [] as unknown[], unread: 0, latest_id: 0 },
  posts: [] as { url: string; json: unknown }[],
  deletes: [] as string[],
  pushed: [] as string[],
}));

vi.mock('@tanstack/react-router', () => ({
  useRouter: () => ({ history: { push: (to: string) => api.pushed.push(to) } }),
  Link: ({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) => (
    <a href="/player/settings?section=notifications" onClick={onClick}>
      {children}
    </a>
  ),
}));

vi.mock('@/app/api-client', () => {
  const ok = (v: unknown) => ({ json: async () => v });
  return {
    refusalText: async (_e: unknown, fallback: string) => fallback,
    apiClient: {
      get: () => ok(api.list),
      post: (url: string, opts?: { json?: unknown }) => {
        api.posts.push({ url, json: opts?.json });
        // the server's side of it, for the refetch that follows
        const ids = (opts?.json as { ids?: number[]; all?: boolean }) ?? {};
        const read = (x: { id: number; read: boolean }) =>
          ids.all || ids.ids?.includes(x.id) ? { ...x, read: true } : x;
        const notes = (api.list.notifications as { id: number; read: boolean }[]).map(read);
        api.list = {
          ...api.list,
          notifications: notes,
          unread: notes.filter((x) => !x.read).length,
        };
        return ok({ success: true });
      },
      delete: (url: string) => {
        api.deletes.push(url);
        const id = Number(url.split('/').at(-1));
        api.list = {
          ...api.list,
          notifications: (api.list.notifications as { id: number }[]).filter((x) => x.id !== id),
        };
        return ok({ success: true });
      },
      put: () => ok({}),
    },
  };
});

const { NotificationBell, when } = await import('./notifications');

const now = Date.now() / 1000;
const item = (id: number, extra: Record<string, unknown> = {}) => ({
  id,
  kind: 'new_episode',
  title: `Note ${id}`,
  body: 'S1 E2',
  image: null,
  link: `/player/tv/${id}`,
  created_at: now - 120,
  read: false,
  ...extra,
});

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NotificationBell />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api.list = {
    notifications: [item(3), item(2), item(1, { read: true })],
    unread: 2,
    latest_id: 3,
  };
  api.posts = [];
  api.deletes = [];
  api.pushed = [];
});

describe('notification bell', () => {
  it('shows the unread count and opens the list', async () => {
    mount();
    const bell = await screen.findByRole('button', { name: 'Notifications, 2 unread' });
    expect(within(bell).getByText('2')).toBeInTheDocument();
    fireEvent.click(bell);
    const panel = screen.getByRole('dialog', { name: 'Notifications' });
    expect(within(panel).getAllByRole('listitem')).toHaveLength(3);
    expect(within(panel).getAllByLabelText('unread')).toHaveLength(2);
    expect(within(panel).getAllByText('2m')).toHaveLength(3);
  });

  it('a row opens its link and counts as read', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications, 2 unread' }));
    fireEvent.click(screen.getByText('Note 3'));
    expect(api.pushed).toEqual(['/player/tv/3']);
    expect(api.posts).toEqual([{ url: 'player/notifications/read', json: { ids: [3] } }]);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Notifications, 1 unread' })).toBeInTheDocument(),
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('mark all read, and dismiss takes a row out', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications, 2 unread' }));
    fireEvent.click(screen.getByRole('button', { name: 'Mark all read' }));
    expect(api.posts.at(-1)).toEqual({ url: 'player/notifications/read', json: { all: true } });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Notifications' })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss Note 2' }));
    expect(api.deletes).toEqual(['player/notifications/2']);
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(2));
  });

  it('an empty list says what will show up', async () => {
    api.list = { notifications: [], unread: 0, latest_id: 0 };
    mount();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Notifications' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }));
    expect(
      await screen.findByText(/New episodes, films and invites show up here/),
    ).toBeInTheDocument();
    expect(screen.getByText('Choose what you hear about')).toBeInTheDocument();
  });

  it('when reads short', () => {
    const t = 1_000_000;
    expect(when(t - 30, t * 1000)).toBe('now');
    expect(when(t - 7200, t * 1000)).toBe('2h');
    expect(when(t - 3 * 86_400, t * 1000)).toBe('3d');
  });
});
