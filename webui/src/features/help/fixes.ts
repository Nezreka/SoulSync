/**
 * What a Health finding's buttons do (HELP_API.md, Fix). The one runner for
 * every kind, wherever the button is: the Health view (health.tsx) shows the
 * outcome on the card, a page's answers (pages/kit.tsx runFix) as a toast.
 *
 *   post       POST the app's own route, then check again when it asks
 *   settings   Settings on that tab, the video side's when media is video
 *   page       a page, on the video side when media is video
 *   external   a new tab, a docker-side host swapped for this one
 *   checklist  the Finish setting up card's own action for that item
 *   logs       the sheet's Logs view
 *   recheck    run Health again, cache skipped
 */

import type { HealthFix } from './api';

import { browserHref, runAction } from '../onboarding/checklist-card';
import { postFix } from './api';
import { goToPage, openExternal } from './links';
import { isVideoSide } from './page';
import { useHelpStore } from './store';

/** ok with the server's own words when it sent some ("Connected to Jellyfin") */
export type FixOutcome = { ok: true; message?: string } | { ok: false; error: string };

export interface FixContext {
  /** Health again with the cache skipped */
  recheck: () => Promise<unknown> | void;
  openLogs: () => void;
}

export async function runFix(fix: HealthFix, ctx: FixContext): Promise<FixOutcome> {
  switch (fix.kind) {
    case 'post': {
      const res = await postFix(fix.url, fix.body);
      if (!res.ok) return { ok: false, error: res.error };
      if (fix.recheck) await ctx.recheck();
      const said = (res.data as { message?: unknown } | null)?.message;
      return typeof said === 'string' && said ? { ok: true, message: said } : { ok: true };
    }
    case 'settings':
      openSettings(fix.tab, fix.media);
      leaveOnPhone();
      return { ok: true };
    case 'page':
      openPage(fix.path, fix.media);
      leaveOnPhone();
      return { ok: true };
    case 'external':
      if (!/^https?:\/\//i.test(fix.href))
        return { ok: false, error: "That link isn't a web address." };
      openExternal(browserHref(fix.href));
      return { ok: true };
    case 'checklist':
      runAction({ id: fix.item, href: fix.href });
      if (fix.item !== 'slskd_shares' && fix.item !== 'extension') leaveOnPhone();
      return { ok: true };
    case 'logs':
      ctx.openLogs();
      return { ok: true };
    case 'recheck':
      await ctx.recheck();
      return { ok: true };
    default:
      return { ok: false, error: "This button doesn't do anything yet." };
  }
}

/** sheet.tsx PHONE_QUERY; the sheet covers the page there, so get out of the way */
const PHONE_QUERY = '(max-width: 768px)';

function leaveOnPhone(): void {
  if (typeof window.matchMedia === 'function' && window.matchMedia(PHONE_QUERY).matches) {
    useHelpStore.getState().close();
  }
}

/** the shell shows one side at a time; a video fix needs the video side */
function toSide(side: 'music' | 'video'): void {
  if (isVideoSide() === (side === 'video')) return;
  window._switchAppSide?.(side);
}

function settingsMedia(): 'video' | null {
  return new URLSearchParams(window.location.search).get('media') === 'video' ? 'video' : null;
}

/**
 * Settings on a tab. The page opens on the tab it was told last
 * (shell.ts pendingTab) when it isn't on screen yet; when it is, on the
 * same side, the tab just switches. Moving between the music and the video
 * settings remounts the page, so the tab is switched once it's back.
 */
export function openSettings(tab: string, media?: 'video'): void {
  const want = media === 'video' ? 'video' : null;
  const safe = tab.replace(/[^\w-]/g, '');
  const mounted = !!document.getElementById('settings-page');
  if (mounted && settingsMedia() === want && isVideoSide() === !!want) {
    window.switchSettingsTab?.(safe);
    return;
  }
  if (!mounted) window.switchSettingsTab?.(safe);
  toSide(want ? 'video' : 'music');
  goToPage(want ? 'video-settings' : 'settings');
  if (!mounted) return;
  let tries = 0;
  const attempt = () => {
    tries += 1;
    if (document.getElementById('settings-page') && settingsMedia() === want) {
      window.switchSettingsTab?.(safe);
    } else if (tries < 30) {
      setTimeout(attempt, 100);
    }
  };
  setTimeout(attempt, 100);
}

/** the video side's page for a shared React route */
const VIDEO_PAGE: Record<string, string> = { 'active-downloads': 'video-downloads' };

export function openPage(path: string, media?: 'video'): void {
  const id =
    path
      .replace(/^\/+/, '')
      .split(/[/?#]/)[0]
      ?.replace(/[^\w-]/g, '') || 'dashboard';
  if (media === 'video') {
    toSide('video');
    goToPage(VIDEO_PAGE[id] ?? (id.startsWith('video-') ? id : `video-${id}`));
    return;
  }
  toSide('music');
  goToPage(id);
}
