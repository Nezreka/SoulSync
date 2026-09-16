/**
 * Deezer's curated playlists, as a Discover shelf.
 *
 * Deliberately built out of the page's existing card markup (`ya-card` and its
 * parts) rather than a new one: the art-forward card styling is scoped to
 * `.discover-section`, so a shelf that uses the same classes looks like the
 * rest of the page for free and cannot drift from it.
 *
 * Clicking a card does not start a second playlist pipeline. It hands the
 * playlist to the Sync page exactly as pasting its link would — see
 * openDeezerPlaylistInSync.
 */

import { useCallback, useEffect, useState } from 'react';

import {
  type DeezerEditorialGenre,
  type DeezerHandoffStage,
  type DeezerEditorialPlaylist,
  fetchDeezerEditorial,
  fetchDeezerEditorialGenres,
  openDeezerPlaylistInSync,
  searchDeezerPlaylists,
} from '../-discover.deezer-editorial';
import { DiscoverSection } from './discover-section';

/** Genre 0 is Deezer's everything chart, and the row the shelf opens on. */
const DEFAULT_GENRE = 0;

/** What the card says while it works. Silence is what made this feel broken. */
function handoffLabel(stage?: DeezerHandoffStage | null): string {
  if (!stage) return 'Starting…';
  if (stage.phase === 'mirroring') return 'Adding to Sync…';
  const { done, total } = stage;
  if (done != null && total) return `Loading ${done} / ${total} tracks…`;
  if (total) return `Loading ${total} tracks…`;
  return 'Loading tracks…';
}

/** 0-100. The load owns the bar; mirroring is quick and finishes it. */
function handoffPercent(stage?: DeezerHandoffStage | null): number {
  if (!stage) return 0;
  if (stage.phase === 'mirroring') return 100;
  const { done, total } = stage;
  if (done == null || !total) return 0;
  return Math.min(100, Math.round((done / total) * 100));
}

function PlaylistCard({
  playlist,
  onOpen,
  busy,
  stage,
}: {
  playlist: DeezerEditorialPlaylist;
  onOpen: (p: DeezerEditorialPlaylist) => void;
  busy?: boolean;
  stage?: DeezerHandoffStage | null;
}) {
  const [failed, setFailed] = useState(false);
  const tracks = playlist.track_count;
  return (
    <button
      type="button"
      className="ya-card discover-album-card"
      title={`${playlist.title} — ${playlist.creator}`}
      aria-label={`${playlist.title} by ${playlist.creator}, ${tracks} tracks`}
      onClick={() => void onOpen(playlist)}
      aria-busy={busy || undefined}
    >
      <div className="ya-card-img">
        {!failed && playlist.image_url && (
          <img
            src={playlist.image_url}
            alt=""
            loading="lazy"
            onError={() => setFailed(true)}
          />
        )}
        {(failed || !playlist.image_url) && <div className="ya-card-placeholder">♫</div>}
      </div>
      <div className="ya-card-gradient" />
      <div className="ya-card-info">
        <div className="ya-card-name">{playlist.title}</div>
        <div className="ya-card-sub">
          {busy
            ? handoffLabel(stage)
            : `${playlist.creator}${tracks ? ` · ${tracks} tracks` : ''}`}
        </div>
        {busy && (
          <div className="dz-ed-progress" role="progressbar" aria-label="Adding to Sync">
            <div className="dz-ed-progress-fill" style={{ width: `${handoffPercent(stage)}%` }} />
          </div>
        )}
      </div>
    </button>
  );
}

export function DeezerEditorialShelf({ onToast }: { onToast?: (message: string) => void }) {
  const [genres, setGenres] = useState<DeezerEditorialGenre[]>([]);
  const [genreId, setGenreId] = useState<number>(DEFAULT_GENRE);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [playlists, setPlaylists] = useState<DeezerEditorialPlaylist[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    void fetchDeezerEditorialGenres().then((g) => {
      if (live) setGenres(g);
    });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    const trimmed = query.trim();
    setSearching(trimmed.length > 0);
    // debounced, so typing does not fire a request per keystroke at Deezer
    const timer = window.setTimeout(
      () => {
        const request = trimmed ? searchDeezerPlaylists(trimmed) : fetchDeezerEditorial(genreId);
        void request.then((rows) => {
          // a genre or query switched away from mid-flight must not overwrite
          // whatever replaced it
          if (!live) return;
          setPlaylists(rows);
          setLoading(false);
        });
      },
      trimmed ? 350 : 0,
    );
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [genreId, query]);

  const [opening, setOpening] = useState<string | null>(null);
  const [stage, setStage] = useState<DeezerHandoffStage | null>(null);

  const open = useCallback(
    async (playlist: DeezerEditorialPlaylist) => {
      setOpening(playlist.id);
      setStage({ phase: 'loading', total: playlist.track_count || undefined });
      try {
        const error = await openDeezerPlaylistInSync(playlist, setStage);
        if (error) onToast?.(error);
      } finally {
        setOpening(null);
        setStage(null);
      }
    },
    [onToast],
  );

  return (
    <DiscoverSection
      id="deezer-editorial"
      title="From Deezer's editors"
      subtitle="Curated playlists, straight from Deezer. Pick one to match it against your library."
      count={playlists.length}
      loaded={!loading}
      actions={
        <div className="dz-ed-controls">
          <input
            type="search"
            className="dz-ed-search"
            value={query}
            placeholder="Search Deezer playlists…"
            aria-label="Search Deezer playlists"
            onChange={(event) => setQuery(event.target.value)}
          />
          {genres.length > 0 ? (
          <div className="dz-ed-genres" role="tablist" aria-label="Deezer genres">
            {genres.map((g) => (
              <button
                key={g.id}
                type="button"
                role="tab"
                aria-selected={!searching && g.id === genreId}
                className={`dz-ed-genre${!searching && g.id === genreId ? ' is-active' : ''}`}
                onClick={() => {
                  // picking a genre leaves a search; they are two ways of
                  // asking the same row a question, not two rows
                  setQuery('');
                  setGenreId(g.id);
                }}
              >
                {g.name}
              </button>
            ))}
            </div>
          ) : null}
        </div>
      }
    >
      {loading && playlists.length === 0 ? (
        <div className="discover-empty">
          <p>{searching ? 'Searching Deezer…' : 'Loading Deezer playlists…'}</p>
        </div>
      ) : playlists.length === 0 ? (
        <div className="discover-empty">
          <p>
            {searching
              ? `No Deezer playlists match “${query.trim()}”.`
              : 'Could not reach Deezer just now.'}
          </p>
        </div>
      ) : (
        <div className="discover-grid">
          {playlists.map((p) => (
            <PlaylistCard
              key={p.id}
              playlist={p}
              onOpen={open}
              busy={opening === p.id}
              stage={opening === p.id ? stage : null}
            />
          ))}
        </div>
      )}
    </DiscoverSection>
  );
}
