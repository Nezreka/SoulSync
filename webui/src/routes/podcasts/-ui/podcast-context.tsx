import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

import {
  downloadPodcastEpisode,
  fetchPodcastDownloads,
} from '../-podcasts.api';
import type {
  ActivePlaybackState,
  PodcastDownloadItem,
  PodcastEpisodeItem,
  PodcastShowDetail,
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
      }}
    >
      {children}
    </PodcastContext.Provider>
  );
}
