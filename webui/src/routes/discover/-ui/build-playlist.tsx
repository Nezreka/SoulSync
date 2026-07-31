import type { SeedArtist } from '../-discover.build-playlist';

import {
  bpArtistImage,
  bpMetaStats,
  bpResultSubtitle,
  bpSelectionState,
  BP_MAX_SEEDS,
  BP_NO_SELECTION_HINT,
  BP_RESULT_TITLE,
} from '../-discover.build-playlist';
import { DiscoverSection } from './discover-section';

/**
 * The Build-a-Playlist section.
 *
 * Transcribed from index.html's build-a-playlist block and discover.js's seed
 * search / generate flow.
 *
 * The seed cap is a real constraint, not a suggestion: the generator takes at
 * most five, so the UI has to stop at five rather than letting the user pick
 * ten and silently dropping half.
 */

export interface BuildPlaylistSectionProps {
  query: string;
  results: SeedArtist[];
  dropdownOpen: boolean;
  selected: SeedArtist[];
  loaded: boolean;
  generating?: boolean;
  /** The generated playlist's stats, once there is one. */
  metadata?: Parameters<typeof bpMetaStats>[0];
  trackCount?: number;
  onQueryChange: (query: string) => void;
  onAdd: (artist: SeedArtist) => void;
  onRemove: (artistId: string) => void;
  onGenerate: () => void;
  onDownload: () => void;
}

export function BuildPlaylistSection({
  query,
  results,
  dropdownOpen,
  selected,
  loaded,
  generating,
  metadata,
  trackCount,
  onQueryChange,
  onAdd,
  onRemove,
  onGenerate,
  onDownload,
}: BuildPlaylistSectionProps) {
  const selection = bpSelectionState(selected);
  const stats = bpMetaStats(metadata);

  return (
    <DiscoverSection
      id="build-a-playlist"
      title="🎛️ Build a Playlist"
      subtitle={`Pick up to ${BP_MAX_SEEDS} artists and we'll build a playlist around them`}
      // Its own controls are the point, so it stays regardless of content.
      count={1}
      loaded={loaded}
    >
      <div className="bp-search" id="build-playlist-search">
        <div className="bp-input-wrap">
          <input
            type="text"
            id="build-playlist-input"
            placeholder="Search an artist to seed the playlist..."
            autoComplete="off"
            // At the cap there is nothing more to add, so the box closes rather
            // than offering results that would be rejected.
            disabled={generating || selection.count >= BP_MAX_SEEDS}
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
          />
          {dropdownOpen && results.length > 0 && (
            <div className="bp-dropdown" id="build-playlist-dropdown">
              {results.map((artist) => (
                <button
                  type="button"
                  key={artist.id}
                  className="bp-result"
                  onClick={() => onAdd(artist)}
                >
                  <img
                    className="bp-result-img"
                    src={bpArtistImage(artist)}
                    alt=""
                    loading="lazy"
                  />
                  <span className="bp-result-name">{artist.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="bp-seeds" id="build-playlist-seeds">
        {selection.showEmptyHint ? (
          <div className="bp-seeds-hint">{BP_NO_SELECTION_HINT}</div>
        ) : (
          selected.map((artist) => (
            <span className="bp-seed" key={artist.id}>
              <img className="bp-seed-img" src={bpArtistImage(artist)} alt="" />
              <span className="bp-seed-name">{artist.name}</span>
              <button
                type="button"
                className="bp-seed-remove"
                aria-label={`Remove ${artist.name}`}
                onClick={() => onRemove(artist.id)}
              >
                ✕
              </button>
            </span>
          ))
        )}
      </div>

      <div className="bp-actions">
        <button
          type="button"
          className="action-button primary"
          id="build-playlist-generate"
          // At least one seed, and not already running.
          disabled={selection.generateDisabled || generating}
          onClick={onGenerate}
        >
          Generate
        </button>
        {/* The counter's wording comes from the module, not a second copy. */}
        <span className="bp-seed-count">{selection.counterLabel}</span>
      </div>

      {trackCount != null && trackCount > 0 && (
        <div className="bp-result-panel" id="build-playlist-result">
          <div className="bp-result-head">
            <h3>{BP_RESULT_TITLE}</h3>
            <div className="bp-result-sub">{bpResultSubtitle(selected)}</div>
          </div>
          {stats.length > 0 && (
            <div className="bp-result-stats">
              {stats.map((stat) => (
                <span className="bp-stat" key={stat.label}>
                  <span className="bp-stat-value">{stat.value}</span>
                  <span className="bp-stat-label">{stat.label}</span>
                </span>
              ))}
            </div>
          )}
          <button type="button" className="action-button" onClick={onDownload}>
            Download playlist
          </button>
        </div>
      )}
    </DiscoverSection>
  );
}
