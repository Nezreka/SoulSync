/**
 * The drawer's plain words, checked against the contract's example payloads
 * (scrap/CONTRACT.md, copied to __fixtures__).
 */

import { describe, expect, it } from 'vitest';

import type { ActivityPayload, Session, StatsPayload } from '@/shell/server-activity-types';

import activity from './__fixtures__/activity.json';
import dune from './__fixtures__/session_dune.json';
import stats from './__fixtures__/stats.json';
import {
  ago,
  awayPhrase,
  clock,
  dayHeading,
  deviceLabel,
  duration,
  heatmapNote,
  heroSentence,
  initials,
  mbps,
  pausedPhrase,
  qualityBadges,
  splitReason,
  transcoderEngine,
  transcoderLine,
  whereLabel,
  whoLine,
} from './format';

const sessions = (activity as unknown as ActivityPayload).sessions;
const byTitle = (t: string) => sessions.find((s) => s.title === t)!;
const text = (segs: { text: string }[]) => segs.map((s) => s.text).join('');

describe('numbers', () => {
  it('reads like a player and a person', () => {
    expect(clock(4092000)).toBe('1:08:12');
    expect(clock(95000)).toBe('1:35');
    expect(duration(9060000)).toBe('2h 31m');
    expect(duration(10800000)).toBe('3h 00m');
    expect(duration(2820000)).toBe('47m');
    expect(duration(39000)).toBe('39s');
    expect(mbps(11800)).toBe('11.8 Mbps');
    expect(mbps(40000)).toBe('40 Mbps');
  });

  it('says how often and how long it paused', () => {
    expect(pausedPhrase(0, 0)).toBeNull();
    expect(pausedPhrase(1, 20000)).toBe('Paused once, under a minute');
    expect(pausedPhrase(2, 360000)).toBe('Paused twice, 6 min');
    expect(pausedPhrase(3, 4500000)).toBe('Paused 3 times, 1h 15m');
  });

  it('names days the way people do', () => {
    const now = new Date(2026, 10, 17, 12);
    expect(dayHeading('2026-11-17', now)).toBe('Today');
    expect(dayHeading('2026-11-16', now)).toBe('Yesterday');
    expect(dayHeading('2026-11-14', now)).toBe('Saturday');
    expect(dayHeading('2026-11-02', now)).toBe('Mon 2 Nov');
    expect(ago(1000, 1000 * 1000 + 30000)).toBe('just now');
  });

  it('initials and the away line', () => {
    expect(initials('maya rhodes')).toBe('MR');
    expect(initials('guest-tv')).toBe('GT');
    expect(awayPhrase(0)).toBe('All at home');
    expect(awayPhrase(80)).toBe('Away from home 80%');
    expect(awayPhrase(null)).toBeNull();
  });
});

describe('a stream card', () => {
  it('names the device like a person would', () => {
    expect(deviceLabel(byTitle('Dune: Part Two').player)).toBe('Apple TV');
    expect(deviceLabel(byTitle('Everything In Its Right Place').player)).toBe('Plexamp on iPhone');
    expect(deviceLabel(byTitle('Project Hail Mary').player)).toBe('Plex on Pixel 8');
    expect(deviceLabel(byTitle('Cold Harbor').player)).toBe('Shield TV');
    // a browser is not a device name
    expect(deviceLabel(byTitle('The Selfish Gene').player)).toBe('Jellyfin Web');
  });

  it('says Home, Remote or the city', () => {
    expect(whereLabel(false, null)).toBe('Home');
    expect(whereLabel(true, null)).toBe('Remote');
    expect(whereLabel(true, 'Leeds, UK')).toBe('Leeds, UK');
    const s = dune as unknown as Session;
    expect(whoLine(s)).toEqual(['Apple TV', 'Remote']);
    expect(whoLine(s, true)).toEqual(['Apple TV', 'Remote', 'Plex']);
  });

  it('shows the mockup badges for a remote 4K transcode', () => {
    const badges = qualityBadges(dune as unknown as Session).map((b) => b.text);
    expect(badges).toEqual(['4K → 1080p', 'HDR10 → SDR', 'Hardware', '11.8 Mbps']);
  });

  it('says Remote as a badge only when the who line names a city', () => {
    const s = structuredClone(dune) as unknown as Session;
    s.connection.location = {
      city: 'Chicago',
      region: 'Illinois',
      country: 'US',
      code: 'US',
      timezone: null,
      lat: null,
      lon: null,
      label: 'Chicago, Illinois, US',
      short: 'Chicago, IL',
    };
    expect(qualityBadges(s).map((b) => b.text)).toContain('Remote');
    expect(whoLine(s)).toEqual(['Apple TV', 'Chicago, IL']);
  });

  it('tints hi-res audio, and only that', () => {
    const music = qualityBadges(byTitle('Everything In Its Right Place'));
    expect(music[0]).toMatchObject({ text: 'FLAC 24-bit · 96 kHz', hires: true });
    expect(qualityBadges(byTitle('Cold Harbor')).some((b) => b.hires)).toBe(false);
    expect(qualityBadges(byTitle('Cold Harbor')).map((b) => b.text)).toEqual([
      '1080p HEVC',
      'EAC3 5.1',
      '9.6 Mbps',
    ]);
  });

  it('a subtitle burn keeps the quality next to it', () => {
    const shogun = byTitle('Crimson Sky');
    expect(
      qualityBadges(shogun)
        .map((b) => b.text)
        .slice(0, 2),
    ).toEqual(['1080p H.264', 'Subtitles burned in']);
  });

  it('the transcoder in words', () => {
    const t = (dune as unknown as Session).transcoder!;
    expect(transcoderEngine(t)).toBe('Intel Quick Sync, decode + encode');
    expect(transcoderLine(t)).toBe('Running at 2.4× · 3m 40s buffered · resting');
    expect(transcoderLine({ ...t, speed: 0, buffered_s: null, throttled: null })).toBe('Hardware');
  });

  it('the reason headline is its first sentence', () => {
    const [head, rest] = splitReason((dune as unknown as Session).reason!.text);
    expect(head).toMatch(/^Converting because .*\.$/);
    expect(rest).toBe('The source runs at 62.4 Mbps and 11.8 Mbps is sent.');
  });
});

describe('the stats sentence', () => {
  it('reads the example headline', () => {
    const s = stats as unknown as StatsPayload;
    expect(text(heroSentence(s))).toBe(
      'In the last 7 days 5 people played 6 minutes across both servers. Most of it was films, mornings are busiest, and 1 in 2 plays had to be converted, mostly for maya away from home.',
    );
  });

  it('says the busiest hours as a span', () => {
    const s = structuredClone(stats) as unknown as StatsPayload;
    s.heatmap.busiest = { weekday: 5, hour_start: 21, hour_end: 23 };
    s.heatmap.quietest = { weekday: 0, hour: 4 };
    expect(heatmapNote(s)).toBe('Busiest: Saturday, 9 to 11 pm · quietest: Monday, 4 am');
    s.heatmap.busiest = { weekday: 6, hour_start: 11, hour_end: 13 };
    expect(heatmapNote(s)).toMatch(/^Busiest: Sunday, 11 am to 1 pm/);
  });

  it('one server, no conversions', () => {
    const s = structuredClone(stats) as unknown as StatsPayload;
    s.headline = { ...s.headline, servers: ['plex'], converted_pct: 0, converted_one_in: null };
    expect(text(heroSentence(s))).toContain('on Plex.');
    expect(text(heroSentence(s))).toContain('every play went out as is');
  });
});
