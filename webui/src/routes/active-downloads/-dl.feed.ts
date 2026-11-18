/**
 * The Downloads page's latest poll, for anyone else who wants the same rows.
 *
 * The page polls /api/downloads/all (music, every 2s) and
 * /api/video/downloads/active (video, 2s active, 6s idle). The help sheet's
 * Downloads answers read the same rows. While the page is mounted it
 * publishes each good poll here and the answers read that instead of
 * polling a second time; when it isn't, they poll on their own
 * (features/help/pages/kit.tsx useFeedOrPoll).
 */

import type { AdlDownloadsResponse } from './-adl.types';
import type { VdlDownload } from './-vdl.types';

export interface FeedSnapshot<T> {
  /** the page is mounted and polling */
  live: boolean;
  /** its last good poll; undefined until the first one lands */
  data: T | undefined;
}

export interface DownloadsFeed<T> {
  /** the page mounted: `refresh` polls now. returns the detach for unmount */
  attach: (refresh: () => void) => () => void;
  publish: (data: T) => void;
  subscribe: (listener: () => void) => () => void;
  snapshot: () => FeedSnapshot<T>;
  /** ask the page for a poll now (after an answer's cancel, say) */
  refresh: () => void;
}

const IDLE = { live: false, data: undefined } as const;

export function createDownloadsFeed<T>(): DownloadsFeed<T> {
  let snap: FeedSnapshot<T> = IDLE;
  const refreshers: (() => void)[] = [];
  const listeners = new Set<() => void>();
  const set = (next: FeedSnapshot<T>) => {
    snap = next;
    for (const listener of listeners) listener();
  };
  return {
    attach(refresh) {
      refreshers.push(refresh);
      if (!snap.live) set({ live: true, data: undefined });
      return () => {
        const at = refreshers.indexOf(refresh);
        if (at >= 0) refreshers.splice(at, 1);
        // nobody polls any more: what's here goes stale, so readers poll themselves
        if (!refreshers.length) set(IDLE);
      };
    },
    publish(data) {
      if (refreshers.length) set({ live: true, data });
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    snapshot: () => snap,
    refresh() {
      refreshers.at(-1)?.();
    },
  };
}

/** the music rows, from useAdlDownloads */
export const musicDownloadsFeed = createDownloadsFeed<AdlDownloadsResponse>();

/** the video rows, from useVideoDownloads */
export const videoDownloadsFeed = createDownloadsFeed<{ downloads?: VdlDownload[] }>();
