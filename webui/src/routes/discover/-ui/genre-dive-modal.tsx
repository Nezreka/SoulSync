import type { CacheItem } from '../-discover.cache-sections';

import {
  cacheDiscoverCard,
  diveArtistHasLink,
  diveTrackSubtitle,
  formatDuration,
  formatFollowers,
  GENRE_DIVE_DEFAULT_SUBTITLE,
  GENRE_DIVE_EMPTY,
  GENRE_DIVE_EMPTY_HINT,
  GENRE_DIVE_ERROR,
  genreDiveSubtitle,
  sourceDotClass,
} from '../-discover.cache-sections';
import { DiscoverAlbumCard } from './album-shelves';

/**
 * The Genre Deep Dive modal.
 *
 * Transcribed from `openGenreDeepDive` (discover.js 10790-10885), the modal
 * the Genre Explorer pills open. Header subtitle starts as "Genre Deep Dive"
 * and becomes the section counts once data lands (`genreDiveSubtitle`); the
 * body walks loading → error → sections → the 🔍 empty state.
 *
 * Every click that leaves the modal closes it first in the vanilla (related
 * pill, artist link, track row, album card) — closing is the HOOK's job here,
 * inside the callbacks it passes.
 */

export interface GenreDiveArtist extends CacheItem {
  followers?: number;
  library_id?: string | number;
}

export interface GenreDiveData {
  related_genres?: { genre?: string }[];
  artists?: GenreDiveArtist[];
  tracks?: CacheItem[];
  albums?: CacheItem[];
}

export interface GenreDiveModalProps {
  genre: string;
  data: GenreDiveData | null;
  phase: 'loading' | 'error' | 'ready';
  buildDetailPath: (id: string, source: string | null) => string;
  /** Re-dives into a related genre (the vanilla closes and reopens). */
  onOpenGenre: (genre: string) => void;
  /** An artist link was followed — close the modal. */
  onFollowArtist: () => void;
  /** openCacheDiscoverAlbum('genre_dive_tracks' / 'genre_dive_albums', i). */
  onOpenTrack: (index: number) => void;
  onOpenAlbum: (index: number) => void;
  onClose: () => void;
}

export function GenreDiveModal({
  genre,
  data,
  phase,
  buildDetailPath,
  onOpenGenre,
  onFollowArtist,
  onOpenTrack,
  onOpenAlbum,
  onClose,
}: GenreDiveModalProps) {
  return (
    <div
      id="genre-deep-dive-modal"
      className="genre-dive-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="genre-dive-modal">
        <div className="genre-dive-header">
          <div>
            <div className="genre-dive-subtitle">
              {phase === 'ready' && data ? genreDiveSubtitle(data) : GENRE_DIVE_DEFAULT_SUBTITLE}
            </div>
            <h2 className="genre-dive-title">{genre}</h2>
          </div>
          <button type="button" className="genre-dive-close" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="genre-dive-body" id="genre-dive-body">
          {phase === 'loading' ? (
            <div className="genre-dive-loading">
              <div className="genre-dive-spinner" />
              Exploring {genre}...
            </div>
          ) : phase === 'error' ? (
            // Inline-styled in the vanilla (10884) — kept verbatim, no class.
            <div style={{ color: 'rgba(255,100,100,0.6)', textAlign: 'center', padding: 40 }}>
              {GENRE_DIVE_ERROR}
            </div>
          ) : (
            <DiveBody
              genre={genre}
              data={data ?? {}}
              buildDetailPath={buildDetailPath}
              onOpenGenre={onOpenGenre}
              onFollowArtist={onFollowArtist}
              onOpenTrack={onOpenTrack}
              onOpenAlbum={onOpenAlbum}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function DiveBody({
  genre,
  data,
  buildDetailPath,
  onOpenGenre,
  onFollowArtist,
  onOpenTrack,
  onOpenAlbum,
}: {
  genre: string;
  data: GenreDiveData;
  buildDetailPath: (id: string, source: string | null) => string;
  onOpenGenre: (genre: string) => void;
  onFollowArtist: () => void;
  onOpenTrack: (index: number) => void;
  onOpenAlbum: (index: number) => void;
}) {
  const related = data.related_genres ?? [];
  const artists = data.artists ?? [];
  const tracks = data.tracks ?? [];
  const albums = data.albums ?? [];

  if (!related.length && !artists.length && !tracks.length && !albums.length) {
    return (
      <div className="genre-dive-empty">
        <div className="genre-dive-empty-icon">🔍</div>
        <p>{GENRE_DIVE_EMPTY}</p>
        <p className="genre-dive-empty-hint">{GENRE_DIVE_EMPTY_HINT}</p>
      </div>
    );
  }

  return (
    <>
      {related.length > 0 && (
        <div className="genre-dive-related">
          <div className="genre-dive-related-label">Related Genres</div>
          {related.map((rg, i) => (
            <button
              type="button"
              key={rg.genre ?? i}
              className="genre-dive-related-pill"
              onClick={() => onOpenGenre(rg.genre ?? '')}
            >
              {rg.genre ?? ''}
            </button>
          ))}
        </div>
      )}
      {artists.length > 0 && (
        <div className="genre-dive-section">
          <h3 className="genre-dive-section-title">
            <span className="genre-dive-icon">🎤</span> Artists in {genre}
          </h3>
          <div className="genre-dive-artists">
            {artists.map((a, i) => {
              const followers = formatFollowers(a.followers);
              return (
                <a
                  key={String(a.entity_id ?? i)}
                  className="genre-dive-artist"
                  // A dive artist links out only with an entity_id (10823).
                  href={
                    diveArtistHasLink(a) ? buildDetailPath(a.entity_id!, a.source || null) : '#'
                  }
                  style={{ textDecoration: 'none', color: 'inherit' }}
                  onClick={onFollowArtist}
                >
                  <div
                    className="genre-dive-artist-img"
                    style={a.image_url ? { backgroundImage: `url("${a.image_url}")` } : undefined}
                  >
                    {!a.image_url && <span>🎤</span>}
                  </div>
                  <span className={`genre-dive-src-dot ${sourceDotClass(a.source)}`} />
                  <div className="genre-dive-artist-name">{a.name ?? ''}</div>
                  {followers !== '' && (
                    <div className="genre-dive-artist-meta">{followers} followers</div>
                  )}
                  {a.library_id && <div className="genre-dive-artist-badge">In Library</div>}
                </a>
              );
            })}
          </div>
        </div>
      )}
      {tracks.length > 0 && (
        <div className="genre-dive-section">
          <h3 className="genre-dive-section-title">
            <span className="genre-dive-icon">🎵</span> Popular Tracks
          </h3>
          <div className="genre-dive-tracks">
            {tracks.map((t, i) => (
              <div key={i} className="genre-dive-track" onClick={() => onOpenTrack(i)}>
                <div className="genre-dive-track-num">{i + 1}</div>
                <div
                  className="genre-dive-track-img"
                  style={t.image_url ? { backgroundImage: `url("${t.image_url}")` } : undefined}
                >
                  {!t.image_url && '🎵'}
                </div>
                <div className="genre-dive-track-info">
                  <div className="genre-dive-track-name">{t.name ?? ''}</div>
                  <div className="genre-dive-track-artist">{diveTrackSubtitle(t)}</div>
                </div>
                <span
                  className={`genre-dive-src-dot ${sourceDotClass(t.source)}`}
                  style={{ flexShrink: 0 }}
                />
                <div className="genre-dive-track-duration">
                  {formatDuration(t.duration_ms as number | undefined)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {albums.length > 0 && (
        <div className="genre-dive-section">
          <h3 className="genre-dive-section-title">
            <span className="genre-dive-icon">💿</span> Albums
          </h3>
          <div className="discover-carousel">
            {albums.map((a, i) => {
              const card = cacheDiscoverCard(a);
              return (
                <DiscoverAlbumCard
                  key={i}
                  cover={card.cover}
                  albumName={card.title}
                  artistName={card.subtitle}
                  badge={card.ownedBadge ? { className: 'owned', icon: '✓' } : undefined}
                  onOpen={() => onOpenAlbum(i)}
                />
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}
