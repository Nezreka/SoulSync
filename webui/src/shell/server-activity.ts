/**
 * Server Activity, the shell side: the floating button, its live badge and
 * the admin gate (#2155). The drawer itself is React
 * (src/features/server-activity), mounted from the main bundle as
 * window.SoulSyncActivityDrawer; this file only opens and closes it.
 *
 * Exposes window.ServerActivity (core.js routes the socket push here).
 */

const URL_ACT = '/api/server-activity';
const READY_EVENT = 'ss:server-activity-ready';
const TABS = ['now', 'history', 'stats'];

interface Payload {
  ok?: boolean;
  reason?: string;
  summary?: { streams?: number };
}

let badgePoll: ReturnType<typeof setInterval> | null = null;
/** asked to open before the React bundle was there */
let pendingTab: string | null = null;
let deepLinked = false;

const drawer = () => window.SoulSyncActivityDrawer;
const isOpen = () => !!drawer()?.isOpen();

function setBadge(n: number): void {
  const b = document.getElementById('activity-float-badge');
  const btn = document.getElementById('activity-float-btn');
  if (!b || !btn) return;
  if (n > 0) {
    b.textContent = n > 99 ? '99+' : String(n);
    b.style.display = '';
    btn.classList.add('activity-live');
  } else {
    b.style.display = 'none';
    btn.classList.remove('activity-live');
  }
}

// plex and jellyfin only. a setup with neither (navidrome, say) gets no
// button; only a definite 'no_server' hides it, never a failed fetch.
function setSupported(on: boolean): void {
  const btn = document.getElementById('activity-float-btn');
  if (btn) btn.style.display = on ? '' : 'none';
}

function sawActivity(raw: unknown): void {
  const d = raw as Payload | null;
  if (!d || typeof d !== 'object') return;
  if (d.ok === false && d.reason === 'no_server') setSupported(false);
  else setSupported(true);
  setBadge((d.summary && d.summary.streams) || 0);
}

function refresh(): Promise<Payload | null> {
  return fetch(URL_ACT, { headers: { Accept: 'application/json' } })
    .then((r) => (r.ok ? (r.json() as Promise<Payload>) : null))
    .catch(() => null)
    .then((d) => {
      sawActivity(d);
      return d;
    });
}

function open(tab?: string): void {
  if (!viewerIsAdmin()) return;
  const d = drawer();
  if (d) d.open(tab);
  else pendingTab = tab || 'now';
}
function close(): void {
  pendingTab = null;
  drawer()?.close();
}
function toggle(): void {
  if (isOpen()) close();
  else open();
}

window.addEventListener(READY_EVENT, () => {
  if (pendingTab !== null && viewerIsAdmin()) drawer()?.open(pendingTab);
  pendingTab = null;
});

// a light tick keeps the badge live on any page; the open drawer feeds it itself
function startBadgePoll(): void {
  if (badgePoll) return;
  void refresh();
  badgePoll = setInterval(() => {
    if (!isOpen()) void refresh();
  }, 20000);
}
function stopBadgePoll(): void {
  if (badgePoll) {
    clearInterval(badgePoll);
    badgePoll = null;
  }
}

/** ?activity=history opens the drawer at that tab (for notifications to land on) */
function openFromLink(): void {
  if (deepLinked) return;
  deepLinked = true;
  let tab: string | null = null;
  try {
    const url = new URL(window.location.href);
    tab = url.searchParams.get('activity');
    if (!tab) return;
    url.searchParams.delete('activity');
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
  } catch {
    return;
  }
  open(TABS.includes(tab) ? tab : 'now');
}

// who's watching what is the admin's (#2155). nothing runs until the profile
// is known, and a non-admin never gets the button or a single poll.
export function viewerIsAdmin(): boolean {
  const ctx = window.getCurrentProfileContext?.();
  return !!ctx && (ctx.isAdmin === true || ctx.profileId === 1);
}
export function applyProfile(): void {
  if (viewerIsAdmin()) {
    startBadgePoll();
    openFromLink();
    return;
  }
  stopBadgePoll();
  close();
  setSupported(false);
  setBadge(0);
}

window.ServerActivity = {
  toggle,
  open,
  close,
  refresh,
  _onSocket: (raw: unknown) => {
    sawActivity(raw);
    drawer()?.push(raw);
  },
  _wantsLive: () => !!drawer()?.wantsLive(),
  _sawActivity: sawActivity,
};
window.addEventListener('ss:webui-profile-context-changed', applyProfile);
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', applyProfile);
} else {
  applyProfile();
}
