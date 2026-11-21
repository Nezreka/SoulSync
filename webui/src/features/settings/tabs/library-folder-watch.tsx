/**
 * Library > Folders, "Watching for changes" (Phase 3 item 3.2): the switch
 * for live folder watching, how often network shares are checked, and each
 * watched folder with how it is watched (live, or checked every N minutes),
 * why, and a choice per folder. The words under it come from
 * /api/library/folder-watch (core/library/folder_watch.py).
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { SettingsStore } from '../settings-store';

import {
  Card,
  Item,
  Items,
  NumberField,
  Row,
  Say,
  SectionLabel,
  Select,
  StatusWords,
  Switch,
  itemClass,
  type Tone,
} from '../ui';
import { useLoadedDraft, useSettings } from './library-card';
import { text } from './source-panels';

export interface WatchRoot {
  path: string;
  kind: 'library' | 'import';
  setting: string;
  mode: 'live' | 'polling' | 'off';
  reason: string;
  note: string;
  reachable: boolean;
  poll_minutes: number | null;
  last_change: number | null;
  changes: number;
  pending: number;
}

export interface WatchStatus {
  enabled: boolean;
  running: boolean;
  live_available: boolean;
  watching: number;
  last_change: number | null;
  pending: number;
  poll_minutes: number;
  roots: WatchRoot[];
}

const STATUS_URL = '/api/library/folder-watch';
const DEFAULT_POLL_MINUTES = 15;

const MODE_OPTIONS = [
  { value: 'auto', label: 'Automatic' },
  { value: 'live', label: 'Live' },
  { value: 'polling', label: 'On a timer' },
  { value: 'off', label: 'Off' },
];

/** "just now", "2 min ago", "3 h ago", "2 days ago" */
export function ago(at: number | null, now: number = Date.now() / 1000): string {
  if (!at) return '';
  const seconds = Math.max(0, now - at);
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function minutesWords(minutes: number | null | undefined) {
  const n = Math.max(1, Math.round(minutes || DEFAULT_POLL_MINUTES));
  return n === 1 ? 'every minute' : `every ${n} min`;
}

/** the one line under the switch: "Watching 3 folders, last change 2 min ago" */
export function statusLine(status: WatchStatus | null, enabled: boolean, now?: number): string {
  if (!enabled) return 'Folder watching is off. New files show up after a library scan.';
  if (!status) return 'Checking what is watched…';
  if (!status.running && !status.roots.length) return 'Folder watching starts with the server.';
  const n = status.watching;
  const head = `Watching ${n} folder${n === 1 ? '' : 's'}`;
  const last = ago(status.last_change, now);
  const pending = status.pending ? `, ${status.pending} waiting to settle` : '';
  return `${head}, ${last ? `last change ${last}` : 'no changes yet'}${pending}.`;
}

/** how one folder is watched, in words, and the tone of its dot */
export function modeWords(root: WatchRoot): { tone: Tone; words: string } {
  if (root.mode === 'off') return { tone: 'idle', words: 'Off' };
  if (!root.reachable) return { tone: 'warn', words: 'Not reachable' };
  if (root.mode === 'polling')
    return { tone: 'ok', words: `Checked ${minutesWords(root.poll_minutes)}` };
  return { tone: 'ok', words: 'Live' };
}

function rootDetail(root: WatchRoot) {
  const kind = root.kind === 'import' ? 'Import folder' : 'Music library';
  const bits = [kind, root.reason].filter(Boolean).join(', ');
  return root.note ? `${bits}. ${root.note[0].toUpperCase()}${root.note.slice(1)}.` : bits;
}

async function readStatus(refresh = false): Promise<WatchStatus | null> {
  try {
    const response = await fetch(refresh ? `${STATUS_URL}/refresh` : STATUS_URL, {
      method: refresh ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' },
      body: refresh ? '{}' : undefined,
    });
    if (!response.ok) return null;
    const data = (await response.json()) as WatchStatus & { success?: boolean };
    return data && Array.isArray(data.roots) ? data : null;
  } catch {
    return null;
  }
}

export function FolderWatchSection({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const library = settings.library || {};
  const enabled = library.watch_folders === true;
  const modes = (library.watch_root_modes as Record<string, string> | undefined) || {};
  const [status, setStatus] = useState<WatchStatus | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const load = useCallback(async (refresh = false) => {
    const next = await readStatus(refresh);
    if (next) setStatus(next);
    setNow(Date.now() / 1000);
  }, []);

  useEffect(() => {
    void load();
    const every = setInterval(() => void load(), 30_000);
    return () => {
      clearInterval(every);
      clearTimeout(timer.current);
    };
  }, [load]);

  /** after a change is saved, ask the watcher to read the settings again */
  const reread = () => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      void store.flush().then(() => load(true));
    }, 800);
  };

  const pollMinutes = useLoadedDraft(
    store,
    text(library.watch_poll_minutes ?? DEFAULT_POLL_MINUTES),
    (raw) => {
      const n = parseInt(raw, 10);
      store.set(
        'library',
        'watch_poll_minutes',
        Number.isFinite(n) && n > 0 ? n : DEFAULT_POLL_MINUTES,
      );
      reread();
    },
  );

  const roots = status?.roots || [];
  return (
    <>
      <SectionLabel
        title="Watching for changes"
        hint="Music you add, retag, move or delete by hand shows up without a scan"
      />
      <Card>
        <Row
          label="Watch library folders for changes"
          htmlFor="watch-folders"
          help="A change in a music library folder rescans just that album, a few seconds after the copy finishes."
          more={
            <>
              Local disks are watched <strong>live</strong>. Network shares (SMB, NFS, a mapped
              drive) don&apos;t pass changes on reliably, so those are{' '}
              <strong>checked on a timer</strong> instead. A file that moved keeps its plays,
              padlocks and history. Files SoulSync writes itself, half-copied files and temp files
              are left alone, and a folder that goes missing as a whole is treated as a drive that
              is away: nothing in it is marked missing. A change in the Import folder starts the
              auto-import right away.
            </>
          }
        >
          <Switch
            id="watch-folders"
            checked={enabled}
            onChange={(event) => {
              store.set('library', 'watch_folders', event.target.checked);
              reread();
            }}
          />
        </Row>
        <Row
          sub
          field
          off={!enabled}
          label="Check network shares every"
          htmlFor="watch-poll-minutes"
          help="How often a folder on a timer is looked through. Only the folders' listings are read each time."
        >
          <NumberField
            id="watch-poll-minutes"
            min="1"
            step="1"
            unit="min"
            placeholder={String(DEFAULT_POLL_MINUTES)}
            disabled={!enabled}
            value={pollMinutes.value}
            onChange={(event) => pollMinutes.onChange(event.target.value)}
          />
        </Row>
        <Say id="folder-watch-status" tone={enabled ? 'ok' : undefined}>
          {statusLine(status, enabled, now)}
          {status && !status.live_available && enabled
            ? ' Live watching needs the watchdog package, so every folder is checked on a timer.'
            : null}
        </Say>
        {enabled && roots.length ? (
          <Items id="folder-watch-roots" aria-label="Watched folders">
            {roots.map((root, index) => {
              const { tone, words } = modeWords(root);
              const last = ago(root.last_change, now);
              return (
                <Item key={root.path} off={root.mode === 'off'} data-watch-root={root.path}>
                  <div className={itemClass.main}>
                    <span className={itemClass.mono}>{root.path}</span>
                    <span className={itemClass.dim}>
                      {rootDetail(root)}
                      {last ? ` Last change ${last}.` : ''}
                    </span>
                  </div>
                  <div className={itemClass.cells}>
                    <StatusWords tone={tone}>{words}</StatusWords>
                    <Select
                      id={`watch-mode-${index}`}
                      aria-label={`How to watch ${root.path}`}
                      value={modes[root.path] || root.setting || 'auto'}
                      options={MODE_OPTIONS}
                      onChange={(event) => {
                        store.set('library', 'watch_root_modes', {
                          ...modes,
                          [root.path]: event.target.value,
                        });
                        reread();
                      }}
                    />
                  </div>
                </Item>
              );
            })}
          </Items>
        ) : null}
      </Card>
    </>
  );
}
