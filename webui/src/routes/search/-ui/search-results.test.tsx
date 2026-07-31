import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SearchAlbum, SearchTrack } from '../-search.types';

import { albumIdentity, trackIdentity } from '../-search.helpers';
import { EMPTY_OWNERSHIP, SearchResults } from './search-results';

const album = (over: Partial<SearchAlbum> = {}): SearchAlbum => ({
  id: 'a1',
  name: 'Drukqs',
  artist: 'Aphex Twin',
  album_type: 'album',
  source: 'spotify',
  ...over,
});

function renderResults(props: Partial<Parameters<typeof SearchResults>[0]> = {}) {
  return render(
    <SearchResults
      activeSource="spotify"
      dbArtists={[]}
      artists={[]}
      albums={[]}
      tracks={[]}
      labels={[]}
      videos={[]}
      videoProgress={{}}
      ownership={EMPTY_OWNERSHIP}
      artistImages={{}}
      onArtistHref={(a) => `/artist-detail/spotify/${a.id}`}
      onLabelHref={(l) => `/label-detail/${l.id}`}
      onAlbumClick={vi.fn()}
      onTrackClick={vi.fn()}
      onTrackPlay={vi.fn()}
      onVideoDownload={vi.fn()}
      {...props}
    />,
  );
}

afterEach(cleanup);

describe('SearchResults', () => {
  it('renders nothing for a section with no results', () => {
    // An empty section with a "0" count is noise; the vanilla hid it.
    renderResults();
    expect(document.getElementById('enh-albums-section')).toBeNull();
    expect(document.getElementById('enh-tracks-section')).toBeNull();
  });

  it('splits albums from singles and EPs into their own sections', () => {
    renderResults({
      albums: [
        album({ id: '1', name: 'LP', album_type: 'album' }),
        album({ id: '2', name: 'A Single', album_type: 'single' }),
        album({ id: '3', name: 'An EP', album_type: 'ep' }),
      ],
    });

    const albumsList = document.getElementById('enh-albums-list');
    const singlesList = document.getElementById('enh-singles-list');
    expect(albumsList?.textContent).toContain('LP');
    expect(singlesList?.textContent).toContain('A Single');
    expect(singlesList?.textContent).toContain('An EP');
    expect(document.getElementById('enh-albums-count')?.textContent).toBe('1');
    expect(document.getElementById('enh-singles-count')?.textContent).toBe('2');
  });

  it('badges the RIGHT album when albums and singles interleave', () => {
    // The whole point of keying ownership by identity. In document order this
    // owned single is the LAST card; in request order it is the middle row.
    const rows = [
      album({ id: 'A1', name: 'First LP', album_type: 'album' }),
      album({ id: 'S1', name: 'Owned Single', album_type: 'single' }),
      album({ id: 'A2', name: 'Second LP', album_type: 'album' }),
    ];
    renderResults({
      albums: rows,
      ownership: { ...EMPTY_OWNERSHIP, ownedAlbums: new Set([albumIdentity(rows[1])]) },
    });

    const badges = document.querySelectorAll('.enh-item-lib-badge');
    expect(badges).toHaveLength(1);
    // The badge sits on the single, not on whichever card shares its index.
    expect(badges[0].closest('.enh-compact-item')?.textContent).toContain('Owned Single');
  });

  it('keeps the compound card classes the stylesheet targets', () => {
    // `.album-card` alone collides with the global one used by the
    // artist-detail and library discographies; the compound is the guard.
    renderResults({ albums: [album()], tracks: [{ id: 't1', name: 'Xtal' }] });
    expect(document.querySelector('.enh-compact-item.album-card')).not.toBeNull();
    expect(document.querySelector('.enh-compact-item.track-item')).not.toBeNull();
  });

  it('renders artists and labels as real links', () => {
    // Links, not click handlers — middle-click and copy-link have to work.
    renderResults({
      artists: [{ id: 'sp1', name: 'Aphex Twin', source: 'spotify' }],
      labels: [{ id: 'l1', name: 'Warp' }],
    });
    expect(screen.getByText('Aphex Twin').closest('a')?.getAttribute('href')).toBe(
      '/artist-detail/spotify/sp1',
    );
    expect(screen.getByText('Warp').closest('a')?.getAttribute('href')).toBe('/label-detail/l1');
  });

  it('gives a label card the artist styling it piggybacks on', () => {
    renderResults({ labels: [{ id: 'l1', name: 'Warp' }] });
    const card = screen.getByText('Warp').closest('.enh-compact-item');
    expect(card?.className).toContain('label-card');
    expect(card?.className).toContain('artist-card');
  });

  it('names each artist section in its cards, without inventing a count', () => {
    // db_artists carry only id/name/image_url, so any count would read 0.
    renderResults({
      dbArtists: [{ id: 1, name: 'Owned' }],
      artists: [{ id: 'sp1', name: 'Found', source: 'deezer' }],
    });
    const metas = [...document.querySelectorAll('.enh-item-meta')].map((el) => el.textContent);
    expect(metas).toEqual(['In Your Library', 'Artist']);
  });

  it('badges an artist with the source being VIEWED, not the row’s own', () => {
    // A Deezer row read under the Spotify tab is still a Spotify result set; the
    // vanilla derives the badge from the active tab (search.js:465-468).
    renderResults({
      activeSource: 'itunes',
      artists: [{ id: 'sp1', name: 'Found', source: 'deezer' }],
    });
    const badge = document.querySelector('.enh-item-badge');
    expect(badge?.textContent).toBe('Apple Music');
    expect(badge?.className).toContain('enh-badge-itunes');
  });

  it('leaves albums, singles and tracks unbadged', () => {
    // Only the Artists section gets a source badge — search.js passes
    // `sourceBadge` to that one call. A badge on every card is noise the design
    // dropped, and the lit picker icon already says where results came from.
    renderResults({
      albums: [album(), album({ id: 's1', album_type: 'single' })],
      tracks: [{ id: 't1', name: 'Xtal', artist: 'Aphex Twin', album: 'SAW 85-92' }],
    });
    expect(document.querySelector('.enh-item-badge')).toBeNull();
  });

  it('reads a track’s album as the plain string the API sends', () => {
    // sources.py:105 copies Track.album, which is a str. Treating it as an
    // object drops the album from every track's meta line.
    renderResults({
      tracks: [{ id: 't1', name: 'Xtal', artist: 'Aphex Twin', album: 'SAW 85-92' }],
    });
    expect(document.querySelector('.enh-item-meta')?.textContent).toBe('Aphex Twin • SAW 85-92');
  });

  it('gives an album card its year, or N/A', () => {
    renderResults({
      albums: [album({ release_date: '2001-10-22' }), album({ id: 'a2', release_date: undefined })],
    });
    const metas = [...document.querySelectorAll('.enh-item-meta')].map((el) => el.textContent);
    expect(metas).toEqual(['Aphex Twin • 2001', 'Aphex Twin • N/A']);
  });

  it('hides labels under soulseek as well as youtube_videos', () => {
    // Labels are fetched additively, so both sources need the section hidden
    // explicitly (search.js:429-434).
    renderResults({ activeSource: 'soulseek', labels: [{ id: 'l1', name: 'Warp' }] });
    expect(document.getElementById('enh-labels-section')).toBeNull();
  });

  it('marks a needs-image artist for the lazy loader, and a resolved one not', () => {
    renderResults({
      artists: [
        { id: 'no-img', name: 'Needs', source: 'spotify' },
        { id: 'has-img', name: 'Has', source: 'spotify', image_url: 'https://cdn/a.jpg' },
      ],
    });
    expect(
      document.querySelector('[data-artist-id="no-img"]')?.getAttribute('data-needs-image'),
    ).toBe('true');
    expect(
      document.querySelector('[data-artist-id="has-img"]')?.getAttribute('data-needs-image'),
    ).toBe('false');
  });

  it('prefers a lazily-resolved image over the source one', () => {
    renderResults({
      artists: [{ id: 'sp1', name: 'A', source: 'spotify', image_url: 'https://cdn/old.jpg' }],
      artistImages: { sp1: 'https://cdn/resolved.jpg' },
    });
    expect(document.querySelector('img')?.getAttribute('src')).toBe('https://cdn/resolved.jpg');
  });

  it('falls back to the placeholder when an image 404s', () => {
    // MusicBrainz cover-art urls are built without probing, so misses are
    // routine; the browser's broken-image glyph is not acceptable.
    renderResults({ albums: [album({ image_url: 'https://cdn/missing.jpg' })] });
    const img = document.querySelector('img') as HTMLImageElement;
    fireEvent.error(img);
    expect(document.querySelector('.album-placeholder')?.textContent).toBe('💿');
  });

  it('opens an album, and a track, through their own handlers', () => {
    const onAlbumClick = vi.fn();
    const onTrackClick = vi.fn();
    renderResults({
      albums: [album()],
      tracks: [{ id: 't1', name: 'Xtal' }],
      onAlbumClick,
      onTrackClick,
    });

    fireEvent.click(screen.getByText('Drukqs'));
    fireEvent.click(screen.getByText('Xtal'));
    expect(onAlbumClick).toHaveBeenCalledOnce();
    expect(onTrackClick).toHaveBeenCalledOnce();
  });

  it('plays a track without also opening its download modal', () => {
    const onTrackClick = vi.fn();
    const onTrackPlay = vi.fn();
    const track: SearchTrack = { id: 't1', name: 'Xtal', duration_ms: 60_000 };
    renderResults({ tracks: [track], onTrackClick, onTrackPlay });

    fireEvent.click(document.querySelector('.enh-item-play-btn') as HTMLElement);
    expect(onTrackPlay).toHaveBeenCalledOnce();
    // The card's own click must not fire underneath the button.
    expect(onTrackClick).not.toHaveBeenCalled();
  });

  it('hands the whole library row to the play handler for an owned track', () => {
    // playLibraryTrack needs the library id, title, thumb and album/artist
    // names, none of which the search result carries — only the library check
    // knows them. The vanilla swapped the button's listener by cloning it; a
    // prop is the same decision made once.
    const onTrackPlay = vi.fn();
    const track: SearchTrack = { id: 't1', name: 'Xtal', duration_ms: 60_000 };
    const row = {
      in_library: true,
      track_id: 99,
      title: 'Xtal',
      file_path: '/music/xtal.flac',
      album_title: 'SAW 85-92',
      artist_name: 'Aphex Twin',
    };
    renderResults({
      tracks: [track],
      onTrackPlay,
      ownership: {
        ...EMPTY_OWNERSHIP,
        ownedTracks: new Set([trackIdentity(track)]),
        libraryTracks: new Map([[trackIdentity(track), row]]),
      },
    });

    expect(document.querySelector('.enh-item-play-btn')?.getAttribute('title')).toBe(
      'Play from library',
    );
    fireEvent.click(document.querySelector('.enh-item-play-btn') as HTMLElement);
    expect(onTrackPlay).toHaveBeenCalledWith(track, row);
  });

  it('streams a track it does not own, and says so', () => {
    const onTrackPlay = vi.fn();
    const track: SearchTrack = { id: 't1', name: 'Xtal', duration_ms: 60_000 };
    renderResults({ tracks: [track], onTrackPlay });

    expect(document.querySelector('.enh-item-play-btn')?.getAttribute('title')).toBe(
      'Stream this track',
    );
    fireEvent.click(document.querySelector('.enh-item-play-btn') as HTMLElement);
    expect(onTrackPlay).toHaveBeenCalledWith(track, undefined);
  });

  it('shows a track ONE badge, never both', () => {
    // The vanilla's else-if. Both badges are absolutely positioned at the same
    // corner (style.css:40405/40436), so two would sit on top of each other.
    const track: SearchTrack = { id: 't1', name: 'Xtal' };
    renderResults({
      tracks: [track],
      ownership: {
        ...EMPTY_OWNERSHIP,
        ownedTracks: new Set([trackIdentity(track)]),
        wishlistTracks: new Set([trackIdentity(track)]),
      },
    });
    expect(document.querySelector('.enh-item-lib-badge')).not.toBeNull();
    expect(document.querySelector('.enh-item-wishlist-badge')).toBeNull();
  });

  it('calls a wishlisted track "In Wishlist"', () => {
    const track: SearchTrack = { id: 't1', name: 'Xtal' };
    renderResults({
      tracks: [track],
      ownership: { ...EMPTY_OWNERSHIP, wishlistTracks: new Set([trackIdentity(track)]) },
    });
    expect(document.querySelector('.enh-item-wishlist-badge')?.textContent).toBe('In Wishlist');
  });

  it('staggers the badges with one counter across all three sections', () => {
    // The badges animate on arrival (libBadgeFadeIn); the vanilla's 30ms
    // setTimeout ladder made them cascade instead of popping together, and it
    // used ONE counter for albums, singles and tracks alike.
    const owned = [
      album({ id: 'a1', album_type: 'album' }),
      album({ id: 's1', album_type: 'single' }),
    ];
    const track: SearchTrack = { id: 't1', name: 'Xtal' };
    renderResults({
      albums: owned,
      tracks: [track],
      ownership: {
        ...EMPTY_OWNERSHIP,
        ownedAlbums: new Set(owned.map(albumIdentity)),
        ownedTracks: new Set([trackIdentity(track)]),
      },
    });

    const delays = [...document.querySelectorAll('.enh-item-lib-badge')].map(
      (el) => (el as HTMLElement).style.animationDelay,
    );
    expect(delays).toEqual(['0ms', '30ms', '60ms']);
  });

  it('does not spend a stagger slot on an unbadged card', () => {
    const rows = [album({ id: 'a1' }), album({ id: 'a2' })];
    renderResults({
      albums: rows,
      ownership: { ...EMPTY_OWNERSHIP, ownedAlbums: new Set([albumIdentity(rows[1])]) },
    });
    // The owned album is second, but it is the FIRST badge, so it starts at 0.
    const badge = document.querySelector('.enh-item-lib-badge') as HTMLElement;
    expect(badge.style.animationDelay).toBe('0ms');
  });

  it('lets a library artist resolve an image too', () => {
    // renderCompactSection stamped the lazy-load attributes on every artist card
    // with an id, library ones included — a server with no thumb is exactly the
    // case that needs resolving.
    renderResults({ dbArtists: [{ id: 7, name: 'Owned' }] });
    const card = document.querySelector('[data-artist-id="7"]');
    expect(card).not.toBeNull();
    expect(card?.getAttribute('data-needs-image')).toBe('true');
    expect(card?.getAttribute('data-artist-name')).toBe('Owned');
  });

  it('shows a library artist a resolved image', () => {
    renderResults({
      dbArtists: [{ id: 7, name: 'Owned' }],
      artistImages: { 7: 'https://cdn/a.jpg' },
    });
    expect(document.querySelector('img')?.getAttribute('src')).toBe('https://cdn/a.jpg');
  });

  it('pairs the two artist sections inside the wrapper that lays them out', () => {
    // Without .enh-artists-wrapper the two sections stack, and without
    // .enh-artist-section each grows the card chrome (border, background,
    // shadow) that the design strips from exactly these two.
    renderResults({
      dbArtists: [{ id: 1, name: 'Owned' }],
      artists: [{ id: 'sp1', name: 'Found', source: 'spotify' }],
    });
    const wrapper = document.querySelector('.enh-artists-wrapper');
    expect(wrapper).not.toBeNull();
    expect(wrapper?.querySelectorAll('.enh-dropdown-section')).toHaveLength(2);
    expect(document.getElementById('enh-db-artists-section')?.className).toContain(
      'enh-artist-section',
    );
    expect(document.getElementById('enh-spotify-artists-section')?.className).toContain(
      'enh-artist-section',
    );
    // Albums are NOT in the wrapper and keep the normal card chrome.
    expect(document.getElementById('enh-albums-section')).toBeNull();
  });

  it('leaves out the wrapper entirely when neither artist section has results', () => {
    // The wrapper carries its own 24px margin-bottom, so an empty one is a gap
    // above Albums with nothing in it.
    renderResults({ albums: [album()] });
    expect(document.querySelector('.enh-artists-wrapper')).toBeNull();
  });

  it('keeps the wrapper when only one of the two has results', () => {
    renderResults({ artists: [{ id: 'sp1', name: 'Found', source: 'spotify' }] });
    expect(document.querySelector('.enh-artists-wrapper')).not.toBeNull();
    expect(document.getElementById('enh-db-artists-section')).toBeNull();
  });

  it('shows ONLY the video grid for the youtube_videos source', () => {
    // search.js:178-186 hides all six sections for this source. Labels matter
    // most: they are fetched additively, so without the rule a video search
    // sprouts a Labels section the vanilla never showed.
    renderResults({
      activeSource: 'youtube_videos',
      videos: [{ video_id: 'v1', title: 'Clip', channel: 'Ch', duration: 215 }],
      labels: [{ id: 'l1', name: 'Warp' }],
      albums: [album()],
      artists: [{ id: 'sp1', name: 'Found', source: 'spotify' }],
    });

    expect(document.getElementById('enh-videos-section')).not.toBeNull();
    // Seconds, not milliseconds — a different unit from track durations.
    expect(screen.getByText('3:35')).toBeInTheDocument();
    expect(document.getElementById('enh-labels-section')).toBeNull();
    expect(document.getElementById('enh-albums-section')).toBeNull();
    expect(document.querySelector('.enh-artists-wrapper')).toBeNull();
  });

  it('says so when a video search found nothing, rather than going blank', () => {
    renderResults({ activeSource: 'youtube_videos', videos: [] });
    expect(document.getElementById('enh-videos-section')).not.toBeNull();
    expect(screen.getByText('No music videos found')).toBeInTheDocument();
  });

  it('never renders a video grid under a metadata source', () => {
    renderResults({ albums: [album()] });
    expect(document.getElementById('enh-videos-section')).toBeNull();
  });
});
