/**
 * Stats against the contract's example payload: the sentence, the tiles, the
 * hand-drawn charts, people and shelves, the range reaching the query, and
 * the empty state.
 */

import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import type { StatsPayload } from '@/shell/server-activity-types';

import { HttpResponse, http, server } from '@/test/msw';

import stats from './__fixtures__/stats.json';
import { clearCache } from './cache';
import { Stats, chartDays, niceMax } from './stats';

// every test starts with nothing read ahead or remembered
beforeEach(() => clearCache());

const DATA = stats as unknown as StatsPayload;

function serve(payload: StatsPayload = DATA) {
  const asked: string[] = [];
  server.use(
    http.get('*/api/server-activity/stats', ({ request }) => {
      asked.push(new URL(request.url).searchParams.get('days')!);
      return HttpResponse.json(payload);
    }),
  );
  return asked;
}

describe('stats', () => {
  it('reads the example: sentence, tiles, charts, people, shelves', async () => {
    serve({ ...DATA, days: 7 });
    render(<Stats days={7} onNavigate={() => {}} />);
    expect((await screen.findByText(/In the last 7 days/)).closest('p')).toHaveTextContent(
      'In the last 7 days 5 people played 6 minutes across both servers.',
    );
    const plays = screen.getByText('Plays').parentElement!;
    expect(plays).toHaveTextContent('7');
    expect(plays).toHaveTextContent('nothing the 7 days before');
    expect(screen.getByText('Played as is').parentElement).toHaveTextContent('57%');
    expect(screen.getByText('Sent remotely').parentElement).toHaveTextContent('90 MB');
    const how = screen.getByRole('region', { name: 'How it played' });
    expect(how).toHaveTextContent('Direct Play 43%');
    expect(how).toHaveTextContent("Audio format the player can't play3");
    expect(how).toHaveTextContent('Converted in hardware2 of 3');
    expect(screen.getByRole('region', { name: 'Where from' })).toHaveTextContent(
      'Away, place unknown',
    );
    const people = screen.getByRole('region', { name: 'People' });
    expect(within(people).getByText('maya')).toBeInTheDocument();
    expect(people).toHaveTextContent('Always away');
    expect(people).toHaveTextContent('100% lossless');
    const books = screen.getByRole('region', { name: 'Books & podcasts' });
    expect(books).toHaveTextContent('ruth · 42% through');
    // a film with a link opens it
    expect(
      within(screen.getByRole('region', { name: 'Films' })).getAllByRole('button'),
    ).toHaveLength(2);
  });

  it('reads each day of the chart with the arrow keys', async () => {
    serve();
    render(<Stats days={7} onNavigate={() => {}} />);
    const chart = await screen.findByRole('group', { name: /Hours played per day/ });
    fireEvent.keyDown(chart, { key: 'End' });
    expect(within(chart).getByRole('status')).toHaveTextContent('6m · 7 plays');
    fireEvent.keyDown(chart, { key: 'ArrowLeft' });
    expect(within(chart).getByRole('status')).toHaveTextContent('0h · 0 plays');
  });

  it('asks for the range it is given', async () => {
    const asked = serve();
    const { rerender } = render(<Stats days={30} onNavigate={() => {}} />);
    await screen.findByText(/In the last/);
    rerender(<Stats days={90} onNavigate={() => {}} />);
    await screen.findByText(/In the last/);
    expect(asked).toEqual(['30', '90']);
  });

  it('nothing played says so', async () => {
    const empty = structuredClone(DATA);
    empty.tiles.plays = { value: 0, prev: 0, change_pct: null };
    empty.days = 365;
    serve(empty);
    render(<Stats days={365} onNavigate={() => {}} />);
    expect(await screen.findByText('Nothing played in the last year')).toBeInTheDocument();
  });
});

describe('the pieces', () => {
  it('rounds the axis', () => {
    expect(niceMax(17)).toBe(20);
    expect(niceMax(0.1)).toBe(0.1);
    expect(niceMax(3.2)).toBe(5);
    expect(niceMax(0)).toBe(1);
  });

  it('a year is weeks, not slivers', () => {
    const year = structuredClone(DATA);
    year.per_day = Array.from({ length: 365 }, (_, i) => ({
      date: `2026-01-01`,
      weekday: i % 7,
      weekend: i % 7 >= 5,
      hours: 1,
      plays: 2,
    }));
    const { unit, bars } = chartDays(year);
    expect(unit).toBe('week');
    expect(bars).toHaveLength(53);
    expect(bars[0]).toMatchObject({ hours: 7, plays: 14 });
    expect(chartDays(DATA).unit).toBe('day');
  });
});
