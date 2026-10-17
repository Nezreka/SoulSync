import { useEffect } from 'react';

/** Subscribe to a shell frame while mounted, retaining the existing truthy-payload contract. */
export function useShellEvent<T>(name: string, onFrame: (frame: T) => void): void {
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<T>).detail;
      if (detail) onFrame(detail);
    };
    window.addEventListener(name, handler);
    return () => window.removeEventListener(name, handler);
  }, [name, onFrame]);
}
