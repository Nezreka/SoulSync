import { queryOptions, useQuery, useQueryClient } from '@tanstack/react-query';
import { getRouteApi } from '@tanstack/react-router';
import { useEffect, useRef, useState, type ReactNode } from 'react';

import { apiClient, readJson } from '@/app/api-client';
import { useProfile } from '@/platform/shell/route-controllers';

import type { LibrarySearch as SongsSearch } from '../-library-v2.songs';
import type { LibraryV2AlbumListItem, LibraryV2ArtistSummary } from '../-library-v2.types';

import { LIBRARY_V2_QUERY_KEY, libraryV2AlbumQueryOptions } from '../-library-v2.api';
import { albumQueueRows, queueableRows } from '../-library-v2.play';
import { getServiceUrl } from '../-library-v2.service-links';
import { filterJiosaavnEntries } from '../../artist-detail/-artist-detail.enrichment';
import { loadTopTracks, trackArtistLabel } from '../../artist-detail/-artist-detail.top-tracks';
import { ExportArtistsModal } from './export-modal';
import extras from './library-index-extras.module.css';
import { songsStripQueryOptions } from './library-songs';
import styles from './library-v2-page.module.css';
import { LineIcon } from './line-icon';

/**
 * The old library page's index tools, put back on the Library v2 page: the
 * alphabet bar, the filters (has/no provider, "could be better"), the search
 * box's Escape and clear, Radio and Export in the header, the play buttons,
 * provider badges and upgradable count on the cards, the song matches above
 * a search, and "Search online" when a search finds nothing.
 */

// the route module imports the page, which imports this file
const Route = getRouteApi('/library');

export const ALPHABET = ['all', ...'abcdefghijklmnopqrstuvwxyz'.split(''), '#'] as const;

/** The artist list's provider filter, the old page's order. */
const ARTIST_SOURCES = [
  'spotify',
  'musicbrainz',
  'deezer',
  'discogs',
  'audiodb',
  'itunes',
  'lastfm',
  'genius',
  'tidal',
  'qobuz',
] as const;

/** Albums: Genius is artist-only, JioSaavn and Bandcamp album-only. */
const ALBUM_SOURCES = [
  'spotify',
  'musicbrainz',
  'deezer',
  'discogs',
  'audiodb',
  'itunes',
  'lastfm',
  'tidal',
  'qobuz',
  'jiosaavn',
  'bandcamp',
] as const;

const SOURCE_LABELS: Record<string, string> = {
  spotify: 'Spotify',
  musicbrainz: 'MusicBrainz',
  deezer: 'Deezer',
  discogs: 'Discogs',
  audiodb: 'AudioDB',
  itunes: 'iTunes',
  lastfm: 'Last.fm',
  genius: 'Genius',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  jiosaavn: 'JioSaavn',
  bandcamp: 'Bandcamp',
};

/** The provider logos the old cards used (core.js's constants). */
const BRAND_LOGOS: Record<string, string> = {
  musicbrainz: '/static/img/brands/musicbrainz.png',
  deezer: '/static/img/brands/deezer.png',
  spotify: '/static/img/brands/spotify.png',
  itunes: '/static/img/brands/itunes.png',
  lastfm: '/static/img/brands/lastfm.png',
  genius: '/static/img/brands/genius.png',
  tidal: '/static/img/brands/tidal.svg',
  qobuz: '/static/img/brands/qobuz.svg',
  discogs: '/static/img/brands/discogs.svg',
  jiosaavn: '/static/img/brands/jiosaavn.webp',
  bandcamp: '/static/img/brands/bandcamp.svg',
};

const BADGE_ABBR: Record<string, string> = {
  spotify: 'SP',
  musicbrainz: 'MB',
  deezer: 'Dz',
  audiodb: 'ADB',
  itunes: 'IT',
  lastfm: 'LFM',
  genius: 'GEN',
  tidal: 'TD',
  qobuz: 'Qz',
  discogs: 'DC',
  jiosaavn: 'JS',
  bandcamp: 'BC',
};

// --- search box --------------------------------------------------------------

/** The library's filter box: Escape clears it, and so does the ×. */
export function LibrarySearchInput({
  id,
  label,
  placeholder,
  value,
  onChange,
}: {
  id?: string;
  label: string;
  placeholder: string;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <span className={extras.searchWrap}>
      <input
        aria-label={label}
        id={id}
        className={styles.searchInput}
        type="text"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && value) {
            e.preventDefault();
            onChange('');
          }
        }}
      />
      {value ? (
        <button
          type="button"
          className={extras.searchClear}
          aria-label="Clear search"
          title="Clear"
          onClick={() => onChange('')}
        >
          ×
        </button>
      ) : null}
    </span>
  );
}

// --- alphabet bar, source filter, could be better ----------------------------

/** All, A-Z and #: the first letter of the name (or title). */
export function LibraryAlphabetBar() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <div className={extras.alphabet} id="alphabet-selector" role="group" aria-label="First letter">
      {ALPHABET.map((letter) => (
        <button
          key={letter}
          type="button"
          className={search.letter === letter ? extras.letterActive : undefined}
          aria-pressed={search.letter === letter}
          data-letter={letter}
          onClick={() =>
            void navigate({ search: (prev) => ({ ...prev, letter, page: 1 }), replace: true })
          }
        >
          {letter === 'all' ? 'All' : letter === '#' ? '#' : letter.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

/** Has / No provider id. A key the list has no column for is left out. */
type LibraryFilterSearch = {
  monitored: 'all' | 'monitored' | 'unmonitored';
  source: string;
  quality: '' | 'upgradable';
  page: number;
};

/** dev's lists: the artist list filters by the Watchlist, the album list by
 *  the Wishlist (Library v2 called both "monitoring") */
function listLabels(albums: boolean) {
  const list = albums ? 'Wishlist' : 'Watchlist';
  return {
    heading: list,
    labels: { all: 'All', monitored: `On ${list}`, unmonitored: `Not on ${list}` },
  } as const;
}
type MonitoringValue = 'all' | 'monitored' | 'unmonitored';

function sourceFilterLabel(value: string): string {
  const missing = value.startsWith('!');
  const label = SOURCE_LABELS[value.replace(/^!/, '')] ?? value.replace(/^!/, '');
  return missing ? `No ${label} match` : `Has ${label}`;
}

/**
 * The list's filters behind one button: monitoring, "could be better" and the
 * metadata match. they were three bare selects and a toggle in the toolbar,
 * one of them just reading "All". what's on shows as chips under the toolbar.
 */
export function LibraryFilterMenu({ albums = false }: { albums?: boolean }) {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const sources: readonly string[] = albums
    ? filterJiosaavnEntries(
        ALBUM_SOURCES.map((id) => ({ id })),
        'id',
      ).map((entry) => entry.id)
    : ARTIST_SOURCES;
  const active =
    (search.monitored !== 'all' ? 1 : 0) +
    (search.source ? 1 : 0) +
    (!albums && search.quality === 'upgradable' ? 1 : 0);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (event: PointerEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const set = (patch: Partial<LibraryFilterSearch>) =>
    void navigate({ search: (prev) => ({ ...prev, ...patch, page: 1 }) });

  return (
    <div className={extras.filterWrap} ref={wrapRef}>
      <button
        type="button"
        className={`${styles.btnGhost} ${extras.filterButton}${active ? ` ${extras.filterOn}` : ''}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
      >
        <LineIcon name="filter" /> Filters
        {active ? <span className={extras.filterCount}>{active}</span> : null}
      </button>
      {open ? (
        <div className={extras.filterPanel} role="dialog" aria-label="Filters">
          <section>
            <h4>{listLabels(albums).heading}</h4>
            <div
              className={extras.segmented}
              role="group"
              aria-label={`Filter by ${listLabels(albums).heading}`}
            >
              {(['all', 'monitored', 'unmonitored'] as MonitoringValue[]).map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={search.monitored === value}
                  onClick={() => set({ monitored: value })}
                >
                  {listLabels(albums).labels[value]}
                </button>
              ))}
            </div>
          </section>
          {albums ? null : (
            <section>
              <button
                type="button"
                role="switch"
                aria-checked={search.quality === 'upgradable'}
                className={extras.switchRow}
                onClick={() =>
                  set({ quality: search.quality === 'upgradable' ? '' : 'upgradable' })
                }
              >
                <span>
                  <strong>Could be better</strong>
                  <small>Only artists with tracks below their quality profile</small>
                </span>
                <span className={extras.switch} aria-hidden="true" />
              </button>
            </section>
          )}
          <section>
            <h4>Metadata match</h4>
            <select
              aria-label="Filter by metadata source"
              className={`${styles.select} ${extras.filterSelect}`}
              value={search.source}
              onChange={(e) => set({ source: e.target.value })}
            >
              <option value="">Any</option>
              <optgroup label="Missing a match">
                {sources.map((s) => (
                  <option key={`!${s}`} value={`!${s}`}>
                    No {SOURCE_LABELS[s]}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Matched to">
                {sources.map((s) => (
                  <option key={s} value={s}>
                    Has {SOURCE_LABELS[s]}
                  </option>
                ))}
              </optgroup>
            </select>
          </section>
        </div>
      ) : null}
    </div>
  );
}

/** What's filtered, each one a chip that takes it off. */
export function LibraryActiveFilters({
  albums = false,
  count,
}: {
  albums?: boolean;
  count?: number;
}) {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const set = (patch: Partial<LibraryFilterSearch>) =>
    void navigate({ search: (prev) => ({ ...prev, ...patch, page: 1 }) });
  const chips: { key: string; label: string; clear: Partial<LibraryFilterSearch> }[] = [];
  if (search.monitored !== 'all') {
    chips.push({
      key: 'monitored',
      label: listLabels(albums).labels[search.monitored],
      clear: { monitored: 'all' },
    });
  }
  if (!albums && search.quality === 'upgradable') {
    chips.push({ key: 'quality', label: 'Could be better', clear: { quality: '' } });
  }
  if (search.source) {
    chips.push({ key: 'source', label: sourceFilterLabel(search.source), clear: { source: '' } });
  }
  if (chips.length === 0) return null;
  return (
    <div className={extras.chips}>
      {count !== undefined ? (
        <span className={extras.chipsCount}>
          {count.toLocaleString()} {albums ? 'album' : 'artist'}
          {count === 1 ? '' : 's'}
        </span>
      ) : null}
      {chips.map((chip) => (
        <button
          key={chip.key}
          type="button"
          className={extras.chip}
          aria-label={`Remove filter: ${chip.label}`}
          onClick={() => set(chip.clear)}
        >
          {chip.label}
          <span aria-hidden="true">×</span>
        </button>
      ))}
      {chips.length > 1 ? (
        <button
          type="button"
          className={extras.chipsClear}
          onClick={() => set({ monitored: 'all', source: '', quality: '' })}
        >
          Clear all
        </button>
      ) : null}
    </div>
  );
}

// --- header buttons ----------------------------------------------------------

/** Library Radio: the whole library shuffled, similar tracks keep coming. */
export function LibraryRadioButton() {
  return (
    <button
      type="button"
      className={styles.btnGhost}
      title="Library Radio: shuffle your whole library, similar tracks keep auto-queuing"
      onClick={() => void window.startLibraryRadio?.()}
    >
      <LineIcon name="radio" /> Radio
    </button>
  );
}

/** Export artists (watchlist or library, JSON / CSV / text). */
export function LibraryExportButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className={styles.btnGhost}
        title="Export artists: pick watchlist or whole library, as JSON / CSV / text"
        onClick={() => setOpen(true)}
      >
        <LineIcon name="download" /> Export
      </button>
      {open ? <ExportArtistsModal initialScope="library" onClose={() => setOpen(false)} /> : null}
    </>
  );
}

// --- search results ----------------------------------------------------------

/** The songs search a library search makes, for the strip above the grid. */
export function useSongsSearchFromRoute(): SongsSearch {
  const search = Route.useSearch();
  return {
    q: search.q,
    letter: search.letter,
    page: search.page,
    sort: search.songSort === 'title' ? '' : search.songSort,
    dupes: search.dupes,
    quality: search.quality,
  };
}

/** A search that found no artists or albums: the songs above did, or look
 *  it up online. Nothing without a search. */
export function LibrarySearchFallback({ noun: _noun }: { noun: string }) {
  const { profileId } = useProfile();
  const songsSearch = useSongsSearchFromRoute();
  const q = songsSearch.q.trim();
  const strip = useQuery({
    ...songsStripQueryOptions(profileId, songsSearch),
    enabled: q.length >= 2,
  });
  // when songs match, the page's own note says so; this is the way out
  // when nothing in the library does
  if (!q || (strip.data?.songs?.length ?? 0) > 0) return null;
  return (
    <button
      type="button"
      className={extras.searchOnline}
      onClick={() => window._handoffLibrarySearchToEnhancedSearch?.(q)}
    >
      🔍 Search online for <span>&quot;{q}&quot;</span> →
    </button>
  );
}

// --- cards -------------------------------------------------------------------

/** A provider's logo, or its short name when there is none or it won't load. */
function BadgeMark({ service }: { service: string }) {
  const [failed, setFailed] = useState(false);
  const logo = BRAND_LOGOS[service];
  if (!logo || failed) return <>{BADGE_ABBR[service]}</>;
  return <img src={logo} alt={BADGE_ABBR[service]} onError={() => setFailed(true)} />;
}

/** Provider badges, linking out where the provider has a page. */
export function ProviderBadges({
  ids,
  entity,
}: {
  ids: Record<string, string> | undefined;
  entity: 'artist' | 'album';
}) {
  if (!ids) return null;
  const keys = filterJiosaavnEntries(
    Object.keys(BADGE_ABBR)
      .filter((key) => ids[key])
      .map((key) => ({ key })),
    'key',
  ).map((entry) => entry.key);
  if (!keys.length) return null;
  return (
    <span className={extras.badges}>
      {keys.map((key) => {
        const url = getServiceUrl(key, entity, ids[key]);
        const label = SOURCE_LABELS[key] ?? key;
        const body: ReactNode = <BadgeMark service={key} />;
        return url ? (
          <a
            key={key}
            className={extras.badge}
            href={url}
            target="_blank"
            rel="noreferrer"
            title={`Open on ${label}`}
            onClick={(e) => e.stopPropagation()}
          >
            {body}
          </a>
        ) : (
          <span key={key} className={extras.badge} title={label}>
            {body}
          </span>
        );
      })}
    </span>
  );
}

/** most badges one column holds before the rest move to a second, as on dev */
const BADGES_PER_COLUMN = 6;

/** One provider badge in the classic card's badge column. */
function ClassicBadge({
  service,
  entity,
  id,
}: {
  service: string;
  entity: 'artist' | 'album';
  id: string;
}) {
  const [failed, setFailed] = useState(false);
  const url = getServiceUrl(service, entity, id);
  const label = SOURCE_LABELS[service] ?? service;
  const logo = BRAND_LOGOS[service];
  const mark =
    logo && !failed ? (
      <img
        src={logo}
        style={{ width: 16, height: 'auto', display: 'block' }}
        alt=""
        onError={() => setFailed(true)}
      />
    ) : (
      <span style={{ fontSize: 9, fontWeight: 700 }}>{BADGE_ABBR[service]}</span>
    );
  // a real link where the provider has a page, so middle-click works too
  return url ? (
    <a
      className="source-card-icon"
      href={url}
      target="_blank"
      rel="noreferrer"
      title={`Open on ${label}`}
      onClick={(e) => e.stopPropagation()}
    >
      {mark}
    </a>
  ) : (
    <div className="source-card-icon" title={label}>
      {mark}
    </div>
  );
}

/** The provider badges in dev's card column markup, so its css places them:
 *  past six, an overflow column comes first and the rest stay in the primary. */
export function ClassicCardBadges({
  ids,
  entity,
}: {
  ids: Record<string, string> | undefined;
  entity: 'artist' | 'album';
}) {
  if (!ids) return null;
  const keys = filterJiosaavnEntries(
    Object.keys(BADGE_ABBR)
      .filter((key) => ids[key])
      .map((key) => ({ key })),
    'key',
  ).map((entry) => entry.key);
  if (!keys.length) return null;
  const badge = (key: string) => (
    <ClassicBadge key={key} service={key} entity={entity} id={ids[key]} />
  );
  if (keys.length <= BADGES_PER_COLUMN) {
    return <div className="card-badge-container">{keys.map(badge)}</div>;
  }
  return (
    <div className="card-badge-container">
      <div className="badge-overflow-column">{keys.slice(BADGES_PER_COLUMN).map(badge)}</div>
      <div className="badge-primary-column">{keys.slice(0, BADGES_PER_COLUMN).map(badge)}</div>
    </div>
  );
}

/** ▶ on an artist card: their top tracks, through the acquisition-aware
 *  player (owned files first, misses go to the normal download flow). */
export function ArtistCardPlayButton({
  artist,
  className = extras.cardPlay,
}: {
  artist: LibraryV2ArtistSummary;
  className?: string;
}) {
  const [pending, setPending] = useState(false);
  const play = async () => {
    if (pending) return;
    setPending(true);
    try {
      const state = await loadTopTracks(artist.id, artist.name);
      if (!state.tracks.length) {
        window.showToast?.(`No top tracks found for ${artist.name}`, 'info');
        return;
      }
      const tracks = state.tracks.map((track) => {
        const artistName = trackArtistLabel(track, artist.name);
        return {
          ...track,
          title: track.title || track.name || 'Unknown Track',
          name: track.name || track.title || 'Unknown Track',
          artist: artistName,
          artists: track.artists?.length ? track.artists : [{ name: artistName }],
        };
      });
      await window.playTrackList?.(tracks, `${artist.name}: Top Tracks`);
    } catch {
      window.showToast?.(`Could not play ${artist.name}'s top tracks`, 'error');
    } finally {
      setPending(false);
    }
  };
  return (
    <button
      type="button"
      className={className}
      aria-label={`Play ${artist.name}'s top tracks`}
      title="Play top tracks"
      disabled={pending}
      onClick={(e) => {
        e.stopPropagation();
        void play();
      }}
    >
      {pending ? '…' : '▶'}
    </button>
  );
}

/** ▶ on an album card: queue the tracks you own. */
export function AlbumCardPlayButton({
  album,
  className = extras.cardPlay,
}: {
  album: LibraryV2AlbumListItem;
  className?: string;
}) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const canPlay = album.tracks_present > 0;
  const play = async () => {
    if (pending || !canPlay) return;
    setPending(true);
    try {
      const detail = await queryClient.fetchQuery(libraryV2AlbumQueryOptions(album.id));
      const rows = queueableRows(albumQueueRows(detail, album.artist_name ?? ''));
      if (!rows.length) {
        window.showToast?.(`No playable tracks in ${album.title}`, 'info');
        return;
      }
      await window.playTrackList?.(rows, album.title || 'Album');
    } catch {
      window.showToast?.(`Could not play ${album.title}`, 'error');
    } finally {
      setPending(false);
    }
  };
  return (
    <button
      type="button"
      className={className}
      aria-label={`Play ${album.title}`}
      title={canPlay ? 'Play' : 'No files in the library for this release'}
      disabled={pending || !canPlay}
      onClick={(e) => {
        e.stopPropagation();
        void play();
      }}
    >
      {pending ? '…' : '▶'}
    </button>
  );
}

/** What an artist card adds below its monitor toggle. */
export function ArtistCardExtras({
  artist,
  upgradable,
}: {
  artist: LibraryV2ArtistSummary;
  upgradable?: number;
}) {
  return (
    <span className={extras.cardExtras}>
      <ArtistCardPlayButton artist={artist} />
      <ProviderBadges ids={artist.provider_ids} entity="artist" />
      {upgradable ? (
        <span
          className={extras.upgradable}
          title="Tracks below their quality profile, which an upgrade could replace"
        >
          {upgradable} could be better
        </span>
      ) : null}
    </span>
  );
}

/** What an album card adds below its monitor toggle. */
export function AlbumCardExtras({ album }: { album: LibraryV2AlbumListItem }) {
  return (
    <span className={extras.cardExtras}>
      <AlbumCardPlayButton album={album} />
      <ProviderBadges ids={album.provider_ids} entity="album" />
    </span>
  );
}

// --- upgradable counts -------------------------------------------------------

function upgradableCountsQueryOptions(ids: number[]) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist-upgradable-counts', ids.join(',')] as const,
    queryFn: async () => {
      const payload = await readJson<{ success?: boolean; counts?: Record<string, number> }>(
        apiClient.get('library/v2/artists/upgradable-counts', {
          searchParams: { ids: ids.join(',') },
        }),
      );
      return payload.counts ?? {};
    },
    enabled: ids.length > 0,
    staleTime: 60_000,
  });
}

/** Each card's "could be better" count, fetched after the cards draw. */
export function useUpgradableCounts(artists: LibraryV2ArtistSummary[]): Map<number, number> {
  const ids = artists.map((a) => a.id);
  const query = useQuery(upgradableCountsQueryOptions(ids));
  const counts = new Map<number, number>();
  for (const [id, n] of Object.entries(query.data ?? {})) counts.set(Number(id), n);
  return counts;
}
