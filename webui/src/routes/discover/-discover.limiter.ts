/**
 * A bounded-concurrency gate for the discover page's shelf requests.
 *
 * ── Why this exists, and why the browser is NOT enough ──────────────────────
 *
 * The vanilla ran its ~20 section loaders through a hand-rolled 5-at-a-time
 * pool (`_runLoadersLimited`). It is tempting to drop that on the grounds that
 * browsers already cap connections per origin — and that reasoning is wrong.
 * The vanilla's own comment says what the pool is actually for:
 *
 *     ~20 heavy DB/consensus queries contend on the backend (Flask + GIL) and
 *     each ends up slow — the page took tens of seconds to become usable
 *
 * The limit protects the SERVER, not the client. SoulSync's backend is Flask
 * under the GIL, so twenty simultaneous heavy queries do not run twenty times
 * faster — they interleave and every one of them gets slower. The browser's
 * per-origin cap (~6 on HTTP/1.1) only helps by accident, and on HTTP/2 it
 * does not help at all: every request is multiplexed down one connection and
 * arrives at Flask together, which is precisely the situation the pool existed
 * to prevent.
 *
 * So the cap is reproduced here rather than discarded. react-query still owns
 * caching, retries and lifecycle; this only decides WHEN a request is allowed
 * to leave.
 */

/** The vanilla's pool size. Same number, same reason. */
export const DISCOVER_REQUEST_LIMIT = 5;

export interface Limiter {
  /** Run `fn` once a slot is free. Resolves/rejects with `fn`'s result. */
  run: <T>(fn: () => Promise<T>) => Promise<T>;
  /** In-flight count — for tests and diagnostics. */
  readonly active: number;
  /** Queued-but-not-started count. */
  readonly pending: number;
  /**
   * Drop everything still queued and zero the in-flight count.
   *
   * For TESTS. `discoverLimiter` is a module-level singleton — correct in the
   * app, where one page shares one budget, but it means queued work outlives an
   * individual test: the test ends, MSW tears its handlers down, and whatever
   * this queue still held then fires into nothing. Reset between tests.
   *
   * Queued operations are dropped, never started, so their promises stay
   * pending forever — which is fine for a torn-down test and is why this is not
   * something to reach for in app code.
   */
  reset: () => void;
}

/**
 * Create a limiter allowing `limit` concurrent operations.
 *
 * A slot is released in `finally`, so a rejected operation frees its slot like
 * any other — the vanilla's pool had the same property via try/catch, and
 * without it one failing shelf would permanently shrink the pool.
 */
export function createLimiter(limit: number = DISCOVER_REQUEST_LIMIT): Limiter {
  let active = 0;
  const queue: (() => void)[] = [];

  const release = () => {
    active--;
    const next = queue.shift();
    if (next) next();
  };

  return {
    get active() {
      return active;
    },
    get pending() {
      return queue.length;
    },
    reset() {
      queue.length = 0;
      active = 0;
    },
    run<T>(fn: () => Promise<T>): Promise<T> {
      const start = async (): Promise<T> => {
        active++;
        try {
          return await fn();
        } finally {
          release();
        }
      };
      if (active < limit) return start();
      return new Promise<T>((resolve, reject) => {
        queue.push(() => {
          start().then(resolve, reject);
        });
      });
    },
  };
}

/**
 * The page-wide limiter.
 *
 * Module-level so every shelf on the page shares one budget, which is the
 * whole point — a per-query limiter would let 20 queries run 20 requests.
 */
export const discoverLimiter = createLimiter();
