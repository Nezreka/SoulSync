import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from '@/test/vanilla-extract';

import type { VideoDiagnostics, VideoWishRowBase } from './-video.types';

import {
  compactCount,
  diagModel,
  failWhy,
  fmtDate,
  fmtDuration,
  initials,
  isFailingItem,
  liveStatus,
  sized,
  sourceLines,
  ytImg,
} from './-video.helpers';

/**
 * The port against the code it replaced, not against what I believed it did.
 *
 * Every function here runs twice: the REAL vanilla body, lifted out of the
 * frozen copy of video-wishlist.js (and the live video-youtube.js, which stays),
 * and the TypeScript port. They must agree on every input in the matrix,
 * including the awkward ones: nulls, unknown statuses, numeric strings, NaN.
 */

const VANILLA = readFileSync(
  resolve(process.cwd(), 'src/routes/wishlist/__fixtures__/-vanilla-video-wishlist.js'),
  'utf8',
);
const YOUTUBE = readFileSync(resolve(process.cwd(), 'static/video/video-youtube.js'), 'utf8');

const PREAMBLE = `
  var STATUS = {
    wanted: ['Wanted', 'vwsh-st--wanted'], searching: ['Searching', 'vwsh-st--searching'],
    downloading: ['Downloading', 'vwsh-st--downloading'], downloaded: ['Done', 'vwsh-st--done'],
    failed: ['Failed', 'vwsh-st--failed'],
    monitored: ['Not out yet', 'vwsh-st--monitored'],
    upgrade: ['⇪ Upgrading', 'vwsh-st--upgrade'],
    muted: ['Muted', 'vwsh-st--monitored'],
  };
  var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
`;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Fn = (...args: any[]) => any;

function vanilla(names: string[], source = VANILLA): Record<string, Fn> {
  const bodies = names.map((n) => extractFunction(n, source)).join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${PREAMBLE}\n${bodies}\nreturn { ${names.join(', ')} };`)() as Record<
    string,
    Fn
  >;
}

it('the frozen fixture still holds the STATUS map this preamble copies', () => {
  // the preamble restates the module-level STATUS + MO the functions close over;
  // if the fixture ever disagreed with it, every comparison below would be moot.
  expect(VANILLA.replace(/\r\n/g, '\n')).toContain(
    "monitored: ['Not out yet', 'vwsh-st--monitored'],\n        upgrade: ['⇪ Upgrading', 'vwsh-st--upgrade'],\n        muted: ['Muted', 'vwsh-st--monitored'],",
  );
  expect(VANILLA).toContain(
    "var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];",
  );
});

const STATUSES = [
  undefined,
  null,
  '',
  'wanted',
  'searching',
  'downloading',
  'downloaded',
  'failed',
  'monitored',
  'upgrade',
  'muted',
  'bogus',
];
const ATTEMPTS: unknown[] = [undefined, null, 0, 2, 3, '3', 40, Number.NaN, 'x', -1, Infinity];

function rowMatrix(): (VideoWishRowBase & Record<string, unknown>)[] {
  const rows: (VideoWishRowBase & Record<string, unknown>)[] = [];
  for (const status of STATUSES)
    for (const downloading of [undefined, false, true])
      for (const upgrade_from of [undefined, null, '', '720p'])
        for (const muted of [undefined, false, true])
          for (const search_attempts of ATTEMPTS)
            rows.push({
              status: status as string,
              downloading,
              upgrade_from,
              muted,
              search_attempts: search_attempts as number,
            });
  return rows;
}

describe('status and failing, over every combination', () => {
  const v = vanilla(['liveStatus', 'isFailingItem']);
  const rows = rowMatrix();

  it(`liveStatus agrees on ${rows.length * 3} rows`, () => {
    for (const row of rows)
      for (const showMuted of [undefined, false, true])
        expect(liveStatus(row, showMuted), JSON.stringify({ row, showMuted })).toBe(
          v.liveStatus(row, showMuted),
        );
  });

  it(`isFailingItem agrees on ${rows.length * 3} rows`, () => {
    for (const row of rows)
      for (const showMuted of [undefined, false, true])
        expect(isFailingItem(row, showMuted), JSON.stringify({ row, showMuted })).toBe(
          v.isFailingItem(row, showMuted),
        );
  });
});

const SNAPSHOTS: unknown[] = [
  undefined,
  null,
  {},
  { sources: null },
  { sources: {} },
  {
    chain: ['torrent', 'soulseek'],
    sources: {
      torrent: { ran: false, reason: 'Prowlarr not configured' },
      soulseek: { ran: true, results: 4, accepted: 0, reason: "SD isn't in your enabled tiers" },
    },
  },
  { chain: [], sources: { a: { ran: true, results: 0 }, b: { ran: false } } },
  { chain: ['missing', 'a'], sources: { a: { ran: true, results: 6, accepted: 2 } } },
  { sources: { a: { ran: true, results: 3, accepted: 0, reason: '' } } },
  { sources: { a: null, b: { ran: true, results: 1 } } },
];

describe('the failure wording', () => {
  const v = vanilla(['failWhy', 'sourceLines']);
  const rows: unknown[] = [];
  for (const search_snapshot of SNAPSHOTS)
    for (const last_search_at of [undefined, '', '2026-09-01 10:00'])
      for (const last_refusal of [undefined, '', 'Over your 4 GB size cap'])
        rows.push({ search_snapshot, last_search_at, last_refusal });
  rows.push(null, undefined);

  it(`sourceLines agrees on ${rows.length} rows`, () => {
    for (const row of rows)
      expect(sourceLines(row as VideoWishRowBase), JSON.stringify(row)).toEqual(v.sourceLines(row));
  });

  it(`failWhy agrees on ${rows.length * 4} rows`, () => {
    for (const row of rows)
      for (const fails of [0, 3, 40, 1])
        expect(failWhy(row as VideoWishRowBase, fails), JSON.stringify(row)).toBe(
          v.failWhy(row, fails),
        );
  });
});

describe('art, dates and names', () => {
  const v = vanilla(['sized', 'fmtDate', 'initials']);
  const URLS = [
    null,
    undefined,
    '',
    '/api/video/poster/show/12',
    '/api/video/poster/show/12?x=1',
    'https://image.tmdb.org/t/p/original/abc.jpg',
    'https://image.tmdb.org/t/p/w92/abc.jpg',
    'https://i.ytimg.com/vi/x/hq.jpg',
    '//i.ytimg.com/vi/x/hq.jpg',
    'https://yt3.ggpht.com/a.jpg',
    'https://example.com/a.jpg',
  ];

  it('sized agrees on every url and width bucket', () => {
    for (const url of URLS)
      for (const w of [92, 185, 186, 240, 342, 343, 500, 501, 1000])
        expect(sized(url, w), `${url} @${w}`).toBe(v.sized(url, w));
  });

  it('fmtDate agrees', () => {
    for (const iso of [
      null,
      undefined,
      '',
      '2026',
      '2026-03',
      '2026-03-04',
      '2026-13-40',
      'a-b-c',
      '2026-00-00',
    ])
      expect(fmtDate(iso), String(iso)).toBe(v.fmtDate(iso));
  });

  it('initials agrees', () => {
    for (const s of [
      null,
      undefined,
      '',
      'Breaking Bad',
      '!!!',
      'ちびまる子ちゃん',
      'a',
      'The 100',
      '  spaced  out ',
    ])
      expect(initials(s), String(s)).toBe(v.initials(s));
  });
});

describe('the youtube helpers, against video-youtube.js', () => {
  const v = vanilla(['img', 'fmtDuration', 'compactCount'], YOUTUBE);

  it('ytImg agrees with VideoYoutube.img', () => {
    for (const u of [
      null,
      '',
      'https://i.ytimg.com/vi/x/hq.jpg',
      '//i.ytimg.com/vi/x/hq.jpg',
      'https://yt3.ggpht.com/a=s88',
      'https://lh3.googleusercontent.com/x',
      'https://evil.com/i.ytimg.com/',
      'http://i.ytimg.com/vi/x/hq.jpg',
      '/api/video/img?u=already',
    ])
      expect(ytImg(u), String(u)).toBe(v.img(u));
  });

  it('fmtDuration and compactCount agree', () => {
    for (const n of [
      null,
      undefined,
      '',
      0,
      -5,
      5,
      59,
      60,
      61,
      599,
      3599,
      3600,
      3661,
      36000,
      '90',
      'x',
      1.5,
    ])
      expect(fmtDuration(n), String(n)).toBe(v.fmtDuration(n));
    for (const n of [
      null,
      undefined,
      '',
      0,
      7,
      999,
      1000,
      1049,
      1050,
      999999,
      1e6,
      1.25e6,
      2e9,
      '12',
      'x',
    ])
      expect(compactCount(n), String(n)).toBe(v.compactCount(n));
  });
});

describe('the diagnostics drawer', () => {
  const v = vanilla(['diagRowsHTML']);

  /** what the vanilla html says, as the same data the port produces. */
  function readVanilla(d: unknown) {
    const host = document.createElement('div');
    host.innerHTML = v.diagRowsHTML(d) as string;
    const lines = [...host.querySelectorAll('.vwsh-diag-line')].map((n) => ({
      k: n.querySelector('.vwsh-diag-k')!.textContent,
      v: n.querySelector('.vwsh-diag-v')!.textContent,
      tone: n.classList.contains('vwsh-diag-line--gap')
        ? 'gap'
        : n.classList.contains('vwsh-diag-line--live')
          ? 'live'
          : '',
    }));
    const heads = [...host.querySelectorAll('.vwsh-diag-src')].map((n) => n.textContent);
    const rels = [...host.querySelectorAll('.vwsh-diag-rel:not(.vwsh-diag-rel--none)')].map(
      (n) => ({
        title: n.querySelector('.vwsh-diag-rel-t')!.textContent,
        verdict: n.querySelector('.vwsh-diag-rel-w')!.textContent,
        ok: n.classList.contains('vwsh-diag-rel--ok'),
      }),
    );
    return { lines, heads, rels };
  }

  const PAYLOADS: VideoDiagnostics[] = [
    {},
    { row: {}, ids: {} },
    {
      row: { search_attempts: 9, last_search_at: '2026-09-02 12:00', last_refusal: 'not yet' },
      ids: { tmdb_id: 10160, tvdb_id: 1234, imdb_id: 'tt0123456' },
      target_dir: '/media/TV',
      downloads: [],
    },
    {
      row: { search_attempts: 0 },
      ids: { tmdb_id: 1, tvdb_id: null, imdb_id: '' },
      target_dir: '',
      downloads: [
        { status: 'downloading', progress: 42.4, release_title: 'BB S28E27' },
        { progress: 0 },
        {},
      ],
    },
    {
      row: {
        search_snapshot: {
          chain: ['torrent', 'soulseek', 'gone'],
          sources: {
            torrent: { ran: false, reason: 'Prowlarr not configured' },
            soulseek: {
              ran: true,
              results: 3,
              accepted: 1,
              samples: [
                { title: 'A 1080p', accepted: true },
                { title: '', rejected: "720p isn't in your enabled tiers" },
                { rejected: null },
              ],
            },
          },
        },
      },
    },
    { row: { search_snapshot: { sources: { x: { ran: true } } } } },
  ];

  it('says the same thing as the vanilla html, line for line', () => {
    for (const d of PAYLOADS) {
      const old = readVanilla(d);
      const port = diagModel(d);
      expect(port.lines, JSON.stringify(d)).toEqual(old.lines);
      expect(
        port.sources.map((s) => s.head),
        JSON.stringify(d),
      ).toEqual(old.heads);
      expect(
        port.sources.flatMap((s) => s.releases),
        JSON.stringify(d),
      ).toEqual(old.rels);
    }
  });
});
