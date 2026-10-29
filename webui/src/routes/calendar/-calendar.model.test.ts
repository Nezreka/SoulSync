import { describe, expect, it } from 'vitest';

import type { CalendarEpisode } from './-calendar.types';

import { pollInterval, IDLE_POLL_MS, LIVE_POLL_MS } from './-calendar.api';
import {
  airMins,
  bandOf,
  byAirTime,
  episodeCode,
  featured,
  fmtMins,
  groupDrops,
  orderedCounts,
  parseISO,
  stateShort,
  stepAnchor,
  timeAgo,
  whenLabel,
  whyLine,
  windowFor,
  windowRange,
  windowTitle,
} from './-calendar.model';

export function ep(over: Partial<CalendarEpisode> = {}): CalendarEpisode {
  return {
    id: 1,
    show_id: 10,
    show_tmdb_id: 100,
    show_title: 'Show',
    season_number: 1,
    episode_number: 1,
    title: 'Pilot',
    overview: null,
    air_date: '2026-10-27',
    has_file: 0,
    monitored: 1,
    airs_time: '21:00',
    acq: 'unaired',
    needs_action: false,
    ...over,
  };
}

describe('windows', () => {
  it('a week runs Sunday to Saturday around any day in it', () => {
    expect(windowFor('week', '2026-10-27')).toEqual({ start: '2026-10-25', days: 7 });
    expect(windowFor('agenda', '2026-10-31')).toEqual({ start: '2026-10-25', days: 7 });
  });

  it('a month is the six-week grid from the Sunday before the 1st', () => {
    expect(windowFor('month', '2026-10-27')).toEqual({ start: '2026-09-27', days: 42 });
  });

  it('steps by a week, or by a month landing on its 1st', () => {
    expect(stepAnchor('week', '2026-10-27', 1)).toBe('2026-11-01');
    expect(stepAnchor('week', '2026-10-27', -1)).toBe('2026-10-18');
    expect(stepAnchor('month', '2026-10-27', 1)).toBe('2026-11-01');
    expect(stepAnchor('month', '2026-01-15', -1)).toBe('2025-12-01');
  });

  it('titles and ranges read like a calendar', () => {
    expect(windowTitle('week', '2026-10-27', '2026-10-27')).toBe('This Week');
    expect(windowTitle('week', '2026-11-03', '2026-10-27')).toBe('Next Week');
    expect(windowTitle('week', '2026-10-06', '2026-10-27')).toBe('3 weeks ago');
    expect(windowTitle('month', '2026-10-27', '2026-10-27')).toBe('October');
    expect(windowRange('week', '2026-10-27')).toBe('Oct 25 – 31, 2026');
    expect(windowRange('week', '2026-10-01')).toBe('Sep 27 – Oct 3, 2026');
  });
});

describe('air times', () => {
  it('reads TVDB times and drops the 00:00 streaming placeholder', () => {
    expect(airMins('21:00')).toBe(1260);
    expect(airMins('9:30 PM')).toBe(1290);
    expect(airMins('12:00 AM')).toBeNull();
    expect(airMins('00:00')).toBeNull();
    expect(airMins(null)).toBeNull();
    expect(fmtMins(1290)).toBe('9:30 PM');
    expect(fmtMins(1200)).toBe('8 PM');
  });

  it('a 3 AM streaming drop sorts after the late-night shows, not before morning', () => {
    const order = [
      ep({ id: 1, show_title: 'Drop', airs_time: '03:00' }),
      ep({ id: 2, show_title: 'Morning', airs_time: '11:00' }),
      ep({ id: 3, show_title: 'Late', airs_time: '23:30' }),
      ep({ id: 4, show_title: 'Anytime', airs_time: null }),
    ].sort(byAirTime);
    expect(order.map((e) => e.show_title)).toEqual(['Morning', 'Late', 'Drop', 'Anytime']);
    expect(bandOf(airMins('03:00'))).toBe('late');
    expect(bandOf(airMins('19:00'))).toBe('prime');
    expect(bandOf(null)).toBe('anytime');
  });
});

describe('same-day drops', () => {
  it('three or more of one show, season and slot become one card', () => {
    const eps = [1, 2, 3, 4].map((n) =>
      ep({ id: n, episode_number: n, acq: n === 1 ? 'owned' : 'missing' }),
    );
    const groups = groupDrops([...eps, ep({ id: 9, show_tmdb_id: 200, show_title: 'Other' })]);
    expect(groups).toHaveLength(2);
    expect(episodeCode(groups[0])).toBe('S1 · E1–4');
    expect(groups[0].acq).toBe('owned');
    expect(groups[0].mixed).toBe(true);
    expect(groups[0].ownedCount).toBe(1);
  });

  it('two episodes stay two cards', () => {
    const eps = [1, 2].map((n) => ep({ id: n, episode_number: n }));
    expect(groupDrops(eps)).toHaveLength(2);
  });
});

describe('states', () => {
  it('an upcoming episode says Auto only when something will grab it', () => {
    expect(stateShort(ep(), { autoOn: true })).toBe('Auto');
    expect(stateShort(ep(), { autoOn: false })).toBe('Upcoming');
    expect(whyLine(ep(), { autoOn: false })).toMatch(/nothing will grab it/);
    expect(stateShort(ep({ acq: 'downloading', progress: 42 }), { autoOn: true })).toBe('42%');
    expect(stateShort(ep({ acq: 'ignored' }), { autoOn: true })).toBe('Skipped');
  });

  it('counts come back in reading order', () => {
    const c = orderedCounts([
      ep({ acq: 'missing' }),
      ep({ acq: 'owned' }),
      ep({ acq: 'unaired' }),
      ep({ acq: 'missing' }),
    ]);
    expect(c).toEqual([
      ['owned', 1],
      ['missing', 2],
      ['unaired', 1],
    ]);
  });
});

describe('next up', () => {
  const at = (iso: string, hhmm: string) => {
    const d = parseISO(iso);
    const [h, m] = hhmm.split(':').map(Number);
    d.setHours(h, m);
    return d.getTime();
  };

  it('moves through the day with a grace for a show on air, one per show', () => {
    const eps = [
      ep({ id: 1, show_title: 'Early', show_tmdb_id: 1, airs_time: '18:00' }),
      ep({ id: 2, show_title: 'On now', show_tmdb_id: 2, airs_time: '20:30' }),
      ep({ id: 3, show_title: 'Later', show_tmdb_id: 3, airs_time: '22:00' }),
      ep({ id: 4, show_title: 'Later', show_tmdb_id: 3, airs_time: '22:30', episode_number: 2 }),
      ep({ id: 5, show_title: 'Tomorrow', show_tmdb_id: 5, air_date: '2026-10-28' }),
    ];
    const now = at('2026-10-27', '21:15');
    expect(featured(eps, now, true).map((e) => e.id)).toEqual([2, 3, 5]);
    expect(whenLabel(eps[1], '2026-10-27', now)).toBe('On now · 8:30 PM');
    expect(whenLabel(eps[2], '2026-10-27', now)).toBe('Tonight, 10 PM');
    expect(whenLabel(eps[4], '2026-10-27', now)).toBe('Tomorrow, 9 PM');
  });

  it('once the week has aired it shows the latest', () => {
    const eps = [
      ep({ id: 1, show_tmdb_id: 1 }),
      ep({ id: 2, show_tmdb_id: 2, airs_time: '22:00' }),
    ];
    expect(featured(eps, at('2026-10-31', '12:00'), true).map((e) => e.id)).toEqual([2, 1]);
  });
});

describe('freshness', () => {
  it('says how long ago in plain words', () => {
    const now = Date.parse('2026-10-28T05:00:00Z');
    expect(timeAgo('2026-10-28 04:59:30', now)).toBe('just now');
    expect(timeAgo('2026-10-28 04:30:00', now)).toBe('30 min ago');
    expect(timeAgo('2026-10-27 23:00:00', now)).toBe('6 h ago');
    expect(timeAgo('2026-10-24 05:00:00', now)).toBe('4 days ago');
    expect(timeAgo(null, now)).toBeNull();
  });

  it('polls fast only while something is moving', () => {
    const base = { episodes: [ep()], movies: [] } as never;
    expect(pollInterval(base)).toBe(IDLE_POLL_MS);
    expect(pollInterval({ episodes: [ep({ acq: 'downloading' })], movies: [] } as never)).toBe(
      LIVE_POLL_MS,
    );
    expect(pollInterval(undefined)).toBe(IDLE_POLL_MS);
  });
});

describe('unmatched', () => {
  it('reads as its own state and says what fixes it', () => {
    const e = ep({ acq: 'unmatched', show_tmdb_id: null });
    expect(stateShort(e, { autoOn: true })).toBe('Unmatched');
    expect(whyLine(e, { autoOn: true })).toMatch(/Fix its match/);
    // it sorts after everything that can be acted on with a wishlist
    expect(orderedCounts([e, ep({ acq: 'missing' })]).map(([s]) => s)).toEqual([
      'missing',
      'unmatched',
    ]);
  });
});
