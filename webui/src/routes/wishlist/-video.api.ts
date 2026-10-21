import { keepPreviousData, queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  VideoDiagnostics,
  VideoMedia,
  VideoMovieItem,
  VideoShowItem,
  VideoSort,
  VideoWishlistCounts,
  VideoWishlistResponse,
} from './-video.types';

import { MEDIA_KIND } from './-video.types';

/**
 * Every endpoint the vanilla video wishlist called, one function each, same
 * paths and bodies. The endpoints resolve the profile from the Flask session,
 * so nothing sends a profile header; the profile still keys every query.
 */

export const VIDEO_WISHLIST_QUERY_KEY = ['video-wishlist'] as const;

/** the vanilla page asked for 60 per page; so does this. */
export const VIDEO_PAGE_SIZE = 60;

export interface VideoListParams {
  media: VideoMedia;
  search: string;
  sort: VideoSort;
  page: number;
}

export type VideoListItem = VideoMovieItem | VideoShowItem;

export function videoWishlistListQueryOptions(profileId: number, p: VideoListParams) {
  return queryOptions({
    queryKey: [
      ...VIDEO_WISHLIST_QUERY_KEY,
      'list',
      profileId,
      p.media,
      p.search,
      p.sort,
      p.page,
    ] as const,
    queryFn: () => {
      const searchParams = { search: p.search, sort: p.sort, page: p.page, limit: VIDEO_PAGE_SIZE };
      return p.media === 'youtube'
        ? readJson<VideoWishlistResponse<VideoShowItem>>(
            apiClient.get('video/youtube/wishlist', { searchParams }),
          )
        : readJson<VideoWishlistResponse<VideoListItem>>(
            apiClient.get('video/wishlist', {
              searchParams: { kind: MEDIA_KIND[p.media], ...searchParams },
            }),
          );
    },
    // a page or search change keeps the old grid up until the new one lands,
    // instead of flashing the loading state between every keystroke.
    placeholderData: keepPreviousData,
    // downloads finishing take rows off server-side and fire no event here.
    refetchInterval: 30_000,
  });
}

/** Movies + shows + episodes + YouTube in one call: the tab counts. */
export function videoWishlistCountsQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...VIDEO_WISHLIST_QUERY_KEY, 'counts', profileId] as const,
    queryFn: () =>
      readJson<VideoWishlistCounts & { success?: boolean }>(apiClient.get('video/wishlist/counts')),
    refetchInterval: 30_000,
  });
}

export interface VideoActionTarget {
  scope: 'movie' | 'show' | 'season' | 'episode';
  tmdb_id: number;
  season_number?: number;
  episode_number?: number;
}

export interface VideoSearchResult {
  success?: boolean;
  queued?: number;
  total?: number;
  /** 'movie' or 'episode' when that library folder isn't set. */
  missing_target?: string | null;
  error?: string;
}

/** "Search now": the click overrides the release gate for this item. */
export function searchVideoNow(target: VideoActionTarget): Promise<VideoSearchResult> {
  return readJson<VideoSearchResult>(apiClient.post('video/wishlist/search', { json: target }));
}

/** clear the row's backoff and evidence, then search every source. */
export function retryVideoAllSources(target: VideoActionTarget): Promise<VideoSearchResult> {
  return readJson<VideoSearchResult>(apiClient.post('video/wishlist/retry', { json: target }));
}

export interface VideoSearchAllResult {
  success?: boolean;
  kinds?: { movie?: string; episode?: string };
}

export function searchAllVideoMissing(): Promise<VideoSearchAllResult> {
  return readJson<VideoSearchAllResult>(apiClient.post('video/wishlist/search-all'));
}

export interface VideoDownloadAllResult {
  success?: boolean;
  queued?: number;
  started?: number;
  refused?: number;
  already?: number;
  unavailable?: number;
  error?: string;
}

/** YouTube's bulk action: there is nothing to search, so it downloads. */
export function downloadAllYoutubeWaiting(): Promise<VideoDownloadAllResult> {
  return readJson<VideoDownloadAllResult>(apiClient.post('video/wishlist/youtube/download-all'));
}

export interface VideoYoutubeNow {
  video_id: string;
  channel_id: string | null;
  channel_title: string | null;
  video_title: string | null;
  published_at: string | null;
}

/** one YouTube video, now, past the retry wait and the unavailable mark. */
export function downloadYoutubeNow(
  body: VideoYoutubeNow,
): Promise<{ success?: boolean; already?: boolean; error?: string }> {
  return readJson(apiClient.post('video/youtube/download', { json: body }));
}

export function removeVideoWish(
  target: VideoActionTarget,
): Promise<{ success?: boolean; error?: string }> {
  return readJson(apiClient.post('video/wishlist/remove', { json: target }));
}

/** YouTube removals go through the youtube table, by video or channel id. */
export function removeYoutubeWish(
  scope: 'video' | 'channel',
  sourceId: string,
): Promise<{ success?: boolean } | null> {
  return readJson(
    apiClient.post('video/youtube/wishlist/remove', { json: { scope, source_id: sourceId } }),
  );
}

export function unfollowYoutubeChannel(youtubeId: string): Promise<{ success?: boolean } | null> {
  return readJson(apiClient.post('video/youtube/unfollow', { json: { youtube_id: youtubeId } }));
}

export function clearVideoWishlist(
  kind: 'movie' | 'show' | 'youtube',
): Promise<{ success?: boolean; removed?: number }> {
  return readJson(apiClient.post('video/wishlist/clear', { json: { kind } }));
}

/** rows added while upcoming have no poster yet; fills them once TMDB does. */
export function backfillMovieArt(): Promise<{ success?: boolean; updated?: number }> {
  return readJson(apiClient.post('video/wishlist/backfill-movie-art'));
}

/** rows added before art capture have no still / season poster. */
export function backfillShowArt(): Promise<{ success?: boolean; updated?: number }> {
  return readJson(apiClient.post('video/wishlist/backfill-art'));
}

export interface VideoDiagKey {
  kind: 'movie' | 'episode';
  tmdb_id: number;
  season_number?: number | null;
  episode_number?: number | null;
}

export function videoDiagnosticsQueryOptions(key: VideoDiagKey) {
  return queryOptions({
    queryKey: [...VIDEO_WISHLIST_QUERY_KEY, 'diagnostics', key] as const,
    queryFn: () => {
      const searchParams: Record<string, string | number> = {
        kind: key.kind,
        tmdb_id: key.tmdb_id,
      };
      if (key.season_number != null) searchParams.season_number = key.season_number;
      if (key.episode_number != null) searchParams.episode_number = key.episode_number;
      return readJson<VideoDiagnostics>(
        apiClient.get('video/wishlist/diagnostics', { searchParams }),
      );
    },
    retry: false,
  });
}

export interface VideoCastMember {
  tmdb_id?: number;
  name: string;
  character?: string | null;
  photo?: string | null;
}

/** a show's synopsis + cast: the library detail when owned, else TMDB. */
export function videoShowInfoQueryOptions(show: { tmdb_id: number; library_id?: number | null }) {
  const path =
    show.library_id != null
      ? `video/detail/show/${show.library_id}`
      : `video/tmdb/show/${show.tmdb_id}`;
  return queryOptions({
    queryKey: [...VIDEO_WISHLIST_QUERY_KEY, 'show-info', path] as const,
    queryFn: () =>
      readJson<{ overview?: string | null; cast?: VideoCastMember[] }>(apiClient.get(path)),
    staleTime: 10 * 60_000,
    retry: false,
  });
}

/** an episode's guest stars. */
export function videoEpisodeInfoQueryOptions(tmdbId: number, season: number, episode: number) {
  return queryOptions({
    queryKey: [...VIDEO_WISHLIST_QUERY_KEY, 'episode-info', tmdbId, season, episode] as const,
    queryFn: () =>
      readJson<{ guest_stars?: VideoCastMember[] }>(
        apiClient.get(`video/episode/${tmdbId}/${season}/${episode}`),
      ),
    staleTime: 10 * 60_000,
    retry: false,
  });
}

/** a YouTube video's full metadata, from yt-dlp. */
export function youtubeVideoInfoQueryOptions(videoId: string) {
  return queryOptions({
    queryKey: [...VIDEO_WISHLIST_QUERY_KEY, 'yt-video', videoId] as const,
    queryFn: () =>
      readJson<{
        video?: {
          description?: string | null;
          duration_seconds?: number | null;
          view_count?: number | null;
          published_at?: string | null;
        } | null;
      }>(apiClient.get(`video/youtube/video/${encodeURIComponent(videoId)}`)),
    staleTime: 10 * 60_000,
    retry: false,
  });
}
