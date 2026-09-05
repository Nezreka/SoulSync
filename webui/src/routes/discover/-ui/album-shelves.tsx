import { useState } from 'react';

import { thumb } from '@/platform/artwork-thumb';

import type { RecentAlbum } from '../-discover.recent-releases';
import type { SeasonalAlbum, SeasonData } from '../-discover.seasonal';

import { recentAlbumCover } from '../-discover.recent-releases';
import { seasonalAlbumCover, seasonalHeader, seasonalIsActive } from '../-discover.seasonal';
import { DiscoverSection } from './discover-section';

/**
 * The Recent Releases and Seasonal Albums shelves.
 *
 * Transcribed from discover.js 1284-1300 and 4295-4310 for the cards, and
 * index.html 4701-4722 for the two sections.
 *
 * The two card renderers in the vanilla are byte-identical apart from which
 * handler they call, so they are ONE component here. That is a genuine
 * deduplication rather than a merge of two similar things — the divergence risk
 * runs the other way, since a fix applied to one and not the other is exactly
 * what having two copies invites.
 */

export interface DiscoverAlbumCardProps {
  /** Already resolved through the section's own cover fallback. */
  cover: string;
  albumName: string;
  artistName: string;
  /** Your Albums marks ownership on the same card (1474). */
  badge?: { className: 'owned' | 'missing'; icon: string };
  /** Your Albums titles the card "Album — Artist" (1468). */
  titleAttr?: string;
  onOpen: () => void;
}

/**
 * a real button, not a clickable div.
 *
 * the card has exactly one action, opening the album, so the card IS the
 * button. keyboard and screen reader get it for free; the label spells out the
 * album and artist because the visible text is two separate lines.
 */

export function DiscoverAlbumCard({
  cover,
  albumName,
  artistName,
  badge,
  titleAttr,
  onOpen,
}: DiscoverAlbumCardProps) {
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      className="ya-card discover-album-card"
      title={titleAttr}
      aria-label={`${albumName}${artistName ? ` by ${artistName}` : ''}`}
      onClick={onOpen}
    >
      <div className="ya-card-img">
        {!failed && (
          <img src={thumb(cover, 'grid')} alt="" loading="lazy" onError={() => setFailed(true)} />
        )}
        <div className="ya-card-placeholder" style={failed ? undefined : { display: 'none' }}>
          ♫
        </div>
      </div>
      <div className="ya-card-gradient" />
      {badge && (
        <div className="ya-card-badges">
          <div className={`discover-album-badge ${badge.className}`}>{badge.icon}</div>
        </div>
      )}
      <div className="ya-card-info">
        <div className="ya-card-name">{albumName}</div>
        <div className="ya-card-sub">{artistName}</div>
      </div>
    </button>
  );
}

// ── Recent Releases ──────────────────────────────────────────────────────────

export interface RecentReleasesShelfProps {
  albums: RecentAlbum[];
  loaded: boolean;
  /** The index is what the download modal looks the album up by. */
  onOpenAlbum: (index: number) => void;
}

export function RecentReleasesShelf({ albums, loaded, onOpenAlbum }: RecentReleasesShelfProps) {
  return (
    <DiscoverSection
      id="recent-releases"
      title="New Releases For You"
      subtitle="Fresh drops from artists you follow"
      count={albums.length}
      loaded={loaded}
      // No emptyMessage: SECTION_EMPTY_POLICY already carries this section's
      // wording. Passing it again would be a second copy of the same string,
      // free to drift from the one the layout module actually owns.
    >
      <div className="discover-grid" id="recent-releases-carousel">
        {albums.map((album, i) => (
          <DiscoverAlbumCard
            key={`${album.artist_name ?? ''}:${album.album_name ?? ''}:${i}`}
            cover={recentAlbumCover(album)}
            albumName={album.album_name ?? ''}
            artistName={album.artist_name ?? ''}
            // The ownership chip — the same badge language Your Albums wears.
            // Only OWNED is badged: missing is a new release's default state,
            // and twenty 'missing' chips would be noise.
            badge={album.in_library ? { className: 'owned', icon: '✓' } : undefined}
            // BY INDEX, not by id: the download modal resolves the album out of
            // the module's own list, and several of these albums have no id at
            // all until the source is queried.
            onOpen={() => onOpenAlbum(i)}
          />
        ))}
      </div>
    </DiscoverSection>
  );
}

// ── Seasonal Albums ──────────────────────────────────────────────────────────

export interface SeasonalAlbumsShelfProps {
  season: SeasonData | null;
  albums: SeasonalAlbum[];
  loaded: boolean;
  onOpenAlbum: (index: number) => void;
}

export function SeasonalAlbumsShelf({
  season,
  albums,
  loaded,
  onOpenAlbum,
}: SeasonalAlbumsShelfProps) {
  // No active season means no section at all — not an empty one. There is
  // nothing seasonal in March, and a titled box saying so is noise.
  if (!seasonalIsActive(season)) return null;
  const header = seasonalHeader(season as SeasonData);

  return (
    <DiscoverSection
      id="seasonal-albums-section"
      title={header.title}
      subtitle={header.subtitle}
      count={albums.length}
      loaded={loaded}
      // As above — the message lives in SECTION_EMPTY_POLICY.
    >
      <div className="discover-grid" id="seasonal-albums-carousel">
        {albums.map((album, i) => (
          <DiscoverAlbumCard
            key={`${album.artist_name ?? ''}:${album.album_name ?? ''}:${i}`}
            cover={seasonalAlbumCover(album)}
            albumName={album.album_name ?? ''}
            artistName={album.artist_name ?? ''}
            onOpen={() => onOpenAlbum(i)}
          />
        ))}
      </div>
    </DiscoverSection>
  );
}
