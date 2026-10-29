import { useCallback, useEffect, useRef } from 'react';

/** Only the latest request for the current album/filter may publish a result. */
export function useRequestScope(scope: string) {
  const selectedScope = useRef(scope);
  selectedScope.current = scope;
  const active = useRef<AbortController | null>(null);
  const cancel = useCallback(() => active.current?.abort(), []);
  const start = useCallback(() => {
    active.current?.abort();
    const controller = new AbortController();
    const requestedScope = selectedScope.current;
    active.current = controller;
    return {
      signal: controller.signal,
      isCurrent: () =>
        !controller.signal.aborted &&
        active.current === controller &&
        selectedScope.current === requestedScope,
      cancel: () => controller.abort(),
    };
  }, []);
  useEffect(() => cancel, [cancel, scope]);
  return { start, cancel };
}
