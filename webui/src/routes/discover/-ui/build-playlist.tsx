import type { SeedArtist } from '../-discover.build-playlist';

import {
  bpArtistImage,
  bpResultSubtitle,
  bpSelectionState,
  BP_NO_SELECTION_HINT,
} from '../-discover.build-playlist';
import { DiscoverSection } from './discover-section';
import { SyncStatus } from './sync-status';

/**
 * The Build-a-Playlist section.
 *
 * Transcribed from index.html 5012-5121.
 *
 * The first draft of this was written from the module contracts WITHOUT reading
 * this markup, and invented almost every id and class — `#build-playlist-input`
 * for `#build-playlist-search`, `.bp-seeds` for
 * `.build-playlist-selected-artists`, and so on. It type-checked, it passed its
 * tests, and it would have rendered as an unstyled column of controls that the
 * vanilla's own handlers could not find. Everything below is the real markup.
 *
 * The section header (5004-5010) puts the info toggle "?" INSIDE the title and
 * the "How it works" panel (5012-5022) between the header and the container,
 * shown by toggling `visible` on `#bp-info-panel`.
 */

export interface BuildPlaylistSectionProps {
  query: string;
  results: SeedArtist[];
  /** The no-results / all-selected copy the search answered with (10921). */
  resultsMessage?: string | null;
  searching?: boolean;
  selected: SeedArtist[];
  generating?: boolean;
  /** Present once a playlist has been generated. */
  resultSubtitle?: string;
  hasResults: boolean;
  /** The live sync panel's progress, when a sync is running. */
  syncing?: boolean;
  syncProgress?: Parameters<typeof SyncStatus>[0]['progress'];
  /** The generated track list, rendered by the caller. */
  children?: React.ReactNode;
  /** The metadata strip, which the vanilla fills separately. */
  metadata?: React.ReactNode;
  onQueryChange: (query: string) => void;
  onAdd: (artist: SeedArtist) => void;
  onRemove: (artistId: string) => void;
  onGenerate: () => void;
  onDownload: () => void;
  onSync: () => void;
  /** The "How it works" panel's open state (5008). */
  infoOpen?: boolean;
  onToggleInfo: () => void;
  loaded: boolean;
}

export function BuildPlaylistSection({
  query,
  results,
  resultsMessage,
  searching,
  selected,
  generating,
  resultSubtitle,
  hasResults,
  syncing,
  syncProgress,
  children,
  metadata,
  onQueryChange,
  onAdd,
  onRemove,
  onGenerate,
  onDownload,
  onSync,
  infoOpen,
  onToggleInfo,
  loaded,
}: BuildPlaylistSectionProps) {
  const selection = bpSelectionState(selected);

  return (
    <DiscoverSection
      id="build-a-playlist"
      title={
        <>
          Build a Playlist{' '}
          <span className="bp-info-toggle" title="How it works" onClick={onToggleInfo}>
            ?
          </span>
        </>
      }
      subtitle="Create a custom playlist from your favorite artists"
      // Its own controls are the point, so it stays regardless of content.
      count={1}
      loaded={loaded}
    >
      <div id="bp-info-panel" className={infoOpen ? 'bp-info-panel visible' : 'bp-info-panel'}>
        <div className="bp-info-content">
          <p>
            <strong>How it works:</strong>
          </p>
          <ol>
            <li>Search and select 1-5 seed artists you like</li>
            <li>
              Hit Generate — the app finds similar artists, pulls their albums, and picks tracks at
              random
            </li>
            <li>You get a fresh 50-track playlist mixing your picks with new discoveries</li>
          </ol>
          <p className="bp-info-note">
            Tip: The more seed artists you add, the more varied the playlist will be.
          </p>
        </div>
      </div>
      <div className="build-playlist-container">
        <div className="build-playlist-search-section">
          <div className="bp-search-input-wrapper">
            <svg
              className="bp-search-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="11" cy="11" r="8" />
              <path d="M21 21l-4.35-4.35" />
            </svg>
            <input
              type="text"
              id="build-playlist-search"
              placeholder="Search for an artist..."
              autoComplete="off"
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
            />
            {searching && (
              <div className="bp-search-spinner" id="bp-search-spinner">
                <div
                  className="loading-spinner"
                  style={{ width: 18, height: 18, borderWidth: 2 }}
                />
              </div>
            )}
          </div>
          <div
            id="build-playlist-search-results"
            className="build-playlist-search-results"
            // display:none in the stylesheet; the vanilla flips it inline once
            // there is something to show (10906). Without this the search
            // "did nothing" — results rendered into an invisible box.
            style={{ display: results.length || resultsMessage ? 'block' : 'none' }}
          >
            {resultsMessage && <div className="build-playlist-no-selection">{resultsMessage}</div>}
            {results.map((artist) => (
              <button
                type="button"
                key={artist.id}
                className="build-playlist-search-result"
                onClick={() => onAdd(artist)}
              >
                <img src={bpArtistImage(artist)} alt={artist.name} loading="lazy" />
                <span className="bp-result-name">{artist.name}</span>
                <span className="bp-result-add">+ Add</span>
              </button>
            ))}
          </div>
        </div>

        <div className="build-playlist-selected-section">
          <div className="bp-selected-header">
            <h3>Seed Artists</h3>
            {/* The counter's wording comes from the module, not a second copy. */}
            <span className="bp-selected-counter" id="bp-selected-counter">
              {selection.counterLabel}
            </span>
          </div>
          <div id="build-playlist-selected-artists" className="build-playlist-selected-artists">
            {selection.showEmptyHint ? (
              <div className="build-playlist-no-selection">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  style={{ width: 32, height: 32, opacity: 0.4, marginBottom: 8 }}
                >
                  <path d="M12 4.5v15m7.5-7.5h-15" />
                </svg>
                <span>{BP_NO_SELECTION_HINT}</span>
              </div>
            ) : (
              selected.map((artist) => (
                <div className="build-playlist-selected-artist" key={artist.id}>
                  <img src={bpArtistImage(artist)} alt={artist.name} loading="lazy" />
                  <span>{artist.name}</span>
                  <button
                    type="button"
                    className="build-playlist-remove-artist"
                    title="Remove"
                    aria-label={`Remove ${artist.name}`}
                    onClick={() => onRemove(artist.id)}
                  >
                    ×
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="build-playlist-actions">
          <button
            type="button"
            id="build-playlist-generate-btn"
            className="build-playlist-generate-btn"
            disabled={selection.generateDisabled || generating}
            onClick={onGenerate}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              style={{ width: 18, height: 18 }}
            >
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
            Generate Playlist
          </button>
          {generating && (
            <div id="build-playlist-loading" className="build-playlist-loading">
              <div className="loading-spinner" />
              <span>Finding similar artists and building your playlist...</span>
            </div>
          )}
        </div>

        {hasResults && (
          <div id="build-playlist-results-wrapper">
            <div className="discover-section-header" style={{ marginTop: 20 }}>
              <div>
                <h3
                  id="build-playlist-results-title"
                  style={{ margin: 0, color: '#fff', fontSize: 18 }}
                >
                  Generated Playlist
                </h3>
                <p
                  id="build-playlist-results-subtitle"
                  style={{ margin: '4px 0 0 0', color: '#999', fontSize: 13 }}
                >
                  {resultSubtitle ?? bpResultSubtitle(selected)}
                </p>
              </div>
              <div className="discover-section-actions">
                <button
                  type="button"
                  className="action-button secondary"
                  title="Download missing tracks"
                  onClick={onDownload}
                >
                  <span className="button-icon">↓</span>
                  <span className="button-text">Download</span>
                </button>
                <button
                  type="button"
                  className="action-button primary"
                  id="build-playlist-sync-btn"
                  title="Sync to media server"
                  onClick={onSync}
                >
                  <span className="button-icon">⟳</span>
                  <span className="button-text">Sync</span>
                </button>
              </div>
            </div>

            <SyncStatus
              statusBase="build-playlist"
              progress={syncProgress}
              visible={Boolean(syncing)}
            />

            <div id="build-playlist-metadata-display">{metadata}</div>

            <div id="build-playlist-results" className="discover-playlist-container compact">
              {children}
            </div>
          </div>
        )}
      </div>
    </DiscoverSection>
  );
}
