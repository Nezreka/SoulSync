/**
 * The Fix Track Match modal — React-native (P4b), replacing the
 * wishlist-tools flow whose registry reads a React page cannot satisfy
 * (P4a review finding #1). Behaviour transcribed from openDiscoveryFixModal /
 * searchDiscoveryFix / lookupDiscoveryFixByMbid / renderDiscoveryFixResults /
 * selectDiscoveryFixTrack (wishlist-tools.js 22-445):
 *
 * - Prefilled from the row's source track/artist, auto-search on open.
 * - The cascade searches the LIVE metadata source first, then the fallbacks,
 *   showing "Trying X..." between sources (241-254). (Bug #5 knowing fix:
 *   the vanilla reordered on the never-updated currentMusicSourceName, so its
 *   active-first reorder never actually fired.)
 * - The MBID escape hatch resolves a MusicBrainz recording directly and
 *   renders it as a one-item result list (322-324).
 * - Result cards sort standard versions first; clicking confirms through the
 *   SoulSync confirm dialog (376) then POSTs update_match; the caller applies
 *   applyFixedMatch and closes.
 *
 * Declared divergences: the auto-search fires on mount instead of after the
 * vanilla's 500ms settle delay (that delay guarded against accidental clicks;
 * the confirm dialog covers it), prefills show the transform's 'Unknown'
 * default rather than 'Unknown Track'/'Unknown Artist' (rows are already
 * normalized), and result text sanitizes shapes the vanilla renders as
 * "[object Object]". Mount with key={row.index} — the prefill and auto-search
 * read the row once.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { FixTrack } from '../-sync.fix';
import type { SourceVerticalConfig } from '../-sync.sources';
import type { DiscoveryRow } from '../-sync.transform';

import { formatDuration } from '../-sync.core';
import {
  buildUpdateMatchBody,
  lookupMbRecording,
  orderedFixSearchSources,
  parseMusicBrainzMbid,
  postUpdateMatch,
  searchFixTracks,
  sortFixResults,
} from '../-sync.fix';
import { metadataSourceLabel } from '../-sync.modal-core';

// showConfirmDialog + showToast are declared by the shell globals
// (globals.d.ts) — the SoulSync confirm dialog, never window.confirm.

export interface FixModalProps {
  config: SourceVerticalConfig;
  /** The source's own id — the backend identifier for every source. */
  sourceId: string;
  row: DiscoveryRow;
  onClose: () => void;
  /** Fired after a successful update_match; the caller applies applyFixedMatch. */
  onFixed: (trackIndex: number, track: FixTrack) => void;
}

function artistsText(track: FixTrack): string {
  const artists = track.artists;
  if (Array.isArray(artists)) {
    return (
      artists
        .map((a) => (typeof a === 'object' && a !== null ? (a.name ?? '') : a))
        .filter(Boolean)
        .join(', ') || 'Unknown Artist'
    );
  }
  return artists || 'Unknown Artist';
}

function albumText(track: FixTrack): string {
  if (typeof track.album === 'object' && track.album !== null) {
    return String((track.album as { name?: string }).name ?? 'Unknown Album');
  }
  return track.album || 'Unknown Album';
}

export function FixModal({ config, sourceId, row, onClose, onFixed }: FixModalProps) {
  const [trackQuery, setTrackQuery] = useState(row.yt_track);
  const [artistQuery, setArtistQuery] = useState(row.yt_artist);
  const [mbidQuery, setMbidQuery] = useState('');
  const [results, setResults] = useState<FixTrack[]>([]);
  const [statusText, setStatusText] = useState('');
  const searchToken = useRef(0);

  const runSearch = useCallback(async (track: string, artist: string) => {
    const trimmedTrack = track.trim();
    const trimmedArtist = artist.trim();
    if (!trimmedTrack && !trimmedArtist) {
      window.showToast?.('Enter track name or artist', 'error');
      return;
    }
    const token = ++searchToken.current;
    const sources = orderedFixSearchSources(metadataSourceLabel());
    setResults([]);
    setStatusText(`🔍 Searching ${sources[0].label}...`);
    for (let i = 0; i < sources.length; i++) {
      try {
        const tracks = await searchFixTracks(sources[i].endpoint, trimmedTrack, trimmedArtist);
        if (token !== searchToken.current) return; // a newer search superseded this one
        if (tracks.length > 0) {
          setResults(sortFixResults(tracks));
          setStatusText('');
          return;
        }
        if (i < sources.length - 1) setStatusText(`🔍 Trying ${sources[i + 1].label}...`);
      } catch {
        // A failing source is skipped, exactly like the vanilla (255-257).
      }
    }
    if (token !== searchToken.current) return;
    setStatusText('No matches found on any source. Try different search terms.');
  }, []);

  // Auto-search with the prefilled values on open (153).
  const initialSearch = useRef(false);
  useEffect(() => {
    if (initialSearch.current) return;
    initialSearch.current = true;
    void runSearch(row.yt_track, row.yt_artist);
  }, [row.yt_track, row.yt_artist, runSearch]);

  const runMbidLookup = useCallback(async () => {
    const mbid = parseMusicBrainzMbid(mbidQuery);
    if (!mbid) {
      setResults([]);
      setStatusText(
        '❌ Not a valid MusicBrainz recording URL or MBID. Paste a URL like https://musicbrainz.org/recording/<uuid> or the bare UUID.',
      );
      window.showToast?.('Invalid MusicBrainz MBID', 'error');
      return;
    }
    const token = ++searchToken.current;
    setResults([]);
    setStatusText('🔗 Looking up MusicBrainz recording...');
    try {
      const result = await lookupMbRecording(mbid);
      if (token !== searchToken.current) return;
      if (result.kind === 'missing') {
        setStatusText('Recording not found on MusicBrainz. Double-check the MBID.');
        return;
      }
      if (result.kind === 'no-id') {
        setStatusText('Recording not found on MusicBrainz.');
        return;
      }
      setResults([result.track]);
      setStatusText('');
    } catch {
      if (token !== searchToken.current) return;
      setStatusText('❌ MBID lookup failed. Try again.');
    }
  }, [mbidQuery]);

  const selectTrack = useCallback(
    async (track: FixTrack) => {
      // The SoulSync confirm dialog, never window.confirm (376).
      const confirmed = await window.showConfirmDialog?.({
        title: 'Confirm Match',
        message: `Match to "${track.name}" by ${artistsText(track)}?`,
        confirmText: 'Confirm',
      });
      if (!confirmed) return;
      try {
        const body = buildUpdateMatchBody(sourceId, row.index, row.yt_track, row.yt_artist, track);
        const data = await postUpdateMatch(config, body);
        if (data.error) {
          window.showToast?.(`Failed to update: ${data.error}`, 'error');
          return;
        }
        window.showToast?.('Match updated successfully!', 'success');
        onFixed(row.index, track);
        onClose();
      } catch {
        window.showToast?.('Failed to update match', 'error');
      }
    },
    [config, sourceId, row, onFixed, onClose],
  );

  return (
    <div className="discovery-fix-modal-overlay" id="discovery-fix-modal-overlay">
      <div className="discovery-fix-modal">
        <div className="discovery-fix-modal-header">
          <h2>Fix Track Match</h2>
          <button type="button" className="modal-close-btn" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="discovery-fix-modal-content">
          <div className="source-track-info">
            <h3>Source Track</h3>
            <div className="source-track-display">
              <div className="source-field">
                <label>Track:</label>
                <span>{row.yt_track}</span>
              </div>
              <div className="source-field">
                <label>Artist:</label>
                <span>{row.yt_artist}</span>
              </div>
            </div>
          </div>

          <div className="search-inputs-section">
            <h3>Search for Match</h3>
            <div className="search-input-group">
              <input
                type="text"
                className="fix-modal-input"
                placeholder="Track name"
                value={trackQuery}
                onChange={(e) => setTrackQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void runSearch(trackQuery, artistQuery);
                }}
              />
              <input
                type="text"
                className="fix-modal-input"
                placeholder="Artist name"
                value={artistQuery}
                onChange={(e) => setArtistQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void runSearch(trackQuery, artistQuery);
                }}
              />
              <button
                type="button"
                className="search-btn"
                onClick={() => void runSearch(trackQuery, artistQuery)}
              >
                🔍 Search
              </button>
            </div>
            <div className="search-input-group" style={{ marginTop: 8 }}>
              <input
                type="text"
                className="fix-modal-input"
                style={{ flex: 1 }}
                placeholder="Or paste MusicBrainz recording URL / MBID"
                value={mbidQuery}
                onChange={(e) => setMbidQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void runMbidLookup();
                }}
              />
              <button type="button" className="search-btn" onClick={() => void runMbidLookup()}>
                🔗 Look up
              </button>
            </div>
          </div>

          <div className="search-results-section">
            <h3>Results</h3>
            <div className="fix-modal-results">
              {/* The vanilla uses three status classes, and .error-message is
                  styled red (style.css 33945-33956); rendering every message
                  as .loading loses that (wishlist-tools.js 291/305/311/317). */}
              {statusText && (
                <div className={statusText.startsWith('❌') ? 'error-message' : 'loading'}>
                  {statusText}
                </div>
              )}
              {results.map((track, index) => (
                <div
                  key={track.id ?? index}
                  className="fix-result-card"
                  onClick={() => void selectTrack(track)}
                >
                  <div className="fix-result-card-content">
                    <div className="fix-result-title">{track.name || 'Unknown Track'}</div>
                    <div className="fix-result-artist">{artistsText(track)}</div>
                    <div className="fix-result-album">{albumText(track)}</div>
                    <div className="fix-result-duration">
                      {formatDuration(track.duration_ms || 0)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="discovery-fix-modal-footer">
          <button type="button" className="modal-btn secondary" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
