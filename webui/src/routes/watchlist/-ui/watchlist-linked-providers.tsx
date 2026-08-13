import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type {
  ProviderSearchResult,
  WatchlistArtistConfigResponse,
  WatchlistProvider,
} from '../-watchlist.types';

import {
  linkWatchlistProvider,
  searchProviderArtists,
  WATCHLIST_QUERY_KEY,
  watchlistArtistConfigQueryOptions,
} from '../-watchlist.api';
import { hideOnError } from './hide-on-error';

const PROVIDER_ROWS: {
  key: WatchlistProvider;
  label: string;
  icon: string;
  idKey: keyof WatchlistArtistConfigResponse;
}[] = [
  { key: 'spotify', label: 'Spotify', icon: '🟢', idKey: 'spotify_artist_id' },
  { key: 'itunes', label: 'Apple Music', icon: '🔴', idKey: 'itunes_artist_id' },
  { key: 'deezer', label: 'Deezer', icon: '🟣', idKey: 'deezer_artist_id' },
  { key: 'discogs', label: 'Discogs', icon: '🟤', idKey: 'discogs_artist_id' },
  { key: 'musicbrainz', label: 'MusicBrainz', icon: 'MB', idKey: 'musicbrainz_artist_id' },
  { key: 'amazon', label: 'Amazon Music', icon: '🟠', idKey: 'amazon_artist_id' },
];

/** Ids longer than 16 chars are shown as the first 14 plus an ellipsis. */
export function shortenProviderId(id: string): string {
  return id.length > 16 ? `${id.substring(0, 14)}...` : id;
}

interface Props {
  profileId: number;
  artistId: string;
  payload: WatchlistArtistConfigResponse;
}

export function WatchlistLinkedProviders({ profileId, artistId, payload }: Props) {
  const queryClient = useQueryClient();
  // The stored watchlist name is what the search box seeds with — it is the
  // name this entry was added under, not whatever a provider calls it now.
  const artistName = payload.watchlist_name || payload.artist?.name || '';

  const [openProvider, setOpenProvider] = useState<WatchlistProvider | null>(null);
  const [query, setQuery] = useState(artistName);
  const [results, setResults] = useState<ProviderSearchResult[] | null>(null);
  const [searchState, setSearchState] = useState<'idle' | 'searching' | 'error'>('idle');

  const refetchConfig = () =>
    queryClient.invalidateQueries({
      queryKey: watchlistArtistConfigQueryOptions(profileId, artistId).queryKey,
    });

  const link = useMutation({
    mutationFn: ({ provider, providerId }: { provider: WatchlistProvider; providerId: string }) =>
      linkWatchlistProvider(artistId, provider, providerId),
    onSuccess: async (_data, variables) => {
      // The vanilla flow closed and reopened the whole modal to pick up the new
      // match. Refetching the config achieves the same without the 300ms
      // close/reopen flicker, and without losing unsaved edits in the form.
      setOpenProvider(null);
      setResults(null);
      await Promise.all([
        refetchConfig(),
        queryClient.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY }),
      ]);
      if (variables.providerId) {
        window.showToast?.(`Linked on ${variables.provider}`, 'success');
      } else {
        window.showToast?.(`Cleared ${variables.provider} match`, 'success');
      }
    },
    onError: (error: Error) => window.showToast?.(error.message, 'error'),
  });

  const runSearch = async (provider: WatchlistProvider, term: string) => {
    const trimmed = term.trim();
    if (!trimmed) return;
    setSearchState('searching');
    try {
      setResults(await searchProviderArtists(provider, trimmed));
      setSearchState('idle');
    } catch {
      setResults(null);
      setSearchState('error');
    }
  };

  const openSearch = (provider: WatchlistProvider) => {
    setOpenProvider(provider);
    setQuery(artistName);
    setResults(null);
    setSearchState('idle');
  };

  const openLabel = openProvider
    ? (PROVIDER_ROWS.find((row) => row.key === openProvider)?.label ?? openProvider)
    : '';

  return (
    // id restores this section's helper.js contextual-help entry.
    <div id="watchlist-linked-provider-section" className="config-section">
      <h3 className="config-section-title">Linked Artist</h3>
      <p className="config-section-subtitle">
        The metadata provider artist linked to this watchlist entry
      </p>

      <div className="wl-linked-sources">
        {PROVIDER_ROWS.map((row) => {
          const rawId = payload[row.idKey];
          const id = typeof rawId === 'string' ? rawId : '';
          const matched = Boolean(id);
          return (
            <div
              key={row.key}
              className={`wl-linked-row ${matched ? 'matched' : 'unmatched'}`}
              data-source={row.key}
            >
              <span className="wl-linked-icon">{row.icon}</span>
              <span className="wl-linked-label">{row.label}</span>
              <span className="wl-linked-status">
                {matched ? (
                  <span className="wl-linked-id" title={id}>
                    {shortenProviderId(id)}
                  </span>
                ) : (
                  <span className="wl-linked-none">Not matched</span>
                )}
              </span>
              <button
                type="button"
                className="wl-linked-fix-btn"
                onClick={() => openSearch(row.key)}
              >
                {matched ? 'Fix' : 'Match'}
              </button>
              {matched ? (
                <button
                  type="button"
                  className="wl-linked-clear-btn"
                  title="Clear this match"
                  aria-label={`Clear ${row.label} match`}
                  disabled={link.isPending}
                  onClick={() => link.mutate({ provider: row.key, providerId: '' })}
                >
                  ×
                </button>
              ) : null}
            </div>
          );
        })}
      </div>

      {openProvider ? (
        <div className="wl-linked-search-panel">
          <div className="wl-linked-search-header">
            <span>Search {openLabel}</span>
            <button
              type="button"
              className="wl-linked-search-close"
              aria-label="Close search"
              onClick={() => setOpenProvider(null)}
            >
              ×
            </button>
          </div>
          <div className="wl-linked-search-input-row">
            <input
              type="text"
              className="watchlist-linked-search-input"
              placeholder="Search..."
              aria-label={`Search ${openLabel}`}
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void runSearch(openProvider, query);
              }}
            />
            <button
              type="button"
              className="watchlist-linked-search-btn"
              onClick={() => void runSearch(openProvider, query)}
            >
              Search
            </button>
          </div>
          <div className="wl-linked-search-results">
            {searchState === 'searching' ? (
              <div style={{ padding: 12, color: '#888', textAlign: 'center' }}>Searching...</div>
            ) : null}
            {searchState === 'error' ? (
              <div style={{ padding: 12, color: '#f44', textAlign: 'center' }}>Search error</div>
            ) : null}
            {searchState === 'idle' && results?.length === 0 ? (
              <div style={{ padding: 12, color: '#888', textAlign: 'center' }}>
                No artists found
              </div>
            ) : null}
            {searchState === 'idle'
              ? results?.map((result) => (
                  <div key={result.id} className="watchlist-linked-search-result">
                    {result.image ? (
                      <img
                        src={result.image}
                        alt=""
                        className="watchlist-linked-result-img"
                        onError={hideOnError}
                      />
                    ) : (
                      <div className="watchlist-linked-result-img-placeholder">🎵</div>
                    )}
                    <div className="watchlist-linked-result-info">
                      <div className="watchlist-linked-result-name">{result.name}</div>
                      <div className="watchlist-linked-result-meta">{result.extra || ''}</div>
                    </div>
                    <button
                      type="button"
                      className="watchlist-linked-select-btn"
                      disabled={link.isPending}
                      onClick={() =>
                        link.mutate({ provider: openProvider, providerId: String(result.id) })
                      }
                    >
                      Select
                    </button>
                  </div>
                ))
              : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
