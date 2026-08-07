/**
 * The genre page — beatport-ui.js 2683-2809 (the shell) and 2811-3118 (the
 * hero slider).
 *
 * It lives INSIDE the browse-by-genre modal: opening a genre hides the search
 * box and the grid and appends this in their place (2696-2742), and Back
 * reverses that. The port renders one or the other instead of toggling
 * `display`, which is the same thing without the hidden-DOM-as-state dance.
 *
 * THE HERO IS THE MAIN HERO'S TWIN. It emits the same `beatport-rebuild-*`
 * classes, one release per slide, 5000ms — so it is BeatportSlider with the
 * hero config and its own ids, and all of updateGenreHeroSlide,
 * startGenreHeroSliderAutoPlay and addGenreHeroReleaseClickHandlers dissolve.
 * With them goes `window.genreHeroSliderState`, which the read flagged as the
 * file's only window-scoped state.
 *
 * NOT TRANSCRIBED, deliberately: showGenrePageView stops the MAIN hero's
 * autoplay on the way in (2687-2690) and showGenreListView restarts it
 * (2791-2796). Both exist because the two sliders drove overlapping global
 * state through shared functions — the comment says "to prevent conflicts".
 * In the port they are two component instances with their own intervals and no
 * shared anything, so there is no conflict to prevent; and the modal covers the
 * page, so the main hero advancing behind it is invisible either way.
 */

import { useCallback, useEffect, useState } from 'react';

import type { BeatportGenre, BeatportRelease } from '../-beatport.api';
import type { BeatportDownloadEnv } from '../-beatport.downloads';

import { BEATPORT_SLIDERS } from '../-beatport.core';
import { openBeatportRelease } from '../-beatport.downloads';
import {
  genreHeroAlbumLine,
  genreHeroClickRelease,
  isBeatportReleaseClickable,
  loadGenreHero,
} from '../-beatport.loaders';
import { heroSlideAttributes } from './beatport-cards';
import { BeatportSlider } from './beatport-slider';

export interface GenrePageProps {
  genre: BeatportGenre;
  onBack: () => void;
  env: BeatportDownloadEnv;
}

export function GenrePage({ genre, onBack, env }: GenrePageProps) {
  return (
    <div className="genre-page-content">
      <div className="genre-page-header">
        <button type="button" className="genre-back-button" id="genre-back-button" onClick={onBack}>
          <span className="back-icon">←</span> Back to Genres
        </button>
        <h2 className="genre-page-title">{genre.name}</h2>
      </div>

      <GenreHeroSlider genre={genre} env={env} />

      <div className="genre-nav-buttons-section">
        <div className="genre-nav-buttons-container">
          {/*
            2757-2766. The vanilla builds this block ONCE and reuses the element
            for every genre thereafter, so its listener had to read the genre off
            the dataset rather than close over the arguments — closing over them
            pinned the button to whichever genre was opened first and downloaded
            THAT genre's Top 100 (fixed in 93eaa90ac). React remounts per genre,
            so the closure is the current genre by construction and the whole
            class of bug is gone.

            The handler itself arrives with the genre Top 100 slice.
          */}
          <button type="button" className="beatport-nav-button" id="genre-top100-btn">
            <span className="beatport-nav-icon top100-icon" />
            <span className="beatport-nav-text">Beatport Top 100</span>
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── The hero (2811-3118) ─────────────────────────────────────────────────── */

type HeroStatus = 'loading' | 'ready' | 'failed';

function GenreHeroSlider({ genre, env }: { genre: BeatportGenre; env: BeatportDownloadEnv }) {
  const [releases, setReleases] = useState<BeatportRelease[]>([]);
  const [status, setStatus] = useState<HeroStatus>('loading');
  const [errorMessage, setErrorMessage] = useState('');
  const [reloadToken, setReloadToken] = useState(0);

  const retry = useCallback(() => setReloadToken((token) => token + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setStatus('loading');
    void (async () => {
      try {
        const loaded = await loadGenreHero(genre.slug, genre.id, controller.signal);
        if (controller.signal.aborted) return;
        setReleases(loaded);
        setStatus('ready');
        // 2847. The count comes from the array, not from `data.count` — the two
        // agree, and reading the array cannot disagree with what is rendered.
        window.showToast?.(`Loaded ${loaded.length} ${genre.name} releases`, 'success');
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof Error && error.name === 'AbortError') return;
        setStatus('failed');
        setErrorMessage(error instanceof Error ? error.message : String(error));
      }
    })();
    return () => controller.abort();
    // The genre is the identity here; a different genre is a different load.
  }, [genre.slug, genre.id, genre.name, reloadToken]);

  if (status === 'loading') {
    return (
      <div className="genre-hero-slider-container" id="genre-hero-slider-container">
        <div className="genre-loading-container">
          <div className="genre-loading-spinner" />
          {/* 2822 names the genre; the block built at 2717 does not. This is the
              one a user actually sees, since the load starts immediately. */}
          <p className="genre-loading-text">🎠 Loading {genre.name} hero releases...</p>
        </div>
      </div>
    );
  }

  if (status === 'failed') {
    return (
      <div className="genre-hero-slider-container" id="genre-hero-slider-container">
        <div className="genre-error-container">
          <p className="genre-error-text">❌ Failed to load {genre.name} releases</p>
          <p className="genre-error-details">{errorMessage}</p>
          <button type="button" className="genre-retry-button" onClick={retry}>
            🔄 Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="genre-hero-slider-container" id="genre-hero-slider-container">
      <BeatportSlider<BeatportRelease>
        config={BEATPORT_SLIDERS.hero}
        items={releases}
        trackId="genre-hero-slider-track"
        prevButtonId="genre-hero-prev-btn"
        nextButtonId="genre-hero-next-btn"
        slideAttributes={(release) => {
          const clickable = genreHeroClickRelease(release);
          const attributes = heroSlideAttributes({
            url: clickable.url,
            image_url: release.image_url,
          });
          if (!isBeatportReleaseClickable(clickable.url)) return attributes;
          return {
            ...attributes,
            style: { ...attributes.style, cursor: 'pointer' },
            onClick: () => {
              void openBeatportRelease(clickable, env);
            },
          };
        }}
        renderItem={(release, index) => (
          <GenreHeroSlide key={index} release={release} genreName={genre.name} />
        )}
      />
    </div>
  );
}

/**
 * 2879-2888. Same three lines as the main hero, but the third is the LABEL
 * (falling back to '<Genre> Hero Release'), where the main hero's is the fixed
 * caption 'New on Beatport'. And the artist is `artists_string`.
 */
function GenreHeroSlide({ release, genreName }: { release: BeatportRelease; genreName: string }) {
  return (
    <>
      <div className="beatport-rebuild-slide-background">
        <div className="beatport-rebuild-slide-gradient" />
      </div>
      <div className="beatport-rebuild-slide-content">
        <div className="beatport-rebuild-track-info">
          <h2 className="beatport-rebuild-track-title">{release.title}</h2>
          <p className="beatport-rebuild-artist-name">{release.artists_string}</p>
          <p className="beatport-rebuild-album-name">{genreHeroAlbumLine(release, genreName)}</p>
        </div>
      </div>
    </>
  );
}
