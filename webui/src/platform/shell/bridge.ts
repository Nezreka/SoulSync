import type { AnyRouter } from '@tanstack/react-router';

import type { ShellStatusPayload } from './status';

import {
  getShellRouteByPageId,
  normalizeShellPath,
  resolveShellPageFromPath,
  shellRouteManifest,
  type ShellPageId,
  type ShellRouteDefinition,
} from './route-manifest';

export interface ShellProfileContext {
  profileId: number;
  isAdmin: boolean;
}

export interface ShellContext {
  bridge: ShellBridge;
  profile: ShellProfileContext;
  status?: ShellStatusPayload | null;
}

export type ShellBridge = NonNullable<typeof window.SoulSyncWebShellBridge>;

export const SHELL_BRIDGE_READY_EVENT = 'ss:webui-shell-bridge-ready';
export const SHELL_PROFILE_CONTEXT_CHANGED_EVENT = 'ss:webui-profile-context-changed';

export function getShellBridge(): ShellBridge | null {
  return window.SoulSyncWebShellBridge ?? null;
}

export function getShellProfileContext(bridge = getShellBridge()): ShellProfileContext | null {
  return bridge?.getCurrentProfileContext() ?? null;
}

export function getShellContext(bridge = getShellBridge()): ShellContext | null {
  const profile = getShellProfileContext(bridge);
  if (!bridge || !profile) return null;

  return { bridge, profile };
}

/** Detail routes need an entity id in the URL, so they are never a landing page. */
const HOME_FALLBACK_EXCLUDED: ReadonlySet<ShellPageId> = new Set(['artist-detail', 'label-detail']);

export function getProfileHomePath(bridge = getShellBridge()): `/${string}` {
  const pageId = bridge?.getProfileHomePage() ?? 'discover';
  const isAllowed = (id: ShellPageId) => bridge?.isPageAllowed(id) ?? true;

  // iss29-B10: every route guard redirects here when it denies access, so a
  // home page the profile may not open hands the router straight back to the
  // page that just refused it — that is an endless redirect, not a bounce. The
  // vanilla shell has always checked this (`navigateToPage` in init.js); the
  // React guards inherited the version that does not. Newly reachable because
  // the legacy `library-v2` page id normalizes to `library`, so a profile whose
  // home is the old id lands on a page its allowed_pages need not contain.
  if (isAllowed(pageId)) {
    return getShellRouteByPageId(pageId)?.path ?? '/discover';
  }

  const reachable = shellRouteManifest.find(
    (route) => !HOME_FALLBACK_EXCLUDED.has(route.pageId) && isAllowed(route.pageId),
  );
  // `help` needs no permission in the shell's own gate, so it is the one path
  // that stays truthful even for a profile allowed nothing else.
  return reachable?.path ?? '/help';
}

export async function waitForShellContext(): Promise<ShellContext> {
  const currentContext = getShellContext();
  if (currentContext) return currentContext;

  return await new Promise<ShellContext>((resolve) => {
    const cleanup = () => {
      window.removeEventListener(SHELL_BRIDGE_READY_EVENT, handleReady);
      window.removeEventListener(SHELL_PROFILE_CONTEXT_CHANGED_EVENT, handleProfileChange);
    };

    const settleIfReady = () => {
      const shell = getShellContext();
      if (!shell) return;
      cleanup();
      resolve(shell);
    };

    const handleReady = () => {
      settleIfReady();
    };

    const handleProfileChange = () => {
      settleIfReady();
    };

    window.addEventListener(SHELL_BRIDGE_READY_EVENT, handleReady);
    window.addEventListener(SHELL_PROFILE_CONTEXT_CHANGED_EVENT, handleProfileChange);

    settleIfReady();
  });
}

export function bindWindowWebRouter(router: AnyRouter) {
  window.SoulSyncWebRouter = {
    routeManifest: [...shellRouteManifest],
    getCurrentPath() {
      return normalizeShellPath(window.location.pathname);
    },
    resolvePageId(pathname: string) {
      return resolveShellPageFromPath(pathname);
    },
    async navigateToPage(pageId, options) {
      const route = getShellRouteByPageId(pageId);
      if (!route) return false;
      if (pageId === 'artist-detail' && !options?.artistId) {
        return false;
      }
      if (pageId === 'label-detail' && !options?.labelId) {
        return false;
      }

      let href: `/${string}` = route.path;
      if (pageId === 'artist-detail' && options?.artistId) {
        const source = options.artistSource ? String(options.artistSource) : 'library';
        href =
          `/artist-detail/${encodeURIComponent(source)}/${encodeURIComponent(String(options.artistId))}` as `/${string}`;
        // Some sources (Bandcamp) have no numeric-ID lookup API — the name
        // has to travel with the URL or the route has nothing to resolve
        // against on mount.
        if (options.artistName) {
          href = `${href}?name=${encodeURIComponent(options.artistName)}` as `/${string}`;
        }
      }
      if (pageId === 'label-detail' && options?.labelId) {
        href = `/label-detail/${encodeURIComponent(String(options.labelId))}` as `/${string}`;
        // The display name travels with the URL so a refresh has something to
        // show before the catalog fetch resolves the canonical name.
        if (options.labelName) {
          href = `${href}?name=${encodeURIComponent(options.labelName)}` as `/${string}`;
        }
      }

      await router.navigate({ href, replace: options?.replace === true });
      return true;
    },
    async navigateToHref(href, options) {
      // Only same-origin app paths; anything else belongs to the browser.
      if (!href.startsWith('/') || href.startsWith('//')) return false;
      const pageId = resolveShellPageFromPath(new URL(href, window.location.origin).pathname);
      if (!pageId || getShellRouteByPageId(pageId)?.kind !== 'react') return false;
      await router.navigate({ href: href as `/${string}`, replace: options?.replace === true });
      return true;
    },
  };
}

export type { ShellPageId, ShellRouteDefinition };
