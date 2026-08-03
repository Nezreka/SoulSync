/**
 * The playlist picker: source tabs, the card strip, the mode toggle and the
 * build row (pages-extra.js:60-260, index.html:4149-4177).
 *
 * Presentational only — every decision it renders comes from
 * `explorerCardView` / `groupPlaylistsBySource`, and every action is a prop.
 */

import type { ExplorerMode, MirroredPlaylist } from '../-explorer.types';

import { explorerCardView, groupPlaylistsBySource, type ExplorerCardView } from '../-explorer.core';

/** What the Discover button on a card is currently saying (:196-207). */
export type DiscoverButtonState = 'idle' | 'starting' | 'open';

const DISCOVER_LABEL: Record<DiscoverButtonState, string> = {
  idle: 'Discover',
  starting: 'Starting...',
  open: 'Open',
};

interface PickerCardProps {
  playlist: MirroredPlaylist;
  view: ExplorerCardView;
  active: boolean;
  /** A live discovery percentage replaces the meta line entirely (:37-40). */
  livePercent: number | null;
  discoverState: DiscoverButtonState;
  onSelect: () => void;
  onDiscover: () => void;
}

function PickerCard({
  playlist,
  view,
  active,
  livePercent,
  discoverState,
  onSelect,
  onDiscover,
}: PickerCardProps) {
  const image = playlist.image_url || '';
  const classes = [
    'explorer-picker-card',
    active ? 'active' : '',
    view.isReady ? '' : 'not-ready',
    view.wasExplored ? 'explored' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    // A card below the readiness gate had NO click handler in the vanilla, not
    // a disabled one: clicking it does nothing at all (:158).
    <div className={classes} data-id={playlist.id} onClick={view.isReady ? onSelect : undefined}>
      <div className="explorer-picker-card-art">
        {image ? (
          <img src={image} alt="" loading="lazy" />
        ) : (
          <div className="explorer-picker-card-art-placeholder">♫</div>
        )}
      </div>
      <div className="explorer-picker-card-info">
        <div className="explorer-picker-card-name-row">
          <div className="explorer-picker-card-name">{playlist.name || 'Untitled'}</div>
          {view.badge ? (
            <div
              className={`explorer-picker-card-badge ${view.badge.kind}`}
              title={view.badge.title}
            >
              {view.badge.text}
            </div>
          ) : null}
        </div>
        <div className="explorer-picker-card-meta">
          {livePercent !== null ? (
            <span className="explorer-discovering-live">
              Discovering... {Math.round(livePercent)}%
            </span>
          ) : (
            <>
              {view.total} tracks ·{' '}
              {view.metaClass ? (
                <span className={view.metaClass}>{view.metaText}</span>
              ) : (
                view.metaText
              )}
              {view.statusParts.length > 0 ? (
                <>
                  <br />
                  {view.statusParts.map((part, i) => (
                    <span key={part.className}>
                      {i > 0 ? ' · ' : null}
                      <span className={part.className}>{part.text}</span>
                    </span>
                  ))}
                </>
              ) : null}
            </>
          )}
        </div>
        {view.showDiscoverButton ? (
          <div className="explorer-picker-card-actions">
            <button
              type="button"
              className="explorer-picker-discover-btn"
              disabled={discoverState === 'starting'}
              title={discoverState === 'open' ? 'Reopen discovery modal' : 'Start discovery'}
              onClick={(event) => {
                event.stopPropagation();
                onDiscover();
              }}
            >
              {DISCOVER_LABEL[discoverState]}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export interface ExplorerPickerProps {
  playlists: MirroredPlaylist[];
  activeSource: string | null;
  onSelectSource: (source: string) => void;
  selectedPlaylistId: number | null;
  onSelectPlaylist: (id: number) => void;
  onStartDiscovery: (id: number) => void;
  discoverStates: Record<number, DiscoverButtonState>;
  liveDiscovery: Record<number, number>;
  mode: ExplorerMode;
  onSetMode: (mode: ExplorerMode) => void;
  building: boolean;
  /** True once a tree has been built, which is when the vanilla's build button
   *  loses its long label for good (:337). */
  hasBuilt: boolean;
  onBuild: () => void;
}

export function ExplorerPicker({
  playlists,
  activeSource,
  onSelectSource,
  selectedPlaylistId,
  onSelectPlaylist,
  onStartDiscovery,
  discoverStates,
  liveDiscovery,
  mode,
  onSetMode,
  building,
  hasBuilt,
  onBuild,
}: ExplorerPickerProps) {
  const {
    groups,
    showTabs,
    activeSource: resolvedSource,
  } = groupPlaylistsBySource(playlists, activeSource);
  const visible = groups.find((group) => group.source === resolvedSource)?.playlists ?? [];
  const selected = playlists.find((playlist) => playlist.id === selectedPlaylistId);

  return (
    <div className="explorer-playlist-picker" id="explorer-playlist-picker">
      <div className="explorer-picker-top">
        <div
          className="explorer-picker-tabs"
          id="explorer-picker-tabs"
          style={showTabs ? undefined : { display: 'none' }}
        >
          {showTabs
            ? groups.map((group) => (
                <button
                  type="button"
                  key={group.source}
                  className={`explorer-picker-tab${group.source === resolvedSource ? ' active' : ''}`}
                  data-source={group.source}
                  onClick={() => onSelectSource(group.source)}
                >
                  {group.label} <span className="explorer-picker-tab-count">{group.count}</span>
                </button>
              ))
            : null}
        </div>
        <div className="explorer-controls">
          <div className="explorer-mode-toggle">
            <button
              type="button"
              className={`explorer-mode-btn${mode === 'albums' ? ' active' : ''}`}
              data-mode="albums"
              onClick={() => onSetMode('albums')}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <rect x="3" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="3" width="7" height="7" rx="1" />
                <rect x="3" y="14" width="7" height="7" rx="1" />
                <rect x="14" y="14" width="7" height="7" rx="1" />
              </svg>
              Albums
            </button>
            <button
              type="button"
              className={`explorer-mode-btn${mode === 'discographies' ? ' active' : ''}`}
              data-mode="discographies"
              onClick={() => onSetMode('discographies')}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <line x1="8" y1="6" x2="21" y2="6" />
                <line x1="8" y1="12" x2="21" y2="12" />
                <line x1="8" y1="18" x2="21" y2="18" />
                <line x1="3" y1="6" x2="3.01" y2="6" />
                <line x1="3" y1="12" x2="3.01" y2="12" />
                <line x1="3" y1="18" x2="3.01" y2="18" />
              </svg>
              Full Discog
            </button>
          </div>
        </div>
      </div>

      <div className="explorer-picker-scroll" id="explorer-picker-scroll">
        {playlists.length === 0 ? (
          <div className="explorer-picker-empty">
            No mirrored playlists found. Sync a playlist first.
          </div>
        ) : (
          visible.map((playlist) => (
            <PickerCard
              key={playlist.id}
              playlist={playlist}
              view={explorerCardView(playlist)}
              active={playlist.id === selectedPlaylistId}
              livePercent={liveDiscovery[playlist.id] ?? null}
              discoverState={discoverStates[playlist.id] ?? 'idle'}
              onSelect={() => onSelectPlaylist(playlist.id)}
              onDiscover={() => onStartDiscovery(playlist.id)}
            />
          ))
        )}
      </div>

      <div className="explorer-build-row">
        <div className="explorer-build-hint" id="explorer-build-hint">
          {selectedPlaylistId === null
            ? 'Select a playlist above, then explore'
            : selected
              ? `Ready: ${selected.name || 'Untitled'}`
              : ''}
        </div>
        <button
          type="button"
          className="explorer-build-btn"
          id="explorer-build-btn"
          disabled={building}
          onClick={onBuild}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          {/* The vanilla's finally block reset this to the shorter "Explore"
              rather than the markup's original label, so the button keeps that
              wording for the rest of the session (:392). */}
          {building ? 'Building...' : hasBuilt ? 'Explore' : 'Explore Selected Playlist'}
        </button>
      </div>
    </div>
  );
}
