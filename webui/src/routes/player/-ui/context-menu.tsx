/**
 * Universal right-click / hold context menu for every media card and track row
 * in /player (movies, shows, seasons, episodes, albums, tracks, audiobooks,
 * podcasts, YouTube). Mounted once in PlayerShell via <ContextMenuHost />.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { triggerBrowserDownload, type DownloadItemType } from '../-lib/downloads';
import { albumItems, bookItem, episodeItems, movieItem } from '../-lib/play-items';
import {
  addToWatchlist,
  albumQuery,
  booksQuery,
  movieDetailQuery,
  removeFromWatchlist,
  showDetailQuery,
  type SongRef,
} from '../-player.api';
import { openAddToPlaylist } from './music/add-to-playlist';
import { getEngine, playItems } from './playback/use-player';

export interface ContextMenuExtraAction {
  label: string;
  onSelect: () => void;
  danger?: boolean;
}

export interface ContextMenuTarget {
  x: number;
  y: number;
  kind:
    | 'movie'
    | 'show'
    | 'season'
    | 'episode'
    | 'album'
    | 'track'
    | 'audiobook'
    | 'podcast'
    | 'youtube'
    | 'other';
  id?: string | null;
  title: string;
  subtitle?: string | null;
  artwork?: string | null;
  href?: string | null;
  /** Optional custom play handler (e.g. TrackList row play, YouTube onOpen, etc.) */
  onPlay?: () => void;
  /** Optional custom queue handler */
  onQueue?: () => void;
  /** Optional like toggle for tracks */
  liked?: boolean;
  onToggleLike?: () => void;
  /** Optional SongRef for Add to Playlist */
  song?: SongRef | null;
  /** Optional Download itemType override */
  downloadType?: DownloadItemType | null;
  downloadId?: string | null;
  /** Whether the user actually owns/has this item locally (default true) */
  owned?: boolean;
  /** Optional request action for missing/unowned items */
  onRequest?: () => void;
  /** Extra card-specific actions (e.g. Remove from Continue Watching) */
  extraActions?: ContextMenuExtraAction[];
}

type Listener = (target: ContextMenuTarget | null) => void;
const listeners = new Set<Listener>();
let currentTarget: ContextMenuTarget | null = null;

export function openContextMenu(target: ContextMenuTarget) {
  currentTarget = target;
  listeners.forEach((fn) => fn(target));
}

export function closeContextMenu() {
  currentTarget = null;
  listeners.forEach((fn) => fn(null));
}

/** Infer a ContextMenuTarget from any RailCard */
export function inferContextTargetFromCard(
  e: React.MouseEvent,
  card: {
    title: string;
    subtitle?: string;
    art?: string | null;
    href?: string;
    source?: { itemType: string; itemId: string };
    preview?: { kind: 'movie' | 'show'; id: number };
    play?: { kind: string; id?: number | string };
  },
  opts?: {
    onOpen?: () => void;
    extraActions?: ContextMenuExtraAction[];
  },
): ContextMenuTarget {
  const href = card.href ?? '';
  let kind: ContextMenuTarget['kind'] = 'other';
  let id: string | null = card.source?.itemId ?? null;
  let downloadType: DownloadItemType | null = null;
  let downloadId: string | null = null;

  if (
    card.source?.itemType === 'movie' ||
    card.preview?.kind === 'movie' ||
    /\/player\/movies\/(\d+)/.test(href)
  ) {
    kind = 'movie';
    id = id ?? String(card.preview?.id ?? href.match(/\/player\/movies\/(\d+)/)?.[1] ?? '');
    if (id) {
      downloadType = 'movie';
      downloadId = id;
    }
  } else if (card.source?.itemType === 'episode') {
    kind = 'episode';
    if (id) {
      downloadType = 'episode';
      downloadId = id;
    }
  } else if (/\/player\/tv\/(\d+)\?season=(\d+)/.test(href)) {
    kind = 'season';
    id = href.match(/\/player\/tv\/(\d+)/)?.[1] ?? id;
  } else if (
    card.source?.itemType === 'show' ||
    card.preview?.kind === 'show' ||
    /\/player\/tv\/(\d+)/.test(href)
  ) {
    kind = 'show';
    id = id ?? String(card.preview?.id ?? href.match(/\/player\/tv\/(\d+)/)?.[1] ?? '');
  } else if (card.play?.kind === 'album' || /\/player\/music\/album\/(\d+)/.test(href)) {
    kind = 'album';
    id = String(card.play?.id ?? href.match(/\/player\/music\/album\/(\d+)/)?.[1] ?? id ?? '');
  } else if (card.source?.itemType === 'track') {
    kind = 'track';
    if (id) {
      downloadType = 'track';
      downloadId = id;
    }
  } else if (
    card.source?.itemType === 'audiobook' ||
    /\/player\/audiobooks\/([^/?#]+)/.test(href)
  ) {
    kind = 'audiobook';
    id = id ?? href.match(/\/player\/audiobooks\/([^/?#]+)/)?.[1] ?? null;
    if (id) {
      downloadType = 'audiobook';
      downloadId = id;
    }
  } else if (
    card.source?.itemType === 'podcast_episode' ||
    /\/player\/podcasts\/([^/?#]+)/.test(href)
  ) {
    kind = 'podcast';
    id = id ?? href.match(/\/player\/podcasts\/([^/?#]+)/)?.[1] ?? null;
    if (card.source?.itemType === 'podcast_episode' && id) {
      downloadType = 'podcast';
      downloadId = id;
    }
  } else if (card.source?.itemType === 'youtube') {
    kind = 'youtube';
    if (id) {
      downloadType = 'youtube';
      downloadId = id;
    }
  }

  return {
    x: e.clientX,
    y: e.clientY,
    kind,
    id: id || null,
    title: card.title,
    subtitle: card.subtitle,
    artwork: card.art ?? null,
    href: card.href ?? null,
    onPlay: opts?.onOpen,
    downloadType,
    downloadId,
    extraActions: opts?.extraActions,
  };
}

const KIND_LABELS: Record<ContextMenuTarget['kind'], string> = {
  movie: 'Movie',
  show: 'TV Series',
  season: 'Season',
  episode: 'Episode',
  album: 'Album',
  track: 'Track',
  audiobook: 'Audiobook',
  podcast: 'Podcast',
  youtube: 'YouTube',
  other: 'Media',
};

export function ContextMenuHost() {
  const [target, setTarget] = useState<ContextMenuTarget | null>(currentTarget);
  const [busyMsg, setBusyMsg] = useState<string | null>(null);
  const [statusNote, setStatusNote] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const qc = useQueryClient();

  useEffect(() => {
    const fn: Listener = (next) => {
      setTarget(next);
      setBusyMsg(null);
      setStatusNote(null);
    };
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, []);

  useEffect(() => {
    if (!target) return;
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) closeContextMenu();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeContextMenu();
    };
    const onScroll = () => closeContextMenu();
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, [target]);

  if (!target) return null;

  const menuW = 268;
  const approxH = 340;
  const left = Math.max(12, Math.min(target.x, window.innerWidth - menuW - 12));
  const top = Math.max(12, Math.min(target.y, window.innerHeight - approxH - 12));

  const runPlay = async (shuffle = false) => {
    if (target.onPlay) {
      closeContextMenu();
      target.onPlay();
      return;
    }
    const idNum = target.id ? Number(target.id) : NaN;
    setBusyMsg('Starting playback…');
    try {
      if (target.kind === 'movie' && Number.isFinite(idNum)) {
        const movie = await qc.fetchQuery(movieDetailQuery(idNum));
        playItems([movieItem(movie)]);
        closeContextMenu();
        return;
      }
      if (target.kind === 'show' && Number.isFinite(idNum)) {
        const show = await qc.fetchQuery(showDetailQuery(idNum));
        const eps = episodeItems(show, show.seasons ?? []);
        if (eps.length) {
          playItems(eps, 0);
          closeContextMenu();
          return;
        }
      }
      if (target.kind === 'album' && Number.isFinite(idNum)) {
        const album = await qc.fetchQuery(albumQuery(idNum));
        if (!album) throw new Error('album not found');
        const items = albumItems(album);
        if (items.length) {
          playItems(items, shuffle ? Math.floor(Math.random() * items.length) : 0);
          if (shuffle) getEngine().toggleShuffle();
          closeContextMenu();
          return;
        }
      }
      if (target.kind === 'audiobook' && target.id) {
        const books = await qc.fetchQuery(booksQuery());
        const book = books.find((b) => b.asin === target.id);
        if (book) {
          playItems([bookItem(book)]);
          closeContextMenu();
          return;
        }
      }
      if (target.href) {
        closeContextMenu();
        void navigate({ to: target.href as '/player' });
      }
    } catch {
      setBusyMsg(null);
      setStatusNote('Could not start playback');
    }
  };

  const runQueue = async () => {
    if (target.onQueue) {
      target.onQueue();
      closeContextMenu();
      return;
    }
    const idNum = target.id ? Number(target.id) : NaN;
    setBusyMsg('Adding to queue…');
    try {
      if (target.kind === 'album' && Number.isFinite(idNum)) {
        const album = await qc.fetchQuery(albumQuery(idNum));
        if (!album) throw new Error('album not found');
        const items = albumItems(album);
        if (items.length) {
          getEngine().append(items);
          closeContextMenu();
          return;
        }
      } else if (target.kind === 'movie' && Number.isFinite(idNum)) {
        const movie = await qc.fetchQuery(movieDetailQuery(idNum));
        getEngine().append([movieItem(movie)]);
        closeContextMenu();
        return;
      } else if (target.kind === 'audiobook' && target.id) {
        const books = await qc.fetchQuery(booksQuery());
        const book = books.find((b) => b.asin === target.id);
        if (book) {
          getEngine().append([bookItem(book)]);
          closeContextMenu();
          return;
        }
      }
    } catch {
      setStatusNote('Could not add to queue');
    } finally {
      setBusyMsg(null);
    }
  };

  const runAddAlbumToPlaylist = async () => {
    const idNum = target.id ? Number(target.id) : NaN;
    if (!Number.isFinite(idNum)) return;
    setBusyMsg('Loading tracks…');
    try {
      const album = await qc.fetchQuery(albumQuery(idNum));
      if (!album) throw new Error('album not found');
      const items = albumItems(album);
      const songs: SongRef[] = items.map((it) => ({
        title: it.title,
        artist: it.subtitle ?? '',
        album: album.title,
        duration_ms: it.duration ? Math.round(it.duration * 1000) : null,
        lib2_track_id: Number(it.id),
      }));
      closeContextMenu();
      if (songs.length) openAddToPlaylist(songs);
    } catch {
      setBusyMsg(null);
      setStatusNote('Could not load album tracks');
    }
  };

  const runDownload = async () => {
    if (!target.downloadType || !target.downloadId) return;
    setBusyMsg('Preparing download…');
    const res = await triggerBrowserDownload({
      itemType: target.downloadType,
      itemId: target.downloadId,
      title: target.title,
      subtitle: target.subtitle,
      artwork: target.artwork,
    });
    if (res.ok) {
      void qc.invalidateQueries({ queryKey: ['player', 'downloads'] });
      closeContextMenu();
    } else {
      setBusyMsg(null);
      setStatusNote(res.error || 'Download blocked');
    }
  };

  const runWatchlist = async (add: boolean) => {
    if (!target.id || (target.kind !== 'movie' && target.kind !== 'show')) return;
    setBusyMsg(add ? 'Saving to watchlist…' : 'Removing from watchlist…');
    try {
      if (add) await addToWatchlist(target.kind, target.id);
      else await removeFromWatchlist(target.kind, target.id);
      void qc.invalidateQueries({ queryKey: ['player', 'watchlist'] });
      closeContextMenu();
    } catch {
      setBusyMsg(null);
      setStatusNote('Could not update watchlist');
    }
  };

  const isOwned = target.owned !== false;

  const canPlayDirect =
    isOwned &&
    (!!target.onPlay ||
      target.kind === 'movie' ||
      target.kind === 'show' ||
      target.kind === 'album' ||
      target.kind === 'audiobook');

  const canQueue =
    isOwned &&
    (!!target.onQueue ||
      target.kind === 'album' ||
      target.kind === 'movie' ||
      target.kind === 'audiobook');

  const itemStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    width: '100%',
    padding: '9px 12px',
    border: 'none',
    borderRadius: 9,
    background: 'transparent',
    color: '#f1f5f9',
    fontSize: 13,
    fontWeight: 500,
    textAlign: 'left',
    cursor: 'pointer',
  };

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      aria-label={`Actions for ${target.title}`}
      style={{
        position: 'fixed',
        left,
        top,
        width: menuW,
        zIndex: 100000,
        borderRadius: 15,
        background: 'rgba(18, 20, 31, 0.96)',
        backdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.13)',
        boxShadow: '0 24px 64px rgba(0,0,0,0.72), 0 0 0 1px rgba(255,255,255,0.04) inset',
        padding: 6,
        color: '#fff',
      }}
      onContextMenu={(e) => e.preventDefault()}
    >
      {/* Header Preview */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '8px 10px 10px',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
          marginBottom: 4,
        }}
      >
        {target.artwork ? (
          <img
            src={target.artwork}
            alt=""
            style={{
              width: 38,
              height: 38,
              borderRadius: 8,
              objectFit: 'cover',
              flexShrink: 0,
              background: 'rgba(255,255,255,0.06)',
            }}
          />
        ) : null}
        <div style={{ minWidth: 0, flex: 1 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 700,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {target.title}
          </div>
          <div
            style={{
              fontSize: 11,
              color: 'rgba(255,255,255,0.58)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {KIND_LABELS[target.kind]}
            {target.subtitle ? ` · ${target.subtitle}` : ''}
          </div>
        </div>
      </div>

      {busyMsg ? (
        <div style={{ padding: '10px 12px', fontSize: 12.5, color: '#a5b4fc', fontWeight: 600 }}>
          {busyMsg}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {canPlayDirect && (
            <button
              type="button"
              role="menuitem"
              style={itemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => void runPlay(false)}
            >
              <span>▶</span>
              <span>Play now</span>
            </button>
          )}

          {isOwned && target.kind === 'album' && (
            <button
              type="button"
              role="menuitem"
              style={itemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => void runPlay(true)}
            >
              <span>🔀</span>
              <span>Shuffle album</span>
            </button>
          )}

          {canQueue && (
            <button
              type="button"
              role="menuitem"
              style={itemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => void runQueue()}
            >
              <span>☰</span>
              <span>Add to queue</span>
            </button>
          )}

          {isOwned && target.onToggleLike && (
            <button
              type="button"
              role="menuitem"
              style={itemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => {
                target.onToggleLike?.();
                closeContextMenu();
              }}
            >
              <span>{target.liked ? '♥' : '♡'}</span>
              <span>{target.liked ? 'Remove from Liked Songs' : 'Add to Liked Songs'}</span>
            </button>
          )}

          {isOwned && (target.song || target.kind === 'album') && (
            <button
              type="button"
              role="menuitem"
              style={itemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => {
                if (target.song) {
                  const song = target.song;
                  closeContextMenu();
                  openAddToPlaylist([song]);
                } else {
                  void runAddAlbumToPlaylist();
                }
              }}
            >
              <span>＋</span>
              <span>Add to playlist</span>
            </button>
          )}

          {!isOwned && target.onRequest && (
            <button
              type="button"
              role="menuitem"
              style={{ ...itemStyle, color: '#a5b4fc', fontWeight: 600 }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(129,140,248,0.16)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => {
                const fn = target.onRequest;
                closeContextMenu();
                fn?.();
              }}
            >
              <span>＋</span>
              <span>Request {KIND_LABELS[target.kind].toLowerCase()}</span>
            </button>
          )}

          {(target.kind === 'movie' || target.kind === 'show') && target.id && isOwned && (
            <>
              <button
                type="button"
                role="menuitem"
                style={itemStyle}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                onClick={() => void runWatchlist(true)}
              >
                <span>🔖</span>
                <span>Add to Watchlist</span>
              </button>
              <button
                type="button"
                role="menuitem"
                style={itemStyle}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                onClick={() => void runWatchlist(false)}
              >
                <span>✕</span>
                <span>Remove from Watchlist</span>
              </button>
            </>
          )}

          {isOwned && target.downloadType && target.downloadId && (
            <button
              type="button"
              role="menuitem"
              style={itemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => void runDownload()}
            >
              <span>⬇</span>
              <span>Download for offline</span>
            </button>
          )}

          {target.href && (
            <button
              type="button"
              role="menuitem"
              style={itemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => {
                const to = target.href!;
                closeContextMenu();
                void navigate({ to: to as '/player' });
              }}
            >
              <span>↗</span>
              <span>Open details</span>
            </button>
          )}

          {target.extraActions?.map((act) => (
            <button
              key={act.label}
              type="button"
              role="menuitem"
              style={{
                ...itemStyle,
                color: act.danger ? '#fda4af' : '#f1f5f9',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => {
                closeContextMenu();
                act.onSelect();
              }}
            >
              <span>•</span>
              <span>{act.label}</span>
            </button>
          ))}
        </div>
      )}

      {statusNote ? (
        <div
          style={{
            marginTop: 6,
            padding: '6px 10px',
            borderRadius: 8,
            background: 'rgba(244,63,94,0.14)',
            color: '#fda4af',
            fontSize: 11.5,
          }}
        >
          {statusNote}
        </div>
      ) : null}
    </div>,
    document.body,
  );
}
