/**
 * Downloading a music video from the grid, ported from _downloadMusicVideo
 * (downloads.js:5446-5495).
 *
 * The vanilla drove the card's DOM directly — adding classes, unhiding the ring,
 * writing strokeDashoffset — and re-armed `cardEl.onclick` to retry after a
 * failure. Here the card is a pure function of state, so this hook owns the
 * state and VideoGrid renders it.
 *
 * A profile without download rights never downloads: it files a request
 * (POST /api/requests/music/videos) that an admin approves, and the button
 * reads "Requested" instead of showing a progress ring.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { SearchVideo } from './-search.types';
import type { VideoProgress } from './-ui/video-grid';

/** How often the vanilla asked for progress. */
const POLL_MS = 1000;

interface StatusResponse {
  status?: string;
  progress?: number;
}

interface FileVideoRequestResponse {
  success: boolean;
  error?: string;
  id?: number;
  /** the server kept the earlier ask: the button still reads "Requested". */
  already?: boolean;
  /** the video is already in the library: show the saved state, not an error. */
  in_library?: boolean;
}

export function useVideoDownloads(): {
  progress: Record<string, VideoProgress>;
  download: (video: SearchVideo) => void;
  /** file a request for this video (profiles that ask first). never downloads. */
  request: (video: SearchVideo) => Promise<void>;
  /** video_ids already requested, for the "Requested" button state. */
  requested: Set<string>;
} {
  const [progress, setProgress] = useState<Record<string, VideoProgress>>({});
  const [requested, setRequested] = useState<Set<string>>(new Set());
  const timersRef = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const progressRef = useRef(progress);
  progressRef.current = progress;
  const requestedRef = useRef(requested);
  requestedRef.current = requested;

  // Every poller has to be stopped, or a navigation away leaves intervals
  // hitting the server for a page nobody is looking at.
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const id of Object.keys(timers)) clearInterval(timers[id]);
    };
  }, []);

  const markRequested = useCallback((id: string) => {
    if (requestedRef.current.has(id)) return;
    setRequested((prev) => new Set(prev).add(id));
  }, []);

  const download = useCallback((video: SearchVideo) => {
    const id = String(video.video_id ?? '');
    if (!id) return;

    // A card mid-download or already finished ignores clicks; a FAILED one is
    // clickable again, which is how the vanilla offered a retry.
    const current = progressRef.current[id]?.state;
    if (current === 'downloading' || current === 'completed') return;

    setProgress((prev) => ({ ...prev, [id]: { state: 'downloading', percent: 0 } }));

    void fetch('/api/music-video/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_id: video.video_id,
        url: video.url,
        title: video.title,
        channel: video.channel,
      }),
    })
      .then((response) => {
        if (!response.ok) throw new Error('Download request failed');

        timersRef.current[id] = setInterval(() => {
          void fetch(`/api/music-video/status/${encodeURIComponent(id)}`)
            .then((r) => r.json() as Promise<StatusResponse>)
            .then((status) => {
              if (status.status === 'completed') {
                clearInterval(timersRef.current[id]);
                delete timersRef.current[id];
                setProgress((prev) => ({ ...prev, [id]: { state: 'completed', percent: 100 } }));
                return;
              }
              if (status.status === 'error') {
                clearInterval(timersRef.current[id]);
                delete timersRef.current[id];
                setProgress((prev) => ({ ...prev, [id]: { state: 'errored', percent: 0 } }));
                return;
              }
              // Only a real number moves the ring: the vanilla guarded on
              // `progress > 0` so an absent field could not snap it back to 0.
              const percent = Number(status.progress);
              if (Number.isFinite(percent) && percent > 0) {
                setProgress((prev) => ({ ...prev, [id]: { state: 'downloading', percent } }));
              }
            })
            .catch(() => {
              // A single failed poll is not a failed download — the next tick
              // may well answer. The interval keeps running.
            });
        }, POLL_MS);
      })
      .catch(() => {
        setProgress((prev) => ({ ...prev, [id]: { state: 'errored', percent: 0 } }));
      });
  }, []);

  const request = useCallback(
    async (video: SearchVideo) => {
      const id = String(video.video_id ?? '');
      if (!id || requestedRef.current.has(id)) return;

      // request limit used up: core.js says so instead of a file the server drops
      if (window.checkMusicRequestQuota && !(await window.checkMusicRequestQuota())) return;

      const title = video.title || 'that video';
      let payload: FileVideoRequestResponse | null = null;
      try {
        const response = await fetch('/api/requests/music/videos', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            video_id: video.video_id,
            url: video.url,
            title: video.title,
            channel: video.channel,
            thumbnail_url: video.thumbnail,
          }),
        });
        payload = (await response.json()) as FileVideoRequestResponse;
      } catch {
        window.showToast?.(`Couldn't request "${title}". Try again?`, 'error');
        return;
      }

      if (payload?.success || payload?.already) {
        markRequested(id);
        // a profile that asks first hears who it went to instead
        if (!window.announceWishlistRequest?.()) {
          window.showToast?.(`Requested "${title}"`, 'success');
        }
        return;
      }
      if (payload?.in_library) {
        setProgress((prev) => ({ ...prev, [id]: { state: 'completed', percent: 100 } }));
        window.showToast?.(`"${title}" is already in your library`, 'info');
        return;
      }
      window.showToast?.(payload?.error || `Couldn't request "${title}"`, 'error');
    },
    [markRequested],
  );

  return { progress, download, request, requested };
}
