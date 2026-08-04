/**
 * The dashboard socket seam — event names must match what the vanilla handlers
 * dispatch (pinned on the Python side by tests/test_dashboard_seam.py), and
 * every hook must deliver while mounted and detach on unmount.
 */

import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  DASHBOARD_ACTIVITY_EVENT,
  DASHBOARD_DB_STATS_EVENT,
  DASHBOARD_STATS_EVENT,
  DASHBOARD_TOAST_EVENT,
  DASHBOARD_WISHLIST_COUNT_EVENT,
  DEV_MODE_EVENT,
  ENRICH_STATUS_EVENT,
  JIOSAAVN_EXPERIMENTAL_EVENT,
  RATE_MONITOR_EVENT,
  SERVICE_STATUS_EVENT,
  WATCHLIST_COUNT_EVENT,
  useDashboardActivityEvent,
  useDashboardDbStatsEvent,
  useDashboardStatsEvent,
  useDashboardToastEvent,
  useDashboardWishlistCountEvent,
  useDevModeEvent,
  useEnrichStatusEvent,
  useJiosaavnExperimentalEvent,
  useRateMonitorEvent,
  useServiceStatusEvent,
  useWatchlistCountEvent,
} from './-dash.events';

afterEach(cleanup);

describe('event names', () => {
  it('match what the vanilla handlers dispatch', () => {
    expect(ENRICH_STATUS_EVENT).toBe('ss:enrich-status');
    expect(DASHBOARD_STATS_EVENT).toBe('ss:dashboard-stats');
    expect(DASHBOARD_ACTIVITY_EVENT).toBe('ss:dashboard-activity');
    expect(DASHBOARD_TOAST_EVENT).toBe('ss:dashboard-toast');
    expect(DASHBOARD_DB_STATS_EVENT).toBe('ss:dashboard-db-stats');
    expect(DASHBOARD_WISHLIST_COUNT_EVENT).toBe('ss:dashboard-wishlist-count');
    expect(WATCHLIST_COUNT_EVENT).toBe('ss:watchlist-count');
    expect(SERVICE_STATUS_EVENT).toBe('ss:service-status');
    expect(RATE_MONITOR_EVENT).toBe('ss:rate-monitor');
    expect(JIOSAAVN_EXPERIMENTAL_EVENT).toBe('ss:jiosaavn-experimental');
    expect(DEV_MODE_EVENT).toBe('ss:dev-mode');
  });
});

/** Mount a hook, fire its event, assert delivery, guard, and teardown. */
function harness(use: (onFrame: (frame: never) => void) => void, name: string) {
  const seen = vi.fn();
  function Probe() {
    use(seen as (frame: never) => void);
    return null;
  }
  const view = render(<Probe />);
  window.dispatchEvent(new CustomEvent(name, { detail: { probe: 1 } }));
  expect(seen).toHaveBeenCalledWith({ probe: 1 });
  window.dispatchEvent(new CustomEvent(name)); // no detail → guarded, not called
  expect(seen).toHaveBeenCalledTimes(1);
  view.unmount();
  window.dispatchEvent(new CustomEvent(name, { detail: { probe: 2 } }));
  expect(seen).toHaveBeenCalledTimes(1);
}

describe('the subscription hooks', () => {
  it.each([
    ['useEnrichStatusEvent', useEnrichStatusEvent, ENRICH_STATUS_EVENT],
    ['useDashboardStatsEvent', useDashboardStatsEvent, DASHBOARD_STATS_EVENT],
    ['useDashboardActivityEvent', useDashboardActivityEvent, DASHBOARD_ACTIVITY_EVENT],
    ['useDashboardToastEvent', useDashboardToastEvent, DASHBOARD_TOAST_EVENT],
    ['useDashboardDbStatsEvent', useDashboardDbStatsEvent, DASHBOARD_DB_STATS_EVENT],
    [
      'useDashboardWishlistCountEvent',
      useDashboardWishlistCountEvent,
      DASHBOARD_WISHLIST_COUNT_EVENT,
    ],
    ['useWatchlistCountEvent', useWatchlistCountEvent, WATCHLIST_COUNT_EVENT],
    ['useServiceStatusEvent', useServiceStatusEvent, SERVICE_STATUS_EVENT],
    ['useRateMonitorEvent', useRateMonitorEvent, RATE_MONITOR_EVENT],
    ['useJiosaavnExperimentalEvent', useJiosaavnExperimentalEvent, JIOSAAVN_EXPERIMENTAL_EVENT],
    ['useDevModeEvent', useDevModeEvent, DEV_MODE_EVENT],
  ])('%s delivers while mounted and detaches on unmount', (_name, hook, event) => {
    harness(hook as never, event);
  });

  it('the enrich channel carries { id, data } frames', () => {
    const seen = vi.fn();
    function Probe() {
      useEnrichStatusEvent(seen);
      return null;
    }
    const view = render(<Probe />);
    window.dispatchEvent(
      new CustomEvent(ENRICH_STATUS_EVENT, {
        detail: { id: 'tidal', data: { running: true } },
      }),
    );
    expect(seen).toHaveBeenCalledWith({ id: 'tidal', data: { running: true } });
    view.unmount();
  });
});
