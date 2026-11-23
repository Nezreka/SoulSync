import type { CSSProperties } from 'react';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute, Link } from '@tanstack/react-router';
import clsx from 'clsx';
import { useEffect, useMemo, useState } from 'react';

import { appURL } from '@/platform/url-base';

import type {
  AcqState,
  CalendarEpisode,
  CalendarMovie,
  CalendarScope,
  CalendarView,
} from '../calendar/-calendar.types';

import {
  calendarQueryOptions,
  followedQueryOptions,
  showDetailQueryOptions,
} from '../calendar/-calendar.api';
import { backdropOf, stillOf } from '../calendar/-calendar.art';
import {
  MO,
  MO_FULL,
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
  whyLine,
  windowFor,
  windowRange,
  windowTitle,
} from '../calendar/-calendar.model';
import { Art } from '../calendar/-ui/art';
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

function fullDate(iso: string): string {
  const d = parseISO(iso);
  return `${WD_FULL[d.getDay()]}, ${MO_FULL[d.getMonth()]} ${d.getDate()}`;
}

function episodeCardArt(ep: CalendarEpisode, w = 600): string | null {
  return (
    stillOf(ep, w) ||
    backdropOf(ep, Math.max(w, 780)) ||
    (ep.show_id && !ep.not_in_library
      ? appURL(`/api/video/poster/show/${ep.show_id}?w=${w}`)
      : null) ||
    ep.still_url ||
    null
  );
}

function movieCardArt(m: CalendarMovie, w = 780): string | null {
  const libId = m.library_id ?? m.movie_id;
  if (libId) return appURL(`/api/video/backdrop/movie/${libId}?w=${w}`);
  return m.poster_url || null;
}

function PlayerCalendarPage() {
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
    baseView === 'month' ? windowFor('month', anchor) : { start: addDays(anchor, -5), days: 7 };
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
    return (
      (ep.acq === 'missing' || ep.acq === 'failed' || ep.acq === 'unaired') && !!ep.show_tmdb_id
    );
  };

  const isMovieRequestable = (m: CalendarMovie) => {
    return (m.acq === 'missing' || m.acq === 'failed' || m.acq === 'unaired') && !!m.tmdb_id;
  };

  const handleOpenMovie = (m: CalendarMovie) => {
    setOpenMovie(m);
  };

  const modalEp = openEp ? (episodes.find((e) => e.id === openEp.id) ?? openEp) : null;

  return (
    <div className={styles.page} style={{ padding: '8px 0 64px', width: '100%' }}>
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
          <div
            className={styles.seg}
            role="group"
            aria-label={baseView === 'month' ? 'Month' : 'Week'}
          >
            <button
              type="button"
              className={styles.segIcon}
              onClick={() => go(-1)}
              aria-label="Previous"
            >
              ‹
            </button>
            <button
              type="button"
              onClick={goToday}
              disabled={currentWindow && anchor === clientToday}
            >
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
            {(
              [
                ['week', 'Week'],
                ['cards', 'Cards'],
                ['agenda', 'Agenda'],
                ['month', 'Month'],
              ] as const
            ).map(([v, label]) => (
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
        <div
          style={{ padding: '48px 0', textAlign: 'center', color: 'var(--text-muted, #9aa0b4)' }}
        >
          Loading release schedule…
        </div>
      ) : visibleEps.length === 0 && visibleMovies.length === 0 ? (
        <div
          style={{ padding: '48px 0', textAlign: 'center', color: 'var(--text-muted, #9aa0b4)' }}
        >
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
                    {dayEps.length + dayMvs.length}{' '}
                    {dayEps.length + dayMvs.length === 1 ? 'item' : 'items'}
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
                    const mArt = movieCardArt(m, 780);
                    const mLibId = m.library_id ?? m.movie_id;
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
                          cursor: 'pointer',
                        }}
                        onClick={() => setOpenMovie(m)}
                      >
                        <div
                          style={{
                            position: 'relative',
                            aspectRatio: '16/9',
                            background:
                              'linear-gradient(135deg, hsla(245, 50%, 24%, 0.7), #12131c)',
                          }}
                        >
                          {mArt && (
                            <img
                              src={mArt}
                              alt=""
                              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                              onError={(e) => {
                                const img = e.currentTarget;
                                if (m.poster_url && img.src !== m.poster_url) {
                                  img.src = m.poster_url;
                                } else {
                                  img.style.display = 'none';
                                }
                              }}
                            />
                          )}
                          <div style={{ position: 'absolute', top: 10, right: 10 }}>
                            <StatePill state={reqState === 'done' ? 'wanted' : m.acq} />
                          </div>
                        </div>
                        <div
                          style={{
                            padding: '12px 14px',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 6,
                            flex: 1,
                          }}
                        >
                          <div style={{ fontWeight: 700, fontSize: 15 }}>🎬 {m.title}</div>
                          <div style={{ fontSize: 12, opacity: 0.7 }}>
                            {m.type === 'cinema' ? 'In cinemas' : 'Home release'}
                            {m.year ? ` · ${m.year}` : ''}
                          </div>
                          <div
                            style={{ marginTop: 'auto', paddingTop: 8, display: 'flex', gap: 8 }}
                          >
                            {mLibId && m.acq === 'owned' && (
                              <Link
                                to={`/player/movies/${mLibId}`}
                                className={s.btn}
                                onClick={(e) => e.stopPropagation()}
                              >
                                Open movie
                              </Link>
                            )}
                            {showReq && (
                              <button
                                type="button"
                                className={s.btnPrimary}
                                disabled={requestMutation.isPending}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  requestMutation.mutate({
                                    key: reqKey,
                                    kind: 'movie',
                                    tmdbId: m.tmdb_id,
                                    title: m.title,
                                    year: m.year,
                                  });
                                }}
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
                    const art = episodeCardArt(ep, 600);
                    const fallbackPoster =
                      ep.show_id && !ep.not_in_library
                        ? appURL(`/api/video/poster/show/${ep.show_id}?w=500`)
                        : null;
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
                          cursor: 'pointer',
                          ['--vcal-h' as string]: showHue(ep.show_title),
                        }}
                        onClick={() => setOpenEp(ep)}
                      >
                        <div
                          style={{
                            position: 'relative',
                            aspectRatio: '16/9',
                            background:
                              'linear-gradient(135deg, hsla(var(--vcal-h), 55%, 24%, 0.7), #12131c)',
                          }}
                        >
                          {art && (
                            <img
                              src={art}
                              alt=""
                              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                              onError={(e) => {
                                const img = e.currentTarget;
                                if (fallbackPoster && img.src !== fallbackPoster) {
                                  img.src = fallbackPoster;
                                } else {
                                  img.style.display = 'none';
                                }
                              }}
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
                            <StatePill state={reqState === 'done' ? 'wanted' : g.acq} />
                          </div>
                        </div>
                        <div
                          style={{
                            padding: '12px 14px',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 4,
                            flex: 1,
                          }}
                        >
                          <div style={{ fontWeight: 700, fontSize: 15 }}>{ep.show_title}</div>
                          <div style={{ fontSize: 12.5, opacity: 0.8 }}>
                            {g.eps.length > 1 ? `${g.eps.length} episodes` : ep.title || 'TBA'}
                          </div>
                          <div style={{ fontSize: 11.5, opacity: 0.6 }}>
                            {[time, ep.network].filter(Boolean).join(' · ')}
                          </div>
                          <div
                            style={{
                              marginTop: 'auto',
                              paddingTop: 10,
                              display: 'flex',
                              gap: 8,
                              flexWrap: 'wrap',
                            }}
                          >
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

      {/* Manager-Style Episode Detail Modal (Player-native navigation & Request) */}
      {modalEp && (
        <PlayerEpisodeModal
          ep={modalEp}
          today={today}
          autoOn={autoOn}
          canRequest={canRequest && isEpisodeRequestable(modalEp)}
          reqVerb={reqVerb}
          requestMode={requestMode}
          requestState={
            requestedKeys[
              `ep:${modalEp.show_tmdb_id}:${modalEp.season_number}:${modalEp.episode_number}`
            ]
          }
          requestPending={requestMutation.isPending}
          onRequest={() =>
            requestMutation.mutate({
              key: `ep:${modalEp.show_tmdb_id}:${modalEp.season_number}:${modalEp.episode_number}`,
              kind: 'episode',
              tmdbId: modalEp.show_tmdb_id!,
              title: `${modalEp.show_title} · S${modalEp.season_number}E${modalEp.episode_number}`,
              seasonNumber: modalEp.season_number,
              episodeNumber: modalEp.episode_number,
            })
          }
          onClose={() => setOpenEp(null)}
        />
      )}

      {/* Manager-Style Movie Detail Modal (Player-native navigation & Request) */}
      {openMovie && (
        <PlayerMovieModal
          movie={openMovie}
          today={today}
          canRequest={canRequest && isMovieRequestable(openMovie)}
          reqVerb={reqVerb}
          requestMode={requestMode}
          requestState={requestedKeys[`movie:${openMovie.tmdb_id}`]}
          requestPending={requestMutation.isPending}
          onRequest={() =>
            requestMutation.mutate({
              key: `movie:${openMovie.tmdb_id}`,
              kind: 'movie',
              tmdbId: openMovie.tmdb_id,
              title: openMovie.title,
              year: openMovie.year,
            })
          }
          onClose={() => setOpenMovie(null)}
        />
      )}
    </div>
  );
}

function PlayerEpisodeModal({
  ep,
  today,
  autoOn,
  canRequest,
  reqVerb,
  requestMode,
  requestState,
  requestPending,
  onRequest,
  onClose,
}: {
  ep: CalendarEpisode;
  today: string;
  autoOn: boolean;
  canRequest: boolean;
  reqVerb: string;
  requestMode: string;
  requestState?: 'done' | 'error';
  requestPending: boolean;
  onRequest: () => void;
  onClose: () => void;
}) {
  const detail = useQuery(showDetailQueryOptions(ep.not_in_library ? null : ep.show_id));
  const time = fmtMins(airMins(ep.airs_time));
  const backdrop = backdropOf(ep, 1000) || episodeCardArt(ep, 1000);
  const still = episodeCardArt(ep, 600);
  const eyebrow = [
    detail.data?.network ?? ep.network,
    detail.data?.year ?? ep.show_year,
    detail.data?.status ?? ep.show_status,
    detail.data?.content_rating,
  ]
    .filter(Boolean)
    .join('  ·  ');
  const showOverview =
    detail.data?.overview && detail.data.overview !== ep.overview ? detail.data.overview : null;
  const effectiveAcq: AcqState = requestState === 'done' ? 'wanted' : ep.acq;
  const inLibrary = Boolean(ep.show_id && !ep.not_in_library);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className={styles.modalViewport}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 99999,
        background: 'rgba(0, 0, 0, 0.76)',
        backdropFilter: 'blur(10px)',
      }}
      role="dialog"
      aria-modal="true"
      aria-label={`${ep.show_title} S${ep.season_number}E${ep.episode_number}`}
      onClick={onClose}
    >
      <div
        className={styles.modal}
        style={
          {
            '--vcal-h': showHue(ep.show_title),
            background: 'var(--bg-primary, #131520)',
            color: 'var(--text-bright, #f8fafc)',
          } as CSSProperties
        }
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className={styles.modalClose} aria-label="Close" onClick={onClose}>
          ×
        </button>
        <div className={styles.modalHero}>
          {backdrop && (
            <div className={styles.modalHeroBg} style={{ backgroundImage: `url("${backdrop}")` }} />
          )}
          <div className={styles.modalHeroScrim} />
          <div className={styles.modalHeroContent}>
            {eyebrow && <div className={styles.modalEyebrow}>{eyebrow}</div>}
            <h2 className={styles.modalShow}>{ep.show_title}</h2>
            {!!detail.data?.genres?.length && (
              <div className={styles.modalGenres}>
                {detail.data.genres.slice(0, 4).map((g) => (
                  <span key={g}>{g}</span>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className={styles.modalBody}>
          <div className={styles.modalEp}>
            <div className={styles.modalStill}>{still && <Art src={still} lazy={false} />}</div>
            <div className={styles.modalEpMain}>
              <div className={styles.modalSe}>
                S{ep.season_number} · E{ep.episode_number}
              </div>
              <h3 className={styles.modalEpTitle}>{ep.title || `Episode ${ep.episode_number}`}</h3>
              <div className={styles.modalWhen}>
                {fullDate(ep.air_date)} · {time || 'Anytime'}
                {ep.air_date === today ? ' · today' : ''}
              </div>
              <div className={styles.modalState}>
                <StatePill
                  state={effectiveAcq}
                  large
                  label={
                    effectiveAcq === 'downloading'
                      ? `Downloading · ${ep.progress ?? 0}%`
                      : undefined
                  }
                />
                <span className={styles.why}>
                  {whyLine({ ...ep, acq: effectiveAcq }, { autoOn })}
                </span>
              </div>
              <div className={styles.modalTags}>
                {ep.runtime_minutes ? <span>{ep.runtime_minutes} min</span> : null}
                {ep.rating ? <span>★ {Math.round(ep.rating * 10) / 10}</span> : null}
                {ep.not_in_library ? <span>Not in your library</span> : null}
              </div>
              <p className={ep.overview ? styles.modalOv : styles.modalOvNone}>
                {ep.overview || 'No episode synopsis yet.'}
              </p>
            </div>
          </div>
          {showOverview && (
            <div className={styles.modalAbout}>
              <h4>About the show</h4>
              <p>{showOverview}</p>
            </div>
          )}
        </div>
        <div className={styles.modalActions}>
          {canRequest && requestState !== 'done' && (
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={requestPending}
              onClick={onRequest}
            >
              {requestPending ? 'Requesting…' : `＋ ${reqVerb} episode`}
            </button>
          )}
          {requestState === 'done' && (
            <span className={styles.doneNote}>
              ✓ {requestMode === 'download' ? 'On your wishlist' : 'Requested'}
            </span>
          )}
          {inLibrary && (
            <Link
              to={`/player/tv/${ep.show_id}?season=${ep.season_number}&episode=${ep.episode_number}`}
              className={
                canRequest && requestState !== 'done' ? styles.btnGhost : styles.btnPrimary
              }
              onClick={onClose}
            >
              {ep.acq === 'owned' ? 'Go to episode →' : 'Open show in Player →'}
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}

function PlayerMovieModal({
  movie,
  today,
  canRequest,
  reqVerb,
  requestMode,
  requestState,
  requestPending,
  onRequest,
  onClose,
}: {
  movie: CalendarMovie;
  today: string;
  canRequest: boolean;
  reqVerb: string;
  requestMode: string;
  requestState?: 'done' | 'error';
  requestPending: boolean;
  onRequest: () => void;
  onClose: () => void;
}) {
  const libId = movie.library_id ?? movie.movie_id;
  const backdrop = movieCardArt(movie, 1000);
  const effectiveAcq: AcqState = requestState === 'done' ? 'wanted' : movie.acq;
  const eyebrow = [
    movie.type === 'cinema' ? 'In cinemas' : 'Home release',
    movie.year ? String(movie.year) : null,
  ]
    .filter(Boolean)
    .join('  ·  ');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className={styles.modalViewport}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 99999,
        background: 'rgba(0, 0, 0, 0.76)',
        backdropFilter: 'blur(10px)',
      }}
      role="dialog"
      aria-modal="true"
      aria-label={movie.title}
      onClick={onClose}
    >
      <div
        className={styles.modal}
        style={
          {
            '--vcal-h': showHue(movie.title),
            background: 'var(--bg-primary, #131520)',
            color: 'var(--text-bright, #f8fafc)',
          } as CSSProperties
        }
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className={styles.modalClose} aria-label="Close" onClick={onClose}>
          ×
        </button>
        <div className={styles.modalHero}>
          {backdrop && (
            <div className={styles.modalHeroBg} style={{ backgroundImage: `url("${backdrop}")` }} />
          )}
          <div className={styles.modalHeroScrim} />
          <div className={styles.modalHeroContent}>
            {eyebrow && <div className={styles.modalEyebrow}>{eyebrow}</div>}
            <h2 className={styles.modalShow}>{movie.title}</h2>
          </div>
        </div>
        <div className={styles.modalBody}>
          <div className={styles.modalEp}>
            <div className={styles.modalStill}>
              {(movie.poster_url || backdrop) && (
                <Art src={(movie.poster_url || backdrop)!} lazy={false} />
              )}
            </div>
            <div className={styles.modalEpMain}>
              <div className={styles.modalSe}>MOVIE</div>
              <h3 className={styles.modalEpTitle}>{movie.title}</h3>
              <div className={styles.modalWhen}>
                {movie.date ? fullDate(movie.date) : 'TBA'}
                {movie.date === today ? ' · today' : ''}
              </div>
              <div className={styles.modalState}>
                <StatePill state={effectiveAcq} large />
              </div>
            </div>
          </div>
        </div>
        <div className={styles.modalActions}>
          {canRequest && requestState !== 'done' && (
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={requestPending}
              onClick={onRequest}
            >
              {requestPending ? 'Requesting…' : `＋ ${reqVerb} movie`}
            </button>
          )}
          {requestState === 'done' && (
            <span className={styles.doneNote}>
              ✓ {requestMode === 'download' ? 'On your wishlist' : 'Requested'}
            </span>
          )}
          {libId && (
            <Link
              to={`/player/movies/${libId}`}
              className={
                canRequest && requestState !== 'done' ? styles.btnGhost : styles.btnPrimary
              }
              onClick={onClose}
            >
              Open movie in Player →
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
