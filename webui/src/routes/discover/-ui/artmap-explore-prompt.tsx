import { useEffect, useRef, useState } from 'react';

/**
 * The Artist Explorer's search prompt — pick a REAL artist before exploring.
 *
 * Transcribed from `_showArtistMapSearchPrompt` (discover.js 9740-9843).
 * The vanilla resolves a Promise with the chosen artist's resolved name and
 * refuses raw text outright: Enter picks the top match, never what was typed,
 * because /artist-map/explore wants a resolvable artist, not a guess.
 */

type PromptState =
  | { kind: 'idle' }
  | { kind: 'searching' }
  | { kind: 'failed' }
  | { kind: 'results'; artists: { name: string; image_url?: string }[] };

/** `setTimeout(..., 350)` (9817). */
export const EXPLORE_PROMPT_DEBOUNCE_MS = 350;

export function ArtMapExplorePrompt({
  onPick,
  onClose,
}: {
  onPick: (name: string) => void;
  onClose: () => void;
}) {
  const [state, setState] = useState<PromptState>({ kind: 'idle' });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const token = useRef(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 50);
    return () => {
      clearTimeout(t);
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const search = (raw: string) => {
    if (timer.current) clearTimeout(timer.current);
    const q = raw.trim();
    if (!q) {
      setState({ kind: 'idle' });
      return;
    }
    timer.current = setTimeout(async () => {
      const myToken = ++token.current;
      setState({ kind: 'searching' });
      try {
        // The same source search the playlist builder uses (9819).
        const res = await fetch(
          `/api/discover/build-playlist/search-artists?query=${encodeURIComponent(q)}`,
        );
        const data = (await res.json()) as {
          success?: boolean;
          artists?: { name: string; image_url?: string }[];
        };
        if (token.current !== myToken) return; // a newer keystroke superseded this
        setState({
          kind: 'results',
          artists: data?.success && Array.isArray(data.artists) ? data.artists : [],
        });
      } catch {
        if (token.current === myToken) setState({ kind: 'failed' });
      }
    }, EXPLORE_PROMPT_DEBOUNCE_MS);
  };

  const artists = state.kind === 'results' ? state.artists : [];

  return (
    <div
      id="artmap-search-prompt"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="artmap-search-prompt-modal">
        <div className="artmap-search-prompt-header">
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
            <line x1="11" y1="8" x2="11" y2="14" />
            <line x1="8" y1="11" x2="14" y2="11" />
          </svg>
          <div>
            <h3>Artist Explorer</h3>
            <p>Search and pick an artist to explore</p>
          </div>
        </div>
        <div className="artmap-explore-search-wrap">
          <input
            ref={inputRef}
            type="text"
            id="artmap-explore-input"
            className="artmap-explore-input"
            placeholder="Search artists…"
            autoComplete="off"
            onChange={(e) => search(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                // Enter = pick the top MATCH, never raw text (9838).
                if (artists.length) onPick(artists[0].name);
              } else if (e.key === 'Escape') {
                onClose();
              }
            }}
          />
          {state.kind === 'searching' && (
            <div className="artmap-explore-spinner" id="artmap-explore-spinner">
              <div className="watch-all-loading-spinner" />
            </div>
          )}
        </div>
        <div className="artmap-explore-results" id="artmap-explore-results">
          {state.kind === 'failed' ? (
            <div className="artmap-explore-empty">Search failed — try again</div>
          ) : state.kind === 'results' && artists.length === 0 ? (
            <div className="artmap-explore-empty">No artists found</div>
          ) : (
            artists.map((a) => (
              <button
                type="button"
                key={a.name}
                className="artmap-explore-result"
                onClick={() => onPick(a.name)}
              >
                <img
                  src={a.image_url || '/static/placeholder-album.png'}
                  alt=""
                  loading="lazy"
                  onError={(e) => {
                    e.currentTarget.src = '/static/placeholder-album.png';
                  }}
                />
                <span className="artmap-explore-result-name">{a.name}</span>
                <span className="artmap-explore-result-go">Explore →</span>
              </button>
            ))
          )}
        </div>
        <div className="artmap-search-prompt-actions">
          <button
            type="button"
            className="btn btn--sm btn--secondary ya-header-btn"
            onClick={onClose}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
