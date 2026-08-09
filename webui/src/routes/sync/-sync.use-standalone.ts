/**
 * The standalone-mode signal — is there a real media server, or is SoulSync
 * its own library ("soulsync" server type)? The vanilla keeps this in the
 * script-scoped `_isSoulsyncStandalone` (core.js:1442), unreachable from
 * modules; both its writers derive it from the service-status payload's
 * `media_server.type`, and core.js re-broadcasts every status frame as the
 * `ss:service-status` CustomEvent — so this hook derives the same flag from
 * the same source. Until the first frame lands the flag is false, exactly the
 * vanilla's initial value (its readers have the same read-before-first-frame
 * window).
 */

import { useEffect, useState } from 'react';

export const SERVICE_STATUS_EVENT = 'ss:service-status';

export function isStandalonePayload(detail: unknown): boolean {
  const payload = detail as { media_server?: { type?: string } } | null | undefined;
  return payload?.media_server?.type === 'soulsync';
}

export function useStandalone(): boolean {
  const [standalone, setStandalone] = useState(false);

  useEffect(() => {
    const onStatus = (event: Event) => {
      setStandalone(isStandalonePayload((event as CustomEvent).detail));
    };
    window.addEventListener(SERVICE_STATUS_EVENT, onStatus);
    return () => window.removeEventListener(SERVICE_STATUS_EVENT, onStatus);
  }, []);

  return standalone;
}
