import { useRouteContext, useRouter } from '@tanstack/react-router';
import { useEffect, useLayoutEffect } from 'react';

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

export function LegacyRouteController({ pathname }: { pathname: string }) {
  const bridge = useShellBridge();

  useEffect(() => {
    if (!bridge) return;
    bridge.activateLegacyPath(pathname);
  }, [bridge, pathname]);

  return null;
}

export function useReactPageShell(pageId: ShellPageId) {
  const bridge = useShellBridge();
  const router = useRouter();

  useLayoutEffect(() => {
    if (!bridge) return;
    if (!bridge.isPageAllowed(pageId)) return;

    bridge.setActivePageChrome(pageId);
    bridge.showReactHost(pageId);
  }, [bridge, pageId]);

  useEffect(() => {
    if (!bridge) return;

    if (!bridge.isPageAllowed(pageId)) {
      void router.navigate({ href: getProfileHomePath(bridge), replace: true });
      return;
    }
  }, [bridge, pageId, router]);

  return bridge;
}
