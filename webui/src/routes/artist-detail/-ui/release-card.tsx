import { useState } from 'react';

import type { DiscographyRelease } from '../-artist-detail.types';

import {
  completionOverlay,
  isExplicit,
  musicbrainzReleaseUrl,
  releaseBackgroundSrc,
  releaseCardClassName,
  releaseYearText,
} from '../-artist-detail.card';
import { releaseFlags } from '../-artist-detail.filters';
import { gapSourceLabel } from '../-artist-detail.gap-fill';

interface Props {
  release: DiscographyRelease;
  /** Drives the flag recomputation and whether the overlay renders at all. */
  isMusicBrainz: boolean;
  isSourceArtist: boolean;
  onOpen: (release: DiscographyRelease) => void;
  /** Resolve the release tracklist and replace the player queue with it. */
  onPlay: (release: DiscographyRelease) => void | Promise<void>;
}

/**
 * One release tile. Markup mirrors createReleaseCard (library.js:1683) so the
 * existing CSS applies unchanged.
 *
 * The `data-is-*` attributes are rendered even though React filters in JS
 * rather than by reading them back: the discography CSS and the guided tour
 * both select on them, and dropping them would change the page's appearance
 * without changing any behaviour a test would notice.
 */
export function ReleaseCard({ release, isMusicBrainz, isSourceArtist, onOpen, onPlay }: Props) {
  const [playPending, setPlayPending] = useState(false);
  const flags = releaseFlags(release, isMusicBrainz);
  const overlay = completionOverlay(release, isSourceArtist);
  const year = releaseYearText(release);
  const bg = releaseBackgroundSrc(release);
  const mbUrl = musicbrainzReleaseUrl(release);
  const releaseId = release.id ?? '';
  // Gap-fill cards (#1067) live in the real grids; the badge is what marks
  // them, and it names the source the click will resolve from.
  const gapSource = release._gap_source ? gapSourceLabel(String(release._gap_source)) : '';

  return (
    <div
      className={`${releaseCardClassName(release)}${release._gap_source ? ' gapfill-card' : ''}`}
      data-release-id={String(releaseId)}
      data-album-id={String(releaseId)}
      data-album-name={release.title ?? ''}
      data-album-type={release.album_type ?? 'album'}
      data-is-live={String(flags.isLive)}
      data-is-compilation={String(flags.isCompilation)}
      data-is-featured={String(flags.isFeatured)}
      onClick={() => onOpen(release)}
    >
      {/* data-bg-src, not a style: an IntersectionObserver swaps it in, so a
          75-card grid does not fetch 75 images up front. */}
      <div className="album-card-image" data-bg-src={bg ?? undefined} />

      <button
        type="button"
        className="release-card-play-btn"
        aria-label={`Play ${release.title || 'album'}`}
        title={`Play ${release.title || 'album'}`}
        disabled={playPending}
        onClick={(e) => {
          e.stopPropagation();
          if (playPending) return;
          setPlayPending(true);
          try {
            void Promise.resolve(onPlay(release)).finally(() => setPlayPending(false));
          } catch {
            setPlayPending(false);
          }
        }}
      >
        {playPending ? '…' : '▶'}
      </button>

      {overlay ? (
        <div className={`completion-overlay ${overlay.className}`}>
          <span className="completion-status">{overlay.label}</span>
        </div>
      ) : null}

      <div className="album-card-content">
        <div className="album-card-name" title={release.title ?? ''}>
          {release.title ?? ''}
          {isExplicit(release) ? <span className="explicit-badge">E</span> : null}
        </div>
        {year ? <div className="album-card-year">{year}</div> : null}
      </div>

      {gapSource ? (
        <div
          className="gapfill-source-badge"
          title={`Only listed on ${gapSource} — opens and downloads from there`}
        >
          {gapSource}
        </div>
      ) : null}

      {/* Rendered LAST so it sits above the gradient overlay. */}
      {mbUrl ? (
        <div
          className="mb-card-icon"
          title="View on MusicBrainz"
          onClick={(e) => {
            // Without this the card's own click fires too and opens the album.
            e.stopPropagation();
            window.open(mbUrl, '_blank');
          }}
        >
          <img
            src="/static/img/brands/musicbrainz.png"
            style={{ width: 20, height: 'auto', display: 'block' }}
            alt="MusicBrainz"
          />
        </div>
      ) : null}
    </div>
  );
}
