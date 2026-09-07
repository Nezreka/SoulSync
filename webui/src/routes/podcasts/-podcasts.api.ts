import { apiClient, readJson } from '@/app/api-client';

import type {
  PodcastDownloadItem,
  PodcastEpisodeItem,
  PodcastShowDetail,
  PodcastShowSummary,
} from './-podcasts.types';

interface SearchResponse {
  success?: boolean;
  results?: PodcastShowSummary[];
  query?: string;
  error?: string;
}

interface FeaturedResponse {
  success?: boolean;
  category?: string;
  results?: PodcastShowSummary[];
  error?: string;
}

interface ShowDetailResponse {
  success?: boolean;
  show?: PodcastShowDetail;
  error?: string;
}

interface DownloadResponse {
  success?: boolean;
  download_id?: string;
  status?: string;
  error?: string;
}

interface DownloadsListResponse {
  success?: boolean;
  downloads?: PodcastDownloadItem[];
  error?: string;
}

export async function searchPodcasts(
  query: string,
  limit: number = 24,
): Promise<PodcastShowSummary[]> {
  const trimmed = query.trim();
  if (!trimmed) return [];

  try {
    const data = await readJson<SearchResponse>(
      apiClient.get('podcasts/search', {
        searchParams: { q: trimmed, limit },
      }),
    );
    return data?.success && Array.isArray(data.results) ? data.results : [];
  } catch (err) {
    console.error('Failed to search podcasts:', err);
    return [];
  }
}

export async function fetchFeaturedPodcasts(
  category: string = 'Trending',
): Promise<PodcastShowSummary[]> {
  try {
    const data = await readJson<FeaturedResponse>(
      apiClient.get('podcasts/featured', {
        searchParams: { category },
      }),
    );
    return data?.success && Array.isArray(data.results) ? data.results : [];
  } catch (err) {
    console.error('Failed to fetch featured podcasts:', err);
    return [];
  }
}

export async function fetchPodcastShow(
  feedUrl?: string | null,
  itunesId?: number | null,
): Promise<PodcastShowDetail | null> {
  if (!feedUrl && !itunesId) return null;

  try {
    const searchParams: Record<string, string | number> = {};
    if (feedUrl) searchParams.url = feedUrl;
    if (itunesId) searchParams.itunes_id = itunesId;

    const data = await readJson<ShowDetailResponse>(
      apiClient.get('podcasts/show', { searchParams }),
    );
    return data?.success && data.show ? data.show : null;
  } catch (err) {
    console.error('Failed to fetch podcast show details:', err);
    return null;
  }
}

export async function downloadPodcastEpisode(
  episode: PodcastEpisodeItem,
  showTitle: string = 'Podcasts',
): Promise<DownloadResponse> {
  try {
    return await readJson<DownloadResponse>(
      apiClient.post('podcasts/download', {
        json: {
          enclosure_url: episode.enclosure_url,
          title: episode.title,
          guid: episode.guid,
          show_title: showTitle,
          artwork_url: episode.artwork_url,
          duration_seconds: episode.duration_seconds,
          season: episode.season,
          episode_number: episode.episode_number,
          episode_type: episode.episode_type,
          enclosure_type: episode.enclosure_type,
          enclosure_length: episode.enclosure_length,
        },
      }),
    );
  } catch (err) {
    console.error('Failed to download podcast episode:', err);
    return { success: false, error: 'Network error triggering download' };
  }
}

export async function fetchPodcastDownloads(): Promise<PodcastDownloadItem[]> {
  try {
    const data = await readJson<DownloadsListResponse>(apiClient.get('podcasts/downloads'));
    return data?.success && Array.isArray(data.downloads) ? data.downloads : [];
  } catch (err) {
    console.error('Failed to fetch podcast downloads:', err);
    return [];
  }
}

interface WatchlistCheckResponse {
  success?: boolean;
  is_watching?: boolean;
  podcast?: any;
  error?: string;
}

interface WatchlistMutationResponse {
  success?: boolean;
  is_watching?: boolean;
  podcast?: any;
  error?: string;
}

export async function checkPodcastWatchlist(
  feedUrl?: string | null,
  itunesId?: number | null,
): Promise<{ isWatching: boolean; podcast?: any }> {
  if (!feedUrl && !itunesId) return { isWatching: false };
  try {
    const data = await readJson<WatchlistCheckResponse>(
      apiClient.post('podcasts/watchlist/check', {
        json: {
          feed_url: feedUrl || undefined,
          itunes_id: itunesId || undefined,
        },
      }),
    );
    return {
      isWatching: Boolean(data?.is_watching),
      podcast: data?.podcast,
    };
  } catch (err) {
    console.warn('Failed to check podcast watchlist status:', err);
    return { isWatching: false };
  }
}

export async function addPodcastToWatchlist(
  show: PodcastShowSummary | PodcastShowDetail,
  options?: { auto_download?: boolean; retention_days?: number },
): Promise<{ success: boolean; isWatching: boolean; podcast?: any }> {
  try {
    const data = await readJson<WatchlistMutationResponse>(
      apiClient.post('podcasts/watchlist/add', {
        json: {
          feed_url: show.feed_url,
          title: show.title,
          itunes_id: show.itunes_id || undefined,
          author: show.author || undefined,
          description: show.description || undefined,
          artwork_url: show.artwork_url || undefined,
          website: show.website || undefined,
          episode_count: show.episode_count || undefined,
          auto_download: options?.auto_download ?? true,
          retention_days: options?.retention_days ?? 14,
        },
      }),
    );
    return {
      success: Boolean(data?.success),
      isWatching: Boolean(data?.is_watching),
      podcast: data?.podcast,
    };
  } catch (err) {
    console.error('Failed to add podcast to watchlist:', err);
    return { success: false, isWatching: false };
  }
}

export async function removePodcastFromWatchlist(
  feedUrl?: string | null,
  itunesId?: number | null,
): Promise<{ success: boolean; isWatching: boolean }> {
  try {
    const data = await readJson<WatchlistMutationResponse>(
      apiClient.post('podcasts/watchlist/remove', {
        json: {
          feed_url: feedUrl || undefined,
          itunes_id: itunesId || undefined,
        },
      }),
    );
    return {
      success: Boolean(data?.success),
      isWatching: Boolean(data?.is_watching),
    };
  } catch (err) {
    console.error('Failed to remove podcast from watchlist:', err);
    return { success: false, isWatching: true };
  }
}

export async function updatePodcastWatchlistSettings(
  feedUrl: string,
  settings: { auto_download?: boolean; retention_days?: number },
): Promise<{ success: boolean; podcast?: any }> {
  try {
    const data = await readJson<WatchlistMutationResponse>(
      apiClient.post('podcasts/watchlist/settings', {
        json: {
          feed_url: feedUrl,
          ...settings,
        },
      }),
    );
    return {
      success: Boolean(data?.success),
      podcast: data?.podcast,
    };
  } catch (err) {
    console.error('Failed to update podcast watchlist settings:', err);
    return { success: false };
  }
}

export async function fetchWatchlistPodcasts(): Promise<any[]> {
  try {
    const data = await readJson<{ success: boolean; podcasts: any[] }>(
      apiClient.get('podcasts/watchlist'),
    );
    return data?.success && Array.isArray(data.podcasts) ? data.podcasts : [];
  } catch (err) {
    console.warn('Failed to fetch watchlist podcasts:', err);
    return [];
  }
}


