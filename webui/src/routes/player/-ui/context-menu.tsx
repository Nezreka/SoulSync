/**
 * Universal More menu for every media card, row and page in /player (movies,
 * shows, seasons, episodes, albums, tracks, audiobooks, podcasts, YouTube,
 * home videos). Right-click, a long press or a ⋯ <MoreButton> opens it.
 * Mounted once in PlayerShell via <ContextMenuHost />, which also holds the
 * download and report sheets its items open.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';

import type { DownloadItemType } from '../-lib/downloads';

import { GROUP_OF, offlineSettingsQuery, type GroupType } from '../-lib/offline';
import { albumItems, bookItem, episodeItems, movieItem } from '../-lib/play-items';
import {
  addToWatchlist,
  albumQuery,
  booksQuery,
  movieDetailQuery,
  removeFromWatchlist,
  showDetailQuery,
  type InfoItemType,
  type SongRef,
} from '../-player.api';
import m from './context-menu.module.css';
import { DownloadSheet } from './download-sheet';
import {
  DownloadIcon,
  HeartIcon,
  InfoIcon,
  MoreIcon,
  PlayIcon,
  PlusIcon,
  ShuffleIcon,
  WatchlistIcon,
  XIcon,
} from './icons';
import { openItemInfo } from './item-info';
import { openAddToPlaylist } from './music/add-to-playlist';
import { getEngine, playItems } from './playback/use-player';
import { FlagIcon, ReportIssueDialog, type ReportTarget } from './report-issue';
import { inPlayer } from './trailer';

export interface ContextMenuExtraAction {
  label: string;
  onSelect: () => void;
  danger?: boolean;
}

export interface ContextMenuTarget {
  x: number;
  y: number;
  /** the ⋯ button it opened from: the menu hangs off it and focus goes back to it */
  anchor?: HTMLElement | null;
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
  /** play it after the one playing (only offered while something plays) */
  onPlayNext?: () => void;
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
  /** what the download sheet offers, when it isn't the download type and id above */
  download?: { type: GroupType; id: string; title?: string } | null;
  /** what Report an issue files against; null: not offered */
  report?: ReportTarget | null;
  /** Whether the user actually owns/has this item locally (default true) */
  owned?: boolean;
  /** Optional request action for missing/unowned items */
  onRequest?: () => void;
  /** Extra card-specific actions (e.g. Remove from Continue Watching) */
  extraActions?: ContextMenuExtraAction[];
  /** what the Info sheet asks about, when it isn't the kind and id above */
  info?: { type: InfoItemType; id: string } | null;
  /** a page's own ⋯: its hero already plays, queues and keeps a watchlist */
  page?: boolean;
}

/** the Info sheet's item for a menu target: its own, else its kind and id */
export function infoFor(target: ContextMenuTarget): { type: InfoItemType; id: string } | null {
  if (target.info !== undefined) return target.info;
  if (target.owned === false || !target.id) return null;
  const byKind: Partial<Record<ContextMenuTarget['kind'], InfoItemType>> = {
    movie: 'movie',
    episode: 'episode',
    track: 'track',
    audiobook: 'audiobook',
    youtube: 'youtube',
  };
  const type = byKind[target.kind];
  return type ? { type, id: target.id } : null;
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
  e: Pick<React.MouseEvent, 'clientX' | 'clientY'>,
  card: {
    key?: string;
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
  let info: ContextMenuTarget['info'];

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
    // an episode card can say what it is; a podcast's own card has no info
    info =
      card.source?.itemType === 'podcast_episode' && id ? { type: 'podcast_episode', id } : null;
    id = id ?? href.match(/\/player\/podcasts\/([^/?#]+)/)?.[1] ?? null;
    // no download: a card carries no enclosure url, and the player has no
    // podcast stream to fetch (it logged a download and saved an error page)
  } else if (card.source?.itemType === 'youtube') {
    kind = 'youtube';
    if (id) {
      downloadType = 'youtube';
      downloadId = id;
    }
  } else if (card.source?.itemType === 'home' && id) {
    info = { type: 'home', id };
  } else if (card.key?.startsWith('yt:')) {
    // a youtube video card links to its channel: its key names the video
    info = { type: 'youtube', id: card.key.slice(3) };
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
    info,
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

/** what the download sheet offers for a menu target, if anything */
export function downloadFor(
  target: ContextMenuTarget,
): { type: GroupType; id: string; title: string } | null {
  if (target.owned === false) return null;
  if (target.download !== undefined) {
    return target.download ? { title: target.title, ...target.download } : null;
  }
  if (target.downloadType && target.downloadId) {
    return { type: GROUP_OF[target.downloadType], id: target.downloadId, title: target.title };
  }
  return null;
}

const MENU_W = 268;
const GAP = 4;
const EDGE = 12;

interface MenuItem {
  key: string;
  label: string;
  icon: ReactNode;
  tone?: 'accent' | 'danger';
  /** run after the menu closes; false keeps it open (it shows its own busy line) */
  run: () => void | Promise<void>;
  keepOpen?: boolean;
}

type Sheet =
  | { kind: 'download'; type: GroupType; id: string; title: string }
  | ({ kind: 'report' } & ReportTarget);

/** the menu's items, focusable, in order */
function menuItems(menu: HTMLElement | null): HTMLElement[] {
  if (!menu) return [];
  return [...menu.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)')];
}

export function ContextMenuHost() {
  const [target, setTarget] = useState<ContextMenuTarget | null>(currentTarget);
  const [busyMsg, setBusyMsg] = useState<string | null>(null);
  const [statusNote, setStatusNote] = useState<string | null>(null);
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const offline = useQuery({ ...offlineSettingsQuery(), retry: false });
  const downloadsOff = !!offline.data && !offline.data.allow_downloads && !offline.data.is_admin;

  useEffect(() => {
    const fn: Listener = (next) => {
      if (next) {
        const active = document.activeElement;
        returnTo.current = next.anchor ?? (active instanceof HTMLElement ? active : null);
      }
      setTarget(next);
      setBusyMsg(null);
      setStatusNote(null);
    };
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, []);

  /** close and put focus back where it was */
  const closeAndReturn = () => {
    const back = returnTo.current;
    closeContextMenu();
    if (back?.isConnected) back.focus();
  };

  useEffect(() => {
    if (!target) return;
    const inside = (node: EventTarget | null, el: HTMLElement | null | undefined) =>
      node instanceof Node && !!el?.contains(node);
    const onDown = (e: MouseEvent) => {
      if (inside(e.target, menuRef.current)) return;
      // the ⋯ button toggles the menu itself
      if (inside(e.target, target.anchor)) return;
      closeContextMenu();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeAndReturn();
      }
    };
    const onScroll = (e: Event) => {
      if (inside(e.target, menuRef.current)) return;
      closeContextMenu();
    };
    const onResize = () => closeContextMenu();
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onResize);
    };
  }, [target]);

  // place it (under the ⋯ button, or at the pointer), flipping up when there's
  // no room below, then put focus on the first item
  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!target || !menu) return;
    const w = menu.offsetWidth || MENU_W;
    const h = menu.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left: number;
    let top: number;
    const rect = target.anchor?.isConnected ? target.anchor.getBoundingClientRect() : null;
    if (rect) {
      left = rect.right - w;
      top = rect.bottom + GAP;
      if (top + h > vh - EDGE && rect.top - h - GAP >= EDGE) top = rect.top - h - GAP;
    } else {
      left = target.x;
      top = target.y;
      if (top + h > vh - EDGE && target.y - h >= EDGE) top = target.y - h;
    }
    menu.style.left = `${Math.max(EDGE, Math.min(left, vw - w - EDGE))}px`;
    menu.style.top = `${Math.max(EDGE, Math.min(top, vh - h - EDGE))}px`;
  }, [target, busyMsg, statusNote]);

  useEffect(() => {
    if (target) menuItems(menuRef.current)[0]?.focus();
  }, [target]);

  const sheetNode = sheet ? (
    sheet.kind === 'download' ? (
      <DownloadSheet
        type={sheet.type}
        id={sheet.id}
        title={sheet.title}
        onClose={() => setSheet(null)}
      />
    ) : (
      <ReportIssueDialog
        entityType={sheet.entityType}
        entityId={sheet.entityId}
        title={sheet.title}
        subtitle={sheet.subtitle}
        positionSec={sheet.positionSec}
        onClose={() => setSheet(null)}
      />
    )
  ) : null;

  if (!target) return sheetNode;

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
        return;
      }
      // nothing to play (a show with no episodes yet) and nowhere to go
      setBusyMsg(null);
      setStatusNote('Nothing to play here yet');
    } catch {
      setBusyMsg(null);
      setStatusNote('Could not start playback');
    }
  };

  const runQueue = async () => {
    if (target.onQueue) {
      closeAndReturn();
      target.onQueue();
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
  const info = infoFor(target);
  const download = downloadsOff ? null : downloadFor(target);
  const report = target.report ?? null;

  const canPlayDirect =
    !target.page &&
    isOwned &&
    (!!target.onPlay ||
      target.kind === 'movie' ||
      target.kind === 'show' ||
      target.kind === 'album' ||
      target.kind === 'audiobook');

  const canQueue =
    !target.page &&
    isOwned &&
    (!!target.onQueue ||
      target.kind === 'album' ||
      target.kind === 'movie' ||
      target.kind === 'audiobook');

  const items: MenuItem[] = [];
  if (canPlayDirect) {
    items.push({
      key: 'play',
      label: 'Play now',
      icon: <PlayIcon />,
      run: () => runPlay(false),
      keepOpen: true,
    });
  }
  if (!target.page && isOwned && target.kind === 'album') {
    items.push({
      key: 'shuffle',
      label: 'Shuffle album',
      icon: <ShuffleIcon />,
      run: () => runPlay(true),
      keepOpen: true,
    });
  }
  if (isOwned && target.onPlayNext) {
    const fn = target.onPlayNext;
    items.push({ key: 'next', label: 'Play next', icon: <QueueIcon />, run: fn });
  }
  if (canQueue) {
    items.push({
      key: 'queue',
      label: 'Add to queue',
      icon: <QueueIcon />,
      run: () => runQueue(),
      keepOpen: true,
    });
  }
  if (isOwned && target.onToggleLike) {
    const fn = target.onToggleLike;
    items.push({
      key: 'like',
      label: target.liked ? 'Remove from Liked Songs' : 'Add to Liked Songs',
      icon: <HeartIcon filled={target.liked} />,
      run: fn,
    });
  }
  if (isOwned && (target.song || target.kind === 'album')) {
    const song = target.song;
    items.push({
      key: 'playlist',
      label: 'Add to playlist',
      icon: <PlusIcon />,
      run: song ? () => openAddToPlaylist([song]) : () => runAddAlbumToPlaylist(),
      keepOpen: !song,
    });
  }
  if (!isOwned && target.onRequest) {
    const fn = target.onRequest;
    items.push({
      key: 'request',
      label: `Request ${KIND_LABELS[target.kind].toLowerCase()}`,
      icon: <PlusIcon />,
      tone: 'accent',
      run: fn,
    });
  }
  if (!target.page && (target.kind === 'movie' || target.kind === 'show') && target.id && isOwned) {
    items.push(
      {
        key: 'watchlist-add',
        label: 'Add to Watchlist',
        icon: <WatchlistIcon />,
        run: () => runWatchlist(true),
        keepOpen: true,
      },
      {
        key: 'watchlist-remove',
        label: 'Remove from Watchlist',
        icon: <XIcon />,
        run: () => runWatchlist(false),
        keepOpen: true,
      },
    );
  }
  if (download) {
    items.push({
      key: 'download',
      label: 'Download',
      icon: <DownloadIcon />,
      run: () => setSheet({ kind: 'download', ...download }),
    });
  }
  if (info) {
    items.push({
      key: 'info',
      label: 'Info',
      icon: <InfoIcon />,
      run: () => openItemInfo({ ...info, title: target.title }),
    });
  }
  if (report) {
    items.push({
      key: 'report',
      label: 'Report an issue',
      icon: <FlagIcon />,
      run: () => setSheet({ kind: 'report', ...report }),
    });
  }
  if (target.href && !target.page) {
    const to = target.href;
    items.push({
      key: 'open',
      label: 'Open details',
      icon: <OpenIcon />,
      run: () => void navigate({ to: to as '/player' }),
    });
  }
  for (const act of target.extraActions ?? []) {
    items.push({
      key: `extra:${act.label}`,
      label: act.label,
      icon: <DotIcon />,
      tone: act.danger ? 'danger' : undefined,
      run: act.onSelect,
    });
  }

  const choose = (item: MenuItem) => {
    if (item.keepOpen) {
      void item.run();
      return;
    }
    closeAndReturn();
    void item.run();
  };

  const onMenuKey = (e: React.KeyboardEvent) => {
    const list = menuItems(menuRef.current);
    if (!list.length) return;
    const at = list.indexOf(document.activeElement as HTMLElement);
    let next = -1;
    if (e.key === 'ArrowDown') next = at < 0 ? 0 : (at + 1) % list.length;
    else if (e.key === 'ArrowUp') next = at <= 0 ? list.length - 1 : at - 1;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = list.length - 1;
    else if (e.key === 'Tab') {
      e.preventDefault();
      closeAndReturn();
      return;
    }
    if (next >= 0) {
      e.preventDefault();
      e.stopPropagation();
      list[next].focus();
    }
  };

  const width = Math.min(MENU_W, window.innerWidth - EDGE * 2);

  return (
    <>
      {sheetNode}
      {inPlayer(
        <div
          ref={menuRef}
          role="menu"
          aria-label={`Actions for ${target.title}`}
          className={m.menu}
          style={{
            left: Math.max(EDGE, Math.min(target.x, window.innerWidth - width - EDGE)),
            top: Math.max(EDGE, target.y),
            width,
            zIndex: 100000,
          }}
          onContextMenu={(e) => e.preventDefault()}
          onKeyDown={onMenuKey}
          onClick={(e) => e.stopPropagation()}
        >
          <div className={m.head}>
            {target.artwork ? <img src={target.artwork} alt="" className={m.headArt} /> : null}
            <div className={m.headText}>
              <div className={m.headTitle}>{target.title}</div>
              <div className={m.headSub}>
                {KIND_LABELS[target.kind]}
                {target.subtitle ? ` · ${target.subtitle}` : ''}
              </div>
            </div>
          </div>

          {busyMsg ? (
            <div className={m.busy}>{busyMsg}</div>
          ) : (
            <div className={m.items}>
              {items.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  role="menuitem"
                  tabIndex={-1}
                  className={m.item}
                  data-tone={item.tone}
                  onClick={() => choose(item)}
                >
                  <span className={m.glyph}>{item.icon}</span>
                  <span>{item.label}</span>
                </button>
              ))}
            </div>
          )}

          {statusNote ? <div className={m.note}>{statusNote}</div> : null}
        </div>,
      )}
    </>
  );
}

function QueueIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h13" />
      <path d="M3 12h9" />
      <path d="M3 18h9" />
      <path d="M18 14v6" />
      <path d="M15 17h6" />
    </svg>
  );
}

function OpenIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M7 17 17 7" />
      <path d="M8 7h9v9" />
    </svg>
  );
}

function DotIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="2.5" fill="currentColor" />
    </svg>
  );
}

/** is the menu open on this ⋯ button */
function useMenuOn(ref: React.RefObject<HTMLElement | null>): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const fn: Listener = (next) => setOn(!!next && !!ref.current && next.anchor === ref.current);
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, [ref]);
  return on;
}

/**
 * the ⋯ button on a row or at the end of a page's buttons: opens the More
 * menu under it (a second press closes it). `target` is built on press, so
 * rows pay nothing until it's used.
 */
export function MoreButton({
  target,
  className,
  heroClassName,
  label,
}: {
  target: () => Omit<ContextMenuTarget, 'x' | 'y' | 'anchor'>;
  /** a row's own round button class, in place of the plain one */
  className?: string;
  /** at the end of a page's hero buttons: the page's button class */
  heroClassName?: string;
  /** what the button is for, read out: "More for <title>" */
  label: string;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const on = useMenuOn(ref);
  return (
    <button
      ref={ref}
      type="button"
      className={heroClassName ? `${heroClassName} ${m.moreHero}` : (className ?? m.more)}
      aria-haspopup="menu"
      aria-expanded={on}
      aria-label={`More for ${label}`}
      title="More"
      onClick={(e) => {
        e.stopPropagation();
        if (on) {
          closeContextMenu();
          return;
        }
        const r = e.currentTarget.getBoundingClientRect();
        openContextMenu({ ...target(), x: r.right, y: r.bottom, anchor: e.currentTarget });
      }}
      onKeyDown={(e) => {
        // a row that plays on enter mustn't also play
        if (e.key === 'Enter' || e.key === ' ') e.stopPropagation();
      }}
      onContextMenu={(e) => e.stopPropagation()}
      data-nav
    >
      <MoreIcon />
    </button>
  );
}
