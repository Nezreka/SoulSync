import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type { LibraryArtist, LibraryArtistsResponse } from '../-library.types';

import { libraryArtistsQueryOptions, setArtistWatchlisted } from '../-library.api';
import { readArtistsResponse, watchlistArtistId } from '../-library.helpers';
import { useLibraryChanged } from '../-library.live';
import { loadTopTracks, trackArtistLabel } from '../../artist-detail/-artist-detail.top-tracks';
import { Route } from '../route';
import { ExportArtistsModal } from './export-modal';
import { LibraryArtistCard } from './library-artist-card';
import { WatchAllModal } from './watch-all-modal';

const ALPHABET = ['all', ...'abcdefghijklmnopqrstuvwxyz'.split(''), '#'];

/** The metadata-source filter's two option groups, verbatim from index.html. */
const SOURCES = [
  'spotify',
  'musicbrainz',
  'deezer',
  'discogs',
  'audiodb',
  'itunes',
  'lastfm',
  'genius',
  'tidal',
  'qobuz',
] as const;
const SOURCE_LABELS: Record<string, string> = {
  spotify: 'Spotify',
  musicbrainz: 'MusicBrainz',
  deezer: 'Deezer',
  discogs: 'Discogs',
  audiodb: 'AudioDB',
  itunes: 'iTunes',
  lastfm: 'Last.fm',
  genius: 'Genius',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
};

const SEARCH_DEBOUNCE_MS = 300;

export function LibraryPage() {
  useReactPageShell('library');

  const { profileId } = useProfile();
  useLibraryChanged();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const [exporting, setExporting] = useState(false);
  const [watchingAll, setWatchingAll] = useState(false);
  const [playing, setPlaying] = useState<ReadonlySet<string>>(new Set());

  // The input is local so typing stays responsive; the URL only catches up
  // after the debounce, exactly as the vanilla 300ms timer did.
  const [draft, setDraft] = useState(search.q);
  const committed = useRef(search.q);
  useEffect(() => {
    // Reflect a URL change that did not come from typing (back button, a
    // cleared filter) back into the box.
    if (search.q !== committed.current) {
      committed.current = search.q;
      setDraft(search.q);
    }
  }, [search.q]);

  useEffect(() => {
    if (draft === committed.current) return;
    const timer = setTimeout(() => {
      committed.current = draft;
      // Any filter change resets to page 1 — page 7 of the old result set is
      // meaningless against a new one.
      void navigate({ search: (prev) => ({ ...prev, q: draft, page: 1 }), replace: true });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, navigate]);

  const query = useQuery(libraryArtistsQueryOptions(profileId, search));
  const { artists, pagination } = useMemo(() => {
    try {
      return readArtistsResponse(query.data);
    } catch {
      // The thrown reason surfaces through query.error below; the grid just
      // renders empty rather than taking the page down.
      return {
        artists: [],
        pagination: { page: 1, totalPages: 0, totalCount: 0, hasPrev: false, hasNext: false },
      };
    }
  }, [query.data]);

  const failed = query.isError || query.data?.success === false;
  const loading = query.isPending;
  const isEmpty = !loading && artists.length === 0;

  // The vanilla catch toasted a FIXED string on every failed load — not the
  // server's reason, which it only used as the thrown message. Keyed on the
  // update timestamp so each distinct failure toasts once, rather than on
  // every re-render (or never again after the first).
  const failedAt = failed ? query.errorUpdatedAt || query.dataUpdatedAt : 0;
  useEffect(() => {
    if (!failedAt) return;
    window.showToast?.('Failed to load artists', 'error');
  }, [failedAt]);

  // Add-to-watchlist straight from a card badge.
  //
  // The result is patched into THIS page of the cache rather than invalidated:
  // the vanilla handler only repainted the one badge, and a refetch here would
  // pull all 75 artists back down for a one-field change.
  const queryClient = useQueryClient();
  // Tracked per artist, not off the mutation's `variables`: those hold only the
  // LATEST call, so adding a second artist while the first is still in flight
  // would move the "..." off the card that is actually waiting.
  const [watching, setWatching] = useState<ReadonlySet<string>>(new Set());
  const watchArtist = useMutation({
    onMutate: (artist: LibraryArtist) => setWatching((s) => new Set(s).add(String(artist.id))),
    onSettled: (_data, _error, artist) =>
      setWatching((s) => {
        const next = new Set(s);
        next.delete(String(artist.id));
        return next;
      }),
    mutationFn: async (artist: LibraryArtist) => {
      const artistId = watchlistArtistId(artist, window.currentMusicSourceName);
      // The badge is only offered when this resolves, so reaching here means
      // the card and the id-picker disagreed.
      if (!artistId) throw new Error('No iTunes or Spotify ID available for this artist');
      await setArtistWatchlisted(artistId, artist.name, true);
    },
    onSuccess: (_data, artist) => {
      queryClient.setQueryData(
        libraryArtistsQueryOptions(profileId, search).queryKey,
        (prev: LibraryArtistsResponse | undefined) =>
          prev?.artists
            ? {
                ...prev,
                artists: prev.artists.map((a) =>
                  a.id === artist.id ? { ...a, is_watched: true } : a,
                ),
              }
            : prev,
      );
      window.showToast?.(`Added ${artist.name} to watchlist`, 'success');
      window.updateWatchlistCount?.();
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  const setSearch = (patch: Partial<typeof search>) =>
    void navigate({ search: (prev) => ({ ...prev, ...patch, page: patch.page ?? 1 }) });

  /**
   * Load the same popularity-ranked list as the artist hero and hand the whole
   * context to the acquisition-aware player. Its queue preflight resolves
   * owned files first, so a provider/Last.fm row cannot duplicate a track that
   * is already in the library; genuine misses enter the normal download flow.
   */
  const playArtistTopTracks = async (artist: LibraryArtist) => {
    const key = String(artist.id);
    if (playing.has(key)) return;
    setPlaying((current) => new Set(current).add(key));
    try {
      const state = await loadTopTracks(artist.id, artist.name);
      if (!state.tracks.length) {
        window.showToast?.(`No top tracks found for ${artist.name}`, 'info');
        return;
      }
      const tracks = state.tracks.map((track) => {
        const artistName = trackArtistLabel(track, artist.name);
        return {
          ...track,
          title: track.title || track.name || 'Unknown Track',
          name: track.name || track.title || 'Unknown Track',
          artist: artistName,
          artists: track.artists?.length ? track.artists : [{ name: artistName }],
        };
      });
      await window.playTrackList?.(tracks, `${artist.name} — Top Tracks`);
    } catch {
      window.showToast?.(`Could not play ${artist.name}'s top tracks`, 'error');
    } finally {
      setPlaying((current) => {
        const next = new Set(current);
        next.delete(key);
        return next;
      });
    }
  };

  // TRAP 0 — showLibraryDownloadsSection (shared-helpers.js) inserts a
  // #library-downloads-section node as a SIBLING before #library-artists-grid,
  // and fires on download events, not just page load. It is bound to
  // `artistDownloadBubbles`, module state in core.js, so it cannot move here.
  // This host is rendered with NO React children, so the vanilla function owns
  // its subtree outright and React never reconciles it away — the same
  // arrangement used to mount the Automation Hub.
  const downloadsHost = useRef<HTMLDivElement>(null);
  useEffect(() => {
    window.showLibraryDownloadsSection?.();
  }, []);

  return (
    // The ids below are the guided tour's anchors (helper.js HELP_CONTENT).
    // The vanilla page owned them until it was deleted; nothing else renders
    // them now, so there is no duplicate-id hazard — and #library-page is what
    // disambiguates these controls from the VIDEO library's .library-controls.
    <div className="library-container" id="library-page">
      <div className="library-header">
        <div className="library-header-content">
          <h2 className="library-title">
            <img src="/static/library.png" className="page-header-icon" alt="" />
            <span>Music Library</span>
          </h2>
          <p className="library-subtitle">Browse your complete music collection</p>
        </div>
        <div className="library-stats" id="library-stats">
          <span className="library-stat">
            <span className="stat-number" id="library-artist-count">
              {pagination.totalCount}
            </span>
            <span className="stat-label">Artists</span>
          </span>
        </div>
        <button
          type="button"
          className="library-watchlist-all-btn library-radio-btn"
          title="Library Radio — shuffle your whole library, similar tracks keep auto-queuing"
          onClick={() => void window.startLibraryRadio?.()}
        >
          <span className="watchlist-all-icon">📻</span>
          <span className="watchlist-all-text">Radio</span>
        </button>
        <button
          type="button"
          className="library-watchlist-all-btn library-export-btn"
          title="Export artists — pick watchlist or whole library, as JSON / CSV / text"
          onClick={() => setExporting(true)}
        >
          <span className="watchlist-all-icon">⬇</span>
          <span className="watchlist-all-text">Export</span>
        </button>
        {exporting ? <ExportArtistsModal onClose={() => setExporting(false)} /> : null}
        {watchingAll ? <WatchAllModal onClose={() => setWatchingAll(false)} /> : null}
      </div>

      <div className="library-controls">
        <div className="library-search-container">
          <input
            type="text"
            className="library-search-input"
            id="library-search-input"
            placeholder="Filter your library…"
            aria-label="Filter your library"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Escape clears the box AND reloads, as the vanilla handler did.
              if (e.key !== 'Escape') return;
              setDraft('');
              committed.current = '';
              void navigate({ search: (prev) => ({ ...prev, q: '', page: 1 }), replace: true });
            }}
          />
          <div className="library-search-icon">🔍</div>
        </div>

        <div className="watchlist-filter" id="watchlist-filter">
          {(['all', 'watched', 'unwatched'] as const).map((f) => (
            <button
              key={f}
              type="button"
              className={`watchlist-filter-btn${search.watchlist === f ? ' active' : ''}`}
              data-filter={f}
              onClick={() => setSearch({ watchlist: f })}
            >
              {f === 'all' ? 'All' : f === 'watched' ? 'Watched' : 'Unwatched'}
            </button>
          ))}
          {/* Only offered while filtered to unwatched — it acts on that set. */}
          <button
            type="button"
            className={`library-watchlist-all-btn${search.watchlist === 'unwatched' ? '' : ' hidden'}`}
            onClick={() => setWatchingAll(true)}
          >
            <span className="watchlist-all-icon">👁️</span>
            <span className="watchlist-all-text">Watch All Unwatched</span>
          </button>
        </div>

        <div className="library-source-filter">
          <select
            className="library-source-filter-select"
            aria-label="Filter by metadata source"
            value={search.source}
            onChange={(e) => setSearch({ source: e.target.value })}
          >
            <option value="">All Sources</option>
            {/* The `!` prefix means "unmatched to", and the backend parses it. */}
            <optgroup label="Unmatched to">
              {SOURCES.map((s) => (
                <option key={`!${s}`} value={`!${s}`}>
                  No {SOURCE_LABELS[s]}
                </option>
              ))}
            </optgroup>
            <optgroup label="Matched to">
              {SOURCES.map((s) => (
                <option key={s} value={s}>
                  Has {SOURCE_LABELS[s]}
                </option>
              ))}
            </optgroup>
          </select>
        </div>

        <div className="alphabet-selector" id="alphabet-selector">
          <div className="alphabet-selector-inner">
            {ALPHABET.map((letter) => (
              <button
                key={letter}
                type="button"
                className={`alphabet-btn${search.letter === letter ? ' active' : ''}`}
                data-letter={letter}
                onClick={() => setSearch({ letter })}
              >
                {letter === 'all' ? 'All' : letter === '#' ? '#' : letter.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="library-content">
        {loading ? (
          <div className="library-loading">
            <div className="loading-spinner" />
            <div className="loading-text">Loading artists...</div>
          </div>
        ) : null}

        {/* Host for the vanilla downloads bubbles — see TRAP 0 above. Sits
            directly before the grid, where insertBefore(section, artistGrid)
            put it: after the loading row, above the cards. */}
        <div ref={downloadsHost} data-library-downloads-host="" />

        <div className="library-artists-grid" id="library-artists-grid">
          {artists.map((artist, i) => (
            <LibraryArtistCard
              key={artist.id}
              artist={artist}
              index={i}
              musicSource={window.currentMusicSourceName}
              href={`/artist-detail/library/${artist.id}`}
              onToggleWatch={() => watchArtist.mutate(artist)}
              watchPending={watching.has(String(artist.id))}
              onPlay={() => void playArtistTopTracks(artist)}
              playPending={playing.has(String(artist.id))}
            />
          ))}
        </div>

        {isEmpty ? <LibraryEmpty query={search.q} failed={failed} /> : null}

        {pagination.totalPages > 1 ? (
          <div className="library-pagination" id="library-pagination">
            <button
              type="button"
              className="pagination-btn"
              disabled={!pagination.hasPrev}
              onClick={() => setSearch({ page: Math.max(1, search.page - 1) })}
            >
              <span>← Previous</span>
            </button>
            <div className="pagination-info">
              <span>
                Page {pagination.page} of {pagination.totalPages}
              </span>
            </div>
            <button
              type="button"
              className="pagination-btn"
              disabled={!pagination.hasNext}
              onClick={() => setSearch({ page: search.page + 1 })}
            >
              <span>Next →</span>
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Empty state.
 *
 * With an active search that found nothing, the copy switches to a CTA that
 * hands the query to /search so the user can look the artist up across
 * metadata sources without retyping — the vanilla page rewrote the icon,
 * title, subtitle and button for exactly this case.
 *
 * DELIBERATE deviation: vanilla showed that same CTA after a FAILED load,
 * because its catch called showLibraryEmpty(true) and the empty state only
 * looked at the search box. "X isn't in your library" is a lie when the
 * library was never successfully read, so a failure keeps the generic copy.
 */
function LibraryEmpty({ query, failed }: { query: string; failed: boolean }) {
  const q = query.trim();
  const searching = q.length > 0 && !failed;

  return (
    <div className="library-empty">
      <div className="empty-icon">{searching ? '🔎' : '🎵'}</div>
      <div className="empty-title">
        {searching ? `"${q}" isn't in your library` : 'No artists found'}
      </div>
      <div className="empty-subtitle">
        {searching
          ? 'They might be available on a connected metadata source.'
          : 'Try adjusting your search or filters'}
      </div>
      {searching ? (
        <button
          type="button"
          className="library-empty-search-cta"
          onClick={() => window._handoffLibrarySearchToEnhancedSearch?.(q)}
        >
          <span className="library-empty-search-cta-icon">🔍</span>
          <span className="library-empty-search-cta-text">
            Search online for <span>&quot;{q}&quot;</span>
          </span>
          <span className="library-empty-search-cta-arrow">→</span>
        </button>
      ) : null}
    </div>
  );
}
