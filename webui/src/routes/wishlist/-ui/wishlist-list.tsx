import { useMemo, useState } from 'react';

import type { ParsedWishlistTrack, WishlistArtistGroup } from '../-wishlist.types';

import { upgradeTitle } from '../-wishlist.helpers';
import { WishlistCover } from './wishlist-cover';

/**
 * The dense LIST view — the nebula's operational twin (Boulder: "alternative
 * ways to display the wishlist? no functional change").
 *
 * Display-only by contract: every action here is one the nebula already has,
 * invoked through the SAME seams — remove via the page's mutations,
 * manual search via window._searchWishlistTrackManually (the orb fan's
 * button, wishlist-orb.tsx:238), artist navigation via
 * window._navigateToArtistFromWishlist. Sorting is a local lens over the
 * groups the page already filtered; it fetches nothing and mutates nothing.
 */
export type WishlistListSort = 'failing' | 'name' | 'size';

const SORTS: { key: WishlistListSort; label: string; title: string }[] = [
  { key: 'failing', label: 'Failing first', title: 'Most stuck artists at the top' },
  { key: 'size', label: 'Most wanted', title: 'Largest artist groups first' },
  { key: 'name', label: 'A–Z', title: 'Alphabetical by artist' },
];

function sortGroups(groups: WishlistArtistGroup[], sort: WishlistListSort): WishlistArtistGroup[] {
  const copy = [...groups];
  if (sort === 'name') return copy.sort((a, b) => a.name.localeCompare(b.name));
  if (sort === 'size')
    return copy.sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
  // failing: stuck counts first, then size, then name — the triage order.
  return copy.sort(
    (a, b) => b.failingCount - a.failingCount || b.total - a.total || a.name.localeCompare(b.name),
  );
}

function TrackRow({
  track,
  onRemoveTrack,
  onRemoveAlbum,
}: {
  track: ParsedWishlistTrack;
  onRemoveTrack: (trackId: string) => void;
  /** Present only on album rows — the ✕ on the album cell removes the set. */
  onRemoveAlbum?: (albumName: string) => void;
}) {
  return (
    <div className={`wl-list-track${track.failing ? ' wl-list-track--failing' : ''}`}>
      <WishlistCover
        className="wl-list-cover"
        src={track.image}
        fallback={track.imageFallback}
        placeholder={<div className="wl-list-cover wl-list-cover--ph">♪</div>}
      />
      {/* Title with the album stacked beneath — ONE flexible cell, so wide
          screens read as left cluster + right cluster instead of columns
          adrift in a void (Boulder's image 8). */}
      <span className="wl-list-track-main">
        <span className="wl-list-track-name" title={track.track}>
          {track.track}
        </span>
        <span className="wl-list-track-album" title={track.album}>
          {/* Upgrades are neither missing tracks nor duplicates. Saying so on
              the row is the difference between "my wishlist is broken" and
              "my quality profile is doing its job". */}
          {track.upgrade ? (
            <span className="wl-list-upgrade" title={upgradeTitle(track)}>
              ⬆ upgrade{track.currentQuality ? ` · ${track.currentQuality}` : ''}
            </span>
          ) : null}
          {track.type === 'single' ? 'Single' : track.album}
          {track.type !== 'single' && onRemoveAlbum ? (
            <button
              type="button"
              className="wl-list-album-x"
              title={`Remove all tracks from "${track.album}"`}
              onClick={() => onRemoveAlbum(track.album)}
            >
              ✕
            </button>
          ) : null}
        </span>
      </span>
      <span
        className={`wl-list-tries${track.failing ? ' wl-list-tries--failing' : ''}`}
        title={track.failReason ? `Last error: ${track.failReason}` : undefined}
      >
        {track.retry > 0 ? `${track.failing ? '⚠ ' : ''}${track.retry} tries` : 'queued'}
      </span>
      <span className="wl-list-last" title={track.lastTried || undefined}>
        {track.lastTried || '—'}
      </span>
      <span className="wl-list-track-actions">
        <button
          type="button"
          className="wl-list-btn"
          title="Search manually"
          onClick={() => window._searchWishlistTrackManually?.(track.artist, track.track)}
        >
          🔍
        </button>
        <button
          type="button"
          className="wl-list-btn wl-list-btn--x"
          title="Remove from wishlist"
          onClick={() => onRemoveTrack(track.id)}
        >
          ✕
        </button>
      </span>
    </div>
  );
}

export function WishlistList({
  groups,
  artistImages,
  onRemoveAlbum,
  onRemoveTrack,
  filterActive = false,
}: {
  /** Keyed by LOWERCASED artist name — buildArtistImageMap's contract. */
  artistImages: Map<string, string>;
  groups: WishlistArtistGroup[];
  onRemoveAlbum: (albumName: string) => void;
  onRemoveTrack: (trackId: string) => void;
  /** A live text filter auto-expands matches — collapsed search hits confuse. */
  filterActive?: boolean;
}) {
  const [sort, setSort] = useState<WishlistListSort>('failing');
  const sorted = useMemo(() => sortGroups(groups, sort), [groups, sort]);
  // Collapsed by default: the page opens as a compact artist INDEX (name,
  // count, failing badge) instead of a hundred-row scroll. null = the
  // explicit all-expanded state.
  const [openArtists, setOpenArtists] = useState<Set<string> | null>(new Set());
  const allOpen = openArtists === null;
  const isOpen = (name: string) => filterActive || allOpen || openArtists.has(name);
  const toggleArtist = (name: string) =>
    setOpenArtists((current) => {
      const next = new Set(current ?? sorted.map((g) => g.name));
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  return (
    <div className="wl-list" data-testid="wishlist-list">
      <div className="wl-list-sortbar">
        <div role="tablist" aria-label="Sort wishlist" className="wl-list-sorts">
          {SORTS.map((s) => (
            <button
              key={s.key}
              type="button"
              role="tab"
              aria-selected={sort === s.key}
              className={`wl-chip${sort === s.key ? ' active' : ''}`}
              title={s.title}
              onClick={() => setSort(s.key)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="wl-chip wl-list-expand-all"
          onClick={() => setOpenArtists(allOpen ? new Set() : null)}
        >
          {allOpen ? 'Collapse all' : 'Expand all'}
        </button>
      </div>

      {/* ONE flat table. Artist grouping is a slim separator row, never a
          box — at 100 tracks nested cards read as an outline document, not a
          track list (Boulder: 'looks wack'). Album context lives IN each row;
          removing a whole album is the ✕ on its album cell. */}
      {sorted.map((group) => (
        <div className="wl-list-section" key={group.name}>
          <div
            className="wl-list-sep"
            role="button"
            tabIndex={0}
            aria-expanded={isOpen(group.name)}
            title={isOpen(group.name) ? 'Collapse' : 'Expand'}
            onClick={() => toggleArtist(group.name)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                toggleArtist(group.name);
              }
            }}
          >
            <span className={`wl-list-chevron${isOpen(group.name) ? ' open' : ''}`}>▸</span>
            {artistImages.get(group.name.toLowerCase()) ? (
              <img
                className="wl-list-avatar"
                src={artistImages.get(group.name.toLowerCase())}
                alt=""
                loading="lazy"
              />
            ) : (
              <div className="wl-list-avatar wl-list-avatar--ph">♪</div>
            )}
            <button
              type="button"
              className="wl-list-artist-name"
              title="Open artist"
              onClick={(event) => {
                event.stopPropagation();
                window._navigateToArtistFromWishlist?.(group.name);
              }}
            >
              {group.name}
            </button>
            <span className="wl-list-artist-meta">
              {group.total} track{group.total === 1 ? '' : 's'}
            </span>
            {group.failingCount > 0 && (
              <span className="wl-list-failing-badge">⚠ {group.failingCount} failing</span>
            )}
          </div>

          {isOpen(group.name) &&
            group.albums.flatMap((album) =>
              album.tracks.map((track) => (
                <TrackRow
                  key={track.id}
                  track={track}
                  onRemoveTrack={onRemoveTrack}
                  onRemoveAlbum={onRemoveAlbum}
                />
              )),
            )}
          {isOpen(group.name) &&
            group.singles.map((track) => (
              <TrackRow key={track.id} track={track} onRemoveTrack={onRemoveTrack} />
            ))}
        </div>
      ))}
    </div>
  );
}
