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

import type { ClientAction, ClientFetch } from '../-adl.api';
import type {
  ClientOverview,
  ClientSlskdItem,
  ClientTorrentItem,
  ClientUsenetItem,
} from '../-adl.types';

import {
  fetchSlskdClient,
  fetchTorrentClient,
  fetchUsenetClient,
  slskdClientCancel,
  torrentClientAction,
  usenetClientAction,
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
  const toggleCard = useCallback((key: string) => {
    setOpenCards((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const runAction = useCallback(
    async (
      label: string,
      call: () => Promise<{ success?: boolean; error?: string }>,
      reload: () => Promise<void>,
    ) => {
      try {
        const data = await call();
        if (data.success) toast(`${label} ok`, 'success');
        else toast(data.error || `${label} failed`, 'error');
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
              onClick={() => setTab(pill.key)}
            >
              <span className={`adl-client-dot adl-client-dot-${health}`} />
              {pill.label}
              {count !== null && health === 'ok' ? ` (${count})` : ''}
            </button>
          );
        })}
        <span className="verif-banner-spacer" />
        {typeLabel ? <span className="adl-client-section-type">{typeLabel}</span> : null}
        <span className={`adl-client-health adl-client-health-${activeHealth}`}>
          {HEALTH_TEXT[activeHealth]}
        </span>
      </div>

      <div className="adl-list adl-clients-list">
        {tab === 'soulseek' ? (
          slskd.overview?.connected && slskd.overview.items.length > 0 ? (
            slskd.overview.items.map((item) => (
              <ClientRow
                key={`${item.username}:${item.id}`}
                name={item.filename.split(/[\\/]/).pop() || item.filename}
                sub={`from ${item.username}`}
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
                  ['Uploader', item.username],
                  ['State', item.state],
                  ['Size', item.size ? formatBytes(item.size) : ''],
                  ['Transferred', item.transferred ? formatBytes(item.transferred) : ''],
                  ['Speed', speedText(item.speed)],
                  ['Time left', durationText(item.time_remaining)],
                  ['Local file', item.file_path],
                  ['Transfer id', item.id],
                  ['SoulSync', item.soulsync?.title],
                ]}
                expanded={openCards.has(`soulseek:${item.username}:${item.id}`)}
                onToggle={() => toggleCard(`soulseek:${item.username}:${item.id}`)}
                owner={<SoulsyncChip item={item} />}
                actions={
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
                }
              />
            ))
          ) : (
            <EmptyState
              health={activeHealth}
              fetchError={slskd.fetchError}
              overview={slskd.overview}
              noun="transfers"
            />
          )
        ) : tab === 'torrent' ? (
          torrent.overview?.connected && torrent.overview.items.length > 0 ? (
            torrent.overview.items.map((item) => (
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
            ))
          ) : (
            <EmptyState
              health={activeHealth}
              fetchError={torrent.fetchError}
              overview={torrent.overview}
              noun="torrents"
            />
          )
        ) : usenet.overview?.connected && usenet.overview.items.length > 0 ? (
          usenet.overview.items.map((item) => (
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
          ))
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
