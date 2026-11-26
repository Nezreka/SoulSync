/**
 * the heartbeat and the admin's orders from Server Activity:
 *
 * - every screen beats, sharing or not (the server keeps members from
 *   seeing a profile that doesn't share), and says how it plays
 * - a stop in the beat's answer stops playback and shows the admin's words
 *   in the player's own dialog
 * - a message shows without stopping
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const engineState = {
  item: { type: 'movie', id: '7', title: 'Heat', subtitle: '1995' } as Record<
    string,
    unknown
  > | null,
  status: 'playing' as string,
  position: 600,
  duration: 6000,
  live: false,
  source: { via: 'disk', tookMs: 12, fileId: 31, mime: 'video/mp4' } as Record<
    string,
    unknown
  > | null,
  transcode: {
    video: 'h264',
    audio: 'aac',
    reasons: ['HEVC video to H.264'],
    quality: '720p',
    encoder: 'CPU',
  },
};
const engine = {
  getState: () => ({ ...engineState }),
  configure: vi.fn(),
  stop: vi.fn(() => {
    engineState.item = null;
    engineState.status = 'idle';
  }),
};

vi.mock('./use-player', () => ({ getEngine: () => engine, usePlayer: () => ({ ...engineState }) }));
vi.mock('./tuner', () => ({ currentChannel: () => null }));
vi.mock('../../-engine/browser', () => ({ screenId: () => 'screen-1' }));
vi.mock('../../-player.api', () => ({
  playerSettingsQuery: () => ({
    queryKey: ['player-settings-test'],
    queryFn: async () => ({
      settings: { share_activity: false, autoplay_next: true, still_watching_after: 3 },
    }),
  }),
}));

import { usePlayerSync } from './presence';
import { ServerOrderHost } from './server-orders';

function Screen() {
  usePlayerSync();
  return <ServerOrderHost />;
}

let answers: unknown[] = [];
const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
  const body = init?.method === 'POST' ? (answers.shift() ?? { success: true }) : { success: true };
  return new Response(JSON.stringify(body), { status: 200 });
});

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Screen />
    </QueryClientProvider>,
  );
}

const beats = () =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init!.body)));

beforeEach(() => {
  answers = [];
  fetchMock.mockClear();
  engine.stop.mockClear();
  engineState.item = { type: 'movie', id: '7', title: 'Heat', subtitle: '1995' };
  engineState.status = 'playing';
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the heartbeat', () => {
  it("beats even when the profile doesn't share, and says how it plays", async () => {
    mount();
    await waitFor(() => expect(beats()).toHaveLength(1));
    const [b] = beats();
    expect(b.session).toBe('screen-1');
    expect(b.item_type).toBe('movie');
    expect(b.stream).toEqual({
      via: 'disk',
      file_id: 31,
      mime: 'video/mp4',
      transcode: {
        video: 'h264',
        audio: 'aac',
        quality: '720p',
        reasons: ['HEVC video to H.264'],
        encoder: 'CPU',
      },
    });
  });

  it("stops on the admin's stop and shows the admin's words", async () => {
    answers = [{ success: true, stop: { message: 'Bedtime, see you tomorrow.' } }];
    mount();
    await waitFor(() => expect(engine.stop).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('dialog', { name: 'Stream stopped' })).toBeTruthy();
    expect(screen.getByText('Bedtime, see you tomorrow.')).toBeTruthy();
    act(() => screen.getByRole('button', { name: 'OK' }).click());
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows a message without stopping', async () => {
    answers = [
      { success: true, message: { header: 'Heads up', text: 'Restarting in 10 minutes' } },
    ];
    mount();
    expect(await screen.findByRole('dialog', { name: 'Heads up' })).toBeTruthy();
    expect(screen.getByText('Restarting in 10 minutes')).toBeTruthy();
    expect(engine.stop).not.toHaveBeenCalled();
    act(() => screen.getByRole('button', { name: 'OK' }).click());
  });
});
