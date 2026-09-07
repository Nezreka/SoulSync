import { useEffect, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';

import type { PodcastShowSummary } from '../-podcasts.types';

import {
  fetchFeaturedPodcasts,
  searchPodcasts,
} from '../-podcasts.api';
import { usePodcastContext } from './podcast-context';
import { PodcastCategoryModal } from './podcast-category-modal';
import { PodcastCategoryPills } from './podcast-category-pills';
import { PodcastGrid } from './podcast-grid';
import { PodcastSearchBar } from './podcast-search-bar';
import { PodcastSpotlight } from './podcast-spotlight';

/**
 * Browse view for /podcasts (the index route).
 *
 * Shows the search bar, category pills, spotlight carousel, and the grid
 * of podcast cards. Clicking a card navigates to /podcasts/$podcastId.
 */
export function PodcastsBrowsePage() {
  const navigate = useNavigate();
  const { downloadsCount } = usePodcastContext();

  // Search & Category state
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('Trending');
  const [searchResults, setSearchResults] = useState<PodcastShowSummary[]>([]);
  const [featuredShows, setFeaturedShows] = useState<PodcastShowSummary[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isLoadingFeatured, setIsLoadingFeatured] = useState(false);
  const [isCategoryModalOpen, setIsCategoryModalOpen] = useState(false);

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

  // Navigate to the detail page for a selected show
  const handleSelectShow = (summary: PodcastShowSummary) => {
    // Use iTunes ID when available (most common), fall back to encoded feed URL
    const id = summary.itunes_id
      ? String(summary.itunes_id)
      : summary.feed_url
        ? encodeURIComponent(summary.feed_url)
        : null;

    if (!id) return;

    void navigate({ to: '/podcasts/$podcastId', params: { podcastId: id } });
  };

  return (
    <>
      {/* Top Search & Navigation */}
      <PodcastSearchBar
        value={searchQuery}
        onChange={setSearchQuery}
        onClear={() => setSearchQuery('')}
        isSearching={isSearching}
        downloadsCount={downloadsCount}
        onOpenDownloads={() => {}}
      />

      {/* Browse / Search View */}
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

      {/* Category Explorer Modal */}
      {isCategoryModalOpen && (
        <PodcastCategoryModal
          selectedCategory={selectedCategory}
          onSelectCategory={(cat) => setSelectedCategory(cat)}
          onClose={() => setIsCategoryModalOpen(false)}
        />
      )}
    </>
  );
}
