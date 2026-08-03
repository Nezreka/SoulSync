/**
 * The tree itself (pages-extra.js:394-545, index.html:4205-4232).
 *
 * The vanilla appended each artist to the last row as its NDJSON line landed;
 * `chunkArtistRows` reproduces exactly that layout from the finished list, so
 * the silhouette is the same whether the tree streams in or re-renders whole.
 *
 * Ids are kept — #explorer-tree, #explorer-root, #explorer-node-<key> — because
 * the connection layer measures the laid-out nodes by querying for them.
 */

import type {
  ExplorerAlbum,
  ExplorerArtist,
  ExplorerMeta,
  ExplorerTrack,
} from '../-explorer.types';

import {
  chunkArtistRows,
  explorerAlbumNodeId,
  explorerAlbumTypeLabel,
  explorerArtistKey,
  explorerFormatDuration,
} from '../-explorer.core';
import { ExplorerConnections, type ExplorerPath } from './explorer-connections';

function TrackNode({ track, index }: { track: ExplorerTrack; index: number }) {
  return (
    <div
      className="explorer-branch"
      style={{ '--enter-delay': `${index * 0.03}s` } as React.CSSProperties}
    >
      <div className="explorer-node explorer-node-track">
        <div className="explorer-node-label">
          <div className="explorer-node-label-main">
            {track.track_number}. {track.name}
          </div>
          <div className="explorer-node-label-meta">
            {explorerFormatDuration(track.duration_ms)}
          </div>
        </div>
      </div>
    </div>
  );
}

interface AlbumNodeProps {
  album: ExplorerAlbum;
  nodeId: string;
  index: number;
  selected: boolean;
  added: boolean;
  tracks: ExplorerTrack[] | null;
  onClick: (nodeId: string) => void;
}

function AlbumNode({ album, nodeId, index, selected, added, tracks, onClick }: AlbumNodeProps) {
  const typeLabel = explorerAlbumTypeLabel(album.album_type);
  const owned = !!album.owned;
  const inPlaylist = !!album.in_playlist;
  const classes = [
    'explorer-node',
    'explorer-node-album',
    selected ? 'selected' : '',
    owned ? 'owned' : '',
    inPlaylist ? 'in-playlist' : '',
    added ? 'added' : '',
  ]
    .filter(Boolean)
    .join(' ');

  const title =
    `${album.title || ''}\n${album.year || ''} · ${typeLabel} · ${album.track_count || '?'} tracks` +
    `${owned ? '\n✓ Already in library' : ''}` +
    `${inPlaylist ? '\n♫ Track from this playlist' : ''}` +
    `\nClick to select · Double-click for tracklist`;

  return (
    <div
      className="explorer-branch"
      style={{ '--enter-delay': `${index * 0.06}s` } as React.CSSProperties}
    >
      <div
        className={classes}
        data-id={nodeId}
        data-key={nodeId}
        title={title}
        onClick={(event) => {
          event.stopPropagation();
          onClick(nodeId);
        }}
      >
        {album.image_url ? (
          <img className="explorer-node-img" src={album.image_url} alt="" loading="lazy" />
        ) : null}
        <div className="explorer-node-label">
          <div className="explorer-node-label-main">{album.title || 'Unknown'}</div>
          <div className="explorer-node-label-meta">
            {album.year || ''} · {album.track_count || '?'} tracks
          </div>
        </div>
        <div className={`explorer-node-select${selected ? ' active' : ''}`}>
          <svg viewBox="0 0 20 20">
            <polyline
              points="4 10 8 14 16 6"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        {owned ? <div className="explorer-node-badge-float owned">Owned</div> : null}
        {inPlaylist ? <div className="explorer-node-badge-float playlist">♫</div> : null}
      </div>
      <div className="explorer-children" id={`explorer-tracks-${nodeId}`}>
        {(tracks ?? []).map((track, i) => (
          <TrackNode key={`${track.track_number}-${track.name}-${i}`} track={track} index={i} />
        ))}
      </div>
    </div>
  );
}

interface ArtistBranchProps {
  artist: ExplorerArtist;
  index: number;
  expanded: boolean;
  hasSelection: boolean;
  selectedAlbums: ReadonlySet<string>;
  addedAlbums: ReadonlySet<string>;
  expandedTracks: Record<string, ExplorerTrack[] | undefined>;
  onToggleArtist: (key: string) => void;
  onAlbumClick: (nodeId: string) => void;
}

function ArtistBranch({
  artist,
  index,
  expanded,
  hasSelection,
  selectedAlbums,
  addedAlbums,
  expandedTracks,
  onToggleArtist,
  onAlbumClick,
}: ArtistBranchProps) {
  const key = explorerArtistKey(artist.name);
  const albums = artist.albums || [];
  const hasError = !!artist.error;
  const clickable = !hasError && albums.length > 0;
  const classes = [
    'explorer-node',
    'explorer-node-artist',
    hasError ? 'error' : '',
    expanded ? 'expanded' : '',
    hasSelection ? 'has-selection' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div
      className="explorer-branch"
      id={`explorer-branch-${key}`}
      // The stagger repeats every five nodes, so a long row still animates in
      // waves rather than trailing off (:431).
      style={{ '--enter-delay': `${(index % 5) * 0.1}s` } as React.CSSProperties}
    >
      <div
        className={classes}
        id={`explorer-node-${key}`}
        data-key={key}
        onClick={clickable ? () => onToggleArtist(key) : undefined}
      >
        {artist.image_url ? (
          <img className="explorer-node-img" src={artist.image_url} alt="" loading="lazy" />
        ) : null}
        <div className="explorer-node-label">
          <div className="explorer-node-label-main">{artist.name || 'Unknown'}</div>
          <div className="explorer-node-label-meta">
            {hasError ? 'Not found' : `${albums.length} album${albums.length !== 1 ? 's' : ''}`}
          </div>
        </div>
        {clickable ? <div className="explorer-node-expand-hint">▾</div> : null}
        {hasError ? <div className="explorer-node-error-ring" /> : null}
      </div>
      <div className="explorer-children" id={`explorer-children-${key}`}>
        {expanded
          ? albums.map((album, i) => {
              const nodeId = explorerAlbumNodeId(album, key, i);
              return (
                <AlbumNode
                  key={nodeId}
                  album={album}
                  nodeId={nodeId}
                  index={i}
                  selected={selectedAlbums.has(nodeId)}
                  added={addedAlbums.has(nodeId)}
                  tracks={expandedTracks[nodeId] ?? null}
                  onClick={onAlbumClick}
                />
              );
            })
          : null}
      </div>
    </div>
  );
}

export interface ExplorerTreeProps {
  meta: ExplorerMeta | null;
  artists: ExplorerArtist[];
  expandedArtists: ReadonlySet<string>;
  artistsWithSelection: ReadonlySet<string>;
  selectedAlbums: ReadonlySet<string>;
  addedAlbums: ReadonlySet<string>;
  expandedTracks: Record<string, ExplorerTrack[] | undefined>;
  onToggleArtist: (key: string) => void;
  onAlbumClick: (nodeId: string) => void;
  zoom: number;
  connections: { width: number; height: number; paths: ExplorerPath[] };
  treeRef?: React.Ref<HTMLDivElement>;
}

export function ExplorerTree({
  meta,
  artists,
  expandedArtists,
  artistsWithSelection,
  selectedAlbums,
  addedAlbums,
  expandedTracks,
  onToggleArtist,
  onAlbumClick,
  zoom,
  connections,
  treeRef,
}: ExplorerTreeProps) {
  const rows = chunkArtistRows(artists);
  let flatIndex = 0;

  return (
    <div
      className="explorer-tree"
      id="explorer-tree"
      ref={treeRef}
      style={{ transform: `scale(${zoom})`, transformOrigin: 'top center' }}
    >
      <ExplorerConnections
        width={connections.width}
        height={connections.height}
        paths={connections.paths}
      />

      {meta ? (
        <>
          <div className="explorer-tier explorer-tier-root">
            <div className="explorer-node explorer-node-root" id="explorer-root">
              <div className="explorer-node-glow" />
              {meta.playlist_image ? (
                <img className="explorer-node-img" src={meta.playlist_image} alt="" />
              ) : (
                <div className="explorer-node-img-placeholder">♫</div>
              )}
              <div className="explorer-node-label">
                <div className="explorer-node-label-sub">SOURCE</div>
                <div className="explorer-node-label-main">{meta.playlist_name}</div>
                <div className="explorer-node-label-meta">
                  {meta.total_tracks} tracks · {meta.total_artists} artists
                </div>
              </div>
            </div>
          </div>
          <div className="explorer-artist-tiers" id="explorer-artist-tiers">
            {rows.map((row, rowIndex) => (
              <div className="explorer-tier explorer-tier-artists" key={rowIndex}>
                {row.map((artist) => {
                  const key = explorerArtistKey(artist.name);
                  flatIndex += 1;
                  return (
                    <ArtistBranch
                      key={`${key}-${flatIndex}`}
                      artist={artist}
                      index={flatIndex}
                      expanded={expandedArtists.has(key)}
                      hasSelection={artistsWithSelection.has(key)}
                      selectedAlbums={selectedAlbums}
                      addedAlbums={addedAlbums}
                      expandedTracks={expandedTracks}
                      onToggleArtist={onToggleArtist}
                      onAlbumClick={onAlbumClick}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="explorer-empty" id="explorer-empty">
          <div className="explorer-empty-icon">
            <svg viewBox="0 0 80 80" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="40" cy="12" r="8" />
              <line x1="40" y1="20" x2="40" y2="35" />
              <line x1="40" y1="35" x2="18" y2="55" />
              <line x1="40" y1="35" x2="62" y2="55" />
              <circle cx="18" cy="60" r="6" />
              <circle cx="40" cy="60" r="6" />
              <circle cx="62" cy="60" r="6" />
              <line x1="40" y1="35" x2="40" y2="54" />
            </svg>
          </div>
          <p className="explorer-empty-title">Select a playlist to explore</p>
          <p className="explorer-empty-desc">
            Choose a mirrored playlist and mode above, then click Explore to build the discovery
            tree
          </p>
        </div>
      )}
    </div>
  );
}
