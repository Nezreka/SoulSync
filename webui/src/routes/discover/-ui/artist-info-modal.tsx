import { useState } from 'react';

import type { ArtistPool, RelatedArtist } from '../-discover.your-artists-actions';
import type { SourceLogos } from './your-artists-shelf';

import {
  cleanBio,
  INFO_EMPTY,
  INFO_ERROR,
  INFO_LOADING,
  infoMatchBadges,
  infoOriginText,
  infoStats,
  infoWatchButtonDone,
  infoWatchButtonLabel,
  formatStatValue,
  relatedIsWatchlist,
  relatedLabel,
  relatedOverflow,
  relatedVisible,
  truncateBio,
} from '../-discover.your-artists-actions';

/**
 * The artist info modal.
 *
 * Transcribed from `openYourArtistInfoModal` (discover.js 5356-5515). This is
 * ONE modal with THREE entry points: the Your Artists card/modal, the related
 * strip inside itself, and the Artist Map (context menu + island card), whose
 * `openYourArtistInfoModal_direct` (10285) merely adapts a map node into a
 * pool entry and calls the same function — the adapter is ported in
 * `-discover.artist-map.entry.ts`, so no second modal exists here either.
 *
 * The pool row (hero, badges, origins, related) renders immediately; `info` is
 * the enrichment fetch that fills the body, so the body has the vanilla's
 * three states while the hero never blanks.
 */

export interface ArtistInfo {
  genres?: string[];
  summary?: string;
  lastfm_listeners?: number;
  followers?: number;
  lastfm_playcount?: number;
  popularity?: number;
}

export interface ArtistInfoModalProps {
  pool: ArtistPool;
  info: ArtistInfo | null;
  phase: 'loading' | 'error' | 'ready';
  logos: SourceLogos;
  buildDetailPath: (id: string, source: string | null) => string;
  onClose: () => void;
  /** The caller owns the request, the toasts, and the pool/card sync. */
  onToggleWatchlist: () => void;
  onExplore: () => void;
  onOpenRelated: (artist: RelatedArtist) => void;
  /** Fired when the View Discography link is followed, to close the overlays. */
  onViewDiscography: () => void;
}

export function ArtistInfoModal({
  pool,
  info,
  phase,
  logos,
  buildDetailPath,
  onClose,
  onToggleWatchlist,
  onExplore,
  onOpenRelated,
  onViewDiscography,
}: ArtistInfoModalProps) {
  const name = pool.artist_name ?? '';
  const imageUrl = pool.image_url ?? '';
  const badges = infoMatchBadges(pool);
  const originText = infoOriginText(pool.source_services);
  const related = pool._related ?? [];

  return (
    <div
      id="ya-info-modal-overlay"
      className="modal-overlay"
      // Above the Your Artists modal it opens from (5369).
      style={{ zIndex: 10001 }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="ya-info-modal">
        <button type="button" className="watch-all-close" onClick={onClose}>
          &times;
        </button>
        <div className="ya-info-hero">
          <div
            className="ya-info-hero-bg"
            style={imageUrl ? { backgroundImage: `url("${imageUrl}")` } : undefined}
          />
          <div className="ya-info-hero-content">
            <div className="ya-info-hero-img">
              {imageUrl ? (
                <img src={imageUrl} alt="" />
              ) : (
                <div className="ya-info-img-fallback">♫</div>
              )}
            </div>
            <div className="ya-info-hero-text">
              <h2 className="ya-info-name">{name}</h2>
              <div className="ya-info-badges">
                {badges.map((badge) => (
                  <InfoBadge key={badge.key} badge={badge} logos={logos} />
                ))}
              </div>
              {originText && <div className="ya-info-origin">Followed on {originText}</div>}
            </div>
          </div>
        </div>
        <div className="ya-info-body" id="ya-info-body">
          {phase === 'loading' ? (
            <div className="cache-health-loading">
              <div className="watch-all-loading-spinner" />
              <div>{INFO_LOADING}</div>
            </div>
          ) : phase === 'error' ? (
            <div className="ya-info-empty">{INFO_ERROR}</div>
          ) : (
            <InfoBody
              info={info ?? {}}
              pool={pool}
              related={related}
              onOpenRelated={onOpenRelated}
            />
          )}
        </div>
        <div className="ya-info-footer" id="ya-info-footer">
          {/* The vanilla fills the footer only after the fetch resolves (5488)
              and leaves it empty on the error path. */}
          {phase === 'ready' && (
            <>
              <WatchToggleButton pool={pool} onToggleWatchlist={onToggleWatchlist} />
              <button
                type="button"
                className="btn btn--sm btn--secondary ya-header-btn"
                onClick={onExplore}
              >
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                >
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <span>Explore</span>
              </button>
              <a
                className="btn btn--sm btn--secondary ya-header-btn ya-viewall-btn"
                href={buildDetailPath(pool.active_source_id ?? '', pool.active_source || null)}
                style={{ textDecoration: 'none', color: 'inherit' }}
                onClick={onViewDiscography}
              >
                <span>View Discography</span>
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                >
                  <path d="M5 12h14" />
                  <path d="M12 5l7 7-7 7" />
                </svg>
              </a>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * The enrichment body: stats, genres, bio, related — or the empty line when
 * every block is absent (5486).
 */
function InfoBody({
  info,
  pool,
  related,
  onOpenRelated,
}: {
  info: ArtistInfo;
  pool: ArtistPool;
  related: RelatedArtist[];
  onOpenRelated: (artist: RelatedArtist) => void;
}) {
  const stats = infoStats(info);
  const genres = info.genres ?? [];
  const bio = truncateBio(cleanBio(info.summary ?? ''));
  const hasAny = stats.visible || genres.length > 0 || bio !== '' || related.length > 0;
  if (!hasAny) return <div className="ya-info-empty">{INFO_EMPTY}</div>;

  return (
    <>
      {stats.visible && (
        <div className="ya-info-stats">
          {stats.listeners > 0 && (
            <div className="ya-info-stat">
              <span className="ya-info-stat-value">{formatStatValue(stats.listeners)}</span>
              <span className="ya-info-stat-label">listeners</span>
            </div>
          )}
          {stats.playcount > 0 && (
            <div className="ya-info-stat">
              <span className="ya-info-stat-value">{formatStatValue(stats.playcount)}</span>
              <span className="ya-info-stat-label">plays</span>
            </div>
          )}
          {stats.popularity > 0 && (
            <div className="ya-info-stat">
              <span className="ya-info-stat-value">{stats.popularity}</span>
              <span className="ya-info-stat-label">popularity</span>
            </div>
          )}
        </div>
      )}
      {genres.length > 0 && (
        <div className="ya-info-section">
          <div className="ya-info-genres">
            {genres.map((g) => (
              <span key={g} className="ya-info-genre">
                {g}
              </span>
            ))}
          </div>
        </div>
      )}
      {bio !== '' && (
        <div className="ya-info-section">
          <div className="ya-info-section-title">About</div>
          <div className="ya-info-bio">{bio}</div>
        </div>
      )}
      {related.length > 0 && (
        <div className="ya-info-section">
          <div className="ya-info-section-title">{relatedLabel(pool.on_watchlist)}</div>
          <div className="ya-info-related">
            {relatedVisible(related).map((r, i) => (
              <div
                key={String(r.id ?? i)}
                className="ya-info-related-item"
                onClick={() => onOpenRelated(r)}
              >
                <div className="ya-info-related-img">
                  {r.image_url ? <img src={r.image_url} alt="" /> : <span>♫</span>}
                </div>
                <div className="ya-info-related-text">
                  <div className="ya-info-related-name">{r.name}</div>
                  {relatedIsWatchlist(r) && (
                    <div className="ya-info-related-badge">★ Watchlist</div>
                  )}
                </div>
              </div>
            ))}
            {relatedOverflow(related) > 0 && (
              <div className="ya-info-related-more">+{relatedOverflow(related)} more</div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

/**
 * The footer watchlist button.
 *
 * The vanilla's onclick fires the toggle, rewrites its own text to Done/Added!
 * and disables itself (5491-5493) — one shot per modal open. Local state is
 * that DOM self-mutation, no more.
 */
function WatchToggleButton({
  pool,
  onToggleWatchlist,
}: {
  pool: ArtistPool;
  onToggleWatchlist: () => void;
}) {
  const [clicked, setClicked] = useState(false);
  return (
    <button
      type="button"
      className="btn btn--sm btn--secondary ya-header-btn"
      disabled={clicked}
      onClick={() => {
        onToggleWatchlist();
        setClicked(true);
      }}
    >
      {clicked ? infoWatchButtonDone(pool.on_watchlist) : infoWatchButtonLabel(pool.on_watchlist)}
    </button>
  );
}

function InfoBadge({
  badge,
  logos,
}: {
  badge: { key: string; fallback: string; title: string };
  logos: SourceLogos;
}) {
  const [failed, setFailed] = useState(false);
  const logo = logos[badge.key as keyof SourceLogos];
  return (
    <div className="ya-info-badge" title={badge.title}>
      {logo && !failed ? (
        <img src={logo} alt={badge.title} onError={() => setFailed(true)} />
      ) : (
        <span>{badge.fallback}</span>
      )}
    </div>
  );
}
