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

import { Menu } from '@base-ui/react/menu';
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

function SoulsyncChip({ item }: { item: { soulsync?: { kind?: string; title?: string } } }) {
  if (!item.soulsync) {
    return (
      <span
        className="adl-client-owner adl-client-owner-external"
        title="SoulSync did not send this download"
      >
        Not in SoulSync
      </span>
    );
  }
  const kind = item.soulsync.kind ? KIND_LABELS[item.soulsync.kind] : '';
  return (
    <span className="adl-client-owner" title="SoulSync is following this download">
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
  { key: 'soulsync', label: 'SoulSync' },
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

function ClientToolbar({
  items,
  owner,
  onOwner,
  ownerCounts,
  totalSpeed,
  upSpeed,
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
  link,
  onRefresh,
}: {
  items: { length: number };
  owner: OwnerFilter;
  onOwner: (value: OwnerFilter) => void;
  ownerCounts: Record<OwnerFilter, number>;
  totalSpeed: number;
  upSpeed?: number;
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
  link: string;
  onRefresh: () => void;
}) {
  const showCategories = Boolean(categories?.some(([name]) => name) && onCategory);
  return (
    <div className="adl-client-toolbar">
      <div className="adl-client-owner-switch" role="radiogroup" aria-label="Whose downloads">
        {OWNER_CHOICES.map((choice) => (
          <button
            key={choice.key}
            type="button"
            role="radio"
            aria-checked={owner === choice.key}
            className={`adl-client-owner-choice${owner === choice.key ? ' active' : ''}`}
            onClick={() => onOwner(choice.key)}
          >
            {choice.label}
            <span className="adl-client-owner-count">{ownerCounts[choice.key]}</span>
          </button>
        ))}
      </div>
      <input
        type="text"
        className="adl-client-search"
        placeholder="Filter by name…"
        value={search}
        onChange={(event) => onSearch(event.target.value)}
      />
      <div className="adl-client-state-chips">
        <button
          type="button"
          className={`adl-client-chip${stateFilter === 'all' ? ' active' : ''}`}
          onClick={() => onStateFilter('all')}
        >
          all
        </button>
        {STATE_CHIP_ORDER.filter((bucket) => stateOf.get(bucket)).map((bucket) => (
          <button
            key={bucket}
            type="button"
            className={`adl-client-chip${stateFilter === bucket ? ' active' : ''}`}
            onClick={() => onStateFilter(stateFilter === bucket ? 'all' : bucket)}
          >
            {bucket} ({stateOf.get(bucket)})
          </button>
        ))}
      </div>
      {showCategories ? (
        <select
          className="adl-deleted-retention adl-client-category"
          title="The download client's category"
          aria-label="Category"
          value={category}
          onChange={(event) => onCategory?.(event.target.value)}
        >
          <option value={ALL_CATEGORIES}>all categories</option>
          {categories?.map(([name, count]) => (
            <option key={name || '(none)'} value={name}>
              {name || 'no category'} ({count})
            </option>
          ))}
        </select>
      ) : null}
      <select
        className="adl-deleted-retention adl-client-sort"
        title="Sort"
        value={sort}
        onChange={(event) => onSort(event.target.value as ClientSort)}
      >
        <option value="default">client order</option>
        <option value="speed">fastest first</option>
        <option value="progress">most complete</option>
        <option value="name">name</option>
        <option value="size">largest</option>
      </select>
      <span className="adl-client-aggregate">
        {items.length} shown · ↓ {formatBytes(totalSpeed) || '0 B'}/s
        {upSpeed !== undefined ? <> · ↑ {formatBytes(upSpeed) || '0 B'}/s</> : null}
      </span>
      <button type="button" className="verif-act" title="Refresh now" onClick={onRefresh}>
        ⟳
      </button>
      {link ? (
        <a
          className="verif-act adl-client-open-link"
          href={link}
          target="_blank"
          rel="noreferrer"
          title="Open the client's own web UI in a new tab"
        >
          ↗
        </a>
      ) : null}
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

function AddBox({
  label,
  placeholder,
  onAdd,
}: {
  /** The reveal button's text ("+ add torrent"). */
  label: string;
  placeholder: string;
  onAdd: (url: string) => Promise<boolean>;
}) {
  // Folded by default: a permanently open bare input right under the filter
  // input read as two lookalike text boxes stacked.
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = () => {
    if (!url.trim() || busy) return;
    setBusy(true);
    void onAdd(url.trim()).then((ok) => {
      setBusy(false);
      if (ok) {
        setUrl('');
        setOpen(false);
      }
    });
  };
  if (!open) {
    return (
      <button
        type="button"
        className="adl-filter-banner-clear adl-client-add-toggle"
        title={placeholder}
        onClick={() => setOpen(true)}
      >
        {label}
      </button>
    );
  }
  return (
    <div className="adl-client-addbox">
      <input
        type="text"
        className="adl-client-search adl-client-add-input"
        placeholder={placeholder}
        value={url}
        autoFocus
        onChange={(event) => setUrl(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') submit();
          if (event.key === 'Escape') setOpen(false);
        }}
      />
      <button
        type="button"
        className="adl-filter-banner-clear"
        disabled={busy || !url.trim()}
        onClick={submit}
      >
        {busy ? 'Adding…' : '+ Add'}
      </button>
      <button
        type="button"
        className="adl-filter-banner-clear"
        title="Close without adding"
        onClick={() => setOpen(false)}
      >
        ✕
      </button>
    </div>
  );
}

/** [label, value] pairs; empty values are dropped so the grid stays tight. */
type DetailPairs = [string, string | null | undefined][];

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
  /** everything the client knows, shown when the card is open. */
  details: DetailPairs;
  owner: React.ReactNode;
  /** the one action the card leads with */
  primary: React.ReactNode;
  /** everything else, behind the ⋯ button */
  menu: MenuAction[];
  expanded: boolean;
  onToggle: () => void;
  /** the bulk-match checkbox, on a download SoulSync doesn't follow */
  select?: React.ReactNode;
}) {
  const bucket = stateBucket(state);
  const shown = details.filter(([, value]) => value != null && String(value).trim() !== '');
  const heading = title || name;
  return (
    <div
      className={`adl-client-card${expanded ? ' expanded' : ''}`}
      data-state={bucket}
      title={expanded ? undefined : 'Click to show everything the client reports'}
      onClick={onToggle}
    >
      <div className="adl-client-card-main">
        {select ? (
          <span className="adl-client-select" onClick={(event) => event.stopPropagation()}>
            {select}
          </span>
        ) : null}
        <span className="adl-client-kind">
          <KindIcon kind={kind} />
        </span>
        <div className="adl-client-text">
          <div className="adl-client-title-line">
            <span className="adl-row-title adl-client-title" title={heading}>
              {heading}
            </span>
            {sub ? <span className="adl-client-sub">{sub}</span> : null}
          </div>
          <div className="adl-client-meta">
            {owner}
            {heading !== name ? (
              <span className="adl-client-raw" title={name}>
                {name}
              </span>
            ) : null}
          </div>
        </div>
        <div className="adl-client-status">
          <span className="adl-client-status-line">
            <span className="adl-client-status-dot" data-state={bucket} aria-hidden="true" />
            {label}
          </span>
          <span className="adl-client-status-detail">
            {Math.round(pct(progress))}%{detail ? ` · ${detail}` : ''}
          </span>
        </div>
        <div className="adl-client-actions" onClick={(event) => event.stopPropagation()}>
          {primary}
          {menu.length > 0 ? (
            <Menu.Root>
              <Menu.Trigger className="adl-client-more" aria-label={`More actions for ${heading}`}>
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  aria-hidden="true"
                >
                  <circle cx="5" cy="12" r="1.8" />
                  <circle cx="12" cy="12" r="1.8" />
                  <circle cx="19" cy="12" r="1.8" />
                </svg>
              </Menu.Trigger>
              <Menu.Portal>
                <Menu.Positioner className="adl-client-menu-positioner" align="end" sideOffset={6}>
                  <Menu.Popup className="adl-client-menu">
                    {menu.map((action) => (
                      <Menu.Item
                        key={action.label}
                        className={`adl-client-menu-item${action.danger ? ' danger' : ''}`}
                        onClick={action.onSelect}
                      >
                        {action.label}
                      </Menu.Item>
                    ))}
                  </Menu.Popup>
                </Menu.Positioner>
              </Menu.Portal>
            </Menu.Root>
          ) : null}
        </div>
      </div>
      {expanded ? (
        <div className="adl-client-details" onClick={(event) => event.stopPropagation()}>
          {error ? <div className="adl-client-details-error">{error}</div> : null}
          <dl>
            {shown.map(([detailLabel, value]) => (
              <div className="adl-client-detail" key={detailLabel}>
                <dt>{detailLabel}</dt>
                <dd title={String(value)}>{String(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      <div className="adl-client-progress" data-state={bucket}>
        <div className="adl-client-progress-fill" style={{ width: `${pct(progress)}%` }} />
      </div>
    </div>
  );
}

function EmptyState({
  health,
  fetchError,
  overview,
  noun,
}: {
  health: Health;
  fetchError: string | null;
  overview: ClientOverview<unknown> | null;
  noun: string;
}) {
  if (health === 'wait') return <div className="adl-client-empty">loading…</div>;
  if (overview === null) {
    // every fetch so far failed - say WHY instead of spinning forever
    return (
      <div className="adl-client-empty adl-client-empty-error">
        couldn't load this from the SoulSync server{fetchError ? ` — ${fetchError}` : ''}
      </div>
    );
  }
  if (!overview.configured) {
    return (
      <div className="adl-client-empty">
        nothing set up — configure a client in Settings and it shows up here
      </div>
    );
  }
  if (!overview.connected) {
    return (
      <div className="adl-client-empty adl-client-empty-error">
        {overview.error || 'could not reach the client'}
      </div>
    );
  }
  return <div className="adl-client-empty">no {noun} right now — all quiet</div>;
}

/** Pause or resume, then remove (which asks about the files). */
function clientActions(
  item: { id: string; name: string; state: string },
  onAction: (id: string, action: ClientAction, deleteFiles: boolean) => void,
): MenuAction[] {
  const paused = stateBucket(item.state) === 'paused';
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
    { key: 'soulseek', label: '🎧 Soulseek', state: slskd },
    { key: 'torrent', label: '🧲 Torrents', state: torrent },
    { key: 'usenet', label: '📰 Usenet', state: usenet },
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

  /** "Details" for a card SoulSync already follows; the card itself also opens. */
  const detailsButton = (expanded: boolean, toggle: () => void) => (
    <button type="button" className="adl-client-secondary" onClick={toggle}>
      {expanded ? 'Hide' : 'Details'}
    </button>
  );

  /** A download SoulSync didn't send leads with matching it. */
  const matchButton = (target: MatchTarget) => (
    <button type="button" className="adl-client-primary" onClick={() => setMatching(target)}>
      Match &amp; import
    </button>
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
        primary={matchable ? matchButton(soulseekFolder(item)) : detailsButton(expanded, toggle)}
        menu={
          readOnly
            ? []
            : matchable
              ? [{ label: expanded ? 'Hide details' : 'Details', onSelect: toggle }, cancel]
              : [cancel]
        }
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
        primary={
          tracked
            ? detailsButton(expanded, toggle)
            : matchButton({ client, id: item.id, name: item.name, size: item.size })
        }
        select={
          tracked ? undefined : (
            <input
              type="checkbox"
              aria-label={`Select ${item.name}`}
              checked={selected.has(key)}
              onChange={() => toggleSelected(key)}
            />
          )
        }
        // an unmatched card leads with matching, so details move to the menu
        menu={
          tracked
            ? actions
            : [{ label: expanded ? 'Hide details' : 'Details', onSelect: toggle }, ...actions]
        }
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

  return (
    <div className="adl-clients" id="adl-clients">
      <div className="adl-batch-filter-banner adl-clients-banner">
        {pills.map((pill) => {
          const health = healthOf(pill.state);
          const count = pill.state.overview?.items.length ?? null;
          return (
            <button
              key={pill.key}
              type="button"
              className={`adl-pill${tab === pill.key ? ' active' : ''}`}
              data-client-tab={pill.key}
              title={HEALTH_TEXT[health]}
              onClick={() => switchTab(pill.key)}
            >
              <span className={`adl-client-dot adl-client-dot-${health}`} />
              {pill.label}
              {count !== null && health === 'ok' ? ` (${count})` : ''}
            </button>
          );
        })}
        <span className="verif-banner-spacer" />
        {tab === 'soulseek' && connectedWithItems(slskd) ? (
          <button
            type="button"
            className="adl-filter-banner-clear"
            title="Tell slskd to drop every finished transfer from its list"
            onClick={() =>
              void runAction('Clear completed', () => slskdClearCompleted(), slskd.reload)
            }
          >
            🧹 Clear completed
          </button>
        ) : null}
        {tab !== 'soulseek' && activeVisible.length > 0 ? (
          <>
            <button
              type="button"
              className="adl-filter-banner-clear"
              title="Pause everything currently listed (respects the filter)"
              onClick={() => bulk('pause')}
            >
              ⏸ Pause all
            </button>
            <button
              type="button"
              className="adl-filter-banner-clear"
              title="Resume everything currently listed (respects the filter)"
              onClick={() => bulk('resume')}
            >
              ▶ Resume all
            </button>
          </>
        ) : null}
        {typeLabel ? <span className="adl-client-section-type">{typeLabel}</span> : null}
        <span className={`adl-client-health adl-client-health-${activeHealth}`}>
          {HEALTH_TEXT[activeHealth]}
        </span>
      </div>

      {tab === 'soulseek' && connectedWithItems(slskd) ? (
        <div className="adl-client-viewswitch">
          <button
            type="button"
            className={`adl-client-chip${slskdView === 'downloads' ? ' active' : ''}`}
            onClick={() => setSlskdView('downloads')}
          >
            ⬇ downloads ({slskd.overview?.items.length ?? 0})
          </button>
          <button
            type="button"
            className={`adl-client-chip${slskdView === 'uploads' ? ' active' : ''}`}
            onClick={() => setSlskdView('uploads')}
          >
            ⬆ uploads ({slskd.overview?.uploads?.length ?? 0})
          </button>
        </div>
      ) : null}

      {activeHealth === 'ok' ? (
        <ClientToolbar
          items={activeVisible}
          owner={owner}
          onOwner={setOwner}
          ownerCounts={ownerCounts}
          totalSpeed={downSpeed}
          upSpeed={upSpeed}
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
          link={activeLink}
          onRefresh={() => void activeState.reload()}
        />
      ) : null}

      {tab === 'torrent' && activeHealth === 'ok' ? (
        <AddBox
          label="+ add torrent"
          placeholder="paste a magnet link or .torrent url to send it to the client…"
          onAdd={async (url) => {
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
          }}
        />
      ) : null}
      {tab === 'usenet' && activeHealth === 'ok' ? (
        <AddBox
          label="+ add nzb"
          placeholder="paste an .nzb url to send it to the client…"
          onAdd={async (url) => {
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
          }}
        />
      ) : null}

      {trimmedNote ? <div className="adl-client-trimnote">{trimmedNote}</div> : null}

      {showSelectionBar ? (
        <div className="adl-client-selectbar" role="toolbar" aria-label="Selection">
          <span className="adl-client-selectbar-count">
            {selectedTargets.length
              ? `${selectedTargets.length} selected`
              : 'Select downloads to match'}
          </span>
          <button
            type="button"
            className="adl-filter-banner-clear"
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
          </button>
          <button
            type="button"
            className="adl-client-primary"
            disabled={selectedTargets.length === 0}
            onClick={() => setBulkTargets(selectedTargets)}
          >
            Match &amp; import{selectedTargets.length ? ` ${selectedTargets.length}` : ''}
          </button>
        </div>
      ) : null}

      <div className="adl-list adl-clients-list">
        {tab === 'soulseek' ? (
          connectedWithItems(slskd) && slskdVisible.length > 0 ? (
            slskdVisible.map((item) => slskdRow(item, slskdView === 'uploads'))
          ) : (
            <EmptyState
              health={activeHealth}
              fetchError={slskd.fetchError}
              overview={slskd.overview}
              noun={slskdView === 'uploads' ? 'uploads' : 'transfers'}
            />
          )
        ) : tab === 'torrent' ? (
          connectedWithItems(torrent) && torrentVisible.length > 0 ? (
            torrentVisible.map(torrentRow)
          ) : (
            <EmptyState
              health={activeHealth}
              fetchError={torrent.fetchError}
              overview={torrent.overview}
              noun="torrents"
            />
          )
        ) : connectedWithItems(usenet) && usenetVisible.length > 0 ? (
          usenetVisible.map(usenetRow)
        ) : (
          <EmptyState
            health={activeHealth}
            fetchError={usenet.fetchError}
            overview={usenet.overview}
            noun="jobs"
          />
        )}
      </div>

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
