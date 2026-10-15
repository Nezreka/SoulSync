/**
 * "add songs" inside one of your playlists: search, see what you've been
 * playing, and add with one tap, without leaving the playlist.
 *
 * the search is the search page's own (/api/enhanced-search, every source).
 * before you type it shows your recent plays, the songs you're most likely to
 * be reaching for. a row the playlist already has says so instead of offering
 * a second copy.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import { thumb } from '@/platform/artwork-thumb';
import { fetchEnhancedSearch } from '@/routes/search/-search.api';

import type { SongCandidate } from './add-songs';

import { candidatesFromRecent, candidatesFromSearch, songDuration, songKey } from './add-songs';
import { addToUserPlaylist } from './user-playlists';

export interface AddSongsPanelProps {
  playlistId: number;
  playlistName: string;
  /** what's in it now, for the rows' "in playlist" state */
  existing: readonly { track_name?: string; artist_name?: string }[];
  /** something was added: the playlist refetches */
  onAdded: () => void;
  onClose: () => void;
}

const SEARCH_DEBOUNCE_MS = 350;

type RowState = 'idle' | 'adding' | 'added' | 'in';

function SongRow({
  song,
  state,
  onAdd,
}: {
  song: SongCandidate;
  state: RowState;
  onAdd: () => void;
}) {
  const done = state === 'added' || state === 'in';
  return (
    <div className={`as-row${done ? ' as-row--done' : ''}`}>
      {song.image_url ? (
        <img className="as-art" src={thumb(song.image_url, 'card')} alt="" loading="lazy" />
      ) : (
        <span className="as-art as-art--blank" aria-hidden="true">
          ♪
        </span>
      )}
      <span className="as-text">
        <span className="as-title">{song.track_name}</span>
        <span className="as-sub">
          {song.artist_name}
          {song.album_name ? ` · ${song.album_name}` : ''}
        </span>
      </span>
      <span className="as-dur">{songDuration(song.duration_ms)}</span>
      <button
        type="button"
        className={`as-add as-add--${state}`}
        disabled={state !== 'idle'}
        aria-label={
          done
            ? `${song.track_name} is in this playlist`
            : `Add ${song.track_name} to this playlist`
        }
        onClick={onAdd}
      >
        {state === 'adding' ? (
          <span className="as-spin" aria-hidden="true" />
        ) : done ? (
          <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
            <path
              d="m5 12.5 4.2 4.2L19 7"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        ) : null}
        {state === 'adding'
          ? null
          : state === 'added'
            ? 'Added'
            : state === 'in'
              ? 'In playlist'
              : 'Add'}
      </button>
    </div>
  );
}

export function AddSongsPanel({
  playlistId,
  playlistName,
  existing,
  onAdded,
  onClose,
}: AddSongsPanelProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SongCandidate[] | null>(null);
  const [recent, setRecent] = useState<SongCandidate[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [failed, setFailed] = useState(false);
  // added in this panel, by key: the refetch confirms them, this paints them now
  const [added, setAdded] = useState<Record<string, RowState>>({});
  const inputRef = useRef<HTMLInputElement>(null);

  const have = useMemo(
    () => new Set(existing.map((t) => songKey(t.track_name ?? '', t.artist_name ?? ''))),
    [existing],
  );

  useEffect(() => {
    inputRef.current?.focus();
    let live = true;
    fetch('/api/stats/recent?limit=50')
      .then((r) => r.json())
      .then((data: { success?: boolean; tracks?: unknown[] }) => {
        if (live) setRecent(data?.success ? candidatesFromRecent(data.tracks as never) : []);
      })
      .catch(() => live && setRecent([]));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults(null);
      setSearching(false);
      setFailed(false);
      return;
    }
    const controller = new AbortController();
    setSearching(true);
    const timer = setTimeout(() => {
      fetchEnhancedSearch(q, 'auto', controller.signal)
        .then((data) => {
          setResults(candidatesFromSearch(data.spotify_tracks));
          setFailed(false);
        })
        .catch((err: unknown) => {
          if ((err as { name?: string })?.name === 'AbortError') return;
          setFailed(true);
          setResults([]);
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const stateOf = (song: SongCandidate): RowState =>
    added[song.key] ?? (have.has(song.key) ? 'in' : 'idle');

  const add = async (song: SongCandidate) => {
    setAdded((m) => ({ ...m, [song.key]: 'adding' }));
    try {
      const { key: _key, ...track } = song;
      const result = await addToUserPlaylist(playlistId, [track]);
      // the server's looser match found it already there
      setAdded((m) => ({ ...m, [song.key]: result.added > 0 ? 'added' : 'in' }));
      if (result.added > 0) onAdded();
    } catch (err) {
      setAdded((m) => {
        const next = { ...m };
        delete next[song.key];
        return next;
      });
      window.showToast?.(err instanceof Error ? err.message : 'Could not add that song', 'error');
    }
  };

  const showing = query.trim() ? results : recent;
  const heading = query.trim() ? 'Results' : 'From your recent plays';

  return (
    <section className="as-panel" aria-label={`Add songs to ${playlistName}`}>
      <div className="as-head">
        <div>
          <h3 className="as-heading">Let’s find something for {playlistName}</h3>
          <p className="as-lede">Search every source, or pick from what you’ve been playing.</p>
        </div>
        <button type="button" className="as-close" aria-label="Close add songs" onClick={onClose}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path
              d="M6 6l12 12M18 6 6 18"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
      <label className="as-search">
        <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
          <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2.2" />
          <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        </svg>
        <input
          ref={inputRef}
          type="search"
          className="as-input"
          placeholder="Search for songs"
          aria-label="Search for songs"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            // escape clears the box first, a second one closes the panel
            if (e.key === 'Escape' && query) {
              e.stopPropagation();
              setQuery('');
            }
          }}
        />
        {searching ? <span className="as-spin as-spin--search" aria-hidden="true" /> : null}
      </label>

      <div className="as-section">{heading}</div>
      <div className="as-list" aria-busy={searching || showing === null || undefined}>
        {showing === null || (searching && !results?.length) ? (
          Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="as-row as-row--skeleton" aria-hidden="true">
              <span className="as-art" />
              <span className="as-text">
                <span className="as-bar" />
                <span className="as-bar as-bar--short" />
              </span>
            </div>
          ))
        ) : failed ? (
          <div className="as-note">The search didn’t come back. Try again in a moment.</div>
        ) : showing.length === 0 ? (
          <div className="as-note">
            {query.trim()
              ? `Nothing found for “${query.trim()}”.`
              : 'Search above to find songs to add.'}
          </div>
        ) : (
          showing.map((song) => (
            <SongRow
              key={song.key}
              song={song}
              state={stateOf(song)}
              onAdd={() => void add(song)}
            />
          ))
        )}
      </div>
    </section>
  );
}
