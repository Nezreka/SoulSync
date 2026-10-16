import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createContext, type ReactNode, useContext, useMemo, useState } from 'react';

import type { DiscographyRelease } from '../../artist-detail/-artist-detail.types';

import {
  LIBRARY_V2_QUERY_KEY,
  libraryV2EnabledQueryOptions,
  monitorLibraryV2DiscoveryAlbum,
} from '../-library-v2.api';
import { artistDetailQueryOptions } from '../../artist-detail/-artist-detail.api';
import styles from './library-v2-page.module.css';

/** Native monitoring for the preserved provider-artist components.
 * Library Discovery reuses their cards and shared track dialog for artists
 * without a catalogue row. Browsing is read-only; monitoring creates a native
 * identity and enables My Library through the supplied callback. */

const BOOKMARK_PATH = 'M5 3.5A1.5 1.5 0 0 1 6.5 2h11A1.5 1.5 0 0 1 19 3.5V22l-7-4.2L5 22V3.5z';

interface ArtistPageLibrary {
  /** The route's source and provider id: the artist the page is about. */
  source: string;
  id: string;
  artistName: string;
  /** The source that produced the discography, which owns the release ids. */
  discographySource: string;
  onMonitored?: (artistId: number) => void;
}

interface ArtistPageRelease {
  source: string;
  providerId: string;
  title: string;
  albumType: string;
  releaseDate: string | null;
  imageUrl: string | null;
  trackCount: number | null;
}

const ArtistPageLibraryContext = createContext<ArtistPageLibrary | null>(null);

/** `null` unless the card sits on the artist page under the provider. */
function useArtistPageLibrary(): ArtistPageLibrary | null {
  return useContext(ArtistPageLibraryContext);
}

function describeRelease(
  release: DiscographyRelease,
  discographySource: string,
): ArtistPageRelease | null {
  if (release.id == null || release.id === '') return null;
  // A gap-fill card (#1067) belongs to the source that listed it.
  const gapSource = typeof release._gap_source === 'string' ? release._gap_source : '';
  const year = release.year == null ? '' : String(release.year);
  return {
    source: gapSource || discographySource,
    providerId: String(release.id),
    title: release.title || release.name || '',
    albumType: release.album_type || 'album',
    releaseDate: release.release_date || year || null,
    imageUrl: release.image_url ?? null,
    trackCount: typeof release.track_count === 'number' ? release.track_count : null,
  };
}

export function ArtistPageLibraryProvider({
  source,
  id,
  name,
  children,
  onMonitored,
}: {
  source: string;
  id: string;
  name: string;
  children: ReactNode;
  onMonitored?: (artistId: number) => void;
}) {
  // The page's own query under the same key, so this reads its cache.
  const detail = useQuery(artistDetailQueryOptions(source, id, name));
  const artistName = detail.data?.artist?.name || name;
  const normalizedSource = source.toLowerCase();
  const discographySource = detail.data?.discography?.source || normalizedSource;

  const value = useMemo<ArtistPageLibrary>(
    () => ({ source: normalizedSource, id, artistName, discographySource, onMonitored }),
    [normalizedSource, id, artistName, discographySource, onMonitored],
  );

  return (
    <ArtistPageLibraryContext.Provider value={value}>{children}</ArtistPageLibraryContext.Provider>
  );
}

/** The bookmark on a release card. Renders nothing outside the provider. */
export function ReleaseMonitorButton({ release }: { release: DiscographyRelease }) {
  const library = useArtistPageLibrary();
  if (!library) return null;
  return <MonitorButton library={library} release={release} />;
}

function MonitorButton({
  library,
  release,
}: {
  library: ArtistPageLibrary;
  release: DiscographyRelease;
}) {
  const queryClient = useQueryClient();
  const enabled = useQuery(libraryV2EnabledQueryOptions());
  const [state, setState] = useState<'idle' | 'busy' | 'done'>('idle');
  const target = describeRelease(release, library.discographySource);
  if (!target) return null;

  const canWish = enabled.data?.enabled === true && enabled.data.canWish;
  const monitored = state === 'done';
  const busy = state === 'busy';

  async function monitor() {
    if (!target || state !== 'idle') return;
    setState('busy');
    try {
      const result = await monitorLibraryV2DiscoveryAlbum({
        source: target.source,
        artistSource: library.source,
        artistProviderId: library.id,
        artistName: library.artistName,
        albumProviderId: target.providerId,
        albumName: target.title,
        albumType: target.albumType,
        releaseDate: target.releaseDate,
        imageUrl: target.imageUrl,
        trackCount: target.trackCount,
      });
      setState('done');
      library.onMonitored?.(result.artistId);
      // The artist has a catalogue row now; the route's lookup has to see it.
      void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } catch (error) {
      setState('idle');
      window.showToast?.(
        `Could not monitor ${target.title || 'this release'}: ${(error as Error).message}`,
        'error',
      );
    }
  }

  return (
    <div className={styles.cardMonitor} onClick={(event) => event.stopPropagation()}>
      <button
        type="button"
        className={`${styles.monitorBtn} ${monitored ? styles.monitorOn : ''}`}
        aria-label={monitored ? 'Monitored' : busy ? 'Starting monitoring' : 'Start monitoring'}
        aria-pressed={monitored}
        title={
          monitored
            ? 'Monitored'
            : canWish
              ? 'Monitor this release'
              : 'Monitoring needs Library access for this profile'
        }
        disabled={!canWish || busy || monitored}
        onClick={() => void monitor()}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d={BOOKMARK_PATH} strokeLinejoin="round" />
        </svg>
      </button>
    </div>
  );
}
