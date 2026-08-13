/**
 * P6 pure core — the activity feed's relative-time formatter and the toast
 * type chain, transcribed 1:1 from api-monitor.js (_activityTimeAgo,
 * checkForActivityToasts' icon/title mapping — the same chain core.js's
 * handleDashboardToast repeats).
 */

import type { ActivityItem } from './-dash.api';

/**
 * _activityTimeAgo (api-monitor.js:989). Activity items carry `timestamp`
 * (Unix epoch SECONDS) — `time` is a human label like "Now" that doesn't
 * parse as a date. `nowMs` is injected for testability; callers pass
 * Date.now().
 */
export function activityTimeAgo(activity: ActivityItem | null | undefined, nowMs: number): string {
  const ts = activity && activity.timestamp;
  if (typeof ts !== 'number' || !isFinite(ts)) {
    return (activity && activity.time) || '';
  }
  const diffMs = nowMs - ts * 1000;
  if (diffMs < 60000) return 'Just now';
  const mins = Math.floor(diffMs / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

/** The toast type chain — icon first, then title keywords. */
export function activityToastType(activity: {
  icon?: string;
  title?: string;
}): 'success' | 'error' | 'warning' | 'info' {
  const title = activity.title || '';
  if (activity.icon === '✅' || title.includes('Complete')) return 'success';
  if (activity.icon === '❌' || title.includes('Failed') || title.includes('Error')) return 'error';
  if (activity.icon === '🚫' || title.includes('Cancelled')) return 'warning';
  return 'info';
}
