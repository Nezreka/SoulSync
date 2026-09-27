import { useEffect, useRef, useState } from 'react';

import { thumb } from '@/platform/artwork-thumb';

import type { EnhancedAlbum, EnhancedData, EnhancedTrack } from '../-artist-detail.enhanced';

import { albumMatchesEdit, clearArtistEdit, peekArtistEdit } from '../-artist-detail.edit-focus';
import {
  albumRowMeta,
  enhancedSectionsFor,
  groupAlbumsByType,
  sectionCountLabel,
  sectionTrackTotal,
} from '../-artist-detail.enhanced';
import {
  getAlbumTrackRows,
  loadCanonicalTracks,
  queueTrackPayload,
} from '../-artist-detail.enhanced-album';
import { syncVanillaEnhancedData, syncVanillaSelection } from '../-artist-detail.vanilla-state';
import { AlbumMetaRow } from './album-meta-row';
import { ArtistMetaPanel } from './artist-meta-panel';
import { EnhancedBulkBar } from './enhanced-bulk-bar';
import { EnhancedTrackTable } from './enhanced-track-table';
import { ExpandedAlbumHeader } from './expanded-album-header';
import { ChevronIcon, PlayIcon } from './lib-icons';

interface Props {
  data: EnhancedData | null;
  /** Non-null while the request is in flight or after it failed. */
  status: { loading: boolean; error: string };
  /** Drives the admin-only action row inside each expanded album. */
  isAdmin: boolean;
  /** Re-fetch the payload (Sync found changes, reorganize batch finished). */
  onReload: () => void;
  focusAlbumId?: string;
}

/**
 * The Enhanced Management view: a stats bar over per-type sections of
 * expandable album rows (renderEnhancedView, library.js:2885).
 *
 * Every bucket renders, named ones first. It used to walk a fixed album/ep/
 * single list, so a release typed anything else (compilation is the common one)
 * was fetched, grouped, and then never shown at all.
 */
export function EnhancedView({ data, status, isAdmin, onReload, focusAlbumId }: Props) {
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
      <div className="lib-state error">
        <div className="enhanced-loading" style={{ color: '#ff6b6b' }}>
          Failed to load: {status.error}
        </div>
        <button type="button" className="lib-btn" onClick={onReload}>
          Try again
        </button>
      </div>
    );
  }
  if (status.loading || !data) return <LibrarySkeleton />;

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
            focusAlbumId={focusAlbumId}
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

/**
 * the loading state: the shape of the page, greyed, instead of one line of
 * text. the text stays for screen readers and for the tests that read it.
 */
function LibrarySkeleton() {
  return (
    <div className="lib-skeleton" aria-busy="true">
      <div className="enhanced-loading lib-sr-only">Loading library data...</div>
      <div className="lib-skeleton-card">
        <div className="lib-skeleton-avatar" />
        <div className="lib-skeleton-lines">
          <div className="lib-skeleton-line w40" />
          <div className="lib-skeleton-line w60 thin" />
        </div>
      </div>
      {[0, 1, 2, 3].map((i) => (
        <div className="lib-skeleton-row" key={i}>
          <div className="lib-skeleton-art" />
          <div className="lib-skeleton-lines">
            <div className="lib-skeleton-line w30" />
            <div className="lib-skeleton-line w50 thin" />
          </div>
        </div>
      ))}
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
  focusAlbumId,
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
  focusAlbumId?: string;
}) {
  return (
    <div className="enhanced-section" data-section={type}>
      <div className="enhanced-section-header">
        <span className="enhanced-section-title">{label}</span>
        <span className="enhanced-section-count">
          {sectionCountLabel(albums.length, sectionTrackTotal(albums))}
        </span>
      </div>
      <div className="enhanced-album-grid lib-album-list">
        {albums.map((album) => (
          <EnhancedAlbumWrapper
            album={album}
            artist={artist}
            isAdmin={isAdmin}
            selected={selected}
            onSelectedChange={onSelectedChange}
            onAlbumDeleted={() => onAlbumDeleted(album.id)}
            onReload={onReload}
            focused={focusAlbumId != null && String(album.id) === focusAlbumId}
            key={String(album.id)}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * how many of the release's tracks are not owned. before the panel has
 * fetched the canonical tracklist this is the source's track count against
 * what we hold; after, it is the diffed missing rows themselves.
 */
function albumGap(album: EnhancedAlbum, rows: EnhancedTrack[]): number {
  const owned = (album.tracks ?? []).length;
  if (album._canonicalTracksLoaded) {
    return rows.filter((r) => (r as { _missingExpected?: boolean })._missingExpected).length;
  }
  const expected = Number(album.api_track_count || album.track_count || 0);
  return Math.max(0, expected - owned);
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
  artist,
  isAdmin,
  selected,
  onSelectedChange,
  onAlbumDeleted,
  onReload,
  focused = false,
}: {
  album: EnhancedAlbum;
  artist: Record<string, unknown> | undefined;
  isAdmin: boolean;
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  onAlbumDeleted: () => void;
  onReload: () => void;
  /** This is the album the URL pointed at. */
  focused?: boolean;
}) {
  // an issue's "edit details" asked for this album: open it, form and all
  const [editFocus] = useState(() => {
    const focus = peekArtistEdit(artist?.id as string | number | undefined);
    return albumMatchesEdit(focus, albumProp) ? focus : null;
  });
  // `focused` is the other way in: ?album= from the library's album grid. Both
  // set the initial value only, so the row can still be closed again by hand.
  const [expanded, setExpanded] = useState(Boolean(editFocus) || focused);
  const [thumbBroken, setThumbBroken] = useState(false);
  // the album's metadata form is behind "edit details" now, not always open
  const [editing, setEditing] = useState(Boolean(editFocus && isAdmin && !editFocus.trackId));

  // One scroll for both entry points. The timer is what makes it land: the
  // track panel renders only once expanded, so scrolling on mount would aim
  // at the collapsed row's height and stop short.
  useEffect(() => {
    if (!editFocus && !focused) return;
    if (editFocus) clearArtistEdit();
    // after the panel has drawn: the track row if one was asked for, else the album
    const timer = setTimeout(() => {
      const row = editFocus?.trackId
        ? Array.from(document.querySelectorAll<HTMLElement>('tr[data-track-id]')).find(
            (tr) => tr.dataset.trackId === editFocus.trackId,
          )
        : null;
      const target =
        row ?? document.getElementById(`enhanced-album-wrapper-${String(albumProp.id)}`);
      target?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    }, 250);
    return () => clearTimeout(timer);
  }, [editFocus, focused, albumProp.id]);

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
  const rows = getAlbumTrackRows(album);
  const albumTitle = typeof album.title === 'string' && album.title ? album.title : 'Unknown';
  const gap = albumGap(album, rows);

  /**
   * The canonical tracklist, fetched once the panel opens (library.js:3422).
   * It is what puts the missing rows into the table, and with them the only
   * way into "I Have This".
   *
   * Keyed on the album record in STATE, with the loading flag as the marker.
   * Any record without the flags is one the diff has not seen: the first
   * expand, a refetch from the page, or the fresh record an import hands
   * back, which is the case that matters most, because the row that was just
   * filled has to drop out. An in-place edit spreads the flags forward and is
   * left alone. The result is folded in only while the marker still stands;
   * a record swapped underneath it drops the stale diff and runs its own.
   */
  const artistName = String(artist?.name ?? '');
  const canonicalSeq = useRef(0);
  useEffect(() => {
    if (!expanded || album._canonicalTracksLoaded || album._canonicalTracksLoading) return;
    const seq = ++canonicalSeq.current;
    setAlbum((current) => ({ ...current, _canonicalTracksLoading: true }));
    void loadCanonicalTracks(album, artistName).then((patch) => {
      if (seq !== canonicalSeq.current) return;
      setAlbum((current) =>
        current._canonicalTracksLoading
          ? { ...current, ...patch, _canonicalTracksLoading: false }
          : current,
      );
    });
  }, [expanded, album, artistName]);

  const playAlbum = () => {
    if (!rows.length) {
      window.showToast?.(`No tracks found for ${albumTitle}`, 'info');
      return;
    }
    const tracks = rows.map((track) => queueTrackPayload(track, album, artist));
    void window.playTrackList?.(tracks, albumTitle);
  };

  return (
    <div
      className={`enhanced-album-wrapper${expanded ? ' expanded' : ''}`}
      id={`enhanced-album-wrapper-${album.id}`}
    >
      <div
        className={`enhanced-album-row${expanded ? ' expanded' : ''}`}
        id={`enhanced-album-row-${album.id}`}
        role="button"
        aria-expanded={expanded}
        tabIndex={0}
        onClick={() => setExpanded((open) => !open)}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setExpanded((open) => !open);
          }
        }}
      >
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
            <div className="enhanced-album-thumb-fallback" aria-hidden="true">
              ♪
            </div>
          )}
          <button
            type="button"
            className="enhanced-album-play-btn"
            aria-label={`Play ${albumTitle}`}
            title={`Play ${albumTitle}`}
            onClick={(e) => {
              e.stopPropagation();
              playAlbum();
            }}
          >
            <PlayIcon size={18} />
          </button>
        </div>

        <div className="enhanced-album-info-block">
          {/* `||`, not `??`: an EMPTY title falls back to "Unknown" too, which
              is what the vanilla did and what an untitled row needs. */}
          <span className="enhanced-album-title" title={String(album.title || '')}>
            {String(album.title || 'Unknown')}
          </span>
          <span className="enhanced-album-meta-line">{meta.metaLine}</span>
        </div>

        <div className="lib-album-side">
          {gap > 0 ? (
            <span
              className="lib-pill warn"
              title={`${gap} of the release's tracks are not in your library`}
            >
              {gap} missing
            </span>
          ) : null}
          {meta.primaryFormat ? (
            <span className={`enhanced-format-badge ${meta.formatClass}`}>
              {meta.primaryFormat}
            </span>
          ) : null}
          <span className="enhanced-album-expand-icon" aria-hidden="true">
            <ChevronIcon />
          </span>
        </div>
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
                rows={rows}
                artistId={artist?.id}
                artistName={String(artist?.name ?? '')}
                isAdmin={isAdmin}
                onArtApplied={(url) => setAlbum((current) => ({ ...current, thumb_url: url }))}
                onAlbumDeleted={onAlbumDeleted}
                onAlbumPatched={(fresh) => setAlbum(fresh as typeof albumProp)}
                editing={editing}
                onToggleEdit={() => setEditing((open) => !open)}
                artist={artist}
                onReassigned={onReload}
              />
              {/* the admin's form is behind "edit details" now, but a
                  non-admin has no such button — they keep the read-only row,
                  which is the only place release date, style, mood and
                  explicit are shown at all. */}
              {isAdmin ? (
                editing ? (
                  <AlbumMetaRow
                    album={album}
                    isAdmin
                    onSaved={(updates) => {
                      setAlbum((current) => ({ ...current, ...updates }));
                      setEditing(false);
                    }}
                  />
                ) : null
              ) : (
                <AlbumMetaRow album={album} isAdmin={false} onSaved={() => {}} />
              )}
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
