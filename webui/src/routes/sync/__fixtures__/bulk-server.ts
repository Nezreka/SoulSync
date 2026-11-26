import { vi } from 'vitest';

/** let every pending promise run */
export async function flush() {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

/** a pipeline server whose playlists finish only when the test says so */
export function gatedServer(fail: number[] = []) {
  const gates: Record<number, () => void> = {};
  const finished = new Set<number>();
  return {
    release: async (id: number) => {
      finished.add(id);
      gates[id]?.();
      await flush();
    },
    deps: {
      start: vi.fn(async () => ({})),
      status: vi.fn(async (id: number) => {
        if (!finished.has(id)) await new Promise<void>((r) => (gates[id] = r));
        return { status: fail.includes(id) ? 'error' : 'finished' } as never;
      }),
      wait: async () => {},
      maxPolls: 5,
    },
  };
}
