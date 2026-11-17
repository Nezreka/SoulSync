/**
 * Mounts the Server Activity drawer in its own root, so the floating activity
 * button opens it from any page. The shell side (src/shell/server-activity.ts)
 * keeps the button, its badge and the admin gate (#2155), and calls
 * window.SoulSyncActivityDrawer to open and close this.
 */

import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';

import { Drawer, TABS, type Tab } from './drawer';
import { pushActivity } from './live';

const HOST_ID = 'soulsync-server-activity-root';
export const READY_EVENT = 'ss:server-activity-ready';

type Command = { open: boolean; tab?: Tab };
let send: ((command: Command) => void) | null = null;
/** a command that came before the drawer first rendered */
let pending: Command | null = null;
let state = { open: false, tab: 'now' as Tab };

function dispatch(command: Command) {
  if (send) send(command);
  else pending = command;
}

function asTab(value: unknown): Tab {
  return TABS.includes(value as Tab) ? (value as Tab) : 'now';
}

export function ServerActivityHost() {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>('now');

  useEffect(() => {
    send = (command) => {
      if (command.tab) setTab(command.tab);
      setOpen(command.open);
    };
    if (pending) {
      send(pending);
      pending = null;
    }
    return () => {
      send = null;
    };
  }, []);

  useEffect(() => {
    state = { open, tab };
  }, [open, tab]);

  return <Drawer open={open} tab={tab} onTab={setTab} onClose={() => setOpen(false)} />;
}

export function installDrawerApi(): void {
  window.SoulSyncActivityDrawer = {
    open: (tab?: string) => dispatch({ open: true, tab: tab ? asTab(tab) : undefined }),
    close: () => dispatch({ open: false }),
    toggle: () => dispatch({ open: !state.open }),
    isOpen: () => state.open,
    wantsLive: () => state.open && state.tab === 'now',
    push: pushActivity,
  };
}

export function mountServerActivityHost(): void {
  if (typeof document === 'undefined' || document.getElementById(HOST_ID)) return;
  const el = document.createElement('div');
  el.id = HOST_ID;
  // after the floating buttons, so the same layer puts the drawer on top (#2090)
  document.body.appendChild(el);
  installDrawerApi();
  createRoot(el).render(<ServerActivityHost />);
  // the shell may have been asked to open before this bundle ran
  window.dispatchEvent(new CustomEvent(READY_EVENT));
}
