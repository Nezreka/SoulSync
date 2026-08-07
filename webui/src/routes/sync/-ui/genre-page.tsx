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
 * ONE DECLARED DIVERGENCE, and it is the reason this file has a visible error
 * block at all. loadGenreHeroSlider renders its error block AND RETHROWS
 * (2862) — alone among the genre page's three loaders, which both swallow.
 * handleGenreBrowserCardClick runs all three in a Promise.all, so that throw
 * rejects the lot, toasts, and calls showGenreListView(): the user is bounced
 * back to the genre grid and NEVER SEES the block that was just rendered. Its
 * Retry button is dead UI — and would be broken anyway, since it is an inline
 * `onclick` with the genre name string-interpolated into it.
 *
 * The port keeps the user on the genre page and makes the block real, with a
 * working Retry. Losing the whole page because one of three sections failed —
 * while the other two would have loaded fine — is not worth reproducing, and
 * the markup shows what was intended. Declared rather than done quietly.
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
import type { GenreTop10Lists as GenreTop10Data } from '../-beatport.loaders';

import { BEATPORT_SLIDERS } from '../-beatport.core';
import { openBeatportGenreTop100, openBeatportRelease } from '../-beatport.downloads';
import {
  genreHeroAlbumLine,
  genreHeroClickRelease,
  isBeatportReleaseClickable,
  loadGenreHero,
  loadGenreTop10Lists,
  loadGenreTop10Releases,
} from '../-beatport.loaders';
import { heroSlideAttributes } from './beatport-cards';
import { BeatportSlider } from './beatport-slider';
import { ReleaseTop10Card, TrackTop10List } from './beatport-top10';

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
          <button
            type="button"
            className="beatport-nav-button"
            id="genre-top100-btn"
            onClick={() => {
              void openBeatportGenreTop100(genre.slug, genre.id, genre.name, env);
            }}
          >
            <span className="beatport-nav-icon top100-icon" />
            <span className="beatport-nav-text">Beatport Top 100</span>
          </button>
        </div>
      </div>

      <GenreTop10Lists genre={genre} env={env} />
      <GenreTop10Releases genre={genre} env={env} />
    </div>
  );
}

/* ── The genre top-10 lists (3123-3296) ───────────────────────────────────── */

/**
 * The same two lists as the homepage — same classes, same cards, same
 * container-level click — differing only in element id, subtitle and the name
 * the download is filed under. So this reuses TrackTop10List rather than
 * restating the card markup, and overrides exactly those three things.
 *
 * The genre names in the copy are LOWER-CASED (3176, 3184, 3225) while the
 * section heading keeps the original casing (3175).
 */
function GenreTop10Lists({ genre, env }: { genre: BeatportGenre; env: BeatportDownloadEnv }) {
  const [data, setData] = useState<GenreTop10Data | null>(null);
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setErrorMessage('');
    void (async () => {
      try {
        const loaded = await loadGenreTop10Lists(genre.slug, genre.id, controller.signal);
        if (controller.signal.aborted) return;
        setData(loaded);
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof Error && error.name === 'AbortError') return;
        // 3151-3160. Unlike the hero, this one SWALLOWS — it renders its block
        // and leaves the user on the page.
        setErrorMessage(error instanceof Error ? error.message : String(error));
      }
    })();
    return () => controller.abort();
  }, [genre.slug, genre.id]);

  if (errorMessage) {
    return (
      <div className="genre-top10-lists-container" id="genre-top10-lists-container">
        <div className="genre-top10-error">
          <h3>❌ Error Loading Top 10 Lists</h3>
          <p>Could not load Top 10 tracks for {genre.name}</p>
          <p className="error-detail">{errorMessage}</p>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="genre-top10-lists-container" id="genre-top10-lists-container">
        <div className="genre-top10-loading-container">
          <div className="genre-loading-spinner" />
          <p className="genre-loading-text">🎵 Loading Top 10 lists...</p>
        </div>
      </div>
    );
  }

  const lower = genre.name.toLowerCase();
  return (
    <div className="genre-top10-lists-container" id="genre-top10-lists-container">
      <div className="beatport-top10-section">
        <div className="beatport-top10-header">
          <h2 className="beatport-top10-title">🏆 {genre.name} Top 10 Lists</h2>
          <p className="beatport-top10-subtitle">Current trending {lower} tracks</p>
        </div>

        <div
          className="beatport-top10-container"
          // 3179: with no hype column the grid collapses to one centred track.
          // Inline, because the vanilla has no class for it.
          style={
            data.hasHypeSection
              ? undefined
              : { gridTemplateColumns: '1fr', justifyItems: 'center', maxWidth: '700px' }
          }
        >
          <TrackTop10List
            variant="beatport"
            tracks={data.beatport}
            env={env}
            listId="genre-beatport-top10-list"
            subtitle={`Most popular ${lower} tracks`}
            chartName={`${genre.name} Beatport Top 10`}
          />
          {/* 3219, and the comment at 3259: NO else branch — the hype column is
              removed outright rather than shown empty. */}
          {data.hasHypeSection ? (
            <TrackTop10List
              variant="hype"
              tracks={data.hype}
              env={env}
              listId="genre-beatport-hype10-list"
              subtitle={`Editor's trending ${lower} picks`}
              chartName={`${genre.name} Hype Top 10`}
            />
          ) : null}
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

/* ── The genre top-10 releases (3444-3641) ────────────────────────────────── */

/**
 * The homepage's release list with a genre-flavoured header and its own id.
 * The cards are identical, so ReleaseTop10Card is reused rather than restated.
 *
 * ONE DECLARED FIX, and it is the item the P0 read flagged and left open.
 * handleGenreReleaseCardClick (3558-3617) is a byte-for-byte copy of
 * handleBeatportReleaseCardClick with ONE line missing: it never calls
 * registerBeatportDownload, so a release started from a genre page downloads
 * with no progress bubble — no indication anything is happening, though the
 * files do arrive.
 *
 * Read as a decision rather than a transcription question, and decided: the
 * function's own comment says "exact parity with main page" (3556), the copy is
 * otherwise identical line for line, and the effect of restoring the call is
 * purely additive — a bubble appears where today there is silence. So this
 * calls the SAME openBeatportRelease as every other release card.
 *
 * Reversing it, if Boulder disagrees, is one argument: a variant that skips the
 * registerDownload call.
 */
function GenreTop10Releases({ genre, env }: { genre: BeatportGenre; env: BeatportDownloadEnv }) {
  const [releases, setReleases] = useState<BeatportRelease[] | null>(null);
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setReleases(null);
    setErrorMessage('');
    void (async () => {
      try {
        const loaded = await loadGenreTop10Releases(genre.slug, genre.id, controller.signal);
        if (controller.signal.aborted) return;
        setReleases(loaded);
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof Error && error.name === 'AbortError') return;
        // 3464 swallows, like the top-10 lists and unlike the hero.
        setErrorMessage(error instanceof Error ? error.message : String(error));
      }
    })();
    return () => controller.abort();
  }, [genre.slug, genre.id]);

  if (errorMessage) {
    return (
      <div className="genre-top10-releases-container" id="genre-top10-releases-container">
        <div className="beatport-releases-top10-section">
          <div className="beatport-releases-top10-header">
            {/* 3628: the error header drops the genre name that the success
                header carries, and the subtitle changes too. */}
            <h2 className="beatport-releases-top10-title">💿 Top 10 Releases</h2>
            <p className="beatport-releases-top10-subtitle">Error loading releases</p>
          </div>
          <div className="beatport-releases-top10-container">
            <div className="beatport-releases-top10-error">
              <h3>❌ Error Loading Releases</h3>
              <p>{errorMessage}</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // 3475 bails on an empty list, leaving the placeholder — so loading and
  // loaded-but-empty look the same, exactly as they do today.
  if (!releases || releases.length === 0) {
    return (
      <div className="genre-top10-releases-container" id="genre-top10-releases-container">
        <div className="genre-top10-releases-loading-container">
          <div className="genre-loading-spinner" />
          <p className="genre-loading-text">💿 Loading Top 10 releases...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="genre-top10-releases-container" id="genre-top10-releases-container">
      <div className="beatport-releases-top10-section">
        <div className="beatport-releases-top10-header">
          <h2 className="beatport-releases-top10-title">💿 Top 10 {genre.name} Releases</h2>
          <p className="beatport-releases-top10-subtitle">
            Most popular albums and EPs for {genre.name}
          </p>
        </div>
        <div className="beatport-releases-top10-container">
          <div className="beatport-releases-top10-list" id="genre-beatport-releases-top10-list">
            <div className="beatport-releases-top10-tracks">
              {releases.map((release, index) => (
                <ReleaseTop10Card
                  key={index}
                  release={release}
                  index={index}
                  // 3549-3551 binds every card with no url test, like the
                  // homepage list — so an url-less release reaches the handler
                  // and gets its toast.
                  onClick={() => {
                    void openBeatportRelease(release, env);
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
