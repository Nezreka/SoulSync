import { http, HttpResponse, type RequestHandler } from 'msw';

/**
 * API fixtures for the visual baselines, as MSW handlers (the idiom the vitest
 * route tests use). Anything not handled here gets `{}`, so most pages show
 * their empty state; the routes below get data so their cards and rows are
 * covered too. Keep image urls null: off-origin images are aborted anyway.
 */

const PROFILE = { id: 1, name: 'Admin', is_admin: true };

/** Every page needs these to get past the profile picker and setup wizard. */
export const shellHandlers: RequestHandler[] = [
  http.get('*/api/profiles/current', () => HttpResponse.json({ success: true, profile: PROFILE })),
  http.get('*/api/profiles', () => HttpResponse.json({ success: true, profiles: [PROFILE] })),
  http.get('*/api/setup/status', () => HttpResponse.json({ setup_complete: true })),
  // The sidebar weather (and its holiday decorations) follows the real sky and
  // date, so keep it off; `{}` would hide it too, but only by accident.
  http.get('*/api/weather', () => HttpResponse.json({ enabled: false })),
  http.get('*/api/auth/plex/available', () => HttpResponse.json({ enabled: false })),
];

const ARTIST_NAMES = [
  'Aphex Twin',
  'Boards of Canada',
  'Burial',
  'Caribou',
  'Four Tet',
  'Jon Hopkins',
  'Massive Attack',
  'Portishead',
  'Radiohead',
  'Röyksopp',
  'Squarepusher',
  'Thom Yorke',
];

const libraryArtists = http.get('*/api/library/artists', () =>
  HttpResponse.json({
    success: true,
    artists: ARTIST_NAMES.map((name, i) => ({
      id: i + 1,
      name,
      image_url: null,
      track_count: 10 + i * 7,
      is_watched: i % 3 === 0,
      upgradable_count: i % 4 === 0 ? 2 : 0,
      spotify_artist_id: i % 2 ? `sp${i}` : null,
      musicbrainz_id: `mb${i}`,
      deezer_id: i % 3 ? i : null,
    })),
    pagination: {
      page: 1,
      limit: 75,
      total_count: ARTIST_NAMES.length,
      total_pages: 1,
      has_prev: false,
      has_next: false,
    },
    upgradable_total: 6,
  }),
);

const watchlistArtists = [
  http.get('*/api/watchlist/count', () => HttpResponse.json({ success: true, count: 6 })),
  http.get('*/api/watchlist/artists', () =>
    HttpResponse.json({
      success: true,
      artists: ARTIST_NAMES.slice(0, 6).map((artist_name, i) => ({
        id: i + 1,
        artist_name,
        date_added: '2025-01-01 10:00:00',
        last_scan_timestamp: i % 2 ? '2025-01-14 10:00:00' : null,
        created_at: '2025-01-01 10:00:00',
        updated_at: '2025-01-01 10:00:00',
        image_url: null,
        spotify_artist_id: `sp${i}`,
        itunes_artist_id: null,
        deezer_artist_id: i % 2 ? `dz${i}` : null,
        discogs_artist_id: null,
        musicbrainz_artist_id: `mb${i}`,
        amazon_artist_id: null,
      })),
    }),
  ),
];

const issues = [
  http.get('*/api/issues/counts', () =>
    HttpResponse.json({
      success: true,
      counts: { open: 2, in_progress: 1, resolved: 0, dismissed: 0, total: 3 },
    }),
  ),
  http.get('*/api/issues', () =>
    HttpResponse.json({
      success: true,
      total: 2,
      issues: [
        {
          id: 7,
          profile_id: 1,
          entity_type: 'album',
          entity_id: '15',
          category: 'wrong_metadata',
          title: 'Bad tags',
          description: 'Album title is wrong',
          status: 'open',
          priority: 'normal',
          snapshot_data: { title: 'Album Name', artist_name: 'Artist', format: 'FLAC' },
          created_at: '2025-01-10 10:30:00',
          reporter_name: 'Ada',
        },
        {
          id: 8,
          profile_id: 1,
          entity_type: 'track',
          entity_id: '99',
          category: 'audio_quality',
          title: 'Clipping in the chorus',
          description: 'Audible distortion around 1:20',
          status: 'in_progress',
          priority: 'high',
          snapshot_data: { title: 'Track Name', artist_name: 'Artist', format: 'MP3' },
          created_at: '2025-01-12 08:00:00',
          reporter_name: 'Admin',
        },
      ],
    }),
  ),
];

const stats = [
  http.get('*/api/stats/cached', () =>
    HttpResponse.json({
      success: true,
      overview: {
        total_plays: 24,
        total_time_ms: 6_600_000,
        unique_artists: 3,
        unique_albums: 4,
        unique_tracks: 12,
      },
      previous: {
        total_plays: 12,
        total_time_ms: 3_300_000,
        unique_artists: 3,
        unique_albums: 2,
        unique_tracks: 6,
      },
      clock: {
        grid: Array.from({ length: 7 }, (_, d) =>
          Array.from({ length: 24 }, (_, h) => (d === 3 && h === 21 ? 9 : (d + h) % 5)),
        ),
        peak: { weekday: 3, hour: 21, plays: 9 },
        total: 9,
      },
      rhythm: {
        current_streak: 4,
        longest_streak: 11,
        busiest_day: { date: '2025-01-12', plays: 9 },
        active_days: 20,
      },
      own_vs_play: [
        { genre: 'Metal', owned_pct: 80, played_pct: 0, gap: -80, owned_tracks: 8, plays: 0 },
        { genre: 'Pop', owned_pct: 20, played_pct: 100, gap: 80, owned_tracks: 2, plays: 4 },
      ],
      neglected: [{ id: 1, name: 'Dusty Record', artist: 'Someone', tracks: 11 }],
      top_artists: [
        { id: 7, name: 'Artist A', play_count: 10 },
        { id: 8, name: 'Artist B', play_count: 6 },
      ],
      top_albums: [],
      top_tracks: [],
      timeline: [
        { date: 'Jan 10', plays: 4 },
        { date: 'Jan 11', plays: 8 },
        { date: 'Jan 12', plays: 12 },
      ],
      genres: [
        { genre: 'House', play_count: 10, percentage: 60 },
        { genre: 'Ambient', play_count: 6, percentage: 40 },
      ],
      recent: [{ title: 'Track A', artist: 'Artist A', played_at: '2025-01-14T08:00:00Z' }],
      health: { total_tracks: 12, format_breakdown: { FLAC: 10, MP3: 2 } },
    }),
  ),
  http.get('*/api/listening-stats/status', () =>
    HttpResponse.json({ stats: { last_poll: '2025-01-14 10:00:00' } }),
  ),
  http.get('*/status', () =>
    HttpResponse.json({ media_server: { type: 'plex', connected: true } }),
  ),
];

/** shared-helpers.js reads this as a server-sent event stream. */
const similarArtistStream = [
  ...['Autechre', 'Squarepusher', 'Boards of Canada', 'Plaid', 'µ-Ziq', 'Luke Vibert'].map(
    (name, i) => ({ artist: { id: `sim${i}`, name, image_url: null, source: 'spotify' } }),
  ),
  { complete: true, total: 6 },
]
  .map((message) => `data: ${JSON.stringify(message)}\n\n`)
  .join('');

const artistDetail = [
  http.get(
    '*/api/artist/similar/:name/stream',
    () =>
      new HttpResponse(similarArtistStream, { headers: { 'Content-Type': 'text/event-stream' } }),
  ),
  http.get('*/api/artist-detail/:id', () =>
    HttpResponse.json({
      success: true,
      artist: { id: 42, name: 'Aphex Twin', server_source: 'plex' },
      discography: {
        albums: [
          { id: 1, title: 'Selected Ambient Works 85-92', owned: true, image_url: null },
          { id: 2, title: 'Richard D. James Album', owned: true, image_url: null },
          { id: 3, title: 'Drukqs', owned: false, image_url: null },
          { id: 4, title: 'Syro', owned: false, image_url: null },
        ],
        source: 'spotify',
      },
    }),
  ),
];

const AUDIOBOOKS: [string, string, string, string, number][] = [
  ['Project Hail Mary', 'Andy Weir', 'Ray Porter', 'Science Fiction', 970],
  ['The Hobbit', 'J.R.R. Tolkien', 'Andy Serkis', 'Fantasy', 641],
  ['Dune', 'Frank Herbert', 'Scott Brick', 'Science Fiction', 1279],
  ['Educated', 'Tara Westover', 'Julia Whelan', 'Biographies & Memoirs', 729],
  ['The Martian', 'Andy Weir', 'R.C. Bray', 'Science Fiction', 653],
  ['Sapiens', 'Yuval Noah Harari', 'Derek Perkins', 'History', 925],
  ['Circe', 'Madeline Miller', 'Perdita Weeks', 'Fantasy', 723],
  ['Atomic Habits', 'James Clear', 'James Clear', 'Self Development', 335],
];

const audiobookItem = (i: number) => {
  const [title, author, narrator, genre, minutes] = AUDIOBOOKS[i % AUDIOBOOKS.length];
  return {
    asin: `B0AUDIO${i}`,
    title,
    subtitle: '',
    authors: [{ name: author, asin: `A${i}` }],
    narrators: [{ name: narrator }],
    author_names: [author],
    narrator_names: [narrator],
    series: i % 3 === 1 ? [{ title: `${title} Saga`, sequence: '1', sequence_value: 1 }] : [],
    publisher: 'Audible Studios',
    summary: `A listener favourite: ${title}, read by ${narrator}.`,
    short_summary: `A listener favourite, read by ${narrator}.`,
    release_date: `202${i % 5}-0${(i % 9) + 1}-15`,
    runtime_minutes: minutes,
    runtime_formatted: `${Math.floor(minutes / 60)} hrs and ${minutes % 60} mins`,
    cover_url: null,
    cover_url_large: null,
    sample_url: null,
    rating: {
      average: 4.2 + (i % 4) / 10,
      count: 1200 + i * 310,
      distribution: { '5': 800, '4': 250, '3': 90, '2': 40, '1': 20 },
    },
    genres: [genre],
    language: 'english',
    format_type: 'unabridged',
    is_adult: false,
    owned: i % 4 === 0,
    source: 'audible',
  };
};

const audiobooks = [
  http.get('*/api/audiobooks/home', () =>
    HttpResponse.json({
      success: true,
      hero: audiobookItem(0),
      shelves: [
        {
          key: 'bestsellers',
          title: 'Bestsellers',
          category: '',
          sort: 'bestsellers',
        },
        { key: 'new', title: 'New Releases', category: '', sort: 'newest' },
        {
          key: 'scifi',
          title: 'Science Fiction & Fantasy',
          category: 'Science Fiction & Fantasy',
          sort: 'bestsellers',
        },
      ].map((shelf, s) => ({
        ...shelf,
        results: Array.from({ length: 8 }, (_, i) => audiobookItem(i + s * 3)),
      })),
    }),
  ),
  http.get('*/api/audiobooks/categories', () =>
    HttpResponse.json({
      success: true,
      categories: [
        'Biographies & Memoirs',
        'History',
        'Literature & Fiction',
        'Mystery, Thriller & Suspense',
        'Science Fiction & Fantasy',
        'Self Development',
      ].map((name, i) => ({ id: `c${i}`, name, children: [] })),
    }),
  ),
  http.get('*/api/audiobooks/wishlist', () =>
    HttpResponse.json({
      success: true,
      items: [],
      counts: {
        wanted: 2,
        searching: 1,
        grabbed: 0,
        done: 1,
        failed: 0,
        total: 4,
      },
      worker: null,
    }),
  ),
];

const PODCAST_SHOWS: [string, string, string][] = [
  ['Hardcore History', 'Dan Carlin', 'History'],
  ['Song Exploder', 'Hrishikesh Hirway', 'Music'],
  ['Radiolab', 'WNYC Studios', 'Science'],
  ['99% Invisible', 'Roman Mars', 'Arts'],
  ['Darknet Diaries', 'Jack Rhysider', 'Technology'],
  ['The Daily', 'The New York Times', 'News'],
  ['Switched On Pop', 'Vulture', 'Music'],
  ['Reply All', 'Gimlet', 'Technology'],
];

const podcastShow = ([title, author, category]: [string, string, string], i: number) => ({
  title,
  author,
  description: `${title}: weekly episodes from ${author} about ${category.toLowerCase()}.`,
  artwork_url: null,
  feed_url: `https://feeds.example/podcast-${i + 1}.xml`,
  itunes_id: 1000 + i,
  website: null,
  language: 'en',
  explicit: i % 4 === 3,
  categories: [category],
  episode_count: 40 + i * 25,
});

const podcasts = [
  http.get('*/api/podcasts/featured', () =>
    HttpResponse.json({
      success: true,
      category: 'Trending',
      results: PODCAST_SHOWS.map(podcastShow),
    }),
  ),
  http.get('*/api/podcasts/watchlist', () =>
    HttpResponse.json({
      success: true,
      podcasts: PODCAST_SHOWS.slice(0, 2).map((show, i) => ({
        ...podcastShow(show, i),
        auto_download: true,
        retention_days: 14,
      })),
    }),
  ),
  http.get('*/api/podcasts/downloads', () => HttpResponse.json({ success: true, downloads: [] })),
];

const SEARCH_ARTISTS = ['Aphex Twin', 'AFX', 'Polygon Window', 'The Tuss'];
const SEARCH_ALBUMS: [string, string, string, number][] = [
  // [name, album_type, release_date, total_tracks]
  ['Selected Ambient Works 85-92', 'album', '1992-11-09', 13],
  ['Richard D. James Album', 'album', '1996-11-04', 10],
  ['Drukqs', 'album', '2001-10-22', 30],
  ['Syro', 'album', '2014-09-19', 12],
  ['Collapse EP', 'ep', '2018-09-14', 5],
  ['Windowlicker', 'single', '1999-03-22', 3],
];
const SEARCH_TRACKS: [string, string, number][] = [
  // [name, album, duration_ms]
  ['Xtal', 'Selected Ambient Works 85-92', 291_000],
  ['Avril 14th', 'Drukqs', 125_000],
  ['Windowlicker', 'Windowlicker', 367_000],
  ['4', 'Richard D. James Album', 218_000],
  ['minipops 67 [120.2][source field mix]', 'Syro', 288_000],
  ['T69 Collapse', 'Collapse EP', 324_000],
];

const search = [
  http.get('*/status', () =>
    HttpResponse.json({
      metadata_source: { source: 'spotify', connected: true },
    }),
  ),
  http.get('*/api/settings/config-status', () =>
    HttpResponse.json({
      spotify: { configured: true },
      itunes: { configured: true },
      deezer: { configured: true },
      soulseek: { configured: true },
    }),
  ),
  // Basic (Soulseek) panel asks for its chip row on mount.
  http.get('*/api/search/sources', () =>
    HttpResponse.json({
      mode: 'soulseek',
      sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
    }),
  ),
  http.post('*/api/enhanced-search/library-check', () =>
    HttpResponse.json({
      albums: SEARCH_ALBUMS.map((_, i) => i === 0),
      tracks: SEARCH_TRACKS.map((_, i) => ({
        in_library: i === 0,
        in_wishlist: i === 1,
      })),
    }),
  ),
  http.post('*/api/enhanced-search', () =>
    HttpResponse.json({
      db_artists: [{ id: 42, name: 'Aphex Twin', image_url: null, source: 'library' }],
      spotify_artists: SEARCH_ARTISTS.map((name, i) => ({
        id: `sp-artist-${i}`,
        name,
        image_url: null,
        source: 'spotify',
        followers: 1_200_000 - i * 250_000,
      })),
      spotify_albums: SEARCH_ALBUMS.map(([name, album_type, release_date, total_tracks], i) => ({
        id: `sp-album-${i}`,
        name,
        artist: 'Aphex Twin',
        artists: [{ id: 'sp-artist-0', name: 'Aphex Twin' }],
        album_type,
        image_url: null,
        release_date,
        total_tracks,
        source: 'spotify',
      })),
      spotify_tracks: SEARCH_TRACKS.map(([name, album, duration_ms], i) => ({
        id: `sp-track-${i}`,
        name,
        artist: 'Aphex Twin',
        artists: ['Aphex Twin'],
        album,
        duration_ms,
        image_url: null,
        source: 'spotify',
        popularity: 70 - i * 5,
        preview_url: null,
      })),
      spotify_playlists: [],
      primary_source: 'spotify',
      metadata_source: 'spotify',
      source_available: true,
    }),
  ),
  http.post('*/api/labels/search', () =>
    HttpResponse.json({
      labels: [
        {
          id: 'lbl-1',
          name: 'Warp Records',
          type: 'Original Production',
          area: 'United Kingdom',
        },
      ],
    }),
  ),
  http.get('*/api/artist/:id/image', () => HttpResponse.json({ success: false })),
];

const WISHLIST_ALBUM_TRACKS: [string, string, string, number][] = [
  // [artist, album, track, retry_count]
  ['Aphex Twin', 'Selected Ambient Works 85-92', 'Xtal', 0],
  ['Aphex Twin', 'Selected Ambient Works 85-92', 'Tha', 0],
  ['Aphex Twin', 'Selected Ambient Works 85-92', 'Pulsewidth', 1],
  ['Boards of Canada', 'Music Has the Right to Children', 'Roygbiv', 0],
  ['Boards of Canada', 'Music Has the Right to Children', 'Aquarius', 4],
  ['Burial', 'Untrue', 'Archangel', 0],
  ['Burial', 'Untrue', 'Near Dark', 0],
  ['Portishead', 'Dummy', 'Sour Times', 3],
];

const WISHLIST_SINGLES: [string, string, number][] = [
  // [artist, track, retry_count]
  ['Four Tet', 'Baby', 0],
  ['Caribou', 'Never Come Back', 0],
  ['Jon Hopkins', 'Emerald Rush', 5],
  ['Röyksopp', 'Eple', 0],
];

const wishlist = [
  http.get('*/api/wishlist/stats', () =>
    HttpResponse.json({
      singles: WISHLIST_SINGLES.length,
      albums: WISHLIST_ALBUM_TRACKS.length,
      total: WISHLIST_ALBUM_TRACKS.length + WISHLIST_SINGLES.length,
      // downloads.js ticks this down once a second on real timers; 2h 0m 59s
      // reads "2h 0m" for the first 59s, so the shot doesn't depend on timing.
      next_run_in_seconds: 7259,
      is_auto_processing: false,
    }),
  ),
  http.get('*/api/wishlist/cycle', () => HttpResponse.json({ cycle: 'albums' })),
  http.get('*/api/wishlist/retry-profile', () => {
    const standard = {
      name: 'standard',
      label: 'Standard',
      description: 'Back off gradually: 1h, 6h, then once a day.',
      ladder: { '1': 3600, '2': 21600 },
      max_cooldown: 86400,
    };
    return HttpResponse.json({
      success: true,
      profile: standard,
      profiles: [
        {
          name: 'aggressive',
          label: 'Aggressive',
          description: 'Retry every cycle.',
          ladder: {},
          max_cooldown: 0,
        },
        standard,
        {
          name: 'patient',
          label: 'Patient',
          description: 'Retry once a week.',
          ladder: { '1': 86400 },
          max_cooldown: 604800,
        },
      ],
    });
  }),
  http.get('*/api/wishlist/tracks', ({ request }) => {
    const category = new URL(request.url).searchParams.get('category');
    const tracks =
      category === 'singles'
        ? WISHLIST_SINGLES.map(([artist, name, retry_count], i) => ({
            id: 100 + i,
            spotify_track_id: `wl-single-${i}`,
            retry_count,
            last_attempted: retry_count ? '2025-01-12 22:00:00' : null,
            failure_reason: retry_count >= 3 ? 'No matching file found' : null,
            spotify_data: { name, album: name, artists: [{ name: artist }] },
          }))
        : WISHLIST_ALBUM_TRACKS.map(([artist, album, name, retry_count], i) => ({
            id: 1 + i,
            spotify_track_id: `wl-album-${i}`,
            retry_count,
            last_attempted: retry_count ? '2025-01-12 22:00:00' : null,
            failure_reason: retry_count >= 3 ? 'No matching file found' : null,
            spotify_data: {
              name,
              album: { name: album, images: [] },
              artists: [{ name: artist }],
            },
          }));
    return HttpResponse.json({ success: true, tracks, artist_images: {} });
  }),
  http.get('*/api/active-processes', () => HttpResponse.json({ active_processes: [] })),
  // The orbs' artist photos; an empty list is the "no curated photos" path.
  http.get('*/api/watchlist/artists', () => HttpResponse.json({ success: true, artists: [] })),
];

const DISCOVER_ARTISTS = [
  'Aphex Twin',
  'Boards of Canada',
  'Burial',
  'Caribou',
  'Four Tet',
  'Jon Hopkins',
];

const DISCOVER_ALBUMS = [
  { album: 'Syro', artist: 'Aphex Twin' },
  { album: 'Geogaddi', artist: 'Boards of Canada' },
  { album: 'Untrue', artist: 'Burial' },
  { album: 'Swim', artist: 'Caribou' },
  { album: 'Rounds', artist: 'Four Tet' },
  { album: 'Immunity', artist: 'Jon Hopkins' },
];

/** A mix's tracks: same shape the release-radar / personalized feeders send. */
const discoverTracks = (offset: number) =>
  Array.from({ length: 6 }, (_, i) => {
    const a = DISCOVER_ALBUMS[(offset + i) % DISCOVER_ALBUMS.length];
    return {
      track_name: `Track ${offset + i + 1}`,
      artist_name: a.artist,
      album_name: a.album,
      album_cover_url: null,
      duration_ms: 240_000 + i * 15_000,
      spotify_track_id: `sptrk${offset + i}`,
    };
  });

const discoverRecArtists = (prefix: string, kind: string) =>
  DISCOVER_ARTISTS.map((artist_name, i) => ({
    artist_id: `${prefix}${i}`,
    artist_name,
    image_url: null,
    source: 'spotify',
    spotify_artist_id: `${prefix}${i}`,
    explanation: {
      kind,
      seeds: [{ name: DISCOVER_ARTISTS[(i + 1) % DISCOVER_ARTISTS.length] }],
    },
  }));

const discover = [
  http.get('*/api/discover/hero', () =>
    HttpResponse.json({
      success: true,
      source: 'spotify',
      artists: DISCOVER_ARTISTS.slice(0, 4).map((artist_name, i) => ({
        artist_id: `hero${i}`,
        artist_name,
        spotify_artist_id: `hero${i}`,
        occurrence_count: 4 - i,
        similarity_rank: i + 1,
        source: 'spotify',
        explanation: {
          kind: 'similar_to',
          seeds: [{ name: 'Radiohead' }, { name: 'Portishead' }],
        },
        owned_album_count: i,
        genres: ['idm', 'electronic', 'ambient'],
        popularity: 72 - i * 10,
      })),
    }),
  ),
  http.post('*/api/watchlist/check', () =>
    HttpResponse.json({ success: true, is_watching: false }),
  ),
  http.post('*/api/discover/similar-artists/enrich', () =>
    HttpResponse.json({ success: true, artists: {} }),
  ),
  http.post('*/api/watchlist/check-batch', () => HttpResponse.json({ success: true, results: {} })),
  http.get('*/api/discover/release-radar', () =>
    HttpResponse.json({ success: true, tracks: discoverTracks(0) }),
  ),
  http.get('*/api/discover/discovery-weekly', () =>
    HttpResponse.json({ success: true, tracks: discoverTracks(1) }),
  ),
  http.get('*/api/discover/personalized/popular-picks', () =>
    HttpResponse.json({ success: true, tracks: discoverTracks(2) }),
  ),
  http.get('*/api/discover/personalized/hidden-gems', () =>
    HttpResponse.json({ success: true, tracks: discoverTracks(3) }),
  ),
  http.get('*/api/discover/listening-recommendations', () =>
    HttpResponse.json({
      success: true,
      source: 'spotify',
      artists: discoverRecArtists('lr', 'listened'),
    }),
  ),
  http.get('*/api/discover/similar-artists', () =>
    HttpResponse.json({
      success: true,
      source: 'spotify',
      artists: discoverRecArtists('sa', 'similar_to'),
    }),
  ),
  http.get('*/api/discover/adventurousness', () =>
    HttpResponse.json({ success: true, value: 0.4 }),
  ),
  http.get('*/api/discover/recent-releases', () =>
    HttpResponse.json({
      success: true,
      albums: DISCOVER_ALBUMS.map((a, i) => ({
        album_name: a.album,
        artist_name: a.artist,
        album_cover_url: null,
        album_spotify_id: `spalb${i}`,
        release_date: '2025-01-0' + (i + 1),
        in_library: i % 3 === 0,
      })),
    }),
  ),
  http.get('*/api/discover/your-albums', () =>
    HttpResponse.json({
      success: true,
      albums: DISCOVER_ALBUMS.map((a, i) => ({
        album_name: a.album,
        artist_name: a.artist,
        image_url: null,
        spotify_album_id: `spalb${i}`,
        release_date: '2014-09-22',
        year: 2014 - i,
        album_type: 'album',
        total_tracks: 10 + i,
        in_library: i % 2 === 0,
      })),
      total: 6,
      page: 1,
      per_page: 48,
      stale: false,
      stats: { total: 6, owned: 3, missing: 3 },
    }),
  ),
  http.get('*/api/discover/your-artists', () =>
    HttpResponse.json({
      success: true,
      artists: DISCOVER_ARTISTS.map((artist_name, i) => ({
        id: i + 1,
        artist_name,
        image_url: null,
        on_watchlist: i % 2 === 0,
        source_services: ['spotify'],
        active_source: 'spotify',
        active_source_id: `sp${i}`,
        spotify_artist_id: `sp${i}`,
      })),
    }),
  ),
  http.get('*/api/discover/stations', () =>
    HttpResponse.json({
      success: true,
      stations: DISCOVER_ARTISTS.slice(0, 4).map((name, i) => ({
        artist_id: i + 1,
        name,
        image_url: null,
        with: DISCOVER_ARTISTS.filter((n) => n !== name).slice(0, 3),
        playable_tracks: 40 - i * 5,
      })),
    }),
  ),
  http.get('*/api/discover/deezer/genres', () =>
    HttpResponse.json({
      success: true,
      genres: [
        { id: 0, name: 'Top' },
        { id: 106, name: 'Electro' },
        { id: 152, name: 'Rock' },
        { id: 129, name: 'Jazz' },
      ],
    }),
  ),
  http.get('*/api/discover/deezer/editorial', () =>
    HttpResponse.json({
      success: true,
      count: 5,
      playlists: [
        'Electronic Essentials',
        'Ambient Focus',
        'Rock Essentials',
        'Jazz Classics',
        'Late Night',
      ].map((title, i) => ({
        id: `13069316${i}`,
        title,
        creator: 'Deezer Editor',
        track_count: 50 + i * 10,
        image_url: null,
        link: `https://www.deezer.com/playlist/13069316${i}`,
        source: 'deezer',
      })),
    }),
  ),
];

// GET /automations (React route). Lands on the overview; the card grid is
// /automations?view=library&nav=all if a library shot is wanted too.

const AUTOMATION_ROWS = [
  {
    id: 1,
    name: 'Nightly watchlist scan',
    enabled: 1,
    trigger_type: 'daily_time',
    trigger_config: { time: '03:00' },
    action_type: 'scan_watchlist',
    action_config: {},
    then_actions: [{ type: 'discord_webhook', config: {} }],
    last_run: '2025-01-13 03:00:00',
    next_run: '2025-01-14 03:00:00',
    run_count: 42,
    last_error: null,
    last_result: {
      artists_scanned: 24,
      new_tracks_found: 6,
      tracks_added_to_wishlist: 6,
    },
    is_system: 0,
    group_name: 'Discovery',
    owned_by: 'music',
  },
  {
    id: 2,
    name: 'Process wishlist',
    enabled: 1,
    trigger_type: 'schedule',
    trigger_config: { interval: 6, unit: 'hours' },
    action_type: 'process_wishlist',
    action_config: {},
    then_actions: [],
    last_run: '2025-01-13 06:00:00',
    next_run: '2025-01-13 12:00:00',
    run_count: 118,
    last_error: null,
    last_result: { processed: 12, downloaded: 9 },
    is_system: 0,
    group_name: 'Discovery',
    owned_by: 'music',
  },
  {
    id: 3,
    name: 'Sync Discover Weekly',
    enabled: 1,
    trigger_type: 'weekly_time',
    trigger_config: { days: ['mon'], time: '08:00' },
    action_type: 'sync_playlist',
    action_config: { playlist_name: 'Discover Weekly' },
    then_actions: [],
    last_run: '2025-01-13 08:00:00',
    next_run: '2025-01-20 08:00:00',
    run_count: 15,
    last_error: 'Spotify rate limit hit',
    last_result: { status: 'error' },
    is_system: 0,
    group_name: 'Playlists',
    owned_by: 'music',
  },
  {
    id: 4,
    name: 'Tag new downloads',
    enabled: 1,
    trigger_type: 'track_downloaded',
    trigger_config: {},
    action_type: 'notify_only',
    action_config: {},
    then_actions: [{ type: 'telegram', config: {} }],
    last_run: '2025-01-13 09:12:00',
    next_run: null,
    run_count: 311,
    last_error: null,
    last_result: null,
    is_system: 0,
    group_name: null,
    owned_by: 'music',
  },
  {
    id: 5,
    name: 'Weekly quality scan',
    enabled: 0,
    trigger_type: 'weekly_time',
    trigger_config: { days: ['sun'], time: '02:00' },
    action_type: 'start_quality_scan',
    action_config: {},
    then_actions: [],
    last_run: null,
    next_run: null,
    run_count: 0,
    last_error: null,
    last_result: null,
    is_system: 0,
    group_name: null,
    owned_by: 'music',
  },
  {
    id: 6,
    name: 'Backup database',
    enabled: 1,
    trigger_type: 'daily_time',
    trigger_config: { time: '04:30' },
    action_type: 'backup_database',
    action_config: {},
    then_actions: [],
    last_run: '2025-01-13 04:30:00',
    next_run: '2025-01-14 04:30:00',
    run_count: 90,
    last_error: null,
    last_result: { size_mb: 48 },
    is_system: 1,
    group_name: null,
    owned_by: 'music',
  },
  {
    id: 7,
    name: 'Update database hourly',
    enabled: 1,
    trigger_type: 'schedule',
    trigger_config: { interval: 1, unit: 'hours' },
    action_type: 'start_database_update_hourly',
    action_config: {},
    then_actions: [],
    last_run: '2025-01-13 09:00:00',
    next_run: '2025-01-13 10:00:00',
    run_count: 2040,
    last_error: null,
    last_result: { new_tracks: 3 },
    is_system: 1,
    group_name: null,
    owned_by: 'music',
  },
].map((row) => ({
  ...row,
  profile_id: 1,
  created_at: '2024-12-01 10:00:00',
  updated_at: '2025-01-10 10:00:00',
}));

const automations = [
  http.get('*/api/automations/master', () => HttpResponse.json({ music: true, video: true })),
  http.get('*/api/automations/progress', () => HttpResponse.json({})),
  http.get('*/api/automations/blocks', () =>
    HttpResponse.json({
      triggers: [
        { type: 'schedule', label: 'Schedule' },
        { type: 'daily_time', label: 'Daily Time' },
        { type: 'weekly_time', label: 'Weekly Time' },
        { type: 'track_downloaded', label: 'Track Downloaded' },
      ],
      actions: [
        { type: 'scan_watchlist', label: 'Scan Watchlist' },
        { type: 'process_wishlist', label: 'Process Wishlist' },
        { type: 'sync_playlist', label: 'Sync Playlist' },
        { type: 'notify_only', label: 'Notify Only' },
        { type: 'start_quality_scan', label: 'Run Quality Scan' },
        { type: 'backup_database', label: 'Backup Database' },
      ],
      notifications: [
        { type: 'discord_webhook', label: 'Discord Webhook' },
        { type: 'telegram', label: 'Telegram' },
      ],
    }),
  ),
  http.get('*/api/automations', () => HttpResponse.json(AUTOMATION_ROWS)),
];

// GET /chat (legacy static/chat.js). Messages carry no channel tag, so they
// all land in #general, the default channel.

const CHAT_MESSAGES = [
  ['09:02', 'basslinejunkie', 'morning all, anyone got the new Burial EP in flac?'],
  ['09:04', 'vinyl_ghost', 'yeah grabbed it last night, sharing now'],
  ['09:05', 'basslinejunkie', 'legend, thanks'],
  ['09:11', 'ambient_annie', 'is the wishlist retry working for everyone after the update?'],
  ['09:13', 'kraut_kid', 'works here, took two passes for a couple of albums'],
  ['09:20', 'soulsync_user', 'same, quality profile fallback picked up the 320s'],
  ['09:31', 'vinyl_ghost', 'Selected Ambient Works on repeat today'],
  ['09:42', 'ambient_annie', 'good choice'],
].map(([time, username, message]) => ({
  timestamp: `2025-01-13T${time}:00`,
  username,
  message,
}));

const chat = [
  http.get('*/api/chat/status', () =>
    HttpResponse.json({
      connected: true,
      configured: true,
      room: 'SoulSync',
      can_send: true,
      is_admin: true,
      username: 'soulsync_user',
    }),
  ),
  http.get('*/api/chat/rooms', () =>
    HttpResponse.json({
      home: 'SoulSync',
      rooms: [
        { name: 'SoulSync', home: true },
        { name: 'electronic', home: false },
      ],
      can_manage: true,
    }),
  ),
  http.get('*/api/chat/room', () =>
    HttpResponse.json({
      room: 'SoulSync',
      joined: true,
      messages: CHAT_MESSAGES,
      users: ['ambient_annie', 'basslinejunkie', 'kraut_kid', 'soulsync_user', 'vinyl_ghost'].map(
        (username) => ({ username }),
      ),
      can_send: true,
      protocol: [],
    }),
  ),
  http.get('*/api/chat/conversations', () =>
    HttpResponse.json({
      conversations: [
        {
          username: 'vinyl_ghost',
          hasUnAcknowledgedMessages: true,
          unAcknowledgedMessageCount: 2,
        },
        {
          username: 'kraut_kid',
          hasUnAcknowledgedMessages: false,
          unAcknowledgedMessageCount: 0,
        },
      ],
      can_send: true,
    }),
  ),
];

// GET /settings (legacy static/settings.js), Connections tab. Secrets come back
// as the redaction sentinel, as the real server sends them. Service tiles read
// "configured" from the filled-in inputs.

const REDACTED = '__redacted_unchanged__';

const SETTINGS = {
  active_media_server: 'plex',
  spotify: {
    client_id: 'a1b2c3d4e5f6',
    client_secret: REDACTED,
    redirect_uri: 'http://127.0.0.1:8888/callback',
  },
  tidal: { client_id: '', client_secret: '', redirect_uri: '' },
  deezer: { app_id: '', app_secret: '', redirect_uri: '' },
  plex: { base_url: 'http://192.168.1.20:32400', token: REDACTED },
  jellyfin: { base_url: '', api_key: '', api_timeout: 120 },
  navidrome: { base_url: '', username: '', password: '' },
  soulseek: {
    slskd_url: 'http://192.168.1.20:5030',
    api_key: REDACTED,
    download_path: '/downloads',
    transfer_path: '/music',
    search_timeout: 60,
    search_timeout_buffer: 15,
    download_timeout: 600,
  },
  lastfm: {
    api_key: REDACTED,
    api_secret: REDACTED,
    username: 'admin',
    scrobble_enabled: true,
    session_key: REDACTED,
  },
  listenbrainz: {
    base_url: '',
    token: REDACTED,
    username: 'admin',
    scrobble_enabled: false,
  },
  genius: { access_token: REDACTED },
  acoustid: { api_key: REDACTED, enabled: true },
  discogs: { token: REDACTED },
  itunes: { country: 'US' },
  concerts: { ticketmaster_api_key: '', setlistfm_api_key: '', country: 'US' },
  hydrabase: { url: '', api_key: '', auto_connect: false },
  metadata: { fallback_source: 'deezer' },
  download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'youtube'] },
  library: { music_paths: ['/music'] },
  logging: { level: 'INFO', path: 'logs/app.log' },
};

const settings = [
  http.get('*/api/settings/config-status', () =>
    HttpResponse.json(
      Object.fromEntries(
        [
          'spotify',
          'lastfm',
          'listenbrainz',
          'genius',
          'acoustid',
          'discogs',
          'itunes',
          'musicbrainz',
        ].map((s) => [s, { configured: true }]),
      ),
    ),
  ),
  http.get('*/api/settings/log-level', () => HttpResponse.json({ success: true, level: 'INFO' })),
  http.get('*/api/settings', () => HttpResponse.json(SETTINGS)),
  http.get('*/api/plex/music-libraries', () =>
    HttpResponse.json({
      success: true,
      libraries: [{ title: 'Music' }, { title: 'Classical' }],
      selected: 'Music',
      current: 'Music',
    }),
  ),
  http.get('*/api/hydrabase/status', () => HttpResponse.json({ connected: false })),
  http.get('*/api/dev-mode', () => HttpResponse.json({ enabled: false })),
];

/** Handlers per route, on top of `shellHandlers`. */
export const routeHandlers: Record<string, RequestHandler[]> = {
  library: [libraryArtists],
  watchlist: watchlistArtists,
  issues,
  stats,
  'artist-detail': artistDetail,
  audiobooks,
  automations,
  chat,
  discover,
  podcasts,
  search,
  settings,
  wishlist,
};

/** Handlers for the overlay and error-state shots in `STATES`. */
export const stateHandlers: Record<string, RequestHandler[]> = {
  issueDetail: [
    http.get('*/api/issues/:issueId', () =>
      HttpResponse.json({
        success: true,
        issue: {
          id: 7,
          profile_id: 1,
          entity_type: 'album',
          entity_id: '15',
          category: 'wrong_metadata',
          title: 'Bad tags',
          description: 'Album title is wrong',
          status: 'open',
          priority: 'normal',
          snapshot_data: { title: 'Album Name', artist_name: 'Artist', format: 'FLAC' },
          created_at: '2025-01-10 10:30:00',
          updated_at: '2025-01-11 09:00:00',
          reporter_name: 'Ada',
          comments: [
            {
              id: 1,
              author_id: 2,
              author_name: 'Ada',
              kind: 'comment',
              body: 'The disc title says "Remastered" twice.',
              created_at: '2025-01-10 10:35:00',
            },
            {
              id: 2,
              author_id: 1,
              author_name: 'Admin',
              kind: 'event',
              body: 'changed the priority to normal',
              created_at: '2025-01-11 09:00:00',
            },
          ],
          followers: [
            { follower_id: 3, follower_name: 'Grace', created_at: '2025-01-11 08:00:00' },
          ],
        },
      }),
    ),
  ],
  issuesEmpty: [
    http.get('*/api/issues/counts', () =>
      HttpResponse.json({
        success: true,
        counts: { open: 0, in_progress: 0, resolved: 0, dismissed: 0, total: 0 },
      }),
    ),
    http.get('*/api/issues', () => HttpResponse.json({ success: true, total: 0, issues: [] })),
  ],
  statsError: [
    http.get('*/api/stats/cached', () =>
      HttpResponse.json({ success: false, error: 'Stats are unavailable' }, { status: 500 }),
    ),
  ],
};
