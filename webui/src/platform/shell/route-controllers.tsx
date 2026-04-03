import { useRouter } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

import { getProfileHomePath, getShellBridge, type ShellPageId } from './bridge';

export const ROUTER_ROOT_ID = 'webui-react-root';
export const SHELL_BRIDGE_READY_EVENT = 'ss:webui-shell-bridge-ready';

export function useShellBridge() {
  const [ready, setReady] = useState(() => Boolean(getShellBridge()));

  useEffect(() => {
    const handleReady = () => {
      setReady(Boolean(getShellBridge()));
    };

    handleReady();
    window.addEventListener(SHELL_BRIDGE_READY_EVENT, handleReady);
    return () => {
      window.removeEventListener(SHELL_BRIDGE_READY_EVENT, handleReady);
    };
  }, []);

  return ready ? getShellBridge() : null;
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

  useEffect(() => {
    if (!bridge) return;

    if (!bridge.isPageAllowed(pageId)) {
      void router.navigate({ href: getProfileHomePath(bridge), replace: true });
      return;
    }

    bridge.setActivePageChrome(pageId);
    bridge.showReactHost(pageId);
  }, [bridge, pageId, router]);

  return bridge;
}
