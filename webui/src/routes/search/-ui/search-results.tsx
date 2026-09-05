import type {
  LibraryCheckTrack,
  SearchAlbum,
  SearchArtist,
  SearchLabel,
  SearchTrack,
  SearchVideo,
} from '../-search.types';
import type { VideoProgress } from './video-grid';

import {
  albumIdentity,
  albumMetaLine,
  artistMetaLine,
  formatDuration,
  labelMetaLine,
  splitAlbums,
  trackIdentity,
  trackMetaLine,
} from '../-search.helpers';
import { SOURCE_LABELS } from '../-search.types';
import { CompactItem, ResultSection } from './compact-item';
import { VideoGrid } from './video-grid';

/** Ownership carried by IDENTITY, never by list position. See the helpers. */
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

const LIBRARY_BADGE = { text: 'In Library', className: 'enh-item-lib-badge' };
const WISHLIST_BADGE = { text: 'In Wishlist', className: 'enh-item-wishlist-badge' };

/**
 * The badges cascaded in 30ms apart in the vanilla, which staggered its
 * setTimeout inserts with ONE counter shared across albums, singles and tracks
 * (search.js:586-644). They animate on arrival (libBadgeFadeIn,
 * style.css:40417), so appearing together is a visibly different effect —
 * an animation-delay is the same thing said declaratively.
 */
const BADGE_STAGGER_MS = 30;

/**
 * The badge naming a source.
 *
 * Built from the ACTIVE source, not from each item's own `source` field, and
 * worn only by the Artists section — search.js:465-499 passes `sourceBadge` to
 * that one renderCompactSection call and to no other. Albums, singles and tracks
 * carry no badge at all; the lit icon in the picker is what says where results
 * came from, and a badge on every card was noise the design deliberately
 * dropped.
 */
function sourceBadge(source: string | undefined) {
  const info = source ? SOURCE_LABELS[source] : undefined;
  const fallback = SOURCE_LABELS.spotify;
  const chosen = info ?? fallback;
  return { text: chosen.text, className: chosen.badgeClass };
}

function artistImage(artist: SearchArtist): string | undefined {
  return artist.image_url || artist.images?.[0]?.url || undefined;
}

function albumImage(album: SearchAlbum): string | undefined {
  return album.image_url || album.images?.[0]?.url || undefined;
}

/** True for the two sources that show no metadata sections at all. */
function suppressesLabels(source: string): boolean {
  // search.js:429-434 — the Labels section is fetched additively, so it has to
  // be hidden explicitly for both of these, not merely left unfilled.
  return source === 'youtube_videos' || source === 'soulseek';
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
  /**
   * `inLibrary` decides the URL, not just the styling: a library artist resolves
   * under /artist-detail/library/<id>, a found one under its metadata source.
   */
  onArtistHref: (artist: SearchArtist, inLibrary: boolean) => string;
  onLabelHref: (label: SearchLabel) => string;
  onAlbumClick: (album: SearchAlbum) => void;
  onTrackClick: (track: SearchTrack) => void;
  /** The library row is present only for an owned track with a local file. */
  onTrackPlay: (track: SearchTrack, libraryRow: LibraryCheckTrack | undefined) => void;
  onVideoDownload: (video: SearchVideo) => void;
}) {
  const { albums: fullAlbums, singlesAndEps } = splitAlbums(albums);

  // One counter across all three badged sections, consumed in render order —
  // albums, then singles, then tracks — which is the order the vanilla's loops
  // ran in. Reset every render, so it is deterministic rather than stateful.
  let badgeSlot = 0;
  const nextBadgeDelay = () => `${badgeSlot++ * BADGE_STAGGER_MS}ms`;

  // The grid carries its own "No music videos found" state, which is the whole
  // reason it can be rendered unconditionally here: a videos search that found
  // nothing has to say so rather than show a blank panel.
  if (activeSource === 'youtube_videos') {
    return <VideoGrid videos={videos} progress={videoProgress} onDownload={onVideoDownload} />;
  }

  const albumCard = (album: SearchAlbum, index: number) => {
    const identity = albumIdentity(album);
    const owned = ownership.ownedAlbums.has(identity);
    return (
      <CompactItem
        key={`${identity}::${index}`}
        kind="album"
        name={album.name ?? ''}
        meta={albumMetaLine(album)}
        placeholder={album.album_type === 'single' || album.album_type === 'ep' ? '🎶' : '💿'}
        image={albumImage(album)}
        // No source badge: only the Artists section wears one. See sourceBadge.
        extraBadges={owned ? [{ ...LIBRARY_BADGE, delay: nextBadgeDelay() }] : undefined}
        onClick={() => onAlbumClick(album)}
      />
    );
  };

  // Rendered only when one of the two has results: the wrapper's own
  // margin-bottom would otherwise leave 24px of dead space above Albums, which
  // is exactly what the vanilla's always-present div does.
  const anyArtists = dbArtists.length > 0 || artists.length > 0;

  // The spotlight: the best match gets the big card. Artists outrank albums
  // outrank tracks — the same priority the section order implies.
  const topArtist = dbArtists[0] ?? artists[0];
  const topAlbum = topArtist ? undefined : (fullAlbums[0] ?? singlesAndEps[0]);
  const topTrack = topArtist || topAlbum ? undefined : tracks[0];
  const spotlight = topArtist
    ? {
        kind: topArtist === dbArtists[0] ? 'Artist · In your library' : 'Artist',
        name: topArtist.name ?? '',
        image: artistImages[String(topArtist.id ?? '')] || artistImage(topArtist),
        round: true,
        href: onArtistHref(topArtist, topArtist === dbArtists[0]),
        onOpen: undefined as (() => void) | undefined,
      }
    : topAlbum
      ? {
          kind:
            topAlbum.album_type === 'single' || topAlbum.album_type === 'ep'
              ? 'Single / EP'
              : 'Album',
          name: topAlbum.name ?? '',
          image: albumImage(topAlbum),
          round: false,
          href: undefined,
          onOpen: () => onAlbumClick(topAlbum),
        }
      : topTrack
        ? {
            kind: 'Track',
            name: topTrack.name ?? '',
            image: topTrack.image_url || undefined,
            round: false,
            href: undefined,
            onOpen: () => onTrackClick(topTrack),
          }
        : null;

  const spotlightBody = spotlight ? (
    <>
      {spotlight.image ? (
        <img
          className={`enh-top-result-art${spotlight.round ? ' enh-top-result-art--round' : ''}`}
          src={spotlight.image}
          alt=""
          onError={(event) => {
            event.currentTarget.style.display = 'none';
          }}
        />
      ) : (
        <div
          className={`enh-top-result-art enh-top-result-art--ph${spotlight.round ? ' enh-top-result-art--round' : ''}`}
        >
          {spotlight.round ? '🎤' : '💿'}
        </div>
      )}
      <div className="enh-top-result-text">
        <div className="enh-top-result-name">{spotlight.name}</div>
        <div className="enh-top-result-kind">{spotlight.kind}</div>
      </div>
    </>
  ) : null;

  return (
    <>
      {spotlight ? (
        <div className="enh-dropdown-section" id="enh-top-result">
          <div className="enh-section-header">
            <h4 className="enh-section-title">Top result</h4>
          </div>
          {spotlight.href ? (
            <a className="enh-top-result-card" href={spotlight.href}>
              {spotlightBody}
            </a>
          ) : (
            <div
              className="enh-top-result-card"
              role="button"
              tabIndex={0}
              onClick={spotlight.onOpen}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  spotlight.onOpen?.();
                }
              }}
            >
              {spotlightBody}
            </div>
          )}
        </div>
      ) : null}

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
                meta={artistMetaLine(true)}
                placeholder="📚"
                image={artistImages[String(artist.id ?? '')] || artistImage(artist)}
                href={onArtistHref(artist, true)}
                badge={{ text: 'Library', className: 'enh-badge-library' }}
                // Library artists take part in lazy image loading too:
                // renderCompactSection stamped these attributes on EVERY artist
                // card with an id, and a library artist whose server has no
                // thumb is exactly the case that needs resolving.
                // iss29-B04c: a lib2-native row's id is not resolvable by the
                // provider image endpoint — withholding it keeps the
                // placeholder instead of another artist's face.
                artistId={artist.image_is_native ? undefined : artist.id}
                artistName={artist.name}
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
                meta={artistMetaLine(false)}
                placeholder="🎤"
                // A lazily-resolved image wins; otherwise whatever the source gave.
                image={artistImages[String(artist.id ?? '')] || artistImage(artist)}
                href={onArtistHref(artist, false)}
                // The badge names the source being VIEWED, not the row's own
                // `source` field — the vanilla derives it from the active tab.
                badge={sourceBadge(activeSource)}
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
          const libraryRow = ownership.libraryTracks.get(identity);
          // Either/or, never both — the vanilla's else-if. On a card where both
          // badges are absolutely positioned at the same corner, two would
          // simply cover each other.
          const owned = ownership.ownedTracks.has(identity);
          const wished = !owned && ownership.wishlistTracks.has(identity);
          const extras = owned
            ? [{ ...LIBRARY_BADGE, delay: nextBadgeDelay() }]
            : wished
              ? [{ ...WISHLIST_BADGE, delay: nextBadgeDelay() }]
              : [];
          return (
            <CompactItem
              key={`${identity}::${index}`}
              kind="track"
              name={track.name ?? ''}
              meta={trackMetaLine(track)}
              placeholder="🎵"
              image={track.image_url}
              duration={formatDuration(track.duration_ms)}
              extraBadges={extras.length ? extras : undefined}
              onClick={() => onTrackClick(track)}
              // An owned track plays from disk; everything else streams. The
              // vanilla achieved this by cloning the button to drop the stream
              // listener — a prop is the same decision, made once.
              onPlay={() => onTrackPlay(track, libraryRow)}
              playTitle={libraryRow ? 'Play from library' : 'Stream this track'}
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
        count={suppressesLabels(activeSource) ? 0 : labels.length}
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
