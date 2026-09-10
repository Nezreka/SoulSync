import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  addPodcastToWatchlist,
  downloadPodcastEpisode,
  fetchPodcastDownloads,
  fetchWatchlistPodcasts,
  removePodcastFromWatchlist,
} from '../-podcasts.api';
import type {
  ActivePlaybackState,
  PodcastDownloadItem,
  PodcastEpisodeItem,
  PodcastShowDetail,
  PodcastShowSummary,
} from '../-podcasts.types';

export interface PodcastContextValue {
  // Playback
  activePlayback: ActivePlaybackState | null;
  handlePlayEpisode: (ep: PodcastEpisodeItem, show: PodcastShowDetail) => void;
  handleTogglePlay: () => void;
  closePlayer: () => void;
  updateProgress: (cur: number, dur: number) => void;

  // Downloads
  downloads: Record<string, PodcastDownloadItem>;
  handleDownloadEpisode: (ep: PodcastEpisodeItem, showTitle: string, showArtwork?: string | null) => void;
  downloadsCount: number;

  // Watchlist
  isWatchingShow: (show: { feed_url?: string | null; itunes_id?: number | null }) => boolean;
  toggleWatchlist: (show: PodcastShowSummary | PodcastShowDetail) => Promise<boolean>;
  isWatchlistBusy: (show: { feed_url?: string | null; itunes_id?: number | null }) => boolean;
  watchlistPodcasts: any[];
  refreshWatchlist: () => Promise<void>;
}

const PodcastContext = createContext<PodcastContextValue | null>(null);

export function usePodcastContext(): PodcastContextValue {
  const ctx = useContext(PodcastContext);
  if (!ctx) throw new Error('usePodcastContext must be used within PodcastProvider');
  return ctx;
}

export function PodcastProvider({ children }: { children: ReactNode }) {
  const [activePlayback, setActivePlayback] = useState<ActivePlaybackState | null>(null);
  const [downloads, setDownloads] = useState<Record<string, PodcastDownloadItem>>({});
  const [watchlist, setWatchlist] = useState<any[]>([]);
  const [watchlistBusyMap, setWatchlistBusyMap] = useState<Record<string, boolean>>({});

  const refreshWatchlist = useCallback(async () => {
    try {
      const items = await fetchWatchlistPodcasts();
      setWatchlist(items);
    } catch {
      // non-fatal
    }
  }, []);

  useEffect(() => {
    void refreshWatchlist();
  }, [refreshWatchlist]);

  const watchlistKeys = useMemo(() => {
    const set = new Set<string>();
    for (const item of watchlist) {
      if (item.feed_url) set.add(item.feed_url.trim().toLowerCase());
      if (item.itunes_id) set.add(String(item.itunes_id));
    }
    return set;
  }, [watchlist]);

  const getShowKey = (item: { feed_url?: string | null; itunes_id?: number | null }): string => {
    return (item.feed_url && item.feed_url.trim()) || (item.itunes_id ? String(item.itunes_id) : '');
  };

  const isWatchingShow = useCallback(
    (show: { feed_url?: string | null; itunes_id?: number | null }): boolean => {
      if (show.feed_url && watchlistKeys.has(show.feed_url.trim().toLowerCase())) return true;
      if (show.itunes_id && watchlistKeys.has(String(show.itunes_id))) return true;
      return false;
    },
    [watchlistKeys],
  );

  const isShowWatchlistBusy = useCallback(
    (show: { feed_url?: string | null; itunes_id?: number | null }): boolean => {
      const key = getShowKey(show);
      return Boolean(key && watchlistBusyMap[key]);
    },
    [watchlistBusyMap],
  );

  const toggleWatchlist = useCallback(
    async (show: PodcastShowSummary | PodcastShowDetail): Promise<boolean> => {
      const key = getShowKey(show);
      if (!key || watchlistBusyMap[key]) return false;

      setWatchlistBusyMap((prev) => ({ ...prev, [key]: true }));
      const currentlyWatching = isWatchingShow(show);

      try {
        if (currentlyWatching) {
          setWatchlist((prev) =>
            prev.filter((p) => {
              const feedMatch =
                show.feed_url &&
                p.feed_url &&
                p.feed_url.trim().toLowerCase() === show.feed_url.trim().toLowerCase();
              const idMatch = show.itunes_id && String(p.itunes_id) === String(show.itunes_id);
              return !feedMatch && !idMatch;
            }),
          );
          const res = await removePodcastFromWatchlist(show.feed_url, show.itunes_id);
          if (res.success || !res.isWatching) {
            window.showToast?.(`Removed "${show.title}" from Watchlist`, 'info');
          } else {
            await refreshWatchlist();
            window.showToast?.('Could not remove from Watchlist', 'error');
          }
        } else {
          setWatchlist((prev) => [
            ...prev,
            {
              feed_url: show.feed_url,
              itunes_id: show.itunes_id,
              title: show.title,
              artwork_url: show.artwork_url,
              author: show.author,
              description: show.description,
              episode_count: show.episode_count,
            },
          ]);
          const res = await addPodcastToWatchlist(show);
          if (res.success || res.isWatching) {
            window.showToast?.(`Added "${show.title}" to Watchlist`, 'success');
          } else {
            await refreshWatchlist();
            window.showToast?.('Could not add to Watchlist', 'error');
          }
        }

        try {
          window.updateWatchlistButtonCount?.();
        } catch {
          // non-fatal
        }
        return true;
      } catch {
        await refreshWatchlist();
        window.showToast?.('Failed to update Watchlist', 'error');
        return false;
      } finally {
        setWatchlistBusyMap((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
      }
    },
    [isWatchingShow, refreshWatchlist, watchlistBusyMap],
  );

  // Poll downloads if any are active
  useEffect(() => {
    const poll = () => {
      void fetchPodcastDownloads()
        .then((items) => {
          const map: Record<string, PodcastDownloadItem> = {};
          for (const it of items) {
            map[it.download_id] = it;
          }
          setDownloads(map);
        })
        .catch(() => {});
    };

    poll();
    const interval = setInterval(poll, 3500);
    return () => clearInterval(interval);
  }, []);

  const handlePlayEpisode = (ep: PodcastEpisodeItem, show: PodcastShowDetail) => {
    if (activePlayback?.episode.guid === ep.guid) {
      setActivePlayback((prev) => (prev ? { ...prev, isPlaying: !prev.isPlaying } : null));
    } else {
      setActivePlayback({
        episode: ep,
        showTitle: show.title,
        showArtwork: show.artwork_url,
        isPlaying: true,
        currentTime: 0,
        duration: ep.duration_seconds || 0,
        playbackRate: 1,
      });
    }
  };

  const handleTogglePlay = () => {
    setActivePlayback((prev) => (prev ? { ...prev, isPlaying: !prev.isPlaying } : null));
  };

  const closePlayer = () => {
    setActivePlayback(null);
  };

  const updateProgress = (cur: number, dur: number) => {
    setActivePlayback((prev) =>
      prev ? { ...prev, currentTime: cur, duration: dur } : null,
    );
  };

  const handleDownloadEpisode = async (
    ep: PodcastEpisodeItem,
    showTitle: string,
    showArtwork?: string | null,
  ) => {
    if (!ep.enclosure_url) return;

    const tempId = `temp-${Date.now()}-${ep.guid || ep.title}`;
    setDownloads((prev) => ({
      ...prev,
      [tempId]: {
        download_id: tempId,
        title: ep.title,
        show_title: showTitle,
        artwork_url: ep.artwork_url || showArtwork || undefined,
        enclosure_url: ep.enclosure_url,
        duration_seconds: ep.duration_seconds,
        status: 'queued',
        progress_bytes: 0,
        total_bytes: 0,
        percent: 0,
        started_at: Date.now() / 1000,
      },
    }));

    try {
      const res = await downloadPodcastEpisode(ep, showTitle);
      if (res.success && res.download_id) {
        setDownloads((prev) => {
          const next = { ...prev };
          delete next[tempId];
          next[res.download_id!] = {
            download_id: res.download_id!,
            title: ep.title,
            show_title: showTitle,
            artwork_url: ep.artwork_url || showArtwork || undefined,
            enclosure_url: ep.enclosure_url,
            duration_seconds: ep.duration_seconds,
            status: 'queued',
            progress_bytes: 0,
            total_bytes: 0,
            percent: 0,
            started_at: Date.now() / 1000,
          };
          return next;
        });
        window.showToast?.(`Downloading "${ep.title}"`, 'info');
      } else {
        setDownloads((prev) => {
          const next = { ...prev };
          delete next[tempId];
          return next;
        });
        window.showToast?.(res.error || 'Failed to start download', 'error');
      }
    } catch {
      setDownloads((prev) => {
        const next = { ...prev };
        delete next[tempId];
        return next;
      });
      window.showToast?.('Failed to start episode download', 'error');
    }
  };

  const downloadsCount = Object.values(downloads).filter((d) => d.status === 'completed').length;

  return (
    <PodcastContext.Provider
      value={{
        activePlayback,
        handlePlayEpisode,
        handleTogglePlay,
        closePlayer,
        updateProgress,
        downloads,
        handleDownloadEpisode,
        downloadsCount,
        isWatchingShow,
        toggleWatchlist,
        isWatchlistBusy: isShowWatchlistBusy,
        watchlistPodcasts: watchlist,
        refreshWatchlist,
      }}
    >
      {children}
    </PodcastContext.Provider>
  );
}
