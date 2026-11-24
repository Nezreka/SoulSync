/**
 * one line at the bottom of the player saying what just went wrong (or
 * right). the player had no shared way to say it, so a failed save, remove
 * or permission change just did nothing.
 */

import { useSyncExternalStore } from 'react';

import s from './notice.module.css';

export type NoticeKind = 'error' | 'info';
interface Notice {
  id: number;
  text: string;
  kind: NoticeKind;
}

const SHOW_MS = 4200;
let notices: Notice[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

/** say it; the same words twice in a row show once */
export function notify(text: string, kind: NoticeKind = 'info') {
  if (notices.some((n) => n.text === text)) return;
  const id = nextId++;
  notices = [...notices.slice(-2), { id, text, kind }];
  emit();
  window.setTimeout(() => {
    notices = notices.filter((n) => n.id !== id);
    emit();
  }, SHOW_MS);
}

/** a mutation's onError: the server's words when it sent some */
export function notifyError(fallback: string) {
  return (error: unknown) =>
    notify(error instanceof Error && error.message ? error.message : fallback, 'error');
}

export function NoticeHost() {
  const list = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => notices,
    () => notices,
  );
  if (!list.length) return null;
  return (
    <div className={s.stack} role="status" aria-live="polite">
      {list.map((n) => (
        <div key={n.id} className={s.notice} data-kind={n.kind}>
          <span className={s.dot} aria-hidden="true" />
          {n.text}
        </div>
      ))}
    </div>
  );
}
