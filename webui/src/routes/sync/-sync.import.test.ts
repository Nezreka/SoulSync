/**
 * Import parsers + mirror normalizer — differential against the live
 * stats-automations.js. The parser suite lifts clean; the two DOM-mixed
 * functions get genuine differentials anyway: _importFileBuildTracks runs
 * against a stubbed document/state, and mirrorPlaylist runs against a stubbed
 * fetch that captures the wire body for comparison with buildMirrorPayload.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { ImportColumnRole, MirrorSourceTrack } from './-sync.import';

import { extractFunction } from '../../test/vanilla-extract';
import {
  IMPORT_FILE_EXTENSIONS,
  buildMirrorPayload,
  importFileAutoMapColumns,
  importFileDetectDelimiter,
  importFileParseCsv,
  importFileParseM3u,
  importFileTypeForExtension,
  importTracksFromCsv,
  importTracksFromText,
} from './-sync.import';

const STATS_SRC = readFileSync(resolve(process.cwd(), 'static/stats-automations.js'), 'utf8');

function lift<T>(names: string[], source: string, preamble = ''): T {
  const body = names.map((n) => extractFunction(n, source)).join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${preamble}\n${body}\nreturn { ${names.join(', ')} };`)() as T;
}

type ParserVanilla = {
  _importFileDetectDelimiter: (l: string) => string;
  _importFileParseCsv: (t: string, d: string) => { headers: string[]; rows: string[][] };
  _importFileParseM3u: (t: string) => { tracks: unknown[]; playlistName: string };
  _importFileAutoMapColumns: (h: string[]) => Record<number, string>;
};

const parser = lift<ParserVanilla>(
  [
    '_importFileDetectDelimiter',
    '_importFileParseCsv',
    '_importFileParseM3u',
    '_importFileAutoMapColumns',
  ],
  STATS_SRC,
);

describe('importFileTypeForExtension', () => {
  it('buckets every supported extension and rejects the rest', () => {
    expect(IMPORT_FILE_EXTENSIONS).toEqual(['csv', 'tsv', 'txt', 'm3u', 'm3u8']);
    expect(importFileTypeForExtension('m3u')).toBe('m3u');
    expect(importFileTypeForExtension('M3U8')).toBe('m3u');
    expect(importFileTypeForExtension('txt')).toBe('text');
    expect(importFileTypeForExtension('csv')).toBe('csv');
    expect(importFileTypeForExtension('tsv')).toBe('csv');
    expect(importFileTypeForExtension('xlsx')).toBe(null);
  });
});

describe('importFileDetectDelimiter (differential)', () => {
  const LINES = [
    'a,b,c',
    'a\tb\tc',
    'a;b;c',
    'a,b;c', // one each: semi >= comma wins
    'a\tb,c;d', // one each incl tab: tab wins ties
    'plain header',
    '',
    'a,b\tc\td', // 2 tabs vs 1 comma
    'x;y,z,w', // 2 commas vs 1 semi → comma
  ];

  it('matches the vanilla on every sniff case', () => {
    for (const l of LINES) {
      expect(importFileDetectDelimiter(l), JSON.stringify(l)).toBe(
        parser._importFileDetectDelimiter(l),
      );
    }
  });

  it('pins the tie rules', () => {
    expect(importFileDetectDelimiter('a,b;c')).toBe(';');
    expect(importFileDetectDelimiter('a\tb,c;d')).toBe('\t');
    expect(importFileDetectDelimiter('plain header')).toBe(',');
  });
});

describe('importFileParseCsv (differential)', () => {
  const CASES: [string, string][] = [
    ['name,artist\nHUMBLE.,Kendrick Lamar\nDNA.,Kendrick Lamar', ','],
    ['name\tartist\nA\tB', '\t'],
    ['name,artist\n"Hello, World",Adele', ','],
    ['name,artist\n"She said ""hi""",X', ','],
    ['header only', ','],
    ['name,artist\n\n , \nA,B', ','], // blank + whitespace-only rows dropped
    ['name;artist\nA;B', ';'],
    ['"quoted,header",b\nv1,v2', ','],
  ];

  it('matches the vanilla over quotes, blanks and delimiters', () => {
    for (const [text, d] of CASES) {
      expect(importFileParseCsv(text, d), JSON.stringify(text)).toEqual(
        parser._importFileParseCsv(text, d),
      );
    }
  });

  it('pins the quote handling as literals', () => {
    expect(importFileParseCsv('name,artist\n"Hello, World",Adele', ',').rows).toEqual([
      ['Hello, World', 'Adele'],
    ]);
    expect(importFileParseCsv('name,artist\n"She said ""hi""",X', ',').rows).toEqual([
      ['She said "hi"', 'X'],
    ]);
    expect(importFileParseCsv('header only', ',')).toEqual({ headers: [], rows: [] });
  });
});

describe('importFileParseM3u (differential)', () => {
  const CASES = [
    // Extended with paths
    '#EXTM3U\n#PLAYLIST:My List\n#EXTINF:213,Kendrick Lamar - HUMBLE.\n/music/humble.mp3\n#EXTINF:180,Adele - Hello\n/music/hello.flac',
    // SoulSync export: missing path replaced by a comment — pending must flush
    '#EXTM3U\n#EXTINF:213,Kendrick Lamar - HUMBLE.\n# MISSING: not located\n#EXTINF:180,Adele - Hello\n/music/hello.flac',
    // Trailing EXTINF with no path
    '#EXTM3U\n#EXTINF:100,A - B',
    // Simple form: names derived from file paths
    '/music/Artist - Title.mp3\nC:\\music\\Other Artist - Other Title.flac\nnodash.mp3',
    // Invalid/negative durations + no comma
    '#EXTINF:-1,X - Y\npath.mp3\n#EXTINF:abc,Z - W\npath2.mp3\n#EXTINF:90\npath3.mp3',
    // Case-insensitive directives + blank lines
    '#extinf:60,a - b\n\n#playlist:Lower\npath.mp3',
    '',
  ];

  it('matches the vanilla over every form', () => {
    for (const text of CASES) {
      expect(importFileParseM3u(text), JSON.stringify(text.slice(0, 40))).toEqual(
        parser._importFileParseM3u(text),
      );
    }
  });

  it('pins the MISSING-path flush and filename derivation', () => {
    const missing = importFileParseM3u(
      '#EXTINF:213,Kendrick Lamar - HUMBLE.\n# MISSING: x\n#EXTINF:180,Adele - Hello\np.flac',
    );
    expect(missing.tracks).toHaveLength(2);
    expect(missing.tracks[0]).toEqual({
      track_name: 'HUMBLE.',
      artist_name: 'Kendrick Lamar',
      album_name: '',
      duration_ms: 213000,
    });
    const simple = importFileParseM3u('/music/Artist - Title.mp3');
    expect(simple.tracks).toEqual([
      { track_name: 'Title', artist_name: 'Artist', album_name: '', duration_ms: 0 },
    ]);
  });
});

describe('importFileAutoMapColumns (differential)', () => {
  const HEADER_SETS = [
    ['Track Name', 'Artist Name', 'Album Name', 'Duration'],
    ['name', 'artists', 'album', 'duration_ms'],
    ['Title', 'Performer', 'Length'],
    ['song', 'artist', 'time'],
    ['col1', 'col2'],
    ['track', 'track name'], // first pattern hit wins
    [],
  ];

  it('matches the vanilla on every header set', () => {
    for (const headers of HEADER_SETS) {
      expect(importFileAutoMapColumns(headers), JSON.stringify(headers)).toEqual(
        parser._importFileAutoMapColumns(headers),
      );
    }
  });

  it('pins the pattern priority as literals', () => {
    expect(importFileAutoMapColumns(['name', 'artists', 'album', 'duration_ms'])).toEqual({
      0: 'track_name',
      1: 'artist_name',
      2: 'album_name',
      3: 'duration',
    });
    expect(importFileAutoMapColumns(['col1', 'col2'])).toEqual({});
  });
});

describe('importTracksFromText / importTracksFromCsv (differential vs _importFileBuildTracks)', () => {
  /**
   * The vanilla builder reads _importFileState + the two format selects; run
   * its REAL body against stubs so the pure split stays honest.
   */
  const buildBody = extractFunction('_importFileBuildTracks', STATS_SRC);
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const runVanillaBuild = new Function(
    'state',
    'domValues',
    `
    const _importFileState = state;
    const document = { getElementById: (id) => (id in domValues ? { value: domValues[id] } : null) };
    ${buildBody}
    _importFileBuildTracks();
    return state.parsedTracks;
  `,
  ) as (state: Record<string, unknown>, domValues: Record<string, string>) => unknown[];

  it('text mode matches for both orders and odd separators', () => {
    const LINES = [
      'Kendrick Lamar - HUMBLE.',
      'A - B - C', // extra separator joins into the second half
      'no separator here',
      'X | Y',
    ];
    for (const order of ['artist-title', 'title-artist'] as const) {
      for (const sep of [' - ', ' | ']) {
        const theirs = runVanillaBuild(
          { fileType: 'text', rows: LINES, columnMap: {} },
          { 'import-file-text-order': order, 'import-file-text-separator': sep },
        );
        expect(importTracksFromText(LINES, order, sep), `${order} ${sep}`).toEqual(theirs);
      }
    }
  });

  it('csv mode matches, duration heuristic included', () => {
    const rows = [
      ['HUMBLE.', 'Kendrick Lamar', 'DAMN.', '3:33'],
      ['Hello', 'Adele', '25', '295'],
      ['Long', 'X', 'Y', '213000'],
      ['Bad', 'Z', 'W', 'abc'],
      ['NoDur', 'N', 'M', ''],
    ];
    const columnMap: Record<number, ImportColumnRole> = {
      0: 'track_name',
      1: 'artist_name',
      2: 'album_name',
      3: 'duration',
    };
    const theirs = runVanillaBuild({ fileType: 'csv', rows, columnMap }, {});
    expect(importTracksFromCsv(rows, columnMap)).toEqual(theirs);
  });

  it('pins the duration heuristic as literals', () => {
    const one = (dur: string) =>
      importTracksFromCsv([[dur]], { 0: 'duration' } as Record<number, ImportColumnRole>)[0]
        .duration_ms;
    expect(one('3:33')).toBe(213000);
    expect(one('295')).toBe(295000); // <= 10000 reads as seconds
    expect(one('213000')).toBe(213000); // > 10000 reads as ms already
    expect(one('abc')).toBe(0);
  });
});

describe('buildMirrorPayload (differential vs mirrorPlaylist)', () => {
  const mirrorBody = extractFunction('mirrorPlaylist', STATS_SRC);
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const runVanillaMirror = new Function(
    'source',
    'sourceId',
    'name',
    'tracks',
    'metadata',
    `
    let captured = null;
    const fetch = (url, opts) => {
      captured = JSON.parse(opts.body);
      return Promise.resolve({ json: () => Promise.resolve({}) });
    };
    const console = { log: () => {}, warn: () => {} };
    ${mirrorBody}
    mirrorPlaylist(source, sourceId, name, tracks, metadata);
    return captured;
  `,
  ) as (
    source: string,
    sourceId: string | number,
    name: string,
    tracks: MirrorSourceTrack[],
    metadata: Record<string, string>,
  ) => unknown;

  const TRACKS: MirrorSourceTrack[] = [
    // Spotify shape: artists as objects, album object with images
    {
      name: 'HUMBLE.',
      artists: [{ name: 'Kendrick Lamar' }],
      album: { name: 'DAMN.', images: [{ url: 'http://img/1' }] },
      duration_ms: 213000,
      id: 'sp1',
    },
    // Flat mirror shape
    {
      track_name: 'Hello',
      artist_name: 'Adele',
      album_name: '25',
      duration_ms: 295000,
      source_track_id: 'd1',
    },
    // Artists as string array / bare string, album as string
    { name: 'X', artists: ['A', 'B'], album: 'Alb' },
    { name: 'Y', artists: 'Solo', spotify_track_id: 'sp2' },
    // extra_data passthrough + empty track
    { name: 'Z', extra_data: { discovered: true, confidence: 1 } },
    {},
  ];

  it('the payload matches the vanilla wire body byte for byte', () => {
    const ours = buildMirrorPayload('tidal', 12345, 'My List', TRACKS, {
      owner: 'me',
      description: 'https://x',
      image_url: 'http://img/c',
    });
    const theirs = runVanillaMirror('tidal', 12345, 'My List', TRACKS, {
      owner: 'me',
      description: 'https://x',
      image_url: 'http://img/c',
    });
    expect(ours).toEqual(theirs);
  });

  it('pins the tolerant extraction as literals', () => {
    const p = buildMirrorPayload('s', 1, 'n', TRACKS);
    expect(p.source_playlist_id).toBe('1');
    expect(p.tracks[0]).toEqual({
      track_name: 'HUMBLE.',
      artist_name: 'Kendrick Lamar',
      album_name: 'DAMN.',
      duration_ms: 213000,
      image_url: 'http://img/1',
      source_track_id: 'sp1',
      extra_data: null,
    });
    expect(p.tracks[2].artist_name).toBe('A'); // first of a string array
    expect(p.tracks[3].artist_name).toBe('Solo');
    expect(p.tracks[3].source_track_id).toBe('sp2');
    expect(p.tracks[4].extra_data).toEqual({ discovered: true, confidence: 1 });
    expect(p.tracks[5]).toEqual({
      track_name: '',
      artist_name: '',
      album_name: '',
      duration_ms: 0,
      image_url: null,
      source_track_id: '',
      extra_data: null,
    });
  });
});
