import { useEffect, useMemo, useState } from 'react';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import type {
  ActivePlaybackState,
  PodcastDownloadItem,
  PodcastEpisodeItem,
  PodcastShowDetail,
  PodcastShowSummary,
} from '../-podcasts.types';

import {
  downloadPodcastEpisode,
  fetchFeaturedPodcasts,
  fetchPodcastDownloads,
  fetchPodcastShow,
  searchPodcasts,
} from '../-podcasts.api';
import { PodcastBillboard } from './podcast-billboard';
import { PodcastCategoryModal } from './podcast-category-modal';
import { PodcastCategoryPills } from './podcast-category-pills';
import { PodcastEpisodeList } from './podcast-episode-list';
import { PodcastGrid } from './podcast-grid';
import { PodcastPlayerBar } from './podcast-player-bar';
import { PodcastSearchBar } from './podcast-search-bar';
import { PodcastSeasonTabs } from './podcast-season-tabs';
import { PodcastShowNotesModal } from './podcast-show-notes-modal';
import { PodcastSpotlight } from './podcast-spotlight';
import styles from './podcasts-page.module.css';

export function PodcastsPage() {
  useReactPageShell('podcasts');

  // Search & Category state
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('Trending');
  const [searchResults, setSearchResults] = useState<PodcastShowSummary[]>([]);
  const [featuredShows, setFeaturedShows] = useState<PodcastShowSummary[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isLoadingFeatured, setIsLoadingFeatured] = useState(false);

  // Detail view state
  const [selectedShow, setSelectedShow] = useState<PodcastShowDetail | null>(null);
  const [isLoadingShow, setIsLoadingShow] = useState(false);
  const [selectedSeason, setSelectedSeason] = useState<number | null>(null);
  const [sortOrder, setSortOrder] = useState<'newest' | 'oldest'>('newest');
  const [episodeSearchFilter, setEpisodeSearchFilter] = useState('');
  const [selectedEpisodeForNotes, setSelectedEpisodeForNotes] = useState<PodcastEpisodeItem | null>(
    null,
  );
  const [isCategoryModalOpen, setIsCategoryModalOpen] = useState(false);

  // Playback & Download state
  const [activePlayback, setActivePlayback] = useState<ActivePlaybackState | null>(null);
  const [downloads, setDownloads] = useState<Record<string, PodcastDownloadItem>>({});

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(searchQuery.trim());
    }, 350);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Fetch featured shows when category changes and query is empty
  useEffect(() => {
    if (debouncedQuery) return;

    let cancelled = false;
    setIsLoadingFeatured(true);

    fetchFeaturedPodcasts(selectedCategory)
      .then((shows) => {
        if (!cancelled) {
          setFeaturedShows(shows);
          setIsLoadingFeatured(false);
        }
      })
      .catch(() => {
        if (!cancelled) setIsLoadingFeatured(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedCategory, debouncedQuery]);

  // Perform search when debounced query changes
  useEffect(() => {
    if (!debouncedQuery) {
      setSearchResults([]);
      setIsSearching(false);
      return;
    }

    let cancelled = false;
    setIsSearching(true);

    searchPodcasts(debouncedQuery)
      .then((results) => {
        if (!cancelled) {
          setSearchResults(results);
          setIsSearching(false);
        }
      })
      .catch(() => {
        if (!cancelled) setIsSearching(false);
      });

    return () => {
      cancelled = true;
    };
  }, [debouncedQuery]);

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

  // Show selection handler
  const handleSelectShow = async (summary: PodcastShowSummary) => {
    setIsLoadingShow(true);
    setSelectedSeason(null);
    setEpisodeSearchFilter('');

    const detail = await fetchPodcastShow(summary.feed_url, summary.itunes_id);
    if (detail) {
      setSelectedShow(detail);
    } else {
      // Fallback: show summary with empty episode list
      setSelectedShow({
        ...summary,
        episodes: [],
      });
    }
    setIsLoadingShow(false);
  };

  const handleBackToBrowse = () => {
    setSelectedShow(null);
  };

  const handlePlayEpisode = (ep: PodcastEpisodeItem) => {
    if (!selectedShow) return;

    if (activePlayback?.episode.guid === ep.guid) {
      setActivePlayback((prev) => (prev ? { ...prev, isPlaying: !prev.isPlaying } : null));
    } else {
      setActivePlayback({
        episode: ep,
        showTitle: selectedShow.title,
        showArtwork: selectedShow.artwork_url,
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

  const handleDownloadEpisode = async (ep: PodcastEpisodeItem) => {
    if (!selectedShow || !ep.enclosure_url) return;

    // Instant optimistic record so the button immediately transitions to saving/queued spinner
    const tempId = `temp-${Date.now()}-${ep.guid || ep.title}`;
    setDownloads((prev) => ({
      ...prev,
      [tempId]: {
        download_id: tempId,
        title: ep.title,
        show_title: selectedShow.title,
        artwork_url: ep.artwork_url || selectedShow.artwork_url || undefined,
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
      const res = await downloadPodcastEpisode(ep, selectedShow.title);
      if (res.success && res.download_id) {
        setDownloads((prev) => {
          const next = { ...prev };
          delete next[tempId];
          next[res.download_id!] = {
            download_id: res.download_id!,
            title: ep.title,
            show_title: selectedShow.title,
            artwork_url: ep.artwork_url || selectedShow.artwork_url || undefined,
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
        // Rollback optimistic state on error
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

  // Derive unique seasons from selected show
  const availableSeasons = useMemo(() => {
    if (!selectedShow?.episodes) return [];
    const set = new Set<number>();
    for (const ep of selectedShow.episodes) {
      if (ep.season != null && ep.season > 0) {
        set.add(ep.season);
      }
    }
    return Array.from(set).sort((a, b) => a - b);
  }, [selectedShow?.episodes]);

  // Filter and sort episodes for detail view
  const processedEpisodes = useMemo(() => {
    if (!selectedShow?.episodes) return [];
    let list = [...selectedShow.episodes];

    // Filter by season
    if (selectedSeason != null) {
      list = list.filter((ep) => ep.season === selectedSeason);
    }

    // Filter by search text
    if (episodeSearchFilter.trim()) {
      const q = episodeSearchFilter.toLowerCase();
      list = list.filter(
        (ep) =>
          (ep.title && ep.title.toLowerCase().includes(q)) ||
          (ep.description && ep.description.toLowerCase().includes(q)),
      );
    }

    // Sort order
    list.sort((a, b) => {
      const dateA = a.pub_date ? new Date(a.pub_date).getTime() : 0;
      const dateB = b.pub_date ? new Date(b.pub_date).getTime() : 0;
      return sortOrder === 'newest' ? dateB - dateA : dateA - dateB;
    });

    return list;
  }, [selectedShow?.episodes, selectedSeason, episodeSearchFilter, sortOrder]);

  const downloadsCount = Object.values(downloads).filter((d) => d.status === 'completed').length;

  return (
    <div className={`page-shell ${styles.podcastsContainer}`}>
      {/* Top Search & Navigation */}
      <PodcastSearchBar
        value={searchQuery}
        onChange={setSearchQuery}
        onClear={() => setSearchQuery('')}
        isSearching={isSearching}
        downloadsCount={downloadsCount}
        onOpenDownloads={() => {
          // If search is active, clear to show browse
          if (selectedShow) setSelectedShow(null);
        }}
      />

      {/* Main View Area */}
      {selectedShow ? (
        // Detail View (Billboard + Episodes)
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24, width: '100%' }}>
          {isLoadingShow ? (
            <div className={styles.loadingContainer}>
              <div className={styles.spinner} />
              <p>Loading show and episodes…</p>
            </div>
          ) : (
            <>
              <PodcastBillboard
                show={selectedShow}
                onBack={handleBackToBrowse}
                onPlayEpisode={handlePlayEpisode}
              />

              <PodcastSeasonTabs
                seasons={availableSeasons}
                selectedSeason={selectedSeason}
                onSelectSeason={setSelectedSeason}
                sortOrder={sortOrder}
                onToggleSort={() => setSortOrder(sortOrder === 'newest' ? 'oldest' : 'newest')}
                searchFilter={episodeSearchFilter}
                onSearchFilterChange={setEpisodeSearchFilter}
                totalEpisodes={selectedShow.episodes?.length || 0}
                filteredCount={processedEpisodes.length}
              />

              <PodcastEpisodeList
                episodes={processedEpisodes}
                showArtwork={selectedShow.artwork_url}
                activeEpisodeGuid={activePlayback?.episode.guid}
                isPlaying={activePlayback?.isPlaying}
                onPlayEpisode={handlePlayEpisode}
                onDownloadEpisode={handleDownloadEpisode}
                onOpenShowNotes={(ep) => setSelectedEpisodeForNotes(ep)}
                downloads={downloads}
              />
            </>
          )}
        </div>
      ) : (
        // Browse / Search View
        <>
          {!debouncedQuery && (
            <PodcastCategoryPills
              selectedCategory={selectedCategory}
              onSelectCategory={(cat) => setSelectedCategory(cat)}
              onOpenCategoryModal={() => setIsCategoryModalOpen(true)}
            />
          )}

          {!debouncedQuery && featuredShows.length > 0 && (
            <PodcastSpotlight shows={featuredShows.slice(0, 5)} onSelectShow={handleSelectShow} />
          )}

          {debouncedQuery ? (
            <PodcastGrid
              title={`Search Results for "${debouncedQuery}"`}
              shows={searchResults}
              isLoading={isSearching}
              onSelectShow={handleSelectShow}
            />
          ) : (
            <PodcastGrid
              title={`${selectedCategory} Podcasts`}
              shows={featuredShows}
              isLoading={isLoadingFeatured}
              onSelectShow={handleSelectShow}
            />
          )}
        </>
      )}

      {/* Floating Audio Player Bar */}
      {activePlayback && (
        <PodcastPlayerBar
          playback={activePlayback}
          onTogglePlay={handleTogglePlay}
          onClose={() => setActivePlayback(null)}
          onUpdateProgress={(cur, dur) => {
            setActivePlayback((prev) =>
              prev ? { ...prev, currentTime: cur, duration: dur } : null,
            );
          }}
        />
      )}

      {/* Show Notes & Transcript Drawer */}
      {selectedEpisodeForNotes && (
        <PodcastShowNotesModal
          episode={selectedEpisodeForNotes}
          showTitle={selectedShow?.title}
          showArtwork={selectedShow?.artwork_url}
          downloads={downloads}
          onClose={() => setSelectedEpisodeForNotes(null)}
          onPlayEpisode={handlePlayEpisode}
          onDownloadEpisode={handleDownloadEpisode}
        />
      )}

      {/* Category Explorer Modal */}
      {isCategoryModalOpen && (
        <PodcastCategoryModal
          selectedCategory={selectedCategory}
          onSelectCategory={(cat) => setSelectedCategory(cat)}
          onClose={() => setIsCategoryModalOpen(false)}
        />
      )}
    </div>
  );
}
