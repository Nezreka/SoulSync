import { useEffect, useMemo, useState } from 'react';

import type { PodcastEpisodeItem, PodcastShowDetail } from '../-podcasts.types';

import { fetchPodcastShow } from '../-podcasts.api';
import { usePodcastContext } from './podcast-context';
import { PodcastBillboard } from './podcast-billboard';
import { PodcastEpisodeList } from './podcast-episode-list';
import { PodcastSeasonTabs } from './podcast-season-tabs';
import { PodcastShowNotesModal } from './podcast-show-notes-modal';
import styles from './podcasts-page.module.css';

interface PodcastDetailPageProps {
  podcastId: string;
}

export function PodcastDetailPage({ podcastId }: PodcastDetailPageProps) {
  const {
    activePlayback,
    handlePlayEpisode,
    handleDownloadEpisode,
    downloads,
  } = usePodcastContext();

  const [show, setShow] = useState<PodcastShowDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedSeason, setSelectedSeason] = useState<number | null>(null);
  const [sortOrder, setSortOrder] = useState<'newest' | 'oldest'>('newest');
  const [episodeSearchFilter, setEpisodeSearchFilter] = useState('');
  const [selectedEpisodeForNotes, setSelectedEpisodeForNotes] = useState<PodcastEpisodeItem | null>(
    null,
  );

  // Load show details based on podcastId
  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setShow(null);
    setSelectedSeason(null);
    setEpisodeSearchFilter('');

    // podcastId can be an iTunes ID (numeric) or a base64-encoded feed URL
    const itunesId = /^\d+$/.test(podcastId) ? Number(podcastId) : null;
    const feedUrl = itunesId ? null : decodeURIComponent(podcastId);

    fetchPodcastShow(feedUrl, itunesId)
      .then((detail) => {
        if (!cancelled) {
          setShow(detail);
          setIsLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [podcastId]);

  // Derive unique seasons
  const availableSeasons = useMemo(() => {
    if (!show?.episodes) return [];
    const set = new Set<number>();
    for (const ep of show.episodes) {
      if (ep.season != null && ep.season > 0) {
        set.add(ep.season);
      }
    }
    return Array.from(set).sort((a, b) => a - b);
  }, [show?.episodes]);

  // Filter and sort episodes
  const processedEpisodes = useMemo(() => {
    if (!show?.episodes) return [];
    let list = [...show.episodes];

    if (selectedSeason != null) {
      list = list.filter((ep) => ep.season === selectedSeason);
    }

    if (episodeSearchFilter.trim()) {
      const q = episodeSearchFilter.toLowerCase();
      list = list.filter(
        (ep) =>
          (ep.title && ep.title.toLowerCase().includes(q)) ||
          (ep.description && ep.description.toLowerCase().includes(q)),
      );
    }

    list.sort((a, b) => {
      const dateA = a.pub_date ? new Date(a.pub_date).getTime() : 0;
      const dateB = b.pub_date ? new Date(b.pub_date).getTime() : 0;
      return sortOrder === 'newest' ? dateB - dateA : dateA - dateB;
    });

    return list;
  }, [show?.episodes, selectedSeason, episodeSearchFilter, sortOrder]);

  const onPlayEpisode = (ep: PodcastEpisodeItem) => {
    if (!show) return;
    handlePlayEpisode(ep, show);
  };

  const onDownloadEpisode = (ep: PodcastEpisodeItem) => {
    if (!show) return;
    handleDownloadEpisode(ep, show.title, show.artwork_url);
  };

  if (isLoading) {
    return (
      <div className={styles.loadingContainer}>
        <div className={styles.spinner} />
        <p>Loading show and episodes…</p>
      </div>
    );
  }

  if (!show) {
    return (
      <div className={styles.emptyContainer}>
        <span style={{ fontSize: 36 }}>🎙️</span>
        <p>Podcast not found. It may have been removed or the feed may be unavailable.</p>
      </div>
    );
  }

  return (
    <>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24, width: '100%' }}>
        <PodcastBillboard
          show={show}
          onPlayEpisode={onPlayEpisode}
        />

        <PodcastSeasonTabs
          seasons={availableSeasons}
          selectedSeason={selectedSeason}
          onSelectSeason={setSelectedSeason}
          sortOrder={sortOrder}
          onToggleSort={() => setSortOrder(sortOrder === 'newest' ? 'oldest' : 'newest')}
          searchFilter={episodeSearchFilter}
          onSearchFilterChange={setEpisodeSearchFilter}
          totalEpisodes={show.episodes?.length || 0}
          filteredCount={processedEpisodes.length}
        />

        <PodcastEpisodeList
          episodes={processedEpisodes}
          showArtwork={show.artwork_url}
          activeEpisodeGuid={activePlayback?.episode.guid}
          isPlaying={activePlayback?.isPlaying}
          onPlayEpisode={onPlayEpisode}
          onDownloadEpisode={onDownloadEpisode}
          onOpenShowNotes={(ep) => setSelectedEpisodeForNotes(ep)}
          downloads={downloads}
        />
      </div>

      {selectedEpisodeForNotes && (
        <PodcastShowNotesModal
          episode={selectedEpisodeForNotes}
          showTitle={show.title}
          showArtwork={show.artwork_url}
          downloads={downloads}
          onClose={() => setSelectedEpisodeForNotes(null)}
          onPlayEpisode={onPlayEpisode}
          onDownloadEpisode={onDownloadEpisode}
        />
      )}
    </>
  );
}
