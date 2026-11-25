/**
 * what the server's admin says to this screen from Server Activity: a stop
 * with its reason, or a message. the heartbeat (presence.ts) brings them;
 * this shows them in the player's own dialog. a stop has already stopped
 * playback by the time it shows; a message leaves it playing.
 */

import { useSyncExternalStore } from 'react';

import { Sheet } from '../sheet';
import s from '../sheet.module.css';

export interface ServerOrder {
  kind: 'stop' | 'message';
  title: string;
  text: string;
}

let current: ServerOrder | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function showServerOrder(order: ServerOrder) {
  current = order;
  emit();
}

function dismiss() {
  current = null;
  emit();
}

export function ServerOrderHost() {
  const order = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
    () => current,
  );
  if (!order) return null;
  return (
    <Sheet
      title={order.title}
      onClose={dismiss}
      footer={
        <button type="button" className={s.btnPrimary} onClick={dismiss} data-nav>
          OK
        </button>
      }
    >
      {order.text}
    </Sheet>
  );
}
