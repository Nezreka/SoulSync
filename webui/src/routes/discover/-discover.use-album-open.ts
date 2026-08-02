import { useCallback } from 'react';

import type { CacheItem, CacheSectionKey } from './-discover.cache-sections';
import type { RecentAlbum } from './-discover.recent-releases';
import type { SeasonalAlbum } from './-discover.seasonal';
import type { AlbumDetail, YourAlbum } from './-discover.your-albums-actions';

import { resolveCacheAlbum } from './-discover.api';
import {
  ALBUM_UNAVAILABLE,
  NO_ALBUM_ID,
  NO_ARTIST_DATA,
  NO_TRACKS_FOUND,
  albumFetchUrl,
  cacheVirtualAlbumId,
  isTrackSection,
  resolvedIdIsUseful,
  shouldResolveAlbum,
  shouldResolveTrackAlbum,
} from './-discover.cache-sections';
import {
  recentAlbumFetchUrl,
  recentAlbumId,
  recentAlbumSource,
  recentNoIdMessage,
  recentTrackAlbum,
  recentTrackArtists,
  recentVirtualAlbumId,
} from './-discover.recent-releases';
import {
  SEASONAL_NO_ALBUM_ID,
  buildDiscoverArtistContext,
  seasonalAlbumSource,
  seasonalVirtualAlbumId,
} from './-discover.seasonal';
import {
  ALBUM_NOT_FOUND,
  ALBUM_NO_TRACKS,
  albumDownloadSources,
  albumVirtualId,
  mapAlbumObject,
  mapAlbumTracks,
} from './-discover.your-albums-actions';

/**
 * The four album-card → download-modal flows.
 *
 * Transcribed from openYourAlbumDownload (1506), openDownloadModalForSeasonalAlbum
 * (4422), openCacheDiscoverAlbum (10477) and openDownloadModalForRecentAlbum
 * (11486). Every decision inside them is already a pure helper in the section
 * modules; this hook is only the SEQUENCE — fetch, map, hand to the shared
 * downloads.js modal — plus the overlay/toast bracketing each flow carries.
 *
 * Raw `fetch` on purpose: the vanilla branches on `response.ok` and
 * `response.status === 404` (the stale-cache resolve), and the ky client throws
 * before either question can be asked. Same reasoning as the ListenBrainz hook.
 *
 * The genre-dive modal reaches here too: its track and album cards are cache
 * items under 'genre_dive_tracks' / 'genre_dive_albums' (10841, 10867), so
 * `openCacheItem` serves the dive modal and the cache shelves alike. The
 * vanilla removes #genre-deep-dive-modal itself (10486, 10554); in React the
 * page closes the dive state before calling — same order, different mechanism.
 */

export type AlbumOpenToast = { message: string; level: 'error' };

export interface AlbumOpenController {
  openYourAlbum: (album: YourAlbum | undefined, index: number) => Promise<void>;
  openRecentAlbum: (album: RecentAlbum | undefined) => Promise<void>;
  openSeasonalAlbum: (album: SeasonalAlbum | undefined) => Promise<void>;
  openCacheItem: (key: CacheSectionKey, item: CacheItem | undefined) => Promise<void>;
}

const overlay = (message: string) => window.showLoadingOverlay?.(message);
const overlayDone = () => window.hideLoadingOverlay?.();

async function fetchAlbumDetail(url: string): Promise<Response> {
  return fetch(url);
}

export function useAlbumOpen(onToast: (toast: AlbumOpenToast) => void): AlbumOpenController {
  const fail = useCallback(
    (prefix: string, error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      overlayDone();
      onToast({ message: `${prefix}${message}`, level: 'error' });
    },
    [onToast],
  );

  /** openYourAlbumDownload (1506-1578): per-source dispatch + name search. */
  const openYourAlbum = useCallback(
    async (album: YourAlbum | undefined, index: number) => {
      if (!album) {
        onToast({ message: ALBUM_NOT_FOUND, level: 'error' });
        return;
      }
      overlay(`Loading tracks for ${album.album_name}...`);
      try {
        let albumData: AlbumDetail | null = null;
        for (const { source, id } of albumDownloadSources(album)) {
          const r = await fetchAlbumDetail(
            albumFetchUrl(source, id, album.album_name || '', album.artist_name || ''),
          );
          if (r.ok) {
            albumData = (await r.json()) as AlbumDetail;
            if (albumData?.tracks?.length) break;
            albumData = null; // empty payload — try the next source (1535)
          }
        }
        if (!albumData) {
          // Last resort — search by name (1540-1543).
          const params = new URLSearchParams({
            name: album.album_name || '',
            artist: album.artist_name || '',
          });
          const r = await fetchAlbumDetail(`/api/discover/album/spotify/search?${params}`);
          if (r.ok) albumData = (await r.json()) as AlbumDetail;
        }
        if (!albumData?.tracks?.length) throw new Error(ALBUM_NO_TRACKS);
        await window.openDownloadMissingModalForArtistAlbum?.(
          albumVirtualId(album, index),
          albumData.name ?? '',
          mapAlbumTracks(albumData, album),
          mapAlbumObject(albumData, album),
          { id: null, name: album.artist_name },
          false,
        );
        overlayDone();
      } catch (error) {
        fail('Failed to load album: ', error);
      }
    },
    [onToast, fail],
  );

  /** openDownloadModalForRecentAlbum (11486-11566). */
  const openRecentAlbum = useCallback(
    async (album: RecentAlbum | undefined) => {
      if (!album) {
        onToast({ message: ALBUM_NOT_FOUND, level: 'error' });
        return;
      }
      overlay(`Loading tracks for ${album.album_name}...`);
      try {
        const source = recentAlbumSource(album);
        const albumId = recentAlbumId(album, source);
        if (!albumId) throw new Error(recentNoIdMessage(source));
        const response = await fetchAlbumDetail(recentAlbumFetchUrl(source, albumId, album));
        if (!response.ok) throw new Error('Failed to fetch album tracks');
        const albumData = (await response.json()) as AlbumDetail;
        if (!albumData.tracks?.length) throw new Error('No tracks found in album');
        const spotifyTracks = albumData.tracks.map((track) => ({
          id: track.id,
          name: track.name,
          artists: recentTrackArtists(track, albumData, album),
          album: recentTrackAlbum(albumData),
          duration_ms: track.duration_ms || 0,
          track_number: track.track_number || 0,
        }));
        await window.openDownloadMissingModalForYouTube?.(
          recentVirtualAlbumId(albumId),
          albumData.name ?? '',
          spotifyTracks,
          buildDiscoverArtistContext(
            source,
            album.artist_name || '',
            album as Record<string, unknown>,
            albumData as Record<string, unknown>,
          ),
          recentTrackAlbum(albumData),
        );
        overlayDone();
      } catch (error) {
        fail('Failed to load album: ', error);
      }
    },
    [onToast, fail],
  );

  /** openDownloadModalForSeasonalAlbum (4422-4502). */
  const openSeasonalAlbum = useCallback(
    async (album: SeasonalAlbum | undefined) => {
      if (!album) {
        onToast({ message: ALBUM_NOT_FOUND, level: 'error' });
        return;
      }
      overlay(`Loading tracks for ${album.album_name}...`);
      try {
        const source = seasonalAlbumSource(album);
        const albumId = album.spotify_album_id;
        if (!albumId) throw new Error(SEASONAL_NO_ALBUM_ID);
        // Same source-agnostic endpoint as Recent Releases (4442) — the row
        // types differ but the name/artist params are built identically.
        const response = await fetchAlbumDetail(
          albumFetchUrl(source, albumId, album.album_name || '', album.artist_name || ''),
        );
        if (!response.ok) throw new Error('Failed to fetch album tracks');
        const albumData = (await response.json()) as AlbumDetail;
        if (!albumData.tracks?.length) throw new Error('No tracks found in album');
        const spotifyTracks = albumData.tracks.map((track) => ({
          id: track.id,
          name: track.name,
          artists: recentTrackArtists(track, albumData, album),
          album: recentTrackAlbum(albumData),
          duration_ms: track.duration_ms || 0,
          track_number: track.track_number || 0,
        }));
        await window.openDownloadMissingModalForYouTube?.(
          seasonalVirtualAlbumId(albumId),
          albumData.name ?? '',
          spotifyTracks,
          buildDiscoverArtistContext(
            source,
            album.artist_name || '',
            album as Record<string, unknown>,
            albumData as Record<string, unknown>,
          ),
          // The seasonal album context carries its SOURCE (4488) — the one
          // field the recent-releases context does not.
          { ...recentTrackAlbum(albumData), source },
        );
        overlayDone();
      } catch (error) {
        fail('Failed to load album tracks: ', error);
      }
    },
    [onToast, fail],
  );

  /** openCacheDiscoverAlbum (10477-10616), both branches. */
  const openCacheItem = useCallback(
    async (key: CacheSectionKey, item: CacheItem | undefined) => {
      if (!item) return;
      const source = item.source || 'spotify';

      if (isTrackSection(key)) {
        // Deep cuts / genre-dive tracks: find the real album (10485-10545).
        const albumName = item.album_name || item.name || '';
        const artistName = item.artist_name || '';
        const trackAlbumId = item.album_id || '';
        if (!artistName) {
          onToast({ message: NO_ARTIST_DATA, level: 'error' });
          return;
        }
        overlay(`Loading ${albumName}...`);
        try {
          let resolvedId = trackAlbumId;
          let response: Response | undefined;
          if (trackAlbumId) {
            response = await fetchAlbumDetail(
              albumFetchUrl(source, trackAlbumId, albumName, artistName),
            );
          }
          if (shouldResolveTrackAlbum(Boolean(trackAlbumId), response?.ok ?? false)) {
            const resolved = await resolveCacheAlbum(albumName, artistName).catch(() => null);
            if (resolved?.success && resolved.entity_id) {
              resolvedId = resolved.entity_id;
              response = await fetchAlbumDetail(
                albumFetchUrl(resolved.source || source, resolvedId, albumName, artistName),
              );
            }
          }
          if (!response?.ok) throw new Error('Failed to fetch album tracks');
          const albumData = (await response.json()) as AlbumDetail;
          if (!albumData.tracks?.length) throw new Error(NO_TRACKS_FOUND);
          const spotifyTracks = albumData.tracks.map((track) => ({
            id: track.id,
            name: track.name,
            artists: recentTrackArtists(track, albumData, { artist_name: artistName }),
            album: recentTrackAlbum(albumData),
            duration_ms: track.duration_ms || 0,
            track_number: track.track_number || 0,
          }));
          await window.openDownloadMissingModalForYouTube?.(
            cacheVirtualAlbumId(resolvedId),
            albumData.name ?? '',
            spotifyTracks,
            buildDiscoverArtistContext(
              // The track branch resolves the SOURCE too (10500); the album
              // branch below never re-sources.
              source,
              artistName,
              item,
              albumData as Record<string, unknown>,
            ),
            recentTrackAlbum(albumData),
          );
          overlayDone();
        } catch (error) {
          fail('Failed to load album: ', error);
        }
        return;
      }

      const albumId = item.entity_id;
      if (!albumId) {
        onToast({ message: NO_ALBUM_ID, level: 'error' });
        return;
      }
      overlay(`Loading ${item.name || 'album'}...`);
      try {
        let response = await fetchAlbumDetail(
          albumFetchUrl(source, albumId, item.name || '', item.artist_name || ''),
        );
        if (shouldResolveAlbum(response.status)) {
          const resolved = await resolveCacheAlbum(item.name || '', item.artist_name || '').catch(
            () => null,
          );
          if (resolvedIdIsUseful(resolved, albumId)) {
            response = await fetchAlbumDetail(
              albumFetchUrl(
                resolved!.source || source,
                resolved!.entity_id!,
                item.name || '',
                item.artist_name || '',
              ),
            );
          }
        }
        if (!response.ok) throw new Error(ALBUM_UNAVAILABLE);
        const albumData = (await response.json()) as AlbumDetail;
        if (!albumData.tracks?.length) throw new Error(NO_TRACKS_FOUND);
        const spotifyTracks = albumData.tracks.map((track) => ({
          id: track.id,
          name: track.name,
          artists: recentTrackArtists(track, albumData, { artist_name: item.artist_name }),
          album: recentTrackAlbum(albumData),
          duration_ms: track.duration_ms || 0,
          track_number: track.track_number || 0,
        }));
        await window.openDownloadMissingModalForYouTube?.(
          // The ORIGINAL id, even after a resolve (10607) — unlike the track
          // branch, which keys the bubble on the resolved id (10538).
          cacheVirtualAlbumId(albumId),
          albumData.name ?? '',
          spotifyTracks,
          buildDiscoverArtistContext(
            source,
            item.artist_name || (albumData.artists as { name?: string }[])?.[0]?.name || '',
            item,
            albumData as Record<string, unknown>,
          ),
          recentTrackAlbum(albumData),
        );
        overlayDone();
      } catch (error) {
        fail('Failed to load album: ', error);
      }
    },
    [onToast, fail],
  );

  return { openYourAlbum, openRecentAlbum, openSeasonalAlbum, openCacheItem };
}
