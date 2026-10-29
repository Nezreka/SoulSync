import { useEffect } from 'react';

/** Subscribe to a shell frame while mounted, retaining the existing truthy-payload contract.
 *  a null name subscribes to nothing (a frame one side of the app doesn't have). */
export function useShellEvent<T>(name: string | null, onFrame: (frame: T) => void): void {
  useEffect(() => {
    if (!name) return;
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<T>).detail;
      if (detail) onFrame(detail);
    };
    window.addEventListener(name, handler);
    return () => window.removeEventListener(name, handler);
  }, [name, onFrame]);
}
