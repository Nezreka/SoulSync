/**
 * The video dashboard's right rail: calm and compact, the system beside the
 * content. The week (did what aired land), what the engine runs next, and the
 * count tiles.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { type CSSProperties, useState } from 'react';

import { useFeatureOn } from '@/platform/features';

import type { CalendarPayload } from '../../calendar/-calendar.types';

import { CALENDAR_QUERY_KEY, wishlistMissing } from '../../calendar/-calendar.api';
import { timeAgo } from '../../calendar/-calendar.model';
import { tilePostersQuery } from './-vdash.api';
import { type WeekDay, weekCardStart, weekDays, weekLine, weekSummary } from './-vdash.model';
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
  // calendar switched off (#1909): the week still counts, no link to it
  const calendarOn = useFeatureOn('video_calendar');
  if (!data) return null;
  // the card is the last seven days: its number, its days and its button all
  // count the same episodes (the read behind it reaches a day further back)
  const first = weekCardStart(today);
  const inWeek = data.episodes.filter((e) => e.air_date >= first);
  const w = weekSummary(inWeek, today);
  const days = weekDays(inWeek, today);
  const wanted = days.reduce((n, day) => n + day.wanted, 0);
  const airing = data.automations?.airing ?? null;
  const ran = timeAgo(airing?.last_run ?? null, Date.now());

  const wishMissing = async () => {
    setBusy(true);
    try {
      const r = await wishlistMissing({
        start: first > start ? first : start,
        end: today,
        scope: 'watchlist',
        specials: false,
      });
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
        {calendarOn ? (
          <button
            type="button"
            className="dash-side-link"
            onClick={() => goVideo('video-calendar')}
          >
            Calendar
          </button>
        ) : null}
      </header>
      <div className={styles.week}>
        <div className={styles.weekTop}>
          <span className={styles.weekNum}>
            {w.owned}
            <small>/{w.aired}</small>
          </span>
          {/* said once: the sentence is the number's caption */}
          <p className={styles.weekLine}>{weekLine(w)}</p>
          {w.aired ? (
            <div className={styles.weekLegend}>
              <span data-state="owned">{w.owned} in library</span>
              {wanted ? <span data-state="wanted">{wanted} on the way</span> : null}
              {w.missing ? <span data-state="missing">{w.missing} missing</span> : null}
            </div>
          ) : null}
        </div>
        <div className={styles.days}>
          {days.map((day) => (
            <WeekDayCell key={day.date} day={day} canOpen={calendarOn} />
          ))}
        </div>
        <div className={styles.weekFoot}>
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
      </div>
    </article>
  );
}

/** One day: the shows that aired, how much of the day you have, in words. */
function WeekDayCell({ day, canOpen }: { day: WeekDay; canOpen: boolean }) {
  const body = (
    <>
      <span className={styles.dayName}>
        {day.label} <small>{day.dayOfMonth}</small>
      </span>
      <span className={styles.dayPosters}>
        {day.posters.map((poster) =>
          poster.src ? (
            <img
              key={poster.key}
              src={poster.src}
              alt=""
              title={poster.title}
              loading="lazy"
              onError={(event) => {
                event.currentTarget.hidden = true;
              }}
            />
          ) : (
            <i key={poster.key} title={poster.title}>
              {poster.title.charAt(0).toUpperCase()}
            </i>
          ),
        )}
        {day.more ? <em>+{day.more}</em> : null}
      </span>
      <span className={styles.dayBar} aria-hidden="true">
        {day.owned ? <u data-state="owned" style={{ flexGrow: day.owned }} /> : null}
        {day.wanted ? <u data-state="wanted" style={{ flexGrow: day.wanted }} /> : null}
        {day.missing ? <u data-state="missing" style={{ flexGrow: day.missing }} /> : null}
      </span>
      <span className={styles.daySay}>
        {day.aired ? (
          <>
            <b>{day.owned}</b> of {day.aired}
          </>
        ) : (
          'Nothing aired'
        )}
      </span>
      <span className={styles.dayMiss}>{day.missing ? `${day.missing} missing` : ''}</span>
    </>
  );
  const className = day.today ? `${styles.day} ${styles.dayToday}` : styles.day;
  const label = `${day.label} ${day.dayOfMonth}: ${
    day.aired ? `${day.owned} of ${day.aired} in your library` : 'nothing aired'
  }${day.missing ? `, ${day.missing} missing` : ''}`;
  return canOpen ? (
    <button
      type="button"
      className={className}
      aria-label={label}
      onClick={() => window.videoOpenCalendar?.({ date: day.date })}
    >
      {body}
    </button>
  ) : (
    <div className={className} aria-label={label}>
      {body}
    </div>
  );
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
  // what the two counts are counts OF: a hand of posters, as the music
  // dashboard's tiles carry faces and sleeves
  const facts = useQuery(tilePostersQuery()).data;
  const watch = facts?.watchlist.counts ?? {};
  const wish = facts?.wishlist.counts ?? {};
  const art = (pictures: string[] | undefined) => (
    <span
      className="room-tile-art room-tile-art--sleeves room-tile-art--posters"
      aria-hidden="true"
    >
      {(pictures ?? []).map((src, n) => (
        <img
          key={src}
          src={src}
          alt=""
          loading="lazy"
          style={{ '--n': n } as CSSProperties}
          onError={(event) => {
            event.currentTarget.hidden = true;
          }}
        />
      ))}
    </span>
  );
  /** what the count is made of: one line a part, drawn to scale */
  const split = (parts: [number | undefined, string, string][], note?: string) => {
    const shown = parts.filter(([n]) => (n ?? 0) > 0) as [number, string, string][];
    const total = shown.reduce((sum, [n]) => sum + n, 0);
    if (!shown.length) return null;
    return (
      <span className={styles.split}>
        {shown.map(([n, one, many]) => (
          <span className={styles.splitRow} key={many}>
            <b>{n.toLocaleString()}</b>
            <span>{n === 1 ? one : many}</span>
            <i aria-hidden="true">
              <u style={{ width: `${Math.max(3, Math.round((n / total) * 100))}%` }} />
            </i>
          </span>
        ))}
        {note ? <span className={styles.splitNote}>{note}</span> : null}
      </span>
    );
  };
  const tile = (
    key: string,
    page: string,
    count: number,
    label: string,
    pictures: string[] | undefined,
    parts: ReturnType<typeof split>,
  ) => (
    <button
      key={key}
      type="button"
      className={`header-button dash-tile ${styles.tile}`}
      onClick={() => goVideo(page)}
    >
      <span className={styles.tileLead}>
        {art(pictures)}
        <span className={count > 0 ? 'hero-btn-badge has-items' : 'hero-btn-badge'}>
          {count.toLocaleString()}
        </span>
        <span className="hero-btn-label">{label}</span>
      </span>
      {parts}
    </button>
  );
  /** issues and findings: a line each, only when there is something to look at */
  const alert = (key: string, page: string, count: number, label: string) => (
    <button key={key} type="button" className={styles.tileAlert} onClick={() => goVideo(page)}>
      <b>{count.toLocaleString()}</b>
      <span>{label}</span>
    </button>
  );
  const wishShows = wish.show ?? 0;
  return (
    <div className={`header-quick-nav dash-tiles ${styles.tiles}`} data-rail="tiles">
      {tile(
        'watchlist',
        'video-watchlist',
        watchlist ?? 0,
        'On your watchlist',
        facts?.watchlist.posters,
        split([
          [watch.show, 'show', 'shows'],
          [watch.person, 'person', 'people'],
          [watch.studio, 'studio', 'studios'],
        ]),
      )}
      {tile(
        'wishlist',
        'video-wishlist',
        wishlist ?? 0,
        'On your wishlist',
        facts?.wishlist.posters,
        split(
          [
            [wish.movie, 'movie', 'movies'],
            [wish.episode, 'episode', 'episodes'],
          ],
          wish.episode && wishShows
            ? `across ${wishShows.toLocaleString()} ${wishShows === 1 ? 'show' : 'shows'}`
            : undefined,
        ),
      )}
      {issues > 0 || findings > 0 ? (
        <span className={styles.tileAlerts}>
          {issues > 0
            ? alert('issues', 'video-issues', issues, issues === 1 ? 'Open issue' : 'Open issues')
            : null}
          {findings > 0
            ? alert(
                'findings',
                'video-tools',
                findings,
                findings === 1 ? 'Finding to review' : 'Findings to review',
              )
            : null}
        </span>
      ) : null}
    </div>
  );
}
