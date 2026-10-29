import { useQuery } from '@tanstack/react-query';
import { getRouteApi } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { useProfile } from '@/platform/shell/route-controllers';

import type { LibraryV2SongSort } from '../-library-v2.types';

import { fetchSongsToPlay, librarySongsQueryOptions, PLAY_ALL_LIMIT } from '../-library-songs.api';
import { readSongsResponse, shuffled, songQueueTrack } from '../-library-songs.helpers';
import {
  LibraryAlphabetBar,
  LibraryRadioButton,
  LibrarySearchInput,
  useSongsSearchFromRoute,
} from './library-index-extras';
import extras from './library-index-extras.module.css';
import { LibrarySongList, LibrarySongSkeleton } from './library-songs';
import styles from './library-v2-page.module.css';

/**
 * The library's Songs tab (#1906) on the Library v2 page: every song you
 * have a file for, searchable by title and artist, with the old page's
 * filters (Duplicates, Lossless, Could be better), sorts, alphabet bar,
 * Play all / Shuffle, and the songs list's own row play, likes, menu,
 * copies dialog, bulk select and delete.
 */

const Route = getRouteApi('/library');

const SEARCH_DEBOUNCE_MS = 300;

const SONG_SORT_LABELS: Record<LibraryV2SongSort, string> = {
  title: 'A–Z',
  artist: 'Artist',
  album: 'Album',
  year_desc: 'Newest first',
  recent: 'Recently added',
};

export function LibrarySongsSection({
  canWrite,
  toolbarLead,
}: {
  /** admin: may delete songs */
  canWrite: boolean;
  /** the page's scope badge and section tabs */
  toolbarLead?: ReactNode;
}) {
  const { profileId } = useProfile();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const songsSearch = useSongsSearchFromRoute();

  // the box is local so typing stays quick; the url catches up after the
  // debounce, replacing the entry so Back doesn't walk every keystroke
  const [draft, setDraft] = useState(search.q);
  const committed = useRef(search.q);
  useEffect(() => {
    if (search.q !== committed.current) {
      committed.current = search.q;
      setDraft(search.q);
    }
  }, [search.q]);
  useEffect(() => {
    if (draft === committed.current) return;
    const timer = window.setTimeout(
      () => {
        committed.current = draft;
        void navigate({ search: (prev) => ({ ...prev, q: draft, page: 1 }), replace: true });
      },
      draft ? SEARCH_DEBOUNCE_MS : 0,
    );
    return () => window.clearTimeout(timer);
  }, [draft, navigate]);

  const query = useQuery(librarySongsQueryOptions(profileId, songsSearch));
  const read = useMemo(() => {
    try {
      return { ...readSongsResponse(query.data), failed: false };
    } catch {
      return {
        songs: [],
        capped: false,
        failed: true,
        pagination: { page: 1, totalPages: 0, totalCount: 0, hasPrev: false, hasNext: false },
      };
    }
  }, [query.data]);
  const { songs, pagination, capped } = read;
  const failed = query.isError || read.failed;
  const q = search.q.trim();
  const filtered = Boolean(q) || search.letter !== 'all' || search.dupes || Boolean(search.quality);

  const setSearch = (patch: Partial<typeof search>) =>
    void navigate({ search: (prev) => ({ ...prev, ...patch, page: patch.page ?? 1 }) });

  // Play all / Shuffle: the first PLAY_ALL_LIMIT songs of what's shown
  const [queueing, setQueueing] = useState(false);
  const playSongs = async (shuffle: boolean) => {
    if (queueing) return;
    setQueueing(true);
    try {
      const list = (await fetchSongsToPlay(songsSearch)).filter((s) => s.file_path);
      if (!list.length) {
        window.showToast?.('Nothing here to play', 'info');
        return;
      }
      const ordered = shuffle ? shuffled(list) : list;
      await window.playTrackList?.(
        ordered.map(songQueueTrack),
        q ? `Songs matching "${q}"` : 'Your songs',
      );
      if (pagination.totalCount > PLAY_ALL_LIMIT || capped) {
        window.showToast?.(`Playing the first ${PLAY_ALL_LIMIT} songs`, 'info');
      }
    } catch {
      window.showToast?.("Couldn't load the songs to play", 'error');
    } finally {
      setQueueing(false);
    }
  };

  const count = `${pagination.totalCount.toLocaleString()}${capped ? '+' : ''}`;

  return (
    <div className={styles.page}>
      <header className={`${styles.header} library-header`}>
        <div>
          <h1 className={styles.title}>Library</h1>
          <p className={styles.subtitle}>
            {query.data ? (
              <>
                <span id="library-artist-count">{count}</span> {filtered ? 'matches' : 'songs'}
              </>
            ) : (
              'Browse your complete music collection'
            )}
          </p>
        </div>
        <div className={styles.headerActions}>
          <LibraryRadioButton />
          <button
            type="button"
            className={styles.btnGhost}
            disabled={queueing || songs.length === 0}
            title={`Play these songs in order (up to ${PLAY_ALL_LIMIT})`}
            onClick={() => void playSongs(false)}
          >
            ▶ Play all
          </button>
          <button
            type="button"
            className={styles.btnGhost}
            disabled={queueing || songs.length === 0}
            title={`Shuffle these songs (up to ${PLAY_ALL_LIMIT})`}
            onClick={() => void playSongs(true)}
          >
            🔀 Shuffle
          </button>
        </div>
      </header>

      <div className={styles.toolbar}>
        {toolbarLead}
        <LibrarySearchInput
          id="library-search-input"
          label="Filter songs"
          placeholder="Filter songs or artists…"
          value={draft}
          onChange={setDraft}
        />
        {/* toggles, so they combine */}
        <div className={styles.viewToggle} role="group" aria-label="Song filters">
          <button
            type="button"
            className={search.dupes ? styles.viewActive : ''}
            aria-pressed={search.dupes}
            title="Songs you have more than one copy of"
            onClick={() => setSearch({ dupes: !search.dupes })}
          >
            Duplicates
          </button>
          <button
            type="button"
            className={search.quality === 'lossless' ? styles.viewActive : ''}
            aria-pressed={search.quality === 'lossless'}
            title="FLAC, WAV, AIFF and other lossless files"
            onClick={() => setSearch({ quality: search.quality === 'lossless' ? '' : 'lossless' })}
          >
            Lossless
          </button>
          <button
            type="button"
            className={search.quality === 'upgradable' ? styles.viewActive : ''}
            aria-pressed={search.quality === 'upgradable'}
            title="Songs below their quality profile (from Library Maintenance)"
            onClick={() =>
              setSearch({ quality: search.quality === 'upgradable' ? '' : 'upgradable' })
            }
          >
            Could be better
          </button>
        </div>
        <select
          aria-label="Sort songs"
          className={styles.select}
          value={search.songSort}
          onChange={(e) => setSearch({ songSort: e.target.value as LibraryV2SongSort })}
        >
          {(Object.keys(SONG_SORT_LABELS) as LibraryV2SongSort[]).map((value) => (
            <option key={value} value={value}>
              {SONG_SORT_LABELS[value]}
            </option>
          ))}
        </select>
      </div>

      <LibraryAlphabetBar />

      {query.isPending ? (
        <LibrarySongSkeleton />
      ) : failed ? (
        <div className={styles.emptyState}>
          <h2>Could not load your songs</h2>
          <button type="button" onClick={() => void query.refetch()}>
            Try again
          </button>
        </div>
      ) : songs.length === 0 ? (
        <div className={styles.emptyState}>
          <h2>
            {q ? `No songs match "${q}"` : filtered ? 'No songs match this view' : 'No songs yet'}
          </h2>
          {q ? (
            <button
              type="button"
              className={extras.searchOnline}
              onClick={() => window._handoffLibrarySearchToEnhancedSearch?.(q)}
            >
              🔍 Search online for <span>&quot;{q}&quot;</span> →
            </button>
          ) : null}
        </div>
      ) : (
        <LibrarySongList
          songs={songs}
          isAdmin={canWrite}
          query={q}
          context={q ? `Songs matching "${q}"` : 'Your songs'}
          stale={query.isPlaceholderData}
          onChanged={() => void query.refetch()}
        />
      )}

      {pagination.totalPages > 1 || pagination.hasNext ? (
        <div className={styles.pagination} id="library-pagination">
          <button
            type="button"
            disabled={!pagination.hasPrev}
            onClick={() => setSearch({ page: Math.max(1, search.page - 1) })}
          >
            ←
          </button>
          <span>
            Page {pagination.page} of {pagination.totalPages}
            {capped ? '+' : ''}
          </span>
          <button
            type="button"
            disabled={!pagination.hasNext}
            onClick={() => setSearch({ page: search.page + 1 })}
          >
            →
          </button>
        </div>
      ) : null}
    </div>
  );
}
