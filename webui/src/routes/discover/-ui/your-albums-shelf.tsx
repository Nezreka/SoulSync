import type { AlbumBadge } from '../-discover.your-albums';

import {
  albumBadge,
  albumCover,
  yourAlbumsPagination,
  YOUR_ALBUMS_DEFAULT_SORT,
  YOUR_ALBUMS_DEFAULT_STATUS,
} from '../-discover.your-albums';
import { DiscoverSection } from './discover-section';
import { GearIcon, RefreshIcon } from './your-artists-shelf';

/**
 * The Your Albums shelf.
 *
 * Transcribed from index.html 4652-4700 for the section, filters and pager.
 *
 * The filters and the pager are BOTH conditionally present in the vanilla, for
 * different reasons: the filters appear once there is a library to filter, and
 * the pager disappears entirely when everything fits on one page — an
 * all-disabled pager is noise, not information.
 */

export interface YourAlbum {
  id?: number | string;
  album_name?: string;
  artist_name?: string;
  image_url?: string;
  in_library?: boolean;
  [key: string]: unknown;
}

export interface YourAlbumsShelfProps {
  albums: YourAlbum[];
  /** The unfiltered total, which drives the pager. */
  total: number;
  page: number;
  loaded: boolean;
  loading?: boolean;
  subtitle: string;
  query: string;
  status: string;
  sort: string;
  /** Shown only when there is something missing to fetch. */
  canDownloadMissing: boolean;
  refreshing?: boolean;
  onRefresh: () => void;
  onConfigureSources: () => void;
  onDownloadMissing: () => void;
  onQueryChange: (query: string) => void;
  onStatusChange: (status: string) => void;
  onSortChange: (sort: string) => void;
  onPrevPage: () => void;
  onNextPage: () => void;
  onOpenAlbum: (album: YourAlbum) => void;
}

const STATUS_OPTIONS = [
  { value: 'all', label: 'All Albums' },
  { value: 'missing', label: 'Missing' },
  { value: 'owned', label: 'Owned' },
];

const SORT_OPTIONS = [
  { value: 'artist_name', label: 'Artist' },
  { value: 'album_name', label: 'Album' },
  { value: 'release_date', label: 'Release Date' },
  { value: 'recent', label: 'Date Added' },
];

export function YourAlbumsShelf({
  albums,
  total,
  page,
  loaded,
  loading,
  subtitle,
  query,
  status,
  sort,
  canDownloadMissing,
  refreshing,
  onRefresh,
  onConfigureSources,
  onDownloadMissing,
  onQueryChange,
  onStatusChange,
  onSortChange,
  onPrevPage,
  onNextPage,
  onOpenAlbum,
}: YourAlbumsShelfProps) {
  const pager = yourAlbumsPagination(total, page);

  return (
    <DiscoverSection
      id="your-albums-section"
      title="Your Albums"
      subtitle={subtitle}
      count={albums.length}
      loaded={loaded}
      actions={
        <>
          <button
            type="button"
            className="btn btn--sm btn--secondary ya-header-btn ya-refresh-btn"
            id="your-albums-refresh-btn"
            title="Refresh from services"
            aria-label="Refresh from services"
            disabled={refreshing}
            onClick={onRefresh}
          >
            <RefreshIcon />
          </button>
          <button
            type="button"
            className="btn btn--sm btn--secondary ya-header-btn ya-settings-btn"
            title="Configure sources"
            aria-label="Configure sources"
            onClick={onConfigureSources}
          >
            <GearIcon />
          </button>
          {/* Hidden until there IS something missing — a download button that
              always fetches nothing teaches the user to ignore it. */}
          {canDownloadMissing && (
            <button
              type="button"
              className="btn btn--sm btn--secondary ya-header-btn"
              id="your-albums-download-btn"
              title="Download missing albums"
              aria-label="Download missing albums"
              onClick={onDownloadMissing}
            >
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
              >
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
              </svg>
            </button>
          )}
        </>
      }
    >
      <div className="spotify-library-filters" id="your-albums-filters">
        <input
          type="text"
          className="spotify-library-search"
          id="your-albums-search"
          placeholder="Filter your albums…"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
        />
        <select
          id="your-albums-status-filter"
          className="spotify-library-select"
          aria-label="Filter by status"
          value={status || YOUR_ALBUMS_DEFAULT_STATUS}
          onChange={(e) => onStatusChange(e.target.value)}
        >
          {STATUS_OPTIONS.map((o) => (
            <option value={o.value} key={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          id="your-albums-sort"
          className="spotify-library-select"
          aria-label="Sort albums"
          value={sort || YOUR_ALBUMS_DEFAULT_SORT}
          onChange={(e) => onSortChange(e.target.value)}
        >
          {SORT_OPTIONS.map((o) => (
            <option value={o.value} key={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="discover-grid" id="your-albums-grid">
        {loading ? (
          <div className="discover-loading">
            <div className="loading-spinner" />
            <p>Loading your albums...</p>
          </div>
        ) : (
          albums.map((album) => (
            <AlbumCard
              key={String(album.id ?? `${album.artist_name}:${album.album_name}`)}
              album={album}
              onOpen={onOpenAlbum}
            />
          ))
        )}
      </div>

      {pager.visible && (
        <div className="spotify-library-pagination" id="your-albums-pagination">
          <button type="button" disabled={pager.prevDisabled} onClick={onPrevPage}>
            Previous
          </button>
          <span className="spotify-library-page-label">{pager.label}</span>
          <button type="button" disabled={pager.nextDisabled} onClick={onNextPage}>
            Next
          </button>
        </div>
      )}
    </DiscoverSection>
  );
}

function AlbumCard({ album, onOpen }: { album: YourAlbum; onOpen: (album: YourAlbum) => void }) {
  const badge: AlbumBadge = albumBadge(album);
  return (
    <div className="spotify-album-card" onClick={() => onOpen(album)}>
      <div className="spotify-album-cover">
        <img src={albumCover(album)} alt={album.album_name ?? ''} loading="lazy" />
        <span className={`spotify-album-badge ${badge.className}`}>{badge.icon}</span>
      </div>
      <div className="spotify-album-info">
        <div className="spotify-album-name">{album.album_name}</div>
        <div className="spotify-album-artist">{album.artist_name}</div>
      </div>
    </div>
  );
}
