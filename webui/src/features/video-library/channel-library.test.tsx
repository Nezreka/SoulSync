import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ChannelLibrary, type ChannelLibraryData } from './channel-library';

const lib = (over: Partial<ChannelLibraryData> = {}): ChannelLibraryData => ({
  files: [
    {
      history_id: 1,
      video_id: 'v1',
      title: 'Why the sky is blue',
      published: '2026-03-01',
      downloaded_at: '2026-03-02 10:00:00',
      path: '/yt/Kurz/Season 2026/v1.mp4',
      folder: '/yt/Kurz/Season 2026',
      size: 1_500_000_000,
      exists: true,
      quality: '1080p',
    },
    {
      history_id: 2,
      video_id: 'v2',
      title: 'A missing one',
      published: '2025-01-01',
      downloaded_at: '',
      path: '/yt/Kurz/Season 2025/v2.mp4',
      folder: '/yt/Kurz/Season 2025',
      size: null,
      exists: false,
      quality: null,
    },
  ],
  count: 1,
  size: 1_500_000_000,
  folder: '/yt/Kurz',
  quality: [{ label: '1080p', count: 1 }],
  waiting: 2,
  retention: { policy: 'count_10', words: 'Keeps the newest 10', due: 0 },
  attention: [{ kind: 'missing', count: 1, video_ids: ['v2'], words: 'gone from disk' }],
  ...over,
});

function stubFetch(responses: Record<string, unknown>) {
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const key = Object.keys(responses).find((k) => url.endsWith(k)) ?? '';
      const body = responses[key];
      return {
        ok: body !== undefined,
        status: body === undefined ? 403 : 200,
        json: async () => body,
      };
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a channel as a library', () => {
  it('says what is on disk, what needs attention, and lists the files', async () => {
    stubFetch({ '/library/UC1': lib() });
    render(<ChannelLibrary channelId="UC1" title="Kurzgesagt" />);
    expect(await screen.findByText('video on disk', { exact: false })).toBeTruthy();
    expect(screen.getAllByText('1.4 GB', { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByText('2 waiting to download')).toBeTruthy();
    expect(screen.getByText('1 video gone from disk')).toBeTruthy();
    expect(screen.getByText('Why the sky is blue')).toBeTruthy();
    expect(screen.getByText('Gone from disk')).toBeTruthy();
  });

  it('draws nothing for someone who may not manage files', async () => {
    stubFetch({});
    const { container } = render(<ChannelLibrary channelId="UC1" title="Kurz" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
  });

  it('deletes a picked video after asking, and offers undo', async () => {
    const calls = stubFetch({
      '/library/UC1': lib(),
      '/library/UC1/delete': {
        ...lib({ files: [], count: 0, size: 0 }),
        removed: 1,
        freed: 1_500_000_000,
        failed: [],
        undo: [{ history_id: 1, trash_path: '/trash/1' }],
      },
      '/library/UC1/undo': { ...lib(), restored: 1 },
    });
    window.showConfirmDialog = vi.fn(async () => true);
    window.SoulSyncVideoDetail = { refreshPage: vi.fn() };
    render(<ChannelLibrary channelId="UC1" title="Kurz" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Why the sky is blue' }));
    await screen.findByText('Undo');
    expect(calls.find((c) => c.url.endsWith('/delete'))?.body).toEqual({
      video_ids: ['v1'],
      forget: false,
    });
    fireEvent.click(screen.getByText('Undo'));
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('/undo'))?.body).toEqual({
        undo: [{ history_id: 1, trash_path: '/trash/1' }],
      }),
    );
  });

  it('gets missing files again (delete with forget)', async () => {
    const calls = stubFetch({
      '/library/UC1': lib(),
      '/library/UC1/delete': { ...lib(), removed: 1, freed: 0, failed: [], undo: [] },
    });
    window.showConfirmDialog = vi.fn(async () => true);
    render(<ChannelLibrary channelId="UC1" title="Kurz" />);
    fireEvent.click(await screen.findByText('Get them again'));
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('/delete'))?.body).toEqual({
        video_ids: ['v2'],
        forget: true,
      }),
    );
  });
});
