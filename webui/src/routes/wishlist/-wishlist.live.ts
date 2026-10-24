import { useEffect, useRef, useState } from 'react';

import type { WishlistStatsResponse } from './-wishlist.types';

/**
 * Fired by downloads.js after Cleanup / Clear All, which run in vanilla code
 * and change the wishlist under the page. Without it the orbs keep showing
 * removed tracks until you navigate away and back — the vanilla page used to
 * refresh itself by calling its own initializer, which a React route cannot do.
 */
export const WISHLIST_CHANGED_EVENT = 'ss:wishlist-changed';

/** How often the vanilla page polled while watching a wishlist run. */
const POLL_MS = 5000;

export interface LiveWishlistState {
  /** True while auto-processing or a manual wishlist batch is in flight. */
  processing: boolean;
  /** the running wishlist batch, whoever started it; the page polls its progress. */
  batchId: string | null;
}

async function readProcessing(): Promise<{
  processing: boolean;
  total: number;
  batchId: string | null;
}> {
  let processing = false;
  let total = 0;
  let batchId: string | null = null;

  try {
    const statsResp = await fetch('/api/wishlist/stats');
    if (statsResp.ok) {
      const stats: WishlistStatsResponse = await statsResp.json();
      processing = Boolean(stats.is_auto_processing);
      total = stats.total || 0;
    }
  } catch {
    /* a blip must not tear the page down */
  }

  // A manual batch does not set is_auto_processing, so the active-process list
  // is checked too — the vanilla poller did both and OR'd them. it also names
  // the batch, so the progress strip can follow an auto run as well.
  try {
    const procResp = await fetch('/api/active-processes');
    if (procResp.ok) {
      const data: { active_processes?: { playlist_id?: string; batch_id?: string }[] } =
        await procResp.json();
      const run = (data.active_processes ?? []).find((p) => p.playlist_id === 'wishlist');
      if (run) {
        processing = true;
        batchId = run.batch_id ?? null;
      }
    }
  } catch {
    /* same */
  }

  return { processing, total, batchId };
}

/**
 * Watch a running wishlist download.
 *
 * Two refreshes, mirroring the vanilla poller:
 *  - while processing, a DROP in the total means tracks landed, so re-read
 *  - on the processing -> idle edge, refresh once to settle the final state
 *
 * `onRefresh` is held in a ref so a re-render cannot restart the interval and
 * silently double the poll rate.
 */
export function useLiveWishlist(onRefresh: () => void): LiveWishlistState {
  const [processing, setProcessing] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const lastTotal = useRef<number | null>(null);
  const wasProcessing = useRef(false);
  const refresh = useRef(onRefresh);
  refresh.current = onRefresh;

  // Vanilla-side changes (Cleanup, Clear All) announce themselves rather than
  // repainting a page they no longer own.
  useEffect(() => {
    const onChanged = () => refresh.current();
    window.addEventListener(WISHLIST_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(WISHLIST_CHANGED_EVENT, onChanged);
  }, []);

  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      const { processing: active, total, batchId: runId } = await readProcessing();
      if (cancelled) return;

      setProcessing(active);
      setBatchId(runId);

      if (active) {
        if (lastTotal.current !== null && total < lastTotal.current) refresh.current();
        lastTotal.current = total;
      } else if (wasProcessing.current) {
        // Settle once on the falling edge, not on every idle tick.
        lastTotal.current = null;
        refresh.current();
      }
      wasProcessing.current = active;
    };

    const timer = setInterval(() => void tick(), POLL_MS);
    void tick();

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return { processing, batchId };
}
