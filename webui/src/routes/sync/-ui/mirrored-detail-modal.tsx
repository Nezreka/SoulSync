/**
 * The mirrored playlist's TRACKS detail modal — openMirroredPlaylistModal
 * (stats-automations.js 1066-1165) ported React-native.
 *
 * Discovery, source editing, pipeline and delete actions are supplied by
 * the native tab, rather than routed through classic dialog globals.
 *
 * The classic detail function remains in stats-automations.js for the root
 * Auto Sync manager, classic rename/source-ref reopen paths and the shared
 * playlist helper. Those consumers must migrate before it can be retired.
 *
 * Markup is transcribed class for class and label for label from 1120-1157;
 * every .mm-* class already exists in style.css (verified), so there is no new
 * styling here. The actions are props because the call sites differ.
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import type { MirroredPlaylistDetail } from '../-sync.api';
import type { MirroredTrack, MirroredTrackStatus } from '../-sync.mirrored';

import { isLiveBeatportRef, saveBeatportSnapshot } from '../-beatport.lists';
import {
  mirroredDetailSourceIcon,
  mirroredDetailSourceLabel,
  mirroredHeroArt,
  mirroredRowDuration,
  mirroredTotalRuntime,
  timeAgo,
  trackStatusChip,
  trackStatusSegments,
} from '../-sync.mirrored';
import { skippedNoteText } from '../-sync.modal-core';
import { AddSongsPanel } from '../../../features/playlists/add-songs-panel';
import { AddToPlaylistButton } from '../../../features/playlists/add-to-playlist';
import { restoreOriginalCover } from '../../../features/playlists/cover-art';
import { playPlaylist, type PlaylistPlayMode } from '../../../features/playlists/play-playlist';
import {
  isLikedPlaylist,
  clearPlaylistCover,
  isUserPlaylist,
  movedOrder,
  setPlaylistCoverFromTrack,
  setPlaylistCoverUrl,
  setPlaylistDescription,
  uploadPlaylistCover,
} from '../../../features/playlists/user-playlists';
import { CoverArtSheet } from './cover-art-sheet';
import { LikedArt } from './playlist-art';
import { useLibraryCheck } from './use-library-check';
import { usePopoverDismiss } from './use-popover-dismiss';

export interface MirroredDetailModalProps {
  playlistId: number;
  data: MirroredPlaylistDetail;
  /** Read once per render, as the vanilla reads the clock once per open. */
  now: number;
  onClose: () => void;
  /** 1148 — the vanilla closes the modal BEFORE it asks to delete. */
  onDelete: () => void;
  /** 1150 — Edit Source. */
  onEditSource: () => void;
  /** 1151 — Auto-Sync (runMirroredPlaylistPipeline). */
  onRunPipeline: () => void;
  /** pull the source + discover the new tracks, nothing pushed (#1413) */
  onRefreshFromSource?: () => void;
  /** 1153 — Discover (discoverMirroredPlaylist). */
  onDiscover: () => void;
  /** "View discovery" once one exists, the card no longer jumps there (#1403). */
  discoverLabel?: string;
  /** a user playlist's edits. set only for one (source 'soulsync'); a synced
   *  mirror's tracks belong to its source and stay read-only. */
  onRemoveTrack?: (position: number) => void;
  /** the full 1-based position list in its new order. */
  onReorder?: (order: number[]) => void;
  /** refetch the detail after a cover/description change. */
  onRefresh: () => void;
  /** copy the whole playlist; the tab opens the new one. */
  onDuplicate?: () => Promise<void>;
  /** a mirror only: an editable copy in My Playlists; the tab opens it. */
  onMakeMine?: () => Promise<void>;
  /** the wing it pool for this playlist. the tab closes this modal first, the
   *  pool is a vanilla overlay that sits under it (#1721). */
  onFindMatches?: () => void;
}

export type TrackSort = 'custom' | 'title' | 'artist' | 'album' | 'added' | 'duration';

export const TRACK_SORTS: { value: TrackSort; label: string }[] = [
  { value: 'custom', label: 'Custom order' },
  { value: 'added', label: 'Date added' },
  { value: 'title', label: 'Title' },
  { value: 'artist', label: 'Artist' },
  { value: 'album', label: 'Album' },
  { value: 'duration', label: 'Duration' },
];

/** the rows in a display order. the playlist's own order never changes:
 *  positions stay the track's, so remove still hits the right row. date added
 *  is newest first; rows from before it was recorded keep their order at the
 *  end. */
export function sortTracks(tracks: readonly MirroredTrack[], sort: TrackSort): MirroredTrack[] {
  if (sort === 'custom') return [...tracks];
  const text = (v: string | undefined) => (v ?? '').toLocaleLowerCase();
  const by: Record<Exclude<TrackSort, 'custom'>, (a: MirroredTrack, b: MirroredTrack) => number> = {
    title: (a, b) => text(a.track_name).localeCompare(text(b.track_name)),
    artist: (a, b) =>
      text(a.artist_name).localeCompare(text(b.artist_name)) ||
      text(a.album_name).localeCompare(text(b.album_name)),
    album: (a, b) => text(a.album_name).localeCompare(text(b.album_name)),
    duration: (a, b) => (a.duration_ms ?? 0) - (b.duration_ms ?? 0),
    added: (a, b) => {
      if (!a.added_at || !b.added_at) return a.added_at ? -1 : b.added_at ? 1 : 0;
      return b.added_at.localeCompare(a.added_at);
    },
  };
  // a stable sort, ties fall back to the playlist's own order
  return tracks
    .map((t, i) => ({ t, i }))
    .sort((x, y) => by[sort](x.t, y.t) || x.i - y.i)
    .map((x) => x.t);
}

function PlayGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path d="M8 5.5v13l11-6.5z" fill="currentColor" />
    </svg>
  );
}

function ShuffleGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path
        d="M16 4h4v4M4 20 20 4M20 16v4h-4M15 15l5 5M4 4l5 5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function QueueGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path d="M4 10h12v2H4zm0-4h12v2H4zm0 8h8v2H4zm10 0v6l5-3z" fill="currentColor" />
    </svg>
  );
}

/**
 * a description that is only the source's own link (deezer hands its URL over
 * as the description) reads as a raw URL under the title. that one becomes an
 * "Open on Deezer" link instead; anything with words in it stays a description.
 */
export function descriptionLink(description: string | null | undefined): string | null {
  const text = (description ?? '').trim();
  return /^https?:\/\/\S+$/i.test(text) ? text : null;
}

export interface MoreMenuItem {
  label: string;
  /** one line under the label, for the ones whose effect isn't obvious */
  hint?: string;
  danger?: boolean;
  /** a line above this one */
  separated?: boolean;
  busy?: boolean;
  onSelect: () => void;
}

/**
 * the hero's ⋯: everything that isn't play, sync or identify. it renders on
 * body for the same reason the cover menu does (the hero clips, the modal's
 * transform traps fixed children) and sits under the button, kept on screen.
 */
function MoreMenu({
  anchor,
  items,
  onClose,
}: {
  anchor: HTMLElement;
  items: MoreMenuItem[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  usePopoverDismiss({ ref, anchor, onClose });
  useLayoutEffect(() => {
    const place = () => {
      const menu = ref.current;
      if (!menu) return;
      const box = anchor.getBoundingClientRect();
      // right-aligned under the button, the way a ⋯ menu opens
      setPos(
        placeCoverMenu(
          { top: box.top, bottom: box.bottom, left: box.right - menu.offsetWidth },
          { width: menu.offsetWidth, height: menu.offsetHeight },
          { width: window.innerWidth, height: window.innerHeight },
        ),
      );
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [anchor]);
  return createPortal(
    <div
      ref={ref}
      className="mm-more-menu"
      role="menu"
      data-popover-layer=""
      style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: 'hidden' }}
    >
      {items.map((item) => (
        <div key={item.label}>
          {item.separated ? <div className="mm-more-sep" /> : null}
          <button
            type="button"
            role="menuitem"
            className={`mm-more-item${item.danger ? ' mm-more-item--danger' : ''}`}
            disabled={item.busy}
            onClick={() => {
              onClose();
              item.onSelect();
            }}
          >
            <span className="mm-more-label">{item.label}</span>
            {item.hint ? <span className="mm-more-hint">{item.hint}</span> : null}
          </button>
        </div>
      ))}
    </div>,
    document.body,
  );
}

interface EditProps {
  index: number;
  busy: boolean;
  /** off while the list shows a sorted view: a drag there means nothing */
  canDrag: boolean;
  dragging: boolean;
  over: boolean;
  onRemove: () => void;
  onDragStart: () => void;
  onDragOver: () => void;
  onDrop: () => void;
  onDragEnd: () => void;
}

function TrackRow({
  track,
  edit,
  onPlay,
}: {
  track: MirroredTrack;
  edit?: EditProps;
  /** play the playlist from this song on; the number turns into it on hover */
  onPlay?: () => void;
}) {
  const cls = `mm-row${edit?.dragging ? ' mm-row--dragging' : ''}${edit?.over ? ' mm-row--over' : ''}`;
  const chip = trackStatusChip(track.status);
  return (
    <div
      className={cls}
      draggable={edit && edit.canDrag && !edit.busy ? true : undefined}
      onDragStart={
        edit?.canDrag
          ? (e) => {
              e.dataTransfer.effectAllowed = 'move';
              // firefox won't start a drag without data
              e.dataTransfer.setData('text/plain', String(edit.index));
              edit.onDragStart();
            }
          : undefined
      }
      onDragOver={
        edit?.canDrag
          ? (e) => {
              e.preventDefault();
              edit.onDragOver();
            }
          : undefined
      }
      onDrop={
        edit?.canDrag
          ? (e) => {
              e.preventDefault();
              edit.onDrop();
            }
          : undefined
      }
      onDragEnd={edit?.onDragEnd}
    >
      <span className="mm-row-pos">
        <span className="mm-row-num">{track.position}</span>
        {onPlay ? (
          <button
            type="button"
            className="mm-row-play"
            aria-label={`Play from ${track.track_name ?? 'this song'}`}
            title="Play from here"
            onClick={(e) => {
              e.stopPropagation();
              onPlay();
            }}
          >
            <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
              <path d="M8 5.5v13l11-6.5z" fill="currentColor" />
            </svg>
          </button>
        ) : null}
      </span>
      {track.image_url ? (
        <img
          className="mm-row-art"
          src={track.image_url}
          loading="lazy"
          // 1102: on a broken image the vanilla swaps the <img> for the empty
          // tile in place. Its `this.onerror=null` guard has no React
          // equivalent and needs none — dropping the src is what stops a
          // second error from firing.
          onError={(event) => {
            const img = event.currentTarget;
            img.classList.add('mm-art-empty');
            img.removeAttribute('src');
          }}
        />
      ) : (
        <span className="mm-row-art mm-art-empty" />
      )}
      <div className="mm-row-main">
        <span className="mm-row-title">{track.track_name}</span>
        <span className="mm-row-artist">
          <span className="mm-row-artist-name">{track.artist_name}</span>
          {chip ? (
            <span className="mm-row-st" data-st={track.status} title={chip}>
              {chip}
            </span>
          ) : null}
        </span>
      </div>
      <span className="mm-row-album">{track.album_name || ''}</span>
      <span className="mm-row-dur">{mirroredRowDuration(track.duration_ms)}</span>
      {/* copy it onto one of your playlists, from any mirror or your own */}
      <AddToPlaylistButton
        track={{
          track_name: track.track_name ?? '',
          artist_name: track.artist_name ?? '',
          album_name: track.album_name ?? '',
          duration_ms: track.duration_ms ?? 0,
          image_url: track.image_url ?? null,
        }}
        className="mm-row-add"
        size={15}
      />
      {edit ? (
        <button
          type="button"
          className="mm-row-remove"
          disabled={edit.busy}
          title="Remove from this playlist"
          aria-label={`Remove ${track.track_name ?? 'track'} from this playlist`}
          onClick={edit.onRemove}
        >
          &times;
        </button>
      ) : null}
    </div>
  );
}

/**
 * where the cover menu goes. it renders on body (the hero clips with
 * overflow hidden and the modal's transform traps fixed children), so it
 * places itself off the cover: below if it fits, else above, else the
 * roomier side, always kept on screen.
 */
export function placeCoverMenu(
  anchor: { top: number; bottom: number; left: number },
  menu: { width: number; height: number },
  viewport: { width: number; height: number },
  gap = 8,
  margin = 8,
): { top: number; left: number } {
  const below = viewport.height - anchor.bottom - gap - margin;
  const above = anchor.top - gap - margin;
  let top: number;
  if (menu.height <= below) top = anchor.bottom + gap;
  else if (menu.height <= above) top = anchor.top - gap - menu.height;
  else top = below >= above ? anchor.bottom + gap : anchor.top - gap - menu.height;
  top = Math.max(margin, Math.min(top, viewport.height - margin - menu.height));
  const left = Math.max(margin, Math.min(anchor.left, viewport.width - margin - menu.width));
  return { top, left };
}

export function MirroredDetailModal({
  playlistId,
  data,
  now,
  onClose,
  onDelete,
  onEditSource,
  onRunPipeline,
  onRefreshFromSource,
  onDiscover,
  discoverLabel = 'Identify',
  onRemoveTrack,
  onReorder,
  onRefresh,
  onDuplicate,
  onMakeMine,
  onFindMatches,
}: MirroredDetailModalProps) {
  const tracks = (data.tracks ?? []) as MirroredTrack[];
  const own = isUserPlaylist(data);
  const liked = isLikedPlaylist(data);
  const [sort, setSort] = useState<TrackSort>(liked ? 'added' : 'custom');
  // an empty playlist of yours opens straight onto finding songs for it
  const [addOpen, setAddOpen] = useState(own && tracks.length === 0);
  const [playing, setPlaying] = useState<PlaylistPlayMode | null>(null);
  const [makingMine, setMakingMine] = useState(false);
  const [snapshotting, setSnapshotting] = useState(false);
  const saveSnapshot = async () => {
    if (snapshotting) return;
    setSnapshotting(true);
    try {
      const result = await saveBeatportSnapshot(String(data.source_playlist_id ?? ''));
      if (result.success) {
        window.showToast?.(
          `Saved "${result.name}" (${result.track_count} tracks) to your mirrored playlists`,
          'success',
        );
      } else {
        window.showToast?.(result.error || 'Snapshot failed', 'error');
      }
    } catch {
      window.showToast?.('Snapshot failed', 'error');
    } finally {
      setSnapshotting(false);
    }
  };
  // click a part of the status line to see just those tracks
  const [statusFilter, setStatusFilter] = useState<MirroredTrackStatus | null>(null);
  // tracks nothing has checked against the library yet get checked now, so
  // the line says what's owned instead of "not checked yet" (#1910)
  const checkingLibrary = useLibraryCheck(
    playlistId,
    data.library_check_due ?? data.status_counts?.unchecked ?? 0,
    onRefresh,
  );
  const segments = trackStatusSegments(data.status_counts).map((seg) =>
    seg.status === 'unchecked' && checkingLibrary ? { ...seg, label: 'being checked…' } : seg,
  );
  const shownTracks = sortTracks(tracks, sort).filter(
    (t) => !statusFilter || t.status === statusFilter,
  );
  // dragging only means something in the playlist's own order, all of it
  const editable = own && Boolean(onRemoveTrack && onReorder);
  const draggable = editable && sort === 'custom' && !statusFilter;
  const runPlay = async (mode: PlaylistPlayMode, from = 0) => {
    if (playing) return;
    setPlaying(mode);
    try {
      // what you see is what plays: a sorted view plays in that order, and a
      // row's play starts there and carries on down the list
      await playPlaylist(
        shownTracks.slice(from),
        data.display_name || data.name || 'this playlist',
        mode,
      );
    } finally {
      setPlaying(null);
    }
  };
  const [moreAnchor, setMoreAnchor] = useState<HTMLElement | null>(null);
  const sourceLink = own ? null : descriptionLink(data.description);
  // cover + description state. meta edits refetch through onRefresh, so the
  // modal always shows what's saved, same as the track edits below.
  const [coverOpen, setCoverOpen] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const [coverError, setCoverError] = useState<string | null>(null);
  const [coverUrlDraft, setCoverUrlDraft] = useState('');
  const [descEditing, setDescEditing] = useState(false);
  const [descDraft, setDescDraft] = useState('');
  const [descBusy, setDescBusy] = useState(false);
  const [dupBusy, setDupBusy] = useState(false);
  const [coverArtOpen, setCoverArtOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const restoreCover = async () => {
    setRestoring(true);
    try {
      await restoreOriginalCover(playlistId);
      onRefresh();
    } catch (err) {
      window.showToast?.(
        err instanceof Error ? err.message : 'Could not restore the cover',
        'error',
      );
    } finally {
      setRestoring(false);
    }
  };
  useEffect(() => {
    setCoverOpen(false);
    setCoverError(null);
    setDescEditing(false);
  }, [data]);
  const coverWrapRef = useRef<HTMLDivElement>(null);
  const coverMenuRef = useRef<HTMLDivElement>(null);
  const [coverPos, setCoverPos] = useState<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    if (!coverOpen) {
      setCoverPos(null);
      return;
    }
    const place = () => {
      const wrap = coverWrapRef.current;
      const menu = coverMenuRef.current;
      if (!wrap || !menu) return;
      setCoverPos(
        placeCoverMenu(
          wrap.getBoundingClientRect(),
          { width: menu.offsetWidth, height: menu.offsetHeight },
          { width: window.innerWidth, height: window.innerHeight },
        ),
      );
    };
    place();
    // it lives outside the modal now, so close it the way a menu closes
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (coverMenuRef.current?.contains(target) || coverWrapRef.current?.contains(target)) return;
      setCoverOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCoverOpen(false);
    };
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
    // error text and the remove row change its height
  }, [coverOpen, coverError, data.image_url]);
  const runCoverEdit = async (edit: () => Promise<unknown>) => {
    setCoverBusy(true);
    setCoverError(null);
    try {
      await edit();
      setCoverOpen(false);
      onRefresh();
    } catch (err) {
      setCoverError(err instanceof Error ? err.message : 'Could not update the cover');
    } finally {
      setCoverBusy(false);
    }
  };
  const saveDescription = async () => {
    setDescBusy(true);
    try {
      await setPlaylistDescription(playlistId, descDraft);
      setDescEditing(false);
      onRefresh();
    } catch (err) {
      window.showToast?.(
        err instanceof Error ? err.message : 'Could not save the description',
        'error',
      );
    } finally {
      setDescBusy(false);
    }
  };
  const artTracks = tracks.filter((t) => t.image_url);
  // one edit at a time: positions are only true until the refetch lands, so a
  // second click on a stale row could hit the wrong track. a new payload
  // (the refetch) is what frees it.
  const [busy, setBusy] = useState(false);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  useEffect(() => {
    setBusy(false);
    setDragFrom(null);
    setDragOver(null);
  }, [data]);
  const source = data.source || 'unknown';
  const sourceIcon = mirroredDetailSourceIcon(source);
  const srcLabel = mirroredDetailSourceLabel(source);
  const heroArt = mirroredHeroArt(data.image_url, tracks);
  const { totalMs, label: totalLabel } = mirroredTotalRuntime(tracks);
  // why a 395 track playlist mirrors as 365 (#1613)
  const skippedNote = own ? null : skippedNoteText(data.source_skipped ?? undefined, srcLabel);

  return (
    <div
      id="mirrored-track-modal"
      className="mirrored-modal-overlay"
      // 1159 — backdrop only, never a click inside the panel.
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="mirrored-modal">
        <div className="mm-hero">
          {heroArt ? (
            <div className="mm-hero-bg" style={{ backgroundImage: `url('${heroArt}')` }} />
          ) : null}
          <div className="mm-hero-content">
            {own ? (
              <div className="mm-cover-wrap" ref={coverWrapRef}>
                {liked && !data.image_url ? (
                  <div className="mm-cover mm-cover-liked">
                    <LikedArt />
                  </div>
                ) : heroArt ? (
                  <div className="mm-cover" style={{ backgroundImage: `url('${heroArt}')` }} />
                ) : (
                  <div className="mm-cover mm-cover-empty soulsync">{sourceIcon}</div>
                )}
                <button
                  type="button"
                  className="mm-cover-edit"
                  disabled={coverBusy}
                  onClick={() => {
                    setCoverError(null);
                    setCoverOpen((v) => !v);
                  }}
                >
                  {coverBusy ? 'Saving…' : 'Change cover'}
                </button>
                {coverOpen
                  ? createPortal(
                      <div
                        ref={coverMenuRef}
                        className="mm-cover-menu"
                        role="menu"
                        data-popover-layer=""
                        style={
                          coverPos
                            ? { top: coverPos.top, left: coverPos.left }
                            : { top: 0, left: 0, visibility: 'hidden' }
                        }
                      >
                        <label className="mm-cover-item">
                          Upload a photo…
                          <input
                            type="file"
                            accept="image/*"
                            hidden
                            disabled={coverBusy}
                            onChange={(event) => {
                              const file = event.target.files?.[0];
                              event.target.value = '';
                              if (file)
                                void runCoverEdit(() => uploadPlaylistCover(playlistId, file));
                            }}
                          />
                        </label>
                        {artTracks.length > 0 ? (
                          <>
                            <div className="mm-cover-label">From this playlist</div>
                            <div className="mm-cover-tiles">
                              {artTracks.slice(0, 8).map((t, i) => (
                                <button
                                  key={`${t.position ?? i}-${t.image_url}`}
                                  type="button"
                                  className="mm-cover-tile"
                                  title={`${t.track_name ?? ''} — ${t.artist_name ?? ''}`}
                                  disabled={coverBusy}
                                  onClick={() =>
                                    void runCoverEdit(() =>
                                      setPlaylistCoverFromTrack(playlistId, t.position ?? i + 1),
                                    )
                                  }
                                >
                                  <img src={t.image_url} alt="" loading="lazy" />
                                </button>
                              ))}
                            </div>
                          </>
                        ) : null}
                        <div className="mm-cover-label">Or paste an image link</div>
                        <div className="mm-cover-urlrow">
                          <input
                            type="url"
                            className="mm-cover-url"
                            placeholder="https://…"
                            value={coverUrlDraft}
                            disabled={coverBusy}
                            onChange={(event) => setCoverUrlDraft(event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter' && coverUrlDraft.trim()) {
                                void runCoverEdit(() =>
                                  setPlaylistCoverUrl(playlistId, coverUrlDraft.trim()),
                                );
                              }
                            }}
                          />
                          <button
                            type="button"
                            className="mm-btn mm-btn-ghost mm-btn-sm"
                            disabled={coverBusy || !coverUrlDraft.trim()}
                            onClick={() =>
                              void runCoverEdit(() =>
                                setPlaylistCoverUrl(playlistId, coverUrlDraft.trim()),
                              )
                            }
                          >
                            Set
                          </button>
                        </div>
                        {data.image_url ? (
                          <button
                            type="button"
                            className="mm-cover-item mm-cover-remove"
                            disabled={coverBusy}
                            onClick={() => void runCoverEdit(() => clearPlaylistCover(playlistId))}
                          >
                            Remove cover
                          </button>
                        ) : null}
                        {coverError ? <div className="mm-cover-error">{coverError}</div> : null}
                      </div>,
                      document.body,
                    )
                  : null}
              </div>
            ) : heroArt ? (
              <div className="mm-cover" style={{ backgroundImage: `url('${heroArt}')` }} />
            ) : (
              <div className={`mm-cover mm-cover-empty ${source}`}>{sourceIcon}</div>
            )}
            <div className="mm-hero-info">
              <span className="mm-eyebrow">
                {own ? null : <span className={`mm-source-pill ${source}`}>{srcLabel}</span>}
                {own ? 'Your Playlist' : 'Mirrored Playlist'}
              </span>
              <h2 className="mm-title">{data.name}</h2>
              {own ? (
                descEditing ? (
                  <div className="mm-desc-edit">
                    <textarea
                      className="mm-desc-input"
                      value={descDraft}
                      maxLength={1000}
                      rows={2}
                      placeholder="What is this mix for?"
                      disabled={descBusy}
                      onChange={(event) => setDescDraft(event.target.value)}
                    />
                    <div className="mm-desc-actions">
                      <button
                        type="button"
                        className="mm-btn mm-btn-ghost mm-btn-sm"
                        disabled={descBusy}
                        onClick={() => setDescEditing(false)}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        className="mm-btn mm-btn-primary mm-btn-sm"
                        disabled={descBusy}
                        onClick={() => void saveDescription()}
                      >
                        {descBusy ? 'Saving…' : 'Save'}
                      </button>
                    </div>
                  </div>
                ) : data.description ? (
                  <div className="mm-desc">
                    <p>{data.description}</p>
                    <button
                      type="button"
                      className="mm-desc-pencil"
                      title="Edit description"
                      aria-label="Edit description"
                      onClick={() => {
                        setDescDraft(data.description ?? '');
                        setDescEditing(true);
                      }}
                    >
                      ✎
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="mm-desc-add"
                    onClick={() => {
                      setDescDraft('');
                      setDescEditing(true);
                    }}
                  >
                    Add a description
                  </button>
                )
              ) : data.description && !sourceLink ? (
                <p className="mm-desc mm-desc-readonly">{data.description}</p>
              ) : null}
              <div className="mm-meta">
                {data.owner ? (
                  <>
                    <span className="mm-meta-item">{data.owner}</span>
                    <span className="mm-dot">&middot;</span>
                  </>
                ) : null}
                <span className="mm-meta-item">{tracks.length} tracks</span>
                {totalMs ? (
                  <>
                    <span className="mm-dot">&middot;</span>
                    <span className="mm-meta-item">{totalLabel}</span>
                  </>
                ) : null}
                <span className="mm-dot">&middot;</span>
                <span className="mm-meta-item">
                  {own ? 'Edited' : 'Mirrored'} {timeAgo(data.updated_at || data.mirrored_at, now)}
                </span>
                {sourceLink ? (
                  <>
                    <span className="mm-dot">&middot;</span>
                    <a
                      className="mm-source-link"
                      href={sourceLink}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Open on {srcLabel === 'unknown' ? 'the source' : srcLabel} ↗
                    </a>
                  </>
                ) : null}
              </div>
              {skippedNote ? <div className="mm-skipped-note">{skippedNote}</div> : null}
              {segments.length > 0 ? (
                <div className="mm-status" role="group" aria-label="Where the tracks stand">
                  {segments.map((seg) => (
                    <button
                      key={seg.status}
                      type="button"
                      className={`mm-status-seg${statusFilter === seg.status ? ' mm-status-seg--on' : ''}`}
                      data-st={seg.status}
                      aria-pressed={statusFilter === seg.status}
                      title={statusFilter === seg.status ? 'Show every track' : 'Show only these'}
                      onClick={() =>
                        setStatusFilter((cur) => (cur === seg.status ? null : seg.status))
                      }
                    >
                      <span className="mm-status-dot" aria-hidden="true" />
                      <b>{seg.count}</b> {seg.label}
                    </button>
                  ))}
                  {onFindMatches && (data.status_counts?.needs_match ?? 0) > 0 ? (
                    <button type="button" className="mm-status-fix" onClick={onFindMatches}>
                      Find matches
                    </button>
                  ) : null}
                </div>
              ) : null}
              {/* play is the one big thing; the rest sit beside it, quiet */}
              <div className="mm-play-row">
                <button
                  type="button"
                  className="mm-play"
                  aria-label={`Play ${data.display_name || data.name || 'this playlist'}`}
                  title="Play"
                  disabled={tracks.length === 0 || playing !== null}
                  aria-busy={playing === 'play' || undefined}
                  onClick={() => void runPlay('play')}
                >
                  {playing === 'play' ? (
                    <span className="mm-play-spin" aria-hidden="true" />
                  ) : (
                    <PlayGlyph />
                  )}
                </button>
                <button
                  type="button"
                  className="mm-round"
                  aria-label="Shuffle play"
                  title="Shuffle play"
                  disabled={tracks.length === 0 || playing !== null}
                  aria-busy={playing === 'shuffle' || undefined}
                  onClick={() => void runPlay('shuffle')}
                >
                  <ShuffleGlyph />
                </button>
                <button
                  type="button"
                  className="mm-round"
                  aria-label="Add to queue"
                  title="Add to queue"
                  disabled={tracks.length === 0 || playing !== null}
                  aria-busy={playing === 'queue' || undefined}
                  onClick={() => void runPlay('queue')}
                >
                  <QueueGlyph />
                </button>
                {editable ? (
                  <button
                    type="button"
                    className={`mm-pill${addOpen ? ' mm-pill--on' : ''}`}
                    aria-expanded={addOpen}
                    onClick={() => setAddOpen((v) => !v)}
                  >
                    <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
                      <path
                        d="M12 5v14M5 12h14"
                        stroke="currentColor"
                        strokeWidth="2.4"
                        strokeLinecap="round"
                      />
                    </svg>
                    Add songs
                  </button>
                ) : null}
                <span className="mm-play-gap" />
                {/* runs the pipeline now; the header's Auto-Sync schedules it */}
                <button
                  type="button"
                  className="mm-pill"
                  title={
                    own
                      ? "Match, push to your server and download what's missing"
                      : "Refresh from the source, match, push to your server and download what's missing"
                  }
                  onClick={onRunPipeline}
                >
                  Sync & download
                </button>
                <button type="button" className="mm-pill mm-pill--quiet" onClick={onDiscover}>
                  {discoverLabel}
                </button>
                <button
                  type="button"
                  className={`mm-round${moreAnchor ? ' mm-round--on' : ''}`}
                  aria-label="More actions"
                  aria-haspopup="menu"
                  aria-expanded={Boolean(moreAnchor)}
                  title="More"
                  onClick={(e) => {
                    const anchor = e.currentTarget;
                    setMoreAnchor((cur) => (cur ? null : anchor));
                  }}
                >
                  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                    <circle cx="5" cy="12" r="1.8" fill="currentColor" />
                    <circle cx="12" cy="12" r="1.8" fill="currentColor" />
                    <circle cx="19" cy="12" r="1.8" fill="currentColor" />
                  </svg>
                </button>
                {moreAnchor ? (
                  <MoreMenu
                    anchor={moreAnchor}
                    onClose={() => setMoreAnchor(null)}
                    items={[
                      ...(!own && onMakeMine
                        ? [
                            {
                              label: makingMine ? 'Copying…' : 'Copy to My Playlists',
                              hint: 'an editable copy, matches kept',
                              busy: makingMine,
                              onSelect: () => {
                                setMakingMine(true);
                                void onMakeMine().finally(() => setMakingMine(false));
                              },
                            },
                          ]
                        : []),
                      ...(own && onDuplicate
                        ? [
                            {
                              label: dupBusy ? 'Duplicating…' : 'Duplicate',
                              hint: 'a copy with its tracks, cover and description',
                              busy: dupBusy,
                              onSelect: () => {
                                setDupBusy(true);
                                void onDuplicate().finally(() => setDupBusy(false));
                              },
                            },
                          ]
                        : []),
                      // no source behind a user playlist, so nothing to pull or point
                      ...(!own && onRefreshFromSource
                        ? [
                            {
                              label: `Refresh from ${srcLabel === 'unknown' ? 'source' : srcLabel}`,
                              hint: 'pull the latest tracks, nothing pushed or downloaded',
                              onSelect: onRefreshFromSource,
                            },
                          ]
                        : []),
                      ...(!own ? [{ label: 'Edit source link', onSelect: onEditSource }] : []),
                      // a live beatport list follows the chart, so last week's
                      // tracks drop out. a snapshot keeps today's as its own
                      // playlist that never refreshes
                      ...(data.source === 'beatport' && isLiveBeatportRef(data.source_playlist_id)
                        ? [
                            {
                              label: snapshotting ? 'Saving snapshot…' : 'Save snapshot',
                              hint: "today's list as its own playlist that never changes",
                              busy: snapshotting,
                              onSelect: () => void saveSnapshot(),
                            },
                          ]
                        : []),
                      {
                        label: 'Generate cover art',
                        hint: 'a new cover from its genres and mood',
                        separated: true,
                        onSelect: () => setCoverArtOpen(true),
                      },
                      ...(!own && data.custom_cover
                        ? [
                            {
                              label: restoring ? 'Restoring…' : 'Restore original cover',
                              hint: `back to ${srcLabel === 'unknown' ? "the source's" : `${srcLabel}'s`} cover`,
                              busy: restoring,
                              onSelect: () => void restoreCover(),
                            },
                          ]
                        : []),
                      {
                        label: own ? 'Delete playlist' : 'Delete mirror',
                        danger: true,
                        separated: true,
                        // 1148 closes first, then asks, so the confirm is not
                        // behind the overlay
                        onSelect: () => {
                          onClose();
                          onDelete();
                        },
                      },
                    ]}
                  />
                ) : null}
              </div>
            </div>
          </div>
          <button type="button" className="mm-close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>
        {coverArtOpen ? (
          <CoverArtSheet
            playlistId={playlistId}
            playlistName={data.display_name || data.name || 'this playlist'}
            onClose={() => setCoverArtOpen(false)}
            onApplied={onRefresh}
          />
        ) : null}
        {addOpen && editable ? (
          <AddSongsPanel
            playlistId={playlistId}
            playlistName={data.display_name || data.name || 'this playlist'}
            existing={tracks}
            onAdded={onRefresh}
            onClose={() => setAddOpen(false)}
          />
        ) : null}
        {tracks.length > 1 ? (
          <div className="mm-sortbar">
            {editable && sort !== 'custom' ? (
              <span className="mm-sort-note">Back to custom order to drag tracks around</span>
            ) : null}
            <label className="mm-sort">
              <select
                value={sort}
                aria-label="Sort tracks"
                onChange={(e) => setSort(e.target.value as TrackSort)}
              >
                {TRACK_SORTS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : null}
        <div className={`mm-list${editable ? ' mm-list--editable' : ''}`}>
          <div className="mm-list-head">
            <span>#</span>
            <span />
            <span>Title</span>
            <span>Album</span>
            <span className="mm-col-dur">Time</span>
            <span />
            {editable ? <span /> : null}
          </div>
          {tracks.length > 0 ? (
            shownTracks.map((track, index) => (
              <TrackRow
                key={`${track.position ?? index}-${track.track_name ?? ''}`}
                track={track}
                onPlay={() => void runPlay('play', index)}
                edit={
                  editable
                    ? {
                        index,
                        busy,
                        canDrag: draggable,
                        dragging: dragFrom === index,
                        over: dragOver === index && dragFrom !== null && dragFrom !== index,
                        onRemove: () => {
                          setBusy(true);
                          onRemoveTrack?.(track.position ?? index + 1);
                        },
                        onDragStart: () => setDragFrom(index),
                        onDragOver: () => setDragOver(index),
                        onDrop: () => {
                          const from = dragFrom;
                          setDragFrom(null);
                          setDragOver(null);
                          if (from === null || from === index) return;
                          setBusy(true);
                          onReorder?.(movedOrder(tracks.length, from, index));
                        },
                        onDragEnd: () => {
                          setDragFrom(null);
                          setDragOver(null);
                        },
                      }
                    : undefined
                }
              />
            ))
          ) : (
            <div className="mm-empty">
              {own
                ? 'Nothing in here yet. Use Add songs above, or the + on any track.'
                : 'No tracks in this mirror yet.'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
