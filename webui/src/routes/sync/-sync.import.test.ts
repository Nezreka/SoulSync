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

// Explicit parser contracts replace comparisons with the retired browser importer.
function track(track_name: string, artist_name = '', duration_ms = 0) {
  return { track_name, artist_name, album_name: '', duration_ms };
}

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

describe('importFileDetectDelimiter', () => {
  it.each([
    ['a,b,c', ','],
    ['a\tb\tc', '\t'],
    ['a;b;c', ';'],
    ['a,b;c', ';'],
    ['a\tb,c;d', '\t'],
    ['plain header', ','],
    ['', ','],
    ['a,b\tc\td', '\t'],
    ['x;y,z,w', ','],
  ])('detects the delimiter for %j, including ties', (line, delimiter) => {
    expect(importFileDetectDelimiter(line)).toBe(delimiter);
  });
});

describe('importFileParseCsv', () => {
  it.each([
    [
      'name,artist\nHUMBLE.,Kendrick Lamar\nDNA.,Kendrick Lamar',
      ',',
      ['name', 'artist'],
      [
        ['HUMBLE.', 'Kendrick Lamar'],
        ['DNA.', 'Kendrick Lamar'],
      ],
    ],
    ['name\tartist\nA\tB', '\t', ['name', 'artist'], [['A', 'B']]],
    ['name,artist\n"Hello, World",Adele', ',', ['name', 'artist'], [['Hello, World', 'Adele']]],
    ['name,artist\n"She said ""hi""",X', ',', ['name', 'artist'], [['She said "hi"', 'X']]],
    ['header only', ',', [], []],
    ['name,artist\n\n , \nA,B', ',', ['name', 'artist'], [['A', 'B']]],
    ['name;artist\nA;B', ';', ['name', 'artist'], [['A', 'B']]],
    ['"quoted,header",b\nv1,v2', ',', ['quoted,header', 'b'], [['v1', 'v2']]],
    ['\n\nname,artist\nA,B', ',', ['name', 'artist'], [['A', 'B']]],
    ['name,artist\n\n', ',', [], []],
  ] as [string, string, string[], string[][]][])('parses %j', (text, delimiter, headers, rows) => {
    expect(importFileParseCsv(text, delimiter)).toEqual({ headers, rows });
  });
});

describe('importFileParseM3u', () => {
  it.each([
    [
      '#EXTM3U\n#PLAYLIST:My List\n#EXTINF:213,Kendrick Lamar - HUMBLE.\n/music/humble.mp3\n#EXTINF:180,Adele - Hello\n/music/hello.flac',
      'My List',
      [track('HUMBLE.', 'Kendrick Lamar', 213000), track('Hello', 'Adele', 180000)],
    ],
    [
      '#EXTM3U\n#EXTINF:213,Kendrick Lamar - HUMBLE.\n# MISSING: not located\n#EXTINF:180,Adele - Hello\n/music/hello.flac',
      '',
      [track('HUMBLE.', 'Kendrick Lamar', 213000), track('Hello', 'Adele', 180000)],
    ],
    ['#EXTM3U\n#EXTINF:100,A - B', '', [track('B', 'A', 100000)]],
    [
      '/music/Artist - Title.mp3\nC:\\music\\Other Artist - Other Title.flac\nnodash.mp3',
      '',
      [track('Title', 'Artist'), track('Other Title', 'Other Artist'), track('nodash')],
    ],
    [
      '#EXTINF:-1,X - Y\npath.mp3\n#EXTINF:abc,Z - W\npath2.mp3\n#EXTINF:90\npath3.mp3',
      '',
      [track('Y', 'X'), track('W', 'Z'), track('path3')],
    ],
    ['#extinf:60,a - b\n\n#playlist:Lower\npath.mp3', 'Lower', [track('b', 'a', 60000)]],
    ['', '', []],
    ['#EXTM3U\n#EXTINF:10,', '', []],
    ['#EXTM3U\n#EXTINF:10,\n/music/Band - Song.mp3', '', [track('Song', 'Band')]],
  ] as [string, string, ReturnType<typeof track>[]][])(
    'parses %j',
    (text, playlistName, tracks) => {
      expect(importFileParseM3u(text)).toEqual({ playlistName, tracks });
    },
  );
});

describe('importFileAutoMapColumns', () => {
  it.each([
    [
      ['Track Name', 'Artist Name', 'Album Name', 'Duration'],
      { 0: 'track_name', 1: 'artist_name', 2: 'album_name', 3: 'duration' },
    ],
    [
      ['name', 'artists', 'album', 'duration_ms'],
      { 0: 'track_name', 1: 'artist_name', 2: 'album_name', 3: 'duration' },
    ],
    [['Title', 'Performer', 'Length'], { 0: 'track_name', 1: 'artist_name', 2: 'duration' }],
    [['song', 'artist', 'time'], { 0: 'track_name', 1: 'artist_name', 2: 'duration' }],
    [['col1', 'col2'], {}],
    [['track', 'track name'], { 1: 'track_name' }],
    [[], {}],
  ] as [string[], Record<number, ImportColumnRole>][])(
    'maps %j using pattern priority',
    (headers, expected) => {
      expect(importFileAutoMapColumns(headers)).toEqual(expected);
    },
  );
});

describe('importTracksFromText', () => {
  const lines = ['Kendrick Lamar - HUMBLE.', 'A - B - C', 'no separator here', 'X | Y'];
  it.each([
    [
      'artist-title',
      ' - ',
      [
        track('HUMBLE.', 'Kendrick Lamar'),
        track('B - C', 'A'),
        track('no separator here'),
        track('X | Y'),
      ],
    ],
    [
      'title-artist',
      ' - ',
      [
        track('Kendrick Lamar', 'HUMBLE.'),
        track('A', 'B - C'),
        track('no separator here'),
        track('X | Y'),
      ],
    ],
    ['artist-title', ' | ', [track(lines[0]), track(lines[1]), track(lines[2]), track('Y', 'X')]],
    ['title-artist', ' | ', [track(lines[0]), track(lines[1]), track(lines[2]), track('X', 'Y')]],
  ] as ['artist-title' | 'title-artist', string, ReturnType<typeof track>[]][])(
    'parses %s with %j',
    (order, separator, expected) => {
      expect(importTracksFromText(lines, order, separator)).toEqual(expected);
    },
  );
});

describe('importTracksFromCsv', () => {
  it('maps every column and preserves album names', () => {
    const rows = [
      ['HUMBLE.', 'Kendrick Lamar', 'DAMN.', '3:33'],
      ['Hello', 'Adele', '25', '295'],
      ['Long', 'X', 'Y', '213000'],
      ['Bad', 'Z', 'W', 'abc'],
      ['NoDur', 'N', 'M', ''],
    ];
    expect(
      importTracksFromCsv(rows, {
        0: 'track_name',
        1: 'artist_name',
        2: 'album_name',
        3: 'duration',
      }),
    ).toEqual([
      { ...track('HUMBLE.', 'Kendrick Lamar', 213000), album_name: 'DAMN.' },
      { ...track('Hello', 'Adele', 295000), album_name: '25' },
      { ...track('Long', 'X', 213000), album_name: 'Y' },
      { ...track('Bad', 'Z'), album_name: 'W' },
      { ...track('NoDur', 'N'), album_name: 'M' },
    ]);
  });

  it.each([
    ['3:33', 213000],
    ['295', 295000],
    ['213000', 213000],
    ['abc', 0],
    ['5000', 5000000],
    ['10000', 10000000],
    ['10001', 10001],
  ])('interprets duration %s with the >10000 milliseconds boundary', (duration, expected) => {
    expect(importTracksFromCsv([[String(duration)]], { 0: 'duration' })[0].duration_ms).toBe(
      expected,
    );
  });
});

describe('buildMirrorPayload (differential vs the surviving mirrorPlaylist)', () => {
  const source = readFileSync(resolve(process.cwd(), 'static/stats-automations.js'), 'utf8');
  const mirrorBody = extractFunction('mirrorPlaylist', source);
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

  const tracks: MirrorSourceTrack[] = [
    {
      name: 'HUMBLE.',
      artists: [{ name: 'Kendrick Lamar' }],
      album: { name: 'DAMN.', images: [{ url: 'http://img/1' }] },
      duration_ms: 213000,
      id: 'sp1',
    },
    {
      track_name: 'Hello',
      artist_name: 'Adele',
      album_name: '25',
      duration_ms: 295000,
      source_track_id: 'd1',
    },
    { name: 'X', artists: ['A', 'B'], album: 'Alb' },
    { name: 'Y', artists: 'Solo', spotify_track_id: 'sp2' },
    { name: 'Z', extra_data: { discovered: true, confidence: 1 } },
    {},
    { name: 'EmptyArtists', artists: [] },
  ];

  it('matches the live legacy mirror wire body, including dropped undefined keys', () => {
    const metadata = { owner: 'me', description: 'https://x', image_url: 'http://img/c' };
    const ours = buildMirrorPayload('tidal', 12345, 'My List', tracks, metadata);
    expect(JSON.parse(JSON.stringify(ours))).toEqual(
      runVanillaMirror('tidal', 12345, 'My List', tracks, metadata),
    );
  });

  it('pins tolerant field extraction and the wire omission for empty artists', () => {
    const payload = buildMirrorPayload('s', 1, 'n', tracks);
    expect(payload.source_playlist_id).toBe('1');
    expect(payload.tracks[0]).toEqual({
      track_name: 'HUMBLE.',
      artist_name: 'Kendrick Lamar',
      album_name: 'DAMN.',
      duration_ms: 213000,
      image_url: 'http://img/1',
      source_track_id: 'sp1',
      extra_data: null,
    });
    expect(payload.tracks[2].artist_name).toBe('A');
    expect(payload.tracks[3].artist_name).toBe('Solo');
    expect(payload.tracks[3].source_track_id).toBe('sp2');
    expect(payload.tracks[4].extra_data).toEqual({ discovered: true, confidence: 1 });
    expect(payload.tracks[5]).toEqual({
      track_name: '',
      artist_name: '',
      album_name: '',
      duration_ms: 0,
      image_url: null,
      source_track_id: '',
      extra_data: null,
    });
    expect(JSON.parse(JSON.stringify(payload.tracks[6]))).not.toHaveProperty('artist_name');
  });
});
