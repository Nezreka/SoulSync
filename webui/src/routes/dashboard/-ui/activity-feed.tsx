/**
 * The Recent Activity card (dash-card data-card="activity") plus the activity
 * toast poller. Markup 1:1 from index.html (artefact differential pins it).
 *
 * Feed data: `ss:dashboard-activity` (socket) + the poller twin's 2s fallback
 * (fetchAndUpdateActivityFeed's cadence, gated on window._socketConnected +
 * document.hidden) + one ungated mount hydrate. The vanilla's sig-based
 * rebuild-avoidance is React's vdom diff; its timestamp-only refresh on
 * unchanged feeds falls out of re-rendering with a fresh Date.now() each
 * poll/push. Top FIVE items, separators between them, empty feeds render the
 * "System Started / Just now" placeholder (the MARKUP's initial says "Now" —
 * both vanilla texts, both preserved).
 *
 * Toasts: checkForActivityToasts' 3s fallback, same gates. The socket path
 * already toasts from core.js handleDashboardToast — React must NOT toast on
 * the ss:dashboard-toast event too, or every push would toast twice. The
 * poller is the only React half.
 */

import { Fragment, useCallback, useEffect, useState } from 'react';

import type { ActivityItem } from '../-dash.api';

import { activityTimeAgo, activityToastType } from '../-dash.activity';
import { fetchActivityFeed, fetchActivityToasts } from '../-dash.api';
import { useDashboardActivityEvent } from '../-dash.events';

interface FeedState {
  /** null before any data — render the markup's initial "Now" placeholder. */
  items: ActivityItem[] | null;
  /** Bumped on every apply so times re-render with a fresh clock. */
  clock: number;
}

export function useActivityFeed(): FeedState {
  const [state, setState] = useState<FeedState>({ items: null, clock: Date.now() });

  const apply = useCallback((activities: ActivityItem[]) => {
    setState({ items: activities.slice(0, 5), clock: Date.now() });
  }, []);

  useDashboardActivityEvent(useCallback((frame) => apply(frame.activities || []), [apply]));

  useEffect(() => {
    let cancelled = false;
    const hydrate = async () => {
      const items = await fetchActivityFeed();
      // null = the fetch failed — keep whatever is shown, like the vanilla's
      // early returns. Only a REAL empty array renders the placeholder.
      if (!cancelled && items !== null) apply(items);
    };
    const pollTick = async () => {
      if (window._socketConnected) return; // the socket push owns live updates
      if (document.hidden) return;
      await hydrate();
    };
    void hydrate();
    // fetchAndUpdateActivityFeed's "responsive activity feed" cadence.
    const timer = setInterval(() => void pollTick(), 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [apply]);

  return state;
}

/** checkForActivityToasts — the socket-down toast fallback, 3s cadence. */
export function useActivityToasts(): void {
  useEffect(() => {
    const tick = async () => {
      if (window._socketConnected) return; // WebSocket handles this (instant push)
      if (document.hidden) return;
      const toasts = await fetchActivityToasts();
      toasts.forEach((activity) => {
        window.showToast?.(`${activity.title}: ${activity.subtitle}`, activityToastType(activity));
      });
    };
    const timer = setInterval(() => void tick(), 3000);
    return () => clearInterval(timer);
  }, []);
}

function ActivityRow({ activity, nowMs }: { activity: ActivityItem; nowMs: number }) {
  return (
    <div className="activity-item">
      <span className="activity-icon">{activity.icon}</span>
      <div className="activity-text-content">
        <p className="activity-title">{activity.title}</p>
        <p className="activity-subtitle">{activity.subtitle}</p>
      </div>
      <p className="activity-time">{activityTimeAgo(activity, nowMs)}</p>
    </div>
  );
}

export function ActivityFeedCard() {
  const { items, clock } = useActivityFeed();
  useActivityToasts();

  return (
    <article className="dash-card" data-card="activity">
      <header className="dash-card__head dash-card__head--withaction">
        <div>
          <h3 className="dash-card__title">Recent Activity</h3>
          <p className="dash-card__sub">What just happened.</p>
        </div>
        <button
          className="dash-card__head-btn"
          title="View full library history"
          onClick={() => window.openLibraryHistoryModal?.()}
        >
          Download History
        </button>
      </header>
      <div className="dash-card__body">
        <div className="activity-feed-container" id="dashboard-activity-feed">
          {items === null || items.length === 0 ? (
            <div className="activity-item">
              <span className="activity-icon">📊</span>
              <div className="activity-text-content">
                <p className="activity-title">System Started</p>
                <p className="activity-subtitle">Dashboard initialized successfully</p>
              </div>
              {/* The markup's initial label is "Now"; an EMPTY FEED from the
                  backend renders "Just now" — both vanilla texts. */}
              <p className="activity-time">{items === null ? 'Now' : 'Just now'}</p>
            </div>
          ) : (
            items.map((activity, index) => (
              // Rows and separators are SIBLINGS in the container, exactly as
              // the vanilla appends them; positional keys mirror its
              // rebuild-in-order.
              // eslint-disable-next-line react/no-array-index-key
              <Fragment key={index}>
                <ActivityRow activity={activity} nowMs={clock} />
                {index < items.length - 1 ? <div className="activity-separator"></div> : null}
              </Fragment>
            ))
          )}
        </div>
      </div>
    </article>
  );
}
