import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router';
import clsx from 'clsx';
import { useEffect, useMemo, useState } from 'react';

import type { AcqState, CalendarEpisode, CalendarMovie, CalendarScope, CalendarView } from '../calendar/-calendar.types';

import { calendarQueryOptions, followedQueryOptions } from '../calendar/-calendar.api';
import { backdropOf, stillOf } from '../calendar/-calendar.art';
import {
  MO,
  WD_FULL,
  addDays,
  airMins,
  byAirTime,
  daysOf,
  episodeCode,
  fmtMins,
  groupDrops,
  isoOf,
  orderedCounts,
  parseISO,
  showHue,
  stateLabel,
  stepAnchor,
  weekStart,
  windowFor,
  windowRange,
  windowTitle,
} from '../calendar/-calendar.model';
import { CalendarHero } from '../calendar/-ui/calendar-hero';
import { AgendaView, MonthView, WeekView } from '../calendar/-ui/calendar-views';
import styles from '../calendar/-ui/calendar.module.css';
import { StatePill } from '../calendar/-ui/state-pill';
import { requestOptionsQuery, requestTitle } from './-player.api';
import s from './-ui/player.module.css';

type PlayerCalView = CalendarView | 'cards';
type StateFilter = 'all' | AcqState;

export const Route = createFileRoute('/player/calendar')({
  component: PlayerCalendarPage,
});

function useNow(ms = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}

function PlayerCalendarPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const now = useNow();
  const clientToday = isoOf(new Date(now));

  const [view, setView] = useState<PlayerCalView>('week');
  const [anchor, setAnchor] = useState<string>(clientToday);
  const [scope, setScope] = useState<CalendarScope>('watchlist');
  const [filter, setFilter] = useState<StateFilter>('all');
  const [openEp, setOpenEp] = useState<CalendarEpisode | null>(null);
  const [openMovie, setOpenMovie] = useState<CalendarMovie | null>(null);
  const [requestedKeys, setRequestedKeys] = useState<Record<string, 'done' | 'error'>>({});

  const baseView: CalendarView = view === 'cards' ? 'agenda' : view;
  // Player rolling 7-day window: 5 days look-behind + Today + 1 day look-ahead (Tomorrow)
  const win =
    baseView === 'month'
      ? windowFor('month', anchor)
      : { start: addDays(anchor, -5), days: 7 };
  const params = { start: win.start, days: win.days, scope, specials: false };

  const modeQuery = useQuery(requestOptionsQuery());
  const requestMode = modeQuery.data?.modes?.show ?? modeQuery.data?.modes?.movie ?? 'none';
  const canRequest = requestMode === 'download' || requestMode === 'ask';
  const reqVerb = requestMode === 'download' ? 'Get' : 'Request';

  // profileId 0 uses the current active session profile on the server
  const query = useQuery(calendarQueryOptions(0, params));
  const followed = useQuery(followedQueryOptions(0, params));
  const data = query.data;
  const today = data?.today ?? clientToday;
  const autoOn = data?.automations?.airing?.enabled ?? true;

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

  const requestMutation = useMutation({
    mutationFn: async (payload: {
      key: string;
      kind: 'episode' | 'movie';
      tmdbId: number;
      title: string;
      seasonNumber?: number;
      episodeNumber?: number;
      year?: number | null;
    }) => {
      await requestTitle({
        kind: payload.kind,
        tmdb_id: payload.tmdbId,
        title: payload.title,
        year: payload.year ?? undefined,
        season: payload.seasonNumber,
        episode: payload.episodeNumber,
        get: requestMode === 'download',
      });
      return payload.key;
    },
    onSuccess: (key) => {
      setRequestedKeys((prev) => ({ ...prev, [key]: 'done' }));
      void qc.invalidateQueries({ queryKey: ['video-calendar'] });
    },
    onError: (_err, vars) => {
      setRequestedKeys((prev) => ({ ...prev, [vars.key]: 'error' }));
    },
  });

  const visibleEps = filter === 'all' ? episodes : episodes.filter((e) => e.acq === filter);
  const visibleMovies = filter === 'all' ? movies : movies.filter((m) => m.acq === filter);
  const counts = orderedCounts([...episodes, ...movies]);
  const currentWindow = today >= win.start && today <= addDays(win.start, win.days - 1);
  const isThisWeek = baseView !== 'month' && weekStart(anchor) === weekStart(today);

  const go = (dir: -1 | 1) => setAnchor((cur) => stepAnchor(baseView, cur, dir));
  const goToday = () => setAnchor(clientToday);

  const isEpisodeRequestable = (ep: CalendarEpisode) => {
    const aired = ep.air_date <= today;
    return aired && ep.acq === 'missing' && !!ep.show_tmdb_id;
  };

  const isMovieRequestable = (m: CalendarMovie) => {
    const aired = m.date <= today;
    return aired && m.acq === 'missing' && !!m.tmdb_id;
  };

  const handleOpenMovie = (m: CalendarMovie) => {
    if (m.movie_id) {
      void navigate({ to: `/player/movies/${m.movie_id}` });
    } else {
      setOpenMovie(m);
    }
  };

  const modalEp = openEp ? (episodes.find((e) => e.id === openEp.id) ?? openEp) : null;

  return (
    <div style={{ maxWidth: 1440, margin: '0 auto', padding: '16px 24px 64px' }}>
      <div className={styles.page}>
        {/* Manager-grade Top Header */}
        <header className={styles.top}>
          <div>
            <h1 className={styles.title}>
              {baseView === 'month'
                ? windowTitle('month', anchor, today)
                : anchor === clientToday
                  ? 'Recent & Tomorrow'
                  : windowTitle(baseView, anchor, today)}
            </h1>
            <div className={styles.range}>
              {baseView === 'month'
                ? windowRange('month', anchor)
                : `${MO[parseISO(win.start).getMonth()]} ${parseISO(win.start).getDate()} – ${MO[parseISO(addDays(win.start, win.days - 1)).getMonth()]} ${parseISO(addDays(win.start, win.days - 1)).getDate()}`}
            </div>
          </div>
          <div className={styles.tools}>
            <div className={styles.seg} role="group" aria-label={baseView === 'month' ? 'Month' : 'Week'}>
              <button type="button" className={styles.segIcon} onClick={() => go(-1)} aria-label="Previous">
                ‹
              </button>
              <button type="button" onClick={goToday} disabled={currentWindow && anchor === clientToday}>
                Today
              </button>
              <button type="button" className={styles.segIcon} onClick={() => go(1)} aria-label="Next">
                ›
              </button>
            </div>
            <div className={styles.seg} role="group" aria-label="View">
              {([
                ['week', 'Week'],
                ['cards', 'Cards'],
                ['agenda', 'Agenda'],
                ['month', 'Month'],
              ] as const).map(([v, label]) => (
                <button
                  key={v}
                  type="button"
                  className={clsx(v === view && styles.segOn)}
                  aria-pressed={v === view}
                  onClick={() => setView(v)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className={styles.seg} role="group" aria-label="Shows">
              <button
                type="button"
                className={clsx(scope === 'watchlist' && styles.segOn)}
                aria-pressed={scope === 'watchlist'}
                onClick={() => setScope('watchlist')}
              >
                Watchlist
              </button>
              <button
                type="button"
                className={clsx(scope === 'all' && styles.segOn)}
                aria-pressed={scope === 'all'}
                onClick={() => setScope('all')}
              >
                All library
              </button>
            </div>
          </div>
        </header>

        {/* Next Up Diagonal Billboard Hero */}
        {!query.isLoading && (
          <CalendarHero
            episodes={episodes}
            today={today}
            now={now}
            currentWeek={isThisWeek}
            autoOn={autoOn}
            onOpen={(ep) => setOpenEp(ep)}
          />
        )}

        {/* Filter Bar with Live State Counts */}
        {counts.length > 0 && (
          <div className={styles.filters} role="group" aria-label="Filter by status">
            <button
              type="button"
              className={clsx(styles.chip, filter === 'all' && styles.chipOn)}
              onClick={() => setFilter('all')}
            >
              All <span>{episodes.length + movies.length}</span>
            </button>
            {counts.map(([st, n]) => (
              <button
                key={st}
                type="button"
                className={clsx(styles.chip, filter === st && styles.chipOn)}
                data-state={st}
                onClick={() => setFilter(filter === st ? 'all' : st)}
              >
                <i className={styles.dot} data-state={st} />
                {stateLabel(st)} <span>{n}</span>
              </button>
            ))}
          </div>
        )}

        {/* Calendar Content */}
        {query.isLoading ? (
          <div style={{ padding: '48px 0', textAlign: 'center', color: 'var(--text-muted, #9aa0b4)' }}>
            Loading release schedule…
          </div>
        ) : visibleEps.length === 0 && visibleMovies.length === 0 ? (
          <div style={{ padding: '48px 0', textAlign: 'center', color: 'var(--text-muted, #9aa0b4)' }}>
            Nothing scheduled in this window.
          </div>
        ) : view === 'week' ? (
          <WeekView
            start={win.start}
            today={today}
            now={now}
            episodes={visibleEps}
            movies={visibleMovies}
            autoOn={autoOn}
            onOpen={(ep) => setOpenEp(ep)}
            onOpenMovie={handleOpenMovie}
            playerLinks
          />
        ) : view === 'agenda' ? (
          <AgendaView
            start={win.start}
            days={win.days}
            today={today}
            episodes={visibleEps}
            movies={visibleMovies}
            autoOn={autoOn}
            onOpen={(ep) => setOpenEp(ep)}
            onOpenMovie={handleOpenMovie}
            playerLinks
          />
        ) : view === 'month' ? (
          <MonthView
            start={win.start}
            anchor={anchor}
            today={today}
            episodes={visibleEps}
            movies={visibleMovies}
            autoOn={autoOn}
            onOpen={(ep) => setOpenEp(ep)}
            onPickDay={(d) => {
              setAnchor(d);
              setView('cards');
            }}
          />
        ) : (
          /* Visual Cards View: 16:9 stills/backdrops grouped by day with direct Request / Open actions */
          <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
            {daysOf(win.start, win.days).map((day) => {
              const dayEps = visibleEps.filter((e) => e.air_date === day).sort(byAirTime);
              const dayMvs = visibleMovies.filter((m) => m.date === day);
              if (!dayEps.length && !dayMvs.length) return null;
              const d = parseISO(day);
              const groups = groupDrops(dayEps);
              return (
                <section key={day}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>
                      {WD_FULL[d.getDay()]}, {MO[d.getMonth()]} {d.getDate()}
                    </h3>
                    {day === today && <span className={styles.agToday}>TODAY</span>}
                    <span style={{ fontSize: 12, opacity: 0.6 }}>
                      {dayEps.length + dayMvs.length} {dayEps.length + dayMvs.length === 1 ? 'item' : 'items'}
                    </span>
                  </div>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fill, minmax(270px, 1fr))',
                      gap: 14,
                    }}
                  >
                    {dayMvs.map((m) => {
                      const reqKey = `movie:${m.tmdb_id}`;
                      const reqState = requestedKeys[reqKey];
                      const showReq = canRequest && isMovieRequestable(m) && reqState !== 'done';
                      return (
                        <div
                          key={`m-${m.tmdb_id}-${m.type}`}
                          style={{
                            borderRadius: 14,
                            overflow: 'hidden',
                            background: 'rgba(255,255,255,0.04)',
                            border: '1px solid rgba(255,255,255,0.09)',
                            display: 'flex',
                            flexDirection: 'column',
                          }}
                        >
                          <div
                            style={{
                              position: 'relative',
                              aspectRatio: '16/9',
                              background: '#14151f',
                              cursor: m.movie_id ? 'pointer' : 'default',
                            }}
                            onClick={() => {
                              if (m.movie_id) void navigate({ to: `/player/movies/${m.movie_id}` });
                            }}
                          >
                            {m.poster_url && (
                              <img
                                src={m.poster_url}
                                alt=""
                                style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                              />
                            )}
                            <div style={{ position: 'absolute', top: 10, right: 10 }}>
                              <StatePill state={reqState === 'done' ? 'wishlist' : m.acq} />
                            </div>
                          </div>
                          <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 6, flex: 1 }}>
                            <div style={{ fontWeight: 700, fontSize: 15 }}>🎬 {m.title}</div>
                            <div style={{ fontSize: 12, opacity: 0.7 }}>
                              {m.type === 'cinema' ? 'In cinemas' : 'Home release'}
                              {m.year ? ` · ${m.year}` : ''}
                            </div>
                            <div style={{ marginTop: 'auto', paddingTop: 8, display: 'flex', gap: 8 }}>
                              {m.movie_id && m.acq === 'owned' && (
                                <Link to={`/player/movies/${m.movie_id}`} className={s.btn}>
                                  Open movie
                                </Link>
                              )}
                              {showReq && (
                                <button
                                  type="button"
                                  className={s.btnPrimary}
                                  disabled={requestMutation.isPending}
                                  onClick={() =>
                                    requestMutation.mutate({
                                      key: reqKey,
                                      kind: 'movie',
                                      tmdbId: m.tmdb_id,
                                      title: m.title,
                                      year: m.year,
                                    })
                                  }
                                >
                                  {reqVerb} movie
                                </button>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}

                    {groups.map((g) => {
                      const ep = g.lead;
                      const art = stillOf(ep, 500) || backdropOf(ep, 780);
                      const time = fmtMins(airMins(ep.airs_time));
                      const reqKey = `ep:${ep.show_tmdb_id}:${ep.season_number}:${ep.episode_number}`;
                      const reqState = requestedKeys[reqKey];
                      const showReq = canRequest && isEpisodeRequestable(ep) && reqState !== 'done';
                      return (
                        <div
                          key={g.key}
                          style={{
                            borderRadius: 14,
                            overflow: 'hidden',
                            background: 'rgba(255,255,255,0.04)',
                            border: '1px solid rgba(255,255,255,0.09)',
                            display: 'flex',
                            flexDirection: 'column',
                            ['--vcal-h' as string]: showHue(ep.show_title),
                          }}
                        >
                          <div
                            style={{
                              position: 'relative',
                              aspectRatio: '16/9',
                              background: 'linear-gradient(135deg, hsla(var(--vcal-h), 55%, 24%, 0.7), #12131c)',
                              cursor: 'pointer',
                            }}
                            onClick={() => setOpenEp(ep)}
                          >
                            {art && (
                              <img
                                src={art}
                                alt=""
                                style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                              />
                            )}
                            <div
                              style={{
                                position: 'absolute',
                                top: 10,
                                left: 10,
                                background: 'rgba(0,0,0,0.68)',
                                backdropFilter: 'blur(8px)',
                                padding: '3px 8px',
                                borderRadius: 6,
                                fontSize: 11,
                                fontWeight: 700,
                              }}
                            >
                              {episodeCode(g)}
                            </div>
                            <div style={{ position: 'absolute', top: 10, right: 10 }}>
                              <StatePill state={reqState === 'done' ? 'wishlist' : g.acq} />
                            </div>
                          </div>
                          <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
                            <div style={{ fontWeight: 700, fontSize: 15 }}>{ep.show_title}</div>
                            <div style={{ fontSize: 12.5, opacity: 0.8 }}>
                              {g.eps.length > 1
                                ? `${g.eps.length} episodes`
                                : ep.title || 'TBA'}
                            </div>
                            <div style={{ fontSize: 11.5, opacity: 0.6 }}>
                              {[time, ep.network].filter(Boolean).join(' · ')}
                            </div>
                            <div style={{ marginTop: 'auto', paddingTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                              {ep.show_id && !ep.not_in_library && (
                                <Link to={`/player/tv/${ep.show_id}`} className={s.btn}>
                                  Open show
                                </Link>
                              )}
                              {showReq && (
                                <button
                                  type="button"
                                  className={s.btnPrimary}
                                  disabled={requestMutation.isPending}
                                  onClick={() =>
                                    requestMutation.mutate({
                                      key: reqKey,
                                      kind: 'episode',
                                      tmdbId: ep.show_tmdb_id!,
                                      title: `${ep.show_title} · S${ep.season_number}E${ep.episode_number}`,
                                      seasonNumber: ep.season_number,
                                      episodeNumber: ep.episode_number,
                                    })
                                  }
                                >
                                  {reqVerb} episode
                                </button>
                              )}
                              {reqState === 'done' && (
                                <span style={{ fontSize: 12, color: '#6ee7b7', fontWeight: 600 }}>
                                  ✓ {requestMode === 'download' ? 'Added to wishlist' : 'Requested'}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </div>

      {/* Player-Native Episode Detail Modal (for Week / Agenda / Month / Hero clicks) */}
      {modalEp && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 80,
            background: 'rgba(0,0,0,0.72)',
            backdropFilter: 'blur(8px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 20,
          }}
          onClick={() => setOpenEp(null)}
        >
          <div
            style={{
              width: '100%',
              maxWidth: 520,
              borderRadius: 18,
              overflow: 'hidden',
              background: '#14151f',
              border: '1px solid rgba(255,255,255,0.12)',
              boxShadow: '0 24px 64px rgba(0,0,0,0.6)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ position: 'relative', aspectRatio: '16/9', background: '#0e0f17' }}>
              {(stillOf(modalEp, 780) || backdropOf(modalEp, 1280)) && (
                <img
                  src={(stillOf(modalEp, 780) || backdropOf(modalEp, 1280))!}
                  alt=""
                  style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                />
              )}
              <button
                type="button"
                onClick={() => setOpenEp(null)}
                aria-label="Close"
                style={{
                  position: 'absolute',
                  top: 12,
                  right: 12,
                  width: 34,
                  height: 34,
                  borderRadius: '50%',
                  border: '1px solid rgba(255,255,255,0.2)',
                  background: 'rgba(0,0,0,0.65)',
                  color: '#fff',
                  cursor: 'pointer',
                  fontSize: 16,
                }}
              >
                ✕
              </button>
            </div>
            <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                <h2 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>{modalEp.show_title}</h2>
                <StatePill state={modalEp.acq} large />
              </div>
              <div style={{ fontSize: 14, opacity: 0.85 }}>
                S{modalEp.season_number} E{modalEp.episode_number}
                {modalEp.title ? ` · ${modalEp.title}` : ''}
              </div>
              <div style={{ fontSize: 12.5, opacity: 0.65 }}>
                {[modalEp.air_date, fmtMins(airMins(modalEp.airs_time)), modalEp.network]
                  .filter(Boolean)
                  .join(' · ')}
              </div>
              {modalEp.overview && (
                <p style={{ margin: '4px 0 0', fontSize: 13.5, lineHeight: 1.5, opacity: 0.8 }}>
                  {modalEp.overview}
                </p>
              )}
              <div style={{ display: 'flex', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
                {modalEp.show_id && !modalEp.not_in_library && (
                  <Link to={`/player/tv/${modalEp.show_id}`} className={s.btnPrimary}>
                    Open in Player
                  </Link>
                )}
                {canRequest && isEpisodeRequestable(modalEp) && (() => {
                  const key = `ep:${modalEp.show_tmdb_id}:${modalEp.season_number}:${modalEp.episode_number}`;
                  const st = requestedKeys[key];
                  return st === 'done' ? (
                    <span style={{ alignSelf: 'center', fontSize: 13, color: '#6ee7b7', fontWeight: 600 }}>
                      ✓ {requestMode === 'download' ? 'Added to wishlist' : 'Requested'}
                    </span>
                  ) : (
                    <button
                      type="button"
                      className={s.btnPrimary}
                      disabled={requestMutation.isPending}
                      onClick={() =>
                        requestMutation.mutate({
                          key,
                          kind: 'episode',
                          tmdbId: modalEp.show_tmdb_id!,
                          title: `${modalEp.show_title} · S${modalEp.season_number}E${modalEp.episode_number}`,
                          seasonNumber: modalEp.season_number,
                          episodeNumber: modalEp.episode_number,
                        })
                      }
                    >
                      {reqVerb} episode
                    </button>
                  );
                })()}
                <button type="button" className={s.btn} onClick={() => setOpenEp(null)}>
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {/* Player-Native Movie Modal (for unowned/missing movies — never opens Manager!) */}
      {openMovie && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 80,
            background: 'rgba(0,0,0,0.72)',
            backdropFilter: 'blur(8px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 20,
          }}
          onClick={() => setOpenMovie(null)}
        >
          <div
            style={{
              width: '100%',
              maxWidth: 460,
              borderRadius: 18,
              overflow: 'hidden',
              background: '#14151f',
              border: '1px solid rgba(255,255,255,0.12)',
              boxShadow: '0 24px 64px rgba(0,0,0,0.6)',
              padding: 22,
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <h2 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>🎬 {openMovie.title}</h2>
              <StatePill state={openMovie.acq} large />
            </div>
            <div style={{ fontSize: 13, opacity: 0.72 }}>
              {[
                openMovie.type === 'cinema' ? 'In cinemas' : 'Home release',
                openMovie.date,
                openMovie.year ? String(openMovie.year) : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
              {canRequest && isMovieRequestable(openMovie) && (() => {
                const key = `movie:${openMovie.tmdb_id}`;
                const st = requestedKeys[key];
                return st === 'done' ? (
                  <span style={{ alignSelf: 'center', fontSize: 13, color: '#6ee7b7', fontWeight: 600 }}>
                    ✓ {requestMode === 'download' ? 'Added to wishlist' : 'Requested'}
                  </span>
                ) : (
                  <button
                    type="button"
                    className={s.btnPrimary}
                    disabled={requestMutation.isPending}
                    onClick={() =>
                      requestMutation.mutate({
                        key,
                        kind: 'movie',
                        tmdbId: openMovie.tmdb_id,
                        title: openMovie.title,
                        year: openMovie.year,
                      })
                    }
                  >
                    {reqVerb} movie
                  </button>
                );
              })()}
              <button type="button" className={s.btn} onClick={() => setOpenMovie(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
