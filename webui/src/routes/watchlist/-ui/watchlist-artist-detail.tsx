import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

import type { WatchlistArtistConfigResponse } from '../-watchlist.types';

import {
  removeWatchlistArtist,
  WATCHLIST_QUERY_KEY,
  watchlistArtistConfigQueryOptions,
} from '../-watchlist.api';
import { artistPills, formatRelativeScanTime } from '../-watchlist.helpers';
import { hideOnError } from './hide-on-error';
import { formatFollowers } from './watchlist-artist-config-modal';

/**
 * Which provider the "View Discography" link should use.
 *
 * Prefers the id belonging to the ACTIVE music source so the discography page
 * opens against the provider the rest of the app is using, then falls back
 * through the remaining matches. iTunes is the explicit fallback before the
 * generic chain, matching the vanilla ladder exactly.
 *
 * The vanilla version read the `currentMusicSourceName` global. That is a
 * script-scoped `let` in core.js and therefore not reachable from a module, so
 * this uses `global_metadata_source` from the same config payload — it is
 * `get_primary_source()` server-side, the value that global mirrors.
 */
export function pickDiscographySource(
  payload: Pick<
    WatchlistArtistConfigResponse,
    | 'spotify_artist_id'
    | 'itunes_artist_id'
    | 'deezer_artist_id'
    | 'discogs_artist_id'
    | 'musicbrainz_artist_id'
    | 'global_metadata_source'
    | 'available_sources'
  >,
): { id: string; source: string } | null {
  const active = (payload.global_metadata_source || '').toLowerCase();

  // Never pin a provider the user has switched off. The artist page treats a
  // pinned source as exclusive and errors out when it can't serve ("Could not
  // access spotify … provider is unavailable"), so an id belonging to a dead
  // provider is worse than no link at all — that's the Discord report where
  // the watchlist failed but Discover worked: Spotify off, artist matched
  // only on Spotify, and this ladder happily pinned it anyway.
  //
  // `available_sources` comes from the server (the only thing that knows).
  // When it is absent — older backend, or a caller that doesn't fetch it —
  // every source stays eligible, i.e. exactly the previous behaviour.
  const availableList = payload.available_sources;

  const ladder = (onlyAvailable: boolean): { id: string; source: string } | null => {
    const pick = (source: string, id: string | null | undefined): string | null => {
      if (!id) return null;
      if (onlyAvailable && availableList && !availableList.includes(source)) return null;
      return id;
    };
    const spotify = pick('spotify', payload.spotify_artist_id);
    const itunes = pick('itunes', payload.itunes_artist_id);
    const deezer = pick('deezer', payload.deezer_artist_id);
    const discogs = pick('discogs', payload.discogs_artist_id);
    const musicbrainz = pick('musicbrainz', payload.musicbrainz_artist_id);

    if (active.includes('spotify') && spotify) return { id: spotify, source: 'spotify' };
    if (active.includes('discogs') && discogs) return { id: discogs, source: 'discogs' };
    if (active.includes('deezer') && deezer) return { id: deezer, source: 'deezer' };
    if (active.includes('musicbrainz') && musicbrainz)
      return { id: musicbrainz, source: 'musicbrainz' };
    if (itunes) return { id: itunes, source: 'itunes' };

    if (spotify) return { id: spotify, source: 'spotify' };
    if (discogs) return { id: discogs, source: 'discogs' };
    if (deezer) return { id: deezer, source: 'deezer' };
    if (musicbrainz) return { id: musicbrainz, source: 'musicbrainz' };
    return null;
  };

  // A live provider's id first. Then — and this is why it's a LAST resort, not
  // a hard filter — the original ladder unchanged: a dead provider's id is
  // still worth linking, because the artist page resolves a LIBRARY artist
  // straight off the source-id column (artist_source_lookup.py) with no
  // provider call at all. Dropping the link outright would break owned
  // artists that render fine today. Strictly monotonic: better when a live
  // provider has an id, identical to before when none does.
  return ladder(true) ?? ladder(false);
}

interface Props {
  profileId: number;
  artistId: string;
  onClose: () => void;
  onOpenSettings: () => void;
}

export function WatchlistArtistDetail({ profileId, artistId, onClose, onOpenSettings }: Props) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const configQuery = useQuery(watchlistArtistConfigQueryOptions(profileId, artistId));
  const payload = configQuery.data;

  // The panel slides in; the vanilla code added .visible on the next frame so
  // the transition had a starting state to animate from.
  const [visible, setVisible] = useState(false);
  const [bannerFailed, setBannerFailed] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  const remove = useMutation({
    mutationFn: () => removeWatchlistArtist(artistId),
    onSuccess: async () => {
      onClose();
      await queryClient.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY });
      try {
        window.updateWatchlistButtonCount?.();
      } catch {
        /* non-fatal */
      }
    },
    onError: (error: Error) =>
      window.showToast?.(`Error removing from watchlist: ${error.message}`, 'error'),
  });

  const artist = payload?.artist;
  const config = payload?.config;
  const releases = payload?.recent_releases ?? [];
  const pills = config ? artistPills(config) : [];
  const discography = payload ? pickDiscographySource(payload) : null;

  const metaTags = [artist?.style, artist?.mood, artist?.label].filter((value): value is string =>
    Boolean(value),
  );

  const scanTimeText = config?.last_scan_timestamp
    ? formatRelativeScanTime(config.last_scan_timestamp)
    : 'Never scanned';
  const dateAddedText = config?.date_added
    ? `Added ${new Date(config.date_added).toLocaleDateString()}`
    : '';

  return (
    <div className={`watchlist-artist-detail-overlay${visible ? ' visible' : ''}`}>
      {artist?.banner_url && !bannerFailed ? (
        <div className="watchlist-detail-banner">
          {/* The vanilla markup removed the whole banner block on error, not
              just the img, so the fade overlay does not linger over nothing. */}
          <img src={artist.banner_url} alt="" onError={() => setBannerFailed(true)} />
          <div className="watchlist-detail-banner-fade" />
        </div>
      ) : null}

      <div className={`watchlist-detail-content${artist?.banner_url ? ' has-banner' : ''}`}>
        <button type="button" className="watchlist-detail-back" onClick={onClose}>
          ← Back to Watchlist
        </button>

        <div className="watchlist-detail-hero">
          {artist?.image_url ? (
            <img src={artist.image_url} alt={artist.name} onError={hideOnError} />
          ) : null}
          <div className="watchlist-detail-hero-info">
            <h2 className="watchlist-detail-hero-name">{artist?.name ?? ''}</h2>
            {artist?.followers || artist?.popularity ? (
              <div className="watchlist-detail-hero-stats">
                {artist.followers ? (
                  <div className="watchlist-detail-stat">
                    <span className="watchlist-detail-stat-value">
                      {formatFollowers(artist.followers)}
                    </span>
                    <span className="watchlist-detail-stat-label">Followers</span>
                  </div>
                ) : null}
                {artist.popularity ? (
                  <div className="watchlist-detail-stat">
                    <span className="watchlist-detail-stat-value">{artist.popularity}/100</span>
                    <span className="watchlist-detail-stat-label">Popularity</span>
                  </div>
                ) : null}
              </div>
            ) : null}
            {artist?.genres && artist.genres.length > 0 ? (
              <div className="watchlist-detail-hero-genres">
                {/* No slice here — the detail view shows every genre, unlike
                    the config modal's hero which caps at three. */}
                {artist.genres.map((genre) => (
                  <span key={genre} className="watchlist-detail-genre-tag">
                    {genre}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        </div>

        {artist?.summary ? (
          <div className="watchlist-detail-section">
            <div className="watchlist-detail-section-title">About</div>
            <p className="watchlist-detail-bio">{artist.summary}</p>
          </div>
        ) : null}

        {metaTags.length > 0 ? (
          <div className="watchlist-detail-section">
            <div className="watchlist-detail-section-title">Info</div>
            <div className="watchlist-detail-hero-genres">
              {metaTags.map((tag) => (
                <span key={tag} className="watchlist-detail-genre-tag">
                  {tag}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {releases.length > 0 ? (
          <div className="watchlist-detail-section">
            <div className="watchlist-detail-section-title">Recent Releases</div>
            <div className="watchlist-detail-releases">
              {releases.map((release) => (
                <div
                  key={`${release.album_name}-${release.release_date ?? ''}`}
                  className="watchlist-detail-release"
                >
                  {release.album_cover_url ? <img src={release.album_cover_url} alt="" /> : null}
                  <div className="watchlist-detail-release-info">
                    <span className="watchlist-detail-release-name">{release.album_name}</span>
                    <span className="watchlist-detail-release-meta">
                      {release.release_date}
                      {release.track_count ? ` · ${release.track_count} tracks` : ''}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="watchlist-detail-section">
          <div className="watchlist-detail-section-title">Watchlist</div>
          <div className="watchlist-detail-watchlist-info">
            <span className="watchlist-card-meta">{scanTimeText}</span>
            {dateAddedText ? (
              <>
                <span className="watchlist-detail-info-sep">·</span>
                <span className="watchlist-card-meta">{dateAddedText}</span>
              </>
            ) : null}
          </div>
          <div className="watchlist-card-pills" style={{ padding: 0, marginTop: 8 }}>
            {pills.length > 0 ? (
              pills.map((pill) => (
                <span key={pill.label} className={`watchlist-pill watchlist-pill-${pill.kind}`}>
                  {pill.label}
                </span>
              ))
            ) : (
              <span className="watchlist-card-meta">No release types enabled</span>
            )}
          </div>
        </div>

        <div className="watchlist-detail-actions">
          <button
            type="button"
            className="watchlist-detail-discog-btn"
            disabled={!discography}
            aria-disabled={!discography}
            style={
              discography ? undefined : { pointerEvents: 'none', opacity: 0.5, color: 'inherit' }
            }
            onClick={() => {
              if (!discography) return;
              onClose();
              void navigate({
                to: '/artist-detail/$source/$id',
                params: { source: discography.source, id: discography.id },
              });
            }}
          >
            View Discography
          </button>
          <button type="button" className="watchlist-detail-settings-btn" onClick={onOpenSettings}>
            Settings
          </button>
          <button
            type="button"
            className="watchlist-detail-remove-btn"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            Remove from Watchlist
          </button>
        </div>
      </div>
    </div>
  );
}
