/**
 * The Clients tab — the external download clients in one pane.
 *
 * Three self-loading sections (slskd, torrent, usenet), each with its own
 * health line and 10s poll while mounted. Scope is "see what's happening and
 * unstick it": pause / resume / remove / cancel. Rows SoulSync itself
 * dispatched carry a chip saying what they are; everything else is honestly
 * labeled external.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { ClientAction } from '../-adl.api';
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

const CLIENT_TYPE_LABELS: Record<string, string> = {
  qbittorrent: 'qBittorrent',
  transmission: 'Transmission',
  deluge: 'Deluge',
  aria2: 'aria2',
  sabnzbd: 'SABnzbd',
  nzbget: 'NZBGet',
};

function clientTypeLabel(type: string | undefined, fallback: string): string {
  if (!type) return fallback;
  return CLIENT_TYPE_LABELS[type] ?? type;
}

/** one section's live state: null until the first fetch lands. */
function useClientPoll<T>(fetcher: () => Promise<ClientOverview<T> | null>) {
  const [data, setData] = useState<ClientOverview<T> | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const reload = useCallback(async () => {
    const next = await fetcherRef.current();
    // a failed fetch keeps the last known state instead of blanking the table
    if (next) setData(next);
  }, []);

  useEffect(() => {
    void reload();
    const timer = setInterval(() => void reload(), CLIENTS_POLL_MS);
    return () => clearInterval(timer);
  }, [reload]);

  return { data, reload };
}

function speedText(bytesPerSec: number): string {
  if (!bytesPerSec) return '';
  return `${formatBytes(bytesPerSec)}/s`;
}

function progressText(progress: number): string {
  // torrent/usenet report 0-1, slskd reports 0-100
  const pct = progress > 1 ? progress : progress * 100;
  return `${Math.round(pct)}%`;
}

function StateChip({ state, error }: { state: string; error?: string | null }) {
  return (
    <span className={`adl-client-state adl-client-state-${state}`} title={error || state}>
      {state}
    </span>
  );
}

function SoulsyncChip({ item }: { item: { soulsync?: { kind?: string; title?: string } } }) {
  if (!item.soulsync) {
    return (
      <span className="adl-client-owner adl-client-owner-external" title="Not dispatched by SoulSync">
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

function SectionShell({
  title,
  typeLabel,
  data,
  children,
}: {
  title: string;
  typeLabel: string;
  data: ClientOverview<unknown> | null;
  children: React.ReactNode;
}) {
  return (
    <div className="adl-client-section">
      <div className="adl-client-section-header">
        <span className="adl-client-section-title">{title}</span>
        <span className="adl-client-section-type">{typeLabel}</span>
        {data === null ? (
          <span className="adl-client-health">checking…</span>
        ) : !data.configured ? (
          <span className="adl-client-health adl-client-health-off">not configured</span>
        ) : data.connected ? (
          <span className="adl-client-health adl-client-health-ok">connected</span>
        ) : (
          <span className="adl-client-health adl-client-health-bad" title={data.error}>
            unreachable
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

function EmptyLine({ data, noun }: { data: ClientOverview<unknown> | null; noun: string }) {
  if (data === null) return <div className="adl-client-empty">loading…</div>;
  if (!data.configured) {
    return <div className="adl-client-empty">configure one in Settings to manage it here</div>;
  }
  if (!data.connected) {
    return <div className="adl-client-empty">{data.error || 'could not reach the client'}</div>;
  }
  return <div className="adl-client-empty">no {noun} right now</div>;
}

/* ── torrent + usenet share one row shape ────────────────────────────────── */

function TransferRow({
  name,
  state,
  error,
  progress,
  size,
  speed,
  extra,
  owner,
  actions,
}: {
  name: string;
  state: string;
  error?: string | null;
  progress: number;
  size: number;
  speed: number;
  extra?: string;
  owner: React.ReactNode;
  actions: React.ReactNode;
}) {
  return (
    <div className="adl-row adl-client-row">
      <div className="adl-row-info">
        <div className="adl-row-title" title={name}>
          {name}
        </div>
        <div className="adl-row-meta">
          {progressText(progress)}
          {size ? ` of ${formatBytes(size)}` : ''}
          {speed ? ` · ${speedText(speed)}` : ''}
          {extra ? ` · ${extra}` : ''}
        </div>
      </div>
      <div className="verif-actions">
        {owner}
        <StateChip state={state} error={error} />
        {actions}
      </div>
    </div>
  );
}

function PauseResumeRemove({
  item,
  onAction,
}: {
  item: { id: string; name: string; state: string };
  onAction: (id: string, action: ClientAction, deleteFiles: boolean) => void;
}) {
  const paused = item.state === 'paused';
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

  const runAction = useCallback(
    async (label: string, call: () => Promise<{ success?: boolean; error?: string }>, reload: () => Promise<void>) => {
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

  return (
    <div className="adl-clients" id="adl-clients">
      <SectionShell title="Soulseek" typeLabel="slskd" data={slskd.data}>
        {slskd.data?.connected && slskd.data.items.length > 0 ? (
          slskd.data.items.map((item) => (
            <TransferRow
              key={`${item.username}:${item.id}`}
              name={item.filename}
              state={item.state}
              progress={item.progress}
              size={item.size}
              speed={item.speed}
              extra={`from ${item.username}`}
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
          <EmptyLine data={slskd.data} noun="transfers" />
        )}
      </SectionShell>

      <SectionShell
        title="Torrents"
        typeLabel={clientTypeLabel(torrent.data?.type, 'torrent client')}
        data={torrent.data}
      >
        {torrent.data?.connected && torrent.data.items.length > 0 ? (
          torrent.data.items.map((item) => (
            <TransferRow
              key={item.id}
              name={item.name}
              state={item.state}
              error={item.error}
              progress={item.progress}
              size={item.size}
              speed={item.download_speed}
              extra={[
                item.seeders ? `${item.seeders} seeders` : '',
                item.ratio != null ? `ratio ${item.ratio.toFixed(2)}` : '',
              ]
                .filter(Boolean)
                .join(' · ')}
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
          <EmptyLine data={torrent.data} noun="torrents" />
        )}
      </SectionShell>

      <SectionShell
        title="Usenet"
        typeLabel={clientTypeLabel(usenet.data?.type, 'usenet client')}
        data={usenet.data}
      >
        {usenet.data?.connected && usenet.data.items.length > 0 ? (
          usenet.data.items.map((item) => (
            <TransferRow
              key={item.id}
              name={item.name}
              state={item.state}
              error={item.error}
              progress={item.progress}
              size={item.size}
              speed={item.download_speed}
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
          <EmptyLine data={usenet.data} noun="jobs" />
        )}
      </SectionShell>
    </div>
  );
}
