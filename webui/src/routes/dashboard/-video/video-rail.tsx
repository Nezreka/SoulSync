/**
 * The video dashboard's right rail: calm and compact, the system beside the
 * content. The week (did what aired land), what the engine runs next, and the
 * count tiles.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { CalendarPayload } from '../../calendar/-calendar.types';

import { CALENDAR_QUERY_KEY, wishlistMissing } from '../../calendar/-calendar.api';
import { timeAgo } from '../../calendar/-calendar.model';
import { StatePill } from '../../calendar/-ui/state-pill';
import { weekLine, weekSummary } from './-vdash.model';
import { goVideo } from './video-bands';
import styles from './video-dashboard.module.css';

export function WeekCard({
  data,
  today,
  start,
  canDownload,
}: {
  data: CalendarPayload | undefined;
  today: string;
  start: string;
  canDownload: boolean;
}) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  const w = weekSummary(data.episodes, today);
  const airing = data.automations?.airing ?? null;
  const ran = timeAgo(airing?.last_run ?? null, Date.now());

  const wishMissing = async () => {
    setBusy(true);
    try {
      const r = await wishlistMissing({ start, end: today, scope: 'watchlist', specials: false });
      if (!r.success) throw new Error(r.error || 'failed');
      window.showToast?.(
        r.added
          ? `Added ${r.added} episode${r.added === 1 ? '' : 's'} to the wishlist`
          : 'Nothing new to add',
        'success',
      );
      document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
      void qc.invalidateQueries({ queryKey: CALENDAR_QUERY_KEY });
    } catch {
      window.showToast?.('Could not add them to the wishlist', 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="dash-card dash-side-card" data-card="week">
      <header className="dash-side-head">
        <h3 className="dash-side-title">This week</h3>
        <button type="button" className="dash-side-link" onClick={() => goVideo('video-calendar')}>
          Calendar
        </button>
      </header>
      <div className={styles.week}>
        <div className={styles.weekFigure}>
          <span className={styles.weekNum}>
            {w.owned}
            <small>/{w.aired}</small>
          </span>
          <span className={styles.weekCaption}>aired episodes in</span>
        </div>
        {w.aired ? (
          <div className={styles.meter} role="img" aria-label={weekLine(w)}>
            {w.segments.map((sg) => (
              <i key={sg.state} data-state={sg.state} style={{ flexGrow: sg.count }} />
            ))}
          </div>
        ) : null}
        <p className={styles.weekLine}>{weekLine(w)}</p>
        {w.segments.length > 1 ? (
          <div className={styles.weekLegend}>
            {w.segments.map((sg) => (
              <StatePill
                key={sg.state}
                state={sg.state}
                label={`${sg.count} ${stateWord(sg.state)}`}
              />
            ))}
          </div>
        ) : null}
        <p className={styles.weekMeta}>
          {w.upcoming ? `${w.upcoming} more airing in the next 7 days. ` : ''}
          {airing && !airing.enabled
            ? 'Auto-wishlist is off.'
            : ran
              ? `Auto-wishlist ran ${ran}.`
              : ''}
        </p>
        {w.missing > 0 && canDownload ? (
          <button
            type="button"
            className={styles.weekAction}
            disabled={busy}
            onClick={() => void wishMissing()}
          >
            {busy ? 'Adding…' : `Wishlist ${w.missing} missing`}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function stateWord(state: string): string {
  if (state === 'owned') return 'in';
  if (state === 'ignored') return 'skipped';
  return state;
}

/** watchlist and wishlist always; issues and findings only when something
 * needs a human. at zero they stay out of the way. */
export function CountTiles({
  watchlist,
  wishlist,
  issues,
  findings,
}: {
  watchlist: number | null;
  wishlist: number | null;
  issues: number;
  findings: number;
}) {
  const tile = (key: string, page: string, count: number, label: string, attention = false) => (
    <button
      key={key}
      type="button"
      className={
        attention ? `header-button dash-tile ${styles.tileAttention}` : 'header-button dash-tile'
      }
      onClick={() => goVideo(page)}
    >
      <span className={count > 0 ? 'hero-btn-badge has-items' : 'hero-btn-badge'}>{count}</span>
      <span className="hero-btn-label">{label}</span>
    </button>
  );
  return (
    <div className="header-quick-nav dash-tiles">
      {tile('watchlist', 'video-watchlist', watchlist ?? 0, 'On your watchlist')}
      {tile('wishlist', 'video-wishlist', wishlist ?? 0, 'On your wishlist')}
      {issues > 0
        ? tile('issues', 'video-issues', issues, issues === 1 ? 'Open issue' : 'Open issues', true)
        : null}
      {findings > 0
        ? tile(
            'findings',
            'video-tools',
            findings,
            findings === 1 ? 'Finding to review' : 'Findings to review',
            true,
          )
        : null}
    </div>
  );
}
