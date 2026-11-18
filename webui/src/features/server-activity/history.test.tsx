/**
 * History against the contract's example payloads: rows grouped under their
 * day, the filters reaching the query, a row opening to its timeline and
 * facts, and the empty states.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { HistoryGroup, HistoryPayload } from '@/shell/server-activity-types';

import { HttpResponse, http, server } from '@/test/msw';

import history from './__fixtures__/history.json';
import group from './__fixtures__/history_group.json';
import { clearCache } from './cache';
import { browserZone, isoDay } from './format';
import { History, byDay, formatKb, timelineBands, toQuery, NO_FILTERS } from './history';

// every test starts with nothing read ahead or remembered
beforeEach(() => clearCache());

const DATA = history as unknown as HistoryPayload;

function serve(payload: HistoryPayload = DATA) {
  const queries: URLSearchParams[] = [];
  server.use(
    http.get('*/api/server-activity/history', ({ request }) => {
      queries.push(new URL(request.url).searchParams);
      return HttpResponse.json(payload);
    }),
    http.get('*/api/server-activity/history/:key', ({ params }) =>
      params.key === group.group_key
        ? HttpResponse.json(group)
        : HttpResponse.json({ ok: false, reason: 'not_found' }, { status: 404 }),
    ),
  );
  return queries;
}

const row = (title: string) =>
  screen.getAllByRole('button', { expanded: false }).find((b) => b.textContent?.includes(title))!;

describe('history', () => {
  it('shows the summary and one row per run', async () => {
    // the server answers in the zone the drawer asks for (tz=): here, the test's
    const data = structuredClone(DATA);
    data.days[0].date = isoDay(data.groups[0].started_at, browserZone());
    serve(data);
    render(<History onNavigate={() => {}} />);
    await screen.findByText('Everything In Its Right Place');
    const sum = document.querySelector('dl')!;
    expect(sum).toHaveTextContent('plays7');
    expect(sum).toHaveTextContent('converted43%');
    expect(sum).toHaveTextContent('places away from home2');
    expect(screen.getAllByText('Playing now')).toHaveLength(7);
    expect(screen.getAllByRole('heading', { level: 3 })[0]).toHaveTextContent('7 plays · 3m');
  });

  it('a filter reaches the query, and the search waits for typing to stop', async () => {
    const queries = serve();
    render(<History onNavigate={() => {}} />);
    await screen.findByText('Everything In Its Right Place');
    expect(queries[0].get('days')).toBe('7');
    expect(queries[0].get('page')).toBe('0');
    expect(queries[0].get('tz')).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: 'Movies' }));
    await waitFor(() => expect(queries.at(-1)!.get('kind')).toBe('movies'));
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search history' }), {
      target: { value: 'dune' },
    });
    await waitFor(() => expect(queries.at(-1)!.get('q')).toBe('dune'), { timeout: 2000 });
  });

  it('the person menu lists who played, and picks one', async () => {
    const queries = serve();
    render(<History onNavigate={() => {}} />);
    await screen.findByText('Everything In Its Right Place');
    fireEvent.click(screen.getByRole('button', { name: 'Person: Everyone' }));
    const list = screen.getByRole('listbox', { name: 'Person' });
    expect(
      within(list)
        .getAllByRole('option')
        // the avatar's initials are drawn, not read
        .map((o) => o.textContent!.replace(/^[A-Z]{2}/, '')),
    ).toEqual(['Everyone', 'maya3 plays', 'dan1 play', 'jo1 play', 'ruth1 play', 'sam1 play']);
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    fireEvent.keyDown(list, { key: 'Enter' });
    await waitFor(() => expect(queries.at(-1)!.get('user')).toBe('maya'));
    expect(screen.getByRole('button', { name: 'Person: maya' })).toBeInTheDocument();
  });

  it('opens a row to its timeline and facts', async () => {
    serve();
    render(<History onNavigate={() => {}} />);
    await screen.findByText('Everything In Its Right Place');
    fireEvent.click(row('Everything In Its Right Place'));
    expect(await screen.findByText('Playback timeline')).toBeInTheDocument();
    expect(screen.getByText('FLAC Stereo')).toBeInTheDocument();
    expect(screen.getByText('Direct Play the whole time')).toBeInTheDocument();
    expect(screen.getByText(/SoulSync missed 1 stretch/)).toBeInTheDocument();
    expect(screen.getByText(/Sent 18 MB · peak 2.9 Mbps/)).toBeInTheDocument();
  });

  it('a row that left the log says so and can try again', async () => {
    serve();
    render(<History onNavigate={() => {}} />);
    await screen.findByText('The Wild Robot');
    fireEvent.click(row('The Wild Robot'));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This play is no longer in the log.',
    );
  });

  it('nothing yet, and nothing that matches, read differently', async () => {
    const empty = {
      ...DATA,
      groups: [],
      days: [],
      summary: { ...DATA.summary, plays: 0 },
    } as HistoryPayload;
    serve(empty);
    render(<History onNavigate={() => {}} />);
    expect(await screen.findByText('No plays in this range')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'TV' }));
    expect(await screen.findByText('Nothing matches')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(await screen.findByText('No plays in this range')).toBeInTheDocument();
  });

  it('a failed load can be tried again', async () => {
    let fail = true;
    server.use(
      http.get('*/api/server-activity/history', () =>
        fail
          ? HttpResponse.json({ ok: false, error: 'db locked' }, { status: 500 })
          : HttpResponse.json(DATA),
      ),
    );
    render(<History onNavigate={() => {}} />);
    expect(await screen.findByText('db locked')).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Everything In Its Right Place')).toBeInTheDocument();
  });

  it('plex history rows say where they came from', async () => {
    const data = structuredClone(DATA);
    Object.assign(data.groups[0], {
      source: 'plex_history',
      live: false,
      method: null,
      percent: null,
    });
    serve(data);
    render(<History onNavigate={() => {}} />);
    expect(await screen.findByText('from Plex history')).toBeInTheDocument();
    expect(screen.getByText('Not known')).toBeInTheDocument();
  });

  it('loads the next page when asked', async () => {
    const pages: string[] = [];
    const second = structuredClone(DATA);
    second.groups = [
      { ...structuredClone(DATA.groups[1]), group_key: 'next', title: 'Page two film' },
    ];
    second.has_more = false;
    server.use(
      http.get('*/api/server-activity/history', ({ request }) => {
        const page = new URL(request.url).searchParams.get('page')!;
        pages.push(page);
        return HttpResponse.json(page === '0' ? { ...DATA, has_more: true } : second);
      }),
    );
    render(<History onNavigate={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Show more' }));
    expect(await screen.findByText('Page two film')).toBeInTheDocument();
    expect(pages).toEqual(['0', '1']);
  });
});

describe('the pieces', () => {
  it('groups rows under the day they started on', () => {
    const g = (key: string, t: number) => ({ group_key: key, started_at: t }) as HistoryGroup;
    const days = byDay([g('a', 1794898345), g('b', 1794890000), g('c', 1794800000)], 'UTC');
    expect(days.map((d) => [d.date, d.groups.length])).toEqual([
      ['2026-11-17', 2],
      ['2026-11-16', 1],
    ]);
  });

  it('turns filters into the contract query', () => {
    expect(toQuery({ ...NO_FILTERS, kind: 'tv', q: '  andor ' }, 2, 'Europe/London')).toMatchObject(
      {
        kind: 'tv',
        q: 'andor',
        days: 7,
        page: 2,
        per_page: 50,
        tz: 'Europe/London',
      },
    );
    expect(toQuery({ ...NO_FILTERS, days: '' }, 0).days).toBeNull();
  });

  it('lays the timeline bands out along the run', () => {
    const { bands } = timelineBands([
      { state: 'playing', start: 0, end: 50 },
      { state: 'paused', start: 50, end: 60 },
      { state: 'playing', start: 60, end: 100 },
    ]);
    expect(bands.map((b) => [b.left, b.width])).toEqual([
      [0, 50],
      [50, 10],
      [60, 40],
    ]);
  });

  it('sizes what was sent', () => {
    expect(formatKb(5400000)).toBe('5.4 GB');
    expect(formatKb(18125)).toBe('18 MB');
  });
});

vi.setConfig({ testTimeout: 20000 });
