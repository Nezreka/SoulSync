import { useRouteContext, useRouter } from '@tanstack/react-router';
import { useEffect, useLayoutEffect } from 'react';

import { appURL } from '@/platform/url-base';

import { getProfileHomePath, type ShellContext, type ShellPageId } from './bridge';

export const ROUTER_ROOT_ID = 'webui-react-root';

export function useShellContext(): ShellContext {
  const context = useRouteContext({
    from: '__root__',
    select: (routeContext) => routeContext.shell,
  });
  return context;
}

export function useShellBridge() {
  return useShellContext().bridge;
}

export function useProfile() {
  return useShellContext().profile;
}

export function useShellStatus() {
  return useShellContext().status ?? null;
}

export function LegacyRouteController({ pathname }: { pathname: string }) {
  const bridge = useShellBridge();

  useEffect(() => {
    if (!bridge) return;
    bridge.activateLegacyPath(pathname);
  }, [bridge, pathname]);

  return null;
}

/**
 * search (optional) is for a page that shows another side's view under
 * ?media= (the wishlist's video tabs): the shell then asks THAT view's
 * permission, the same way the route guard does. see isPageAccessAllowed.
 */
export function useReactPageShell(pageId: ShellPageId, search?: string) {
  const bridge = useShellBridge();
  const router = useRouter();

  useLayoutEffect(() => {
    if (!bridge) return;
    if (!pageAllowed(bridge, pageId, search)) return;

    bridge.setActivePageChrome(pageId);
    bridge.showReactHost(pageId);
  }, [bridge, pageId, search]);

  useEffect(() => {
    if (!bridge) return;

    if (!pageAllowed(bridge, pageId, search)) {
      void router.navigate({ href: appURL(getProfileHomePath(bridge)), replace: true });
      return;
    }
  }, [bridge, pageId, search, router]);

  return bridge;
}

function pageAllowed(
  bridge: NonNullable<ReturnType<typeof useShellBridge>>,
  pageId: ShellPageId,
  search: string | undefined,
): boolean {
  return search === undefined ? bridge.isPageAllowed(pageId) : bridge.isPageAllowed(pageId, search);
}
