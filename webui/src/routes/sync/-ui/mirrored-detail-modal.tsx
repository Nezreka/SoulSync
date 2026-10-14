/**
 * The mirrored playlist's TRACKS detail modal — openMirroredPlaylistModal
 * (stats-automations.js 1066-1165) ported React-native.
 *
 * WHY PORTED AND NOT ADOPTED (the pools were adopted; this is not the same):
 * the vanilla modal's primary button, Discover, calls discoverMirroredPlaylist
 * → openYouTubeDiscoveryModal, which lives in sync-services.js and the flip
 * deletes. Adoption's whole premise — "it keeps working untouched" — is false
 * for its main action. Three of its remaining four buttons (Edit Source,
 * Auto-Sync, Delete) already have React implementations from P5g/P5e, so
 * adopting would also run the same action two ways depending on where it was
 * clicked, including two pipeline pollers unaware of each other.
 *
 * The vanilla function STAYS ALIVE in stats-automations.js: auto-sync.js calls
 * it three times (the board's Details button at 1180 and the rename /
 * source-ref reopen tails at 2403/2437) and shared-helpers.js once at 1755
 * (already typeof-guarded). Those die with the auto-sync board phase, and the
 * vanilla copy dies with them. stats-automations.js survives the flip anyway —
 * the two pool modals live there.
 *
 * Markup is transcribed class for class and label for label from 1120-1157;
 * every .mm-* class already exists in style.css (verified), so there is no new
 * styling here. The actions are props because the call sites differ.
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import type { MirroredPlaylistDetail } from '../-sync.api';
import type { MirroredTrack } from '../-sync.mirrored';

import {
  mirroredDetailSourceIcon,
  mirroredDetailSourceLabel,
  mirroredHeroArt,
  mirroredRowDuration,
  mirroredTotalRuntime,
  timeAgo,
} from '../-sync.mirrored';
import { skippedNoteText } from '../-sync.modal-core';
import { AddToPlaylistButton } from '../../../features/playlists/add-to-playlist';
import {
  clearPlaylistCover,
  isUserPlaylist,
  movedOrder,
  setPlaylistCoverFromTrack,
  setPlaylistCoverUrl,
  setPlaylistDescription,
  uploadPlaylistCover,
} from '../../../features/playlists/user-playlists';

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
}

interface EditProps {
  index: number;
  busy: boolean;
  dragging: boolean;
  over: boolean;
  onRemove: () => void;
  onDragStart: () => void;
  onDragOver: () => void;
  onDrop: () => void;
  onDragEnd: () => void;
}

function TrackRow({ track, edit }: { track: MirroredTrack; edit?: EditProps }) {
  const cls = `mm-row${edit?.dragging ? ' mm-row--dragging' : ''}${edit?.over ? ' mm-row--over' : ''}`;
  return (
    <div
      className={cls}
      draggable={edit && !edit.busy ? true : undefined}
      onDragStart={
        edit
          ? (e) => {
              e.dataTransfer.effectAllowed = 'move';
              // firefox won't start a drag without data
              e.dataTransfer.setData('text/plain', String(edit.index));
              edit.onDragStart();
            }
          : undefined
      }
      onDragOver={
        edit
          ? (e) => {
              e.preventDefault();
              edit.onDragOver();
            }
          : undefined
      }
      onDrop={
        edit
          ? (e) => {
              e.preventDefault();
              edit.onDrop();
            }
          : undefined
      }
      onDragEnd={edit?.onDragEnd}
    >
      <span className="mm-row-pos">{track.position}</span>
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
        <span className="mm-row-artist">{track.artist_name}</span>
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
}: MirroredDetailModalProps) {
  const tracks = (data.tracks ?? []) as MirroredTrack[];
  const own = isUserPlaylist(data);
  const editable = own && Boolean(onRemoveTrack && onReorder);
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
                {heroArt ? (
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
              <span className="mm-eyebrow">{own ? 'Your Playlist' : 'Mirrored Playlist'}</span>
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
              ) : data.description ? (
                <p className="mm-desc mm-desc-readonly">{data.description}</p>
              ) : null}
              <div className="mm-meta">
                {own ? null : <span className={`mm-source-pill ${source}`}>{srcLabel}</span>}
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
              </div>
              {skippedNote ? <div className="mm-skipped-note">{skippedNote}</div> : null}
            </div>
          </div>
          <button type="button" className="mm-close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>
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
            tracks.map((track, index) => (
              <TrackRow
                key={`${track.position ?? index}-${track.track_name ?? ''}`}
                track={track}
                edit={
                  editable
                    ? {
                        index,
                        busy,
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
                ? 'Nothing in here yet. Add songs with the + on any track.'
                : 'No tracks in this mirror yet.'}
            </div>
          )}
        </div>
        <div className="mm-actions">
          <button
            type="button"
            className="mm-btn mm-btn-danger"
            onClick={() => {
              // 1148 closes first, then asks — so the confirm is not behind
              // the overlay.
              onClose();
              onDelete();
            }}
          >
            {own ? 'Delete playlist' : 'Delete Mirror'}
          </button>
          <div className="mm-actions-right">
            {/* no source behind a user playlist, so nothing to point or pull */}
            {own ? null : (
              <button type="button" className="mm-btn mm-btn-ghost" onClick={onEditSource}>
                Edit Source
              </button>
            )}
            {own && onDuplicate ? (
              <button
                type="button"
                className="mm-btn mm-btn-ghost"
                title="Copy this playlist, with its tracks, cover and description"
                disabled={dupBusy}
                onClick={() => {
                  setDupBusy(true);
                  void onDuplicate().finally(() => setDupBusy(false));
                }}
              >
                {dupBusy ? 'Duplicating…' : 'Duplicate'}
              </button>
            ) : null}
            {onRefreshFromSource && !own ? (
              <div className="mm-btn-wrap">
                <button
                  type="button"
                  className="mm-btn mm-btn-ghost"
                  title="Pull the latest tracks from the source and discover the new ones. Your matches are kept, nothing is pushed to your server or downloaded"
                  onClick={onRefreshFromSource}
                >
                  Refresh from {srcLabel === 'unknown' ? 'source' : srcLabel}
                </button>
                <span className="mm-btn-caption">Pull only — nothing pushed or downloaded</span>
              </div>
            ) : null}
            {/* Runs the pipeline now; the header's "Auto-Sync" schedules it. */}
            <div className="mm-btn-wrap">
              <button
                type="button"
                className="mm-btn mm-btn-secondary"
                title={
                  own
                    ? "Match, push to your server and download what's missing"
                    : "Refresh from the source, match, push to your server and download what's missing"
                }
                onClick={onRunPipeline}
              >
                Sync & download
              </button>
              <span className="mm-btn-caption">Push the playlist and download what's missing</span>
            </div>
            <button type="button" className="mm-btn mm-btn-ghost" onClick={onClose}>
              Close
            </button>
            <button type="button" className="mm-btn mm-btn-primary" onClick={onDiscover}>
              {discoverLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
