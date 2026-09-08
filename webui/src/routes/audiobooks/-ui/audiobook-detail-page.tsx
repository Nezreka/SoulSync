import { Link, useParams } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

import type { AudiobookItem } from '../-audiobooks.types';

import {
  fetchAudiobook,
  fetchByAuthor,
  fetchByNarrator,
  fetchSeries,
  fetchSimilarAudiobooks,
} from '../-audiobooks.api';
import { AudiobookBackButton } from './audiobook-back-button';
import { useAudiobookContext } from './audiobook-context';
import { AudiobookPersonLinks } from './audiobook-person-links';
import { AudiobookRail } from './audiobook-rail';
import { AudiobookRatingBreakdown } from './audiobook-rating';
import { AudiobookReleasesModal } from './audiobook-releases-modal';
import { AudiobookSeriesStrip } from './audiobook-series-strip';
import { AudiobookWishlistButton } from './audiobook-wishlist-button';
import styles from './audiobooks-page.module.css';

interface Related {
  series: AudiobookItem[];
  byAuthor: AudiobookItem[];
  byNarrator: AudiobookItem[];
  similar: AudiobookItem[];
}

const EMPTY_RELATED: Related = { series: [], byAuthor: [], byNarrator: [], similar: [] };

/**
 * The detail page.
 *
 * The book loads first and renders on its own; everything around it (series,
 * bibliography, narrator's other work, recommendations) fills in after. Each of
 * those is a separate catalogue call, and waiting for all four before painting
 * anything would make a fast page feel slow.
 */
export function AudiobookDetailPage() {
  const { asin } = useParams({ from: '/audiobooks/$asin' });
  const { playSample, isPlaying } = useAudiobookContext();

  const [book, setBook] = useState<AudiobookItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [related, setRelated] = useState<Related>(EMPTY_RELATED);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [releasesOpen, setReleasesOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setBook(null);
    setRelated(EMPTY_RELATED);
    setSummaryOpen(false);

    void (async () => {
      const found = await fetchAudiobook(asin);
      if (cancelled) return;
      setBook(found);
      setLoading(false);
      if (!found) return;

      const seriesEntry = found.series[0];
      const author = found.author_names[0];
      const narrator = found.narrator_names[0];

      const [series, byAuthor, byNarrator, similar] = await Promise.all([
        seriesEntry ? fetchSeries(seriesEntry.title, seriesEntry.asin, 30) : Promise.resolve([]),
        author ? fetchByAuthor(author, 20) : Promise.resolve([]),
        narrator ? fetchByNarrator(narrator, 20) : Promise.resolve([]),
        fetchSimilarAudiobooks(found.asin, 20),
      ]);
      if (cancelled) return;
      setRelated({
        series,
        // The book being read is already the subject of the page; leaving it in
        // its own "more by" rail is filler.
        byAuthor: byAuthor.filter((item) => item.asin !== found.asin),
        byNarrator: byNarrator.filter((item) => item.asin !== found.asin),
        similar: similar.filter((item) => item.asin !== found.asin),
      });
    })();

    return () => {
      cancelled = true;
    };
  }, [asin]);

  if (loading) {
    return (
      <div className={styles.detailPage}>
        <AudiobookBackButton />
        <div className={styles.detailSkeleton} />
      </div>
    );
  }

  if (!book) {
    return (
      <div className={styles.detailPage}>
        <AudiobookBackButton />
        <div className={styles.emptyState}>
          <h3>Not found</h3>
          <p>The catalogue has nothing for {asin}.</p>
          <Link to="/audiobooks" className={styles.heroSecondary}>
            Back to audiobooks
          </Link>
        </div>
      </div>
    );
  }

  const cover = book.cover_url_large || book.cover_url;
  const playing = isPlaying(book.asin);
  const seriesEntry = book.series[0];
  const summaryIsLong = book.summary.length > 420;
  const summaryText =
    summaryOpen || !summaryIsLong ? book.summary : `${book.summary.slice(0, 420).trimEnd()}…`;

  return (
    <div className={styles.detailPage}>
      {cover && (
        <div className={styles.detailBackdrop} style={{ backgroundImage: `url(${cover})` }} />
      )}
      <div className={styles.detailScrim} />

      <AudiobookBackButton />

      <div className={styles.detailTop}>
        <div className={styles.detailCoverWrap}>
          {cover ? (
            <img className={styles.detailCover} src={cover} alt={book.title} />
          ) : (
            <div className={`${styles.detailCover} ${styles.coverFallback}`} aria-hidden="true" />
          )}
          <AudiobookWishlistButton book={book} variant="full" />

          {book.source === 'audible' && (
            <button
              type="button"
              className={styles.wishlistFull}
              onClick={() => setReleasesOpen(true)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path
                  d="M12 3v12m0 0l-4.5-4.5M12 15l4.5-4.5M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.9"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              Find releases
            </button>
          )}

          {book.sample_url && (
            <button type="button" className={styles.detailPlay} onClick={() => playSample(book)}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                {playing ? (
                  <>
                    <rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" />
                    <rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" />
                  </>
                ) : (
                  <path d="M8 5v14l11-7z" fill="currentColor" />
                )}
              </svg>
              {playing ? 'Pause sample' : 'Play sample'}
            </button>
          )}
        </div>

        <div className={styles.detailInfo}>
          {book.genres.length > 0 && (
            <nav className={styles.breadcrumb} aria-label="Genre">
              {book.genres.slice(0, 3).map((genre, index) => (
                <span key={genre}>
                  {index > 0 && <span className={styles.breadcrumbSep}>›</span>}
                  {genre}
                </span>
              ))}
            </nav>
          )}

          <h1 className={styles.detailTitle}>{book.title}</h1>
          {book.subtitle && <p className={styles.detailSubtitle}>{book.subtitle}</p>}

          {seriesEntry && (
            <p className={styles.detailSeriesLine}>
              {seriesEntry.sequence ? `Book ${seriesEntry.sequence} of ` : 'Part of '}
              <strong>{seriesEntry.title}</strong>
            </p>
          )}

          <div className={styles.detailCredits}>
            {book.authors.length > 0 && (
              <div className={styles.creditRow}>
                <span className={styles.creditLabel}>Written by</span>
                <AudiobookPersonLinks
                  people={book.authors}
                  role="author"
                  className={styles.creditNames}
                />
              </div>
            )}
            {book.narrators.length > 0 && (
              <div className={styles.creditRow}>
                <span className={styles.creditLabel}>Narrated by</span>
                <AudiobookPersonLinks
                  people={book.narrators}
                  role="narrator"
                  className={styles.creditNames}
                />
              </div>
            )}
          </div>

          <div className={styles.detailFacts}>
            {book.runtime_formatted && <Fact label="Runtime" value={book.runtime_formatted} />}
            {book.format_type && <Fact label="Format" value={titleCase(book.format_type)} />}
            {book.release_date && <Fact label="Released" value={book.release_date} />}
            {book.publisher && <Fact label="Publisher" value={book.publisher} />}
            {book.language && <Fact label="Language" value={titleCase(book.language)} />}
          </div>

          {book.summary && (
            <div className={styles.detailSummary}>
              {summaryText.split('\n').map((para, index) => (
                <p key={index}>{para}</p>
              ))}
              {summaryIsLong && (
                <button
                  type="button"
                  className={styles.summaryToggle}
                  onClick={() => setSummaryOpen((open) => !open)}
                >
                  {summaryOpen ? 'Show less' : 'Show more'}
                </button>
              )}
            </div>
          )}

          <AudiobookRatingBreakdown rating={book.rating} />
        </div>
      </div>

      {seriesEntry && (
        <AudiobookSeriesStrip
          seriesTitle={seriesEntry.title}
          books={related.series}
          currentAsin={book.asin}
        />
      )}

      <AudiobookRail
        title={`More by ${book.author_names[0] ?? 'this author'}`}
        books={related.byAuthor}
      />
      <AudiobookRail
        title={`More narrated by ${book.narrator_names[0] ?? 'this narrator'}`}
        books={related.byNarrator}
      />
      <AudiobookRail title="Listeners also enjoyed" books={related.similar} />

      {releasesOpen && (
        <AudiobookReleasesModal
          asin={book.asin}
          title={book.title}
          onClose={() => setReleasesOpen(false)}
        />
      )}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.fact}>
      <span className={styles.factLabel}>{label}</span>
      <span className={styles.factValue}>{value}</span>
    </div>
  );
}

function titleCase(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}
