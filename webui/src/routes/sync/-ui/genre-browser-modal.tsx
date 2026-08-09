/**
 * The Browse-by-Genre modal — beatport-ui.js 2243-2340 and 2627-2681.
 *
 * The load is deliberately two-stage: the list renders at once with emoji
 * placeholders and the pictures arrive behind it, two at a time (see
 * -beatport.genres.ts). What this component adds is the shell around that:
 * open/close, the search box, and the FOUR states the grid can be in — loading,
 * loaded, "the API sent nothing", and "the request failed" — of which the last
 * two look alike and are not.
 *
 * Closing PAUSES the image loading rather than cancelling it (2327-2330), and
 * the genre list and the pictures fetched so far survive in the module cache,
 * so reopening is instant and only queues what is still missing.
 */

import type { CSSProperties, ReactNode } from 'react';

import { useCallback, useEffect, useState } from 'react';

import type { BeatportGenre } from '../-beatport.api';

import {
  beatportGenreKey,
  filterGenresBySearch,
  getCachedGenreImages,
  getCachedGenres,
  isGenreImageLoadingActive,
  loadBeatportGenreList,
  loadGenreImagesProgressively,
  pauseGenreImageLoading,
  shouldLoadGenreImages,
} from '../-beatport.genres';

/** 2372 and 2442 have no CSS class for this — the colour is inline. */
const MESSAGE_STYLE: CSSProperties = { color: 'rgba(255, 255, 255, 0.7)' };

/** 2373 / 2443, transcribed. The vanilla wires it with an inline onclick. */
const RETRY_STYLE: CSSProperties = {
  marginTop: '10px',
  padding: '10px 20px',
  border: '1px solid rgba(255, 255, 255, 0.3)',
  background: 'rgba(20, 20, 20, 0.8)',
  color: 'white',
  borderRadius: '8px',
  cursor: 'pointer',
};

const IMAGE_STYLE: CSSProperties = { width: '100%', height: '100%', objectFit: 'cover' };

type GridStatus = 'loading' | 'ready' | 'empty' | 'failed';

export interface GenreBrowserModalProps {
  open: boolean;
  onClose: () => void;
  /** Opening a genre replaces the modal with the genre page (2659-2680). */
  onSelectGenre: (genre: BeatportGenre) => void;
  /**
   * The genre page, when one is open. Rendered INSIDE
   * `.genre-browser-modal-content` in place of the search box and the grid —
   * which is exactly what the vanilla does (2696-2745): it hides
   * `.genre-browser-search-section` and `.genre-browser-genres-section` and
   * appends `.genre-page-content` alongside them. The overlay, container and
   * header stay, so the close button and the backdrop keep working.
   *
   * A SLOT rather than the caller re-creating the modal chrome: this component
   * owns that markup, and a second copy would be a second thing to keep in
   * step with the stylesheet.
   */
  genreView?: ReactNode;
}

export function GenreBrowserModal({
  open,
  onClose,
  onSelectGenre,
  genreView,
}: GenreBrowserModalProps) {
  const [genres, setGenres] = useState<BeatportGenre[] | null>(getCachedGenres);
  const [images, setImages] = useState<Map<string, string>>(getCachedGenreImages);
  const [status, setStatus] = useState<GridStatus>(() =>
    (getCachedGenres()?.length ?? 0) > 0 ? 'ready' : 'loading',
  );
  const [errorMessage, setErrorMessage] = useState('');
  const [search, setSearch] = useState('');
  const [reloadToken, setReloadToken] = useState(0);

  const startImages = useCallback((list: BeatportGenre[]) => {
    // 2433: only past the threshold.
    //
    // 2491's `!imageLoadingActive` is also transcribed, and the mutation pass
    // showed it makes no difference TODAY: the only way back into this effect
    // is `open` changing (and closing pauses) or `reloadToken` changing, and
    // the retry that bumps that token only exists in the empty and failed
    // states, where no image run can be in flight. Kept because it is the
    // vanilla's guard and because it is what stops a future second caller from
    // doubling the worker count.
    if (!shouldLoadGenreImages(list.length) || isGenreImageLoadingActive()) return;
    void loadGenreImagesProgressively(list, (key, url) => {
      setImages((current) => new Map(current).set(key, url));
    });
  }, []);

  useEffect(() => {
    if (!open) return;

    // 2298: a cached list is shown instantly and NOT re-fetched. The images
    // resume from wherever the last close paused them.
    const cached = getCachedGenres();
    if (cached && cached.length > 0 && reloadToken === 0) {
      setGenres(cached);
      setImages(getCachedGenreImages());
      setStatus('ready');
      startImages(cached);
      return;
    }

    let cancelled = false;
    setStatus('loading');
    void (async () => {
      try {
        const { genres: loaded, rawCount } = await loadBeatportGenreList();
        if (cancelled) return;
        if (rawCount === 0) {
          // The retryable empty state — the API sent nothing at all.
          setStatus('empty');
          return;
        }
        setGenres(loaded);
        setImages(getCachedGenreImages());
        setStatus('ready');
        // 2430. Fires with 0 when the filter removed everything, which is the
        // vanilla's other empty state and is NOT the retryable one.
        window.showToast?.(`Loaded ${loaded.length} genres for browsing`, 'success');
        startImages(loaded);
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : String(error);
        setStatus('failed');
        setErrorMessage(message);
        window.showToast?.(`Error loading genres: ${message}`, 'error');
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, reloadToken, startImages]);

  // 2327-2330: closing PAUSES the workers. They are not cancelled and their
  // progress is kept, so the next open picks up the remaining genres.
  useEffect(() => {
    if (open) return;
    pauseGenreImageLoading();
    // 2319-2323: the search box is cleared on close, but the data is not.
    setSearch('');
  }, [open]);

  // 2295 / 2316: the page behind the modal must not scroll.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  // 2280-2284.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const visible = filterGenresBySearch(genres ?? [], search);

  return (
    <div
      className="genre-browser-modal-overlay active"
      id="genre-browser-modal"
      // 2264-2268: the OVERLAY closes, the container does not — so a click on
      // the modal body must not bubble out as a dismiss.
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="genre-browser-modal-container">
        <div className="genre-browser-modal-header">
          <h2 className="genre-browser-modal-title">🎵 Browse by Genre</h2>
          <button type="button" className="genre-browser-modal-close" onClick={onClose}>
            <span className="genre-browser-close-icon">×</span>
          </button>
        </div>

        <div className="genre-browser-modal-content">
          {/* 2696-2699: with a genre open the search box and the grid are
              HIDDEN, not unmounted — but nothing reads their state while they
              are hidden, and the genre list itself lives in the module cache,
              so rendering one or the other is the same behaviour without
              keeping a dead subtree around. */}
          {genreView ?? (
            <>
              <div className="genre-browser-search-section">
                <div className="genre-browser-search-container">
                  <input
                    type="text"
                    className="genre-browser-search-input"
                    placeholder="Search genres..."
                    id="genre-browser-search"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                  <span className="genre-browser-search-icon">🔍</span>
                </div>
              </div>

              <div className="genre-browser-genres-section">
                <div className="genre-browser-genres-grid" id="genre-browser-genres-grid">
                  {status === 'loading' ? (
                    <div className="genre-browser-loading-container">
                      <div className="genre-browser-loading-spinner" />
                      <p className="genre-browser-loading-text">
                        🔍 Discovering current Beatport genres...
                      </p>
                    </div>
                  ) : null}

                  {status === 'empty' ? (
                    <div className="genre-browser-loading-container">
                      <p style={MESSAGE_STYLE}>⚠️ No genres available</p>
                      <RetryButton onClick={() => setReloadToken((token) => token + 1)} />
                    </div>
                  ) : null}

                  {status === 'failed' ? (
                    <div className="genre-browser-loading-container">
                      <p style={MESSAGE_STYLE}>❌ Failed to load genres: {errorMessage}</p>
                      <RetryButton onClick={() => setReloadToken((token) => token + 1)} />
                    </div>
                  ) : null}

                  {status === 'ready'
                    ? visible.map((genre) => (
                        <GenreCard
                          key={beatportGenreKey(genre)}
                          genre={genre}
                          imageUrl={images.get(beatportGenreKey(genre))}
                          onClick={() => onSelectGenre(genre)}
                        />
                      ))
                    : null}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" style={RETRY_STYLE} onClick={onClick}>
      🔄 Retry
    </button>
  );
}

/**
 * 2404-2416. The `genre-browser-card-fallback` class is what styles the emoji
 * tile, and the vanilla REMOVES it the moment a picture lands (2519, 2569) —
 * so it is bound to the image's presence rather than set once.
 */
function GenreCard({
  genre,
  imageUrl,
  onClick,
}: {
  genre: BeatportGenre;
  imageUrl: string | undefined;
  onClick: () => void;
}) {
  return (
    <div
      className={`genre-browser-card${imageUrl ? '' : ' genre-browser-card-fallback'}`}
      data-genre-slug={genre.slug}
      data-genre-id={String(genre.id)}
      data-genre-name={genre.name}
      data-url={genre.url ?? ''}
      onClick={onClick}
    >
      <div className="genre-browser-card-image">
        {imageUrl ? (
          <img src={imageUrl} alt={genre.name} loading="lazy" style={IMAGE_STYLE} />
        ) : (
          '🎵'
        )}
      </div>
      <div className="genre-browser-card-content">
        <h3 className="genre-browser-card-title">{genre.name}</h3>
        <p className="genre-browser-card-subtitle">Top 10 &amp; Top 100 Charts</p>
      </div>
    </div>
  );
}
