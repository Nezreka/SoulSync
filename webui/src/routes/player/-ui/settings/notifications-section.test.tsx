/**
 * settings, you, notifications: a switch per kind from the server's list,
 * the admin's messages can't be switched off, sub-options show under their
 * kind, quiet hours and the digest open their times, and a test can be sent.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  prefs: {} as Record<string, unknown>,
  puts: [] as Record<string, unknown>[],
  posts: [] as string[],
}));

const KINDS = [
  { id: 'new_episode', label: 'New episodes', help: 'A new episode.', default: true },
  { id: 'featured_movie', label: 'Featured films', help: 'Good films.', default: true },
  { id: 'download_ready', label: 'Downloads ready', help: 'Done.', default: true, device: 'phone' },
  {
    id: 'admin_message',
    label: 'Messages from the admin',
    help: 'Always.',
    default: true,
    locked: true,
  },
  { id: 'home_memories', label: 'Memories', help: 'On this day.', default: false },
];

vi.mock('@/app/api-client', () => {
  const run = (method: string) => (url: string, opts?: { json?: Record<string, unknown> }) => ({
    json: async () => {
      if (method === 'put' && url === 'player/notifications/prefs') {
        api.puts.push(opts?.json ?? {});
        const { kinds, ...rest } = opts?.json ?? {};
        api.prefs = {
          ...api.prefs,
          ...rest,
          kinds: { ...(api.prefs.kinds as object), ...(kinds as object) },
        };
      }
      if (method === 'post') api.posts.push(url);
      if (url === 'player/notifications/prefs') return { prefs: api.prefs, kinds: KINDS };
      return { notifications: [], unread: 0, latest_id: 0 };
    },
  });
  return {
    refusalText: async (_e: unknown, fallback: string) => fallback,
    apiClient: { get: run('get'), put: run('put'), post: run('post'), delete: run('delete') },
  };
});

const { NotificationsSection } = await import('./notifications');

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NotificationsSection />
    </QueryClientProvider>,
  );
}

const sw = (name: string) => screen.getByRole('switch', { name });

beforeEach(() => {
  api.puts = [];
  api.posts = [];
  api.prefs = {
    kinds: {
      new_episode: true,
      featured_movie: true,
      download_ready: true,
      admin_message: true,
      home_memories: false,
    },
    featured_not_in_library: false,
    memories_time: '09:00',
    quiet_hours: false,
    quiet_start: '22:00',
    quiet_end: '08:00',
    digest: false,
    digest_time: '18:00',
    timezone: '',
  };
});

describe('notifications settings', () => {
  it('has a switch per kind and saves just the one changed, with this browser zone', async () => {
    mount();
    await waitFor(() => expect(sw('New episodes')).toHaveAttribute('aria-checked', 'true'));
    fireEvent.click(sw('New episodes'));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0].kinds).toEqual({ new_episode: false });
    expect(typeof api.puts[0].timezone).toBe('string');
    await waitFor(() => expect(sw('New episodes')).toHaveAttribute('aria-checked', 'false'));
  });

  it("the admin's messages are always on and can't be switched", async () => {
    mount();
    await waitFor(() => expect(sw('Messages from the admin')).toBeDisabled());
    expect(sw('Messages from the admin')).toHaveAttribute('aria-checked', 'true');
  });

  it('shows the sub-options under their kind', async () => {
    mount();
    await waitFor(() => expect(sw('Also films not in your library')).toBeInTheDocument());
    expect(screen.queryByLabelText('Memories arrive at')).toBeNull();
    fireEvent.click(sw('Memories'));
    await waitFor(() => expect(screen.getByLabelText('Memories arrive at')).toHaveValue('09:00'));
    expect(screen.getByText(/Each phone can turn it off/)).toBeInTheDocument();
  });

  it('quiet hours and the digest open their times, and a time saves', async () => {
    mount();
    await waitFor(() => expect(sw('Quiet hours')).not.toBeDisabled());
    fireEvent.click(sw('Quiet hours'));
    const start = await screen.findByLabelText('Quiet hours start');
    fireEvent.change(start, { target: { value: '23:30' } });
    await waitFor(() => expect(api.puts.at(-1)).toMatchObject({ quiet_start: '23:30' }));
    fireEvent.click(sw('Daily digest'));
    expect(await screen.findByLabelText('Send the digest at')).toHaveValue('18:00');
  });

  it('sends a test', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Send test' }));
    await waitFor(() => expect(api.posts).toContain('player/notifications/test'));
    expect(await screen.findByText(/It is in the bell/)).toBeInTheDocument();
  });
});
