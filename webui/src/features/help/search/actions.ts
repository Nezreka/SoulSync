/**
 * The things help search can do, not just find (plan 4.4). Each one is a
 * button that already exists somewhere in SoulSync; this calls the same
 * route or opens the same view, never a copy of its logic.
 *
 * Permission is two things: `admin` (the server answers 403 to anyone else,
 * so a member never sees the row) and `when` (the page, the side or the
 * right this profile needs). A member's list is what they could click
 * themselves.
 */

import { startLibraryDeepScan, startLibraryScan } from '@/routes/dashboard/-dash.api';
import { processWishlist } from '@/routes/library/-library-v2.api';
import { startWatchlistScan } from '@/routes/watchlist/-watchlist.api';

import type { IconName } from '../icons';
import type { ViewId } from '../views';

import { goToPage, openDocsHome } from '../links';
import { useHelpStore } from '../store';

export type Side = 'music' | 'video';

/** a few icons the sheet's set has no word for */
export type SearchIconName = IconName | 'scan' | 'swap';

export interface ActionContext {
  isAdmin: boolean;
  side: Side;
  /** the sidebar's own answer: may this profile open that page */
  canOpen: (pageId: string) => boolean;
}

export interface HelpAction {
  id: string;
  title: string;
  /** when the words depend on where you are */
  titleFor?: (ctx: ActionContext) => string;
  /** a few words after the title: what it does */
  hint: string;
  keywords?: string;
  icon: SearchIconName;
  /** a server route that is @admin_only, or a view that is admin only */
  admin?: boolean;
  /** only on this side of the app */
  side?: Side;
  /** anything else it needs: a page this profile may open, a global that exists */
  when?: (ctx: ActionContext) => boolean;
  /** key caps shown on the row */
  keys?: string[];
  run: (ctx: ActionContext) => void | Promise<void>;
}

type Globals = {
  canDownload?: () => boolean;
  profileAllowedSides?: () => string;
  openFeaturePicker?: () => void;
};
const g = () => window as unknown as Globals;

function toast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'success') {
  window.showToast?.(message, type);
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/** a help view, in the sheet (from the palette the sheet opens on it) */
function openView(view: ViewId) {
  useHelpStore.getState().openSheet({ view });
}

async function readSuccess(res: Response, fallback: string): Promise<void> {
  const data = (await res.json().catch(() => null)) as { success?: boolean; error?: string } | null;
  if (!res.ok || !data?.success) throw new Error(data?.error || fallback);
}

function canDownload(): boolean {
  const fn = g().canDownload;
  return typeof fn === 'function' ? fn() !== false : true;
}

function otherSide(side: Side): Side {
  return side === 'video' ? 'music' : 'video';
}

export const ACTIONS: HelpAction[] = [
  // ── music library ──
  {
    id: 'action:library-quick-scan',
    title: 'Quick scan the library',
    hint: 'new files only',
    keywords: 'scan refresh update media server new tracks incremental database',
    icon: 'scan',
    admin: true,
    side: 'music',
    run: async () => {
      try {
        await readSuccess(await startLibraryScan(false), 'Failed to start scan');
        toast('Library scan started');
      } catch (error) {
        toast(errorText(error, 'Failed to start scan'), 'error');
      }
    },
  },
  {
    id: 'action:library-deep-scan',
    title: 'Deep scan the library',
    hint: 're-reads everything',
    keywords: 'scan full rescan refresh media server missing removed database',
    icon: 'scan',
    admin: true,
    side: 'music',
    run: async () => {
      const confirmed = await window.showConfirmDialog?.({
        title: 'Deep Scan Library',
        message:
          'A deep scan re-checks every track in your media server library. It adds what was missed, removes what is gone from your server and keeps your metadata. Large libraries take a while. Continue?',
        confirmText: 'Deep scan',
      });
      if (!confirmed) return;
      try {
        await readSuccess(await startLibraryDeepScan(), 'Failed to start deep scan');
        toast('Deep scan started, this may take a while');
      } catch (error) {
        toast(errorText(error, 'Failed to start deep scan'), 'error');
      }
    },
  },
  {
    id: 'action:watchlist-scan',
    title: 'Scan the watchlist now',
    hint: 'new releases from artists you follow',
    keywords: 'watchlist new releases check artists follow scan',
    icon: 'refresh',
    side: 'music',
    when: (ctx) => ctx.canOpen('watchlist'),
    run: async () => {
      try {
        await startWatchlistScan();
        toast('Watchlist scan started');
      } catch (error) {
        toast(errorText(error, 'Failed to start watchlist scan'), 'error');
      }
    },
  },
  {
    id: 'action:wishlist-process',
    title: 'Download the wishlist now',
    hint: 'search for everything waiting',
    keywords: 'wishlist process download missing search monitored',
    icon: 'down',
    side: 'music',
    when: (ctx) => ctx.canOpen('wishlist') && canDownload(),
    run: async () => {
      try {
        toast(await processWishlist());
      } catch (error) {
        toast(errorText(error, 'Wishlist processing failed to start'), 'error');
      }
    },
  },

  // ── video library ──
  {
    id: 'action:video-quick-scan',
    title: 'Quick scan movies and TV',
    hint: 'new files only',
    keywords: 'scan refresh video library movies shows incremental',
    icon: 'scan',
    admin: true,
    side: 'video',
    run: () => {
      // video-scan.js owns the request and the progress everywhere it shows
      document.dispatchEvent(
        new CustomEvent('soulsync:video-scan-start', {
          detail: { mode: 'incremental', media_type: 'all' },
        }),
      );
      toast('Library scan started');
    },
  },
  {
    id: 'action:video-deep-scan',
    title: 'Deep scan movies and TV',
    hint: 're-reads everything',
    keywords: 'scan full rescan video library movies shows',
    icon: 'scan',
    admin: true,
    side: 'video',
    run: () => {
      document.dispatchEvent(
        new CustomEvent('soulsync:video-scan-start', {
          detail: { mode: 'deep', media_type: 'all' },
        }),
      );
      toast('Deep scan started, this may take a while');
    },
  },

  // ── help itself ──
  {
    id: 'action:health',
    title: 'Check folders and services now',
    hint: 'runs Health',
    keywords:
      'health check status diagnose troubleshoot broken not working folders paths services disk',
    icon: 'pulse',
    admin: true,
    run: () => openView('health'),
  },
  {
    id: 'action:report',
    title: 'Report a problem',
    hint: 'a redacted bundle for GitHub or Discord',
    keywords: 'bug issue report diagnostics debug info support help',
    icon: 'bug',
    admin: true,
    run: () => openView('report'),
  },
  {
    id: 'action:logs',
    title: 'Open the logs',
    hint: 'read or download them',
    keywords: 'logs log file errors download debug',
    icon: 'terminal',
    admin: true,
    run: () => openView('logs'),
  },
  {
    id: 'action:report-member',
    title: 'Report a problem with a song or title',
    hint: 'tell your admin',
    keywords: 'report issue wrong song bad file problem admin',
    icon: 'flag',
    when: (ctx) => !ctx.isAdmin,
    run: (ctx) => goToPage(ctx.side === 'video' ? 'video-issues' : 'issues'),
  },
  {
    id: 'action:whats-new',
    title: "What's new",
    hint: 'this release',
    keywords: 'release notes changelog update version new features',
    icon: 'spark',
    run: () => openView('whats-new'),
  },
  {
    id: 'action:shortcuts',
    title: 'Keyboard shortcuts',
    hint: 'every key in one list',
    keywords: 'keys hotkeys keyboard shortcuts',
    icon: 'key',
    keys: ['?'],
    run: () => openView('shortcuts'),
  },
  {
    id: 'action:docs',
    title: 'Open Help & Docs',
    hint: 'every guide',
    keywords: 'docs documentation manual guide help wiki',
    icon: 'book',
    run: () => openDocsHome(),
  },

  // ── you ──
  {
    id: 'action:switch-side',
    title: 'Switch sides',
    titleFor: (ctx) => (ctx.side === 'video' ? 'Switch to music' : 'Switch to movies and TV'),
    hint: 'the other side of SoulSync',
    keywords: 'switch side video music movies tv toggle',
    icon: 'swap',
    when: () => {
      const sides = g().profileAllowedSides;
      const both = typeof sides !== 'function' || sides() === 'both';
      const toggle = document.querySelector<HTMLElement>('.side-toggle');
      return (
        both &&
        !!window._switchAppSide &&
        !!toggle &&
        !toggle.classList.contains('nav-feature-hidden')
      );
    },
    run: (ctx) => window._switchAppSide?.(otherSide(ctx.side)),
  },
  {
    id: 'action:customize',
    title: 'Choose what shows in the sidebar',
    hint: 'hide what you never use',
    keywords: 'customize sidebar hide pages features simplify make it yours',
    icon: 'sliders',
    when: () =>
      typeof g().openFeaturePicker === 'function' &&
      !document.documentElement.hasAttribute('data-features-locked'),
    run: () => g().openFeaturePicker?.(),
  },
  {
    id: 'action:my-account',
    title: 'Open My Account',
    hint: 'your server login and music services',
    keywords: 'account profile my settings spotify listenbrainz lastfm login',
    icon: 'user',
    when: (ctx) => !ctx.isAdmin && typeof window.openMyAccountsModal === 'function',
    run: () => window.openMyAccountsModal?.(),
  },
];

/** what this viewer may run here: admin rows only for an admin, this side's only */
export function actionsFor(ctx: ActionContext, list: HelpAction[] = ACTIONS): HelpAction[] {
  return list.filter((a) => {
    if (a.admin && !ctx.isAdmin) return false;
    if (a.side && a.side !== ctx.side) return false;
    try {
      return a.when ? a.when(ctx) : true;
    } catch {
      return false;
    }
  });
}
