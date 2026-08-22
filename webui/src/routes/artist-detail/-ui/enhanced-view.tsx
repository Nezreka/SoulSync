import { useEffect, useState } from 'react';

import { thumb } from '@/platform/artwork-thumb';

import type { EnhancedAlbum, EnhancedData } from '../-artist-detail.enhanced';

import {
  albumRowMeta,
  enhancedSectionsFor,
  enhancedStats,
  groupAlbumsByType,
  sectionCountLabel,
  sectionTrackTotal,
} from '../-artist-detail.enhanced';
import { getAlbumTrackRows } from '../-artist-detail.enhanced-album';
import { syncVanillaEnhancedData, syncVanillaSelection } from '../-artist-detail.vanilla-state';
import { AlbumMetaRow } from './album-meta-row';
import { ArtistMetaPanel } from './artist-meta-panel';
import { EnhancedBulkBar } from './enhanced-bulk-bar';
import { EnhancedTrackTable } from './enhanced-track-table';
import { ExpandedAlbumHeader } from './expanded-album-header';

interface Props {
  data: EnhancedData | null;
  /** Non-null while the request is in flight or after it failed. */
  status: { loading: boolean; error: string };
  /** Drives the admin-only action row inside each expanded album. */
  isAdmin: boolean;
  /** Re-fetch the payload (Sync found changes, reorganize batch finished). */
  onReload: () => void;
}

/**
 * The Enhanced Management view: a stats bar over per-type sections of
 * expandable album rows (renderEnhancedView, library.js:2885).
 *
 * Every bucket renders, named ones first. It used to walk a fixed album/ep/
 * single list, so a release typed anything else (compilation is the common one)
 * was fetched, grouped, and then never shown at all.
 */
export function EnhancedView({ data, status, isAdmin, onReload }: Props) {
  /**
   * Selection is page-level and shared across albums, as the vanilla's single
   * artistDetailPageState.selectedTracks was: the bulk bar acts on everything
   * ticked, and expanding a second album is a normal way to build that set.
   */
  const [selected, setSelected] = useState<Set<string>>(() => new Set());

  /**
   * The meta panel patches data.artist IN PLACE (save/match/enrich fold into
   * the loaded payload, as the vanilla's updateLocalEnhancedData did); this
   * counter is what re-renders from the mutated object.
   */
  const [, setArtistVersion] = useState(0);

  /**
   * The album and track actions that are still vanilla read this payload for
   * the artist name and to patch their own copy of the album list, and read
   * the selection to drop ids they just deleted. Cleared on unmount so a later
   * page cannot act on a stale artist.
   */
  useEffect(() => {
    syncVanillaEnhancedData(data);
    return () => syncVanillaEnhancedData(null);
  }, [data]);

  useEffect(() => {
    syncVanillaSelection(selected);
    return () => syncVanillaSelection(new Set());
  }, [selected]);

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

  /**
   * A batch edit is applied to the loaded payload in place, as
   * updateLocalEnhancedData did — the panels re-render from it rather than
   * re-fetching every track of every album.
   */
  const applyBatch = (trackIds: string[], updates: Record<string, unknown>) => {
    const ids = new Set(trackIds);
    for (const album of data.albums ?? []) {
      album.tracks = (album.tracks ?? []).map((track) =>
        ids.has(String(track.id)) ? { ...track, ...updates } : track,
      );
    }
  };

  /**
   * A deleted album leaves the payload and takes its ticked tracks with it
   * (deleteLibraryAlbum's state cleanup, library.js:4045-4055). The payload is
   * a prop, so the removal mutates it in place like applyBatch does; the
   * selection setState is what re-renders — always called, even when nothing
   * was ticked, so the section drops the album immediately.
   */
  const removeAlbum = (albumId: unknown) => {
    const albums = data.albums ?? [];
    const album = albums.find((a) => String(a.id) === String(albumId));
    data.albums = albums.filter((a) => String(a.id) !== String(albumId));
    const next = new Set(selected);
    for (const track of album?.tracks ?? []) next.delete(String(track.id));
    setSelected(next);
  };

  return (
    <>
      <ArtistMetaPanel
        artist={(data.artist ?? {}) as import('../-artist-detail.types').ArtistInfo}
        albums={data.albums ?? []}
        isAdmin={isAdmin}
        onReload={onReload}
        onArtistPatched={() => setArtistVersion((v) => v + 1)}
      />
      <EnhancedStatsBar data={data} />
      {enhancedSectionsFor(data.albums ?? []).map(({ type, label }) => {
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
            selected={selected}
            onSelectedChange={setSelected}
            onAlbumDeleted={removeAlbum}
            onReload={onReload}
          />
        );
      })}

      <EnhancedBulkBar
        selected={selected}
        isAdmin={isAdmin}
        onClear={() => setSelected(new Set())}
        onEdited={applyBatch}
      />
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
  selected,
  onSelectedChange,
  onAlbumDeleted,
  onReload,
}: {
  type: string;
  label: string;
  albums: EnhancedAlbum[];
  artist: Record<string, unknown> | undefined;
  isAdmin: boolean;
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  onAlbumDeleted: (albumId: unknown) => void;
  onReload: () => void;
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
            selected={selected}
            onSelectedChange={onSelectedChange}
            onAlbumDeleted={() => onAlbumDeleted(album.id)}
            onReload={onReload}
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
  selected,
  onSelectedChange,
  onAlbumDeleted,
  onReload,
}: {
  album: EnhancedAlbum;
  type: string;
  artist: Record<string, unknown> | undefined;
  isAdmin: boolean;
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  onAlbumDeleted: () => void;
  onReload: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [thumbBroken, setThumbBroken] = useState(false);

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
        onClick={() => setExpanded((open) => !open)}
      >
        <span className="enhanced-album-expand-icon">▶</span>

        <div className="enhanced-album-art-wrap">
          {album.thumb_url && !thumbBroken ? (
            <img
              className="enhanced-album-thumb"
              src={thumb(String(album.thumb_url), 'card')}
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
                onArtApplied={(url) => setAlbum((current) => ({ ...current, thumb_url: url }))}
                onAlbumDeleted={onAlbumDeleted}
                onAlbumPatched={(fresh) => setAlbum(fresh as typeof albumProp)}
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
                onSelectedChange={onSelectedChange}
                onTrackEdited={(trackId, field, value) =>
                  setAlbum((current) => ({
                    ...current,
                    tracks: (current.tracks ?? []).map((t) =>
                      String(t.id) === String(trackId) ? { ...t, [field]: value } : t,
                    ),
                  }))
                }
                onTrackDeleted={(trackId) =>
                  setAlbum((current) => ({
                    ...current,
                    tracks: (current.tracks ?? []).filter((t) => String(t.id) !== String(trackId)),
                  }))
                }
                onAlbumPatched={(fresh) => setAlbum(fresh as typeof albumProp)}
                onReload={onReload}
              />
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
