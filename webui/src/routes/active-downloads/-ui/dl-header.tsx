/**
 * The downloads page header, one for both sides. Three tiers by weight:
 * the Music | Video switch with the actions, then the view tabs with the
 * filter chips (the counts live on the chips now), then a live strip while
 * anything moves, or one quiet line when nothing does.
 */

import { createContext, useContext, useEffect, useRef, useState } from 'react';

import type { ActionMenuItem } from '../../artist-detail/-ui/action-menu';

import { DlMore } from './dl-parts';

// ── the side switch ───────────────────────────────────────────────────────

export type DownloadsMedia = 'music' | 'video';

export interface DownloadsMediaState {
  media: DownloadsMedia;
  setMedia: (media: DownloadsMedia) => void;
  /** live counts per side. null until that side has said anything. */
  active: Record<DownloadsMedia, number | null>;
  /** each side reports its own count as it polls. */
  reportActive: (media: DownloadsMedia, count: number) => void;
}

/** Provided by the unified page. Absent (a side rendered alone, in a test) means no switch. */
export const DownloadsMediaContext = createContext<DownloadsMediaState | null>(null);

/** a side tells the switch how many of its downloads are moving. */
export function useReportActive(media: DownloadsMedia, count: number): void {
  const ctx = useContext(DownloadsMediaContext);
  const report = ctx?.reportActive;
  useEffect(() => {
    report?.(media, count);
  }, [report, media, count]);
}

const MusicIcon = (
  <svg
    width="15"
    height="15"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    aria-hidden="true"
  >
    <path d="M9 18V5l12-2v13" />
    <circle cx="6" cy="18" r="3" />
    <circle cx="18" cy="16" r="3" />
  </svg>
);
const VideoIcon = (
  <svg
    width="15"
    height="15"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    aria-hidden="true"
  >
    <rect x="2" y="4" width="20" height="16" rx="2" />
    <path d="M7 4v16M17 4v16M2 12h20" />
  </svg>
);

function MediaSwitch({ ctx }: { ctx: DownloadsMediaState }) {
  const side = (media: DownloadsMedia, label: string, icon: React.ReactNode) => {
    const n = ctx.active[media];
    return (
      <button
        type="button"
        role="tab"
        aria-selected={ctx.media === media}
        // the live count is decoration on the button; its name stays the side
        aria-label={label}
        className={`dlh-side${ctx.media === media ? ' dlh-side-on' : ''}`}
        onClick={() => ctx.setMedia(media)}
      >
        {icon}
        {label}
        {n ? (
          <span className="dlh-side-live" title={`${n} downloading`}>
            {n}
          </span>
        ) : n === 0 ? (
          <span className="dlh-side-idle">idle</span>
        ) : null}
      </button>
    );
  };
  return (
    <div className="dlh-switch" role="tablist" aria-label="Downloads media">
      {side('music', 'Music', MusicIcon)}
      {side('video', 'Video', VideoIcon)}
    </div>
  );
}

// ── the speed graph ───────────────────────────────────────────────────────

/** How many 2s samples the graph keeps: one minute. */
export const SPEED_SAMPLES = 30;

/**
 * The last minute of combined speed, sampled every 2s from whatever the page
 * last polled. browser-only on purpose: it starts empty when the page opens
 * and fills as you watch, nothing is stored server-side.
 */
export function useSpeedSamples(speed: number, live: boolean, everyMs = 2000): number[] {
  const latest = useRef(speed);
  latest.current = speed;
  const [samples, setSamples] = useState<number[]>([]);
  useEffect(() => {
    if (!live) return undefined;
    const tick = () => setSamples((prev) => [...prev, latest.current || 0].slice(-SPEED_SAMPLES));
    tick();
    const timer = setInterval(tick, everyMs);
    return () => clearInterval(timer);
  }, [live, everyMs]);
  return samples;
}

/** area + line, scaled to the busiest sample. nothing drawn under two points. */
export function Sparkline({ samples }: { samples: number[] }) {
  if (samples.length < 2) return <div className="dlh-spark dlh-spark-empty" aria-hidden="true" />;
  const w = 180;
  const h = 44;
  const max = Math.max(...samples, 1);
  const step = w / (SPEED_SAMPLES - 1);
  const x0 = w - step * (samples.length - 1);
  const pts = samples.map((v, i) => [x0 + i * step, h - 3 - (v / max) * (h - 8)] as const);
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
  const area = `${line} L${w} ${h} L${x0.toFixed(1)} ${h} Z`;
  return (
    <svg
      className="dlh-spark"
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Download speed over the last minute"
    >
      <defs>
        <linearGradient id="dlh-spark-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="rgb(var(--accent-rgb))" stopOpacity="0.4" />
          <stop offset="1" stopColor="rgb(var(--accent-rgb))" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path className="dlh-spark-area" d={area} fill="url(#dlh-spark-fill)" />
      <path className="dlh-spark-line" d={line} />
    </svg>
  );
}

// ── the header ────────────────────────────────────────────────────────────

export interface DlHeaderTab {
  value: string;
  label: string;
  title?: string;
  badge?: number | null;
  /** the old automation selector, kept on the tab. */
  dataFilter?: string;
}

export interface DlHeaderChip {
  value: string;
  label: string;
  count: number;
  bad?: boolean;
}

export interface DlLive {
  speedText: string;
  speedBps: number;
  downloading: number;
  done: number;
  active: number;
  queued: number;
  failed: number;
  /** the first thing moving: "Saudade" + "MARCØS". */
  nowTitle?: string;
  nowSub?: string;
  /** how many others are moving beside it. */
  nowMore?: number;
  etaText?: string;
}

export interface DlHeaderProps {
  tabs: DlHeaderTab[];
  view: string;
  onView: (value: string) => void;
  chips: DlHeaderChip[] | null;
  filter: string;
  onFilter: (value: string) => void;
  historyCount: number | null;
  onOpenHistory: (() => void) | null;
  menu: ActionMenuItem[];
  live: DlLive | null;
  /** the one line shown when nothing moves. */
  quiet: string;
}

function LiveStrip({ live }: { live: DlLive }) {
  const samples = useSpeedSamples(live.speedBps, true);
  const total = Math.max(1, live.done + live.active + live.queued + live.failed);
  const pct = (n: number) => `${(n / total) * 100}%`;
  return (
    <div className="dlh-strip" id="adl-count">
      <div className="dlh-speed">
        <strong>{live.speedText || '·'}</strong>
        <span>{live.downloading} downloading</span>
      </div>
      <div className="dlh-now">
        {live.nowTitle ? (
          <div className="dlh-now-line">
            Now: <b>{live.nowTitle}</b>
            {live.nowSub ? ` · ${live.nowSub}` : ''}
            {live.nowMore ? `, and ${live.nowMore} more` : ''}
            {live.etaText ? (
              <>
                {' · '}
                <b>{live.etaText}</b>
              </>
            ) : null}
          </div>
        ) : (
          <div className="dlh-now-line">{live.etaText || 'Working on it'}</div>
        )}
        <div className="dlh-bar" aria-hidden="true">
          <i className="dlh-bar-done" style={{ width: pct(live.done) }} />
          <i className="dlh-bar-fail" style={{ width: pct(live.failed) }} />
          <i className="dlh-bar-active" style={{ width: pct(live.active) }} />
        </div>
        <div className="dlh-legend">
          <span className="dlh-l-done">
            <em>{live.done}</em> done
          </span>
          <span className="dlh-l-active">
            <em>{live.active}</em> active
          </span>
          {live.queued ? (
            <span className="dlh-l-queued">
              <em>{live.queued}</em> queued
            </span>
          ) : null}
          {live.failed ? (
            <span className="dlh-l-failed">
              <em>{live.failed}</em> failed
            </span>
          ) : null}
        </div>
      </div>
      <Sparkline samples={samples} />
    </div>
  );
}

const HistoryIcon = (
  <svg
    width="15"
    height="15"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M3 3v5h5" />
    <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" />
    <path d="M12 7v5l4 2" />
  </svg>
);

export function DlHeader({
  tabs,
  view,
  onView,
  chips,
  filter,
  onFilter,
  historyCount,
  onOpenHistory,
  menu,
  live,
  quiet,
}: DlHeaderProps) {
  const ctx = useContext(DownloadsMediaContext);
  return (
    <div className="dlh">
      <div className="dlh-top">
        {ctx ? <MediaSwitch ctx={ctx} /> : <span />}
        <div className="dlh-actions" id="adl-actions">
          {onOpenHistory ? (
            <button
              type="button"
              className="dlc-btn dlh-history"
              title="Download history"
              onClick={onOpenHistory}
            >
              {HistoryIcon}
              History
              {historyCount ? (
                <span className="dlh-history-n" data-vdh-count>
                  {historyCount.toLocaleString()}
                </span>
              ) : null}
            </button>
          ) : null}
          <span className="dlh-more">
            <DlMore items={menu} label="Download actions" />
          </span>
        </div>
      </div>

      <div className="dlh-nav">
        <div className="dlh-tabs" id="adl-view-tabs" role="tablist">
          {tabs.map((tab) => (
            <button
              key={tab.value}
              type="button"
              role="tab"
              aria-selected={view === tab.value}
              className={`dlh-tab${view === tab.value ? ' dlh-tab-on' : ''}`}
              data-filter={tab.dataFilter}
              title={tab.title}
              onClick={() => onView(tab.value)}
            >
              {tab.label}
              {/* how much is waiting, so a full queue doesn't look like an empty one */}
              {tab.badge ? <span className="dlh-badge">{tab.badge}</span> : null}
            </button>
          ))}
        </div>
        {chips ? (
          <div className="dlh-chips" id="adl-filter-pills">
            {chips.map((chip) => (
              <button
                key={chip.value}
                type="button"
                className={`dlh-chip${filter === chip.value ? ' dlh-chip-on' : ''}${chip.count ? '' : ' dlh-chip-zero'}${chip.bad && chip.count ? ' dlh-chip-bad' : ''}`}
                data-filter={chip.value}
                aria-pressed={filter === chip.value}
                onClick={() => onFilter(chip.value)}
              >
                {chip.label}
                {chip.count ? <b>{chip.count}</b> : null}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {chips ? (
        live ? (
          <LiveStrip live={live} />
        ) : quiet ? (
          <div className="dlh-quiet" id="adl-count">
            <span className="dlh-quiet-dot" aria-hidden="true" />
            {quiet}
          </div>
        ) : null
      ) : null}
    </div>
  );
}

/** "Nothing downloading. 3 finished today, last at 9:41 PM." */
export function quietLine(finishedToday: number, lastAt: string): string {
  if (!finishedToday) return 'Nothing downloading.';
  return `Nothing downloading. ${finishedToday} finished today${lastAt ? `, last at ${lastAt}` : ''}.`;
}
