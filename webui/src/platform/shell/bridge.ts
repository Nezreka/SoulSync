import type { AnyRouter } from '@tanstack/react-router';

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

export type ShellBridge = NonNullable<typeof window.SoulSyncWebShellBridge>;

export function getShellBridge(): ShellBridge | null {
  return window.SoulSyncWebShellBridge ?? null;
}

export function getShellProfileContext(bridge = getShellBridge()): ShellProfileContext | null {
  return bridge?.getCurrentProfileContext() ?? null;
}

export function getProfileHomePath(bridge = getShellBridge()): `/${string}` {
  const pageId = bridge?.getProfileHomePage() ?? 'discover';
  return getShellRouteByPageId(pageId)?.path ?? '/discover';
}

export function bindWindowWebRouter(router: AnyRouter) {
  window.SoulSyncWebRouter = {
    routeManifest: [...shellRouteManifest],
    getCurrentPageId() {
      return resolveShellPageFromPath(window.location.pathname);
    },
    getCurrentPath() {
      return normalizeShellPath(window.location.pathname);
    },
    resolvePageId(pathname: string) {
      return resolveShellPageFromPath(pathname);
    },
    async navigateToPage(pageId, options) {
      const route = getShellRouteByPageId(pageId);
      if (!route) return false;

      await router.navigate({
        href: route.path,
        replace: options?.replace === true,
      });
      return true;
    },
  };
}

export type { ShellPageId, ShellRouteDefinition };
