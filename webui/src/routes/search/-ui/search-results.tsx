import type {
  SearchAlbum,
  SearchArtist,
  SearchLabel,
  SearchTrack,
  SearchVideo,
} from '../-search.types';
import type { VideoProgress } from './video-grid';

import {
  albumIdentity,
  artistMetaLine,
  formatDuration,
  labelMetaLine,
  splitAlbums,
  trackIdentity,
} from '../-search.helpers';
import { SOURCE_LABELS } from '../-search.types';
import { CompactItem, ResultSection } from './compact-item';
import { VideoGrid } from './video-grid';

/** Ownership carried by IDENTITY, never by list position. See the helpers. */
export interface OwnershipState {
  ownedAlbums: ReadonlySet<string>;
  ownedTracks: ReadonlySet<string>;
  wishlistTracks: ReadonlySet<string>;
  /** Track identity → local file path, when the library has one. */
  playableTracks: ReadonlyMap<string, string>;
}

export const EMPTY_OWNERSHIP: OwnershipState = {
  ownedAlbums: new Set(),
  ownedTracks: new Set(),
  wishlistTracks: new Set(),
  playableTracks: new Map(),
};

const LIBRARY_BADGE = { text: 'In Library', className: 'enh-item-lib-badge' };
const WISHLIST_BADGE = { text: 'Wishlisted', className: 'enh-item-wishlist-badge' };

function sourceBadge(source: string | undefined) {
  const info = source ? SOURCE_LABELS[source] : undefined;
  return info ? { text: info.text, className: info.badgeClass } : undefined;
}

function artistImage(artist: SearchArtist): string | undefined {
  return artist.image_url || artist.images?.[0]?.url || undefined;
}

function albumImage(album: SearchAlbum): string | undefined {
  return album.image_url || album.images?.[0]?.url || undefined;
}

/**
 * The six result sections, in the vanilla's order.
 *
 * That order is deliberate on an acquisition surface: what you already own
 * ("In Your Library") comes first, then artists you could add, then releases.
 *
 * Two structural details are load-bearing and easy to lose in a port:
 *
 * 1. The two artist sections live inside `.enh-artists-wrapper` and each wears
 *    `enh-artist-section`, which strips the card chrome every other section has
 *    (index.html:4203-4226 + style.css:40214-40244). Flat siblings would render
 *    two bordered cards where the design has two bare columns.
 * 2. `youtube_videos` is EXCLUSIVE: search.js:178-186 hides all six sections and
 *    shows only the video grid. It matters because labels are fetched
 *    additively, so without the rule a video search grows a Labels section the
 *    vanilla never showed.
 */
export function SearchResults({
  activeSource,
  dbArtists,
  artists,
  albums,
  tracks,
  labels,
  videos,
  videoProgress,
  ownership,
  artistImages,
  onArtistHref,
  onLabelHref,
  onAlbumClick,
  onTrackClick,
  onTrackPlay,
  onVideoDownload,
}: {
  /** Required, not derived: it is what makes the videos-only rule unforgettable. */
  activeSource: string;
  dbArtists: SearchArtist[];
  artists: SearchArtist[];
  albums: SearchAlbum[];
  tracks: SearchTrack[];
  labels: SearchLabel[];
  videos: SearchVideo[];
  videoProgress: Record<string, VideoProgress>;
  ownership: OwnershipState;
  /** Lazily-resolved images, keyed by artist id. */
  artistImages: Record<string, string>;
  onArtistHref: (artist: SearchArtist) => string;
  onLabelHref: (label: SearchLabel) => string;
  onAlbumClick: (album: SearchAlbum) => void;
  onTrackClick: (track: SearchTrack) => void;
  onTrackPlay: (track: SearchTrack, filePath: string | undefined) => void;
  onVideoDownload: (video: SearchVideo) => void;
}) {
  const { albums: fullAlbums, singlesAndEps } = splitAlbums(albums);

  // The grid carries its own "No music videos found" state, which is the whole
  // reason it can be rendered unconditionally here: a videos search that found
  // nothing has to say so rather than show a blank panel.
  if (activeSource === 'youtube_videos') {
    return <VideoGrid videos={videos} progress={videoProgress} onDownload={onVideoDownload} />;
  }

  const albumCard = (album: SearchAlbum, index: number) => {
    const identity = albumIdentity(album);
    return (
      <CompactItem
        key={`${identity}::${index}`}
        kind="album"
        name={album.name ?? ''}
        meta={[album.artist, album.release_date?.slice(0, 4)].filter(Boolean).join(' • ')}
        placeholder={album.album_type === 'single' || album.album_type === 'ep' ? '🎶' : '💿'}
        image={albumImage(album)}
        badge={sourceBadge(album.source)}
        extraBadges={ownership.ownedAlbums.has(identity) ? [LIBRARY_BADGE] : undefined}
        onClick={() => onAlbumClick(album)}
      />
    );
  };

  // Rendered only when one of the two has results: the wrapper's own
  // margin-bottom would otherwise leave 24px of dead space above Albums, which
  // is exactly what the vanilla's always-present div does.
  const anyArtists = dbArtists.length > 0 || artists.length > 0;

  return (
    <>
      {anyArtists ? (
        <div className="enh-artists-wrapper">
          {/* Already yours — first, because this is an acquisition surface. */}
          <ResultSection
            id="enh-db-artists-section"
            listId="enh-db-artists-list"
            countId="enh-db-artists-count"
            icon="📚"
            title="In Your Library"
            kind="artist"
            sectionClass="enh-artist-section"
            count={dbArtists.length}
          >
            {dbArtists.map((artist, index) => (
              <CompactItem
                key={`${artist.id ?? artist.name}::${index}`}
                kind="artist"
                name={artist.name ?? ''}
                meta={artistMetaLine(artist, true)}
                placeholder="📚"
                image={artistImage(artist)}
                href={onArtistHref(artist)}
                badge={{ text: 'Library', className: 'enh-badge-library' }}
              />
            ))}
          </ResultSection>

          <ResultSection
            id="enh-spotify-artists-section"
            listId="enh-spotify-artists-list"
            countId="enh-spotify-artists-count"
            icon="🎤"
            title="Artists"
            kind="artist"
            sectionClass="enh-artist-section"
            count={artists.length}
          >
            {artists.map((artist, index) => (
              <CompactItem
                key={`${artist.id ?? artist.name}::${index}`}
                kind="artist"
                name={artist.name ?? ''}
                meta={artistMetaLine(artist, false)}
                placeholder="🎤"
                // A lazily-resolved image wins; otherwise whatever the source gave.
                image={artistImages[String(artist.id ?? '')] || artistImage(artist)}
                href={onArtistHref(artist)}
                badge={sourceBadge(artist.source)}
                artistId={artist.id}
                artistName={artist.name}
              />
            ))}
          </ResultSection>
        </div>
      ) : null}

      <ResultSection
        id="enh-albums-section"
        listId="enh-albums-list"
        countId="enh-albums-count"
        icon="💿"
        title="Albums"
        kind="album"
        count={fullAlbums.length}
      >
        {fullAlbums.map(albumCard)}
      </ResultSection>

      <ResultSection
        id="enh-singles-section"
        listId="enh-singles-list"
        countId="enh-singles-count"
        icon="🎶"
        title="Singles & EPs"
        kind="album"
        count={singlesAndEps.length}
      >
        {singlesAndEps.map(albumCard)}
      </ResultSection>

      <ResultSection
        id="enh-tracks-section"
        listId="enh-tracks-list"
        countId="enh-tracks-count"
        icon="🎵"
        title="Tracks"
        kind="track"
        count={tracks.length}
      >
        {tracks.map((track, index) => {
          const identity = trackIdentity(track);
          const filePath = ownership.playableTracks.get(identity);
          const extras = [
            ...(ownership.ownedTracks.has(identity) ? [LIBRARY_BADGE] : []),
            ...(ownership.wishlistTracks.has(identity) ? [WISHLIST_BADGE] : []),
          ];
          return (
            <CompactItem
              key={`${identity}::${index}`}
              kind="track"
              name={track.name ?? ''}
              meta={[track.artist, track.album?.name].filter(Boolean).join(' • ')}
              placeholder="🎵"
              image={track.image_url || albumImage(track.album ?? {})}
              duration={formatDuration(track.duration_ms)}
              badge={sourceBadge(track.source)}
              extraBadges={extras.length ? extras : undefined}
              onClick={() => onTrackClick(track)}
              // An owned track plays from disk; everything else streams. The
              // vanilla achieved this by cloning the button to drop the stream
              // listener — a prop is the same decision, made once.
              onPlay={() => onTrackPlay(track, filePath)}
            />
          );
        })}
      </ResultSection>

      <ResultSection
        id="enh-labels-section"
        listId="enh-labels-list"
        countId="enh-labels-count"
        icon="🏷️"
        title="Labels"
        kind="label"
        count={labels.length}
      >
        {labels.map((label, index) => (
          <CompactItem
            key={`${label.id ?? label.name}::${index}`}
            kind="label"
            name={label.name ?? ''}
            meta={labelMetaLine(label)}
            placeholder="🏷️"
            href={onLabelHref(label)}
          />
        ))}
      </ResultSection>

      {/* No video grid down here on purpose. Only the youtube_videos source ever
          fills `videos`, and that source returns above — so a grid rendered here
          would be a branch that cannot be reached, which is worse than none. The
          vanilla agrees: it hides #enh-videos-section for every other source. */}
    </>
  );
}
