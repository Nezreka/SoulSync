/**
 * The sync page's right-hand sidebar — index.html 3301-3315.
 *
 * Two sections. Sync Actions carries the selection line and the Start Sync
 * button; Sync Progress carries the bar, the status line and the readonly log
 * textarea. Markup is 1:1 with the vanilla, including the ids, which are not
 * decorative: helper.js keys its tour copy off `.sync-sidebar`,
 * `#start-sync-btn` and `#sync-log-area` (798-811), and lists them among the
 * page's tour anchors (3449). Renaming them would silently empty the help
 * bubbles.
 *
 * The sidebar is hidden at rest and shown while a sequential sync runs — see
 * syncSidebarVisible in -sync.sidebar.ts for what drives that and which half
 * of the vanilla's rule is left to CSS.
 *
 * Start Sync is a TOGGLE, matching the vanilla handler: while a sync runs the
 * same button reads 'Cancel Sequential Sync' and cancels. One callback, and
 * the caller decides from `running` — the port does not re-derive it, because
 * the vanilla's own reliance on a synchronously-set `isRunning` is what made
 * the accumulated-listener bug bite.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import type { SyncActionsState } from '../-sync.sidebar';

import { fetchSyncLogs } from '../-sync.api';
import { useSyncLogsEvent } from '../-sync.events';
import {
  SYNC_LOG_PLACEHOLDER,
  syncLogShouldScrollTop,
  syncLogText,
  syncProgressLabel,
  syncProgressPercent,
  syncSelectionLabel,
  syncStartDisabled,
  syncStartLabel,
} from '../-sync.sidebar';

/**
 * The log area's data. `ss:sync-logs` (the socket push, re-broadcast from
 * api-monitor.js's updateLogsFromData) plus the poller twin — a 3s /api/logs
 * fetch gated on window._socketConnected, which is `loadLogs`'s own cadence
 * and its own gate. One ungated hydrate on mount so the textarea fills
 * immediately rather than after the first tick.
 */
export function useSyncLog(): string {
  const [text, setText] = useState(SYNC_LOG_PLACEHOLDER);
  /**
   * Bumped on every socket push. A fetch started before a push must not apply
   * its older answer afterwards — the request is in flight for as long as the
   * server takes, and the push that lands meanwhile is strictly newer. The
   * vanilla has the same race and loses it; there is no reason to inherit
   * that when the fix is a counter.
   */
  const pushes = useRef(0);

  const apply = useCallback((frame: unknown) => {
    const next = syncLogText(frame);
    // null = no usable `logs` array. The vanilla returns without touching the
    // textarea, so a malformed frame must not blank what is already shown.
    if (next !== null) setText(next);
  }, []);

  const onPush = useCallback(
    (frame: unknown) => {
      pushes.current += 1;
      apply(frame);
    },
    [apply],
  );

  useSyncLogsEvent(onPush);

  useEffect(() => {
    let cancelled = false;
    const hydrate = async () => {
      const seen = pushes.current;
      const data = await fetchSyncLogs();
      // `data === null` is BELT-AND-BRACES, not load-bearing: `apply` already
      // drops a nullish frame, because syncLogText finds no logs array in it.
      // Kept because null is fetchSyncLogs's documented "the request failed"
      // answer and reads better than relying on the downstream guard —
      // a mutation removing it survives, and equivalently so.
      if (cancelled || data === null) return;
      if (pushes.current !== seen) return; // a push overtook this fetch
      apply(data);
    };
    const tick = () => {
      // The socket push owns live updates when it is up — same gate as
      // loadLogs (api-monitor.js 1115).
      if (window._socketConnected) return;
      void hydrate();
    };
    void hydrate();
    const timer = setInterval(tick, 3000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [apply]);

  return text;
}

export interface SyncSidebarProps {
  state: SyncActionsState;
  /** Start when idle, cancel when running — the vanilla's one toggle. */
  onStartSync: () => void;
  /**
   * syncSidebarVisible's result. The element stays MOUNTED either way and only
   * its display flips, because the vanilla's log poller runs for the whole
   * time the page is open regardless of whether the sidebar is on screen —
   * unmounting would stop the feed whenever the panel is hidden.
   */
  visible: boolean;
  /** Injected so tests can drive the textarea without a live poller. */
  logText?: string;
}

export function SyncSidebar({ state, onStartSync, visible, logText }: SyncSidebarProps) {
  const polled = useSyncLog();
  const text = logText ?? polled;
  const logRef = useRef<HTMLTextAreaElement>(null);

  /**
   * updateLogsFromData's scroll rule (1137-1147).
   *
   * The vanilla measures the scroll position BEFORE assigning the new value.
   * An effect cannot: it runs after React has already committed the text, and
   * writing a textarea's value can clamp scrollTop, so measuring there would
   * read post-update numbers the vanilla never sees. So the metrics are
   * captured on scroll instead — that is the position the reader actually left
   * the box in, which is exactly what the rule is asking about. They start at
   * zero, which reads as "at the top" and matches a freshly-rendered box.
   */
  const metrics = useRef({ scrollTop: 0, scrollHeight: 0, clientHeight: 0 });
  const onScroll = () => {
    const el = logRef.current;
    if (el) {
      metrics.current = {
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
      };
    }
  };

  // Layout effect, not a passive one: the reset must land before paint, or a
  // new frame flashes at the old offset first. `[text]` is the whole guard —
  // a re-render that does not change the log (a selection change, say) must
  // leave the reader's position alone, and the dependency already ensures it.
  useLayoutEffect(() => {
    const el = logRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = metrics.current;
    if (syncLogShouldScrollTop(scrollTop, scrollHeight, clientHeight)) {
      el.scrollTop = 0;
      metrics.current = { ...metrics.current, scrollTop: 0 };
    }
  }, [text]);

  return (
    <div className={`sync-sidebar${visible ? ' sync-sidebar--visible' : ''}`}>
      <div className="sidebar-section">
        <h4>Sync Actions</h4>
        <div id="selection-info">{syncSelectionLabel(state)}</div>
        <button
          type="button"
          id="start-sync-btn"
          className="neo-button"
          disabled={syncStartDisabled(state.running, state.selectedCount)}
          onClick={onStartSync}
        >
          {syncStartLabel(state.running)}
        </button>
      </div>
      <div className="sidebar-section progress-section">
        <h4>Sync Progress</h4>
        <div className="progress-bar-container">
          <div
            className="progress-bar-fill"
            id="sync-progress-bar"
            style={{ width: `${syncProgressPercent(state)}%` }}
          />
        </div>
        <div id="sync-progress-text">{syncProgressLabel(state)}</div>
        <textarea id="sync-log-area" readOnly ref={logRef} value={text} onScroll={onScroll} />
      </div>
    </div>
  );
}
