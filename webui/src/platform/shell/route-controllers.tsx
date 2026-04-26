import { useRouter } from '@tanstack/react-router';
import { useEffect, useLayoutEffect, useState } from 'react';

import { getProfileHomePath, getShellBridge, type ShellPageId } from './bridge';

export const ROUTER_ROOT_ID = 'webui-react-root';
export const SHELL_BRIDGE_READY_EVENT = 'ss:webui-shell-bridge-ready';
export const SHELL_PROFILE_CONTEXT_CHANGED_EVENT = 'ss:webui-profile-context-changed';

export function useShellBridge() {
  const [, setRevision] = useState(0);

  useEffect(() => {
    const handleContextChange = () => {
      setRevision((value) => value + 1);
    };

    handleContextChange();
    window.addEventListener(SHELL_BRIDGE_READY_EVENT, handleContextChange);
    window.addEventListener(SHELL_PROFILE_CONTEXT_CHANGED_EVENT, handleContextChange);
    return () => {
      window.removeEventListener(SHELL_BRIDGE_READY_EVENT, handleContextChange);
      window.removeEventListener(SHELL_PROFILE_CONTEXT_CHANGED_EVENT, handleContextChange);
    };
  }, []);

  return getShellBridge();
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
