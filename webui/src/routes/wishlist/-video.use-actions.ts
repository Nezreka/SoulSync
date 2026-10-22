import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import type { VideoEpisode, VideoMedia, VideoMovieItem, VideoShowItem } from './-video.types';

import {
  type VideoActionTarget,
  VIDEO_WISHLIST_QUERY_KEY,
  clearVideoWishlist,
  downloadAllYoutubeWaiting,
  downloadYoutubeNow,
  removeVideoWish,
  removeYoutubeWish,
  retryVideoAllSources,
  searchAllVideoMissing,
  searchVideoNow,
  unfollowYoutubeChannel,
} from './-video.api';
import {
  clearLabel,
  downloadAllToast,
  retryToast,
  searchAllToast,
  searchNowToast,
} from './-video.helpers';
import { MEDIA_KIND } from './-video.types';

const toast = (message: string, type: string) => window.showToast?.(message, type);

/** the event every other video surface (the nav badge, the calendar) listens for. */
export const VIDEO_WISHLIST_CHANGED = 'soulsync:video-wishlist-changed';

function announceChange(): void {
  document.dispatchEvent(new CustomEvent(VIDEO_WISHLIST_CHANGED));
}

/** a stable key per action target, so one row's button can stand down alone. */
export function targetKey(verb: string, t: VideoActionTarget | string): string {
  if (typeof t === 'string') return `${verb}:${t}`;
  return `${verb}:${t.scope}:${t.tmdb_id}:${t.season_number ?? ''}:${t.episode_number ?? ''}`;
}

/**
 * Every write the page makes. Each shows the vanilla page's toast for the same
 * outcome, then refreshes the list and tells the rest of the app (the nav badge,
 * the calendar) the wishlist changed.
 */
export function useVideoWishlistActions(media: VideoMedia) {
  const queryClient = useQueryClient();
  // in flight right now: the button stands down.
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  // a "Search now" that queued something stays down until the list reloads,
  // like the vanilla button did. a reload drops these.
  const [queued, setQueued] = useState<ReadonlySet<string>>(new Set());

  const refresh = useCallback(async () => {
    setQueued(new Set());
    await queryClient.invalidateQueries({ queryKey: VIDEO_WISHLIST_QUERY_KEY });
  }, [queryClient]);

  const run = useCallback(async (key: string, work: () => Promise<void>) => {
    setBusy((prev) => new Set(prev).add(key));
    try {
      await work();
    } finally {
      setBusy((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  }, []);

  const isBusy = (key: string) => busy.has(key) || queued.has(key);

  const searchNow = (target: VideoActionTarget) => {
    const key = targetKey('hunt', target);
    return run(key, async () => {
      try {
        const res = await searchVideoNow(target);
        if (!res || !res.success) throw new Error();
        const t = searchNowToast(res);
        toast(t.message, t.type);
        if (!res.missing_target && res.queued) setQueued((prev) => new Set(prev).add(key));
      } catch {
        toast('Search could not start', 'error');
      }
    });
  };

  const retry = (target: VideoActionTarget) =>
    run(targetKey('retry', target), async () => {
      try {
        const res = await retryVideoAllSources(target);
        if (!res || !res.success) throw new Error();
        const t = retryToast(res);
        toast(t.message, t.type);
        await refresh();
      } catch {
        toast('Retry could not start', 'error');
      }
    });

  const youtubeNow = (show: VideoShowItem, ep: VideoEpisode) =>
    run(targetKey('yt-now', ep.source_id || ''), async () => {
      try {
        const res = await downloadYoutubeNow({
          video_id: ep.source_id || '',
          channel_id: show.youtube_id || null,
          channel_title: show.title || null,
          video_title: ep.title || 'Untitled',
          published_at: ep.air_date || null,
        });
        if (!res || !res.success) throw new Error(res?.error || 'failed');
        toast(res.already ? 'Already downloading' : 'Downloading now', 'success');
        setQueued((prev) => new Set(prev).add(targetKey('yt-now', ep.source_id || '')));
      } catch (err) {
        toast((err as Error)?.message || 'Could not start that download', 'error');
      }
    });

  const bulk = () =>
    run('bulk', async () => {
      if (media === 'youtube') {
        try {
          const res = await downloadAllYoutubeWaiting();
          if (!res || !res.success) throw new Error(res?.error || 'failed');
          const t = downloadAllToast(res);
          toast(t.message, t.type);
          await refresh();
        } catch (err) {
          toast((err as Error)?.message || 'Could not start the downloads', 'error');
        }
        return;
      }
      try {
        const res = await searchAllVideoMissing();
        if (!res || !res.success) throw new Error();
        const t = searchAllToast(res.kinds);
        toast(t.message, t.type);
      } catch {
        toast('Search could not start', 'error');
      }
    });

  const removed = async () => {
    toast('Removed from wishlist', 'info');
    announceChange();
    await refresh();
  };

  /** one movie / episode / season / whole show. bigger scopes confirm first. */
  const remove = (target: VideoActionTarget, confirmText?: string) =>
    run(targetKey('rm', target), async () => {
      if (confirmText) {
        const ok = await window.showConfirmDialog?.({
          title: 'Remove from wishlist',
          message: confirmText,
          confirmText: 'Remove',
          destructive: true,
        });
        if (ok === false) return;
      }
      try {
        const res = await removeVideoWish(target);
        if (!res || !res.success) return;
        await removed();
      } catch {
        /* the button comes back; nothing was removed */
      }
    });

  const removeYoutubeVideos = (key: string, ids: string[], confirmText?: string) =>
    run(targetKey('rm', key), async () => {
      if (!ids.length) return;
      if (confirmText) {
        const ok = await window.showConfirmDialog?.({
          title: 'Remove from wishlist',
          message: confirmText,
          confirmText: 'Remove',
          destructive: true,
        });
        if (ok === false) return;
      }
      try {
        await Promise.all(ids.map((id) => removeYoutubeWish('video', id)));
        await removed();
      } catch {
        /* the button comes back */
      }
    });

  /** the channel's videos come off the wishlist AND the channel is unfollowed. */
  const removeChannel = (show: VideoShowItem) =>
    run(targetKey('rm', `channel:${show.youtube_id}`), async () => {
      const ok = await window.showConfirmDialog?.({
        title: 'Remove channel',
        message: `Unfollow "${show.title}" and remove its ${show.wanted} video${show.wanted === 1 ? '' : 's'} from the wishlist?`,
        confirmText: 'Remove',
        destructive: true,
      });
      if (ok === false) return;
      try {
        await unfollowYoutubeChannel(show.youtube_id || '');
        await removeYoutubeWish('channel', show.youtube_id || '');
        await removed();
      } catch {
        /* the button comes back */
      }
    });

  const clearAll = () =>
    run('clear', async () => {
      const label = clearLabel(media);
      const ok = await window.showConfirmDialog?.({
        title: 'Clear wishlist',
        message: `Remove ALL ${label} from your wishlist? This can’t be undone.`,
        confirmText: 'Clear all',
        cancelText: 'Cancel',
        destructive: true,
      });
      if (ok === false) return;
      try {
        const res = await clearVideoWishlist(MEDIA_KIND[media]);
        if (res && res.success) {
          toast(`Cleared ${res.removed || 0} ${label} from your wishlist`, 'success');
          announceChange();
          await refresh();
        } else {
          toast('Could not clear wishlist', 'error');
        }
      } catch {
        toast('Could not clear wishlist', 'error');
      }
    });

  /** the shared release picker (VideoDownload.manualSearch), as the old page opened it. */
  const pickMovie = (movie: VideoMovieItem) => {
    const ms = window.VideoDownload?.manualSearch;
    if (!ms) {
      toast('Manual search unavailable', 'error');
      return;
    }
    ms({
      title: movie.title || '',
      scope: 'movie',
      year: movie.year || null,
      poster: movie.poster_url || null,
    });
  };

  const pickEpisodes = (show: VideoShowItem, season: number, episode: number | null) => {
    const ms = window.VideoDownload?.manualSearch;
    if (!ms) {
      toast('Manual search unavailable', 'error');
      return;
    }
    ms({
      title: show.title || '',
      scope: episode == null ? 'season' : 'episode',
      season,
      episode,
      year: show.year || null,
      poster: show.poster_url || null,
    });
  };

  return {
    isBusy,
    searchNow,
    retry,
    youtubeNow,
    bulk,
    remove,
    removeYoutubeVideos,
    removeChannel,
    clearAll,
    pickMovie,
    pickEpisodes,
    refresh,
  };
}

export type VideoWishlistActions = ReturnType<typeof useVideoWishlistActions>;
