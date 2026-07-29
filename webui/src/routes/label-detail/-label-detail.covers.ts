/**
 * The visible-first cover loader, ported from label-detail.js's `_pumpCovers`.
 *
 * Covers resolve through a rate-limited external lookup (/api/labels/cover →
 * iTunes → 302 to a CDN). Firing all sixty at once just piles them up behind
 * the server's rate lock and the visible ones finish LAST, so the queue is
 * deliberately narrow and ordered by what is on screen:
 *
 *   - at most two in flight (the vanilla's _COVER_CONCURRENCY)
 *   - a card enters the queue when it scrolls into view, not when it renders
 *   - a resolved url is cached, so filter/sort/ownership re-renders keep the
 *     art instead of re-requesting it
 *   - a MISS is remembered too. Without that, every re-render re-queues the
 *     same dead lookups and the queue never drains.
 *
 * Kept as a plain class rather than a hook: it outlives individual renders,
 * and the component only needs to tell it what became visible.
 */

export const COVER_CONCURRENCY = 2;

/** Swappable for tests — the real one preloads through an <img>. */
export type CoverProbe = (url: string) => Promise<boolean>;

const imageProbe: CoverProbe = (url) =>
  new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve(true);
    image.onerror = () => resolve(false);
    image.src = url;
  });

export class CoverLoader {
  private readonly resolved = new Map<string, string>();
  private readonly attempted = new Set<string>();
  private queue: { key: string; url: string }[] = [];
  private active = 0;
  private disposed = false;

  constructor(
    private readonly onResolved: (key: string, url: string) => void,
    private readonly probe: CoverProbe = imageProbe,
  ) {}

  /** The url to paint for a key, or '' while it is unresolved. */
  urlFor(key: string): string {
    return this.resolved.get(key) ?? '';
  }

  /**
   * A card came into view. Ignored if it is already resolved, already tried,
   * or already queued — the observer fires repeatedly for the same element as
   * it crosses the margin.
   */
  request(key: string, url: string): void {
    if (this.disposed || !key || !url) return;
    if (this.resolved.has(key) || this.attempted.has(key)) return;
    if (this.queue.some((job) => job.key === key)) return;
    this.queue.push({ key, url });
    this.pump();
  }

  /**
   * Drop everything not yet started. Called when the label changes: those
   * covers belong to a catalog nobody is looking at any more, and letting them
   * finish would hold the two slots against the new label's visible cards.
   */
  reset(): void {
    this.queue = [];
    this.resolved.clear();
    this.attempted.clear();
  }

  dispose(): void {
    this.disposed = true;
    this.queue = [];
  }

  private pump(): void {
    while (!this.disposed && this.active < COVER_CONCURRENCY && this.queue.length) {
      const job = this.queue.shift();
      if (!job) return;
      if (this.resolved.has(job.key) || this.attempted.has(job.key)) continue;
      // Marked BEFORE the probe: two observers firing in the same tick would
      // otherwise both pass the guard above and spend both slots on one cover.
      this.attempted.add(job.key);
      this.active += 1;
      void this.probe(job.url).then((ok) => {
        this.active -= 1;
        if (this.disposed) return;
        if (ok) {
          this.resolved.set(job.key, job.url);
          this.onResolved(job.key, job.url);
        }
        this.pump();
      });
    }
  }
}
