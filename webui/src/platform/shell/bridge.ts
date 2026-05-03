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

export interface ShellContext {
  bridge: ShellBridge;
  profile: ShellProfileContext;
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

export function getProfileHomePath(bridge = getShellBridge()): `/${string}` {
  const pageId = bridge?.getProfileHomePage() ?? 'discover';
  return getShellRouteByPageId(pageId)?.path ?? '/discover';
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

      await router.navigate({
        href: route.path,
        replace: options?.replace === true,
      });
      return true;
    },
  };
}

export type { ShellPageId, ShellRouteDefinition };
