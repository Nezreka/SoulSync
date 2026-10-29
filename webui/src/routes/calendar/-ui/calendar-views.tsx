import type { CSSProperties, MouseEvent } from 'react';

import clsx from 'clsx';
import { useState } from 'react';

import type { AcqState, CalendarEpisode, CalendarMovie } from '../-calendar.types';

import { movieHref, openMovieDetail, showHref, stillOf } from '../-calendar.art';
import {
  BANDS,
  MO,
  STATE_ORDER,
  WD,
  WD_FULL,
  airMins,
  bandOf,
  byAirTime,
  countStates,
  daysOf,
  episodeCode,
  fmtMins,
  groupDrops,
  orderedCounts,
  parseISO,
  showHue,
  stateLabel,
  stateShort,
  whyLine,
  type EpisodeGroup,
} from '../-calendar.model';
import { Art } from './art';
import styles from './calendar.module.css';
import { StatePill } from './state-pill';

type Open = (ep: CalendarEpisode) => void;

const MOVIE_TYPE: Record<CalendarMovie['type'], string> = {
  cinema: 'In cinemas',
  available: 'Home release',
};

/** A plain click opens the modal; cmd/ctrl/middle click keeps the real link. */
function clickOpen(e: MouseEvent, fn: () => void) {
  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  fn();
}

function byDay<T>(items: T[], day: (t: T) => string): Map<string, T[]> {
  const m = new Map<string, T[]>();
  for (const it of items) {
    const k = day(it);
    const list = m.get(k);
    if (list) list.push(it);
    else m.set(k, [it]);
  }
  return m;
}

/** A thin bar of the day's states, in reading order. */
function StateBar({ items }: { items: { acq: AcqState }[] }) {
  const c = countStates(items);
  const n = items.length || 1;
  return (
    <div className={styles.bar} aria-hidden>
      {STATE_ORDER.filter((s) => c.get(s)).map((s) => (
        <i key={s} data-state={s} style={{ width: `${((c.get(s) ?? 0) / n) * 100}%` }} />
      ))}
    </div>
  );
}

function daySummary(items: { acq: AcqState }[]): string {
  return orderedCounts(items)
    .map(([s, n]) => `${n} ${stateLabel(s).toLowerCase()}`)
    .join(' · ');
}

function groupLabel(g: EpisodeGroup, autoOn: boolean): string {
  if (g.eps.length > 1 && g.mixed) return `${g.ownedCount}/${g.eps.length}`;
  return stateShort({ acq: g.acq, progress: g.lead.progress }, { autoOn });
}

// ── week ───────────────────────────────────────────────────────────────────

export function WeekView({
  start,
  today,
  episodes,
  movies,
  autoOn,
  onOpen,
}: {
  start: string;
  today: string;
  now: number;
  episodes: CalendarEpisode[];
  movies: CalendarMovie[];
  autoOn: boolean;
  onOpen: Open;
}) {
  const days = daysOf(start, 7);
  const eps = byDay(episodes, (e) => e.air_date);
  const mvs = byDay(movies, (m) => m.date);
  return (
    <div className={styles.week}>
      {days.map((day) => {
        const list = (eps.get(day) ?? []).slice().sort(byAirTime);
        const dayMovies = mvs.get(day) ?? [];
        const d = parseISO(day);
        let lastBand: string | null = null;
        return (
          <section
            key={day}
            className={clsx(
              styles.col,
              day === today && styles.colToday,
              day < today && styles.colPast,
            )}
            aria-label={`${WD_FULL[d.getDay()]} ${MO[d.getMonth()]} ${d.getDate()}`}
          >
            <header className={styles.dh}>
              <div className={styles.dhRow}>
                <span className={styles.dhWd}>{day === today ? 'Today' : WD[d.getDay()]}</span>
                {list.length + dayMovies.length > 0 && (
                  <span className={styles.dhN}>{list.length + dayMovies.length}</span>
                )}
              </div>
              <div className={styles.dhDate}>
                {MO[d.getMonth()]} {d.getDate()}
              </div>
              <StateBar items={[...list, ...dayMovies]} />
            </header>
            <div className={styles.list}>
              {dayMovies.map((m) => (
                <a
                  key={`m${m.tmdb_id}${m.type}`}
                  href={movieHref(m)}
                  className={clsx(styles.tile, styles.tileMovie)}
                  data-state={m.acq}
                  onClick={(e) => clickOpen(e, () => openMovieDetail(m))}
                  title={`${m.title}${m.year ? ` (${m.year})` : ''} · ${MOVIE_TYPE[m.type]}`}
                >
                  <span className={styles.tileShow}>🎬 {m.title}</span>
                  <span className={styles.tileRow}>
                    <span className={styles.tileMeta}>{MOVIE_TYPE[m.type]}</span>
                    <StatePill
                      state={m.acq}
                      label={m.acq === 'downloading' ? `${m.progress ?? 0}%` : undefined}
                    />
                  </span>
                </a>
              ))}
              {groupDrops(list).map((g) => {
                const band = bandOf(airMins(g.lead.airs_time));
                const label = band !== lastBand ? BANDS.find((b) => b.key === band)?.label : null;
                lastBand = band;
                const time = fmtMins(airMins(g.lead.airs_time));
                return (
                  <div key={g.key} className={styles.tileWrap}>
                    {label && <div className={styles.band}>{label}</div>}
                    <a
                      href={showHref(g.lead)}
                      className={styles.tile}
                      data-state={g.acq}
                      style={{ '--vcal-h': showHue(g.lead.show_title) } as CSSProperties}
                      onClick={(e) => clickOpen(e, () => onOpen(g.lead))}
                      title={`${g.lead.show_title} · ${episodeCode(g)}${g.lead.title && g.eps.length === 1 ? ` · ${g.lead.title}` : ''} · ${whyLine(g.lead, { autoOn })}`}
                      data-cal-ep={String(g.lead.id)}
                    >
                      <span className={styles.tileShow}>{g.lead.show_title}</span>
                      <span className={styles.tileRow}>
                        <span className={styles.tileMeta}>
                          {episodeCode(g)}
                          {time ? ` · ${time}` : ''}
                          {g.eps.length > 1 ? ` · ${g.eps.length} eps` : ''}
                        </span>
                        <StatePill state={g.acq} label={groupLabel(g, autoOn)} />
                      </span>
                      {g.acq === 'downloading' && (
                        <span
                          className={styles.tileProg}
                          style={{ width: `${g.lead.progress ?? 0}%` }}
                        />
                      )}
                    </a>
                  </div>
                );
              })}
              {list.length + dayMovies.length === 0 && (
                <span className={styles.quiet}>Nothing airs</span>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

// ── agenda ─────────────────────────────────────────────────────────────────

export function AgendaView({
  start,
  days,
  today,
  episodes,
  movies,
  autoOn,
  onOpen,
}: {
  start: string;
  days: number;
  today: string;
  episodes: CalendarEpisode[];
  movies: CalendarMovie[];
  autoOn: boolean;
  onOpen: Open;
}) {
  const eps = byDay(episodes, (e) => e.air_date);
  const mvs = byDay(movies, (m) => m.date);
  const [showEarlier, setShowEarlier] = useState(false);
  const windowDays = daysOf(start, days);
  // on the week you're in, what's coming leads; the days already gone fold away
  const inWindow = windowDays.includes(today);
  const earlier = inWindow
    ? windowDays.filter(
        (d) => d < today && (eps.get(d)?.length ?? 0) + (mvs.get(d)?.length ?? 0) > 0,
      )
    : [];
  const earlierCount = earlier.reduce(
    (n, d) => n + (eps.get(d)?.length ?? 0) + (mvs.get(d)?.length ?? 0),
    0,
  );
  const shown = inWindow && !showEarlier ? windowDays.filter((d) => d >= today) : windowDays;
  return (
    <div className={styles.agenda}>
      {earlier.length > 0 && (
        <button type="button" className={styles.earlier} onClick={() => setShowEarlier((v) => !v)}>
          {showEarlier
            ? 'Hide earlier this week'
            : `Earlier this week · ${earlierCount} ${earlierCount === 1 ? 'item' : 'items'}`}
        </button>
      )}
      {shown.map((day) => {
        const list = (eps.get(day) ?? []).slice().sort(byAirTime);
        const dayMovies = mvs.get(day) ?? [];
        if (!list.length && !dayMovies.length) return null;
        const d = parseISO(day);
        const all = [...list, ...dayMovies];
        return (
          <section
            key={day}
            className={clsx(styles.agDay, day < today && styles.agDayPast)}
            data-day={day}
          >
            <header className={styles.agHead}>
              <h3>
                {WD_FULL[d.getDay()]}, {MO[d.getMonth()]} {d.getDate()}
                {day === today && <span className={styles.agToday}>TODAY</span>}
              </h3>
              <span className={styles.agSum}>
                {all.length} {all.length === 1 ? 'item' : 'items'} · {daySummary(all)}
              </span>
              <StateBar items={all} />
            </header>
            {dayMovies.map((m) => (
              <a
                key={`m${m.tmdb_id}${m.type}`}
                href={movieHref(m)}
                className={styles.row}
                onClick={(e) => clickOpen(e, () => openMovieDetail(m))}
              >
                <span className={clsx(styles.rowTime, styles.rowTimeNone)}>All day</span>
                <span className={clsx(styles.thumb, styles.thumbPoster)}>
                  {m.poster_url && <Art src={m.poster_url} />}
                </span>
                <span className={styles.rowMain}>
                  <span className={styles.rowTitle}>🎬 {m.title}</span>
                  <span className={styles.rowSub}>
                    {MOVIE_TYPE[m.type]}
                    {m.year ? ` · ${m.year}` : ''}
                  </span>
                </span>
                <span className={styles.rowState}>
                  <StatePill
                    state={m.acq}
                    label={
                      m.acq === 'downloading' ? `Downloading · ${m.progress ?? 0}%` : undefined
                    }
                    large
                  />
                  <span className={styles.why}>
                    {m.acq === 'owned' ? 'In your library' : 'On your wishlist'}
                  </span>
                </span>
              </a>
            ))}
            {groupDrops(list).map((g) => {
              const ep = g.lead;
              const time = fmtMins(airMins(ep.airs_time));
              const art = stillOf(ep, 400);
              return (
                <a
                  key={g.key}
                  href={showHref(ep)}
                  className={styles.row}
                  onClick={(e) => clickOpen(e, () => onOpen(ep))}
                  data-cal-ep={String(ep.id)}
                >
                  <span className={clsx(styles.rowTime, !time && styles.rowTimeNone)}>
                    {time || 'Anytime'}
                  </span>
                  <span
                    className={styles.thumb}
                    style={{ '--vcal-h': showHue(ep.show_title) } as CSSProperties}
                  >
                    {art && <Art src={art} />}
                  </span>
                  <span className={styles.rowMain}>
                    <span className={styles.rowTitle}>{ep.show_title}</span>
                    <span className={styles.rowSub}>
                      {episodeCode(g)}
                      {g.eps.length > 1
                        ? ` · ${g.eps.length} episodes`
                        : ep.title
                          ? ` · ${ep.title}`
                          : ''}
                    </span>
                    <span className={styles.rowSub2}>
                      {[ep.network, ep.not_in_library ? 'Not in your library' : null]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </span>
                  <span className={styles.rowState}>
                    <StatePill
                      state={g.acq}
                      large
                      label={
                        g.eps.length > 1 && g.mixed
                          ? `${g.ownedCount} of ${g.eps.length} in library`
                          : g.acq === 'downloading'
                            ? `Downloading · ${ep.progress ?? 0}%`
                            : g.acq === 'unaired' && autoOn
                              ? 'Upcoming · auto'
                              : undefined
                      }
                    />
                    {g.acq === 'downloading' && (
                      <span className={styles.progress}>
                        <i style={{ width: `${ep.progress ?? 0}%` }} />
                      </span>
                    )}
                    <span className={styles.why}>{whyLine(ep, { autoOn })}</span>
                  </span>
                </a>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}

// ── month ──────────────────────────────────────────────────────────────────

/** One line per show per day ("Natural High ×3"), the state of its first episode. */
function byShow(list: CalendarEpisode[]): { title: string; acq: AcqState; n: number }[] {
  const out: { title: string; acq: AcqState; n: number }[] = [];
  const seen = new Map<string, { title: string; acq: AcqState; n: number }>();
  for (const e of list) {
    const k = String(e.show_tmdb_id ?? e.show_title);
    const hit = seen.get(k);
    if (hit) {
      hit.n += 1;
      // the line shows the state that most wants attention
      if (
        STATE_ORDER.indexOf(e.acq) > STATE_ORDER.indexOf(hit.acq) &&
        (e.acq === 'missing' || e.acq === 'failed')
      )
        hit.acq = e.acq;
      continue;
    }
    const row = { title: e.show_title, acq: e.acq, n: 1 };
    seen.set(k, row);
    out.push(row);
  }
  return out;
}

const MONTH_LINES = 4;

export function MonthView({
  start,
  anchor,
  today,
  episodes,
  movies,
  onPickDay,
}: {
  start: string;
  anchor: string;
  today: string;
  episodes: CalendarEpisode[];
  movies: CalendarMovie[];
  onPickDay: (day: string) => void;
}) {
  const month = parseISO(anchor).getMonth();
  const eps = byDay(episodes, (e) => e.air_date);
  const mvs = byDay(movies, (m) => m.date);
  return (
    <div className={styles.month} role="grid">
      {WD.map((w) => (
        <div key={w} className={styles.mwd} role="columnheader">
          {w}
        </div>
      ))}
      {daysOf(start, 42).map((day) => {
        const d = parseISO(day);
        const list = (eps.get(day) ?? []).slice().sort(byAirTime);
        const dayMovies = mvs.get(day) ?? [];
        const lines = [
          ...dayMovies.map((m) => ({ title: `🎬 ${m.title}`, acq: m.acq, n: 1 })),
          ...byShow(list),
        ];
        const total = list.length + dayMovies.length;
        return (
          <button
            key={day}
            type="button"
            role="gridcell"
            className={clsx(
              styles.mc,
              d.getMonth() !== month && styles.mcOut,
              day === today && styles.mcToday,
            )}
            onClick={() => onPickDay(day)}
            aria-label={`${WD_FULL[d.getDay()]} ${MO[d.getMonth()]} ${d.getDate()}, ${total} ${total === 1 ? 'item' : 'items'}`}
          >
            <span className={styles.mn}>
              <span>{d.getDate()}</span>
              {total > 0 && <small>{total}</small>}
            </span>
            {total > 0 && <StateBar items={[...list, ...dayMovies]} />}
            {lines.slice(0, MONTH_LINES).map((l, i) => (
              <span key={i} className={styles.ml} data-state={l.acq}>
                <span className={styles.dot} aria-hidden />
                <span className={styles.mlText}>
                  {l.title}
                  {l.n > 1 && <em> ×{l.n}</em>}
                </span>
              </span>
            ))}
            {lines.length > MONTH_LINES && (
              <span className={styles.more}>+{lines.length - MONTH_LINES} more</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
