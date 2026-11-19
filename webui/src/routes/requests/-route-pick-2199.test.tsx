import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { videoPickTargets } from './-ui/pick-queue';

const inspector = vi.hoisted(() => ({ open: vi.fn() }));
vi.mock('@/features/downloads/inspector-modal', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  openRequestPickInspector: inspector.open,
}));

/**
 * #2199: approved requests that wait for a release to be picked. The admin
 * sets it per kind (Approval settings), can go the other way for one ask
 * from its menu, picks from the Pick a release tab with each kind's own
 * picker, and the requester sees it waiting.
 */

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const group = {
  key: 'album:blue',
  profile_id: 3,
  requester_name: 'Kim',
  kind: 'album',
  title: 'Blue',
  artist: 'Joni Mitchell',
  album: 'Blue',
  image_url: null,
  track_ids: ['1', '2'],
  tracks: [
    { id: '1', title: 'All I Want' },
    { id: '2', title: 'My Old Man' },
  ],
  track_count: 2,
  created_at: '2026-09-24 10:00:00',
  status: 'pending',
};

const bookAsk = {
  id: 41,
  profile_id: 3,
  requester_name: 'Kim',
  kind: 'audiobook',
  item_key: 'B0TEST',
  title: 'Wind and Truth',
  subtitle: 'Brandon Sanderson',
  status: 'pending',
  created_at: '2026-09-24 10:00:00',
};

function list(extra: Record<string, unknown> = {}) {
  return {
    success: true,
    asks_first: false,
    counts: { pending: 1 },
    pending: [group],
    history: [],
    pending_media: [bookAsk],
    media_history: [],
    acquisition: { music: 'auto', movie: 'auto', tv: 'auto', audiobook: 'auto' },
    awaiting: 0,
    ...extra,
  };
}

const awaitingMusic = {
  ...group,
  key: 'track:solo',
  kind: 'track',
  title: 'River',
  track_ids: ['9'],
  tracks: [{ id: '9', title: 'River' }],
  track_count: 1,
  status: 'awaiting_selection',
  approved_at: '2026-09-24 12:00:00',
};

const awaitingVideo = {
  id: 77,
  kind: 'show',
  tmdb_id: 95396,
  title: 'Severance',
  year: 2022,
  poster_url: null,
  approved_at: '2026-09-24 11:00:00',
  requesters: ['Kim'],
  request_ids: [77],
  seasons: [
    { season_number: 1, episodes: [1, 2, 3] },
    { season_number: 2, episodes: [4] },
  ],
  downloading: false,
};

function renderRequests(isAdmin: boolean, entry = '/requests') {
  window.SoulSyncWebShellBridge = createShellBridge({
    getCurrentProfileContext: vi.fn(() => ({ profileId: isAdmin ? 1 : 3, isAdmin })),
  });
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

async function openMenu(name: string) {
  const trigger = await screen.findByRole('button', { name });
  fireEvent.click(trigger);
  return screen.findByRole('menu');
}

describe('requests: picking the release (#2199)', () => {
  let calls: Array<{ url: string; method: string; body?: string }>;
  let payload: Record<string, unknown>;
  let awaiting: Record<string, unknown>;
  let videoAwaiting: Record<string, unknown>;

  beforeEach(() => {
    calls = [];
    payload = list();
    awaiting = { success: true, music: [], media: [] };
    videoAwaiting = { success: true, titles: [] };
    window.refreshMusicRequestsBadge = vi.fn();
    window.showToast = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const req = input instanceof Request ? input : null;
        const url =
          input instanceof Request ? input.url : input instanceof URL ? input.href : input;
        const method = (req?.method || init?.method || 'GET').toUpperCase();
        const body = req && method !== 'GET' ? await req.clone().text() : (init?.body as string);
        calls.push({ url, method, body });
        if (url.includes('/api/video/requests/awaiting')) return json(videoAwaiting);
        if (url.includes('/api/requests/awaiting')) return json(awaiting);
        if (url.includes('/api/requests/acquisition'))
          return json({ success: true, modes: { music: 'manual' } });
        if (url.includes('/api/requests/music/seen')) return json({ success: true, marked: 0 });
        if (url.includes('/approve')) return json({ success: true, approved: 2 });
        if (url.includes('/api/requests/music') && method === 'GET') return json(payload);
        if (url.includes('/api/issues/counts')) {
          return json({
            success: true,
            counts: { open: 0, in_progress: 0, resolved: 0, dismissed: 0, total: 0 },
          });
        }
        return json({ success: true });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.SoulSyncWebShellBridge = undefined;
    window.refreshMusicRequestsBadge = undefined;
    window.VideoDownload = undefined;
  });

  it('keeps Approve as the setting and offers the other way in the menu', async () => {
    renderRequests(true);
    expect(await screen.findByText('Blue')).toBeInTheDocument();

    // the plain button sends no choice: the kind's setting decides
    fireEvent.click(screen.getAllByRole('button', { name: 'Approve' })[0]!);
    await waitFor(() => {
      const hit = calls.find((c) => c.url.includes('/api/requests/music/approve'));
      expect(JSON.parse(hit?.body || '{}')).toEqual({ profile_id: 3, key: 'album:blue' });
    });

    const menu = await openMenu('More for Blue');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Approve & pick release' }));
    await waitFor(() => {
      const hits = calls.filter((c) => c.url.includes('/api/requests/music/approve'));
      expect(JSON.parse(hits[1]?.body || '{}')).toEqual({
        profile_id: 3,
        key: 'album:blue',
        acquisition: 'manual',
      });
    });
  });

  it('words the other way after the setting: manual books offer auto-download', async () => {
    payload = list({
      acquisition: { music: 'auto', movie: 'auto', tv: 'auto', audiobook: 'manual' },
    });
    renderRequests(true);
    const menu = await openMenu('More for Wind and Truth');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Approve & auto-download' }));
    await waitFor(() => {
      const hit = calls.find((c) => c.url.includes('/api/requests/media/41/approve'));
      expect(JSON.parse(hit?.body || '{}')).toEqual({ acquisition: 'auto' });
    });
  });

  it('has no pick tab while every kind downloads automatically and nothing waits', async () => {
    renderRequests(true);
    expect(await screen.findByText('Blue')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Pick a release/ })).not.toBeInTheDocument();
  });

  it('lists what waits for a pick and opens each kind’s own picker', async () => {
    awaiting = {
      success: true,
      music: [awaitingMusic],
      media: [
        {
          ...bookAsk,
          id: 52,
          status: 'awaiting_selection',
          asin: 'B0TEST',
          resolved_at: '2026-09-24 09:00:00',
        },
      ],
    };
    videoAwaiting = { success: true, titles: [awaitingVideo] };
    const manualSearch = vi.fn();
    window.VideoDownload = { manualSearch };
    renderRequests(true, '/requests?tab=pick');

    expect(await screen.findByRole('tab', { name: 'Pick a release · 3' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(await screen.findByText('River')).toBeInTheDocument();
    expect(screen.getByText('Severance')).toBeInTheDocument();
    expect(screen.getAllByText(/Kim asked · approved/).length).toBe(3);

    // a show: one button per season, a season with one episode searches it
    fireEvent.click(screen.getByRole('button', { name: 'Season 1' }));
    expect(manualSearch).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Severance',
        scope: 'season',
        season: 1,
        mediaId: 95396,
        mediaSource: 'tmdb',
        tmdbId: 95396,
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'S02E04' }));
    expect(manualSearch).toHaveBeenLastCalledWith(
      expect.objectContaining({ scope: 'episode', season: 2, episode: 4 }),
    );

    // oldest approval first: the book, the show, then the track.
    // a held track opens the candidate inspector for the requester's row
    const pickButtons = screen.getAllByRole('button', { name: 'Pick release' });
    fireEvent.click(pickButtons[1]!);
    expect(inspector.open).toHaveBeenCalledWith(
      expect.objectContaining({ profileId: 3, trackId: '9', name: 'River' }),
    );
    // a held book opens the audiobook release picker
    fireEvent.click(pickButtons[0]!);
    expect(await screen.findByRole('dialog', { name: /Releases for Wind and Truth/ })).toBeTruthy();
  });

  it('hands a held ask back to the automatic download from its menu', async () => {
    videoAwaiting = { success: true, titles: [{ ...awaitingVideo, kind: 'movie', seasons: [] }] };
    renderRequests(true, '/requests?tab=pick');
    const menu = await openMenu('More for Severance');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Download automatically instead' }));
    await waitFor(() =>
      expect(
        calls.some((c) => c.url.includes('/api/video/requests/77/auto') && c.method === 'POST'),
      ).toBe(true),
    );
  });

  it('lets an admin set each kind in Approval settings', async () => {
    renderRequests(true);
    fireEvent.click(await screen.findByRole('button', { name: 'Approval settings' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Movies: I pick the release' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      const hit = calls.find(
        (c) => c.url.includes('/api/requests/acquisition') && c.method === 'PUT',
      );
      expect(JSON.parse(hit?.body || '{}')).toEqual({ modes: { movie: 'manual' } });
    });
  });

  it('shows the requester their ask waiting for a pick, with no queue or settings', async () => {
    payload = list({
      pending: [],
      pending_media: [],
      history: [
        {
          id: 9,
          profile_id: 3,
          requester_name: 'Kim',
          group_key: 'album:court',
          kind: 'album',
          title: 'Court and Spark',
          artist: 'Joni Mitchell',
          tracks: [],
          track_count: 11,
          status: 'awaiting_selection',
          resolved_at: '2026-09-24 11:00:00',
        },
      ],
      acquisition: { music: 'manual', movie: 'auto', tv: 'auto', audiobook: 'auto' },
    });
    renderRequests(false, '/requests?tab=on-the-way');
    expect(await screen.findByText('Court and Spark')).toBeInTheDocument();
    expect(screen.getByText('Approved, waiting for a release to be picked')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Pick a release/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approval settings' })).not.toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('/api/requests/awaiting'))).toBe(false);
  });
});

describe('video pick targets', () => {
  it('searches a movie and an episode request as themselves', () => {
    expect(videoPickTargets({ ...awaitingVideo, kind: 'movie', seasons: [] })).toEqual([
      { label: 'Pick release', scope: 'movie' },
    ]);
    expect(
      videoPickTargets({
        ...awaitingVideo,
        kind: 'episode',
        season_number: 1,
        episode_number: 2,
        seasons: [],
      }),
    ).toEqual([{ label: 'Pick release', scope: 'episode', season: 1, episode: 2 }]);
  });
});
