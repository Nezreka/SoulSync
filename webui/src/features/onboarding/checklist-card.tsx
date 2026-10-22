/**
 * "Finish setting up" on the dashboard: what setup left for later, worked
 * out by the server from the config each time (core/onboarding/checklist.py),
 * so doing a thing anywhere ticks it here. Gone once everything is done or
 * it's dismissed. Admins only; anyone else gets a 403 and sees nothing.
 */

import { useEffect, useState } from 'react';

import { apiClient, readJson } from '@/app/api-client';
import { appURL } from '@/platform/url-base';

import c from './checklist.module.css';

interface ChecklistItem {
  id: string;
  title: string;
  description: string;
  action: string;
  done: boolean;
  href?: string;
}

interface Checklist {
  dismissed: boolean;
  items: ChecklistItem[];
}

type ShellWindow = Window & {
  navigateToPage?: (page: string) => unknown;
  switchSettingsTab?: (tab: string) => void;
  openSettingsForSource?: (src: string) => void;
  openProfileManager?: () => void;
  openInviteCreator?: () => void;
};

/** a docker-side address means nothing to the browser, swap in this host */
export function browserHref(href: string): string {
  try {
    const url = new URL(href);
    if (['host.docker.internal', '172.17.0.1', 'localhost', '127.0.0.1'].includes(url.hostname))
      url.hostname = window.location.hostname;
    return url.toString();
  } catch {
    return href;
  }
}

function runAction(item: ChecklistItem) {
  const w = window as ShellWindow;
  switch (item.id) {
    case 'torrent':
      void w.navigateToPage?.('settings');
      window.setTimeout(() => w.switchSettingsTab?.('downloads'), 60);
      return;
    case 'spotify':
      w.openSettingsForSource?.('spotify');
      return;
    case 'video':
      window.location.assign(appURL('/video-settings'));
      return;
    case 'invite':
      if (w.openInviteCreator) w.openInviteCreator();
      else w.openProfileManager?.();
      return;
    default:
      if (item.href) window.open(browserHref(item.href), '_blank', 'noopener');
  }
}

export function FinishSetupCard() {
  const [data, setData] = useState<Checklist | null>(null);

  // asked again whenever the tab comes back, so sharing a folder in slskd or
  // installing the extension in another tab ticks the row on return
  useEffect(() => {
    let live = true;
    const load = () =>
      readJson<Checklist>(apiClient.get('onboarding/checklist'))
        .then((d) => live && setData(d))
        .catch(() => undefined);
    const onVisible = () => {
      if (document.visibilityState === 'visible') void load();
    };
    void load();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      live = false;
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  const post = (json: Record<string, unknown>) =>
    readJson<Checklist>(apiClient.post('onboarding/checklist', { json }))
      .then(setData)
      .catch(() => undefined);

  if (!data || data.dismissed) return null;
  const items = data.items;
  const done = items.filter((i) => i.done).length;
  if (!items.length || done === items.length) return null;
  const pct = Math.round((done / items.length) * 100);

  return (
    <section className={c.card} aria-labelledby="finish-setup-title">
      <div className={c.head}>
        <div
          className={c.ring}
          style={{
            background: `conic-gradient(rgb(var(--accent-rgb)) 0 ${pct}%, rgba(255,255,255,0.08) ${pct}% 100%)`,
          }}
          role="img"
          aria-label={`${done} of ${items.length} done`}
        >
          <span>
            {done}/{items.length}
          </span>
        </div>
        <div className={c.titles}>
          <h2 id="finish-setup-title">Finish setting up</h2>
          <p>A few things that make SoulSync better. None of them are urgent.</p>
        </div>
        <button
          type="button"
          className={c.close}
          aria-label="Hide this checklist"
          onClick={() => post({ dismissed: true })}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>
      <ul className={c.items}>
        {items.map((item) => (
          <li key={item.id} className={c.item} data-done={item.done || undefined}>
            <button
              type="button"
              className={c.tick}
              aria-pressed={item.done}
              aria-label={item.done ? `${item.title}, done` : `Mark ${item.title} done`}
              title={item.done ? 'Done' : 'Mark as done'}
              disabled={item.done}
              onClick={() => post({ done: item.id })}
            >
              <svg
                width="11"
                height="11"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="3.6"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M5 12.5l4.5 4.5L19 7.5" />
              </svg>
            </button>
            <div className={c.text}>
              <span className={c.itemTitle}>{item.title}</span>
              <span className={c.itemDesc}>{item.description}</span>
            </div>
            {!item.done ? (
              <button
                type="button"
                className={c.action}
                onClick={() => {
                  runAction(item);
                  if (item.id === 'extension') void post({ done: item.id });
                }}
              >
                {item.action}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
