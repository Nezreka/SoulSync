/**
 * Stats: a sentence first, then the numbers that answer "is my server doing
 * fine and who uses it" (the approved mockup, frame 3). Charts are drawn by
 * hand in svg and css; no chart library.
 */

import type { KeyboardEvent, ReactNode } from 'react';

import { useEffect, useMemo, useRef, useState } from 'react';

import type { PlayMethod, ShelfItem, StatsPayload } from '@/shell/server-activity-types';

import { getStats } from './api';
import {
  METHOD_LABEL,
  RANGE_WORDS,
  WEEKDAYS_MON,
  ago,
  awayPhrase,
  browserZone,
  count,
  dateTime,
  dayLabel,
  dayLabelLong,
  heatmapNote,
  heroSentence,
  hourTick,
  hoursNumber,
  mbps,
} from './format';
import { ChartIcon } from './icons';
import { openLink } from './links';
import st from './stats.module.css';
import { Avatar, Button, EmptyState, ErrorState, Poster, Skel, cx } from './ui';

export const RANGES = [
  { value: 7, label: '7 days' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
  { value: 365, label: 'Year' },
];

const cache = new Map<string, { at: number; data: StatsPayload }>();
const CACHE_MS = 60000;

function useStats(days: number) {
  const tz = useMemo(() => browserZone(), []);
  const key = `${days}|${tz}`;
  const [data, setData] = useState<StatsPayload | null>(() => cache.get(key)?.data ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!cache.has(key));
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const hit = cache.get(key);
    if (hit) setData(hit.data);
    if (hit && Date.now() - hit.at < CACHE_MS && nonce === 0) {
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getStats(days, tz, ctrl.signal)
      .then((result) => {
        if (ctrl.signal.aborted) return;
        setLoading(false);
        if (!result.ok || !result.data.ok) {
          setError(result.ok ? 'The play log could not be read.' : result.error);
          return;
        }
        cache.set(key, { at: Date.now(), data: result.data });
        setData(result.data);
      })
      .catch(() => {
        // a newer range took over
      });
    return () => ctrl.abort();
  }, [key, days, tz, nonce]);

  return { data, error, loading, retry: () => setNonce((x) => x + 1) };
}

/** a round axis top: 20, 50, 100, 2.5 */
export function niceMax(value: number): number {
  if (value <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(value)));
  for (const step of [1, 2, 2.5, 5, 10]) if (step * exp >= value) return step * exp;
  return 10 * exp;
}

/** a year is 52 weekly bars, not 365 slivers */
export function chartDays(stats: StatsPayload) {
  const days = stats.per_day;
  if (days.length <= 100) {
    return {
      unit: 'day' as const,
      bars: days.map((d) => ({
        key: d.date,
        label: dayLabelLong(d.date),
        tick: dayLabel(d.date),
        hours: d.hours,
        plays: d.plays,
        bright: d.weekend,
      })),
    };
  }
  const bars: {
    key: string;
    label: string;
    tick: string;
    hours: number;
    plays: number;
    bright: boolean;
  }[] = [];
  for (let i = 0; i < days.length; i += 7) {
    const week = days.slice(i, i + 7);
    bars.push({
      key: week[0].date,
      label: `Week of ${dayLabelLong(week[0].date)}`,
      tick: dayLabel(week[0].date),
      hours: week.reduce((sum, d) => sum + d.hours, 0),
      plays: week.reduce((sum, d) => sum + d.plays, 0),
      bright: false,
    });
  }
  return { unit: 'week' as const, bars };
}

function hoursShort(hours: number): string {
  if (hours <= 0) return '0h';
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`;
  return `${hours < 10 ? Math.round(hours * 10) / 10 : Math.round(hours)}h`;
}

// ── the parts ──

function Hero({ stats }: { stats: StatsPayload }) {
  return (
    <p className={st.hero}>
      {heroSentence(stats).map((seg, i) =>
        seg.strong ? <b key={i}>{seg.text}</b> : <span key={i}>{seg.text}</span>,
      )}
    </p>
  );
}

function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className={st.tile}>
      <div className={st.tileKey}>{label}</div>
      <div className={st.tileValue}>{value}</div>
      <div className={st.tileSub}>{sub ?? ' '}</div>
    </div>
  );
}

function gbText(gb: number): string {
  if (gb >= 100) return `${Math.round(gb)} GB`;
  if (gb >= 1) return `${Math.round(gb * 10) / 10} GB`;
  return `${Math.max(gb > 0 ? 1 : 0, Math.round(gb * 1000))} MB`;
}

function Tiles({ stats }: { stats: StatsPayload }) {
  const t = stats.tiles;
  const range = RANGE_WORDS[stats.days] ?? `${stats.days} days`;
  const change = t.plays.change_pct;
  const kinds = Object.entries(t.hours.by_kind)
    .filter(([, h]) => h >= 0.5)
    .sort((a, b) => b[1] - a[1])
    .map(([k, h]) => `${k === 'tv' ? 'TV' : k} ${hoursNumber(h)}`);
  return (
    <div className={st.tiles}>
      <Tile
        label="Plays"
        value={t.plays.value.toLocaleString('en-US')}
        sub={
          change != null ? (
            <>
              <span data-trend={change > 0 ? 'up' : change < 0 ? 'down' : undefined}>
                {change > 0 ? '+' : ''}
                {change}%
              </span>{' '}
              on the {range} before
            </>
          ) : t.plays.prev === 0 ? (
            `nothing the ${range} before`
          ) : null
        }
      />
      <Tile
        label="Hours played"
        value={hoursNumber(t.hours.value)}
        sub={
          kinds.length
            ? kinds.join(' · ') +
              (t.hours.estimated_hours >= 0.5
                ? ` · ${hoursNumber(t.hours.estimated_hours)} estimated`
                : '')
            : t.hours.value > 0
              ? 'under an hour of each kind'
              : 'nothing yet'
        }
      />
      <Tile
        label="Most at once"
        value={t.most_at_once?.value ?? 0}
        sub={t.most_at_once?.at ? dateTime(t.most_at_once.at) : 'nothing played together'}
      />
      <Tile
        label="Played as is"
        value={t.played_as_is.pct != null ? `${t.played_as_is.pct}%` : 'n/a'}
        sub={
          t.played_as_is.known < t.plays.value
            ? `of the ${t.played_as_is.known} plays with a known method`
            : 'Direct Play or Direct Stream'
        }
      />
      <Tile
        label="Sent remotely"
        value={gbText(t.sent_remotely.gb)}
        sub={
          t.sent_remotely.peak_kbps
            ? `peak ${mbps(t.sent_remotely.peak_kbps)} upload`
            : 'nothing left home'
        }
      />
    </div>
  );
}

const CW = 620;
const CH = 170;
const PAD_L = 30;
const PAD_B = 20;
const PAD_T = 8;

function DayChart({ stats }: { stats: StatsPayload }) {
  const { unit, bars } = chartDays(stats);
  const [active, setActive] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const top = niceMax(Math.max(...bars.map((b) => b.hours), 0.5));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => top * f);
  const n = bars.length || 1;
  const gap = n > 40 ? 2 : 3;
  const bw = (CW - PAD_L - (n - 1) * gap) / n;
  const plotH = CH - PAD_B - PAD_T;
  const every = n <= 8 ? 1 : n <= 31 ? 7 : n <= 60 ? 7 : 14;
  const r = Math.min(3, bw / 2);
  const x = (i: number) => PAD_L + i * (bw + gap);

  const onMove = (event: React.PointerEvent) => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect || !rect.width) return;
    const px = ((event.clientX - rect.left) / rect.width) * CW;
    const i = Math.floor((px - PAD_L) / (bw + gap));
    setActive(i >= 0 && i < n ? i : null);
  };
  const onKey = (event: KeyboardEvent) => {
    if (
      event.key !== 'ArrowLeft' &&
      event.key !== 'ArrowRight' &&
      event.key !== 'Home' &&
      event.key !== 'End'
    )
      return;
    event.preventDefault();
    setActive((a) => {
      if (event.key === 'Home') return 0;
      if (event.key === 'End') return n - 1;
      const from = a ?? n - 1;
      return Math.max(0, Math.min(n - 1, from + (event.key === 'ArrowRight' ? 1 : -1)));
    });
  };
  const bar = active != null ? bars[active] : null;
  const busiest = bars.reduce((best, b, i) => (b.hours > bars[best].hours ? i : best), 0);

  return (
    <div className={st.chart}>
      <div
        ref={box}
        className={st.chartBox}
        tabIndex={0}
        role="group"
        aria-label={`Hours played per ${unit}. Use the arrow keys to read each ${unit}.`}
        onPointerMove={onMove}
        onPointerLeave={() => setActive(null)}
        onKeyDown={onKey}
        onBlur={() => setActive(null)}
      >
        <svg viewBox={`0 0 ${CW} ${CH}`} aria-hidden="true">
          {ticks.map((v) => {
            const y = PAD_T + plotH * (1 - v / top);
            return (
              <g key={v}>
                <line className={st.gridLine} x1={PAD_L} x2={CW} y1={y} y2={y} />
                <text className={st.axis} x={PAD_L - 6} y={y + 3} textAnchor="end">
                  {hoursShort(v)}
                </text>
              </g>
            );
          })}
          {bars.map((b, i) => {
            const h = b.hours > 0 ? Math.max(2, (plotH * b.hours) / top) : 0;
            const y = CH - PAD_B - h;
            const bx = x(i);
            const rr = Math.min(r, h);
            const d = h
              ? `M${bx} ${CH - PAD_B} V${y + rr} q0 ${-rr} ${rr} ${-rr} h${bw - 2 * rr} q${rr} 0 ${rr} ${rr} V${CH - PAD_B} Z`
              : '';
            return (
              <g key={b.key}>
                {d ? (
                  <path
                    className={st.colBar}
                    data-bright={b.bright || undefined}
                    data-active={active === i || undefined}
                    data-dim={active != null && active !== i ? true : undefined}
                    d={d}
                    style={{ animationDelay: `${Math.min(i, 40) * 12}ms` }}
                  />
                ) : (
                  <rect className={st.zeroBar} x={bx} y={CH - PAD_B - 1} width={bw} height={1} />
                )}
                {i % every === 0 ? (
                  <text className={st.axis} x={bx + bw / 2} y={CH - 5} textAnchor="middle">
                    {n <= 8 ? WEEKDAYS_MON[(stats.per_day[i]?.weekday ?? 0) % 7] : b.tick}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
        {bar && active != null ? (
          <div
            className={st.tip}
            style={{ left: `${((x(active) + bw / 2) / CW) * 100}%` }}
            role="status"
          >
            <b>{bar.label}</b>
            <span>
              {hoursShort(bar.hours)} · {count(bar.plays, 'play')}
            </span>
          </div>
        ) : null}
      </div>
      <div className={st.chartFoot}>
        {bars[busiest]?.hours > 0
          ? `Busiest ${unit}: ${bars[busiest].label}, ${hoursShort(bars[busiest].hours)}`
          : 'No plays in this range yet'}
      </div>
    </div>
  );
}

const METHOD_ORDER: PlayMethod[] = ['direct_play', 'direct_stream', 'transcode'];

function HowItPlayed({ stats }: { stats: StatsPayload }) {
  const total = METHOD_ORDER.reduce((sum, m) => sum + stats.methods[m].plays, 0);
  const topReason = Math.max(1, ...stats.reasons.map((r) => r.plays));
  return (
    <>
      <div
        className={st.stack}
        role="img"
        aria-label={METHOD_ORDER.map(
          (m) => `${METHOD_LABEL[m]} ${stats.methods[m].pct ?? 0}%`,
        ).join(', ')}
      >
        {METHOD_ORDER.map((m) =>
          stats.methods[m].plays ? (
            <i key={m} data-method={m} style={{ flexGrow: stats.methods[m].plays }} />
          ) : null,
        )}
        {!total ? <i data-empty style={{ flexGrow: 1 }} /> : null}
      </div>
      <div className={st.legend}>
        {METHOD_ORDER.map((m) => (
          <span key={m}>
            <i data-method={m} />
            {METHOD_LABEL[m]} <em>{stats.methods[m].pct ?? 0}%</em>
          </span>
        ))}
      </div>
      <div className={st.rlist}>
        <div className={st.rlistHead}>Why plays were converted</div>
        {stats.reasons.length ? (
          stats.reasons.slice(0, 6).map((r) => (
            <div key={r.code} className={st.rrow}>
              <span className={st.rname}>{r.label}</span>
              <span className={st.rbar} data-tone="reason">
                <i style={{ transform: `scaleX(${r.plays / topReason})` }} />
              </span>
              <span className={st.rnum}>{r.plays}</span>
            </div>
          ))
        ) : (
          <div className={st.rquiet}>Nothing had to be converted.</div>
        )}
        {stats.hardware.total ? (
          <div className={cx(st.rrow, st.rrowQuiet)}>
            <span className={st.rname}>Converted in hardware</span>
            <span />
            <span className={st.rnum}>
              {stats.hardware.hw} of {stats.hardware.total}
            </span>
          </div>
        ) : null}
        {stats.unknown_method_plays ? (
          <div className={st.rquiet}>
            {count(stats.unknown_method_plays, 'play')} from Plex history have no method.
          </div>
        ) : null}
      </div>
    </>
  );
}

function Heatmap({ stats }: { stats: StatsPayload }) {
  const hm = stats.heatmap;
  const max = hm.max || 1;
  const note = heatmapNote(stats);
  return (
    <>
      <div className={st.heat} role="img" aria-label={note || 'No plays to map yet'}>
        <span />
        {Array.from({ length: 24 }, (_, hour) => (
          <span key={`h${hour}`} className={st.heatX}>
            {hour % 6 === 0 ? hourTick(hour) : ''}
          </span>
        ))}
        {hm.cells.map((row, day) => [
          <span key={`d${day}`} className={st.heatY}>
            {WEEKDAYS_MON[day]}
          </span>,
          ...row.map((hours, hour) => {
            const v = Math.min(1, hours / max);
            return (
              <i
                key={`${day}-${hour}`}
                className={st.heatCell}
                title={`${WEEKDAYS_MON[day]} ${hourTick(hour)}: ${hoursShort(hours)}`}
                style={{
                  background:
                    hours > 0
                      ? `rgba(var(--accent-rgb), ${(0.08 + v * 0.84).toFixed(2)})`
                      : undefined,
                }}
              />
            );
          }),
        ])}
      </div>
      <div className={st.heatFoot}>
        <span>{note || 'Nothing played in this range yet.'}</span>
        <span className={st.ramp} aria-hidden="true">
          less <i /> more
        </span>
      </div>
    </>
  );
}

function RankList({
  rows,
}: {
  rows: { key: string; name: string; value: number; hint?: string; home?: boolean }[];
}) {
  const top = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <div className={st.rquiet}>Nothing yet.</div>;
  return (
    <>
      {rows.map((r) => (
        <div key={r.key} className={st.rrow} title={r.hint}>
          <span className={st.rname}>{r.name}</span>
          <span className={st.rbar} data-tone={r.home ? 'home' : undefined}>
            <i style={{ transform: `scaleX(${r.value / top})` }} />
          </span>
          <span className={st.rnum}>{r.value.toLocaleString('en-US')}</span>
        </div>
      ))}
    </>
  );
}

function People({ stats }: { stats: StatsPayload }) {
  if (!stats.people.length) return <div className={st.rquiet}>Nobody has played anything yet.</div>;
  return (
    <div className={st.people}>
      {stats.people.map((p) => {
        const top = Math.max(...p.spark, 0.001);
        const second =
          p.lossless_pct != null && p.mostly?.kind === 'track'
            ? `${p.lossless_pct}% lossless`
            : p.top_location && p.away_pct
              ? p.top_location
              : awayPhrase(p.away_pct);
        return (
          <div key={p.user} className={st.person}>
            <div className={st.personHead}>
              <Avatar name={p.user} url={p.avatar} size={30} />
              <div className={st.personText}>
                <div className={st.personName}>
                  {p.user}
                  {p.live ? <i className={st.liveDot} title="Playing now" /> : null}
                </div>
                <div className={st.personMeta}>
                  {hoursShort(p.hours)} · {count(p.plays, 'play')}
                </div>
              </div>
            </div>
            <div className={st.mini} aria-hidden="true">
              {p.spark.map((h, i) => (
                <i
                  key={i}
                  style={{ transform: `scaleY(${Math.max(h > 0 ? 0.08 : 0.03, h / top)})` }}
                />
              ))}
            </div>
            <div className={st.personFacts}>
              {p.mostly ? (
                <>
                  Mostly <b>{p.mostly.title}</b>
                </>
              ) : null}
              {p.devices.length ? ` · ${p.devices.slice(0, 2).join(', ')}` : ''}
              <br />
              {[second, p.live ? 'seen now' : ago(p.last_seen)].filter(Boolean).join(' · ')}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function shelfSub(kind: keyof StatsPayload['top'], item: ShelfItem): string {
  const hrs = hoursShort(item.hours);
  if (kind === 'films') return `${count(item.people, 'person', 'people')} · ${hrs}`;
  if (kind === 'shows') return `${count(item.episodes ?? item.plays, 'episode')} · ${hrs}`;
  if (kind === 'artists') return `${count(item.tracks ?? item.plays, 'track')} · ${hrs}`;
  if (item.kind === 'podcast') return `podcast · ${count(item.episodes ?? item.plays, 'episode')}`;
  const how = item.finished ? 'finished' : item.percent != null ? `${item.percent}% through` : hrs;
  return item.user ? `${item.user} · ${how}` : how;
}

const SHELVES: {
  key: keyof StatsPayload['top'];
  title: string;
  kind: 'movie' | 'episode' | 'track' | 'audiobook';
}[] = [
  { key: 'films', title: 'Films', kind: 'movie' },
  { key: 'shows', title: 'Shows', kind: 'episode' },
  { key: 'artists', title: 'Artists', kind: 'track' },
  { key: 'books', title: 'Books & podcasts', kind: 'audiobook' },
];

function Shelves({ stats, onNavigate }: { stats: StatsPayload; onNavigate: () => void }) {
  return (
    <div className={st.grid4}>
      {SHELVES.map((shelf) => {
        const items = stats.top[shelf.key].slice(0, 5);
        return (
          <section key={shelf.key} className={st.panel} aria-label={shelf.title}>
            <div className={st.panelHead}>
              <b>{shelf.title}</b>
              <span>hours</span>
            </div>
            {items.length ? (
              <ol className={st.shelf}>
                {items.map((item, i) => {
                  const inner = (
                    <>
                      <span className={st.rank}>{i + 1}</span>
                      <Poster
                        thumb={item.thumb}
                        kind={
                          shelf.key === 'books' && item.kind === 'podcast' ? 'podcast' : shelf.kind
                        }
                        size="shelf"
                      />
                      <span className={st.shelfText}>
                        <span className={st.shelfTitle}>{item.title}</span>
                        <span className={st.shelfSub}>{shelfSub(shelf.key, item)}</span>
                      </span>
                    </>
                  );
                  return (
                    <li key={`${item.title}-${i}`}>
                      {item.link ? (
                        <button
                          type="button"
                          className={st.shelfItem}
                          onClick={() => {
                            onNavigate();
                            void openLink(item.link!, item.title);
                          }}
                        >
                          {inner}
                        </button>
                      ) : (
                        <div className={st.shelfItem}>{inner}</div>
                      )}
                    </li>
                  );
                })}
              </ol>
            ) : (
              <div className={st.rquiet}>Nothing yet.</div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function Panel({
  title,
  hint,
  children,
  className,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx(st.panel, className)} aria-label={title}>
      <div className={st.panelHead}>
        <b>{title}</b>
        {hint ? <span>{hint}</span> : null}
      </div>
      {children}
    </section>
  );
}

function StatsSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading stats">
      <div className={st.hero}>
        <Skel w="86%" h={20} />
        <Skel w="58%" h={20} className={st.skelGap} />
      </div>
      <div className={st.tiles}>
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className={st.tile}>
            <Skel w={70} h={10} />
            <Skel w={64} h={26} className={st.skelGap} />
            <Skel w="80%" h={10} className={st.skelGap} />
          </div>
        ))}
      </div>
      <div className={st.grid2}>
        <div className={st.panel}>
          <Skel w={150} h={12} />
          <Skel w="100%" h={150} className={st.skelGap} />
        </div>
        <div className={st.panel}>
          <Skel w={110} h={12} />
          <Skel w="100%" h={12} className={st.skelGap} />
          {[0, 1, 2, 3].map((i) => (
            <Skel key={i} w="100%" h={12} className={st.skelGap} />
          ))}
        </div>
      </div>
    </div>
  );
}

export function Stats({ days, onNavigate }: { days: number; onNavigate: () => void }) {
  const { data, error, loading, retry } = useStats(days);

  if (!data) {
    if (loading) return <StatsSkeleton />;
    return <ErrorState message={error || "Couldn't load the stats."} onRetry={retry} />;
  }
  const range = RANGE_WORDS[data.days] ?? `${data.days} days`;
  if (!data.tiles.plays.value) {
    return (
      <EmptyState
        icon={<ChartIcon size={22} />}
        title={`Nothing played in the last ${range}`}
        action={
          data.days < 365 ? (
            <Button tone="ghost" onClick={retry}>
              Check again
            </Button>
          ) : undefined
        }
      >
        Stats come from SoulSync's own play log: every play on Plex and Jellyfin, how long, how well
        and from where. They fill in as people watch and listen.
      </EmptyState>
    );
  }

  return (
    <div className={st.view} data-stale={loading || undefined}>
      <Hero stats={data} />
      <Tiles stats={data} />
      <div className={st.grid2}>
        <Panel
          title={`Hours played per ${data.days > 100 ? 'week' : 'day'}`}
          hint={data.days > 100 ? 'hover a week' : 'weekends brighter · hover a day'}
        >
          <DayChart stats={data} />
        </Panel>
        <Panel title="How it played" hint={count(data.tiles.plays.value, 'play')}>
          <HowItPlayed stats={data} />
        </Panel>
      </div>
      <div className={st.grid2}>
        <Panel title="When people play" hint="brighter means more hours">
          <Heatmap stats={data} />
        </Panel>
        <div className={st.stackCol}>
          <Panel title="Players" hint="plays">
            <RankList
              rows={data.players.slice(0, 6).map((p) => ({
                key: p.product,
                name: p.product,
                value: p.plays,
                hint: `${hoursShort(p.hours)} played`,
              }))}
            />
          </Panel>
          <Panel title="Where from" hint="plays">
            <RankList
              rows={data.locations.slice(0, 6).map((l) => ({
                key: l.label,
                name: l.label,
                value: l.plays,
                hint: `${hoursShort(l.hours)} played`,
                home: l.home,
              }))}
            />
          </Panel>
        </div>
      </div>
      <Panel title="People" hint="by hours played" className={st.spaced}>
        <People stats={data} />
      </Panel>
      <Shelves stats={data} onNavigate={onNavigate} />
    </div>
  );
}
