import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SearchAlbum, SearchTrack } from '../-search.types';

import { albumIdentity, trackIdentity } from '../-search.helpers';
import { EMPTY_OWNERSHIP, FilterPills, resultCounts, SearchResults } from './search-results';

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
      onArtistHref={(a, inLibrary) => `/artist-detail/${inLibrary ? 'library' : 'spotify'}/${a.id}`}
      onLabelHref={(l) => `/label-detail/${l.id}`}
      onAlbumClick={vi.fn()}
      onTrackClick={vi.fn()}
      onTrackPlay={vi.fn()}
      onVideoDownload={vi.fn()}
      {...props}
    />,
  );
}

const section = (id: string) => document.getElementById(id) as HTMLElement;
const top = () => document.getElementById('enh-top-result') as HTMLElement;

afterEach(cleanup);

describe('SearchResults sections', () => {
  it('renders nothing for a section with no results', () => {
    // An empty section with a "0" count is noise; the vanilla hid it.
    renderResults();
    expect(section('enh-albums-section')).toBeNull();
    expect(section('enh-tracks-section')).toBeNull();
    expect(top()).toBeNull();
  });

  it('splits albums from singles and EPs into their own sections', () => {
    renderResults({
      albums: [
        album({ id: '1', name: 'Full Length', album_type: 'album' }),
        album({ id: '2', name: 'A Single', album_type: 'single' }),
        album({ id: '3', name: 'An EP', album_type: 'ep' }),
      ],
    });
    expect(within(section('enh-albums-section')).getByText('Full Length')).toBeInTheDocument();
    const singles = section('enh-singles-section');
    expect(singles.textContent).toContain('A Single');
    expect(singles.textContent).toContain('An EP');
    expect(singles.textContent).not.toContain('Full Length');
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
    expect(within(section('enh-albums-section')).queryByText('In library')).toBeNull();
    expect(within(section('enh-singles-section')).getByText('In library')).toBeInTheDocument();
  });

  it('puts library artists first, with their own link and marker', () => {
    // this is where things get acquired: the ones already yours lead
    renderResults({
      dbArtists: [{ id: 7, name: 'Owned Artist' }],
      artists: [{ id: 'sp1', name: 'Found Artist', source: 'spotify' }],
    });
    const faces = section('enh-spotify-artists-section').querySelectorAll('a');
    expect(faces[0].textContent).toContain('Owned Artist');
    expect(faces[0].textContent).toContain('In your library');
    expect(faces[0].getAttribute('href')).toBe('/artist-detail/library/7');
    expect(faces[1].getAttribute('href')).toBe('/artist-detail/spotify/sp1');
  });

  it('renders artists and labels as real links', () => {
    // Links, not click handlers — middle-click and copy-link have to work.
    renderResults({
      artists: [{ id: 'sp1', name: 'Aphex Twin', source: 'spotify' }],
      labels: [{ id: 'l1', name: 'Warp' }],
    });
    // the face and the top result's button both go to one place
    expect(
      within(section('enh-spotify-artists-section')).getByRole('link').getAttribute('href'),
    ).toBe('/artist-detail/spotify/sp1');
    expect(within(top()).getByRole('link', { name: 'Open artist' })).toHaveAttribute(
      'href',
      '/artist-detail/spotify/sp1',
    );
    expect(screen.getByText('Warp').closest('a')?.getAttribute('href')).toBe('/label-detail/l1');
  });

  it('reads a track’s album as the plain string the API sends', () => {
    // sources.py:105 copies Track.album, which is a str. Treating it as an
    // object drops the album from every track's meta line.
    renderResults({
      tracks: [{ id: 't1', name: 'Xtal', artist: 'Aphex Twin', album: 'SAW 85-92' }],
    });
    expect(within(section('enh-tracks-section')).getByText('Aphex Twin • SAW 85-92')).toBeTruthy();
  });

  it('gives an album card its year, or N/A', () => {
    renderResults({
      albums: [album({ release_date: '2001-10-22' }), album({ id: 'a2', release_date: undefined })],
    });
    const grid = section('enh-albums-section');
    expect(within(grid).getByText('Aphex Twin • 2001')).toBeTruthy();
    expect(within(grid).getByText('Aphex Twin • N/A')).toBeTruthy();
  });

  it('hides labels under soulseek as well as youtube_videos', () => {
    // Labels are fetched additively, so both sources need the section hidden
    // explicitly (search.js:429-434).
    renderResults({ activeSource: 'soulseek', labels: [{ id: 'l1', name: 'Warp' }] });
    expect(section('enh-labels-section')).toBeNull();
  });

  it('renders a Playlists section and opens the preview on click', () => {
    const onPlaylistClick = vi.fn();
    renderResults({
      albums: [album()],
      playlists: [
        {
          id: 'pl-1',
          name: 'Chill Vibes',
          creator: 'DJ Chill',
          track_count: 25,
          image_url: 'cover.jpg',
          source: 'deezer',
        },
      ],
      onPlaylistClick,
    });
    const playlists = section('enh-playlists-section');
    expect(playlists.textContent).toContain('by DJ Chill · 25 tracks');
    fireEvent.click(within(playlists).getByRole('button', { name: 'Chill Vibes' }));
    expect(onPlaylistClick).toHaveBeenCalledWith(expect.objectContaining({ id: 'pl-1' }));
  });
});

describe('artist images', () => {
  it('marks a needs-image artist for the lazy loader, and a resolved one not', () => {
    renderResults({
      artists: [
        { id: 'no-img', name: 'Needs', source: 'spotify' },
        { id: 'has-img', name: 'Has', source: 'spotify', image_url: 'https://cdn/a.jpg' },
      ],
    });
    const faces = section('enh-spotify-artists-section');
    expect(
      faces.querySelector('[data-needs-image="true"][data-artist-id="no-img"]'),
    ).not.toBeNull();
    expect(faces.querySelector('[data-needs-image][data-artist-id="has-img"]')).toBeNull();
  });

  it('lets a library artist resolve an image too', () => {
    // a library artist whose server has no thumb is exactly the case that needs it
    renderResults({ dbArtists: [{ id: 7, name: 'Owned' }] });
    expect(
      section('enh-spotify-artists-section').querySelector(
        '[data-needs-image="true"][data-artist-id="7"]',
      ),
    ).not.toBeNull();
  });

  it('prefers a lazily-resolved image over the source one', () => {
    renderResults({
      artists: [{ id: 'sp1', name: 'A', source: 'spotify', image_url: 'https://cdn/old.jpg' }],
      artistImages: { sp1: 'https://cdn/resolved.jpg' },
    });
    for (const img of document.querySelectorAll('img')) {
      expect(img.getAttribute('src')).toBe('https://cdn/resolved.jpg');
    }
  });

  it('falls back to initials when a cover 404s', () => {
    // MusicBrainz cover-art urls are built without probing, so misses are
    // routine; the browser's broken-image glyph is not acceptable.
    renderResults({
      albums: [album({ name: 'Drukqs Two', image_url: 'https://cdn/missing.jpg' })],
    });
    const grid = section('enh-albums-section');
    fireEvent.error(grid.querySelector('img') as HTMLImageElement);
    expect(grid.querySelector('img')).toBeNull();
    expect(grid.textContent).toContain('DT');
  });
});

describe('the top result', () => {
  it('ranks a library artist over a found one, an album and a track', () => {
    renderResults({
      dbArtists: [{ id: 7, name: 'Owned Artist' }],
      artists: [{ id: 'sp1', name: 'Found Artist', source: 'spotify' }],
      albums: [album()],
      tracks: [{ id: 't1', name: 'Xtal' }],
    });
    expect(top().textContent).toContain('Owned Artist');
    expect(top().textContent).toContain('In your library');
    expect(within(top()).getByRole('link', { name: 'Open artist' })).toHaveAttribute(
      'href',
      '/artist-detail/library/7',
    );
  });

  it('is an album when no artist matched, and its button downloads it', () => {
    const onAlbumClick = vi.fn();
    renderResults({ albums: [album()], tracks: [{ id: 't1', name: 'Xtal' }], onAlbumClick });
    expect(top().textContent).toContain('Drukqs');
    fireEvent.click(within(top()).getByRole('button', { name: 'Download' }));
    expect(onAlbumClick).toHaveBeenCalledWith(expect.objectContaining({ id: 'a1' }));
  });

  it('is a track with play and download when only tracks came back', () => {
    const onTrackPlay = vi.fn();
    const onTrackClick = vi.fn();
    const track: SearchTrack = { id: 't1', name: 'Xtal' };
    renderResults({ tracks: [track], onTrackPlay, onTrackClick });
    fireEvent.click(within(top()).getByRole('button', { name: 'Play' }));
    expect(onTrackPlay).toHaveBeenCalledWith(track, undefined);
    fireEvent.click(within(top()).getByRole('button', { name: 'Download' }));
    expect(onTrackClick).toHaveBeenCalledWith(track);
  });
});

describe('track rows', () => {
  const track: SearchTrack = { id: 't1', name: 'Xtal', duration_ms: 60_000 };
  const row = () =>
    within(section('enh-tracks-section')).getByRole('button', { name: /^Xtal/ }) as HTMLElement;

  it('opens the download from the row and from its arrow', () => {
    const onTrackClick = vi.fn();
    renderResults({ tracks: [track], onTrackClick });
    fireEvent.click(row());
    fireEvent.click(within(row()).getByRole('button', { name: 'Download Xtal' }));
    expect(onTrackClick).toHaveBeenCalledTimes(2);
  });

  it('plays a track without also opening its download', () => {
    const onTrackClick = vi.fn();
    const onTrackPlay = vi.fn();
    renderResults({ tracks: [track], onTrackClick, onTrackPlay });
    fireEvent.click(within(row()).getByRole('button', { name: /Stream this track/ }));
    expect(onTrackPlay).toHaveBeenCalledWith(track, undefined);
    expect(onTrackClick).not.toHaveBeenCalled();
  });

  it('hands the whole library row to the play handler for an owned track', () => {
    // playLibraryTrack needs the library id, title, thumb and album/artist
    // names, none of which the search result carries. only the library check
    // knows them.
    const onTrackPlay = vi.fn();
    const libraryRow = {
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
        libraryTracks: new Map([[trackIdentity(track), libraryRow]]),
      },
    });
    fireEvent.click(within(row()).getByRole('button', { name: /Play from library/ }));
    expect(onTrackPlay).toHaveBeenCalledWith(track, libraryRow);
  });

  it('shows a track ONE badge, never both', () => {
    renderResults({
      tracks: [track],
      ownership: {
        ...EMPTY_OWNERSHIP,
        ownedTracks: new Set([trackIdentity(track)]),
        wishlistTracks: new Set([trackIdentity(track)]),
      },
    });
    expect(within(row()).getByText('In library')).toBeTruthy();
    expect(within(row()).queryByText('In wishlist')).toBeNull();
  });

  it('calls a wishlisted track "In wishlist"', () => {
    renderResults({
      tracks: [track],
      ownership: { ...EMPTY_OWNERSHIP, wishlistTracks: new Set([trackIdentity(track)]) },
    });
    expect(within(row()).getByText('In wishlist')).toBeTruthy();
  });
});

describe('the filter', () => {
  const many = (n: number, over: Partial<SearchAlbum> = {}) =>
    Array.from({ length: n }, (_, i) => album({ id: `a${i}`, name: `Album ${i}`, ...over }));
  const tracks = (n: number): SearchTrack[] =>
    Array.from({ length: n }, (_, i) => ({ id: `t${i}`, name: `Track ${i}` }));

  it('previews each section under All and offers the rest', () => {
    const onFilter = vi.fn();
    renderResults({ albums: many(15), tracks: tracks(9), onFilter });
    expect(section('enh-albums-section').querySelectorAll('[role="button"]').length).toBe(12);
    expect(screen.getAllByText(/^Track \d$/).length).toBe(5);

    fireEvent.click(within(section('enh-albums-section')).getByText('Show all 15'));
    expect(onFilter).toHaveBeenCalledWith('albums');
    fireEvent.click(screen.getByText('Show all 9'));
    expect(onFilter).toHaveBeenCalledWith('tracks');
  });

  it('shows one kind in full, and nothing else', () => {
    renderResults({ albums: many(15), tracks: tracks(9), filter: 'tracks' });
    expect(top()).toBeNull();
    expect(section('enh-albums-section')).toBeNull();
    expect(screen.getAllByText(/^Track \d$/).length).toBe(9);
    expect(screen.queryByText(/^Show all/)).toBeNull();
  });

  it('counts what came back, labels out under files', () => {
    const counts = resultCounts({
      dbArtists: [{ id: 1 }],
      artists: [{ id: 2 }],
      albums: [album(), album({ id: 's', album_type: 'single' })],
      tracks: [],
      playlists: [],
      labels: [{ id: 'l' }],
      activeSource: 'spotify',
    });
    expect(counts).toEqual({
      artists: 2,
      albums: 1,
      singles: 1,
      tracks: 0,
      playlists: 0,
      labels: 1,
    });
    expect(
      resultCounts({
        dbArtists: [],
        artists: [],
        albums: [],
        tracks: [],
        playlists: [],
        labels: [{ id: 'l' }],
        activeSource: 'soulseek',
      }).labels,
    ).toBe(0);
  });

  it('pills only the kinds that came back, and presses the current one', () => {
    const onFilter = vi.fn();
    render(
      <FilterPills
        counts={{ artists: 2, albums: 1, singles: 0, tracks: 4, playlists: 0, labels: 0 }}
        filter="tracks"
        onFilter={onFilter}
        servedBy="Deezer"
      />,
    );
    const pills = screen.getAllByRole('button').map((b) => b.textContent);
    expect(pills).toEqual(['All', 'Artists2', 'Albums1', 'Tracks4']);
    expect(screen.getByRole('button', { name: 'Tracks4' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('from Deezer')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    expect(onFilter).toHaveBeenCalledWith('all');
  });
});

describe('videos', () => {
  it('shows ONLY the video grid for the youtube_videos source', () => {
    // Labels matter most: they are fetched additively, so without the rule a
    // video search sprouts a Labels section the vanilla never showed.
    renderResults({
      activeSource: 'youtube_videos',
      videos: [{ video_id: 'v1', title: 'Clip', channel: 'Ch', duration: 215 }],
      labels: [{ id: 'l1', name: 'Warp' }],
      albums: [album()],
      artists: [{ id: 'sp1', name: 'Found', source: 'spotify' }],
    });
    expect(section('enh-videos-section')).not.toBeNull();
    // Seconds, not milliseconds — a different unit from track durations.
    expect(screen.getByText('3:35')).toBeInTheDocument();
    expect(section('enh-labels-section')).toBeNull();
    expect(section('enh-albums-section')).toBeNull();
    expect(section('enh-spotify-artists-section')).toBeNull();
    expect(top()).toBeNull();
  });

  it('says so when a video search found nothing, rather than going blank', () => {
    renderResults({ activeSource: 'youtube_videos', videos: [] });
    expect(screen.getByText('No music videos found.')).toBeInTheDocument();
  });

  it('never renders a video grid under a metadata source', () => {
    renderResults({ albums: [album()] });
    expect(section('enh-videos-section')).toBeNull();
  });
});
