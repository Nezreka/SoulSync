/**
 * The library-search overlay behind Swap and Find & add — serverSearchReplace
 * (pages-extra.js 746-816) and _serverSearchExecute (818-884).
 *
 * Slice C. The overlay only SEARCHES and reports the pick; the write, the
 * toast and the in-place patch belong to the compare editor, exactly as the
 * vanilla splits _serverSearchExecute from _serverSelectTrack.
 *
 * The vanilla appends this to <body>; here it renders inside the editor, which
 * is equivalent because `.server-search-overlay` is `position: fixed; inset: 0;
 * z-index: 10000` (style.css 61532-61543) — its DOM parent has no bearing on it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { CompareTrack, LibrarySearchTrack, ServerSearchMode } from '../-sync.server';

import {
  formatDurationMs,
  searchBitrateText,
  searchFormatBadge,
  searchLibraryTracks,
  searchModalTitle,
  searchResultsHeaderText,
  searchSeed,
} from '../-sync.server';

/**
 * 827 vs 832 vs 841-846 vs 857 vs 882 — the five bodies the results pane takes.
 *
 * DECLARED DIVERGENCE: the vanilla builds the overlay with a sixth body (a
 * magnifier over 'Searching...', 789-792) that no user ever sees. It is
 * overwritten by _serverSearchExecute's spinner synchronously in the same task,
 * before the browser paints. Reproducing it as a React state would introduce a
 * one-frame flicker the vanilla does not have, so the first phase is computed
 * to be whatever the immediate search sets.
 */
type SearchPhase =
  | { kind: 'empty-query' }
  | { kind: 'searching' }
  | { kind: 'no-results' }
  | { kind: 'results'; tracks: LibrarySearchTrack[] }
  | { kind: 'error'; message: string };

export interface ServerSearchOverlayProps {
  track: CompareTrack;
  mode: ServerSearchMode;
  onClose: () => void;
  /**
   * Resolves true when the write succeeded. A false keeps the overlay open and
   * restores the row's Select button (974), which is why this is awaited rather
   * than fired and forgotten.
   *
   * `resolvePicked` is a function, not a value, on purpose: the vanilla looks
   * the pick up in `_searchResults` AFTER the write returns (946), so a second
   * search landing while the write is in flight makes it miss — and that miss
   * is what its full-reload fallback exists for. Passing the clicked row
   * directly would silently delete that branch.
   */
  onSelect: (
    newTrackId: string,
    resolvePicked: () => LibrarySearchTrack | undefined,
  ) => Promise<boolean>;
}

export function ServerSearchOverlay({ track, mode, onClose, onSelect }: ServerSearchOverlayProps) {
  const seed = searchSeed(track);
  const [query, setQuery] = useState(seed.query);
  // 826/832: the same branch the mount search takes one tick later.
  const [phase, setPhase] = useState<SearchPhase>(() =>
    seed.query.trim() ? { kind: 'searching' } : { kind: 'empty-query' },
  );
  const [pendingId, setPendingId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // 811: the fade/scale only plays if `visible` lands on a LATER frame than the
  // element itself, which is what the vanilla's requestAnimationFrame buys.
  const [visible, setVisible] = useState(false);

  /**
   * The artist hint is captured when the overlay OPENS (759) and is not
   * refreshed as the user retypes, so a second search still ranks against the
   * original source artist. Reading it from the track prop is the same value.
   */
  const contextArtist = seed.contextArtist;

  /**
   * The vanilla's `_serverEditorState._searchResults` (854): written ONLY by a
   * successful, non-empty search, and never cleared — a failed search leaves
   * the previous list in place. Harmless, because a failed search also leaves
   * no row to click.
   */
  const resultsRef = useRef<LibrarySearchTrack[]>([]);

  const runId = useRef(0);
  const runSearch = useCallback(
    async (raw: string) => {
      const id = ++runId.current;
      const trimmed = raw.trim();
      if (!trimmed) {
        setPhase({ kind: 'empty-query' });
        return;
      }
      setPhase({ kind: 'searching' });
      try {
        const data = await searchLibraryTracks(trimmed, contextArtist);
        if (runId.current !== id) return;
        // 841: all three failures land on the same 'No results found' body —
        // an unsuccessful response is not distinguished from an empty one.
        if (!data.success || !data.tracks || data.tracks.length === 0) {
          setPhase({ kind: 'no-results' });
          return;
        }
        resultsRef.current = data.tracks;
        setPhase({ kind: 'results', tracks: data.tracks });
      } catch (error) {
        if (runId.current !== id) return;
        setPhase({
          kind: 'error',
          message: error instanceof Error ? error.message : 'unknown error',
        });
      }
    },
    [contextArtist],
  );

  // 810-815: appended, faded in, focused, text selected, then searched.
  useEffect(() => {
    const frame = requestAnimationFrame(() => setVisible(true));
    inputRef.current?.focus();
    inputRef.current?.select();
    void runSearch(seed.query);
    return () => {
      cancelAnimationFrame(frame);
      runId.current++;
    };
    // Mount only: the vanilla rebuilds the whole overlay for each open, so a
    // changed seed means a new overlay, never a re-seeded one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 798-804: Escape closes. The vanilla tears the listener down with a
  // MutationObserver watching for the overlay's removal; unmount is the same.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const select = async (searchTrack: LibrarySearchTrack) => {
    const newTrackId = String(searchTrack.id);
    setPendingId(newTrackId);
    // 946-947: resolved by id out of whatever the result list holds AT THAT
    // MOMENT — deliberately not the clicked row. Two rows sharing an id
    // therefore resolve to the first, as the vanilla's find() does.
    const ok = await onSelect(newTrackId, () =>
      resultsRef.current.find((row) => String(row.id) === newTrackId),
    );
    if (!ok) setPendingId(null);
  };

  return (
    <div
      id="server-search-overlay"
      className={`server-search-overlay${visible ? ' visible' : ''}`}
      // 797: the backdrop closes, but a click INSIDE the popover must not.
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      {/* The vanilla parks trackIndex and mode on this element's dataset (807-808)
          purely so _serverSearchExecute can read them back when it builds each
          row's onclick. Both are closure state here, so the attributes carry no
          information and are not emitted. */}
      <div className="server-search-popover" id="server-search-popover">
        <div className="server-search-header">
          <div>
            <div className="server-search-title">{searchModalTitle(mode)}</div>
            {/* 772: the context line is omitted entirely when neither side
                named the track. */}
            {seed.contextName ? (
              <div className="server-search-context">
                <span className="server-search-context-label">Source:</span>
                <span className="server-search-context-artist">{seed.contextArtist}</span>
                <span className="server-search-context-sep">—</span>
                <span className="server-search-context-name">{seed.contextName}</span>
              </div>
            ) : null}
          </div>
          <button type="button" className="server-search-close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="server-search-input-wrap">
          <div className="server-search-input-icon">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <circle cx="11" cy="11" r="8" />
              <path d="m21 21-4.35-4.35" />
            </svg>
          </div>
          <input
            type="text"
            className="server-search-input"
            id="server-search-input"
            ref={inputRef}
            value={query}
            placeholder="Search by track name, artist, or album..."
            onChange={(event) => setQuery(event.target.value)}
            // 785: Enter searches. There is no debounce — typing alone never
            // fires a request.
            onKeyDown={(event) => {
              if (event.key === 'Enter') void runSearch(query);
            }}
          />
        </div>

        {/* 855: the count header exists only for a result list. */}
        <div className="server-search-results-header" id="server-search-results-header">
          {phase.kind === 'results' ? searchResultsHeaderText(phase.tracks.length) : ''}
        </div>

        <div className="server-search-results" id="server-search-results">
          {phase.kind === 'empty-query' && (
            <div className="server-search-hint">Type a search query</div>
          )}
          {phase.kind === 'searching' && (
            <div className="server-search-hint">
              <div className="server-search-spinner" />
              Searching library...
            </div>
          )}
          {phase.kind === 'no-results' && (
            <div className="server-search-hint">
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                style={{ marginBottom: 6, opacity: 0.3 }}
              >
                <path d="M9.172 14.828a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <br />
              No results found
              <br />
              <span style={{ fontSize: 10, opacity: 0.5 }}>
                Try different keywords or a shorter query
              </span>
            </div>
          )}
          {phase.kind === 'error' && (
            <div className="server-search-hint">Error: {phase.message}</div>
          )}
          {phase.kind === 'results' &&
            phase.tracks.map((searchTrack, index) => {
              const id = String(searchTrack.id);
              const format = searchFormatBadge(searchTrack.file_path);
              const bitrate = searchBitrateText(searchTrack.bitrate);
              const duration = formatDurationMs(searchTrack.duration);
              const isPending = pendingId === id;
              return (
                <div
                  key={`${id}-${index}`}
                  className="server-search-result"
                  // 863: the rows cascade in — the delay is per-index inline.
                  style={{ animationDelay: `${index * 0.03}s` }}
                  onClick={() => void select(searchTrack)}
                >
                  <div className="server-search-result-art">
                    {searchTrack.album_thumb_url ? (
                      <img src={searchTrack.album_thumb_url} alt="" loading="lazy" />
                    ) : (
                      <div className="server-search-result-art-empty" />
                    )}
                  </div>
                  <div className="server-search-result-info">
                    <div className="server-search-result-title">{searchTrack.title}</div>
                    <div className="server-search-result-meta">
                      {searchTrack.artist_name}
                      {searchTrack.album_title ? ` · ${searchTrack.album_title}` : ''}
                    </div>
                  </div>
                  <div className="server-search-result-details">
                    {format ? <span className="server-search-format">{format}</span> : null}
                    {bitrate ? <span className="server-search-bitrate">{bitrate}</span> : null}
                    {duration ? <span className="server-search-dur">{duration}</span> : null}
                  </div>
                  <button type="button" className="server-search-select-btn" disabled={isPending}>
                    {isPending ? '...' : 'Select'}
                  </button>
                </div>
              );
            })}
        </div>
      </div>
    </div>
  );
}
