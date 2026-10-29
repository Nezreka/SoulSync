import { fetchLibraryV2ArtworkStatus } from '../-library-v2.api';

/** Delivery of a background-built cover to an already-rendered page (rev25-02).
 *
 *  The cold artwork path answers `404` + `X-Artwork-Pending` and resolves the
 *  image off the request thread. An `<img>` cannot read that header, and the
 *  client's previous answer — three fixed retries totalling 14.5s — regularly
 *  expired before a cold provider walk plus download plus two JPEG encodes had
 *  finished, especially with a screenful of covers serialising behind a handful
 *  of workers. A resolvable cover could then stay a placeholder until the next
 *  full page load.
 *
 *  So the wait is driven by the server instead of a constant: every failed
 *  local cover registers here, and one batched poll per tick asks what actually
 *  happened. `ready` hands back the cache-bust version to render, `unavailable`
 *  ends the wait decisively (nothing is in flight and nothing is cached), and
 *  `pending` keeps waiting with a widening interval. One request per tick for
 *  the whole page, instead of four blind retries per image.
 *
 *  Deliberately *not* here: re-scheduling a walk for an `unavailable` entity.
 *  Repeatedly re-walking entities that have no resolvable image is Finding 10's
 *  negative-cache question, which stays deferred.
 */

export type ArtworkKind = 'artist' | 'album';
/** `version` for a finished build, `null` when nothing will ever arrive. */
type Settled = (version: number | null) => void;

const POLL_START_MS = 1500;
const POLL_MAX_MS = 15000;
const POLL_GROWTH = 1.6;
// A page left open must not poll forever for something the server keeps
// calling pending (a wedged build, a saturated queue). Roughly three minutes
// at the widening interval, then the placeholder is final until the next
// render.
const MAX_TICKS = 25;

const watchers = new Map<string, Set<Settled>>();
let timer: ReturnType<typeof setTimeout> | null = null;
let delay = POLL_START_MS;
let ticks = 0;

function key(kind: ArtworkKind, id: number): string {
  return `${kind}:${id}`;
}

function settle(entryKey: string, version: number | null): void {
  const listeners = watchers.get(entryKey);
  if (!listeners) return;
  watchers.delete(entryKey);
  for (const listener of listeners) {
    try {
      listener(version);
    } catch {
      // A subscriber that throws must not take the whole poll loop with it.
    }
  }
}

function stop(): void {
  if (timer) clearTimeout(timer);
  timer = null;
  delay = POLL_START_MS;
  ticks = 0;
}

function schedule(): void {
  if (timer || watchers.size === 0) return;
  timer = setTimeout(() => {
    timer = null;
    void poll();
  }, delay);
}

async function poll(): Promise<void> {
  if (watchers.size === 0) {
    stop();
    return;
  }
  ticks += 1;
  const byKind = new Map<ArtworkKind, number[]>();
  for (const entryKey of watchers.keys()) {
    const [kind, rawId] = entryKey.split(':');
    const id = Number(rawId);
    if (!Number.isFinite(id)) continue;
    const bucket = byKind.get(kind as ArtworkKind);
    if (bucket) bucket.push(id);
    else byKind.set(kind as ArtworkKind, [id]);
  }

  await Promise.all(
    [...byKind].map(async ([kind, ids]) => {
      let states: Awaited<ReturnType<typeof fetchLibraryV2ArtworkStatus>>;
      try {
        states = await fetchLibraryV2ArtworkStatus(kind, ids);
      } catch {
        // Transient failure: keep waiting rather than nailing every cover on
        // the page to its placeholder because one request lost the network.
        return;
      }
      for (const id of ids) {
        const state = states[String(id)];
        if (!state) continue;
        if (state.state === 'ready') settle(key(kind, id), state.version);
        else if (state.state === 'unavailable') settle(key(kind, id), null);
      }
    }),
  );

  if (watchers.size === 0) {
    stop();
    return;
  }
  if (ticks >= MAX_TICKS) {
    // Snapshot first: settle() deletes from the map it is iterating.
    for (const entryKey of Array.from(watchers.keys())) settle(entryKey, null);
    stop();
    return;
  }
  delay = Math.min(POLL_MAX_MS, Math.round(delay * POLL_GROWTH));
  schedule();
}

/** Wait for one entity's background artwork build. Returns an unsubscribe. */
export function watchPendingArtwork(kind: ArtworkKind, id: number, onSettled: Settled): () => void {
  const entryKey = key(kind, id);
  const listeners = watchers.get(entryKey) ?? new Set<Settled>();
  listeners.add(onSettled);
  watchers.set(entryKey, listeners);
  // A newly mounted page is the strongest hint that a build just started, so
  // the interval restarts tight rather than inheriting a widened one.
  delay = POLL_START_MS;
  ticks = 0;
  schedule();
  return () => {
    const current = watchers.get(entryKey);
    if (!current) return;
    current.delete(onSettled);
    if (current.size === 0) watchers.delete(entryKey);
    if (watchers.size === 0) stop();
  };
}

/** `/api/library/v2/artwork/<kind>/<id>` → the entity it points at. */
export function parseArtworkTarget(src: string): { kind: ArtworkKind; id: number } | null {
  const match = /\/api\/library\/v2\/artwork\/(artist|album)\/(\d+)/.exec(src || '');
  if (!match) return null;
  const id = Number(match[2]);
  return Number.isFinite(id) && id > 0 ? { kind: match[1] as ArtworkKind, id } : null;
}

/** Test seam: drop every subscription and the pending timer. */
export function resetPendingArtworkWatchers(): void {
  watchers.clear();
  stop();
}
