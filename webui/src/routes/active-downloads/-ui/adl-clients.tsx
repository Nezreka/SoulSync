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

function etaText(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '';
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

function StateChip({ state, error }: { state: string; error?: string | null }) {
  return (
    <span
      className={`adl-client-state adl-client-state-${stateBucket(state)}`}
      title={error || state}
    >
      {state}
    </span>
  );
}

function SoulsyncChip({ item }: { item: { soulsync?: { kind?: string; title?: string } } }) {
  if (!item.soulsync) {
    return (
      <span
        className="adl-client-owner adl-client-owner-external"
        title="Not dispatched by SoulSync"
      >
        external
      </span>
    );
  }
  const label = item.soulsync.title || item.soulsync.kind || 'SoulSync';
  return (
    <span className="adl-client-owner" title={`SoulSync dispatched this: ${label}`}>
      {label}
    </span>
  );
}

/* ── the list view: search, state filter, sort ───────────────────────────── */

export type ClientSort = 'default' | 'speed' | 'progress' | 'name' | 'size';

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
  totalSpeed,
  upSpeed,
  search,
  onSearch,
  stateFilter,
  onStateFilter,
  sort,
  onSort,
  stateOf,
  link,
  onRefresh,
}: {
  items: { length: number };
  totalSpeed: number;
  upSpeed?: number;
  search: string;
  onSearch: (value: string) => void;
  stateFilter: string;
  onStateFilter: (value: string) => void;
  sort: ClientSort;
  onSort: (value: ClientSort) => void;
  stateOf: Map<string, number>;
  link: string;
  onRefresh: () => void;
}) {
  return (
    <div className="adl-client-toolbar">
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
  placeholder,
  onAdd,
}: {
  placeholder: string;
  onAdd: (url: string) => Promise<boolean>;
}) {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = () => {
    if (!url.trim() || busy) return;
    setBusy(true);
    void onAdd(url.trim()).then((ok) => {
      setBusy(false);
      if (ok) setUrl('');
    });
  };
  return (
    <div className="adl-client-addbox">
      <input
        type="text"
        className="adl-client-search adl-client-add-input"
        placeholder={placeholder}
        value={url}
        onChange={(event) => setUrl(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') submit();
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

function ClientRow({
  name,
  sub,
  state,
  error,
  progress,
  detail,
  details,
  owner,
  actions,
  expanded,
  onToggle,
}: {
  name: string;
  sub?: string;
  state: string;
  error?: string | null;
  progress: number;
  detail: string;
  /** everything the client knows, shown when the card is open. */
  details: DetailPairs;
  owner: React.ReactNode;
  actions: React.ReactNode;
  expanded: boolean;
  onToggle: () => void;
}) {
  const bucket = stateBucket(state);
  const shown = details.filter(([, value]) => value != null && String(value).trim() !== '');
  return (
    <div
      className={`adl-client-card${expanded ? ' expanded' : ''}`}
      data-state={bucket}
      title={expanded ? undefined : 'Click to show everything the client reports'}
      onClick={onToggle}
    >
      <div className="adl-client-card-main">
        <div className="adl-row-info">
          <div className="adl-client-row-top">
            <span className="adl-row-title" title={name}>
              {name}
            </span>
            {owner}
          </div>
          {sub ? <div className="adl-row-meta">{sub}</div> : null}
          <div className="adl-client-progress" data-state={bucket}>
            <div className="adl-client-progress-fill" style={{ width: `${pct(progress)}%` }} />
          </div>
          <div className="adl-client-row-stats">
            <span>{Math.round(pct(progress))}%</span>
            {detail ? <span>{detail}</span> : null}
          </div>
        </div>
        <div
          className="verif-actions adl-client-actions"
          onClick={(event) => event.stopPropagation()}
        >
          <StateChip state={state} error={error} />
          {actions}
          <span className={`adl-client-chevron${expanded ? ' open' : ''}`} aria-hidden>
            ▾
          </span>
        </div>
      </div>
      {expanded ? (
        <div className="adl-client-details" onClick={(event) => event.stopPropagation()}>
          {error ? <div className="adl-client-details-error">{error}</div> : null}
          <dl>
            {shown.map(([label, value]) => (
              <div className="adl-client-detail" key={label}>
                <dt>{label}</dt>
                <dd title={String(value)}>{String(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
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

function PauseResumeRemove({
  item,
  onAction,
}: {
  item: { id: string; name: string; state: string };
  onAction: (id: string, action: ClientAction, deleteFiles: boolean) => void;
}) {
  const paused = stateBucket(item.state) === 'paused';
  return (
    <>
      <button
        type="button"
        className="verif-act"
        title={paused ? 'Resume' : 'Pause'}
        onClick={() => onAction(item.id, paused ? 'resume' : 'pause', false)}
      >
        {paused ? '▶' : '⏸'}
      </button>
      <button
        type="button"
        className="verif-act verif-act-del"
        title="Remove from the client (asks about the files)"
        onClick={() => {
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
        }}
      >
        🗑
      </button>
    </>
  );
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
  const [slskdView, setSlskdView] = useState<'downloads' | 'uploads'>('downloads');
  const [links, setLinks] = useState<ClientLinks | null>(null);

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
  const slskdVisible = applyView(slskdSource, search, stateFilter, sort, {
    name: (t) => t.filename,
    speed: (t) => t.speed,
    size: (t) => t.size,
    progress: (t) => t.progress,
    state: (t) => t.state,
    haystack: (t) => t.username,
  });
  const torrentVisible = applyView(torrent.overview?.items ?? [], search, stateFilter, sort, {
    name: (t) => t.name,
    speed: (t) => t.download_speed,
    size: (t) => t.size,
    progress: (t) => t.progress,
    state: (t) => t.state,
  });
  const usenetVisible = applyView(usenet.overview?.items ?? [], search, stateFilter, sort, {
    name: (t) => t.name,
    speed: (t) => t.download_speed,
    size: (t) => t.size,
    progress: (t) => t.progress,
    state: (t) => t.state,
  });

  const slskdRow = (item: ClientSlskdItem, readOnly: boolean) => (
    <ClientRow
      key={`${readOnly ? 'up' : 'dl'}:${item.username}:${item.id}`}
      name={item.filename.split(/[\\/]/).pop() || item.filename}
      sub={readOnly ? `to ${item.username}` : `from ${item.username}`}
      state={item.state}
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
      expanded={openCards.has(`soulseek:${slskdView}:${item.username}:${item.id}`)}
      onToggle={() => toggleCard(`soulseek:${slskdView}:${item.username}:${item.id}`)}
      owner={<SoulsyncChip item={item} />}
      actions={
        readOnly ? null : (
          <button
            type="button"
            className="verif-act verif-act-del"
            title="Cancel this transfer in slskd"
            onClick={() =>
              void runAction(
                'Cancel',
                () => slskdClientCancel(item.id, item.username, true),
                slskd.reload,
              )
            }
          >
            ✕
          </button>
        )
      }
    />
  );

  const torrentRow = (item: ClientTorrentItem) => (
    <ClientRow
      key={item.id}
      name={item.name}
      state={item.state}
      error={item.error}
      progress={item.progress}
      detail={[
        item.size ? formatBytes(item.size) : '',
        speedText(item.download_speed),
        etaText(item.eta),
        item.seeders ? `${item.seeders} seeders` : '',
      ]
        .filter(Boolean)
        .join(' · ')}
      details={[
        ['State', item.state],
        ['Size', item.size ? formatBytes(item.size) : ''],
        ['Downloaded', item.downloaded ? formatBytes(item.downloaded) : ''],
        ['Down speed', speedText(item.download_speed)],
        ['Up speed', speedText(item.upload_speed)],
        ['ETA', durationText(item.eta)],
        ['Seeders', item.seeders ? String(item.seeders) : ''],
        ['Peers', item.peers ? String(item.peers) : ''],
        ['Ratio', item.ratio != null ? item.ratio.toFixed(2) : ''],
        ['Seeding time', durationText(item.seeding_time)],
        ['Save path', item.save_path],
        ['Content path', item.content_path],
        ['Hash', item.id],
        ['SoulSync', item.soulsync?.title],
      ]}
      expanded={openCards.has(`torrent:${item.id}`)}
      onToggle={() => toggleCard(`torrent:${item.id}`)}
      owner={<SoulsyncChip item={item} />}
      actions={
        <PauseResumeRemove
          item={item}
          onAction={(id, action, deleteFiles) =>
            void runAction(
              action === 'remove' ? 'Remove' : action === 'pause' ? 'Pause' : 'Resume',
              () => torrentClientAction(id, action, deleteFiles),
              torrent.reload,
            )
          }
        />
      }
    />
  );

  const usenetRow = (item: ClientUsenetItem) => (
    <ClientRow
      key={item.id}
      name={item.name}
      state={item.state}
      error={item.error}
      progress={item.progress}
      detail={[
        item.size ? formatBytes(item.size) : '',
        speedText(item.download_speed),
        etaText(item.eta),
      ]
        .filter(Boolean)
        .join(' · ')}
      details={[
        ['State', item.state],
        ['Size', item.size ? formatBytes(item.size) : ''],
        ['Downloaded', item.downloaded ? formatBytes(item.downloaded) : ''],
        ['Speed', speedText(item.download_speed)],
        ['ETA', durationText(item.eta)],
        ['Category', item.category],
        ['Save path', item.save_path],
        ['Staging path', item.incomplete_path],
        ['Job id', item.id],
        ['SoulSync', item.soulsync?.title],
      ]}
      expanded={openCards.has(`usenet:${item.id}`)}
      onToggle={() => toggleCard(`usenet:${item.id}`)}
      owner={<SoulsyncChip item={item} />}
      actions={
        <PauseResumeRemove
          item={item}
          onAction={(id, action, deleteFiles) =>
            void runAction(
              action === 'remove' ? 'Remove' : action === 'pause' ? 'Pause' : 'Resume',
              () => usenetClientAction(id, action, deleteFiles),
              usenet.reload,
            )
          }
        />
      }
    />
  );

  const connectedWithItems = (state: ClientState<unknown>) =>
    Boolean(state.overview?.connected);

  const activeVisible =
    tab === 'soulseek' ? slskdVisible : tab === 'torrent' ? torrentVisible : usenetVisible;
  const activeAll =
    tab === 'soulseek'
      ? slskdSource
      : tab === 'torrent'
        ? (torrent.overview?.items ?? [])
        : (usenet.overview?.items ?? []);
  const stateCounts = bucketCounts(
    activeAll as { state: string }[],
    (item) => item.state,
  );
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
          totalSpeed={downSpeed}
          upSpeed={upSpeed}
          search={search}
          onSearch={setSearch}
          stateFilter={stateFilter}
          onStateFilter={setStateFilter}
          sort={sort}
          onSort={setSort}
          stateOf={stateCounts}
          link={activeLink}
          onRefresh={() => void activeState.reload()}
        />
      ) : null}

      {tab === 'torrent' && activeHealth === 'ok' ? (
        <AddBox
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
    </div>
  );
}
