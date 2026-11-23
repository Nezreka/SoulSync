import { useQuery } from '@tanstack/react-query';
import { createFileRoute, Link } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import { apiClient } from '@/app/api-client';

import { remoteImage } from './-lib/images';
import { posterUrl } from './-lib/rails';
import { requestOptionsQuery, requestTitle, type RequestMode, type RequestResult } from './-player.api';
import { Art, Empty, PageTitle, styles as s } from './-ui/kit';
import { NotPartOfProfile } from './-ui/not-part';
import { useSections } from './-ui/sections';

export const Route = createFileRoute('/player/calendar')({
  component: CalendarPage,
});

interface CalendarEpisode {
  id?: number;
  show_id?: number | null;
  show_title?: string;
  tmdb_id?: number | null;
  season_number: number;
  episode_number: number;
  title?: string | null;
  air_date: string;
  has_file?: boolean | number;
  has_poster?: boolean;
  poster_url?: string | null;
  acq?: string;
  progress?: number | null;
}

interface CalendarMovie {
  id?: number;
  tmdb_id: number;
  title: string;
  year?: number | null;
  release_date?: string;
  event_date?: string;
  event_kind?: string;
  poster_url?: string | null;
  library_id?: number | null;
  acq?: string;
  progress?: number | null;
}

interface CalendarPayload {
  today: string;
  start: string;
  end: string;
  days: number;
  episodes: CalendarEpisode[];
  movies: CalendarMovie[];
}

type CalFilter = 'all' | 'owned' | 'upcoming' | 'missing';

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function addDays(baseIso: string, delta: number): string {
  const [y, m, d] = baseIso.split('-').map(Number);
  const dt = new Date(y, (m || 1) - 1, (d || 1) + delta);
  return isoDate(dt);
}

function formatDayHeading(dayIso: string, todayIso: string): string {
  if (dayIso === todayIso) return 'Today';
  if (dayIso === addDays(todayIso, 1)) return 'Tomorrow';
  if (dayIso === addDays(todayIso, -1)) return 'Yesterday';
  const [y, m, d] = dayIso.split('-').map(Number);
  const dt = new Date(y, (m || 1) - 1, d || 1);
  return dt.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
}

function acqTone(acq: string | undefined): { label: string; bg: string; border: string; color: string } {
  switch (acq) {
    case 'owned':
      return {
        label: 'In library',
        bg: 'rgba(16, 185, 129, 0.16)',
        border: 'rgba(16, 185, 129, 0.45)',
        color: '#6ee7b7',
      };
    case 'downloading':
      return {
        label: 'Downloading',
        bg: 'rgba(59, 130, 246, 0.16)',
        border: 'rgba(59, 130, 246, 0.45)',
        color: '#93c5fd',
      };
    case 'queued':
    case 'wanted':
      return {
        label: 'On wishlist',
        bg: 'rgba(168, 85, 247, 0.16)',
        border: 'rgba(168, 85, 247, 0.45)',
        color: '#d8b4fe',
      };
    case 'missing':
    case 'failed':
      return {
        label: 'Missing',
        bg: 'rgba(245, 158, 11, 0.16)',
        border: 'rgba(245, 158, 11, 0.45)',
        color: '#fcd34d',
      };
    default:
      return {
        label: 'Coming soon',
        bg: 'rgba(255, 255, 255, 0.06)',
        border: 'rgba(255, 255, 255, 0.14)',
        color: 'var(--pl-text-2)',
      };
  }
}

function CalendarPage() {
  const sections = useSections();
  const [days, setDays] = useState<7 | 14 | 28>(14);
  const [startOffset, setStartOffset] = useState(-3);
  const [scope, setScope] = useState<'all' | 'watchlist'>('all');
  const [filter, setFilter] = useState<CalFilter>('all');
  const [reqState, setReqState] = useState<Record<string, RequestResult | 'busy'>>({});

  const todayIso = useMemo(() => isoDate(new Date()), []);
  const startIso = useMemo(() => addDays(todayIso, startOffset), [todayIso, startOffset]);

  const cal = useQuery({
    queryKey: ['player', 'calendar', startIso, days, scope],
    queryFn: () =>
      apiClient
        .get('video/calendar', { searchParams: { start: startIso, days, scope } })
        .json<CalendarPayload>(),
    enabled: sections.video,
    staleTime: 2 * 60_000,
  });

  const followed = useQuery({
    queryKey: ['player', 'calendar-followed', startIso, days],
    queryFn: () =>
      apiClient
        .get('video/calendar/followed', { searchParams: { start: startIso, days } })
        .json<{ episodes?: CalendarEpisode[] }>(),
    enabled: sections.video,
    staleTime: 5 * 60_000,
  });

  const reqOpts = useQuery({ ...requestOptionsQuery(), enabled: sections.video });
  const showMode: RequestMode = reqOpts.data?.show ?? 'none';
  const movieMode: RequestMode = reqOpts.data?.movie ?? 'none';

  const realToday = cal.data?.today ?? todayIso;

  type UnifiedEntry =
    | { kind: 'episode'; key: string; date: string; ep: CalendarEpisode }
    | { kind: 'movie'; key: string; date: string; mv: CalendarMovie };

  const groupedByDate = useMemo(() => {
    const allEps = [...(cal.data?.episodes ?? []), ...(followed.data?.episodes ?? [])];
    const seenEp = new Set<string>();
    const entries: UnifiedEntry[] = [];
    for (const ep of allEps) {
      if (!ep.air_date) continue;
      const dedupe = `${ep.tmdb_id ?? ep.show_id ?? ep.show_title}-S${ep.season_number}E${ep.episode_number}`;
      if (seenEp.has(dedupe)) continue;
      seenEp.add(dedupe);
      entries.push({ kind: 'episode', key: `ep:${dedupe}`, date: ep.air_date, ep });
    }
    for (const mv of cal.data?.movies ?? []) {
      const dt = mv.event_date || mv.release_date;
      if (!dt) continue;
      entries.push({
        kind: 'movie',
        key: `mv:${mv.tmdb_id}:${mv.event_kind ?? dt}`,
        date: dt,
        mv,
      });
    }

    const filtered = entries.filter((entry) => {
      const acq = entry.kind === 'episode' ? entry.ep.acq : entry.mv.acq;
      if (filter === 'owned') return acq === 'owned';
      if (filter === 'missing') return acq === 'missing' || acq === 'failed';
      if (filter === 'upcoming') return acq === 'unaired' || entry.date > realToday;
      return true;
    });

    const map = new Map<string, UnifiedEntry[]>();
    for (const entry of filtered) {
      const list = map.get(entry.date);
      if (list) list.push(entry);
      else map.set(entry.date, [entry]);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [cal.data, followed.data, filter, realToday]);

  if (!sections.video) return <NotPartOfProfile what="Calendar" single />;

  const handleRequestEpisode = async (key: string, ep: CalendarEpisode) => {
    if (!ep.tmdb_id || showMode === 'none' || reqState[key] === 'busy') return;
    setReqState((prev) => ({ ...prev, [key]: 'busy' }));
    const res = await requestTitle({
      kind: 'episode',
      tmdb_id: ep.tmdb_id,
      title: ep.show_title || 'TV Show',
      season: ep.season_number,
      episode: ep.episode_number,
      get: showMode === 'download',
    });
    setReqState((prev) => ({ ...prev, [key]: res }));
  };

  const handleRequestMovie = async (key: string, mv: CalendarMovie) => {
    if (!mv.tmdb_id || movieMode === 'none' || reqState[key] === 'busy') return;
    setReqState((prev) => ({ ...prev, [key]: 'busy' }));
    const res = await requestTitle({
      kind: 'movie',
      tmdb_id: mv.tmdb_id,
      title: mv.title,
      year: mv.year ?? null,
      poster_url: mv.poster_url ?? null,
      get: movieMode === 'download',
    });
    setReqState((prev) => ({ ...prev, [key]: res }));
  };

  const renderReqLabel = (st: RequestResult | 'busy' | undefined, mode: RequestMode) => {
    if (st === 'busy') return 'Submitting…';
    if (st && typeof st === 'object') {
      if (st.state === 'got') return 'Added to wishlist ✓';
      if (st.state === 'asked') return st.already ? 'Already requested ✓' : 'Requested ✓';
      if (st.state === 'on-the-way') return 'On the way ✓';
      if (st.state === 'owned') return 'In library ✓';
    }
    return mode === 'download' ? 'Get' : 'Request';
  };

  const isReqDone = (st: RequestResult | 'busy' | undefined) =>
    !!st &&
    typeof st === 'object' &&
    (st.state === 'got' || st.state === 'asked' || st.state === 'on-the-way' || st.state === 'owned');

  return (
    <>
      <PageTitle>Release Calendar</PageTitle>

      {/* Window navigation + Scope + Status filters */}
      <div
        className={s.toolbar}
        style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', justifyContent: 'space-between' }}
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <button
            type="button"
            className={s.filterPill}
            onClick={() => setStartOffset((o) => o - days)}
            data-nav
          >
            ← Prev
          </button>
          <button
            type="button"
            className={s.filterPill}
            aria-pressed={startOffset === -3}
            onClick={() => setStartOffset(-3)}
            data-nav
          >
            Today
          </button>
          <button
            type="button"
            className={s.filterPill}
            onClick={() => setStartOffset((o) => o + days)}
            data-nav
          >
            Next →
          </button>
          <span style={{ width: 6 }} />
          {([7, 14, 28] as const).map((d) => (
            <button
              key={d}
              type="button"
              className={s.filterPill}
              aria-pressed={days === d}
              onClick={() => setDays(d)}
              data-nav
            >
              {d === 7 ? 'Week' : d === 14 ? '2 Weeks' : 'Month'}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          {(
            [
              ['all', 'All'],
              ['owned', 'In library'],
              ['upcoming', 'Coming soon'],
              ['missing', 'Missing'],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={s.filterPill}
              aria-pressed={filter === id}
              onClick={() => setFilter(id)}
              data-nav
            >
              {label}
            </button>
          ))}
          <button
            type="button"
            className={s.filterPill}
            aria-pressed={scope === 'watchlist'}
            onClick={() => setScope((v) => (v === 'all' ? 'watchlist' : 'all'))}
            data-nav
          >
            {scope === 'watchlist' ? '✓ Followed only' : 'All shows'}
          </button>
        </div>
      </div>

      {cal.isLoading ? <div className={s.skeleton} style={{ height: '42vh', marginTop: 16 }} /> : null}

      {!cal.isLoading && groupedByDate.length === 0 ? (
        <div className={s.rail}>
          <Empty title="Nothing on the calendar in this window">
            Try switching to a wider window or toggling between All shows and Followed.
          </Empty>
        </div>
      ) : null}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 22, marginTop: 14 }}>
        {groupedByDate.map(([dayIso, items]) => {
          const isToday = dayIso === realToday;
          return (
            <section key={dayIso} aria-label={dayIso}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  marginBottom: 10,
                }}
              >
                <h2
                  className={s.sectionTitle}
                  style={isToday ? { color: 'var(--accent-light, #a78bfa)' } : undefined}
                >
                  {formatDayHeading(dayIso, realToday)}
                </h2>
                <span style={{ fontSize: '0.8rem', color: 'var(--pl-text-3)' }}>{dayIso}</span>
              </div>

              <div className={s.episodeList}>
                {items.map((entry) => {
                  if (entry.kind === 'episode') {
                    const ep = entry.ep;
                    const tone = acqTone(ep.acq);
                    const code = `S${String(ep.season_number).padStart(2, '0')}E${String(ep.episode_number).padStart(2, '0')}`;
                    const aired = ep.air_date <= realToday;
                    const canRequest =
                      aired &&
                      (ep.acq === 'missing' || ep.acq === 'failed') &&
                      !!ep.tmdb_id &&
                      showMode !== 'none';
                    const st = reqState[entry.key];
                    const art = ep.show_id
                      ? posterUrl('show', ep.show_id, 342)
                      : remoteImage(ep.poster_url);

                    return (
                      <div key={entry.key} className={s.episodeCard}>
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 14,
                            padding: '10px 14px',
                          }}
                        >
                          <div
                            style={{
                              width: 50,
                              aspectRatio: '2 / 3',
                              borderRadius: 8,
                              overflow: 'hidden',
                              background: 'rgba(255,255,255,0.06)',
                              flex: 'none',
                            }}
                          >
                            <Art src={art} alt={ep.show_title ?? ''} />
                          </div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                              {ep.show_id ? (
                                <Link
                                  to="/player/tv/$showId"
                                  params={{ showId: String(ep.show_id) }}
                                  search={{ season: ep.season_number }}
                                  style={{
                                    color: 'var(--pl-text)',
                                    fontWeight: 700,
                                    fontSize: '0.96rem',
                                    textDecoration: 'none',
                                  }}
                                  data-nav
                                >
                                  {ep.show_title}
                                </Link>
                              ) : (
                                <span style={{ fontWeight: 700, fontSize: '0.96rem' }}>
                                  {ep.show_title}
                                </span>
                              )}
                              <span className={s.epChipAccent}>{code}</span>
                            </div>
                            {ep.title ? (
                              <div
                                style={{
                                  fontSize: '0.84rem',
                                  color: 'var(--pl-text-2)',
                                  marginTop: 2,
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                }}
                              >
                                {ep.title}
                              </div>
                            ) : null}
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 'none' }}>
                            <span
                              style={{
                                padding: '3px 10px',
                                borderRadius: 999,
                                fontSize: '0.76rem',
                                fontWeight: 700,
                                background: tone.bg,
                                border: `1px solid ${tone.border}`,
                                color: tone.color,
                              }}
                            >
                              {ep.acq === 'downloading' && ep.progress
                                ? `Downloading ${Math.round(ep.progress)}%`
                                : tone.label}
                            </span>
                            {canRequest ? (
                              <button
                                type="button"
                                className={isReqDone(st) ? s.btn : s.epActionPrimary}
                                disabled={st === 'busy' || isReqDone(st)}
                                onClick={() => void handleRequestEpisode(entry.key, ep)}
                                style={{ height: 32, padding: '0 13px', fontSize: '0.78rem' }}
                                data-nav
                              >
                                {renderReqLabel(st, showMode)}
                              </button>
                            ) : null}
                          </div>
                        </div>
                      </div>
                    );
                  }

                  const mv = entry.mv;
                  const tone = acqTone(mv.acq);
                  const aired = entry.date <= realToday;
                  const canRequest =
                    aired &&
                    (mv.acq === 'missing' || mv.acq === 'failed') &&
                    !!mv.tmdb_id &&
                    movieMode !== 'none';
                  const st = reqState[entry.key];
                  const art = mv.library_id
                    ? posterUrl('movie', mv.library_id, 342)
                    : remoteImage(mv.poster_url);

                  return (
                    <div key={entry.key} className={s.episodeCard}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 14,
                          padding: '10px 14px',
                        }}
                      >
                        <div
                          style={{
                            width: 50,
                            aspectRatio: '2 / 3',
                            borderRadius: 8,
                            overflow: 'hidden',
                            background: 'rgba(255,255,255,0.06)',
                            flex: 'none',
                          }}
                        >
                          <Art src={art} alt={mv.title} />
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            {mv.library_id ? (
                              <Link
                                to="/player/movies/$movieId"
                                params={{ movieId: String(mv.library_id) }}
                                style={{
                                  color: 'var(--pl-text)',
                                  fontWeight: 700,
                                  fontSize: '0.96rem',
                                  textDecoration: 'none',
                                }}
                                data-nav
                              >
                                {mv.title}
                              </Link>
                            ) : (
                              <span style={{ fontWeight: 700, fontSize: '0.96rem' }}>
                                {mv.title}
                              </span>
                            )}
                            <span className={s.epChipGold}>Movie</span>
                          </div>
                          {mv.event_kind ? (
                            <div style={{ fontSize: '0.82rem', color: 'var(--pl-text-2)', marginTop: 2 }}>
                              {mv.event_kind === 'digital' ? 'Digital release' : 'Theatrical release'}
                            </div>
                          ) : null}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 'none' }}>
                          <span
                            style={{
                              padding: '3px 10px',
                              borderRadius: 999,
                              fontSize: '0.76rem',
                              fontWeight: 700,
                              background: tone.bg,
                              border: `1px solid ${tone.border}`,
                              color: tone.color,
                            }}
                          >
                            {tone.label}
                          </span>
                          {canRequest ? (
                            <button
                              type="button"
                              className={isReqDone(st) ? s.btn : s.epActionPrimary}
                              disabled={st === 'busy' || isReqDone(st)}
                              onClick={() => void handleRequestMovie(entry.key, mv)}
                              style={{ height: 32, padding: '0 13px', fontSize: '0.78rem' }}
                              data-nav
                            >
                              {renderReqLabel(st, movieMode)}
                            </button>
                          ) : null}
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
    </>
  );
}
