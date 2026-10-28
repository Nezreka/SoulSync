import { useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { useCallback, useEffect, useMemo, useState } from 'react';

import type {
  AcqState,
  AutomationStatus,
  CalendarEpisode,
  CalendarPayload,
  CalendarScope,
  CalendarView,
} from '../-calendar.types';
import type { CalendarSearch } from '../route';

import {
  CALENDAR_QUERY_KEY,
  calendarQueryOptions,
  followedQueryOptions,
  wishlistMissing,
} from '../-calendar.api';
import {
  addDays,
  isoOf,
  orderedCounts,
  stateLabel,
  stepAnchor,
  timeAgo,
  weekStart,
  windowFor,
  windowRange,
  windowTitle,
} from '../-calendar.model';
import { CalendarHero } from './calendar-hero';
import { AgendaView, MonthView, WeekView } from './calendar-views';
import styles from './calendar.module.css';
import { EpisodeModal } from './episode-modal';
import { FeedDialog } from './feed-dialog';

const toast = (message: string, type: string) => window.showToast?.(message, type);

function readPref<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode */
  }
}

/** Wall clock, ticking once a minute: "on now" and "next up" move with it. */
function useNow(ms = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}

export type StateFilter = 'all' | AcqState;

export function CalendarPage({
  profileId,
  isAdmin,
  canDownload,
  search,
  onSearch,
}: {
  profileId: number;
  isAdmin: boolean;
  canDownload: boolean;
  search: CalendarSearch;
  onSearch: (next: Partial<CalendarSearch>) => void;
}) {
  const now = useNow();
  const clientToday = isoOf(new Date(now));
  const view: CalendarView =
    search.view ?? readPref('vcalView2', ['week', 'agenda', 'month'], 'week');
  const anchor = search.date ?? clientToday;
  const [scope, setScopeState] = useState<CalendarScope>(() =>
    readPref('vcalScope', ['watchlist', 'all'], 'watchlist'),
  );
  const [specials, setSpecialsState] = useState(
    () => readPref('vcalSpecials', ['0', '1'], '0') === '1',
  );
  const [filter, setFilter] = useState<StateFilter>('all');
  const [menuOpen, setMenuOpen] = useState(false);
  const [feedOpen, setFeedOpen] = useState(false);
  const [openEp, setOpenEp] = useState<CalendarEpisode | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  const win = windowFor(view, anchor);
  const params = { start: win.start, days: win.days, scope, specials };
  const query = useQuery(calendarQueryOptions(profileId, params));
  const followed = useQuery(followedQueryOptions(profileId, params));
  const qc = useQueryClient();
  const data = query.data;
  const today = data?.today ?? clientToday;
  const autoOn = data?.automations?.airing?.enabled ?? true;

  // the library rows plus followed shows you don't own, one row per episode
  const episodes = useMemo(() => {
    const own = data?.episodes ?? [];
    const seen = new Set(
      own.map((e) => `${e.show_tmdb_id}|${e.season_number}|${e.episode_number}`),
    );
    const extra = (followed.data?.episodes ?? []).filter(
      (e) => !seen.has(`${e.show_tmdb_id}|${e.season_number}|${e.episode_number}`),
    );
    return [...own, ...extra];
  }, [data, followed.data]);
  const movies = data?.movies ?? [];

  const refresh = useCallback(() => {
    void qc.invalidateQueries({ queryKey: CALENDAR_QUERY_KEY });
  }, [qc]);

  // anything that changes an episode's state elsewhere on the video side
  useEffect(() => {
    const events = ['soulsync:video-wishlist-changed', 'soulsync:video-download-started'];
    events.forEach((e) => document.addEventListener(e, refresh));
    return () => events.forEach((e) => document.removeEventListener(e, refresh));
  }, [refresh]);

  // ?ep= opens that episode once it's in the window (the dashboard's row)
  useEffect(() => {
    if (!search.ep || !data) return;
    const hit = episodes.find((e) => String(e.id) === search.ep);
    if (hit) setOpenEp(hit);
    onSearch({ ep: undefined });
  }, [search.ep, data, episodes, onSearch]);

  // keep an open modal on the freshest copy of its episode
  const modalEp = openEp ? (episodes.find((e) => e.id === openEp.id) ?? openEp) : null;

  const setView = (v: CalendarView) => {
    writePref('vcalView2', v);
    onSearch({ view: v });
  };
  const setScope = (s: CalendarScope) => {
    writePref('vcalScope', s);
    setScopeState(s);
  };
  const setSpecials = (on: boolean) => {
    writePref('vcalSpecials', on ? '1' : '0');
    setSpecialsState(on);
  };
  const go = (dir: -1 | 1) => onSearch({ date: stepAnchor(view, anchor, dir) });
  const goToday = () => onSearch({ date: undefined });

  // ← / → step, T jumps to today, when nothing else has the keyboard
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (openEp || feedOpen || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
      if (e.key === 'ArrowLeft') go(-1);
      else if (e.key === 'ArrowRight') go(1);
      else if (e.key === 't' || e.key === 'T') goToday();
      else return;
      e.preventDefault();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  });

  const visibleEps = filter === 'all' ? episodes : episodes.filter((e) => e.acq === filter);
  const visibleMovies = filter === 'all' ? movies : movies.filter((m) => m.acq === filter);
  const counts = orderedCounts([...episodes, ...movies]);
  // library and followed-but-not-owned alike: the server's bulk add takes both
  const missing = episodes.filter((e) => e.acq === 'missing');
  const currentWindow = today >= win.start && today <= addDays(win.start, win.days - 1);
  const isThisWeek = view !== 'month' && weekStart(anchor) === weekStart(today);

  const wishMissing = async () => {
    setBulkBusy(true);
    try {
      const r = await wishlistMissing({
        start: win.start,
        end: addDays(win.start, win.days - 1),
        scope,
        specials,
      });
      if (!r.success) throw new Error(r.error || 'failed');
      toast(
        r.added
          ? `Added ${r.added} episode${r.added === 1 ? '' : 's'} to the wishlist`
          : 'Nothing new to add',
        'success',
      );
      document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
    } catch {
      toast('Could not add them to the wishlist', 'error');
    } finally {
      setBulkBusy(false);
    }
  };

  const hasAnything = visibleEps.length > 0 || visibleMovies.length > 0;

  return (
    <div className={styles.page} data-testid="video-calendar">
      <header className={styles.top}>
        <div>
          <h1 className={styles.title}>{windowTitle(view, anchor, today)}</h1>
          <div className={styles.range}>{windowRange(view, anchor)}</div>
        </div>
        <div className={styles.tools}>
          <div className={styles.seg} role="group" aria-label={view === 'month' ? 'Month' : 'Week'}>
            <button
              type="button"
              className={styles.segIcon}
              onClick={() => go(-1)}
              aria-label="Previous"
            >
              ‹
            </button>
            <button type="button" onClick={goToday} disabled={currentWindow && !search.date}>
              Today
            </button>
            <button
              type="button"
              className={styles.segIcon}
              onClick={() => go(1)}
              aria-label="Next"
            >
              ›
            </button>
          </div>
          <div className={styles.seg} role="group" aria-label="View">
            {(['week', 'agenda', 'month'] as const).map((v) => (
              <button
                key={v}
                type="button"
                className={clsx(v === view && styles.segOn)}
                aria-pressed={v === view}
                onClick={() => setView(v)}
              >
                {v[0].toUpperCase() + v.slice(1)}
              </button>
            ))}
          </div>
          <div className={styles.seg} role="group" aria-label="Shows">
            <button
              type="button"
              className={clsx(scope === 'watchlist' && styles.segOn)}
              aria-pressed={scope === 'watchlist'}
              title="Shows you follow, plus every airing show you haven't muted"
              onClick={() => setScope('watchlist')}
            >
              Watchlist
            </button>
            <button
              type="button"
              className={clsx(scope === 'all' && styles.segOn)}
              aria-pressed={scope === 'all'}
              title="Every airing show in your library"
              onClick={() => setScope('all')}
            >
              All library
            </button>
          </div>
          <div className={styles.menuWrap}>
            <button
              type="button"
              className={styles.iconBtn}
              aria-label="More"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((o) => !o)}
            >
              ⋯
            </button>
            {menuOpen && (
              <div className={styles.menu} role="menu" onMouseLeave={() => setMenuOpen(false)}>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    setFeedOpen(true);
                  }}
                >
                  Subscribe in a calendar app
                  <small>Google, Apple or Outlook</small>
                </button>
                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={specials}
                  onClick={() => setSpecials(!specials)}
                >
                  {specials ? '✓ ' : ''}Show specials
                  <small>Season 0 extras and bonus episodes</small>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    refresh();
                  }}
                >
                  Refresh now
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {view !== 'month' && episodes.length > 0 && (
        <CalendarHero
          episodes={episodes}
          today={today}
          now={now}
          currentWeek={isThisWeek}
          autoOn={autoOn}
          onOpen={setOpenEp}
        />
      )}

      <div className={styles.pulse}>
        <div className={styles.chips} role="group" aria-label="Filter by state">
          <button
            type="button"
            className={clsx(styles.chip, filter === 'all' && styles.chipOn)}
            aria-pressed={filter === 'all'}
            onClick={() => setFilter('all')}
          >
            All <b>{episodes.length + movies.length}</b>
          </button>
          {counts.map(([state, n]) => (
            <button
              key={state}
              type="button"
              className={clsx(styles.chip, filter === state && styles.chipOn)}
              data-state={state}
              aria-pressed={filter === state}
              onClick={() => setFilter(filter === state ? 'all' : state)}
            >
              <span className={styles.dot} aria-hidden />
              {state === 'unaired' && autoOn ? 'Upcoming' : stateLabel(state)} <b>{n}</b>
            </button>
          ))}
        </div>
        <Health data={data} now={now} />
      </div>

      {missing.length > 0 && canDownload && (
        <MissingCallout
          count={missing.length}
          airing={data?.automations?.airing ?? null}
          now={now}
          busy={bulkBusy}
          onWish={wishMissing}
          onShow={() => setFilter('missing')}
        />
      )}

      <div
        className={clsx(styles.body, query.isFetching && query.isPlaceholderData && styles.fading)}
      >
        {query.isError && !data ? (
          <div className={styles.empty}>
            <h3>Couldn't load the calendar</h3>
            <p>The server didn't answer. It retries on its own, or try again now.</p>
            <button type="button" className={styles.btn} onClick={refresh}>
              Try again
            </button>
          </div>
        ) : !data ? (
          <div className={styles.loading} aria-busy="true">
            Loading the calendar…
          </div>
        ) : !hasAnything ? (
          <div className={styles.empty}>
            <h3>
              {filter === 'all'
                ? 'Nothing airing'
                : `Nothing ${stateLabel(filter as AcqState).toLowerCase()}`}
            </h3>
            <p>
              {filter !== 'all'
                ? 'Nothing in this window is in that state.'
                : scope === 'watchlist'
                  ? 'Nothing from your watchlist airs in this window. Try All library, or follow more shows.'
                  : 'No show in your library airs in this window.'}
            </p>
            {filter !== 'all' && (
              <button type="button" className={styles.btn} onClick={() => setFilter('all')}>
                Show everything
              </button>
            )}
          </div>
        ) : view === 'week' ? (
          <WeekView
            start={win.start}
            today={today}
            now={now}
            episodes={visibleEps}
            movies={visibleMovies}
            autoOn={autoOn}
            onOpen={setOpenEp}
          />
        ) : view === 'agenda' ? (
          <AgendaView
            start={win.start}
            days={win.days}
            today={today}
            episodes={visibleEps}
            movies={visibleMovies}
            autoOn={autoOn}
            onOpen={setOpenEp}
          />
        ) : (
          <MonthView
            start={win.start}
            anchor={anchor}
            today={today}
            episodes={visibleEps}
            movies={visibleMovies}
            onPickDay={(day) => onSearch({ view: 'agenda', date: day })}
          />
        )}
      </div>

      {modalEp && (
        <EpisodeModal
          ep={modalEp}
          today={today}
          autoOn={autoOn}
          isAdmin={isAdmin}
          canDownload={canDownload}
          onClose={() => setOpenEp(null)}
          onChanged={refresh}
        />
      )}
      <FeedDialog open={feedOpen} scope={scope} onClose={() => setFeedOpen(false)} />
    </div>
  );
}

function Health({ data: d, now }: { data: CalendarPayload | undefined; now: number }) {
  if (!d) return null;
  const airing = d.automations?.airing;
  const sched = d.schedule;
  const refreshed = timeAgo(sched?.refreshed_at, now);
  const ran = timeAgo(airing?.last_run, now);
  return (
    <div className={styles.health}>
      <span
        className={clsx(sched?.stale && styles.warn)}
        title="Air dates are re-pulled from TMDB every night"
      >
        {refreshed ? `Schedules refreshed ${refreshed}` : 'Schedules never refreshed'}
      </span>
      {airing && (
        <span
          className={clsx(!airing.enabled && styles.warn)}
          title="Wishes every aired episode of the shows you follow, every few hours"
        >
          {!airing.enabled
            ? 'Auto-wishlist is off'
            : ran
              ? `Auto-wishlist ran ${ran}`
              : 'Auto-wishlist hasn’t run yet'}
        </span>
      )}
    </div>
  );
}

function MissingCallout({
  count,
  airing,
  now,
  busy,
  onWish,
  onShow,
}: {
  count: number;
  airing: AutomationStatus | null;
  now: number;
  busy: boolean;
  onWish: () => void;
  onShow: () => void;
}) {
  const ran = timeAgo(airing?.last_run, now);
  const why =
    airing && !airing.enabled
      ? 'Auto-wishlist is off, so aired episodes wait for you.'
      : 'Usually older episodes, or ones aired before you followed the show.';
  const label = `${count} aired episode${count === 1 ? '' : 's'} ${count === 1 ? "isn't" : "aren't"} on your wishlist`;
  return (
    <div className={styles.callout} role="status">
      <span className={styles.calloutIcon} aria-hidden>
        !
      </span>
      <p>
        <b>{label}.</b> {why}
        {airing?.enabled && ran ? ` Auto-wishlist last ran ${ran}.` : ''}
      </p>
      <button type="button" className={styles.btnGhost} onClick={onShow}>
        Show them
      </button>
      <button type="button" className={styles.btnPrimary} disabled={busy} onClick={onWish}>
        {busy ? 'Adding…' : `Wishlist ${count}`}
      </button>
    </div>
  );
}
