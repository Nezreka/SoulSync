/**
 * The Playlist Explorer page — the whole vanilla region's controller, rebuilt
 * as one component (pages-extra.js:1-1134, index.html:4141-4241).
 *
 * The pieces it wires together are all tested on their own: the pure core, the
 * four endpoints, the presentational tree, the interaction controllers and the
 * wishlist modal. What lives HERE is only the state those pieces share.
 */

import { useQuery } from '@tanstack/react-query';
import { useCallback, useMemo, useRef, useState } from 'react';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import type { ExplorerArtist, ExplorerMeta, ExplorerMode, ExplorerTrack } from '../-explorer.types';

import { fetchAlbumTracks, fetchMirroredPlaylists, streamBuildTree } from '../-explorer.api';
import {
  CONNECTION_BUILD_DELAY_MS,
  CONNECTION_EXPAND_DELAY_MS,
  useExplorerConnections,
} from '../-explorer.connections';
import {
  artistHasSelection,
  explorerArtistKey,
  explorerBuildProgress,
  explorerSelectableAlbumIds,
  groupSelectionByArtist,
  isRealAlbumId,
} from '../-explorer.core';
import { useExplorerDiscovery } from '../-explorer.discovery';
import {
  createAlbumClickController,
  useExplorerPan,
  useExplorerZoom,
} from '../-explorer.interactions';
import { ExplorerActionBar, ExplorerProgress, ExplorerZoomControls } from './explorer-chrome';
import { ExplorerPicker } from './explorer-picker';
import { ExplorerTree } from './explorer-tree';
import { ExplorerWishlistModal } from './explorer-wishlist-modal';

export const EXPLORER_PLAYLISTS_QUERY_KEY = ['playlist-explorer', 'mirrored-playlists'];

export function ExplorerPage() {
  useReactPageShell('playlist-explorer');

  const viewportRef = useRef<HTMLDivElement>(null);
  const treeRef = useRef<HTMLDivElement>(null);

  const playlistsQuery = useQuery({
    queryKey: EXPLORER_PLAYLISTS_QUERY_KEY,
    queryFn: fetchMirroredPlaylists,
  });
  const playlists = useMemo(() => playlistsQuery.data ?? [], [playlistsQuery.data]);

  const [activeSource, setActiveSource] = useState<string | null>(null);
  const [selectedPlaylistId, setSelectedPlaylistId] = useState<number | null>(null);
  const [mode, setMode] = useState<ExplorerMode>('albums');

  const [building, setBuilding] = useState(false);
  const [hasBuilt, setHasBuilt] = useState(false);
  const [progress, setProgress] = useState<{ pct: number; text: string } | null>(null);

  const [meta, setMeta] = useState<ExplorerMeta | null>(null);
  const [artists, setArtists] = useState<ExplorerArtist[]>([]);
  const [expandedArtists, setExpandedArtists] = useState<ReadonlySet<string>>(new Set());
  const [selectedAlbums, setSelectedAlbums] = useState<ReadonlySet<string>>(new Set());
  const [addedAlbums, setAddedAlbums] = useState<ReadonlySet<string>>(new Set());
  const [expandedTracks, setExpandedTracks] = useState<Record<string, ExplorerTrack[] | undefined>>(
    {},
  );
  const [wishlistOpen, setWishlistOpen] = useState(false);

  const { zoom, zoomBy, resetZoom, fitToView } = useExplorerZoom(viewportRef, treeRef);
  useExplorerPan(viewportRef);
  const { geometry, scheduleRedraw } = useExplorerConnections(treeRef, zoom, !!meta);

  const refreshPlaylists = useCallback(() => void playlistsQuery.refetch(), [playlistsQuery]);
  const { liveDiscovery, discoverStates, startDiscovery } = useExplorerDiscovery(refreshPlaylists);

  // ── tree interactions ───────────────────────────────────────────────────

  const toggleArtist = useCallback(
    (key: string) => {
      setExpandedArtists((current) => {
        const next = new Set(current);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      });
      scheduleRedraw({ delayMs: CONNECTION_EXPAND_DELAY_MS });
    },
    [scheduleRedraw],
  );

  const expandTracks = useCallback(
    async (albumId: string) => {
      // A second double-click collapses, exactly as the vanilla's "does the
      // container already have content" test did.
      if (expandedTracks[albumId]) {
        setExpandedTracks((current) => ({ ...current, [albumId]: undefined }));
        scheduleRedraw({ delayMs: CONNECTION_EXPAND_DELAY_MS });
        return;
      }
      if (!isRealAlbumId(albumId)) return;
      const tracks = await fetchAlbumTracks(albumId);
      // null means the fetch failed; leaving it unset lets the next
      // double-click retry, which is what the vanilla did.
      if (!tracks) return;
      setExpandedTracks((current) => ({ ...current, [albumId]: tracks }));
      scheduleRedraw({ delayMs: CONNECTION_EXPAND_DELAY_MS });
    },
    [expandedTracks, scheduleRedraw],
  );

  const expandTracksRef = useRef(expandTracks);
  expandTracksRef.current = expandTracks;

  // One controller for the page's lifetime: the 250ms pending click is a
  // single shared slot, as the vanilla's module globals were.
  const clickController = useRef<ReturnType<typeof createAlbumClickController> | null>(null);
  if (clickController.current === null) {
    clickController.current = createAlbumClickController({
      onSelect: (albumId) =>
        setSelectedAlbums((current) => {
          const next = new Set(current);
          if (next.has(albumId)) next.delete(albumId);
          else next.add(albumId);
          return next;
        }),
      onExpandTracks: (albumId) => void expandTracksRef.current(albumId),
    });
  }

  const onAlbumClick = useCallback((albumId: string) => {
    clickController.current?.click(albumId);
  }, []);

  // ── build ───────────────────────────────────────────────────────────────

  async function buildTree() {
    if (selectedPlaylistId === null) {
      window.showToast?.('Select a playlist first', 'error');
      return;
    }
    if (building) return;

    setBuilding(true);
    setArtists([]);
    setMeta(null);
    setSelectedAlbums(new Set());
    setExpandedArtists(new Set());
    setExpandedTracks({});
    setAddedAlbums(new Set());
    resetZoom();
    setProgress({ pct: 0, text: 'Building tree...' });

    let total = 0;
    try {
      await streamBuildTree(selectedPlaylistId, mode, {
        onMeta: (nextMeta) => {
          total = nextMeta.total_artists || 0;
          setMeta(nextMeta);
        },
        onArtist: (artist, index) => {
          setArtists((current) => [...current, artist]);
          setProgress(explorerBuildProgress(index, total));
        },
      });
      setProgress(null);
      setHasBuilt(true);
      // The server persists explored_at; re-read so the card badge follows.
      refreshPlaylists();
      scheduleRedraw({ animate: true, delayMs: CONNECTION_BUILD_DELAY_MS });
    } catch (error) {
      window.showToast?.(`Explorer: ${(error as Error)?.message}`, 'error');
      setMeta(null);
      setProgress(null);
    } finally {
      setBuilding(false);
    }
  }

  // ── selection ───────────────────────────────────────────────────────────

  const artistsWithSelection = useMemo(() => {
    const keys = new Set<string>();
    for (const artist of artists) {
      if (artistHasSelection(artist, selectedAlbums)) keys.add(explorerArtistKey(artist.name));
    }
    return keys;
  }, [artists, selectedAlbums]);

  const wishlistSections = useMemo(
    () => groupSelectionByArtist(artists, selectedAlbums),
    [artists, selectedAlbums],
  );

  function openWishlist() {
    if (selectedAlbums.size === 0) {
      window.showToast?.('No albums selected', 'error');
      return;
    }
    if (wishlistSections.length === 0) {
      window.showToast?.('No valid albums selected', 'error');
      return;
    }
    setWishlistOpen(true);
  }

  return (
    <div className="page-shell explorer-container">
      <div className="dashboard-header" style={{ marginBottom: '12px' }}>
        <div className="dashboard-header-sweep" aria-hidden="true">
          <span />
        </div>
        <div className="header-text">
          <h2 className="header-title">
            <img src="/static/explorer.png" className="page-header-icon" alt="" />
            <span>Playlist Explorer</span>
          </h2>
        </div>
      </div>

      <ExplorerPicker
        playlists={playlists}
        activeSource={activeSource}
        onSelectSource={setActiveSource}
        selectedPlaylistId={selectedPlaylistId}
        onSelectPlaylist={setSelectedPlaylistId}
        onStartDiscovery={(id) => void startDiscovery(id)}
        discoverStates={discoverStates}
        liveDiscovery={liveDiscovery}
        mode={mode}
        onSetMode={setMode}
        building={building}
        hasBuilt={hasBuilt}
        onBuild={() => void buildTree()}
      />

      {meta && !building ? (
        <ExplorerActionBar
          selectedCount={selectedAlbums.size}
          onSelectAll={() => setSelectedAlbums(new Set(explorerSelectableAlbumIds(artists)))}
          onDeselectAll={() => setSelectedAlbums(new Set())}
          onAddToWishlist={openWishlist}
        />
      ) : null}

      <div className="explorer-viewport" id="explorer-viewport" ref={viewportRef}>
        <ExplorerZoomControls onZoom={zoomBy} onFitToView={fitToView} onResetZoom={resetZoom} />
        <ExplorerTree
          meta={meta}
          artists={artists}
          expandedArtists={expandedArtists}
          artistsWithSelection={artistsWithSelection}
          selectedAlbums={selectedAlbums}
          addedAlbums={addedAlbums}
          expandedTracks={expandedTracks}
          onToggleArtist={toggleArtist}
          onAlbumClick={onAlbumClick}
          zoom={zoom}
          connections={geometry}
          treeRef={treeRef}
        />
      </div>

      {progress ? <ExplorerProgress percent={progress.pct} text={progress.text} /> : null}

      {wishlistOpen ? (
        <ExplorerWishlistModal
          sections={wishlistSections}
          onClose={() => setWishlistOpen(false)}
          onFinished={() => {
            // The whole selection is marked added, not just what was
            // submitted — the vanilla iterated `_explorer.selectedAlbums`.
            setAddedAlbums((current) => new Set([...current, ...selectedAlbums]));
            setSelectedAlbums(new Set());
          }}
        />
      ) : null}
    </div>
  );
}
