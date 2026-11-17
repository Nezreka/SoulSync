/**
 * Now playing against the contract's example payload: the pulse, one card per
 * stream, the open card's detail, and the Stop / Send message sheets.
 */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { ActivityPayload, Session } from '@/shell/server-activity-types';

import { HttpResponse, http, server } from '@/test/msw';

import activity from './__fixtures__/activity.json';
import { usePresence } from './live';
import { NowPlaying, presetChip, sparkPaths } from './now-playing';

const DATA = activity as unknown as ActivityPayload;
const PRESETS = ['Server maintenance, back soon.', 'Please watch at home tonight.'];

function view(
  data: ActivityPayload | null = DATA,
  extra: Partial<Parameters<typeof NowPlaying>[0]> = {},
) {
  const props = {
    data,
    error: null,
    loading: false,
    receivedAt: Date.now(),
    now: Date.now(),
    presets: PRESETS,
    onRetry: vi.fn(),
    onClose: vi.fn(),
    onSeeHistory: vi.fn(),
    ...extra,
  };
  return { ...render(<NowPlaying {...props} />), props };
}

const card = (title: string) => screen.getByRole('article', { name: new RegExp(`^${title},`) });
const open = (title: string) =>
  fireEvent.click(within(card(title)).getByRole('button', { name: new RegExp(title) }));

describe('now playing', () => {
  it('shows the pulse from the summary', () => {
    view();
    const pulse = screen.getByRole('region', { name: 'Server right now' });
    expect(pulse).toHaveTextContent('Streams7');
    expect(pulse).toHaveTextContent('4 watching3 listening');
    expect(pulse).toHaveTextContent('35.7Mbps');
    expect(pulse).toHaveTextContent('15.1 remote of 40 Mbps upload');
    expect(pulse).toHaveTextContent('2 hardware, 1 software');
    expect(pulse).toHaveTextContent('CPU 14%');
    expect(screen.getByRole('meter', { name: /upload/ })).toHaveAttribute('aria-valuenow', '38');
  });

  it('draws no upload bar without a cap, and no CPU without a sample', () => {
    const data = structuredClone(DATA);
    data.summary.upload_cap_kbps = null;
    data.summary.cpu = null;
    view(data);
    expect(screen.queryAllByRole('meter')).toHaveLength(0);
    expect(screen.getByRole('region', { name: 'Server right now' })).toHaveTextContent(
      '15.1 remote, 20.6 home',
    );
  });

  it('one card per stream, keyed and in the server order', () => {
    view();
    const titles = screen.getAllByRole('article').map((a) => a.getAttribute('aria-label'));
    expect(titles).toHaveLength(7);
    expect(titles[0]).toMatch(/^Cold Harbor, playing for dan/);
    expect(within(card('Dune: Part Two')).getByText('Transcode')).toBeInTheDocument();
    expect(within(card('Crimson Sky')).getByText('Buffering')).toBeInTheDocument();
    expect(within(card('The Selfish Gene')).getByText('Paused')).toBeInTheDocument();
  });

  it('an audiobook shows the chapter and the whole book', () => {
    view();
    const book = card('Project Hail Mary');
    expect(within(book).getByText('Chapter 18:20')).toBeInTheDocument();
    expect(within(book).getByText('Book 42%')).toBeInTheDocument();
    expect(within(book).getByText('9h 40m left in the book')).toBeInTheDocument();
  });

  it('opens a card to the file against what is sent, and why', () => {
    view();
    open('Dune: Part Two');
    const dune = card('Dune: Part Two');
    expect(within(dune).getByRole('button', { name: /Dune: Part Two/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    const table = within(dune).getByRole('table');
    expect(table).toHaveTextContent('4K HEVC Main 10 · HDR10');
    expect(table).toHaveTextContent('1080p H.264 · SDR');
    expect(table).toHaveTextContent('62.4 Mbps');
    expect(dune).toHaveTextContent(/Converting because the file is bigger/);
    // plex states no reason: say SoulSync worked it out
    expect(dune).toHaveTextContent('Reason worked out by SoulSync from the stream details.');
    expect(dune).toHaveTextContent('Intel Quick Sync, decode + encode');
    expect(dune).toHaveTextContent('Remote · 73.41.208.17');
    expect(dune).toHaveTextContent('secure, not relayed');
    expect(dune).toHaveTextContent('Plex for Apple TV 8.41');
    expect(dune).toHaveTextContent('tvOS 18.1 · Living room');
    expect(within(dune).getByRole('button', { name: /Preview in SoulSync/ })).toBeInTheDocument();
  });

  it('a jellyfin reason is the server’s own, not marked worked out', () => {
    view();
    open('The Wild Robot');
    expect(card('The Wild Robot')).not.toHaveTextContent('worked out by SoulSync');
  });

  it('a direct play says the file is played as is', () => {
    view();
    open('Cold Harbor');
    expect(card('Cold Harbor')).toHaveTextContent('The file, played as is');
    expect(within(card('Cold Harbor')).queryByRole('table')).toBeNull();
  });

  it('stops a stream with the picked message', async () => {
    let body: unknown = null;
    server.use(
      http.post('*/api/server-activity/stop', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true, message_shown: true });
      }),
    );
    const { props } = view();
    open('Dune: Part Two');
    fireEvent.click(within(card('Dune: Part Two')).getByRole('button', { name: /Stop stream/ }));
    expect(screen.getByText('Stop Dune: Part Two for maya?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Please watch at home tonight' }));
    expect(screen.getByRole('textbox', { name: 'Message' })).toHaveValue(
      'Please watch at home tonight.',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Stop stream' }));
    await screen.findByText('Stopped. maya saw your message.');
    expect(body).toEqual({ session_key: '27', message: 'Please watch at home tonight.' });
    expect(props.onRetry).toHaveBeenCalled();
  });

  it('says why a stop failed, in the sheet', async () => {
    server.use(
      http.post('*/api/server-activity/stop', () =>
        HttpResponse.json(
          {
            ok: false,
            code: 'plex_pass',
            error: 'Stopping a stream needs Plex Pass on the server.',
          },
          { status: 400 },
        ),
      ),
    );
    view();
    open('Dune: Part Two');
    fireEvent.click(within(card('Dune: Part Two')).getByRole('button', { name: /Stop stream/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop stream' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('needs Plex Pass');
  });

  it('Escape in the sheet closes the sheet, not the drawer', () => {
    const outer = vi.fn();
    render(
      <div onKeyDown={outer}>
        <NowPlaying
          data={DATA}
          error={null}
          loading={false}
          receivedAt={0}
          now={0}
          presets={[]}
          onRetry={() => {}}
          onClose={() => {}}
          onSeeHistory={() => {}}
        />
      </div>,
    );
    open('Dune: Part Two');
    fireEvent.click(within(card('Dune: Part Two')).getByRole('button', { name: /Stop stream/ }));
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Message' }), { key: 'Escape' });
    expect(screen.queryByText(/for maya\?/)).toBeNull();
    expect(outer).not.toHaveBeenCalled();
  });

  it('send message is offered only where the player can show one', async () => {
    let body: unknown = null;
    server.use(
      http.post('*/api/server-activity/message', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true });
      }),
    );
    view();
    open('Dune: Part Two');
    expect(
      within(card('Dune: Part Two')).queryByRole('button', { name: /Send message/ }),
    ).toBeNull();
    open('Cold Harbor');
    fireEvent.click(within(card('Cold Harbor')).getByRole('button', { name: /Send message/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), {
      target: { value: 'dinner in 10' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await screen.findByText(/Sent\. It shows on dan's Shield TV/);
    expect(body).toEqual({
      session_key: 'jellyfin:8f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e',
      message: 'dinner in 10',
    });
  });

  it('says so when stopping needs plex pass', () => {
    const data = structuredClone(DATA);
    const s = data.sessions.find((x) => x.id === 'plex:27')!;
    s.controls = {
      stop: false,
      message: false,
      stop_note: 'Stopping a stream needs Plex Pass on the server.',
    };
    view(data);
    open('Dune: Part Two');
    expect(card('Dune: Part Two')).toHaveTextContent('Stopping a stream needs Plex Pass');
    expect(
      within(card('Dune: Part Two')).queryByRole('button', { name: /Stop stream/ }),
    ).toBeNull();
  });

  it('nothing playing, no server, loading and a failed load each say so', () => {
    const empty = { ...structuredClone(DATA), sessions: [] as Session[] };
    const { rerender, props } = view(empty);
    expect(screen.getByText('Nothing playing right now')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'See history' }));
    expect(props.onSeeHistory).toHaveBeenCalled();

    rerender(
      <NowPlaying {...props} data={{ ...DATA, ok: false, reason: 'no_server', sessions: [] }} />,
    );
    expect(screen.getByText('Connect Plex or Jellyfin')).toBeInTheDocument();

    rerender(<NowPlaying {...props} data={null} loading />);
    expect(screen.getByLabelText("Loading what's playing")).toBeInTheDocument();

    rerender(<NowPlaying {...props} data={null} loading={false} error="Something went wrong." />);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(props.onRetry).toHaveBeenCalled();
  });

  it('the bar glides while playing and holds while paused', () => {
    const t0 = 1_000_000;
    const { rerender, props } = view(DATA, { receivedAt: t0, now: t0 });
    expect(within(card('Dune: Part Two')).getByText('1:08:12')).toBeInTheDocument();
    rerender(<NowPlaying {...props} receivedAt={t0} now={t0 + 5000} />);
    expect(within(card('Dune: Part Two')).getByText('1:08:17')).toBeInTheDocument();
    // paused: the clock stays
    expect(within(card('The Selfish Gene')).getByText(/Chapter 8:20/)).toBeInTheDocument();
  });
});

describe('the pieces', () => {
  it('a preset chip is its first clause', () => {
    expect(presetChip('The server is restarting for an update. Back in 5 minutes.')).toBe(
      'The server is restarting for an update',
    );
    expect(presetChip('Server maintenance, back soon.')).toBe('Server maintenance');
  });

  it('the sparkline breaks where no poll ran', () => {
    const at = 10_000;
    const series = {
      window_s: 600,
      peak_kbps: 2000,
      peak_remote_kbps: 0,
      points: [
        { t: at - 300, total_kbps: 1000, remote_kbps: 0, streams: 1 },
        { t: at - 297, total_kbps: 1200, remote_kbps: 0, streams: 1 },
        { t: at - 6, total_kbps: 2000, remote_kbps: 0, streams: 1 },
        { t: at - 3, total_kbps: 1800, remote_kbps: 0, streams: 1 },
      ],
    };
    expect(sparkPaths(series, at).total.match(/M/g)).toHaveLength(2);
  });

  it('a stream that ends fades out before it goes', async () => {
    vi.useFakeTimers();
    const seen: { key: string; leaving: boolean }[][] = [];
    function List({ items }: { items: string[] }) {
      const out = usePresence(items, (x) => x);
      seen.push(out.map(({ key, leaving }) => ({ key, leaving })));
      return null;
    }
    const { rerender } = render(<List items={['a', 'b']} />);
    rerender(<List items={['a']} />);
    await act(async () => {});
    expect(seen.at(-1)).toEqual([
      { key: 'a', leaving: false },
      { key: 'b', leaving: true },
    ]);
    await act(async () => {
      vi.advanceTimersByTime(400);
    });
    expect(seen.at(-1)).toEqual([{ key: 'a', leaving: false }]);
    vi.useRealTimers();
  });
});
