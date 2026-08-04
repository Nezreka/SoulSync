/** P6 pure core — literal assertions against api-monitor.js. */

import { describe, expect, it } from 'vitest';

import { activityTimeAgo, activityToastType } from './-dash.activity';

const NOW = 1_700_000_000_000; // ms
const secondsAgo = (s: number) => (NOW - s * 1000) / 1000;

describe('activityTimeAgo', () => {
  it('falls back to the human label when there is no numeric timestamp', () => {
    expect(activityTimeAgo({ time: 'Now' }, NOW)).toBe('Now');
    expect(activityTimeAgo({ timestamp: NaN, time: 'Now' }, NOW)).toBe('Now');
    expect(activityTimeAgo({}, NOW)).toBe('');
    expect(activityTimeAgo(null, NOW)).toBe('');
  });

  it('buckets the epoch-SECONDS timestamp exactly like the vanilla', () => {
    expect(activityTimeAgo({ timestamp: secondsAgo(30) }, NOW)).toBe('Just now');
    expect(activityTimeAgo({ timestamp: secondsAgo(90) }, NOW)).toBe('1m ago');
    expect(activityTimeAgo({ timestamp: secondsAgo(59 * 60) }, NOW)).toBe('59m ago');
    expect(activityTimeAgo({ timestamp: secondsAgo(2 * 3600) }, NOW)).toBe('2h ago');
    expect(activityTimeAgo({ timestamp: secondsAgo(3 * 86400) }, NOW)).toBe('3d ago');
    expect(activityTimeAgo({ timestamp: secondsAgo(45 * 86400) }, NOW)).toBe('1mo ago');
  });
});

describe('activityToastType', () => {
  it('icon outranks title, chain order verbatim', () => {
    expect(activityToastType({ icon: '✅', title: 'x' })).toBe('success');
    expect(activityToastType({ icon: '❌', title: 'x' })).toBe('error');
    expect(activityToastType({ icon: '🚫', title: 'x' })).toBe('warning');
    expect(activityToastType({ title: 'Download Complete' })).toBe('success');
    expect(activityToastType({ title: 'Sync Failed' })).toBe('error');
    expect(activityToastType({ title: 'Error while matching' })).toBe('error');
    expect(activityToastType({ title: 'Job Cancelled' })).toBe('warning');
    expect(activityToastType({ title: 'Something happened' })).toBe('info');
    // Complete beats Failed — the success arm is checked first.
    expect(activityToastType({ title: 'Complete but Failed' })).toBe('success');
  });
});
