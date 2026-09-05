/**
 * The Server Manager tab's playlist list (loadServerPlaylists, pages-extra.js
 * 12-156) and its disambiguation modal (_showServerDisambig, 185-245).
 *
 * Slice A of the server tab. The compare EDITOR is slice B and does not exist
 * yet, so opening a card raises the choice it resolves and hands the caller the
 * ids — the tab's own state machine, not a mount-site prop, will own the swap.
 *
 * Every class here is transcribed from the vanilla and already exists in
 * style.css; the skeleton, the two section headers and the card body are
 * structural, so they are asserted as literals in the test.
 */

import type { CSSProperties, ReactElement } from 'react';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import type { MirroredMatch, ServerPlaylist } from '../-sync.server';

import { recordServerLink } from '../-sync.api';
import { timeAgo } from '../-sync.mirrored';
import {
  fetchMirroredMatches,
  fetchMirroredPlaylistById,
  fetchServerPlaylistData,
  serverCardHue,
  serverTabTitle,
  splitServerPlaylists,
} from '../-sync.server';

/** The three server icons, verbatim from 86-90. */
const SERVER_ICONS: Readonly<Record<string, ReactElement>> = {
  plex: (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M11.643 0H4.68l7.679 12L4.68 24h6.963L19.32 12z" />
    </svg>
  ),
  jellyfin: (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M12 2C8.5 2 6 5.1 6 9c0 2.4 1.2 5.5 3.3 8.7.7 1 1.5 2 2.2 2.9.2.3.4.3.5.4.1 0 .3-.1.5-.4.7-.9 1.5-1.9 2.2-2.9C16.8 14.5 18 11.4 18 9c0-3.9-2.5-7-6-7z" />
    </svg>
  ),
  navidrome: (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
    </svg>
  ),
};

/** 91: an unrecognised server type falls back to the Plex mark. */
function serverIcon(serverType: string | undefined): ReactElement {
  return SERVER_ICONS[serverType ?? ''] ?? SERVER_ICONS.plex;
}

function SkeletonGrid() {
  return (
    <div className="server-pl-grid">
      {Array.from({ length: 6 }, (_, i) => (
        <div
          key={i}
          className="server-pl-card server-pl-skeleton"
          style={{ animationDelay: `${i * 0.06}s` }}
        >
          <div className="server-pl-card-top">
            <div className="skeleton-box" style={{ width: 44, height: 44, borderRadius: 12 }} />
            <div className="skeleton-box" style={{ width: 28, height: 28, borderRadius: 8 }} />
          </div>
          <div className="server-pl-card-body">
            {/* The vanilla randomises this width per card (30); a fixed width
                keeps the render deterministic and the shimmer identical. */}
            <div
              className="skeleton-box"
              style={{ width: '75%', height: 14, borderRadius: 4, marginBottom: 8 }}
            />
            <div className="skeleton-box" style={{ width: '40%', height: 11, borderRadius: 4 }} />
          </div>
          <div
            className="server-pl-card-footer"
            style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 12 }}
          >
            <div className="skeleton-box" style={{ width: 60, height: 10, borderRadius: 3 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function PlaylistCard({
  playlist,
  index,
  isSynced,
  icon,
  onOpen,
}: {
  playlist: ServerPlaylist;
  index: number;
  isSynced: boolean;
  icon: ReactElement;
  onOpen: () => void;
}) {
  return (
    <div
      className={isSynced ? 'server-pl-card' : 'server-pl-card server-pl-unsynced'}
      style={
        {
          animationDelay: `${index * 0.04}s`,
          '--card-hue': serverCardHue(index),
        } as CSSProperties
      }
      onClick={onOpen}
    >
      <div className="server-pl-card-glow" />
      <div className="server-pl-card-top">
        <div className="server-pl-card-icon-wrap">
          <div className="server-pl-card-bars">
            <span />
            <span />
            <span />
            <span />
          </div>
        </div>
        <div className="server-pl-card-badge">{icon}</div>
      </div>
      <div className="server-pl-card-body">
        <div className="server-pl-card-name">{playlist.name}</div>
        <div className="server-pl-card-meta">
          <span className="server-pl-track-count">{playlist.track_count}</span> tracks
          {isSynced ? <span className="server-pl-synced-badge">Synced</span> : null}
        </div>
      </div>
      <div className="server-pl-card-footer">
        <span className="server-pl-card-action">{isSynced ? 'Open Editor' : 'View Tracks'}</span>
        <span className="server-pl-card-arrow">
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </span>
      </div>
    </div>
  );
}

function Section({
  className,
  icon,
  title,
  playlists,
  startIndex,
  isSynced,
  serverType,
  onOpen,
}: {
  className: string;
  icon: string;
  title: string;
  playlists: ServerPlaylist[];
  startIndex: number;
  isSynced: boolean;
  serverType: string | undefined;
  onOpen: (playlist: ServerPlaylist) => void;
}) {
  return (
    <div className={className}>
      <div className="server-pl-section-header">
        <span className="server-pl-section-icon">{icon}</span>
        <span className="server-pl-section-title">{title}</span>
        <span className="server-pl-section-count">{playlists.length}</span>
      </div>
      <div className="server-pl-grid">
        {playlists.map((pl, i) => (
          <PlaylistCard
            key={pl.id}
            playlist={pl}
            // 145: the Other grid continues the synced grid's numbering, so the
            // hue and the stagger never restart.
            index={startIndex + i}
            isSynced={isSynced}
            icon={serverIcon(serverType)}
            onOpen={() => onOpen(pl)}
          />
        ))}
      </div>
    </div>
  );
}

export interface ServerPlaylistListProps {
  /**
   * Resolved by the name lookup: exactly one mirrored match, none (server-only)
   * or several (after the user has picked one). Slice B renders the compare
   * view from this.
   */
  onOpenCompare: (playlist: ServerPlaylist, mirrored: MirroredMatch | null) => void;
}

export function ServerPlaylistList({ onOpenCompare }: ServerPlaylistListProps) {
  const [state, setState] = useState<{
    synced: ServerPlaylist[];
    unsynced: ServerPlaylist[];
    serverType?: string;
  } | null>(null);
  const [placeholder, setPlaceholder] = useState<string | null>(null);
  /** Held in a ref so `openCompare` stays stable across loads. */
  const serverTypeRef = useRef<string | undefined>(undefined);
  const [refreshing, setRefreshing] = useState(false);
  /** The several-matches case (183): pick one before the compare view opens. */
  /**
   * Every path that resolves "this server playlist IS that mirror" ends at
   * onOpenCompare, so recording the link here covers the single-match case,
   * the no-match case and the disambiguation the user just answered — without
   * going anywhere near the sync path.
   *
   * Write only: nothing reads it yet.
   */
  const openCompare = useCallback(
    (playlist: ServerPlaylist, mirrored: MirroredMatch | null): void => {
      if (mirrored && serverTypeRef.current) {
        recordServerLink(mirrored.id, playlist.id, serverTypeRef.current);
      }
      onOpenCompare(playlist, mirrored);
    },
    [onOpenCompare],
  );

  const [disambig, setDisambig] = useState<{
    playlist: ServerPlaylist;
    candidates: MirroredMatch[];
  } | null>(null);

  const load = useCallback(async () => {
    setState(null);
    setPlaceholder(null);
    setRefreshing(true);
    try {
      const { data, mirroredNames, historyNames } = await fetchServerPlaylistData();
      if (!data.success || !data.playlists) {
        setPlaceholder(data.error || 'Could not load server playlists');
        return;
      }
      const { synced, unsynced } = splitServerPlaylists(
        data.playlists,
        mirroredNames,
        historyNames,
      );
      if (synced.length === 0 && unsynced.length === 0) {
        setPlaceholder('No playlists found on your media server.');
        return;
      }
      serverTypeRef.current = data.server_type;
      setState({ synced, unsynced, serverType: data.server_type });
    } catch (error) {
      setPlaceholder(`Error: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setRefreshing(false);
    }
  }, []);

  useLayoutEffect(() => {
    void load();
  }, [load]);

  /** selectDisambigPlaylist (234-243) — re-fetch, then open. */
  const pickMirrored = useCallback(
    async (playlist: ServerPlaylist, mirroredId: number) => {
      try {
        openCompare(playlist, await fetchMirroredPlaylistById(mirroredId));
      } catch (error) {
        window.showToast?.(
          `Failed to load mirrored playlist: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [openCompare],
  );

  /** openServerPlaylistEditor's three-way branch (172-183). */
  const openPlaylist = useCallback(
    async (playlist: ServerPlaylist) => {
      const matches = await fetchMirroredMatches(playlist.name);
      if (matches.length === 1) openCompare(playlist, matches[0]);
      else if (matches.length === 0) openCompare(playlist, null);
      else setDisambig({ playlist, candidates: matches });
    },
    [openCompare],
  );

  return (
    <div>
      <div className="playlist-header">
        <h3 id="server-tab-title">{serverTabTitle(state?.serverType)}</h3>
        <button
          type="button"
          className="refresh-button"
          id="server-refresh-btn"
          disabled={refreshing}
          onClick={() => void load()}
        >
          {refreshing ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div id="server-playlist-container">
        {refreshing && !state ? (
          <SkeletonGrid />
        ) : placeholder ? (
          <div className="playlist-placeholder">{placeholder}</div>
        ) : state ? (
          <>
            {state.synced.length > 0 && (
              <Section
                className="server-pl-section"
                icon="🔗"
                title="Synced Playlists"
                playlists={state.synced}
                startIndex={0}
                isSynced
                serverType={state.serverType}
                onOpen={(pl) => void openPlaylist(pl)}
              />
            )}
            {state.unsynced.length > 0 && (
              <Section
                className="server-pl-section server-pl-section-unsynced"
                icon="🎵"
                title="Other Server Playlists"
                playlists={state.unsynced}
                startIndex={state.synced.length}
                isSynced={false}
                serverType={state.serverType}
                onOpen={(pl) => void openPlaylist(pl)}
              />
            )}
          </>
        ) : null}
      </div>
      {disambig && (
        <ServerDisambigModal
          playlistName={disambig.playlist.name}
          candidates={disambig.candidates}
          now={Date.now()}
          onClose={() => setDisambig(null)}
          onPick={(candidate) => {
            const playlist = disambig.playlist;
            setDisambig(null);
            // 234-243: the picked row is only a LIST entry — the compare view
            // gets the full mirrored playlist, re-fetched by id.
            void pickMirrored(playlist, candidate.id);
          }}
        />
      )}
    </div>
  );
}

/* ── Disambiguation (_showServerDisambig 185-223) ─────────────────────────── */

/**
 * 193 — this tab's OWN icon table, and it is not the mirrored tab's. Spotify is
 * a green circle here and a musical note there; deezer is purple here; `file`
 * exists here and qobuz does not. Do not share a table between them.
 */
const DISAMBIG_SOURCE_ICONS: Readonly<Record<string, string>> = {
  spotify: '🟢',
  tidal: '🌊',
  youtube: '▶️',
  beatport: '🎛️',
  deezer: '🟣',
  file: '📄',
};

/** How many DISTINCT sources the candidates span (#1219). */
export function distinctSources(candidates: { source?: string }[]): number {
  return new Set((candidates ?? []).map((c) => (c.source ?? '').toLowerCase())).size;
}

/** Whether any two candidates show the same name AND source — i.e. the list
    gives the user nothing to choose on. */
export function hasIndistinguishable(
  candidates: { name?: string; display_name?: string; source?: string }[],
): boolean {
  const seen = new Set<string>();
  for (const c of candidates ?? []) {
    const key = `${((c.display_name || c.name) ?? '').trim().toLowerCase()}::${(c.source ?? '').toLowerCase()}`;
    if (seen.has(key)) return true;
    seen.add(key);
  }
  return false;
}

/** The tail of a source reference — enough to tell two mirrors apart without
    printing a full URL into a card. */
export function shortRef(candidate: { source_ref?: string; id?: number }): string {
  const ref = String(candidate.source_ref ?? '').trim();
  if (!ref) return `#${candidate.id ?? '?'}`;
  const tail = ref.split(/[/:]/).filter(Boolean).pop() ?? ref;
  return tail.length > 14 ? `…${tail.slice(-12)}` : tail;
}

export function ServerDisambigModal({
  playlistName,
  candidates,
  now,
  onClose,
  onPick,
}: {
  playlistName: string;
  candidates: MirroredMatch[];
  /** One clock read, as the vanilla calls timeAgo once per card build. */
  now: number;
  onClose: () => void;
  onPick: (candidate: MirroredMatch) => void;
}) {
  // 221-222 parks the handler on window._disambigEsc and removes it in
  // closeServerDisambig; an effect is the same teardown without the global.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Two candidates the user cannot tell apart: same shown name, same source.
  const ambiguous = hasIndistinguishable(candidates);

  return (
    <div
      id="server-disambig-overlay"
      className="server-disambig-overlay visible"
      // 220 — backdrop only.
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="server-disambig-modal" id="server-disambig-modal">
        {/* index.html 3243-3249. The port opened straight onto the subtitle, so
            this modal had NO TITLE and NO CLOSE BUTTON, and the subtitle
            carried its id but not its class — meaning default size and colour
            instead of the 12px muted line. Only the backdrop and Esc dismissed
            it. The header is `display: flex; justify-content: space-between`
            (61012), which is what puts the × opposite the title. */}
        <div className="server-disambig-header">
          <div>
            <h3 className="server-disambig-title">Multiple Sources Found</h3>
            {/* #1219: this said "found on N sources" no matter what, so four
                mirrors of four same-named TIDAL playlists read as "4 sources"
                — and the reporter quite reasonably said "they are all the same
                source". Count the sources actually present. */}
            <p className="server-disambig-subtitle" id="server-disambig-subtitle">
              &ldquo;{playlistName}&rdquo;{' '}
              {distinctSources(candidates) > 1
                ? `was found on ${distinctSources(candidates)} sources.`
                : `matches ${candidates.length} mirrored playlists.`}{' '}
              Which one do you want to compare against?
            </p>
          </div>
          {/* 3248 — `onclick="closeServerDisambig()"`, the same close the
              backdrop and Esc already call here. */}
          <button type="button" className="server-disambig-close" onClick={onClose}>
            ×
          </button>
        </div>
        {ambiguous ? (
          <p className="server-disambig-hint">
            These share a name. You can rename a mirror from the Mirrored tab (card menu → Rename)
            to tell them apart here.
          </p>
        ) : null}
        <div className="server-disambig-list" id="server-disambig-list">
          {candidates.map((candidate, i) => (
            <div
              key={candidate.id}
              className="server-disambig-card"
              style={{ animationDelay: `${i * 0.06}s` }}
              onClick={() => onPick(candidate)}
            >
              <div className="server-disambig-icon">
                {DISAMBIG_SOURCE_ICONS[candidate.source ?? ''] ?? '📋'}
              </div>
              <div className="server-disambig-info">
                {/* display_name is the user's alias when they have renamed
                    this mirror. Rename exists (Mirrored tab → card menu) but
                    this modal showed the raw upstream name, so renaming to tell
                    two same-named playlists apart changed nothing HERE — the
                    one screen where you need them told apart. */}
                <div className="server-disambig-name">
                  {candidate.display_name || candidate.name}
                </div>
                <div className="server-disambig-details">
                  <span className="source-badge">{candidate.source}</span>
                  <span>{candidate.track_count || 0} tracks</span>
                  {candidate.owner ? <span>by {candidate.owner}</span> : null}
                  {/* When two candidates share a name AND a source, everything
                      above is identical and there is nothing to choose on. The
                      source reference is the one thing that differs. */}
                  {ambiguous ? (
                    <span className="server-disambig-ref" title={candidate.source_ref}>
                      {shortRef(candidate)}
                    </span>
                  ) : null}
                  {/* 197: mirrored_at FIRST here. The mirrored tab's card reads
                      the same two fields in the OPPOSITE order (527). */}
                  <span>
                    Mirrored {timeAgo(candidate.mirrored_at || candidate.updated_at, now)}
                  </span>
                </div>
              </div>
              <div className="server-disambig-arrow">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M5 12h14M12 5l7 7-7 7" />
                </svg>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
