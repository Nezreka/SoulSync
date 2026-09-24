import { useState } from 'react';

import type {
  LibraryCheckTrack,
  SearchAlbum,
  SearchArtist,
  SearchLabel,
  SearchPlaylist,
  SearchTrack,
  SearchVideo,
} from '../-search.types';
import type { VideoProgress } from './video-grid';

import {
  albumIdentity,
  albumMetaLine,
  foundArtistLine,
  formatDuration,
  mergeArtistFaces,
  rankPlaylists,
  shelfAlbums,
  trackIdentity,
  trackMetaLine,
} from '../-search.helpers';
import { useShelfColumns } from '../-search.use-shelf-columns';
import { ArtistFace, CoverCard, LabelTile, TrackRow } from './compact-item';
import { DownloadIcon, PlayIcon } from './search-icons';
import styles from './search.module.css';
import { VideoGrid } from './video-grid';

export interface OwnershipState {
  ownedAlbums: ReadonlySet<string>;
  ownedTracks: ReadonlySet<string>;
  wishlistTracks: ReadonlySet<string>;
  /**
   * Track identity → the library row, for tracks that have a local file.
   *
   * The whole row, not just the path: playLibraryTrack wants the library track
   * id, its title, the album thumb and the album/artist names, and those come
   * from the library check rather than from the search result.
   */
  libraryTracks: ReadonlyMap<string, LibraryCheckTrack>;
}

export const EMPTY_OWNERSHIP: OwnershipState = {
  ownedAlbums: new Set(),
  ownedTracks: new Set(),
  wishlistTracks: new Set(),
  libraryTracks: new Map(),
};

/** which slice of the results is showing */
export type ResultFilter =
  | 'all'
  | 'artists'
  | 'albums'
  | 'singles'
  | 'tracks'
  | 'playlists'
  | 'labels';

/**
 * how much of each section the All view previews before "Show all". the cover
 * and face shelves show one measured row instead; these only hold where nothing
 * can measure (jsdom, a first paint).
 */
const PREVIEW = { artists: 12, albums: 12, singles: 8, tracks: 5, playlists: 8, labels: 8 };

/** the css grid each shelf lays out on, see .covers and .faces */
const COVER_GRID = { min: 168, gap: 14 };
const FACE_GRID = { min: 140, gap: 12 };

function artistImage(artist: SearchArtist): string | undefined {
  return artist.image_url || artist.images?.[0]?.url || undefined;
}

function albumImage(album: SearchAlbum): string | undefined {
  return album.image_url || album.images?.[0]?.url || undefined;
}

function playlistMetaLine(playlist: SearchPlaylist): string {
  const parts: string[] = [];
  if (playlist.creator) parts.push(`by ${playlist.creator}`);
  if (playlist.track_count) parts.push(`${playlist.track_count} tracks`);
  return parts.length > 0 ? parts.join(' · ') : 'Playlist';
}

/** labels come from the catalog; videos and files have none */
function suppressesLabels(source: string): boolean {
  return source === 'youtube_videos' || source === 'soulseek';
}

export interface ResultCounts {
  artists: number;
  albums: number;
  singles: number;
  tracks: number;
  playlists: number;
  labels: number;
}

export function resultCounts({
  dbArtists,
  artists,
  albums,
  tracks,
  playlists,
  labels,
  activeSource,
}: {
  dbArtists: SearchArtist[];
  artists: SearchArtist[];
  albums: SearchAlbum[];
  tracks: SearchTrack[];
  playlists: SearchPlaylist[];
  labels: SearchLabel[];
  activeSource: string;
}): ResultCounts {
  const split = shelfAlbums(albums);
  return {
    artists: mergeArtistFaces(dbArtists, artists).length,
    albums: split.albums.length,
    singles: split.singlesAndEps.length,
    tracks: tracks.length,
    playlists: playlists.length,
    labels: suppressesLabels(activeSource) ? 0 : labels.length,
  };
}

const PILLS: { key: Exclude<ResultFilter, 'all'>; label: string }[] = [
  { key: 'artists', label: 'Artists' },
  { key: 'albums', label: 'Albums' },
  { key: 'singles', label: 'Singles & EPs' },
  { key: 'tracks', label: 'Tracks' },
  { key: 'playlists', label: 'Playlists' },
  { key: 'labels', label: 'Labels' },
];

/** All plus a pill for every kind that came back, with its count */
export function FilterPills({
  counts,
  filter,
  onFilter,
  servedBy,
}: {
  counts: ResultCounts;
  filter: ResultFilter;
  onFilter: (filter: ResultFilter) => void;
  servedBy: string;
}) {
  const shown = PILLS.filter((pill) => counts[pill.key] > 0);
  return (
    <div className={styles.filters} id="enh-jump-chips" role="group" aria-label="Show">
      <button
        type="button"
        className={styles.pill}
        aria-pressed={filter === 'all'}
        onClick={() => onFilter('all')}
      >
        All
      </button>
      {shown.map((pill) => (
        <button
          key={pill.key}
          type="button"
          className={styles.pill}
          aria-pressed={filter === pill.key}
          onClick={() => onFilter(pill.key)}
        >
          {pill.label}
          <span className={styles.pillCount}>{counts[pill.key]}</span>
        </button>
      ))}
      {servedBy ? <span className={styles.servedBy}>from {servedBy}</span> : null}
    </div>
  );
}

function SectionHead({
  title,
  count,
  showAll,
}: {
  title: string;
  count?: number;
  showAll?: () => void;
}) {
  return (
    <div className={styles.sectionHead}>
      <h2 className={styles.sectionTitle}>
        {title}
        {count != null ? <span className={styles.sectionCount}>{count}</span> : null}
      </h2>
      {showAll ? (
        <button type="button" className={styles.textLink} onClick={showAll}>
          Show all {count}
        </button>
      ) : null}
    </div>
  );
}

/**
 * One kind of result: a heading, then its cards.
 *
 * in the All view it is one full row, as many as fit, with "Show all" whenever
 * more exist than fit. the same rule for every shelf, so "Show all" no longer
 * turns up on some rows and not others.
 */
function Shelf<T>({
  id,
  title,
  items,
  preview,
  all,
  grid,
  className,
  onShowAll,
  render,
}: {
  id: string;
  title: string;
  items: T[];
  preview: number;
  all: boolean;
  grid: { min: number; gap: number };
  className: string;
  onShowAll: () => void;
  render: (item: T, index: number) => React.ReactNode;
}) {
  const [ref, columns] = useShelfColumns(grid.min, grid.gap);
  // a measured row is the answer on its own; a ceiling under it would leave a
  // hole on a wide screen, which is the bug this replaced
  const fit = all ? (columns ?? preview) : items.length;
  return (
    <section className={styles.section} id={id}>
      <SectionHead
        title={title}
        count={items.length}
        showAll={all && items.length > fit ? onShowAll : undefined}
      />
      <div ref={ref} className={className} data-shelf={all || undefined}>
        {items.slice(0, fit).map(render)}
      </div>
    </section>
  );
}

export function SearchResults({
  activeSource,
  dbArtists,
  artists,
  albums,
  tracks,
  playlists = [],
  labels,
  query = '',
  videos,
  videoProgress,
  ownership,
  artistImages,
  filter = 'all',
  onFilter = () => {},
  onArtistHref,
  onLabelHref,
  onAlbumClick,
  onTrackClick,
  onPlaylistClick,
  onTrackPlay,
  onVideoDownload,
}: {
  activeSource: string;
  dbArtists: SearchArtist[];
  artists: SearchArtist[];
  albums: SearchAlbum[];
  tracks: SearchTrack[];
  playlists?: SearchPlaylist[];
  labels: SearchLabel[];
  /** what was searched, for ranking playlists that name it first */
  query?: string;
  videos: SearchVideo[];
  videoProgress: Record<string, VideoProgress>;
  ownership: OwnershipState;
  artistImages: Record<string, string>;
  filter?: ResultFilter;
  onFilter?: (filter: ResultFilter) => void;
  /**
   * `inLibrary` decides the URL, not just the styling: a library artist resolves
   * under /artist-detail/library/<id>, a found one under its metadata source.
   */
  onArtistHref: (artist: SearchArtist, inLibrary: boolean) => string;
  onLabelHref: (label: SearchLabel) => string;
  onAlbumClick: (album: SearchAlbum) => void;
  onTrackClick: (track: SearchTrack) => void;
  onPlaylistClick?: (playlist: SearchPlaylist) => void;
  onTrackPlay: (track: SearchTrack, libraryRow: LibraryCheckTrack | undefined) => void;
  onVideoDownload: (video: SearchVideo) => void;
}) {
  if (activeSource === 'youtube_videos') {
    return <VideoGrid videos={videos} progress={videoProgress} onDownload={onVideoDownload} />;
  }

  const { albums: fullAlbums, singlesAndEps } = shelfAlbums(albums);
  const rankedPlaylists = rankPlaylists(playlists, query);
  const shownLabels = suppressesLabels(activeSource) ? [] : labels;
  const all = filter === 'all';
  const show = (key: Exclude<ResultFilter, 'all'>) => all || filter === key;
  const cap = <T,>(list: T[], key: keyof typeof PREVIEW) =>
    all ? list.slice(0, PREVIEW[key]) : list;
  const more = (count: number, key: Exclude<ResultFilter, 'all'>) =>
    all && count > PREVIEW[key as keyof typeof PREVIEW] ? () => onFilter(key) : undefined;

  const trackRow = (track: SearchTrack, index: number) => {
    const identity = trackIdentity(track);
    const libraryRow = ownership.libraryTracks.get(identity);
    const owned = ownership.ownedTracks.has(identity);
    const wished = !owned && ownership.wishlistTracks.has(identity);
    return (
      <TrackRow
        key={`${identity}::${index}`}
        index={index}
        name={track.name ?? ''}
        sub={trackMetaLine(track)}
        image={track.image_url}
        duration={formatDuration(track.duration_ms)}
        badge={owned ? 'library' : wished ? 'wishlist' : undefined}
        playTitle={libraryRow ? 'Play from library' : 'Stream this track'}
        onOpen={() => onTrackClick(track)}
        onPlay={() => onTrackPlay(track, libraryRow)}
      />
    );
  };

  // singles folded into the albums row say so; on their own shelf they need not
  const albumCard = (album: SearchAlbum, index: number, withKind = false) => {
    const identity = albumIdentity(album);
    return (
      <CoverCard
        key={`${identity}::${index}`}
        name={album.name ?? ''}
        sub={albumMetaLine(album, { withKind })}
        image={albumImage(album)}
        badge={ownership.ownedAlbums.has(identity) ? 'In library' : undefined}
        onOpen={() => onAlbumClick(album)}
        actionLabel={`Download ${album.name ?? 'album'}`}
        onAction={() => onAlbumClick(album)}
      />
    );
  };

  // library artists first: this page is where things get acquired, and the
  // ones already yours are the most likely click
  const faces = mergeArtistFaces(dbArtists, artists);

  return (
    <>
      {all ? (
        <TopAndTracks
          faces={faces}
          fullAlbums={fullAlbums}
          singles={singlesAndEps}
          tracks={tracks}
          playlists={rankedPlaylists}
          artistImages={artistImages}
          onArtistHref={onArtistHref}
          onAlbumClick={onAlbumClick}
          onTrackClick={onTrackClick}
          onPlaylistClick={onPlaylistClick}
          onTrackPlay={(track) =>
            onTrackPlay(track, ownership.libraryTracks.get(trackIdentity(track)))
          }
          renderTracks={() => cap(tracks, 'tracks').map(trackRow)}
          showAllTracks={more(tracks.length, 'tracks')}
        />
      ) : null}

      {show('artists') && faces.length ? (
        <Shelf
          id="enh-spotify-artists-section"
          title="Artists"
          items={faces}
          preview={PREVIEW.artists}
          all={all}
          grid={FACE_GRID}
          className={styles.faces}
          onShowAll={() => onFilter('artists')}
          render={({ artist, inLibrary }, index) => (
            <ArtistFace
              key={`${inLibrary ? 'lib' : 'src'}:${artist.id ?? artist.name}:${index}`}
              name={artist.name ?? ''}
              sub={inLibrary ? undefined : foundArtistLine(artist)}
              image={artistImages[String(artist.id ?? '')] || artistImage(artist)}
              href={onArtistHref(artist, inLibrary)}
              inLibrary={inLibrary}
              // iss29-B04c: a lib2-native row's id is not resolvable by the
              // provider image endpoint — withholding it keeps the
              // placeholder instead of another artist's face.
              artistId={artist.image_is_native ? undefined : artist.id}
            />
          )}
        />
      ) : null}

      {show('albums') && fullAlbums.length ? (
        <Shelf
          id="enh-albums-section"
          title="Albums"
          items={fullAlbums}
          preview={PREVIEW.albums}
          all={all}
          grid={COVER_GRID}
          className={styles.covers}
          onShowAll={() => onFilter('albums')}
          render={(album, index) => albumCard(album, index, true)}
        />
      ) : null}

      {show('singles') && singlesAndEps.length ? (
        <Shelf
          id="enh-singles-section"
          title="Singles & EPs"
          items={singlesAndEps}
          preview={PREVIEW.singles}
          all={all}
          grid={COVER_GRID}
          className={styles.covers}
          onShowAll={() => onFilter('singles')}
          render={(album, index) => albumCard(album, index)}
        />
      ) : null}

      {filter === 'tracks' && tracks.length ? (
        <section className={styles.section} id="enh-tracks-section">
          <SectionHead title="Tracks" count={tracks.length} />
          <div className={styles.list}>{tracks.map(trackRow)}</div>
        </section>
      ) : null}

      {show('playlists') && rankedPlaylists.length ? (
        <Shelf
          id="enh-playlists-section"
          title="Playlists"
          items={rankedPlaylists}
          preview={PREVIEW.playlists}
          all={all}
          grid={COVER_GRID}
          className={styles.covers}
          onShowAll={() => onFilter('playlists')}
          render={(playlist, index) => (
            <CoverCard
              key={`${playlist.id ?? playlist.name}::${index}`}
              name={playlist.name ?? ''}
              sub={playlistMetaLine(playlist)}
              image={playlist.image_url || undefined}
              onOpen={() => onPlaylistClick?.(playlist)}
            />
          )}
        />
      ) : null}

      {/* labels have no art to speak of: a row of initials circles read as
          broken artist photos. quiet tiles, last. */}
      {show('labels') && shownLabels.length ? (
        <section className={styles.section} id="enh-labels-section">
          <SectionHead
            title="Labels"
            count={shownLabels.length}
            showAll={more(shownLabels.length, 'labels')}
          />
          <div className={styles.labelTiles}>
            {cap(shownLabels, 'labels').map((label, index) => (
              <LabelTile
                key={`${label.id ?? label.name}::${index}`}
                name={label.name ?? ''}
                area={label.area}
                href={onLabelHref(label)}
              />
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}

/**
 * The top result, next to the first few tracks.
 *
 * the top result is the best single answer: a library artist, else a found
 * artist, else an album, a track, a playlist. its buttons do real things,
 * the old decorative play affordance is gone.
 */
function TopAndTracks({
  faces,
  fullAlbums,
  singles,
  tracks,
  playlists,
  artistImages,
  onArtistHref,
  onAlbumClick,
  onTrackClick,
  onPlaylistClick,
  onTrackPlay,
  renderTracks,
  showAllTracks,
}: {
  faces: { artist: SearchArtist; inLibrary: boolean }[];
  fullAlbums: SearchAlbum[];
  singles: SearchAlbum[];
  tracks: SearchTrack[];
  playlists: SearchPlaylist[];
  artistImages: Record<string, string>;
  onArtistHref: (artist: SearchArtist, inLibrary: boolean) => string;
  onAlbumClick: (album: SearchAlbum) => void;
  onTrackClick: (track: SearchTrack) => void;
  onPlaylistClick?: (playlist: SearchPlaylist) => void;
  onTrackPlay: (track: SearchTrack) => void;
  renderTracks: () => React.ReactNode;
  showAllTracks?: () => void;
}) {
  const topArtist = faces[0]?.artist;
  const libraryArtist = faces[0]?.inLibrary ?? false;
  const topAlbum = topArtist ? undefined : (fullAlbums[0] ?? singles[0]);
  const topTrack = topArtist || topAlbum ? undefined : tracks[0];
  const topPlaylist = topArtist || topAlbum || topTrack ? undefined : playlists[0];

  let card: React.ReactNode = null;
  if (topArtist) {
    card = (
      <TopCard
        kind={libraryArtist ? 'Artist · In your library' : 'Artist'}
        name={topArtist.name ?? ''}
        sub={libraryArtist ? undefined : optionalArtistLine(topArtist)}
        image={artistImages[String(topArtist.id ?? '')] || artistImage(topArtist)}
        round
        actions={
          <a
            className={`${styles.button} ${styles.primary}`}
            href={onArtistHref(topArtist, libraryArtist)}
          >
            Open artist
          </a>
        }
      />
    );
  } else if (topAlbum) {
    card = (
      <TopCard
        kind={
          topAlbum.album_type === 'single' || topAlbum.album_type === 'ep' ? 'Single / EP' : 'Album'
        }
        name={topAlbum.name ?? ''}
        sub={albumMetaLine(topAlbum)}
        image={albumImage(topAlbum)}
        actions={
          <button
            type="button"
            className={`${styles.button} ${styles.primary}`}
            onClick={() => onAlbumClick(topAlbum)}
          >
            <DownloadIcon />
            Download
          </button>
        }
      />
    );
  } else if (topTrack) {
    card = (
      <TopCard
        kind="Track"
        name={topTrack.name ?? ''}
        sub={trackMetaLine(topTrack)}
        image={topTrack.image_url || undefined}
        actions={
          <>
            <button
              type="button"
              className={`${styles.button} ${styles.primary}`}
              onClick={() => onTrackPlay(topTrack)}
            >
              <PlayIcon />
              Play
            </button>
            <button type="button" className={styles.button} onClick={() => onTrackClick(topTrack)}>
              <DownloadIcon />
              Download
            </button>
          </>
        }
      />
    );
  } else if (topPlaylist) {
    card = (
      <TopCard
        kind="Playlist"
        name={topPlaylist.name ?? ''}
        sub={playlistMetaLine(topPlaylist)}
        image={topPlaylist.image_url || undefined}
        actions={
          <button
            type="button"
            className={`${styles.button} ${styles.primary}`}
            onClick={() => onPlaylistClick?.(topPlaylist)}
          >
            Open playlist
          </button>
        }
      />
    );
  }

  if (!card && !tracks.length) return null;
  return (
    <div className={styles.topGrid} data-solo={!card || !tracks.length ? true : undefined}>
      {card ? (
        <section className={styles.topCol} id="enh-top-result">
          <SectionHead title="Top result" />
          {card}
        </section>
      ) : null}
      {tracks.length ? (
        <section className={styles.topCol} id="enh-tracks-section">
          <SectionHead title="Tracks" count={tracks.length} showAll={showAllTracks} />
          <div className={styles.list}>{renderTracks()}</div>
        </section>
      ) : null}
    </div>
  );
}

/** "9.8M fans", or nothing when the source sent no count */
function optionalArtistLine(artist: SearchArtist): string | undefined {
  const line = foundArtistLine(artist);
  return line === 'Artist' ? undefined : line;
}

function TopCard({
  kind,
  name,
  sub,
  image,
  round = false,
  actions,
}: {
  kind: string;
  name: string;
  sub?: string;
  image?: string;
  round?: boolean;
  actions: React.ReactNode;
}) {
  const [failed, setFailed] = useState(false);
  const art = image && !failed ? image : undefined;
  return (
    <div className={styles.top}>
      {/* the art, blurred wide behind the card, so the space around the
          picture carries its colour instead of a flat panel */}
      {art ? (
        <div
          className={styles.topBackdrop}
          style={{ backgroundImage: `url("${art.replace(/"/g, '%22')}")` }}
          aria-hidden="true"
        />
      ) : null}
      {art ? (
        <img
          className={styles.topArt}
          data-round={round || undefined}
          src={art}
          alt=""
          onError={() => setFailed(true)}
        />
      ) : null}
      <div className={styles.topText}>
        <div className={styles.topKind}>{kind}</div>
        <div className={styles.topName}>{name}</div>
        {sub ? <div className={styles.topSub}>{sub}</div> : null}
      </div>
      <div className={styles.topActions}>{actions}</div>
    </div>
  );
}
