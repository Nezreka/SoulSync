/**
 * History: SoulSync's own play log, newest first, grouped into binge runs
 * ("Andor · 3 episodes"). Filters in one row, a summary line, a heading per
 * day with its totals, and a row that opens to its timeline and facts. Pages
 * of 50 load as you reach the end.
 */

import type { ReactNode } from 'react';

import { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';

import type {
  HistoryGroup,
  HistoryGroupDetail,
  HistoryPayload,
  PlayRow,
  TimelineBand,
} from '@/shell/server-activity-types';

import { type HistoryQuery, getHistory, getHistoryGroup, historyParams } from './api';
import { load, peek, put } from './cache';
import {
  METHOD_LABEL,
  browserZone,
  count,
  dayHeading,
  deviceLabel,
  duration,
  isoDay,
  mbps,
  pausedPhrase,
  playedLine,
  shortDate,
  timeOfDay,
  whereLabel,
} from './format';
import h from './history.module.css';
import { ChevronDown, ClockIcon, OpenIcon, SearchIcon } from './icons';
import { linkLabel, openLink } from './links';
import {
  Avatar,
  Button,
  EmptyState,
  ErrorState,
  Fact,
  MenuSelect,
  type MenuOption,
  MethodBadge,
  Poster,
  Segmented,
  Skel,
  TextBadge,
  cx,
  useDrawn,
} from './ui';

export interface HistoryFilters {
  kind: string;
  user: string;
  method: string;
  server: string;
  days: string;
  q: string;
}

export const NO_FILTERS: HistoryFilters = {
  kind: '',
  user: '',
  method: '',
  server: '',
  days: '7',
  q: '',
};

const KINDS = [
  { value: '', label: 'All' },
  { value: 'movies', label: 'Movies' },
  { value: 'tv', label: 'TV' },
  { value: 'music', label: 'Music' },
  { value: 'audiobooks', label: 'Audiobooks' },
];

const METHODS: MenuOption[] = [
  { value: '', label: 'Any playback' },
  { value: 'direct_play', label: 'Direct Play' },
  { value: 'direct_stream', label: 'Direct Stream' },
  { value: 'transcode', label: 'Transcode' },
];

const RANGES: MenuOption[] = [
  { value: '1', label: 'Today' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
  { value: '365', label: 'Last year' },
  { value: '', label: 'All time' },
];

export const PER_PAGE = 50;

export function toQuery(f: HistoryFilters, page: number, tz?: string): HistoryQuery {
  return {
    kind: f.kind || null,
    user: f.user || null,
    method: f.method || null,
    server: f.server || null,
    days: f.days ? Number(f.days) : null,
    q: f.q.trim() || null,
    page,
    per_page: PER_PAGE,
    tz,
  };
}

/** the groups of a page split under their day headings, in order */
export function byDay(groups: HistoryGroup[], tz?: string) {
  const out: { date: string; groups: HistoryGroup[] }[] = [];
  for (const g of groups) {
    const date = isoDay(g.started_at, tz);
    const last = out[out.length - 1];
    if (last && last.date === date) last.groups.push(g);
    else out.push({ date, groups: [g] });
  }
  return out;
}

interface Loaded {
  groups: HistoryGroup[];
  days: Map<string, { plays: number; played_ms: number }>;
  page: number;
  hasMore: boolean;
  summary: HistoryPayload['summary'];
  logStartedAt: number | null;
}

/** a cached answer younger than this is shown without asking again */
export const FRESH_MS = 15000;

export const historyKey = (filters: HistoryFilters, tz: string | undefined) =>
  `history${historyParams(toQuery(filters, 0, tz))}`;

function firstPage(d: HistoryPayload): Loaded {
  return {
    groups: d.groups,
    days: new Map(d.days.map((x) => [x.date, x])),
    page: 0,
    hasMore: d.has_more,
    summary: d.summary,
    logStartedAt: d.log_started_at,
  };
}

// the filters outlive a tab switch, so History comes back as it was left
const FILTERS_KEY = 'history:filters';
const remembered = () => peek<HistoryFilters>(FILTERS_KEY)?.data ?? NO_FILTERS;

/** the first page for the filters History will open with, read ahead */
export function prefetchHistory(tz: string | undefined): void {
  const filters = remembered();
  const key = historyKey(filters, tz);
  const hit = peek<HistoryPayload>(key);
  if (hit && Date.now() - hit.at < FRESH_MS) return;
  void load(key, () => getHistory(toQuery(filters, 0, tz))).catch(() => {
    // a nicety; the tab asks again when it opens
  });
}

function useHistory(filters: HistoryFilters, tz: string | undefined) {
  const key = historyKey(filters, tz);
  const initial = peek<HistoryPayload>(key)?.data;
  const [loaded, setLoaded] = useState<Loaded | null>(() => (initial ? firstPage(initial) : null));
  // the filters the rows on screen belong to: other filters dim them until the answer lands
  const [shownKey, setShownKey] = useState<string | null>(initial ? key : null);
  const [facets, setFacets] = useState<HistoryPayload['facets']>(() => initial?.facets ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!initial);
  const [more, setMore] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    const hit = peek<HistoryPayload>(key);
    // what is already known paints at once; the server is asked behind it
    if (hit) {
      setLoaded(firstPage(hit.data));
      setShownKey(key);
      if (hit.data.facets) setFacets(hit.data.facets);
      setError(null);
      if (Date.now() - hit.at < FRESH_MS && nonce === 0) {
        setLoading(false);
        return;
      }
    }
    setLoading(true);
    if (!hit) setError(null);
    load(key, () => getHistory(toQuery(filters, 0, tz)))
      .then((result) => {
        if (!live) return;
        setLoading(false);
        if (!result.ok || !result.data.ok) {
          setError(result.ok ? 'The play log could not be read.' : result.error);
          return;
        }
        const d = result.data;
        setError(null);
        if (d.facets) setFacets(d.facets);
        setLoaded(firstPage(d));
        setShownKey(key);
      })
      .catch(() => {
        // a newer filter took over
      });
    return () => {
      live = false;
    };
    // key stands for filters
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  const loadMore = useCallback(async () => {
    if (!loaded || !loaded.hasMore || more) return;
    setMore(true);
    const next = loaded.page + 1;
    try {
      const result = await getHistory(toQuery(filters, next, tz));
      if (result.ok && result.data.ok) {
        const d = result.data;
        setLoaded((prev) => {
          if (!prev) return prev;
          const days = new Map(prev.days);
          for (const day of d.days) if (!days.has(day.date)) days.set(day.date, day);
          const seen = new Set(prev.groups.map((g) => g.group_key));
          return {
            ...prev,
            groups: [...prev.groups, ...d.groups.filter((g) => !seen.has(g.group_key))],
            days,
            page: next,
            hasMore: d.has_more,
          };
        });
      }
    } finally {
      setMore(false);
    }
  }, [loaded, more, filters, tz]);

  return {
    loaded,
    facets,
    error,
    loading,
    stale: loading && shownKey !== key,
    loadingMore: more,
    loadMore,
    retry: () => setNonce((x) => x + 1),
  };
}

// ── filters ──

function FilterBar({
  filters,
  setFilters,
  facets,
}: {
  filters: HistoryFilters;
  setFilters: (f: HistoryFilters) => void;
  facets: HistoryPayload['facets'];
}) {
  const [text, setText] = useState(filters.q);
  const searchId = useId();
  const latest = useRef(filters);
  latest.current = filters;
  useEffect(() => setText(filters.q), [filters.q]);
  useEffect(() => {
    if (text === latest.current.q) return;
    const timer = setTimeout(() => setFilters({ ...latest.current, q: text }), 300);
    return () => clearTimeout(timer);
  }, [text, setFilters]);

  const people: MenuOption[] = [
    { value: '', label: 'Everyone' },
    ...(facets?.users ?? []).map((u) => ({
      value: u.user,
      label: u.user,
      hint: count(u.plays, 'play'),
      avatar: { name: u.user, url: u.avatar },
    })),
  ];
  const servers: MenuOption[] = [
    { value: '', label: 'Both servers' },
    { value: 'plex', label: 'Plex' },
    { value: 'jellyfin', label: 'Jellyfin' },
  ];

  return (
    <div className={h.filters} role="search">
      <label className={h.search} htmlFor={searchId}>
        <SearchIcon size={14} />
        <input
          id={searchId}
          type="search"
          value={text}
          placeholder="Search titles, people, players"
          aria-label="Search history"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape' && text) {
              event.stopPropagation();
              setText('');
            }
          }}
        />
      </label>
      <div className={h.kinds}>
        <Segmented
          label="Kind"
          options={KINDS}
          value={filters.kind}
          onChange={(kind) => setFilters({ ...filters, kind })}
        />
      </div>
      <MenuSelect
        label="Person"
        value={filters.user}
        options={people}
        onChange={(user) => setFilters({ ...filters, user })}
      />
      <MenuSelect
        label="Playback"
        value={filters.method}
        options={METHODS}
        onChange={(method) => setFilters({ ...filters, method })}
      />
      {(facets?.servers.length ?? 0) > 1 ? (
        <MenuSelect
          label="Server"
          value={filters.server}
          options={servers}
          onChange={(server) => setFilters({ ...filters, server })}
        />
      ) : null}
      <span className={h.rangeSlot}>
        <MenuSelect
          label="Dates"
          value={filters.days}
          options={RANGES}
          align="end"
          onChange={(days) => setFilters({ ...filters, days })}
        />
      </span>
    </div>
  );
}

function SummaryLine({ summary }: { summary: HistoryPayload['summary'] }) {
  const cells: [ReactNode, string][] = [
    [summary.plays.toLocaleString('en-US'), summary.plays === 1 ? 'play' : 'plays'],
    [duration(summary.played_ms), 'played'],
    [summary.people, summary.people === 1 ? 'person' : 'people'],
    [summary.converted_pct != null ? `${summary.converted_pct}%` : 'n/a', 'converted'],
    [
      summary.away_places,
      summary.away_places === 1 ? 'place away from home' : 'places away from home',
    ],
  ];
  return (
    <dl className={h.sumline}>
      {cells.map(([value, label]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

// ── a row ──

function placeOf(g: HistoryGroup): string {
  return whereLabel(g.connection.remote, g.connection.location);
}

function Played({ g }: { g: HistoryGroup }) {
  const pct = g.book_percent ?? g.percent;
  const done = g.count > 0 && g.finished >= g.count;
  if (g.live) {
    return (
      <div className={h.played} data-live>
        <span className={h.liveTag}>
          <i aria-hidden="true" />
          Playing now
        </span>
        <span className={h.playedNum}>{playedLine(g)}</span>
      </div>
    );
  }
  return (
    <div className={h.played} data-done={done || undefined}>
      <span className={h.playedNum}>{playedLine(g)}</span>
      {g.source !== 'plex_history' && pct != null ? (
        <span className={h.miniBar} aria-hidden="true">
          <i style={{ transform: `scaleX(${Math.min(100, pct) / 100})` }} />
        </span>
      ) : null}
    </div>
  );
}

function Playback({ g }: { g: HistoryGroup }) {
  if (!g.method) return <span className={h.muted}>Not known</span>;
  const extra = g.change || g.quality;
  return (
    <div className={h.ql}>
      <MethodBadge method={g.method} label={g.method_label} />
      {extra ? <TextBadge>{extra}</TextBadge> : null}
    </div>
  );
}

const HistoryRow = memo(function HistoryRow({
  g,
  open,
  onToggle,
  onNavigate,
}: {
  g: HistoryGroup;
  open: boolean;
  onToggle: (key: string) => void;
  onNavigate: () => void;
}) {
  const detailId = useId();
  const drawn = useDrawn(open);
  const device = deviceLabel(g.player) || g.player.product || 'Unknown player';
  return (
    <div className={h.row} data-open={open || undefined}>
      <button
        type="button"
        className={cx(h.grid, h.rowButton)}
        aria-expanded={open}
        aria-controls={detailId}
        onClick={() => onToggle(g.group_key)}
      >
        <span className={h.when}>{timeOfDay(g.started_at)}</span>
        <span className={h.titleCell}>
          <span className={h.posterSlot}>
            <Poster thumb={g.thumb} image={g.image} kind={g.kind} size="row" />
          </span>
          <span className={h.tt}>
            <span className={h.ttMain}>
              <span className={h.ttText}>{g.title}</span>
              {g.count > 1 && g.count_label ? (
                <span className={h.groupCount}>{g.count_label}</span>
              ) : null}
            </span>
            <span className={h.ttSub}>{g.subtitle}</span>
          </span>
        </span>
        <span className={h.person}>
          <Avatar name={g.user} url={g.user_avatar} />
          <span className={h.personName}>{g.user}</span>
        </span>
        <span className={h.where}>
          <b>{device}</b> · {placeOf(g)}
        </span>
        <span className={h.meta} aria-hidden="true">
          <Avatar name={g.user} url={g.user_avatar} size={18} />
          <b>{g.user}</b>
          <span>
            · {timeOfDay(g.started_at)} · {device} · {placeOf(g)}
          </span>
        </span>
        <Played g={g} />
        <Playback g={g} />
        <span className={h.chevron} aria-hidden="true">
          <ChevronDown size={13} />
        </span>
      </button>
      <div className={h.detailWrap} id={detailId} data-open={open || undefined} inert={!open}>
        <div className={h.detailClip}>
          {drawn ? <GroupDetail g={g} onNavigate={onNavigate} /> : null}
        </div>
      </div>
    </div>
  );
});

// ── the opened row ──

const detailCache = new Map<string, HistoryGroupDetail>();

/** the bands as fractions of the whole run */
export function timelineBands(bands: TimelineBand[]) {
  if (!bands.length)
    return { start: 0, end: 0, bands: [] as (TimelineBand & { left: number; width: number })[] };
  const start = Math.min(...bands.map((b) => b.start));
  const end = Math.max(...bands.map((b) => b.end));
  const span = Math.max(1, end - start);
  return {
    start,
    end,
    bands: bands.map((b) => ({
      ...b,
      left: ((b.start - start) / span) * 100,
      width: Math.max(0.4, ((b.end - b.start) / span) * 100),
    })),
  };
}

function playsFact(d: HistoryGroupDetail): { label: string; value: string; sub: string } {
  const plays = d.plays;
  const allDone = d.count > 0 && d.finished >= d.count;
  if (d.kind === 'episode' && plays.length > 1) {
    const eps = plays
      .map((p) => (p.episode != null ? `E${p.episode}` : null))
      .filter(Boolean)
      .reverse();
    return {
      label: 'Episodes',
      value: eps.join(', ') || count(plays.length, 'episode'),
      sub: allDone ? 'each played to the end' : `${d.finished} of ${d.count} played to the end`,
    };
  }
  if ((d.kind === 'audiobook' || d.kind === 'podcast') && plays.some((p) => p.chapter)) {
    const chapters = plays.map((p) => p.chapter?.index).filter((x): x is number => x != null);
    const lo = Math.min(...chapters);
    const hi = Math.max(...chapters);
    return {
      label: 'Chapters',
      value: lo === hi ? `Chapter ${lo}` : `Chapters ${lo} to ${hi}`,
      sub: d.book_percent != null ? `book ${d.book_percent}% through` : count(plays.length, 'part'),
    };
  }
  if (plays.length > 1) {
    return {
      label: d.kind === 'track' ? 'Tracks' : 'Plays',
      value: count(plays.length, d.kind === 'track' ? 'track' : 'play'),
      sub: allDone ? 'each played to the end' : `${d.finished} played to the end`,
    };
  }
  const p = plays[0];
  return {
    label: 'Played',
    value:
      p?.duration_ms != null
        ? `${duration(d.played_ms)} of ${duration(p.duration_ms)}`
        : duration(d.played_ms),
    sub:
      d.percent != null
        ? `${d.percent}% · ${d.live ? 'still playing' : allDone ? 'to the end' : 'stopped part way'}`
        : '',
  };
}

function qualityFact(d: HistoryGroupDetail): { value: string; sub: string } {
  const qd = d.quality_detail;
  const video = qd?.video?.[0];
  const audio = qd?.audio?.[0];
  const value =
    [video, !video ? audio : null].filter(Boolean).join(' · ') || d.quality || 'Not known';
  let sub = '';
  if (d.method_changes.length) {
    const first = d.method_changes[0];
    sub = `${METHOD_LABEL[first.from]}, then ${METHOD_LABEL[first.to]} at ${timeOfDay(first.at)}`;
  } else if (d.method) {
    sub = `${METHOD_LABEL[d.method]} the whole time`;
  }
  if (d.hw_engine) sub += ` · ${d.hw_engine}`;
  return { value, sub };
}

function playTitle(p: PlayRow): string {
  if (p.kind === 'episode' && p.episode != null) {
    return `${p.season != null ? `S${p.season} ` : ''}E${p.episode} · ${p.title}`;
  }
  if (p.chapter)
    return `Chapter ${p.chapter.index}${p.chapter.title ? ` · ${p.chapter.title}` : ''}`;
  if (p.kind === 'track' && p.track != null) return `${p.track}. ${p.title}`;
  return p.title;
}

function GroupDetail({ g, onNavigate }: { g: HistoryGroup; onNavigate: () => void }) {
  const [detail, setDetail] = useState<HistoryGroupDetail | null>(
    () => (!g.live && detailCache.get(g.group_key)) || null,
  );
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (detail && !g.live && nonce === 0) return;
    const ctrl = new AbortController();
    setError(null);
    getHistoryGroup(g.group_key, ctrl.signal)
      .then((result) => {
        if (ctrl.signal.aborted) return;
        if (!result.ok || !result.data.ok) {
          setError(
            result.ok || result.status === 404
              ? 'This play is no longer in the log.'
              : result.error,
          );
          return;
        }
        if (!result.data.live) detailCache.set(g.group_key, result.data);
        setDetail(result.data);
      })
      .catch(() => {
        // closed before it answered
      });
    return () => ctrl.abort();
    // detail is the cache, not a trigger
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [g.group_key, g.live, nonce]);

  if (error) {
    return (
      <div className={h.detail}>
        <div className={h.detailError} role="alert">
          {error}{' '}
          <Button tone="quiet" onClick={() => setNonce((x) => x + 1)}>
            Try again
          </Button>
        </div>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className={h.detail} aria-busy="true">
        <div className={h.tlHead}>
          <Skel w={50} h={10} />
          <Skel w={90} h={10} />
          <Skel w={50} h={10} />
        </div>
        <Skel w="100%" h={22} />
        <div className={h.facts}>
          {[0, 1, 2, 3].map((i) => (
            <Skel key={i} w="100%" h={62} />
          ))}
        </div>
      </div>
    );
  }

  const tl = timelineBands(detail.timeline);
  const paused = pausedPhrase(detail.pause_count, detail.paused_ms);
  const plays = playsFact(detail);
  const quality = qualityFact(detail);
  const server = detail.server === 'plex' ? 'Plex' : 'Jellyfin';
  const serverName =
    detail.server_name && detail.server_name.toLowerCase() !== detail.server
      ? `${server} · ${detail.server_name}`
      : server;
  const bufferingNote = detail.buffering_ms
    ? `buffered for ${duration(detail.buffering_ms)}`
    : 'no buffering';
  const fromPlex = detail.source === 'plex_history';

  return (
    <div className={h.detail}>
      {fromPlex ? (
        <p className={h.plexNote}>
          This play came from Plex's own history, from before SoulSync kept its log. Plex records
          when and who, not how long or how well.
        </p>
      ) : tl.bands.length ? (
        <>
          <div className={h.tlHead}>
            <span>{timeOfDay(tl.start)}</span>
            <span>Playback timeline</span>
            <span>{detail.live ? 'now' : timeOfDay(tl.end)}</span>
          </div>
          <div className={h.timeline} role="img" aria-label={timelineWords(detail, tl.bands)}>
            {tl.bands.map((band, i) => (
              <i
                key={i}
                data-state={band.state}
                style={{ left: `${band.left}%`, width: `${band.width}%` }}
                title={`${bandWord(band.state)} ${timeOfDay(band.start)} to ${timeOfDay(band.end)}`}
              />
            ))}
          </div>
          <div className={h.legend}>
            <span>
              <i data-state="playing" />
              Playing
            </span>
            <span>
              <i data-state="paused" />
              Paused{detail.pause_count ? ` (${duration(detail.paused_ms)})` : ' (never)'}
            </span>
            <span>
              <i data-state="buffering" />
              Buffering{detail.buffering_ms ? ` (${duration(detail.buffering_ms)})` : ' (none)'}
            </span>
            {tl.bands.some((b) => b.state === 'between') ? (
              <span>
                <i data-state="between" />
                Between plays
              </span>
            ) : null}
          </div>
        </>
      ) : null}

      <div className={h.facts}>
        <Fact label={plays.label} value={plays.value} sub={plays.sub} />
        <Fact
          label="Paused"
          value={
            paused
              ? paused.replace(/^Paused (\w)/, (_, c: string) => c.toUpperCase())
              : 'Never paused'
          }
          sub={bufferingNote}
        />
        <Fact label="Quality" value={quality.value} sub={quality.sub} />
        <Fact
          label="Server"
          value={serverName}
          sub={[detail.connection.ip, placeOf(detail)].filter(Boolean).join(' · ')}
        />
      </div>

      {detail.reasons.length ? (
        <div className={h.reasons}>
          <span className={h.reasonsKey}>Why it converted</span>
          {detail.reasons.map((r) => (
            <TextBadge key={r.code}>{r.label}</TextBadge>
          ))}
          {detail.reasons_inferred ? (
            <span className={h.inferred}>worked out by SoulSync from the stream details</span>
          ) : null}
        </div>
      ) : null}

      {detail.plays.length > 1 ? (
        <ol className={h.plays} aria-label="Each play">
          {detail.plays.map((p) => (
            <li key={p.play_key} className={h.play}>
              <span className={h.playWhen}>{timeOfDay(p.started_at)}</span>
              <span className={h.playTitle}>{playTitle(p)}</span>
              <span className={h.playLen}>
                {duration(p.played_ms)}
                {p.percent != null ? ` · ${p.percent}%` : ''}
              </span>
              <span className={h.miniBar} aria-hidden="true">
                <i style={{ transform: `scaleX(${Math.min(100, p.percent ?? 0) / 100})` }} />
              </span>
            </li>
          ))}
        </ol>
      ) : null}

      <div className={h.detailFoot}>
        <span>
          {[
            detail.sent_kb ? `Sent ${formatKb(detail.sent_kb)}` : null,
            detail.peak_kbps ? `peak ${mbps(detail.peak_kbps)}` : null,
            detail.gaps
              ? `SoulSync missed ${count(detail.gaps, 'stretch', 'stretches')} (a restart or the server away), not counted as played`
              : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
        {detail.link ? (
          <Button
            tone="link"
            onClick={() => {
              onNavigate();
              void openLink(detail.link!, detail.title);
            }}
          >
            {linkLabel(detail.link)}
            <OpenIcon size={13} />
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function bandWord(state: TimelineBand['state']): string {
  return state === 'between' ? 'Between plays' : state[0].toUpperCase() + state.slice(1);
}

function timelineWords(d: HistoryGroupDetail, bands: TimelineBand[]): string {
  const played = bands.filter((b) => b.state === 'playing').length;
  return `Played in ${count(played, 'stretch', 'stretches')}, ${
    pausedPhrase(d.pause_count, d.paused_ms)?.toLowerCase() ?? 'never paused'
  }`;
}

export function formatKb(kb: number): string {
  const gb = kb / 1e6;
  if (gb >= 1) return `${gb >= 10 ? Math.round(gb) : gb.toFixed(1)} GB`;
  return `${Math.max(1, Math.round(kb / 1000))} MB`;
}

// ── the view ──

function HistorySkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading history">
      <div className={h.sumline}>
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i}>
            <Skel w={54} h={20} />
            <Skel w={44} h={10} className={h.skelGap} />
          </div>
        ))}
      </div>
      <div className={h.day}>
        <Skel w={70} h={10} />
      </div>
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div key={i} className={h.row}>
          <div className={cx(h.grid, h.rowButton)} data-skeleton>
            <Skel w={46} h={11} />
            <span className={h.titleCell}>
              <Skel w={32} h={48} />
              <span className={h.tt}>
                <Skel w="70%" h={12} />
                <Skel w="45%" h={10} className={h.skelGap} />
              </span>
            </span>
            <span className={h.person}>
              <Skel w={20} h={20} round />
              <Skel w={40} h={11} />
            </span>
            <Skel w="70%" h={11} className={h.skelHideNarrow} />
            <Skel w="60%" h={11} className={h.skelHideNarrow} />
            <Skel w={90} h={20} className={h.skelHideNarrow} />
            <span />
          </div>
        </div>
      ))}
    </div>
  );
}

export function History({ onNavigate }: { onNavigate: () => void }) {
  const tz = useMemo(() => browserZone(), []);
  const [filters, setFiltersState] = useState<HistoryFilters>(remembered);
  const setFilters = useCallback((next: HistoryFilters) => {
    put(FILTERS_KEY, next);
    setFiltersState(next);
  }, []);
  const { loaded, facets, error, loading, stale, loadingMore, loadMore, retry } = useHistory(
    filters,
    tz,
  );
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const sentinel = useRef<HTMLDivElement>(null);

  const toggle = useCallback(
    (key: string) =>
      setOpen((current) => {
        const next = new Set(current);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      }),
    [],
  );

  // reaching the end loads the next page
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !loaded?.hasMore || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) void loadMore();
      },
      { rootMargin: '400px 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [loaded?.hasMore, loadMore]);

  const filtered =
    filters.kind || filters.user || filters.method || filters.server || filters.q.trim();
  const days = useMemo(() => (loaded ? byDay(loaded.groups, tz) : []), [loaded, tz]);

  let body: ReactNode;
  if (loading && !loaded) body = <HistorySkeleton />;
  else if (error && !loaded) body = <ErrorState message={error} onRetry={retry} />;
  else if (loaded && !loaded.groups.length) {
    body = filtered ? (
      <EmptyState
        icon={<SearchIcon size={22} />}
        title="Nothing matches"
        action={
          <Button tone="ghost" onClick={() => setFilters({ ...NO_FILTERS, days: filters.days })}>
            Clear filters
          </Button>
        }
      >
        No plays in this range fit those filters. Try a wider date range or fewer filters.
      </EmptyState>
    ) : (
      <EmptyState
        icon={<ClockIcon size={22} />}
        title={filters.days ? 'No plays in this range' : 'No plays yet'}
        action={
          filters.days ? (
            <Button tone="ghost" onClick={() => setFilters({ ...filters, days: '' })}>
              Show all time
            </Button>
          ) : undefined
        }
      >
        SoulSync writes down every play it sees on Plex and Jellyfin: who, what, how long, how well
        and from where. Plays show up here as soon as someone presses play.
      </EmptyState>
    );
  } else if (loaded) {
    body = (
      <div className={h.results} data-stale={stale || undefined}>
        <SummaryLine summary={loaded.summary} />
        <div className={cx(h.grid, h.colHead)} aria-hidden="true">
          <span>When</span>
          <span>Title</span>
          <span>Who</span>
          <span>Player · where</span>
          <span>Played</span>
          <span>Playback</span>
          <span />
        </div>
        {days.map((day) => {
          const totals = loaded.days.get(day.date);
          return (
            <section key={day.date} className={h.daySection} aria-label={dayHeading(day.date)}>
              <h3 className={h.day}>
                {dayHeading(day.date)}
                {totals ? (
                  <span>
                    {count(totals.plays, 'play')} · {duration(totals.played_ms)}
                  </span>
                ) : null}
              </h3>
              {day.groups.map((g) => (
                <HistoryRow
                  key={g.group_key}
                  g={g}
                  open={open.has(g.group_key)}
                  onToggle={toggle}
                  onNavigate={onNavigate}
                />
              ))}
            </section>
          );
        })}
        <div ref={sentinel} className={h.more}>
          {loaded.hasMore ? (
            <Button
              tone="ghost"
              onClick={() => void loadMore()}
              busy={loadingMore}
              disabled={loadingMore}
            >
              {loadingMore ? 'Loading…' : 'Show more'}
            </Button>
          ) : loaded.logStartedAt ? (
            <span className={h.logNote}>
              SoulSync's own log started {shortDate(loaded.logStartedAt)}. Anything older came from
              Plex's history.
            </span>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className={h.view}>
      <FilterBar filters={filters} setFilters={setFilters} facets={facets} />
      {body}
    </div>
  );
}
