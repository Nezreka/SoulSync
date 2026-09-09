import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { useCallback, useEffect, useState } from 'react';

import type {
  AudiobookCategory,
  AudiobookHome,
  AudiobookItem,
  AudiobookSearchType,
  AudiobookSource,
} from '../-audiobooks.types';

import {
  fetchAudiobookHome,
  fetchBrowse,
  fetchCategories,
  searchAudiobooks,
} from '../-audiobooks.api';
import { AudiobookBackButton } from './audiobook-back-button';
import { AudiobookCard } from './audiobook-card';
import { useAudiobookContext } from './audiobook-context';
import { AudiobookGenreNav } from './audiobook-genre-nav';
import { AudiobookHero } from './audiobook-hero';
import { AudiobookLibraryModal } from './audiobook-library-modal';
import { AudiobookPeopleRow } from './audiobook-people-row';
import { AudiobookRail } from './audiobook-rail';
import { AudiobookReviewModal } from './audiobook-review-modal';
import { AudiobookSearchBar } from './audiobook-search-bar';
import styles from './audiobooks-page.module.css';

interface GenreView {
  name: string;
  bestsellers: AudiobookItem[];
  newest: AudiobookItem[];
}

/**
 * The audiobooks browse page.
 *
 * Three views share one shell — the default home shelves, a single genre, and
 * search results — and which one shows is decided entirely by the URL. That is
 * what makes Back out of a detail page land where the listener actually was,
 * and what makes a search worth sending to someone.
 *
 * The draft in the input box is the one piece of state that stays local: the URL
 * only changes on submit, so typing does not spam history with a dozen entries
 * Back would then have to walk through.
 */
export function AudiobooksBrowsePage() {
  const { wishlistCounts } = useAudiobookContext();
  const search = useSearch({ from: '/audiobooks/' });
  const navigate = useNavigate({ from: '/audiobooks/' });

  const activeQuery = search.q ?? '';
  const searchType: AudiobookSearchType = search.type ?? 'keywords';
  const activeGenre = search.genre ?? '';
  const mode = activeQuery ? 'search' : activeGenre ? 'genre' : 'home';

  const [showBlocklist, setShowBlocklist] = useState(false);
  const [showLibrary, setShowLibrary] = useState(false);
  const [draft, setDraft] = useState(activeQuery);

  const [home, setHome] = useState<AudiobookHome>({ hero: null, shelves: [] });
  const [homeLoading, setHomeLoading] = useState(true);
  const [categories, setCategories] = useState<AudiobookCategory[]>([]);

  const [genre, setGenre] = useState<GenreView | null>(null);
  const [genreLoading, setGenreLoading] = useState(false);

  const [results, setResults] = useState<AudiobookItem[]>([]);
  const [resultSource, setResultSource] = useState<AudiobookSource>('audible');
  const [searching, setSearching] = useState(false);

  // Keep the input in step when the URL changes underneath it (Back, a shared
  // link, clearing a search) without stomping on what the user is typing.
  useEffect(() => {
    setDraft(activeQuery);
  }, [activeQuery]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [homeData, categoryData] = await Promise.all([fetchAudiobookHome(), fetchCategories()]);
      if (cancelled) return;
      setHome(homeData);
      setCategories(categoryData);
      setHomeLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!activeQuery) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    void (async () => {
      const { results: found, source } = await searchAudiobooks(activeQuery, searchType, 40);
      if (cancelled) return;
      setResults(found);
      setResultSource(source);
      setSearching(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [activeQuery, searchType]);

  useEffect(() => {
    if (!activeGenre) {
      setGenre(null);
      return;
    }
    let cancelled = false;
    setGenreLoading(true);
    setGenre({ name: activeGenre, bestsellers: [], newest: [] });
    void (async () => {
      const [bestsellers, newest] = await Promise.all([
        fetchBrowse(activeGenre, 'bestsellers', 20),
        fetchBrowse(activeGenre, 'newest', 20),
      ]);
      if (cancelled) return;
      setGenre({ name: activeGenre, bestsellers, newest });
      setGenreLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [activeGenre]);

  const submitSearch = useCallback(() => {
    const trimmed = draft.trim();
    if (!trimmed) return;
    void navigate({ search: { q: trimmed, type: searchType }, resetScroll: false });
  }, [draft, navigate, searchType]);

  const changeSearchType = useCallback(
    (next: AudiobookSearchType) => {
      const trimmed = draft.trim();
      // Replace rather than push: flipping the mode is refining one search, not
      // a new place to come back to.
      void navigate({
        search: trimmed ? { q: trimmed, type: next } : { type: next },
        replace: true,
        resetScroll: false,
      });
    },
    [draft, navigate],
  );

  /**
   * Out of a search or a genre and back to the shelves.
   *
   * Deliberately not history.back(): refining a search leaves a trail of
   * intermediate queries, and walking back through them one at a time is not
   * what "back to browse" means. Clearing the search params goes straight there.
   */
  const backToBrowse = useCallback(() => {
    setDraft('');
    void navigate({ search: {}, resetScroll: false });
  }, [navigate]);

  const selectGenre = useCallback(
    (name: string) => {
      setDraft('');
      void navigate({ search: name ? { genre: name } : {}, resetScroll: false });
    },
    [navigate],
  );

  return (
    <div className={styles.browsePage}>
      <div className={styles.browseTopRow}>
        <AudiobookSearchBar
          query={draft}
          searchType={searchType}
          busy={searching}
          onQueryChange={setDraft}
          onSearchTypeChange={changeSearchType}
          onSubmit={submitSearch}
          onClear={backToBrowse}
        />

        <Link to="/wishlist" search={{ media: 'audiobooks' }} className={styles.wishlistLink}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M12 20.5l-1.4-1.3C5.4 14.5 2 11.4 2 7.6 2 4.9 4.1 3 6.7 3c1.5 0 3 .7 3.9 1.9L12 6.2l1.4-1.3C14.3 3.7 15.8 3 17.3 3 19.9 3 22 4.9 22 7.6c0 3.8-3.4 6.9-8.6 11.6z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
            />
          </svg>
          Wishlist
          {wishlistCounts.total > 0 && (
            <span className={styles.wishlistLinkCount}>{wishlistCounts.total}</span>
          )}
        </Link>

        {/* What you already have, beside what you want and what you refuse. */}
        <button
          type="button"
          className={styles.wishlistLink}
          onClick={() => setShowLibrary(true)}
          title="Everything downloaded and filed"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M4 4h5v16H4zM11 4h4v16h-4zM17 5l3 15"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
          Library
        </button>

        {/* Beside the wishlist because they are two halves of the same idea:
            what to fetch, and what never to fetch again. */}
        <button
          type="button"
          className={styles.wishlistLink}
          onClick={() => setShowBlocklist(true)}
          title="Deleted books you can still restore, and releases that will never be grabbed"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
            <path d="M5.6 5.6l12.8 12.8" stroke="currentColor" strokeWidth="1.8" />
          </svg>
          Bin
        </button>
      </div>

      {showLibrary && <AudiobookLibraryModal onClose={() => setShowLibrary(false)} />}
      {showBlocklist && <AudiobookReviewModal onClose={() => setShowBlocklist(false)} />}

      <AudiobookGenreNav categories={categories} active={activeGenre} onSelect={selectGenre} />

      {mode !== 'home' && <AudiobookBackButton onBack={backToBrowse} label="Back to browse" />}

      {mode === 'search' && (
        <section className={styles.resultsSection}>
          <header className={styles.resultsHeader}>
            <h2 className={styles.railTitle}>
              {searching
                ? 'Searching…'
                : `${results.length} result${results.length === 1 ? '' : 's'}`}
              <span className={styles.resultsQuery}> for “{activeQuery}”</span>
            </h2>
            {resultSource === 'apple' && !searching && results.length > 0 && (
              // Apple has no narrator, series or runtime. Saying so is better
              // than showing cards that look broken next to Audible ones.
              <span className={styles.sourceNote}>
                Audible had no match — showing Apple results, which carry less detail.
              </span>
            )}
          </header>

          {!searching && results.length > 0 && (
            <AudiobookPeopleRow
              results={results}
              roles={
                searchType === 'narrator'
                  ? ['narrator']
                  : searchType === 'author'
                    ? ['author']
                    : ['author', 'narrator']
              }
            />
          )}

          {searching ? (
            <SkeletonGrid />
          ) : results.length === 0 ? (
            <EmptyState
              title="Nothing matched"
              body={
                searchType === 'narrator'
                  ? 'Narrator search only covers the Audible catalogue. Try the narrator’s full name.'
                  : 'Try a different spelling, or switch the search mode above.'
              }
            />
          ) : (
            <div className={styles.grid}>
              {results.map((book) => (
                <AudiobookCard book={book} key={`${book.source}-${book.asin}`} />
              ))}
            </div>
          )}
        </section>
      )}

      {mode === 'genre' && (
        <>
          <h2 className={styles.genreHeading}>{activeGenre || 'Browse'}</h2>
          {genreLoading || !genre ? (
            <SkeletonGrid />
          ) : genre.bestsellers.length === 0 && genre.newest.length === 0 ? (
            <EmptyState
              title="This shelf is empty"
              body="Audible returned nothing for this genre right now."
            />
          ) : (
            <>
              <AudiobookRail title="Top sellers" books={genre.bestsellers} />
              <AudiobookRail title="New releases" books={genre.newest} />
            </>
          )}
        </>
      )}

      {mode === 'home' && (
        <>
          {homeLoading ? (
            <>
              <div className={styles.heroSkeleton} />
              <SkeletonGrid />
            </>
          ) : (
            <>
              {home.hero && <AudiobookHero book={home.hero} />}
              {home.shelves.map((shelf) => (
                <AudiobookRail
                  key={shelf.key}
                  title={shelf.title}
                  books={shelf.results}
                  onSeeAll={shelf.category ? () => selectGenre(shelf.category) : undefined}
                />
              ))}
              {!home.hero && home.shelves.every((shelf) => shelf.results.length === 0) && (
                <EmptyState
                  title="Couldn’t reach the catalogue"
                  body="The Audible catalogue didn’t answer. Check the server’s network access and reload."
                />
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div className={styles.grid} aria-hidden="true">
      {Array.from({ length: 12 }).map((_, index) => (
        <div className={styles.cardSkeleton} key={index} />
      ))}
    </div>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className={styles.emptyState}>
      <h3>{title}</h3>
      <p>{body}</p>
    </div>
  );
}
