/**
 * Static-markup fixtures of the unified downloads page for REAL screenshots.
 *
 * Renders the real components (via the real router + mocked API) and writes
 * the HTML where a playwright script can lay it out under the real
 * stylesheets. The expanded-card fixture clicks a real card and waits for
 * the real lazy meta fetch, so the drawer markup is exactly what the page
 * produces.
 *
 * Run: vitest run src/test/vdl-fixtures.test.tsx
 * Then: node scripts/shot-unified-downloads.mjs
 */

import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

const OUT = resolve(process.cwd(), 'tests/layout/__fixtures__');

function emit(name: string, html: string) {
  mkdirSync(OUT, { recursive: true });
  writeFileSync(resolve(OUT, name), html, 'utf8');
  expect(html.length).toBeGreaterThan(100);
}

/** Colored SVG placeholders so the image slots visibly render. */
function svgURI(w: number, h: number, bg: string, fg: string, text: string): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">` +
    `<rect width="100%" height="100%" fill="${bg}"/>` +
    `<text x="50%" y="54%" font-size="${Math.round(Math.min(w, h) / 3.2)}" ` +
    `text-anchor="middle" fill="${fg}" font-family="sans-serif">${text}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

const POSTER_DUNE = svgURI(200, 300, '#1a2b4a', '#9db8dd', 'DUNE');
const POSTER_BEAR = svgURI(200, 300, '#3a2b1a', '#ddb89d', 'BEAR');
const POSTER_OPP = svgURI(200, 300, '#2b1a2b', '#c99ddb', 'OPP');
const BACKDROP_DUNE = svgURI(1280, 720, '#0e1626', '#5a7396', 'DUNE · PART TWO');
const YT_THUMB = svgURI(320, 180, '#231a1a', '#db9d9d', '▶');
const castPhoto = (initials: string) => svgURI(100, 100, '#2a3b55', '#cfe0f5', initials);

const videoRows = [
  {
    id: 101,
    kind: 'movie',
    title: 'Dune: Part Two',
    year: 2024,
    status: 'downloading',
    progress: 42,
    poster_url: POSTER_DUNE,
    quality_label: '4K',
    release_title: 'Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.Atmos.H.265',
    size_bytes: 18_000_000_000,
    speed_bps: 12_500_000,
    eta_seconds: 840,
    username: 'cinephile',
    media_id: 123,
    media_source: 'tmdb',
    resolution: '2160p',
    source: 'WEB-DL',
    codec: 'H.265',
    attempts: 1,
    search_ctx: JSON.stringify({}),
  },
  {
    id: 201,
    kind: 'show',
    title: 'The Bear',
    year: 2022,
    status: 'downloading',
    progress: 78,
    poster_url: POSTER_BEAR,
    quality_label: '1080p',
    is_pack: true,
    media_id: 456,
    media_source: 'tmdb',
    size_bytes: 9_000_000_000,
    speed_bps: 8_000_000,
    search_ctx: JSON.stringify({ season: 3, scope: 'season' }),
  },
  {
    id: 202,
    kind: 'show',
    title: 'The Bear',
    year: 2022,
    status: 'completed',
    dest_path: '/media/tv/The Bear/Season 3/The Bear S03E01.mkv',
    media_id: 456,
    media_source: 'tmdb',
    quality_label: '1080p',
    search_ctx: JSON.stringify({ season: 3, episode: 1 }),
  },
  {
    id: 203,
    kind: 'show',
    title: 'The Bear',
    year: 2022,
    status: 'downloading',
    progress: 12,
    poster_url: POSTER_BEAR,
    media_id: 456,
    media_source: 'tmdb',
    quality_label: '1080p',
    size_bytes: 3_000_000_000,
    speed_bps: 4_000_000,
    search_ctx: JSON.stringify({ season: 3, episode: 2 }),
  },
  {
    id: 301,
    kind: 'movie',
    title: 'Oppenheimer',
    year: 2023,
    status: 'failed',
    error: 'No seeders after 3 attempts',
    username: 'peer1',
    filename: 'Oppenheimer.2023.mkv',
    poster_url: POSTER_OPP,
    quality_label: '1080p',
    attempts: 3,
    media_id: 789,
    media_source: 'tmdb',
    search_ctx: JSON.stringify({}),
  },
  {
    id: 302,
    kind: 'movie',
    title: 'Poor Things',
    year: 2023,
    status: 'import_failed',
    error: 'Could not place file in library',
    username: 'peer2',
    filename: 'Poor.Things.2023.mkv',
    quality_label: '1080p',
    media_id: 790,
    media_source: 'tmdb',
    search_ctx: JSON.stringify({}),
  },
  {
    id: 401,
    kind: 'movie',
    title: 'The Zone of Interest',
    year: 2023,
    status: 'searching',
    quality_label: '1080p',
    media_id: 791,
    media_source: 'tmdb',
    search_ctx: JSON.stringify({}),
  },
  {
    id: 501,
    kind: 'youtube',
    title: 'Every Frame a Painting: The Art of the Cut',
    status: 'downloading',
    progress: 63,
    poster_url: YT_THUMB,
    media_id: 'dQw4w9WgXcQ',
    size_bytes: 400_000_000,
    speed_bps: 2_000_000,
    search_ctx: JSON.stringify({
      channel: 'Every Frame a Painting',
      channel_id: 'UCx',
      duration: '18:24',
      description: 'A video essay about editing rhythms in modern cinema.',
    }),
  },
  {
    // a youtube video grabbed from the download panel is saved as kind 'movie'
    id: 502,
    kind: 'movie',
    title: 'How Pixar Lights a Scene',
    status: 'completed',
    poster_url: YT_THUMB,
    media_id: 'abc123xyz00',
    media_source: 'youtube',
    quality_label: '1080p',
    search_ctx: JSON.stringify({ channel: 'Pixar', channel_id: 'UCpixar' }),
  },
  {
    id: 601,
    kind: 'movie',
    title: 'Past Lives',
    year: 2023,
    status: 'completed',
    upgrade_watch: true,
    quality_label: '1080p',
    dest_path: '/media/movies/Past Lives (2023)/Past Lives 2023 1080p BluRay.mkv',
    media_id: 792,
    media_source: 'tmdb',
    search_ctx: JSON.stringify({}),
  },
];

const duneMeta = {
  title: 'Dune: Part Two',
  overview:
    'Paul Atreides unites with Chani and the Fremen while seeking revenge against the ' +
    'conspirators who destroyed his family. Facing a choice between the love of his life ' +
    'and the fate of the known universe, he endeavors to prevent a terrible future.',
  tagline: 'Long live the fighters.',
  backdrop_url: BACKDROP_DUNE,
  year: 2024,
  rating: 8.2,
  runtime_minutes: 166,
  studio: 'Legendary Pictures',
  status: 'Released',
  director: 'Denis Villeneuve',
  genres: ['Science Fiction', 'Adventure', 'Drama'],
  trailer_url: 'https://www.youtube.com/watch?v=Way9Dexny3w',
  providers: [{ name: 'Netflix', logo: svgURI(60, 60, '#b20710', '#fff', 'N') }],
  cast: [
    { name: 'Timothée Chalamet', character: 'Paul Atreides', photo: castPhoto('TC') },
    { name: 'Zendaya', character: 'Chani', photo: castPhoto('Z') },
    { name: 'Rebecca Ferguson', character: 'Lady Jessica', photo: castPhoto('RF') },
    { name: 'Austin Butler', character: 'Feyd-Rautha', photo: castPhoto('AB') },
    { name: 'Florence Pugh', character: 'Princess Irulan', photo: castPhoto('FP') },
  ],
};

/** utc "YYYY-MM-DD HH:MM:SS", the shape library_history.created_at has. */
function utcStamp(msAgo: number): string {
  return new Date(Date.now() - msAgo).toISOString().slice(0, 19).replace('T', ' ');
}

const COVER_MARCOS = svgURI(300, 300, '#7c2d12', '#fcd34d', 'MH');
const COVER_SF = svgURI(300, 300, '#991b1b', '#fde68a', 'SF');
const COVER_NPR = svgURI(300, 300, '#1e3a8a', '#bfdbfe', 'NPR');
const COVER_BOOK = svgURI(300, 300, '#4c0519', '#fecdd3', 'LF');
const PHOTO_MARCOS = svgURI(200, 200, '#78350f', '#fde68a', 'M');
const PHOTO_LEO = svgURI(200, 200, '#7f1d1d', '#fecaca', 'L');
const PHOTO_NPR = svgURI(200, 200, '#172554', '#bfdbfe', 'N');
const mixCover = (bg: string, t: string) => svgURI(300, 300, bg, '#fff', t);

const album = (over: Record<string, unknown>) => ({
  artist: 'MARCØS',
  album: "Marco's House",
  batch_id: 'b1',
  batch_name: "Marco's House",
  batch_total: 6,
  playlist_id: 'p1',
  artwork: COVER_MARCOS,
  artist_image: PHOTO_MARCOS,
  quality: '',
  progress: 0,
  timestamp: Date.now() / 1000 - 60,
  ...over,
});

const musicRows = [
  album({
    task_id: 't1',
    title: 'Saudade',
    status: 'downloading',
    progress: 62,
    track_index: 5,
    batch_position: 6,
    download_source: 'Soulseek',
    live_detail: {
      source: 'Soulseek',
      username: 'dj_marrow',
      filename: '@@dj_marrow\\Music\\MARCOS\\06 Saudade.flac',
      candidate_index: 0,
      candidate_count: 6,
      picked: { quality: 'flac', confidence: 0.96, free_upload_slots: 2, queue_length: 0 },
      speed: 1_900_000,
      size: 43_200_000,
      bytes: 26_800_000,
    },
  }),
  album({
    task_id: 't2',
    title: 'Night Bus',
    status: 'searching',
    track_index: 6,
    batch_position: 5,
    live_detail: {
      source: 'Tidal',
      query: 'MARCØS Night Bus',
      query_index: 1,
      query_count: 4,
      results: 14,
    },
  }),
  album({
    task_id: 't3',
    title: 'Rua Escura',
    status: 'not_found',
    track_index: 3,
    batch_position: 4,
    error: 'Not found on 4 sources',
  }),
  album({ task_id: 't4', title: 'Lado B', status: 'queued', track_index: 7, batch_position: 7 }),
  album({
    task_id: 't5',
    title: 'Intro',
    status: 'completed',
    progress: 100,
    track_index: 0,
    batch_position: 1,
    quality: 'FLAC 24/48',
    verification_status: 'verified',
    download_source: 'Soulseek',
  }),
  album({
    task_id: 't6',
    title: 'Casa',
    status: 'completed',
    progress: 100,
    track_index: 1,
    batch_position: 2,
    quality: 'FLAC 24/48',
    verification_status: 'verified',
    download_source: 'Soulseek',
  }),
  ...[
    ['Blue', 'Sault', '#1d4ed8'],
    ['Nights', 'Frank Ocean', '#a16207'],
    ['Runaway', 'Kanye West', '#be123c'],
    ['Pyramids', 'Frank Ocean', '#047857'],
  ].map(([title, artist, bg], i) => ({
    task_id: `m${i}`,
    title,
    artist,
    album: `${title} (single)`,
    artwork: mixCover(bg, title.slice(0, 2).toUpperCase()),
    status: i === 0 ? 'downloading' : 'completed',
    progress: i === 0 ? 30 : 100,
    quality: i === 0 ? '' : 'FLAC 16/44.1',
    verification_status: i === 0 ? null : 'verified',
    download_source: 'Tidal',
    batch_id: 'b3',
    batch_name: 'Late Night Drive',
    batch_total: 4,
    batch_position: i + 1,
    playlist_id: 'p3',
    track_index: i,
    timestamp: Date.now() / 1000 - 30,
  })),
  {
    task_id: 'ab1',
    title: 'Los fuegos de Diciembre',
    artist: 'Brandon Sanderson',
    album: 'Audiobooks',
    status: 'failed',
    error: 'No releases found',
    batch_id: 'b2',
    batch_name: 'Audiobooks',
    playlist_id: 'audiobooks',
    track_index: 0,
    batch_total: 1,
    artwork: COVER_BOOK,
    progress: 0,
    timestamp: Date.now() / 1000 - 300,
  },
  {
    task_id: 'history-901',
    title: 'Prologue',
    artist: 'MARCØS',
    album: "Marco's House (Bonus)",
    artwork: COVER_MARCOS,
    status: 'completed',
    progress: 100,
    is_persistent_history: true,
    batch_id: '',
    batch_name: 'Deezer',
    download_source: 'Deezer',
    quality: 'FLAC 16/44.1',
    verification_status: 'unverified',
    file_path: "H:\\Music\\MARCØS\\MARCØS - 2026 Marco's House\\01 - Prologue.flac",
    created_at: utcStamp(40 * 60_000),
    event_type: 'download',
  },
  {
    task_id: 'history-902',
    title: 'Chun-Li Theme',
    artist: 'Leo Birenberg & Zach Robinson',
    album: 'Street Fighter (Original Motion Picture Soundtrack)',
    artwork: COVER_SF,
    artist_image: PHOTO_LEO,
    status: 'completed',
    progress: 100,
    is_persistent_history: true,
    batch_id: '',
    batch_name: 'Deezer',
    download_source: 'Deezer',
    quality: 'FLAC 16/44.1',
    verification_status: 'unverified',
    acoustid_result: 'matched a different title',
    expected_title: 'Chun-Li: Woman of Vengeance',
    expected_artist: 'Leo Birenberg',
    file_path:
      'H:\\Music\\Leo Birenberg & Zach Robinson\\Street Fighter\\03 - Chun-Li_ Woman of Vengeance.flac',
    created_at: utcStamp(65 * 60_000),
    event_type: 'download',
  },
  {
    task_id: 'history-903',
    title: 'Where did U.S. support for Ukraine go?',
    artist: 'NPR',
    album: 'Consider This from NPR',
    artwork: COVER_NPR,
    artist_image: PHOTO_NPR,
    status: 'completed',
    progress: 100,
    is_persistent_history: true,
    batch_id: '',
    batch_name: 'Podcast',
    download_source: 'Podcast',
    quality: 'audio/mpeg',
    created_at: utcStamp(26 * 3_600_000),
    event_type: 'podcast',
  },
  {
    task_id: 'history-904',
    title: "How will the riots in France affect the country's politics?",
    artist: 'NPR',
    album: 'Consider This from NPR',
    artwork: COVER_NPR,
    artist_image: PHOTO_NPR,
    status: 'completed',
    progress: 100,
    is_persistent_history: true,
    batch_id: '',
    batch_name: 'Podcast',
    download_source: 'Podcast',
    quality: 'audio/mpeg',
    created_at: utcStamp(26.2 * 3_600_000),
    event_type: 'podcast',
  },
];

/** naive local isoformat, the shape datetime.now().isoformat() writes. */
function localIso(msAgo: number): string {
  const d = new Date(Date.now() - msAgo);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.123456`;
}

const quarantineEntries = [
  ...[0, 1, 2].map((i) => ({
    id: `q${i}`,
    filename: `2026_Saudade_${i}.flac.quarantined`,
    original_filename: i === 2 ? '06 Saudade (Preview).mp3' : '06 Saudade.flac',
    reason:
      i === 2
        ? 'AcoustID matched a different recording'
        : 'Duration mismatch: file is 0:31, expected 4:02',
    expected_track: 'Saudade',
    expected_artist: 'MARCØS',
    group_key: 'marcos|saudade',
    timestamp: localIso((i + 1) * 3_600_000),
    size_bytes: 9_000_000,
    has_full_context: true,
    trigger: i === 2 ? 'acoustid' : 'integrity',
    source_username: ['dj_marrow', 'cassette_k', 'lofi_vault'][i],
    source_filename: `@@user\\Music\\MARCOS\\06 Saudade.${i === 2 ? 'mp3' : 'flac'}`,
    thumb_url: COVER_MARCOS,
    quality: i === 2 ? 'MP3 320' : 'FLAC 24/48',
    library_status: 'present',
    library_match: {
      album: "Marco's House",
      filename: '06 - Saudade.flac',
      quality: 'FLAC 16/44.1',
      verification_status: 'verified',
    },
  })),
  {
    id: 'q9',
    filename: 'Night_Bus.mp3.quarantined',
    original_filename: 'Night Bus (Radio Edit).mp3',
    reason: 'AcoustID: no fingerprint match',
    expected_track: 'Night Bus',
    expected_artist: 'MARCØS',
    group_key: 'marcos|nightbus',
    timestamp: localIso(5 * 3_600_000),
    size_bytes: 7_400_000,
    has_full_context: true,
    trigger: 'acoustid_unverified',
    source_username: 'cassette_k',
    source_filename: '@@cassette_k\\mp3\\Night Bus (Radio Edit).mp3',
    thumb_url: '',
    quality: 'MP3 320',
    library_status: 'missing',
  },
];

const torrents = [
  {
    id: 'h1',
    name: 'Big.Brother.2023.S04E14.1080p.ITVX.WEB-DL.AAC2.0.H.264-RAWR',
    state: 'downloading',
    progress: 0.35,
    size: 2_250_000_000,
    downloaded: 790_000_000,
    download_speed: 316_000,
    upload_speed: 42_000,
    seeders: 14,
    peers: 3,
    eta: 3960,
    ratio: 0.02,
    category: 'soulsync-tv',
    save_path: '/downloads/incomplete',
    soulsync: { kind: 'episode', title: 'Big Brother (2023) · S04E14' },
  },
  {
    id: 'h2',
    name: 'Radiohead - Kid A (2000) [FLAC]',
    state: 'seeding',
    progress: 1,
    size: 432_000_000,
    downloaded: 432_000_000,
    download_speed: 0,
    upload_speed: 120_000,
    ratio: 3.2,
    category: 'music',
    save_path: '/downloads/music',
  },
  {
    id: 'h3',
    name: 'Good.Mythical.Evening.2021.720p',
    state: 'error',
    progress: 0.12,
    size: 1_400_000_000,
    downloaded: 168_000_000,
    download_speed: 0,
    upload_speed: 0,
    error: 'Tracker unreachable for 2 hours',
    category: 'soulsync-movies',
    soulsync: { kind: 'movie', title: 'Good Mythical Evening (2021)' },
  },
];

function stubAll() {
  server.use(
    http.get('/api/downloads/all', () =>
      HttpResponse.json({
        success: true,
        downloads: musicRows,
        batches: [
          {
            batch_id: 'b1',
            batch_name: "Marco's House",
            source_page: 'discover',
            phase: 'downloading',
            total: 6,
            completed: 2,
            failed: 1,
            active: 2,
            queued: 1,
            playlist_id: 'p1',
          },
          {
            batch_id: 'b3',
            batch_name: 'Late Night Drive',
            source_page: 'sync',
            phase: 'downloading',
            total: 4,
            completed: 3,
            failed: 0,
            active: 1,
            queued: 0,
            playlist_id: 'p3',
          },
          {
            batch_id: 'b2',
            batch_name: 'Audiobooks',
            batch_type: 'audiobook',
            playlist_id: 'audiobooks',
            phase: 'downloading',
            total: 1,
            completed: 0,
            failed: 1,
            active: 0,
            queued: 0,
          },
        ],
      }),
    ),
    http.get('/api/downloads/task/:id/detail', () =>
      HttpResponse.json({
        success: true,
        detail: {
          status: 'not_found',
          status_kind: 'not_found',
          reason: 'Not found on 4 sources',
          decision: {
            outcome: 'nothing_passed',
            accepted_total: 0,
            rejected_total: 9,
            rejected_counts: { duration_mismatch: 5, match_weak: 4 },
          },
        },
      }),
    ),
    http.get('/api/library/history', () =>
      HttpResponse.json({ success: true, entries: [], total: 4835 }),
    ),
    http.get('/api/downloads/batch-history', () =>
      HttpResponse.json({ success: true, history: [] }),
    ),
    http.get('/api/verification/config', () =>
      HttpResponse.json({ success: true, acoustid_enabled: true, require_verified: false }),
    ),
    http.get('/api/review-queue/summary', () =>
      HttpResponse.json({ success: true, quarantine: 4, unverified: 2, total: 6 }),
    ),
    http.get('/api/quarantine/list', () =>
      HttpResponse.json({
        success: true,
        entries: quarantineEntries,
        total: quarantineEntries.length,
      }),
    ),
    http.get('/api/deleted-files', () =>
      HttpResponse.json({
        success: true,
        entries: [
          {
            id: 'deleted:a',
            name: '03 - Pyramids.flac',
            rel: 'Frank Ocean/Channel Orange/03 - Pyramids.flac',
            size: 50_500_000,
            deleted_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
            source: 'duplicate-cleaner',
            original_path: 'H:\\Music\\Frank Ocean\\Channel Orange\\03 - Pyramids.flac',
          },
        ],
        total_size: 50_500_000,
        count: 1,
        keep_days: 30,
      }),
    ),
    http.get('/api/clients/links', () =>
      HttpResponse.json({
        success: true,
        slskd: 'http://slskd',
        torrent: 'http://qbit',
        usenet: '',
      }),
    ),
    http.get('/api/clients/torrent', () =>
      HttpResponse.json({
        success: true,
        configured: true,
        connected: true,
        type: 'qbittorrent',
        items: torrents,
      }),
    ),
    http.get('/api/clients/usenet', () =>
      HttpResponse.json({ success: true, configured: false, connected: false, items: [] }),
    ),
    http.get('/api/clients/slskd', () =>
      HttpResponse.json({
        success: true,
        configured: true,
        connected: true,
        items: [],
        uploads: [],
      }),
    ),
    http.get('/api/video/downloads/active', () => HttpResponse.json({ downloads: videoRows })),
    http.get('/api/video/downloads/recycle', () =>
      HttpResponse.json({
        items: [
          {
            name: '20260101_120000_Old.Movie.2020.1080p.mkv',
            trash_dir: 'trash/vid',
            original_path: '/media/movies/Old Movie (2020)/Old.Movie.2020.1080p.mkv',
            reason: 'upgraded to 4K',
            size: 8_000_000_000,
            age_seconds: 7200,
          },
        ],
        keep_days: 7,
        recycle_enabled: true,
      }),
    ),
    http.get('/api/video/downloads/blocklist', () =>
      HttpResponse.json({
        success: true,
        items: [
          {
            id: 9,
            title: 'Dune: Part Two',
            release_title: 'Dune.Part.Two.2024.FAKE.WEB-DL',
            username: 'badpeer',
            reason: 'Sample file',
            season_number: null,
            episode_number: null,
          },
        ],
      }),
    ),
    http.get('/api/video/downloads/history', () =>
      HttpResponse.json({
        success: true,
        items: [],
        counts: { total: 128, movie: 90, show: 30, youtube: 8 },
        pagination: { total_pages: 4 },
      }),
    ),
    http.get('/api/video/downloads/meta/movie/123', () => HttpResponse.json(duneMeta)),
    http.get('/api/video/downloads/yt-meta/:id', () => HttpResponse.json({})),
  );
}

/** a row by its title (the header's live strip names the same titles). */
async function rowNamed(name: string | RegExp): Promise<HTMLElement> {
  const hits = await screen.findAllByText(name);
  const row = hits.map((h) => h.closest('.dlc-row')).find(Boolean);
  expect(row).toBeTruthy();
  return row as HTMLElement;
}

function renderRoute(path: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createAppRouter({ history, queryClient });
  const { container } = render(<AppRouterProvider router={router} queryClient={queryClient} />);
  return container;
}

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  // the real grouper lives in wishlist-tools.js; group by group_key the same way
  window._groupQuarantineEntries = (entries: unknown[]) => {
    const groups = new Map<string, { key: string; members: unknown[] }>();
    for (const e of entries as { group_key?: string }[]) {
      const k = e.group_key || String(Math.random());
      if (!groups.has(k)) groups.set(k, { key: k, members: [] });
      groups.get(k)!.members.push(e);
    }
    return [...groups.values()];
  };
  stubAll();
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('unified downloads fixtures', () => {
  it('writes the music view', async () => {
    const container = renderRoute('/active-downloads');
    await rowNamed('Saudade');
    const root = container.querySelector('.adl-unified');
    expect(root).not.toBeNull();
    emit('unified-downloads-music.fixture.txt', root!.innerHTML);
  });

  it('writes the music view with rows expanded', async () => {
    const container = renderRoute('/active-downloads');
    for (const title of ['Saudade', 'Rua Escura', 'Prologue']) {
      fireEvent.click(await rowNamed(title));
    }
    // the not-found row's fetched decision lands
    await screen.findByText(/wrong length/);
    const root = container.querySelector('.adl-unified');
    expect(root).not.toBeNull();
    emit('unified-downloads-music-expanded.fixture.txt', root!.innerHTML);
  });

  it('writes the review and clients views', async () => {
    const container = renderRoute('/active-downloads');
    await rowNamed('Saudade');
    fireEvent.click(
      container.querySelector('#adl-view-tabs [data-filter="unverified"]') as HTMLElement,
    );
    fireEvent.click(await rowNamed('Chun-Li Theme'));
    fireEvent.click((await rowNamed('Prologue')).querySelector('.dlc-check') as HTMLElement);
    emit(
      'unified-downloads-review.fixture.txt',
      container.querySelector('.adl-unified')!.innerHTML,
    );

    fireEvent.click(container.querySelector('[data-seg="quarantine"]') as HTMLElement);
    const head = await waitFor(() => {
      const h = container.querySelector('.dlc-qhead') as HTMLElement;
      expect(h).not.toBeNull();
      return h;
    });
    fireEvent.click(head);
    fireEvent.click(container.querySelector('[data-quarantine-id="q0"]') as HTMLElement);
    emit(
      'unified-downloads-quarantine.fixture.txt',
      container.querySelector('.adl-unified')!.innerHTML,
    );

    fireEvent.click(container.querySelector('[data-seg="deleted"]') as HTMLElement);
    await screen.findByText('03 - Pyramids.flac');
    emit(
      'unified-downloads-deleted.fixture.txt',
      container.querySelector('.adl-unified')!.innerHTML,
    );

    fireEvent.click(
      container.querySelector('#adl-view-tabs [data-filter="clients"]') as HTMLElement,
    );
    fireEvent.click(
      await waitFor(() => container.querySelector('[data-seg="torrent"]') as HTMLElement),
    );
    fireEvent.click(await rowNamed('Big Brother (2023) · S04E14'));
    emit(
      'unified-downloads-clients.fixture.txt',
      container.querySelector('.adl-unified')!.innerHTML,
    );

    fireEvent.click(container.querySelector('[data-seg="usenet"]') as HTMLElement);
    await screen.findByText(/No usenet client connected/);
    emit(
      'unified-downloads-usenet.fixture.txt',
      container.querySelector('.adl-unified')!.innerHTML,
    );
  });

  it('writes the video review view', async () => {
    const container = renderRoute('/active-downloads?media=video');
    await rowNamed(/Dune: Part Two/);
    fireEvent.click(
      container.querySelector('#adl-view-tabs [role="tab"]:nth-child(2)') as HTMLElement,
    );
    await screen.findAllByText(/Old.Movie.2020/);
    emit(
      'unified-downloads-vreview.fixture.txt',
      container.querySelector('.adl-unified')!.innerHTML,
    );
    // the video view's tab is kept across mounts: leave it on Downloads
    fireEvent.click(container.querySelector('#adl-view-tabs [role="tab"]') as HTMLElement);
  });

  it('writes the video view', async () => {
    const container = renderRoute('/active-downloads?media=video');
    await rowNamed(/Dune: Part Two/);
    const root = container.querySelector('.adl-unified');
    expect(root).not.toBeNull();
    emit('unified-downloads-video.fixture.txt', root!.innerHTML);
  });

  it('writes the expanded video card with cast info', async () => {
    const container = renderRoute('/active-downloads?media=video');
    // click the card body (not a control) to open the drawer
    fireEvent.click(await rowNamed(/Dune: Part Two/));
    // the lazy meta fetch lands: cast names paint
    await screen.findByText('Timothée Chalamet');
    await screen.findByText('Paul Atreides');
    const root = container.querySelector('.adl-unified');
    expect(root).not.toBeNull();
    emit('unified-downloads-video-expanded.fixture.txt', root!.innerHTML);
  });
});
