import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// static/video/search-filters.js is a plain browser script: run it against a
// stand-in window and test what it exports.
const SRC = readFileSync(resolve(process.cwd(), 'static/video/search-filters.js'), 'utf8');

interface Row {
  title: string;
  resolution?: string;
  codec?: string;
  group?: string;
  username?: string;
  size_gb?: number;
  seeders?: number | null;
  format_score?: number;
  published_at?: string;
  accepted?: boolean;
}
interface Cond {
  field: string;
  op: string;
  value: string;
}
interface Filter {
  match?: 'all' | 'any';
  conditions: Cond[];
}
interface State {
  text?: string;
  filter?: Filter | null;
  sort?: string;
  dir?: 'asc' | 'desc';
}
interface Api {
  view: (rows: Row[], state: State, now?: number) => { row: Row; index: number }[];
  matches: (row: Row, filter: Filter, now?: number) => boolean;
  opsFor: (field: string) => string[];
}

function load(): Api {
  const win: { VideoSearchFilters?: Api } = {};
  new Function('window', SRC)(win);
  return win.VideoSearchFilters as Api;
}

const NOW = Date.parse('2026-11-04T12:00:00Z');
const ROWS: Row[] = [
  {
    title: 'Dexter.Resurrection.S01E02.1080p.HEVC.x265-MeGusta',
    resolution: '1080p',
    codec: 'x265',
    group: 'MeGusta',
    username: 'NZBgeek',
    size_gb: 0.4,
    format_score: 300,
    accepted: true,
    published_at: '2026-11-02T12:00:00Z',
  },
  {
    title: 'Dexter.Resurrection.S01E02.1080p.WEB.H264-NTb',
    resolution: '1080p',
    codec: 'h264',
    group: 'NTb',
    username: 'DrunkenSlug',
    size_gb: 1.4,
    format_score: 0,
    accepted: true,
    published_at: '2026-11-03T12:00:00Z',
  },
  {
    title: 'Dexter.Resurrection.S01E02.720p.HEVC-MeGusta',
    resolution: '720p',
    codec: 'hevc',
    group: 'MeGusta',
    username: 'NZBgeek',
    size_gb: 0.25,
    format_score: 300,
    accepted: true,
    published_at: '2026-10-20T12:00:00Z',
  },
  {
    title: 'Dexter.Resurrection.S01E02.2160p.WEB-FLUX',
    resolution: '2160p',
    codec: 'hevc',
    group: 'FLUX',
    username: 'Torrentio',
    size_gb: 4.1,
    seeders: 40,
    accepted: false,
  },
];

const titles = (items: { row: Row }[]) =>
  items.map((i) => i.row.title.split('.').slice(3).join('.'));

describe('manual search filters (SeadogsBooty)', () => {
  const api = load();

  it('the 1080p HEVC MeGusta under 1 GB filter keeps only that release', () => {
    const filter: Filter = {
      match: 'all',
      conditions: [
        { field: 'resolution', op: 'equals', value: '1080' },
        { field: 'codec', op: 'equals', value: 'HEVC' },
        { field: 'group', op: 'contains', value: 'megusta' },
        { field: 'size_gb', op: 'lt', value: '1' },
      ],
    };
    const shown = api.view(ROWS, { filter }, NOW);
    expect(titles(shown)).toEqual(['1080p.HEVC.x265-MeGusta']);
    expect(shown[0].index).toBe(0); // the card keeps its index, so GET grabs the right row
  });

  it('x265, h.265 and hevc are one codec', () => {
    const hevc = {
      match: 'all' as const,
      conditions: [{ field: 'codec', op: 'equals', value: 'h.265' }],
    };
    expect(api.view(ROWS, { filter: hevc }, NOW).map((i) => i.index)).toEqual([0, 2, 3]);
  });

  it('any matches either condition', () => {
    const filter: Filter = {
      match: 'any',
      conditions: [
        { field: 'resolution', op: 'gt', value: '1080' },
        { field: 'size_gb', op: 'lt', value: '0.3' },
      ],
    };
    expect(api.view(ROWS, { filter }, NOW).map((i) => i.index)).toEqual([2, 3]);
  });

  it('a number the row does not know is not a match, an empty value filters nothing', () => {
    const seeded = { conditions: [{ field: 'seeders', op: 'gt', value: '10' }] };
    expect(api.view(ROWS, { filter: seeded }, NOW).map((i) => i.index)).toEqual([3]);
    const blank = { conditions: [{ field: 'size_gb', op: 'lt', value: '' }] };
    expect(api.view(ROWS, { filter: blank }, NOW)).toHaveLength(4);
  });

  it('meets profile and the quick box', () => {
    const ok = { conditions: [{ field: 'meets_profile', op: 'equals', value: 'yes' }] };
    expect(api.view(ROWS, { filter: ok }, NOW)).toHaveLength(3);
    expect(api.view(ROWS, { text: 'megusta 720p' }, NOW).map((i) => i.index)).toEqual([2]);
    expect(api.view(ROWS, { text: 'drunkenslug' }, NOW).map((i) => i.index)).toEqual([1]);
  });

  it('sorts by size, age and indexer, unknown values last, rank keeps the server order', () => {
    expect(api.view(ROWS, { sort: 'size_gb', dir: 'asc' }, NOW).map((i) => i.index)).toEqual([
      2, 0, 1, 3,
    ]);
    expect(api.view(ROWS, { sort: 'size_gb', dir: 'desc' }, NOW).map((i) => i.index)).toEqual([
      3, 1, 0, 2,
    ]);
    // newest first; the row with no date goes last either way
    expect(api.view(ROWS, { sort: 'age_days', dir: 'asc' }, NOW).map((i) => i.index)).toEqual([
      1, 0, 2, 3,
    ]);
    expect(api.view(ROWS, { sort: 'indexer', dir: 'asc' }, NOW).map((i) => i.index)).toEqual([
      1, 0, 2, 3,
    ]);
    expect(api.view(ROWS, { sort: 'rank' }, NOW).map((i) => i.index)).toEqual([0, 1, 2, 3]);
  });

  it('offers only the operators that fit a field', () => {
    expect(api.opsFor('size_gb')).toEqual(['equals', 'not_equals', 'gt', 'lt']);
    expect(api.opsFor('title')).toEqual(['contains', 'not_contains', 'equals', 'not_equals']);
    expect(api.opsFor('meets_profile')).toEqual(['equals', 'not_equals']);
  });
});

describe('the language filter (SeadogsBooty: dubbed anime)', () => {
  const api = load();
  const anime = [
    { title: 'Frieren.S01E01.JAPANESE.1080p.WEB', languages: ['ja'], multi: false },
    { title: 'Frieren.S01E01.DUAL.1080p.WEB', languages: ['en', 'ja'], multi: true },
    { title: 'Frieren.S01E01.1080p.WEB', languages: [], multi: false },
  ] as unknown as Row[];
  const pick = (value: string, op = 'contains') =>
    api
      .view(anime, { filter: { conditions: [{ field: 'language', op, value }] } }, NOW)
      .map((i) => i.index);

  it('finds a language by name or code', () => {
    expect(pick('japanese')).toEqual([0, 1]);
    expect(pick('ja')).toEqual([0, 1]);
  });

  it('finds a dual audio release', () => {
    expect(pick('dual')).toEqual([1]);
    expect(pick('english')).toEqual([1]);
  });

  it('can rule a dub out', () => {
    expect(pick('english', 'not_contains')).toEqual([0, 2]);
  });
});
