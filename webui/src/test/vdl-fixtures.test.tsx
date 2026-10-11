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
import { fireEvent, render, screen } from '@testing-library/react';
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

const musicRows = [
  {
    task_id: 't1',
    title: 'Xtal',
    artist: 'Aphex Twin',
    album: 'Selected Ambient Works 85–92',
    status: 'downloading',
    progress: 40,
    batch_id: 'b1',
    batch_name: 'Aphex Twin — SAW 85–92',
    playlist_id: 'p1',
    track_index: 0,
    artwork: '',
    quality: 'FLAC',
  },
  {
    task_id: 't2',
    title: 'Tha',
    artist: 'Aphex Twin',
    album: 'Selected Ambient Works 85–92',
    status: 'queued',
    progress: 0,
    batch_id: 'b1',
    batch_name: 'Aphex Twin — SAW 85–92',
    playlist_id: 'p1',
    track_index: 1,
    artwork: '',
    quality: 'FLAC',
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
            batch_name: 'Aphex Twin — SAW 85–92',
            phase: 'downloading',
            active: 1,
            queued: 1,
            playlist_id: 'p1',
          },
        ],
      }),
    ),
    http.get('/api/downloads/batch-history', () =>
      HttpResponse.json({ success: true, history: [] }),
    ),
    http.get('/api/verification/config', () =>
      HttpResponse.json({ success: true, acoustid_enabled: true, require_verified: false }),
    ),
    http.get('/api/review-queue/summary', () =>
      HttpResponse.json({ success: true, quarantine: 0, unverified: 0, total: 0 }),
    ),
    http.get('/api/quarantine/list', () => HttpResponse.json({ success: true, entries: [] })),
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

function renderRoute(path: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createAppRouter({ history, queryClient });
  const { container } = render(<AppRouterProvider router={router} queryClient={queryClient} />);
  return container;
}

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  stubAll();
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('unified downloads fixtures', () => {
  it('writes the music view', async () => {
    const container = renderRoute('/active-downloads');
    await screen.findByText('Xtal');
    const root = container.querySelector('.adl-unified');
    expect(root).not.toBeNull();
    emit('unified-downloads-music.fixture.txt', root!.innerHTML);
  });

  it('writes the video view', async () => {
    const container = renderRoute('/active-downloads?media=video');
    await screen.findByText(/Dune: Part Two/);
    const root = container.querySelector('.adl-unified');
    expect(root).not.toBeNull();
    emit('unified-downloads-video.fixture.txt', root!.innerHTML);
  });

  it('writes the expanded video card with cast info', async () => {
    const container = renderRoute('/active-downloads?media=video');
    const card = await screen.findByText(/Dune: Part Two/);
    // click the card body (not a control) to open the drawer
    const row = card.closest('.vdpg-card') as HTMLElement;
    expect(row).not.toBeNull();
    fireEvent.click(row);
    // the lazy meta fetch lands: cast names paint
    await screen.findByText('Timothée Chalamet');
    await screen.findByText('Paul Atreides');
    const root = container.querySelector('.adl-unified');
    expect(root).not.toBeNull();
    emit('unified-downloads-video-expanded.fixture.txt', root!.innerHTML);
  });
});
