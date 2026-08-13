import { useEffect, useRef, useState } from 'react';

import type { EnrichmentOutcome, MatchSearchResult } from '../-artist-detail.enrich-match';

import {
  applyManualMatchRequest,
  clearMatchRequest,
  matchServiceLabel,
  searchServiceRequest,
} from '../-artist-detail.enrich-match';

/**
 * The manual match modal (openManualMatchModal, library.js:4625): search one
 * metadata service for the right artist/album/track and pin the match, or
 * clear a wrong one back to Not Found. Auto-searches the default query on
 * open, exactly as the vanilla clicked Search for you.
 *
 * Two deliberate fixes over the vanilla:
 *  - Clear Match confirms through the shared confirm dialog, not a bare
 *    window.confirm.
 *  - Its updated_data refreshes the view through onUpdated — the vanilla
 *    called renderEnhancedArtistView, which was defined nowhere.
 */

interface Props {
  entityType: 'artist' | 'album' | 'track';
  entityId: unknown;
  service: string;
  defaultQuery: string;
  artistId: unknown;
  onUpdated: (outcome: EnrichmentOutcome) => void;
  onClose: () => void;
}

type Results =
  | { kind: 'hint'; text: string }
  | { kind: 'loading' }
  | { kind: 'loaded'; results: MatchSearchResult[] };

export function ManualMatchModal({
  entityType,
  entityId,
  service,
  defaultQuery,
  artistId,
  onUpdated,
  onClose,
}: Props) {
  const [query, setQuery] = useState(defaultQuery);
  const [results, setResults] = useState<Results>({
    kind: 'hint',
    text: 'Press Search or Enter to find matches',
  });
  const label = matchServiceLabel(service);
  const searchedOnOpen = useRef(false);

  const search = async (value: string) => {
    if (!value.trim()) {
      setResults({ kind: 'hint', text: 'Enter a search term' });
      return;
    }
    setResults({ kind: 'loading' });
    try {
      const found = await searchServiceRequest(service, entityType, value);
      setResults(
        found.length
          ? { kind: 'loaded', results: found }
          : { kind: 'hint', text: 'No results found. Try a different search.' },
      );
    } catch (error) {
      setResults({ kind: 'hint', text: `Error: ${(error as Error).message}` });
    }
  };

  useEffect(() => {
    if (searchedOnOpen.current) return;
    searchedOnOpen.current = true;
    void search(defaultQuery);
    // Auto-search happens exactly once, with the query the caller provided.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyMatch = async (result: MatchSearchResult) => {
    try {
      window.showToast?.(`Matching ${entityType} to ${service}...`, 'info');
      const outcome = await applyManualMatchRequest({
        entityType,
        entityId,
        // A proxied result matches through its real provider (4792).
        service: result.provider || service,
        serviceId: result.id,
        artistId,
      });
      window.showToast?.(`Manually matched to ${service} ID: ${result.id}`, 'success');
      onClose();
      onUpdated(outcome);
    } catch (error) {
      window.showToast?.(`Match failed: ${(error as Error).message}`, 'error');
    }
  };

  const clearMatch = async () => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Clear Match',
      message: `Clear ${label} match for this ${entityType}? It will revert to "Not Found".`,
      confirmText: 'Clear Match',
      destructive: true,
    });
    if (!confirmed) return;
    try {
      const outcome = await clearMatchRequest({ entityType, entityId, service, artistId });
      window.showToast?.(`Cleared ${label} match`, 'success');
      onClose();
      onUpdated(outcome);
    } catch {
      window.showToast?.('Error clearing match', 'error');
    }
  };

  return (
    <div
      id="enhanced-manual-match-overlay"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="enhanced-manual-match-modal">
        <div className="enhanced-bulk-modal-header">
          <h3>
            Match {entityType} on {label}
          </h3>
          <button className="enhanced-bulk-modal-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="enhanced-match-search-row">
          <input
            type="text"
            className="enhanced-match-search-input"
            placeholder={
              service === 'musicbrainz'
                ? `Search ${label}… or paste a MusicBrainz ID/URL`
                : `Search ${label}...`
            }
            value={query}
            autoFocus
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void search(query);
            }}
          />
          <button className="enhanced-enrich-btn" type="button" onClick={() => void search(query)}>
            Search
          </button>
          <button
            className="enhanced-enrich-btn enhanced-clear-match-btn"
            type="button"
            title="Remove the current match — reverts to Not Found"
            style={{ background: 'rgba(255,80,80,0.12)', color: '#ff6b6b', marginLeft: 6 }}
            onClick={() => void clearMatch()}
          >
            Clear Match
          </button>
        </div>

        <div className="enhanced-match-results">
          {results.kind === 'hint' ? (
            <div className="enhanced-match-results-hint">{results.text}</div>
          ) : results.kind === 'loading' ? (
            <div className="enhanced-loading">Searching...</div>
          ) : (
            results.results.map((result) => (
              <div
                className="enhanced-match-result-row"
                key={`${result.provider || service}:${result.id}`}
              >
                {result.image ? (
                  <MatchResultImage src={result.image} />
                ) : (
                  <div className="enhanced-match-result-img-placeholder">🎵</div>
                )}
                <div className="enhanced-match-result-info">
                  <div className="enhanced-match-result-name">{result.name || 'Unknown'}</div>
                  {result.extra ? (
                    <div className="enhanced-match-result-extra">{result.extra}</div>
                  ) : null}
                  <div className="enhanced-match-result-id">
                    ID: {result.id}
                    {result.provider && result.provider !== service ? ` (${result.provider})` : ''}
                  </div>
                </div>
                <button
                  className="enhanced-meta-save-btn"
                  type="button"
                  onClick={() => void applyMatch(result)}
                >
                  Match
                </button>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

/** The vanilla hid a broken result image rather than showing alt text (4761). */
function MatchResultImage({ src }: { src: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return <div className="enhanced-match-result-img-placeholder">🎵</div>;
  return (
    <img className="enhanced-match-result-img" src={src} alt="" onError={() => setBroken(true)} />
  );
}
