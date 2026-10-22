/**
 * The Clients tab — the external download clients, one sub-tab each.
 *
 * Boulder's call: isolate them like the review area's sub-views rather than
 * stacking three sections. All three poll every 10s regardless of which is
 * open, so every pill's health dot and count stay live; only the active
 * client's list renders.
 *
 * A failed fetch NEVER leaves "loading…" standing: the failure message is
 * kept and shown, because the first version swallowed errors into an
 * eternal spinner and the user had no way to see what was wrong.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { ClientAction, ClientFetch, ClientLinks } from '../-adl.api';
import type {
  ClientOverview,
  ClientSlskdItem,
  ClientTorrentItem,
  ClientUsenetItem,
} from '../-adl.types';

import {
  fetchClientLinks,
  fetchSlskdClient,
  fetchTorrentClient,
  fetchUsenetClient,
  slskdClearCompleted,
  slskdClientCancel,
  torrentClientAction,
  torrentClientAdd,
  torrentClientBulk,
  usenetClientAction,
  usenetClientAdd,
  usenetClientBulk,
} from '../-adl.api';
import { formatBytes } from '../-adl.helpers';
import { AdlBulkMatchModal } from './adl-bulk-match';
import { AdlMatchModal, type MatchTarget } from './adl-match-modal';
import {
  type DlFact,
  DlButton,
  DlFacts,
  DlGlyph,
  DlIcon,
  DlMore,
  DlRing,
  DlSegmented,
} from './dl-parts';

const toast = (message: string, type: string) => window.showToast?.(message, type);

export const CLIENTS_POLL_MS = 10_000;

export type ClientSubTab = 'soulseek' | 'torrent' | 'usenet';

const CLIENT_TYPE_LABELS: Record<string, string> = {
  qbittorrent: 'qBittorrent',
  transmission: 'Transmission',
  deluge: 'Deluge',
  aria2: 'aria2',
  sabnzbd: 'SABnzbd',
  nzbget: 'NZBGet',
};

interface ClientState<T> {
  overview: ClientOverview<T> | null;
  /** the last fetch failure, shown when there is nothing better to show. */
  fetchError: string | null;
  loaded: boolean;
}

function useClientPoll<T>(fetcher: () => Promise<ClientFetch<T>>) {
  const [state, setState] = useState<ClientState<T>>({
    overview: null,
    fetchError: null,
    loaded: false,
  });
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const reload = useCallback(async () => {
    const next = await fetcherRef.current();
    setState((prev) =>
      next.ok
        ? { overview: next.overview, fetchError: null, loaded: true }
        : // keep the last good overview on a blip; only the error text updates
          { overview: prev.overview, fetchError: next.message, loaded: true },
    );
  }, []);

  useEffect(() => {
    void reload();
    const timer = setInterval(() => void reload(), CLIENTS_POLL_MS);
    return () => clearInterval(timer);
  }, [reload]);

  return { ...state, reload };
}

/* ── little pieces ───────────────────────────────────────────────────────── */

type Health = 'wait' | 'ok' | 'bad' | 'off';

function healthOf(s: ClientState<unknown>): Health {
  if (!s.loaded) return 'wait';
  if (s.overview === null) return 'bad'; // never fetched successfully
  if (!s.overview.configured) return 'off';
  return s.overview.connected ? 'ok' : 'bad';
}

const HEALTH_TEXT: Record<Health, string> = {
  wait: 'checking…',
  ok: 'connected',
  bad: 'unreachable',
  off: 'not configured',
};

function speedText(bytesPerSec: number): string {
  if (!bytesPerSec) return '';
  return `${formatBytes(bytesPerSec)}/s`;
}

function pct(progress: number): number {
  // torrent/usenet report 0-1, slskd reports 0-100
  const value = progress > 1 ? progress : progress * 100;
  return Math.max(0, Math.min(100, value));
}

/** qBittorrent answers 8640000 (100 days) when it has no estimate. Anything
 * that long is "unknown", never a real time left. */
const NO_ESTIMATE_SECONDS = 8_640_000;

/** A client's eta, or null when it is the no-estimate sentinel. */
function etaSeconds(seconds: number | null | undefined): number | null {
  if (!seconds || seconds <= 0 || seconds >= NO_ESTIMATE_SECONDS) return null;
  return seconds;
}

function etaText(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0 || seconds >= NO_ESTIMATE_SECONDS) return '';
  if (seconds < 60) return `${Math.round(seconds)}s left`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m left`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m left`;
}

/** normalize a client's state string into one of the css-known buckets. */
function stateBucket(state: string): string {
  const lower = state.toLowerCase();
  if (lower.includes('progress') || lower.includes('download')) return 'downloading';
  if (lower.includes('queue')) return 'queued';
  if (lower.includes('seed')) return 'seeding';
  if (lower.includes('complete') || lower.includes('succeed')) return 'completed';
  if (lower.includes('pause')) return 'paused';
  if (lower.includes('stall')) return 'stalled';
  if (lower.includes('error') || lower.includes('fail')) return 'error';
  return 'other';
}

const STATE_WORDS: Record<string, string> = {
  downloading: 'Downloading',
  queued: 'Queued',
  seeding: 'Seeding',
  completed: 'Complete',
  paused: 'Paused',
  stalled: 'Stalled',
  error: 'Error',
};

/** The state in plain words. A download with nothing moving and nothing done
 * is waiting on peers, which "downloading" would hide. */
function stateWords(state: string, progress: number, speed: number): string {
  const bucket = stateBucket(state);
  if (bucket === 'downloading' && !speed && pct(progress) === 0) return 'Waiting for peers';
  return STATE_WORDS[bucket] ?? state;
}

const KIND_LABELS: Record<string, string> = {
  movie: 'Movie',
  show: 'TV',
  episode: 'Episode',
  season: 'Season',
  video: 'Video',
  audiobook: 'Audiobook',
  album: 'Album',
  track: 'Track',
};

function KindIcon({ kind }: { kind?: string }) {
  const common = {
    width: 22,
    height: 22,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.7,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  };
  if (kind === 'movie')
    return (
      <svg {...common}>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" />
      </svg>
    );
  if (kind === 'show' || kind === 'episode' || kind === 'season' || kind === 'video')
    return (
      <svg {...common}>
        <rect x="3" y="5" width="18" height="12" rx="2" />
        <path d="M8 21h8M12 17v4" />
      </svg>
    );
  if (kind === 'audiobook')
    return (
      <svg {...common}>
        <path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" />
        <path d="M4 21V5M9 7h6" />
      </svg>
    );
  if (kind === 'album' || kind === 'track')
    return (
      <svg {...common}>
        <path d="M9 18V5l12-2v13" />
        <circle cx="6" cy="18" r="3" />
        <circle cx="18" cy="16" r="3" />
      </svg>
    );
  return (
    <svg {...common}>
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <path d="M14 3v6h6" />
    </svg>
  );
}

/** what SoulSync knows this download as. nothing for one it didn't send. */
function SoulsyncChip({ item }: { item: { soulsync?: { kind?: string; title?: string } } }) {
  if (!item.soulsync) return null;
  const kind = item.soulsync.kind ? KIND_LABELS[item.soulsync.kind] : '';
  return (
    <span className="dlc-tag dlc-tag-hq dlc-owner" title="SoulSync is following this download">
      {kind ? `SoulSync · ${kind}` : 'SoulSync'}
    </span>
  );
}

/* ── the list view: search, state filter, sort ───────────────────────────── */

export type ClientSort = 'default' | 'speed' | 'progress' | 'name' | 'size';

/** Whose downloads to show: everything, the ones SoulSync follows, or the rest. */
export type OwnerFilter = 'all' | 'soulsync' | 'external';

export function byOwner<T extends { soulsync?: unknown }>(items: T[], owner: OwnerFilter): T[] {
  if (owner === 'all') return items;
  return items.filter((item) => Boolean(item.soulsync) === (owner === 'soulsync'));
}

/** the client's own category. '' is the torrents that have none. */
export const ALL_CATEGORIES = '*';

export function byCategory<T extends { category?: string | null }>(
  items: T[],
  category: string,
): T[] {
  if (category === ALL_CATEGORIES) return items;
  return items.filter((item) => (item.category || '') === category);
}

/** every category the client reported, with counts, most used first, the uncategorized last. */
export function categoryCounts<T extends { category?: string | null }>(
  items: T[],
): [string, number][] {
  const counts = new Map<string, number>();
  for (const item of items)
    counts.set(item.category || '', (counts.get(item.category || '') ?? 0) + 1);
  return [...counts.entries()].sort(
    (a, b) => Number(!a[0]) - Number(!b[0]) || b[1] - a[1] || a[0].localeCompare(b[0]),
  );
}

const OWNER_CHOICES: { key: OwnerFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'soulsync', label: "SoulSync's" },
  { key: 'external', label: 'Not in SoulSync' },
];

interface ViewAccessors<T> {
  name: (item: T) => string;
  speed: (item: T) => number;
  size: (item: T) => number;
  progress: (item: T) => number;
  state: (item: T) => string;
  /** extra searchable text (uploader name etc). */
  haystack?: (item: T) => string;
}

export function applyView<T>(
  items: T[],
  search: string,
  stateFilter: string,
  sort: ClientSort,
  acc: ViewAccessors<T>,
): T[] {
  let out = items;
  const needle = search.trim().toLowerCase();
  if (needle) {
    out = out.filter((item) =>
      (acc.name(item) + ' ' + (acc.haystack?.(item) ?? '')).toLowerCase().includes(needle),
    );
  }
  if (stateFilter !== 'all') {
    out = out.filter((item) => stateBucket(acc.state(item)) === stateFilter);
  }
  if (sort !== 'default') {
    out = [...out].sort((a, b) => {
      if (sort === 'name') return acc.name(a).localeCompare(acc.name(b));
      if (sort === 'speed') return acc.speed(b) - acc.speed(a);
      if (sort === 'size') return acc.size(b) - acc.size(a);
      return acc.progress(b) - acc.progress(a);
    });
  }
  return out;
}

const STATE_CHIP_ORDER = [
  'downloading',
  'queued',
  'seeding',
  'paused',
  'stalled',
  'completed',
  'error',
  'other',
];

/**
 * A row of count chips, one of which is on: the owner switch and the state
 * filter use the same look as the Downloads filters. single-choice, so it's a
 * radio group to a screen reader.
 */
function CountChips<V extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: V; label: string; count: number; bad?: boolean }[];
  value: V;
  onChange: (value: V) => void;
}) {
  return (
    <div className="dlh-chips dlc-chips-inline" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          className={`dlh-chip${value === o.value ? ' dlh-chip-on' : ''}${o.count ? '' : ' dlh-chip-zero'}${o.bad && o.count ? ' dlh-chip-bad' : ''}`}
          data-chip={o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
          {o.count ? <b>{o.count}</b> : null}
        </button>
      ))}
    </div>
  );
}

const STATE_CHIP_LABELS: Record<string, string> = {
  downloading: 'Downloading',
  queued: 'Queued',
  seeding: 'Seeding',
  paused: 'Paused',
  stalled: 'Stalled',
  completed: 'Complete',
  error: 'Errors',
  other: 'Other',
};

function ClientToolbar({
  lead,
  owner,
  onOwner,
  ownerCounts,
  search,
  onSearch,
  stateFilter,
  onStateFilter,
  sort,
  onSort,
  stateOf,
  categories,
  category,
  onCategory,
  shown,
}: {
  /** how many rows the filters leave on screen. */
  shown: number;
  /** the slskd downloads/uploads switch, when there is one. */
  lead?: React.ReactNode;
  owner: OwnerFilter;
  onOwner: (value: OwnerFilter) => void;
  ownerCounts: Record<OwnerFilter, number>;
  search: string;
  onSearch: (value: string) => void;
  stateFilter: string;
  onStateFilter: (value: string) => void;
  sort: ClientSort;
  onSort: (value: ClientSort) => void;
  stateOf: Map<string, number>;
  /** the client's categories; the picker shows only when a torrent has one. */
  categories?: [string, number][];
  category?: string;
  onCategory?: (value: string) => void;
}) {
  const showCategories = Boolean(categories?.some(([name]) => name) && onCategory);
  const total = [...stateOf.values()].reduce((a, b) => a + b, 0);
  return (
    <div className="dlc-toolbar">
      {lead}
      <CountChips
        label="Whose downloads"
        value={owner}
        onChange={onOwner}
        options={OWNER_CHOICES.map((c) => ({
          value: c.key,
          label: c.label,
          count: ownerCounts[c.key],
        }))}
      />
      <span className="dlc-vsep" aria-hidden="true" />
      <CountChips
        label="State"
        value={stateFilter}
        // a second click on the active state goes back to everything
        onChange={(v) => onStateFilter(v === stateFilter && v !== 'all' ? 'all' : v)}
        options={[
          { value: 'all', label: 'Any state', count: total },
          ...STATE_CHIP_ORDER.filter((bucket) => stateOf.get(bucket)).map((bucket) => ({
            value: bucket,
            label: STATE_CHIP_LABELS[bucket] ?? bucket,
            count: stateOf.get(bucket) ?? 0,
            bad: bucket === 'error' || bucket === 'stalled',
          })),
        ]}
      />
      <label className="dlc-search">
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          aria-hidden="true"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
        <input
          type="text"
          className="dlc-search-input"
          placeholder="Filter by name"
          aria-label="Filter by name"
          value={search}
          onChange={(event) => onSearch(event.target.value)}
        />
      </label>
      {showCategories ? (
        <select
          className="dlc-select"
          title="The download client's own category"
          aria-label="Category"
          value={category}
          onChange={(event) => onCategory?.(event.target.value)}
        >
          <option value={ALL_CATEGORIES}>All categories</option>
          {categories?.map(([name, count]) => (
            <option key={name || '(none)'} value={name}>
              {name || 'No category'} ({count})
            </option>
          ))}
        </select>
      ) : null}
      <select
        className="dlc-select"
        aria-label="Sort"
        value={sort}
        onChange={(event) => onSort(event.target.value as ClientSort)}
      >
        <option value="default">Client order</option>
        <option value="speed">Fastest first</option>
        <option value="progress">Most complete</option>
        <option value="name">Name</option>
        <option value="size">Largest</option>
      </select>
      <span className="dlc-toolbar-count">{shown} shown</span>
    </div>
  );
}

function bucketCounts<T>(items: T[], stateOf: (item: T) => string): Map<string, number> {
  const counts = new Map<string, number>();
  for (const item of items) {
    const bucket = stateBucket(stateOf(item));
    counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
  }
  return counts;
}

/** Paste a magnet / .torrent / .nzb url and send it to the client. Opened from the Add button. */
function AddBox({
  placeholder,
  onAdd,
  onClose,
}: {
  placeholder: string;
  onAdd: (url: string) => Promise<boolean>;
  onClose: () => void;
}) {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = () => {
    if (!url.trim() || busy) return;
    setBusy(true);
    void onAdd(url.trim()).then((ok) => {
      setBusy(false);
      if (ok) {
        setUrl('');
        onClose();
      }
    });
  };
  return (
    <div className="dlc-addbox">
      <input
        type="text"
        className="dlc-addbox-input"
        placeholder={placeholder}
        aria-label={placeholder}
        value={url}
        autoFocus
        onChange={(event) => setUrl(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') submit();
          if (event.key === 'Escape') onClose();
        }}
      />
      <DlButton primary disabled={busy || !url.trim()} onClick={submit}>
        {busy ? 'Adding…' : 'Add'}
      </DlButton>
      <DlButton title="Close without adding" onClick={onClose}>
        Cancel
      </DlButton>
    </div>
  );
}

/** [label, value] pairs; empty values are dropped so the grid stays tight. */
type DetailPairs = [string, string | null | undefined][];

/** the "where" half of a client row's sheet; everything else is the transfer. */
const WHERE_KEYS = new Set([
  'Remote path',
  'Local file',
  'Save path',
  'Content path',
  'Staging path',
  'Category',
  'Hash',
  'Job id',
  'Transfer id',
  'SoulSync',
]);
const MONO_KEYS = new Set([
  'Remote path',
  'Local file',
  'Save path',
  'Content path',
  'Staging path',
  'Hash',
  'Job id',
  'Transfer id',
]);

function durationText(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '';
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

interface MenuAction {
  label: string;
  onSelect: () => void;
  danger?: boolean;
}

const WarnIcon = (
  <svg
    width="18"
    height="18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.2"
    strokeLinecap="round"
    aria-hidden="true"
  >
    <circle cx="12" cy="12" r="9" />
    <path d="M12 8v5M12 16.5h.01" />
  </svg>
);

function ClientRow({
  name,
  title,
  sub,
  kind,
  state,
  label,
  error,
  progress,
  detail,
  details,
  owner,
  primary,
  menu,
  expanded,
  onToggle,
  select,
}: {
  /** the name the client shows */
  name: string;
  /** a clean title, when SoulSync knows what this is */
  title?: string;
  sub?: string;
  kind?: string;
  state: string;
  /** the state in plain words */
  label: string;
  error?: string | null;
  progress: number;
  detail: string;
  /** everything the client knows, shown when the row is open. */
  details: DetailPairs;
  owner: React.ReactNode;
  /** the one action the row leads with */
  primary: React.ReactNode;
  /** everything else, behind the ⋯ button */
  menu: MenuAction[];
  expanded: boolean;
  onToggle: () => void;
  /** the bulk-match select box, on a download SoulSync doesn't follow */
  select?: { checked: boolean; onChange: () => void; label: string };
}) {
  const bucket = stateBucket(state);
  const shown = details.filter(([, value]) => value != null && String(value).trim() !== '');
  const heading = title || name;
  const percent = Math.round(pct(progress));
  const moving = bucket === 'downloading' && percent > 0 && percent < 100;
  const toFact = ([k, v]: [string, string | null | undefined]): DlFact => [
    k,
    String(v),
    MONO_KEYS.has(k) ? { mono: true, copy: String(v) } : undefined,
  ];
  const transfer = shown.filter(([k]) => !WHERE_KEYS.has(k)).map(toFact);
  const where = shown.filter(([k]) => WHERE_KEYS.has(k)).map(toFact);
  const subLine = sub || (heading !== name ? name : '');
  return (
    <>
      <div
        className={`dlc-row dlc-client-row${expanded ? ' dlc-row-open' : ''}${select?.checked ? ' dlc-row-sel' : ''}`}
        data-state={bucket}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <div className="dlc-lead-wrap">
          <DlGlyph
            tone={
              bucket === 'error'
                ? 'bad'
                : bucket === 'seeding' || bucket === 'completed'
                  ? 'ok'
                  : undefined
            }
          >
            {bucket === 'error' ? WarnIcon : <KindIcon kind={kind} />}
          </DlGlyph>
          {moving ? <DlRing pct={percent} /> : null}
          {select ? (
            <button
              type="button"
              role="checkbox"
              aria-checked={select.checked}
              aria-label={select.label}
              className={`dlc-check${select.checked ? ' dlc-check-on' : ''}`}
              onClick={(e) => {
                e.stopPropagation();
                select.onChange();
              }}
            >
              {select.checked ? DlIcon.check : null}
            </button>
          ) : null}
        </div>
        <div className="dlc-main">
          <div className="dlc-title" title={heading}>
            {heading}
            {owner}
          </div>
          {error ? (
            <div className="dlc-sub dlc-sub-bad">{error}</div>
          ) : subLine ? (
            <div className={`dlc-sub${subLine === name ? ' dlc-mono' : ''}`} title={subLine}>
              {subLine}
            </div>
          ) : null}
        </div>
        <div className="dlc-meta">
          <span className={`dlc-state dlc-cstate-${bucket}`}>
            {bucket === 'downloading' && percent > 0 ? `${percent}%` : label}
          </span>
          {detail ? <span>{detail}</span> : null}
        </div>
        <div className="dlc-acts" onClick={(e) => e.stopPropagation()}>
          {primary}
          <DlMore
            items={menu.map((m, i) => ({
              key: `${i}`,
              label: m.label,
              danger: m.danger,
              onSelect: m.onSelect,
            }))}
            label={`More actions for ${heading}`}
          />
        </div>
        {moving || bucket === 'seeding' ? (
          <div className="dlc-rowbar" aria-hidden="true">
            <i
              className={bucket === 'seeding' ? 'dlc-rowbar-seed' : undefined}
              style={{ width: `${percent}%` }}
            />
          </div>
        ) : null}
      </div>
      {expanded ? (
        <div className="dlc-sheet dlc-client-sheet" onClick={(e) => e.stopPropagation()}>
          {error ? <div className="dlc-sheet-error">{error}</div> : null}
          <div className="dlc-sheet-cols">
            <section>
              <h4 className="dlc-sheet-h">Transfer</h4>
              <DlFacts facts={transfer} />
            </section>
            <section>
              <h4 className="dlc-sheet-h">Where</h4>
              <DlFacts facts={where} />
            </section>
          </div>
        </div>
      ) : null}
    </>
  );
}

const CLIENT_NAMES: Record<ClientSubTab, string> = {
  soulseek: 'Soulseek',
  torrent: 'torrent client',
  usenet: 'usenet client',
};
const CLIENT_PITCH: Record<ClientSubTab, string> = {
  soulseek:
    'Connect slskd and its transfers show up here: what SoulSync is pulling, what other people are pulling from you.',
  torrent:
    'Connect qBittorrent, Transmission, Deluge or aria2 and its torrents show up here, next to Soulseek. SoulSync can then grab releases from your indexers too.',
  usenet:
    'Connect SABnzbd or NZBGet and its queue shows up here, next to Soulseek and your torrent client. SoulSync can then grab from your indexers too.',
};

const PlugIcon = (
  <svg
    width="22"
    height="22"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    aria-hidden="true"
  >
    <path d="M9 2v6M15 2v6M7 8h10v4a5 5 0 0 1-10 0zM12 17v5" />
  </svg>
);

function EmptyState({
  tab,
  health,
  fetchError,
  overview,
  noun,
  onSettings,
}: {
  tab: ClientSubTab;
  health: Health;
  fetchError: string | null;
  overview: ClientOverview<unknown> | null;
  noun: string;
  onSettings: () => void;
}) {
  if (health === 'wait') return <div className="dlc-loading">Checking the client…</div>;
  if (overview === null) {
    // every fetch so far failed. say WHY instead of spinning forever
    return (
      <div className="dlc-emptycard">
        <DlGlyph tone="bad">{WarnIcon}</DlGlyph>
        <div>
          <h3>Couldn&apos;t ask the SoulSync server</h3>
          <p>{fetchError || 'The request failed.'} It tries again every 10 seconds.</p>
        </div>
      </div>
    );
  }
  if (!overview.configured) {
    return (
      <div className="dlc-emptycard">
        <DlGlyph>{PlugIcon}</DlGlyph>
        <div>
          <h3>No {CLIENT_NAMES[tab]} connected</h3>
          <p>{CLIENT_PITCH[tab]}</p>
          <DlButton primary onClick={onSettings}>
            Set it up in Settings
          </DlButton>
        </div>
      </div>
    );
  }
  if (!overview.connected) {
    return (
      <div className="dlc-emptycard">
        <DlGlyph tone="bad">{WarnIcon}</DlGlyph>
        <div>
          <h3>Can&apos;t reach the {CLIENT_NAMES[tab]}</h3>
          <p>
            {overview.error || 'It is set up, but it is not answering.'} SoulSync tries again every
            10 seconds.
          </p>
          <DlButton onClick={onSettings}>Check the connection</DlButton>
        </div>
      </div>
    );
  }
  return <div className="dlc-empty-line">No {noun} right now. All quiet.</div>;
}

/** Pause or resume, then remove (which asks about the files). */
function clientActions(
  item: { id: string; name: string; state: string },
  onAction: (id: string, action: ClientAction, deleteFiles: boolean) => void,
): MenuAction[] {
  // an errored download restarts the same way a paused one does
  const paused = ['paused', 'error'].includes(stateBucket(item.state));
  return [
    {
      label: paused ? 'Resume' : 'Pause',
      onSelect: () => onAction(item.id, paused ? 'resume' : 'pause', false),
    },
    {
      label: 'Remove…',
      danger: true,
      onSelect: () => {
        void (async () => {
          const withFiles = await window.showConfirmDialog?.({
            title: 'Remove Download',
            message: `Remove "${item.name}" from the client? Choose whether the downloaded files are deleted too.`,
            confirmText: 'Remove + delete files',
            cancelText: 'Remove only',
            destructive: true,
          });
          // ESC closes the dialog and resolves undefined - do nothing then.
          if (withFiles === undefined) return;
          onAction(item.id, 'remove', Boolean(withFiles));
        })();
      },
    },
  ];
}

/* ── the tab ─────────────────────────────────────────────────────────────── */

export function AdlClientsTab() {
  const slskd = useClientPoll<ClientSlskdItem>(fetchSlskdClient);
  const torrent = useClientPoll<ClientTorrentItem>(fetchTorrentClient);
  const usenet = useClientPoll<ClientUsenetItem>(fetchUsenetClient);
  const [tab, setTab] = useState<ClientSubTab>('soulseek');
  // expanded cards, keyed tab:id so the 10s poll re-render keeps them open
  const [openCards, setOpenCards] = useState<ReadonlySet<string>>(new Set());
  const [search, setSearch] = useState('');
  const [stateFilter, setStateFilter] = useState('all');
  const [sort, setSort] = useState<ClientSort>('default');
  // kept across client tabs: "show me what SoulSync isn't following" is a
  // question about every client at once
  const [owner, setOwner] = useState<OwnerFilter>('all');
  const [category, setCategory] = useState(ALL_CATEGORIES);
  const [slskdView, setSlskdView] = useState<'downloads' | 'uploads'>('downloads');
  const [links, setLinks] = useState<ClientLinks | null>(null);
  // the download being matched by hand, or null when the window is closed
  const [matching, setMatching] = useState<MatchTarget | null>(null);
  // bulk match & import: the checked downloads, by `${client}:${id}`
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [bulkTargets, setBulkTargets] = useState<MatchTarget[] | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  useEffect(() => {
    let live = true;
    void fetchClientLinks().then((next) => {
      if (live && next) setLinks(next);
    });
    return () => {
      live = false;
    };
  }, []);

  const toggleCard = useCallback((key: string) => {
    setOpenCards((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const toggleSelected = useCallback((key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  // a selection belongs to the client it was made in
  useEffect(() => {
    setSelected(new Set());
  }, [tab]);

  const switchTab = useCallback((next: ClientSubTab) => {
    setTab(next);
    setAddOpen(false);
    setSearch('');
    setStateFilter('all');
  }, []);

  const runAction = useCallback(
    async (
      label: string,
      call: () => Promise<{ success?: boolean; error?: string; done?: number }>,
      reload: () => Promise<void>,
    ) => {
      try {
        const data = await call();
        if (data.success) {
          toast(data.done !== undefined ? `${label}: ${data.done} ok` : `${label} ok`, 'success');
        } else toast(data.error || `${label} failed`, 'error');
      } catch {
        toast(`${label} failed`, 'error');
      }
      void reload();
    },
    [],
  );

  const pills: {
    key: ClientSubTab;
    label: string;
    state: ClientState<unknown>;
  }[] = [
    { key: 'soulseek', label: 'Soulseek', state: slskd },
    { key: 'torrent', label: 'Torrents', state: torrent },
    { key: 'usenet', label: 'Usenet', state: usenet },
  ];

  const activeState = tab === 'soulseek' ? slskd : tab === 'torrent' ? torrent : usenet;
  const activeHealth = healthOf(activeState);
  const typeLabel =
    tab === 'soulseek'
      ? 'slskd'
      : activeState.overview?.type
        ? (CLIENT_TYPE_LABELS[activeState.overview.type] ?? activeState.overview.type)
        : '';
  const activeLink = links ? links[tab === 'soulseek' ? 'slskd' : tab] : '';

  /* filtered views per kind */
  const slskdSource =
    slskdView === 'uploads' ? (slskd.overview?.uploads ?? []) : (slskd.overview?.items ?? []);
  const slskdVisible = applyView(byOwner(slskdSource, owner), search, stateFilter, sort, {
    name: (t) => t.filename,
    speed: (t) => t.speed,
    size: (t) => t.size,
    progress: (t) => t.progress,
    state: (t) => t.state,
    haystack: (t) => t.username,
  });
  const torrentCategories = categoryCounts(torrent.overview?.items ?? []);
  // a category the client no longer reports (last one removed, client
  // switched) would otherwise leave the list empty with nothing to undo it
  const activeCategory =
    category === ALL_CATEGORIES || torrentCategories.some(([name]) => name === category)
      ? category
      : ALL_CATEGORIES;
  const torrentVisible = applyView(
    byOwner(byCategory(torrent.overview?.items ?? [], activeCategory), owner),
    search,
    stateFilter,
    sort,
    {
      name: (t) => t.name,
      speed: (t) => t.download_speed,
      size: (t) => t.size,
      progress: (t) => t.progress,
      state: (t) => t.state,
    },
  );
  const usenetVisible = applyView(
    byOwner(usenet.overview?.items ?? [], owner),
    search,
    stateFilter,
    sort,
    {
      name: (t) => t.name,
      speed: (t) => t.download_speed,
      size: (t) => t.size,
      progress: (t) => t.progress,
      state: (t) => t.state,
    },
  );

  /* bulk match & import: only what is on screen and not followed yet, so a
     filter change quietly drops anything it hides */
  const selectable: MatchTarget[] =
    tab === 'soulseek'
      ? []
      : (tab === 'torrent' ? torrentVisible : usenetVisible)
          .filter((item) => !item.soulsync)
          .map((item) => ({ client: tab, id: item.id, name: item.name, size: item.size }));
  const selectedTargets = selectable.filter((t) => selected.has(`${t.client}:${t.id}`));
  const showSelectionBar =
    selectable.length > 0 && (owner === 'external' || selectedTargets.length > 0);

  /** A download SoulSync didn't send leads with matching it. */
  const matchButton = (target: MatchTarget) => (
    <DlButton
      primary
      title="Match it to your library and import it"
      onClick={() => setMatching(target)}
    >
      Match &amp; import
    </DlButton>
  );

  /** slskd lists files, but a release is a folder: the target is every
   * transfer from this peer in this folder that SoulSync isn't following. */
  const soulseekFolder = (item: ClientSlskdItem): MatchTarget => {
    const dirOf = (path: string) => path.replace(/[\\/][^\\/]*$/, '');
    const dir = dirOf(item.filename);
    const files = (slskd.overview?.items ?? []).filter(
      (other) =>
        other.username === item.username && dirOf(other.filename) === dir && !other.soulsync,
    );
    return {
      client: 'soulseek',
      id: '',
      name: dir.split(/[\\/]/).pop() || item.filename,
      size: files.reduce((sum, file) => sum + (file.size || 0), 0),
      folder: {
        username: item.username,
        files: files.map((file) => ({ filename: file.filename, size: file.size })),
      },
    };
  };

  const slskdRow = (item: ClientSlskdItem, readOnly: boolean) => {
    const key = `soulseek:${slskdView}:${item.username}:${item.id}`;
    const expanded = openCards.has(key);
    const toggle = () => toggleCard(key);
    const matchable = !readOnly && !item.soulsync;
    const cancel: MenuAction = {
      label: 'Cancel transfer',
      danger: true,
      onSelect: () =>
        void runAction(
          'Cancel',
          () => slskdClientCancel(item.id, item.username, true),
          slskd.reload,
        ),
    };
    return (
      <ClientRow
        key={`${readOnly ? 'up' : 'dl'}:${item.username}:${item.id}`}
        name={item.filename.split(/[\\/]/).pop() || item.filename}
        sub={readOnly ? `to ${item.username}` : `from ${item.username}`}
        kind={item.soulsync?.kind}
        state={item.state}
        label={stateWords(item.state, item.progress, item.speed)}
        progress={item.progress}
        detail={[
          item.size ? formatBytes(item.size) : '',
          speedText(item.speed),
          etaText(item.time_remaining),
        ]
          .filter(Boolean)
          .join(' · ')}
        details={[
          ['Remote path', item.filename],
          [readOnly ? 'Peer' : 'Uploader', item.username],
          ['State', item.state],
          ['Size', item.size ? formatBytes(item.size) : ''],
          ['Transferred', item.transferred ? formatBytes(item.transferred) : ''],
          ['Speed', speedText(item.speed)],
          ['Time left', durationText(item.time_remaining)],
          ['Local file', item.file_path],
          ['Transfer id', item.id],
          ['SoulSync', item.soulsync?.title],
        ]}
        expanded={expanded}
        onToggle={toggle}
        owner={<SoulsyncChip item={item} />}
        primary={matchable ? matchButton(soulseekFolder(item)) : null}
        menu={readOnly ? [] : [cancel]}
      />
    );
  };

  /** The torrent or usenet card: the same card, different numbers. */
  const jobRow = (
    client: 'torrent' | 'usenet',
    item: ClientTorrentItem | ClientUsenetItem,
    detail: string,
    details: DetailPairs,
    onAction: (id: string, action: ClientAction, deleteFiles: boolean) => void,
  ) => {
    const key = `${client}:${item.id}`;
    const expanded = openCards.has(key);
    const toggle = () => toggleCard(key);
    const tracked = Boolean(item.soulsync);
    const actions = clientActions(item, onAction);
    return (
      <ClientRow
        key={item.id}
        name={item.name}
        title={item.soulsync?.title || undefined}
        kind={item.soulsync?.kind}
        state={item.state}
        label={stateWords(item.state, item.progress, item.download_speed)}
        error={item.error}
        progress={item.progress}
        detail={detail}
        details={details}
        expanded={expanded}
        onToggle={toggle}
        owner={<SoulsyncChip item={item} />}
        // a followed download leads with pause/resume; one SoulSync didn't
        // send leads with matching it
        primary={
          tracked ? (
            <DlButton onClick={actions[0].onSelect}>{actions[0].label}</DlButton>
          ) : (
            matchButton({ client, id: item.id, name: item.name, size: item.size })
          )
        }
        select={
          tracked
            ? undefined
            : {
                checked: selected.has(key),
                onChange: () => toggleSelected(key),
                label: `Select ${item.name}`,
              }
        }
        menu={tracked ? actions.slice(1) : actions}
      />
    );
  };

  const torrentRow = (item: ClientTorrentItem) => {
    const bucket = stateBucket(item.state);
    return jobRow(
      'torrent',
      item,
      [
        item.size ? formatBytes(item.size) : '',
        bucket === 'seeding' && item.ratio != null ? `ratio ${item.ratio.toFixed(2)}` : '',
        speedText(item.download_speed),
        bucket === 'downloading' ? etaText(item.eta) : '',
        item.seeders ? `${item.seeders} seeders` : '',
      ]
        .filter(Boolean)
        .join(' · '),
      [
        ['State', item.state],
        ['Size', item.size ? formatBytes(item.size) : ''],
        ['Downloaded', item.downloaded ? formatBytes(item.downloaded) : ''],
        ['Down speed', speedText(item.download_speed)],
        ['Up speed', speedText(item.upload_speed)],
        ['ETA', bucket === 'downloading' ? durationText(etaSeconds(item.eta)) : ''],
        ['Seeders', item.seeders ? String(item.seeders) : ''],
        ['Peers', item.peers ? String(item.peers) : ''],
        ['Ratio', item.ratio != null ? item.ratio.toFixed(2) : ''],
        ['Seeding time', durationText(item.seeding_time)],
        ['Save path', item.save_path],
        ['Content path', item.content_path],
        ['Hash', item.id],
        ['SoulSync', item.soulsync?.title],
      ],
      (id, action, deleteFiles) =>
        void runAction(
          action === 'remove' ? 'Remove' : action === 'pause' ? 'Pause' : 'Resume',
          () => torrentClientAction(id, action, deleteFiles),
          torrent.reload,
        ),
    );
  };

  const usenetRow = (item: ClientUsenetItem) =>
    jobRow(
      'usenet',
      item,
      [item.size ? formatBytes(item.size) : '', speedText(item.download_speed), etaText(item.eta)]
        .filter(Boolean)
        .join(' · '),
      [
        ['State', item.state],
        ['Size', item.size ? formatBytes(item.size) : ''],
        ['Downloaded', item.downloaded ? formatBytes(item.downloaded) : ''],
        ['Speed', speedText(item.download_speed)],
        ['ETA', durationText(etaSeconds(item.eta))],
        ['Category', item.category],
        ['Save path', item.save_path],
        ['Staging path', item.incomplete_path],
        ['Job id', item.id],
        ['SoulSync', item.soulsync?.title],
      ],
      (id, action, deleteFiles) =>
        void runAction(
          action === 'remove' ? 'Remove' : action === 'pause' ? 'Pause' : 'Resume',
          () => usenetClientAction(id, action, deleteFiles),
          usenet.reload,
        ),
    );

  const connectedWithItems = (state: ClientState<unknown>) => Boolean(state.overview?.connected);

  const activeVisible =
    tab === 'soulseek' ? slskdVisible : tab === 'torrent' ? torrentVisible : usenetVisible;
  const activeAll =
    tab === 'soulseek'
      ? slskdSource
      : tab === 'torrent'
        ? byCategory(torrent.overview?.items ?? [], activeCategory)
        : (usenet.overview?.items ?? []);
  const stateCounts = bucketCounts(activeAll as { state: string }[], (item) => item.state);
  const owned = (activeAll as { soulsync?: unknown }[]).filter((item) => item.soulsync).length;
  const ownerCounts: Record<OwnerFilter, number> = {
    all: activeAll.length,
    soulsync: owned,
    external: activeAll.length - owned,
  };
  const downSpeed = activeVisible.reduce(
    (sum, item) =>
      sum +
      ((item as { speed?: number; download_speed?: number }).speed ??
        (item as { download_speed?: number }).download_speed ??
        0),
    0,
  );
  const upSpeed =
    tab === 'torrent'
      ? torrentVisible.reduce((sum, item) => sum + (item.upload_speed || 0), 0)
      : undefined;

  const bulk = (action: ClientAction) => {
    const call =
      tab === 'torrent'
        ? () =>
            torrentClientBulk(
              torrentVisible.map((i) => i.id),
              action,
            )
        : () =>
            usenetClientBulk(
              usenetVisible.map((i) => i.id),
              action,
            );
    const reload = tab === 'torrent' ? torrent.reload : usenet.reload;
    void runAction(action === 'pause' ? 'Pause all' : 'Resume all', call, reload);
  };

  const trimmedNote =
    tab === 'soulseek' && slskd.overview?.counts
      ? slskdView === 'uploads'
        ? (slskd.overview.counts.uploads_completed ?? 0) > 25
          ? `${slskd.overview.counts.uploads_completed} completed trimmed - showing the active ones`
          : ''
        : (slskd.overview.counts.downloads_completed ?? 0) > 100
          ? `${slskd.overview.counts.downloads_completed} completed trimmed - showing the newest`
          : ''
      : '';

  const typeName = typeLabel || (tab === 'soulseek' ? 'slskd' : '');
  const buckets = stateCounts;
  const n = (bucket: string) => buckets.get(bucket) ?? 0;
  const ratios =
    tab === 'torrent'
      ? torrentVisible.map((t) => t.ratio).filter((r): r is number => r != null)
      : [];
  const avgRatio = ratios.length ? ratios.reduce((x, y) => x + y, 0) / ratios.length : null;
  const barTotal = Math.max(1, activeAll.length);
  const barPct = (count: number) => `${(count / barTotal) * 100}%`;
  const summaryBits = [
    n('downloading') ? `${n('downloading')} downloading` : '',
    n('queued') ? `${n('queued')} queued` : '',
    n('seeding') ? `${n('seeding')} seeding` : '',
    n('paused') ? `${n('paused')} paused` : '',
    n('stalled') ? `${n('stalled')} stalled` : '',
    n('error') ? `${n('error')} with errors` : '',
  ].filter(Boolean);

  const menu = [
    ...(tab === 'soulseek' && connectedWithItems(slskd)
      ? [
          {
            key: 'clear',
            label: 'Clear finished transfers',
            title: 'Tell slskd to drop every finished transfer from its list',
            onSelect: () =>
              void runAction('Clear completed', () => slskdClearCompleted(), slskd.reload),
          },
        ]
      : []),
    ...(tab !== 'soulseek' && activeVisible.length > 0
      ? [
          {
            key: 'pause',
            label: `Pause all shown (${activeVisible.length})`,
            onSelect: () => bulk('pause'),
          },
          {
            key: 'resume',
            label: `Resume all shown (${activeVisible.length})`,
            onSelect: () => bulk('resume'),
          },
        ]
      : []),
    { key: 'refresh', label: 'Refresh now', onSelect: () => void activeState.reload() },
  ];

  const addTorrent = async (url: string) => {
    try {
      const data = await torrentClientAdd(url);
      if (data.success) {
        toast('Sent to the torrent client', 'success');
        void torrent.reload();
        return true;
      }
      toast(data.error || 'Add failed', 'error');
    } catch {
      toast('Add failed', 'error');
    }
    return false;
  };
  const addNzb = async (url: string) => {
    try {
      const data = await usenetClientAdd(url);
      if (data.success) {
        toast('Sent to the usenet client', 'success');
        void usenet.reload();
        return true;
      }
      toast(data.error || 'Add failed', 'error');
    } catch {
      toast('Add failed', 'error');
    }
    return false;
  };
  const canAdd = tab !== 'soulseek' && activeHealth === 'ok';

  return (
    <div className="dlc-stack" id="adl-clients">
      <div className="dlc-subbar">
        <DlSegmented
          label="Download clients"
          value={tab}
          onChange={(v) => switchTab(v as ClientSubTab)}
          segments={pills.map((pill) => {
            const health = healthOf(pill.state);
            return {
              value: pill.key,
              label: pill.label,
              title: HEALTH_TEXT[health],
              dot: health === 'ok' ? 'ok' : health === 'bad' ? 'bad' : 'off',
              count: health === 'ok' ? (pill.state.overview?.items.length ?? null) : null,
            };
          })}
        />
        <div className="dlc-subbar-side">
          {canAdd ? (
            <DlButton primary icon={DlIcon.plus} onClick={() => setAddOpen((v) => !v)}>
              {tab === 'torrent' ? 'Add torrent' : 'Add nzb'}
            </DlButton>
          ) : null}
          {activeLink && activeHealth === 'ok' ? (
            <a
              className="dlc-btn"
              href={activeLink}
              target="_blank"
              rel="noreferrer"
              title="Open the client's own page in a new tab"
            >
              {typeName || 'Open client'}
              {DlIcon.open}
            </a>
          ) : null}
          <span className="dlh-more">
            <DlMore items={menu} label="Client actions" />
          </span>
        </div>
      </div>

      {addOpen && canAdd ? (
        <AddBox
          placeholder={
            tab === 'torrent' ? 'Paste a magnet link or .torrent url' : 'Paste an .nzb url'
          }
          onAdd={tab === 'torrent' ? addTorrent : addNzb}
          onClose={() => setAddOpen(false)}
        />
      ) : null}

      {activeHealth === 'ok' ? (
        <div className={`dlc-strip${n('downloading') ? ' dlc-strip-accent' : ''}`}>
          <div className="dlc-strip-big">
            <strong>↓ {speedText(downSpeed) || '0 B/s'}</strong>
            <span>
              {upSpeed !== undefined ? `↑ ${speedText(upSpeed) || '0 B/s'} · ` : ''}
              {HEALTH_TEXT[activeHealth]}
            </span>
          </div>
          <div className="dlc-strip-line">
            {typeName ? <b>{typeName}</b> : null}
            {typeName && summaryBits.length ? ' · ' : ''}
            {summaryBits.join(', ') || 'Nothing in the list'}
            {avgRatio != null ? ` · average ratio ${avgRatio.toFixed(2)}` : ''}
            {activeAll.length ? (
              <>
                <div className="dlh-bar" aria-hidden="true">
                  <i className="dlh-bar-active" style={{ width: barPct(n('downloading')) }} />
                  <i
                    className="dlc-bar-seed"
                    style={{ width: barPct(n('seeding') + n('completed')) }}
                  />
                  <i
                    className="dlc-bar-idle"
                    style={{ width: barPct(n('paused') + n('queued') + n('other')) }}
                  />
                  <i
                    className="dlh-bar-fail"
                    style={{ width: barPct(n('error') + n('stalled')) }}
                  />
                </div>
              </>
            ) : null}
            {trimmedNote ? <div className="dlc-strip-note">{trimmedNote}</div> : null}
          </div>
          <span />
        </div>
      ) : null}

      {activeHealth === 'ok' ? (
        <ClientToolbar
          lead={
            tab === 'soulseek' ? (
              <DlSegmented
                label="Direction"
                value={slskdView}
                onChange={(v) => setSlskdView(v as 'downloads' | 'uploads')}
                segments={[
                  {
                    value: 'downloads',
                    label: 'Downloads',
                    count: slskd.overview?.items.length ?? 0,
                  },
                  {
                    value: 'uploads',
                    label: 'Uploads',
                    count: slskd.overview?.uploads?.length ?? 0,
                  },
                ]}
              />
            ) : undefined
          }
          owner={owner}
          onOwner={setOwner}
          ownerCounts={ownerCounts}
          search={search}
          onSearch={setSearch}
          stateFilter={stateFilter}
          onStateFilter={setStateFilter}
          sort={sort}
          onSort={setSort}
          stateOf={stateCounts}
          categories={tab === 'torrent' ? torrentCategories : undefined}
          category={activeCategory}
          onCategory={setCategory}
          shown={activeVisible.length}
        />
      ) : null}

      {activeHealth === 'ok' && activeVisible.length > 0 ? (
        <div className="dlc-list">
          {tab === 'soulseek'
            ? slskdVisible.map((item) => slskdRow(item, slskdView === 'uploads'))
            : tab === 'torrent'
              ? torrentVisible.map(torrentRow)
              : usenetVisible.map(usenetRow)}
        </div>
      ) : (
        <EmptyState
          tab={tab}
          health={activeHealth}
          fetchError={activeState.fetchError}
          overview={activeState.overview}
          noun={
            tab === 'soulseek'
              ? slskdView === 'uploads'
                ? 'uploads'
                : 'transfers'
              : tab === 'torrent'
                ? 'torrents'
                : 'jobs'
          }
          // settings is still a classic page: the shell navigates there
          onSettings={() => void window.navigateToPage?.('settings')}
        />
      )}

      {showSelectionBar ? (
        <div className="dlc-selbar" role="toolbar" aria-label="Selection">
          <span className="dlc-selbar-n">
            {selectedTargets.length
              ? `${selectedTargets.length} selected`
              : 'Select downloads to match'}
          </span>
          <DlButton
            onClick={() =>
              setSelected(
                selectedTargets.length === selectable.length
                  ? new Set()
                  : new Set(selectable.map((t) => `${t.client}:${t.id}`)),
              )
            }
          >
            {selectedTargets.length === selectable.length
              ? 'Clear'
              : `Select all shown (${selectable.length})`}
          </DlButton>
          <DlButton
            primary
            disabled={selectedTargets.length === 0}
            onClick={() => setBulkTargets(selectedTargets)}
          >
            Match &amp; import{selectedTargets.length ? ` ${selectedTargets.length}` : ''}
          </DlButton>
        </div>
      ) : null}

      <AdlBulkMatchModal
        targets={bulkTargets}
        onClose={() => setBulkTargets(null)}
        onFinished={(imported) => {
          if (imported) {
            setSelected(new Set());
            void (tab === 'usenet' ? usenet.reload() : torrent.reload());
          }
        }}
      />
      <AdlMatchModal
        target={matching}
        onClose={() => setMatching(null)}
        onMatched={(message) => {
          setMatching(null);
          toast(message, 'success');
          void (matching?.client === 'usenet'
            ? usenet.reload()
            : matching?.client === 'soulseek'
              ? slskd.reload()
              : torrent.reload());
        }}
      />
    </div>
  );
}
