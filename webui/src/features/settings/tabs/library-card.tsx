/**
 * What the Library tab's pages share (settings-bic phase 4): the kinds of
 * media, the folder field every path uses, the folder checks the hero counts,
 * and the small helpers the old cards had (the Docker check, a draft that a
 * fresh load resets).
 */

import {
  type RefObject,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';

import type { SettingsStore } from '../settings-store';
import type { HeroPart } from './library/words';

import { Btn, type FolderCheck, Hero, HeroWarn, PathField, Tag } from '../ui';
import s from '../ui/settings.module.css';
import { useDraft } from './parts';
import { text } from './source-panels';

export const LIBRARY_TAB = 'library';

export type Media = 'music' | 'audiobooks' | 'podcasts' | 'video';

export const MEDIA_LABEL: Record<Media, string> = {
  music: 'Music',
  audiobooks: 'Audiobooks',
  podcasts: 'Podcasts',
  video: 'Video',
};

type Shell = Window & {
  SoulSyncFolderCheck?: {
    refresh: (group: string) => void;
    environment?: () => Promise<{ docker?: boolean } | null>;
  };
  showToast?: (message: string, type?: string) => void;
  showConfirmDialog?: (options: {
    title: string;
    message: string;
    confirmText?: string;
    cancelText?: string;
    destructive?: boolean;
  }) => Promise<boolean>;
  navigateToPage?: (page: string) => void;
};
export const shell = () => window as Shell;
export const toast = (message: string, type: string) => shell().showToast?.(message, type);

/** folder-check.js looks at a group again (after a load, a row added) */
export function refreshChecks(...groups: string[]) {
  setTimeout(() => {
    for (const group of groups) shell().SoulSyncFolderCheck?.refresh(group);
  }, 0);
}

export function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}

export function useLoads(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().loads);
}

/** a field that shows what is typed and saves what it reads as; a fresh
 *  load puts the saved value back */
export function useLoadedDraft(store: SettingsStore, value: string, save: (raw: string) => void) {
  const draft = useDraft(value, save);
  const loads = useLoads(store);
  useEffect(() => draft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return draft;
}

/** parseInt with a fallback for what does not read as one (_cfgInt) */
export function int(raw: string, fallback: number) {
  const n = parseInt(raw, 10);
  return Number.isFinite(n) ? n : fallback;
}

/** the Docker check loadSettingsData made, for the default folders */
export function isDocker(settings: Record<string, Record<string, unknown> | undefined>) {
  const env = settings._environment || {};
  const soulseek = settings.soulseek || {};
  return (
    !!env.docker ||
    text(soulseek.transfer_path).startsWith('/app/') ||
    text(soulseek.download_path).startsWith('/app/')
  );
}

// ── what the folder checks said, for the heroes ──

const answers = new Map<string, FolderCheck>();
const listeners = new Set<() => void>();
let version = 0;

export const folderChecks = {
  set(id: string, check: FolderCheck | null) {
    const was = answers.get(id);
    if (check ? was?.tone === check.tone && was.text === check.text : !was) return;
    if (check) answers.set(id, check);
    else answers.delete(id);
    version += 1;
    for (const listener of listeners) listener();
  },
  get: (id: string) => answers.get(id) ?? null,
  /** for tests */
  reset() {
    answers.clear();
    version += 1;
  },
};

/** the checks of these fields, redrawn when one changes */
export function useFolderChecks(ids: string[]): Array<FolderCheck | null> {
  useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => version,
  );
  return ids.map((id) => folderChecks.get(id));
}

/** "412 GB free" out of a check's words */
export function freeOf(check: FolderCheck | null): string {
  return check?.text.match(/([\d.]+ [GT]B) free/)?.[1] ?? '';
}

/** what folder-check.js reads on a checked field */
export function checkAttrs(
  group: string,
  label: string,
  role?: 'input' | 'existing',
): Record<`data-${string}`, string> {
  return {
    'data-folder-check': group,
    'data-folder-label': label,
    ...(role ? { 'data-folder-role': role } : {}),
  };
}

/**
 * A folder saved through the store: locked until Unlock (not saved; every
 * field locks again when it is drawn again), what is typed stays on screen
 * and a fresh load puts the saved one back, and the folder check under it.
 */
export function LibPath({
  store,
  id,
  value,
  onChange,
  placeholder,
  check,
  warn,
  ariaLabel,
}: {
  store: SettingsStore;
  id: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  /** folder-check.js's attributes; none: not checked */
  check?: Record<`data-${string}`, string>;
  /** a warning of the page's own that outranks the check (the install default) */
  warn?: string | null;
  ariaLabel?: string;
}) {
  const [locked, setLocked] = useState(true);
  const loads = useLoads(store);
  const draft = useDraft(value, onChange);
  useEffect(() => draft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <PathField
      id={id}
      value={draft.value}
      onChange={draft.onChange}
      placeholder={placeholder}
      locked={locked}
      onLock={(next) => {
        setLocked(next);
        if (!next) setTimeout(() => document.getElementById(id)?.focus(), 0);
      }}
      check={warn ? { tone: 'warn', text: warn } : undefined}
      onCheck={(answer) => folderChecks.set(id, answer)}
      inputProps={{ ...check, 'aria-label': ariaLabel }}
    />
  );
}

/**
 * While a model loads (or failed to) every control in the block is off but
 * Retry, and back as it was after, as loadSharedLibrary did it.
 */
export function useBusyGate(ref: RefObject<HTMLElement | null>, busy: boolean) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !busy) return;
    const off = [
      ...el.querySelectorAll<HTMLInputElement>(
        'input, select, textarea, button:not(.stg-library-retry):not([data-more])',
      ),
    ].filter((c) => !c.disabled);
    for (const c of off) c.disabled = true;
    return () => {
      for (const c of off) c.disabled = false;
    };
  }, [busy]); // eslint-disable-line react-hooks/exhaustive-deps
}

/**
 * A list of folders: one field per row, its folder check under it, a remove
 * per row and "Add folder" under them. Each row keeps its own key, so taking
 * one out takes that row (and its check), not the last.
 */
export function FolderRows({
  id,
  rows,
  onRows,
  onRemove,
  onAdd,
  placeholder,
  check,
  addLabel = 'Add folder',
  empty,
  itemLabel,
  tagFor,
  listRef,
  addAttrs,
  listAttrs,
  shown = true,
}: {
  id: string;
  rows: string[];
  /** a row typed into */
  onRows: (rows: string[]) => void;
  /** a row taken out (default: onRows without it) */
  onRemove?: (rows: string[]) => void;
  /** a row added (default: onRows with an empty one) */
  onAdd?: (rows: string[]) => void;
  placeholder: string;
  check?: Record<`data-${string}`, string>;
  addLabel?: string;
  empty?: string;
  itemLabel: string;
  tagFor?: (index: number) => string | null;
  /** the list, for one that saves when a field is left (change bubbles) */
  listRef?: (el: HTMLDivElement | null) => void;
  addAttrs?: Record<`data-${string}`, string>;
  listAttrs?: Record<`data-${string}`, string | undefined>;
  /** false until the settings load: the list is empty, not "none" */
  shown?: boolean;
}) {
  const keys = useRef<{ list: number[]; next: number }>({ list: [], next: 0 });
  while (keys.current.list.length < rows.length) keys.current.list.push(keys.current.next++);
  if (keys.current.list.length > rows.length) keys.current.list.length = rows.length;
  const list = useRef<HTMLDivElement>(null);
  const group = check?.['data-folder-check'];
  return (
    <div className={s.list}>
      <div
        id={id}
        ref={(el) => {
          list.current = el;
          listRef?.(el);
        }}
        className={s.folderRows}
        {...listAttrs}
      >
        {shown && rows.length === 0 && empty ? <p className={s.listEmpty}>{empty}</p> : null}
        {rows.map((value, index) => {
          const tag = tagFor?.(index);
          return (
            <div className={s.listRow} key={keys.current.list[index]} data-folder-row="">
              <PathField
                value={value}
                placeholder={placeholder}
                onChange={(next) => onRows(rows.map((old, i) => (i === index ? next : old)))}
                inputProps={{ ...check, 'aria-label': itemLabel }}
              />
              {tag ? <Tag accent>{tag}</Tag> : null}
              <Btn
                kind="ghost"
                small
                iconOnly
                icon="x"
                aria-label={`Remove ${value.trim() || itemLabel.toLowerCase()}`}
                onClick={() => {
                  keys.current.list.splice(index, 1);
                  (onRemove ?? onRows)(rows.filter((_, i) => i !== index));
                  if (group) refreshChecks(group);
                }}
              />
            </div>
          );
        })}
      </div>
      <div>
        <Btn
          small
          icon="plus"
          {...addAttrs}
          onClick={() => {
            (onAdd ?? onRows)([...rows, '']);
            requestAnimationFrame(() => {
              const inputs = list.current?.querySelectorAll<HTMLInputElement>('input');
              inputs?.[inputs.length - 1]?.focus();
            });
            if (group) refreshChecks(group);
          }}
        >
          {addLabel}
        </Btn>
      </div>
    </div>
  );
}

/** a hero sentence: names stand out, what needs a look is amber */
export function LibHero({
  parts,
  stats,
}: {
  parts: HeroPart[];
  stats?: Array<{ value: string | number; label: string }>;
}) {
  return (
    <Hero stats={stats}>
      {parts.map((part, index) =>
        part.kind === 'name' ? (
          // eslint-disable-next-line react/no-array-index-key
          <em key={index}>{part.text}</em>
        ) : part.kind === 'warn' ? (
          // eslint-disable-next-line react/no-array-index-key
          <HeroWarn key={index}>{part.text}</HeroWarn>
        ) : (
          part.text
        ),
      )}
    </Hero>
  );
}
