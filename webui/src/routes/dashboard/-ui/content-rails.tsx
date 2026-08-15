/**
 * The dashboard content band: ONE full-width card holding both album rails
 * behind a tab switcher — Recently Added | Fresh Releases.
 *
 * One container instead of two (Boulder: "maybe recently added and fresh
 * releases can be one section? … i like it pretty and elegant"). The tabs
 * earn their keep three ways:
 * - half the chrome: one purple card, one strip of covers, no second box
 *   competing for the fold;
 * - each feed keeps its own identity (title, subtitle, click behaviour)
 *   without stacking two scrollbars;
 * - a feed with no rows simply has no tab, and with both empty the band
 *   renders NOTHING — a fresh install sees the ops grid it always saw.
 *
 * Clicks are unchanged: Recently Added plays the newest landed track; Fresh
 * Releases plays when fully owned and opens the standard download-missing
 * modal when not (openFreshRelease). One fetch per feed, on mount.
 */

import { useCallback, useState } from 'react';

import type { FreshRelease, RecentlyAddedAlbum } from '../-dash.content';
import { useLiveRefresh } from '../-dash.live-refresh';

import { getShellBridge } from '@/platform/shell/bridge';
import { thumb } from '@/platform/artwork-thumb';

import {
  fetchFreshReleases,
  fetchRecentlyAdded,
  fileBadge,
  openArtistFromRail,
  openFreshRelease,
  relativeAge,
} from '../-dash.content';

interface RailCardProps {
  /** Small corner check — the library already has this (discover's badge). */
  owned?: boolean;
  cover: string;
  /** Second image to try when `cover` fails to load (the artist's art). */
  fallbackCover?: string;
  name: string;
  sub: string;
  /** Top-right pill (time-ago / release date). */
  caption?: string;
  /** Bottom file line ("FLAC · soulseek"). */
  badge?: string;
  titleAttr?: string;
  /** Makes the sub (artist) line its own hit-target — straight to the artist page. */
  onArtistClick?: () => void;
  onOpen: () => void;
}

function RailCard({ owned, cover, fallbackCover, name, sub, caption, badge, titleAttr, onOpen, onArtistClick }: RailCardProps) {
  // Fallback ladder, one rung per failure: cover -> the artist's art -> ♫.
  // History thumb URLs can be stale or media-server-authed and die in the
  // browser even when they exist, so the second image matters as much as the
  // server-side backfill.
  const candidates = [cover, fallbackCover].filter((c, i, all) => c && all.indexOf(c) === i);
  const [rung, setRung] = useState(0);
  const src = candidates[rung];
  return (
    <div className="ya-card dash-rail-card" title={titleAttr ?? `${name} — ${sub}`} onClick={onOpen}>
      <div className="ya-card-img">
        {src && <img key={src} src={thumb(src, 'grid')} alt="" loading="lazy" onError={() => setRung(rung + 1)} />}
        <div className="ya-card-placeholder" style={src ? { display: 'none' } : undefined}>
          ♫
        </div>
      </div>
      <div className="ya-card-gradient" />
      {owned && (
        <div className="ya-card-badges">
          <div className="discover-album-badge owned" title="In your library">✓</div>
        </div>
      )}
      {caption && <div className="dash-rail-caption">{caption}</div>}
      <div className="ya-card-info">
        <div className="ya-card-name">{name}</div>
        {onArtistClick ? (
          <button
            type="button"
            className="ya-card-sub ya-card-sub--link"
            title={`Open ${sub}`}
            onClick={(event) => {
              // The card's own onOpen plays/opens the ALBUM — the artist
              // line must not trigger both.
              event.stopPropagation();
              onArtistClick();
            }}
          >
            {sub}
          </button>
        ) : (
          <div className="ya-card-sub">{sub}</div>
        )}
        {badge && <div className="dash-rail-badge">{badge}</div>}
      </div>
    </div>
  );
}

// Slower than Recently Played: a library gains albums far less often than
// it gains plays, and this rail costs two queries.
const CONTENT_REFRESH_MS = 120_000;

type BandTab = 'recent' | 'fresh';

export function ContentBand() {
  const [albums, setAlbums] = useState<RecentlyAddedAlbum[]>([]);
  const [releases, setReleases] = useState<FreshRelease[]>([]);
  const [tab, setTab] = useState<BandTab>('recent');

  // Was a one-shot mount load, so a finished download did not show up here
  // until the page was reloaded. Both rails refresh on a timer now and catch
  // up whenever the tab comes back to the front.
  const load = useCallback(async () => {
    const [added, fresh] = await Promise.all([
      fetchRecentlyAdded().catch(() => null),
      fetchFreshReleases().catch(() => null),
    ]);
    // null means that rail's fetch failed; keep what is on screen rather than
    // blanking a populated rail because of one bad poll.
    if (added) setAlbums(added);
    if (fresh) setReleases(fresh);
  }, []);

  useLiveRefresh(load, { intervalMs: CONTENT_REFRESH_MS });

  const hasRecent = albums.length > 0;
  const hasFresh = releases.length > 0;
  if (!hasRecent && !hasFresh) return null;

  // A tab you can't switch to is clutter; a selected tab whose feed is empty
  // (recent empty, fresh not) silently shows the one with rows.
  const active: BandTab = tab === 'recent' && !hasRecent ? 'fresh' : tab === 'fresh' && !hasFresh ? 'recent' : tab;
  const fromDiscover = hasFresh && releases[0].fromDiscover;
  const now = Date.now();

  return (
    <article className="dash-card dash-card--rail">
      <div className="dash-rail-head">
        <div className="dash-band-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={active === 'recent'}
            className={`dash-band-tab${active === 'recent' ? ' active' : ''}`}
            style={hasRecent ? undefined : { display: 'none' }}
            onClick={() => setTab('recent')}
          >
            Recently Added
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={active === 'fresh'}
            className={`dash-band-tab${active === 'fresh' ? ' active' : ''}`}
            style={hasFresh ? undefined : { display: 'none' }}
            onClick={() => setTab('fresh')}
          >
            Fresh Releases
          </button>
        </div>
        <span className="dash-rail-subtitle">
          {active === 'recent'
            ? 'the latest albums to land in your library'
            : fromDiscover
              ? 'new music picked for you — follow artists to tune this'
              : 'new music from artists you watch'}
        </span>
      </div>

      {active === 'recent' ? (
        <div className="dash-rail">
          {albums.map((album) => (
            <RailCard
              key={`${album.artistName}::${album.albumName}`}
              cover={album.cover}
              fallbackCover={album.artistCover}
              name={album.albumName}
              sub={album.artistName}
              caption={relativeAge(album.addedAt, now)}
              badge={fileBadge(album.quality, album.source)}
              titleAttr={`Play ${album.albumName} — ${album.artistName}`}
              onArtistClick={() => void openArtistFromRail({ name: album.artistName })}
              onOpen={() => {
                if (!album.playFilePath) return;
                // id -1 is truthy, so playLibraryTrack canonicalises against
                // the DB (resolve-track) and picks up metadata + artwork.
                getShellBridge()?.playLibraryTrack(
                  { id: -1, title: album.playTitle, file_path: album.playFilePath },
                  album.albumName,
                  album.artistName,
                );
              }}
            />
          ))}
        </div>
      ) : (
        <div className="dash-rail">
          {releases.map((release, i) => (
            <RailCard
              key={`${release.artistName}::${release.albumName}::${i}`}
              owned={release.owned}
              cover={release.cover}
              name={release.albumName}
              sub={release.artistName}
              caption={release.releaseDate}
              badge={release.trackCount ? `${release.trackCount} tracks` : undefined}
              onArtistClick={() =>
                void openArtistFromRail({
                  name: release.artistName,
                  spotifyArtistId: release.spotifyArtistId || null,
                })
              }
              onOpen={() => void openFreshRelease(release)}
            />
          ))}
        </div>
      )}
    </article>
  );
}
