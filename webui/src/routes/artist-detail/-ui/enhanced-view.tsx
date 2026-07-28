import { useState } from 'react';

import type { EnhancedAlbum, EnhancedData } from '../-artist-detail.enhanced';

import {
  albumRowMeta,
  ENHANCED_SECTIONS,
  enhancedStats,
  groupAlbumsByType,
  sectionCountLabel,
  sectionTrackTotal,
} from '../-artist-detail.enhanced';
import { getAlbumTrackRows } from '../-artist-detail.enhanced-album';
import { AlbumMetaRow } from './album-meta-row';
import { EnhancedTrackTable } from './enhanced-track-table';
import { ExpandedAlbumHeader } from './expanded-album-header';

interface Props {
  data: EnhancedData | null;
  /** Non-null while the request is in flight or after it failed. */
  status: { loading: boolean; error: string };
  /** Drives the admin-only action row inside each expanded album. */
  isAdmin: boolean;
}

/**
 * The Enhanced Management view: a stats bar over per-type sections of
 * expandable album rows (renderEnhancedView, library.js:2885).
 *
 * Only album/ep/single sections render. groupAlbumsByType will happily create a
 * bucket for any other record_type, and the vanilla ignored it the same way —
 * an album typed "live" is grouped and then never shown.
 */
export function EnhancedView({ data, status, isAdmin }: Props) {
  if (status.error) {
    return (
      <div className="enhanced-loading" style={{ color: '#ff6b6b' }}>
        Failed to load: {status.error}
      </div>
    );
  }
  if (status.loading || !data)
    return <div className="enhanced-loading">Loading library data...</div>;

  const grouped = groupAlbumsByType(data.albums ?? []);

  return (
    <>
      <EnhancedStatsBar data={data} />
      {ENHANCED_SECTIONS.map(({ type, label }) => {
        const albums = grouped[type] ?? [];
        // An empty section is omitted entirely, not rendered as a header with
        // nothing under it.
        if (albums.length === 0) return null;
        return (
          <EnhancedSection
            key={type}
            type={type}
            label={label}
            albums={albums}
            artist={data.artist}
            isAdmin={isAdmin}
          />
        );
      })}
    </>
  );
}

function EnhancedStatsBar({ data }: { data: EnhancedData }) {
  const stats = enhancedStats(data);
  return (
    <div className="enhanced-stats-bar">
      <div className="enhanced-stats-items">
        {stats.items.map((item) => (
          <div className="enhanced-stat-item" key={item.label}>
            <span className="enhanced-stat-value">{item.value}</span>
            <span className="enhanced-stat-label">{item.label}</span>
          </div>
        ))}
      </div>
      <div className="enhanced-stats-formats">
        {stats.badges.map((badge) => (
          <span className={`enhanced-format-badge ${badge.className}`} key={badge.format}>
            {badge.format} ({badge.count})
          </span>
        ))}
      </div>
    </div>
  );
}

function EnhancedSection({
  type,
  label,
  albums,
  artist,
  isAdmin,
}: {
  type: string;
  label: string;
  albums: EnhancedAlbum[];
  artist: Record<string, unknown> | undefined;
  isAdmin: boolean;
}) {
  return (
    <div className="enhanced-section">
      <div className="enhanced-section-header">
        <span className="enhanced-section-title">{label}</span>
        <span className="enhanced-section-count">
          {sectionCountLabel(albums.length, sectionTrackTotal(albums))}
        </span>
      </div>
      <div className="enhanced-album-grid">
        {albums.map((album) => (
          <EnhancedAlbumWrapper
            album={album}
            type={type}
            artist={artist}
            isAdmin={isAdmin}
            key={String(album.id)}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * One album: the collapsed row plus its track panel.
 *
 * Expansion is local state per album rather than a page-level Set. The vanilla
 * needed the Set because it re-rendered the whole container from scratch and
 * had to restore which rows were open; React keeps each row mounted, so the
 * state can live where it is used.
 *
 * The panel body is rendered only once expanded — the vanilla's lazy render,
 * kept because a large library can have hundreds of albums and each panel is a
 * full track table.
 */
function EnhancedAlbumWrapper({
  album: albumProp,
  type,
  artist,
  isAdmin,
}: {
  album: EnhancedAlbum;
  type: string;
  artist: Record<string, unknown> | undefined;
  isAdmin: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [thumbBroken, setThumbBroken] = useState(false);
  // Selection is per album, and clears when the panel closes — the vanilla's
  // shared Set leaked ticks between albums.
  const [selected, setSelected] = useState<Set<string>>(() => new Set());

  /**
   * A saved edit is applied here rather than refetching the whole artist. The
   * vanilla mutated the shared album object in place and hand-patched the row;
   * holding the record in state does the same thing declaratively, and it
   * resets whenever a real refetch hands down a new object.
   */
  const [seenAlbum, setSeenAlbum] = useState(albumProp);
  const [album, setAlbum] = useState(albumProp);
  if (albumProp !== seenAlbum) {
    setSeenAlbum(albumProp);
    setAlbum(albumProp);
  }
  const meta = albumRowMeta(album);

  return (
    <div
      className={`enhanced-album-wrapper${expanded ? ' expanded' : ''}`}
      id={`enhanced-album-wrapper-${album.id}`}
    >
      <div
        className={`enhanced-album-row${expanded ? ' expanded' : ''}`}
        id={`enhanced-album-row-${album.id}`}
        onClick={() =>
          setExpanded((open) => {
            if (open) setSelected(new Set());
            return !open;
          })
        }
      >
        <span className="enhanced-album-expand-icon">▶</span>

        <div className="enhanced-album-art-wrap">
          {album.thumb_url && !thumbBroken ? (
            <img
              className="enhanced-album-thumb"
              src={String(album.thumb_url)}
              alt=""
              loading="lazy"
              onError={() => setThumbBroken(true)}
            />
          ) : (
            <div className="enhanced-album-thumb-fallback">🎵</div>
          )}
        </div>

        <div className="enhanced-album-info-block">
          {/* `||`, not `??`: an EMPTY title falls back to "Unknown" too, which
              is what the vanilla did and what an untitled row needs. */}
          <span className="enhanced-album-title" title={String(album.title || '')}>
            {String(album.title || 'Unknown')}
          </span>
          <span className="enhanced-album-meta-line">{meta.metaLine}</span>
        </div>

        <span className={`enhanced-album-type-badge ${(type || 'album').toLowerCase()}`}>
          {type}
        </span>
        {meta.primaryFormat ? (
          <span className={`enhanced-format-badge ${meta.formatClass}`}>{meta.primaryFormat}</span>
        ) : null}
      </div>

      <div
        className={`enhanced-tracks-panel${expanded ? ' visible' : ''}`}
        id={`enhanced-tracks-panel-${album.id}`}
      >
        {/* The row interactions — inline edit, play, queue, per-track actions —
            land in the next slice with _attachTableDelegation. */}
        <div className="enhanced-tracks-panel-inner">
          {expanded ? (
            <>
              <ExpandedAlbumHeader
                album={album}
                rows={getAlbumTrackRows(album)}
                artistId={artist?.id}
                artistName={String(artist?.name ?? '')}
                isAdmin={isAdmin}
              />
              <AlbumMetaRow
                album={album}
                isAdmin={isAdmin}
                onSaved={(updates) => setAlbum((current) => ({ ...current, ...updates }))}
              />
              <EnhancedTrackTable
                album={album}
                isAdmin={isAdmin}
                artist={artist}
                selected={selected}
                onSelectedChange={setSelected}
                onTrackEdited={(trackId, field, value) =>
                  setAlbum((current) => ({
                    ...current,
                    tracks: (current.tracks ?? []).map((t) =>
                      String(t.id) === String(trackId) ? { ...t, [field]: value } : t,
                    ),
                  }))
                }
              />
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
