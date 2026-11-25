import { describe, expect, it } from 'vitest';

import {
  batches,
  emptyTally,
  endedMs,
  isHistoryFile,
  leftOutLine,
  parseHistoryJson,
  parseRecord,
  readSpotifyExport,
  readZipEntries,
  summaryLine,
  withoutOverlap,
} from './-spotify-history';

// the two export shapes, as Spotify writes them

const ACCOUNT_DATA = [
  { endTime: '2019-03-02 21:14', artistName: 'Band', trackName: 'Old Song', msPlayed: 120000 },
  { endTime: '2019-03-02 21:15', artistName: 'Band', trackName: 'Skipped', msPlayed: 4000 },
];

const ACCOUNT_PODCAST = [
  { endTime: '2019-03-02 22:00', podcastName: 'A Show', episodeName: 'Ep 1', msPlayed: 900000 },
];

const EXTENDED = [
  {
    ts: '2024-01-15T08:23:45Z',
    ms_played: 180000,
    master_metadata_track_name: 'Song',
    master_metadata_album_artist_name: 'Band',
    master_metadata_album_album_name: 'Album',
    spotify_track_uri: 'spotify:track:4uLU6hMCjMI75M1A2tKUQC',
    reason_end: 'trackdone',
    skipped: false,
    shuffle: false,
    incognito_mode: false,
    episode_name: null,
    episode_show_name: null,
    spotify_episode_uri: null,
    audiobook_title: null,
    audiobook_uri: null,
    audiobook_chapter_uri: null,
  },
  {
    ts: '2024-01-15T09:00:00Z',
    ms_played: 200000,
    master_metadata_track_name: 'Secret',
    master_metadata_album_artist_name: 'Band',
    spotify_track_uri: 'spotify:track:7ouMYWpwJ422jRcDASZB7P',
    incognito_mode: true,
  },
  {
    ts: '2024-01-15T10:00:00Z',
    ms_played: 1800000,
    master_metadata_track_name: null,
    master_metadata_album_artist_name: null,
    episode_name: 'Episode',
    episode_show_name: 'Show',
    spotify_episode_uri: 'spotify:episode:abc',
    incognito_mode: false,
  },
  {
    ts: '2024-01-15T11:00:00Z',
    ms_played: 600000,
    master_metadata_track_name: null,
    audiobook_title: 'A Book',
    audiobook_uri: 'spotify:show:book',
  },
  {
    ts: '2024-01-15T12:00:00Z',
    ms_played: 12000,
    master_metadata_track_name: 'Skipped',
    master_metadata_album_artist_name: 'Band',
    spotify_track_uri: 'spotify:track:7ouMYWpwJ422jRcDASZB7P',
    skipped: true,
  },
];

describe('parsing the export', () => {
  it('keeps an extended play with its track uri', () => {
    expect(parseRecord(EXTENDED[0])).toEqual({
      ended_at: '2024-01-15T08:23:45Z',
      ms_played: 180000,
      track: 'Song',
      artist: 'Band',
      album: 'Album',
      uri: 'spotify:track:4uLU6hMCjMI75M1A2tKUQC',
    });
  });

  it('keeps an account-data play as minute precision', () => {
    expect(parseRecord(ACCOUNT_DATA[0])).toEqual({
      ended_at: '2019-03-02 21:14',
      ms_played: 120000,
      track: 'Old Song',
      artist: 'Band',
      coarse: true,
    });
  });

  it('drops private session plays, podcasts, audiobooks and plays under 30 seconds', () => {
    expect(EXTENDED.slice(1).map(parseRecord)).toEqual(['private', 'podcast', 'podcast', 'short']);
    expect(parseRecord(ACCOUNT_DATA[1])).toBe('short');
    expect(parseRecord(ACCOUNT_PODCAST[0])).toBe('podcast');
  });

  it('a play of exactly 30 seconds counts', () => {
    expect(typeof parseRecord({ ...EXTENDED[0], ms_played: 30000 })).toBe('object');
    expect(parseRecord({ ...EXTENDED[0], ms_played: 29999 })).toBe('short');
  });

  it('nothing private leaves in a row', () => {
    const tally = emptyTally();
    const rows = parseHistoryJson(EXTENDED, tally);
    expect(rows.map((r) => r.track)).toEqual(['Song']);
    const sent = JSON.stringify(rows);
    expect(sent).not.toContain('Secret');
    expect(sent).not.toContain('incognito');
    expect(sent).not.toContain('Episode');
    expect(tally).toMatchObject({ kept: 1, private: 1, podcast: 2, short: 1 });
  });

  it('a file that is not history counts as unreadable', () => {
    const tally = emptyTally();
    expect(parseHistoryJson({ not: 'a list' }, tally)).toEqual([]);
    expect(parseHistoryJson([{ something: 'else' }], tally)).toEqual([]);
    expect(tally.unreadable).toBe(2);
  });

  it('picks the history files out of an export by name', () => {
    expect(isHistoryFile('Spotify Account Data/StreamingHistory_music_0.json')).toBe(true);
    expect(isHistoryFile('Spotify Account Data/StreamingHistory0.json')).toBe(true);
    expect(
      isHistoryFile('Spotify Extended Streaming History/Streaming_History_Audio_2015-2017_0.json'),
    ).toBe(true);
    expect(isHistoryFile('MyData/endsong_3.json')).toBe(true);
    expect(isHistoryFile('Spotify Account Data/StreamingHistory_podcast_0.json')).toBe(false);
    expect(isHistoryFile('Streaming_History_Video_2020-2024.json')).toBe(false);
    expect(isHistoryFile('Spotify Account Data/YourLibrary.json')).toBe(false);
    expect(isHistoryFile('Spotify Account Data/Userdata.json')).toBe(false);
  });
});

// ── a zip, built here the way any zip tool would ──────────────────────────────

async function deflateRaw(data: Uint8Array): Promise<Uint8Array> {
  const stream = new CompressionStream('deflate-raw');
  const writer = stream.writable.getWriter();
  void writer.write(data as Uint8Array<ArrayBuffer>);
  void writer.close();
  return new Uint8Array(await new Response(stream.readable).arrayBuffer());
}

async function makeZip(
  entries: { name: string; text: string }[],
  { deflate = true } = {},
): Promise<Uint8Array> {
  const enc = new TextEncoder();
  const locals: number[] = [];
  const central: number[] = [];
  const u16 = (n: number) => [n & 0xff, (n >> 8) & 0xff];
  const u32 = (n: number) => [n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >>> 24) & 0xff];
  for (const entry of entries) {
    const name = enc.encode(entry.name);
    const raw = enc.encode(entry.text);
    const data = deflate ? await deflateRaw(raw) : raw;
    const method = deflate ? 8 : 0;
    const offset = locals.length;
    // crc is left 0: the reader does not check it
    locals.push(
      ...u32(0x04034b50),
      ...u16(20),
      ...u16(0),
      ...u16(method),
      ...u16(0),
      ...u16(0),
      ...u32(0),
      ...u32(data.length),
      ...u32(raw.length),
      ...u16(name.length),
      ...u16(0),
      ...name,
      ...data,
    );
    central.push(
      ...u32(0x02014b50),
      ...u16(20),
      ...u16(20),
      ...u16(0),
      ...u16(method),
      ...u16(0),
      ...u16(0),
      ...u32(0),
      ...u32(data.length),
      ...u32(raw.length),
      ...u16(name.length),
      ...u16(0),
      ...u16(0),
      ...u16(0),
      ...u16(0),
      ...u32(0),
      ...u32(offset),
      ...name,
    );
  }
  const end = [
    ...u32(0x06054b50),
    ...u16(0),
    ...u16(0),
    ...u16(entries.length),
    ...u16(entries.length),
    ...u32(central.length),
    ...u32(locals.length),
    ...u16(0),
  ];
  return new Uint8Array([...locals, ...central, ...end]);
}

const EXPORT_ENTRIES = [
  {
    name: 'Spotify Account Data/StreamingHistory_music_0.json',
    text: JSON.stringify(ACCOUNT_DATA),
  },
  {
    name: 'Spotify Account Data/StreamingHistory_podcast_0.json',
    text: JSON.stringify(ACCOUNT_PODCAST),
  },
  {
    name: 'Spotify Extended Streaming History/Streaming_History_Audio_2024.json',
    text: JSON.stringify(EXTENDED),
  },
  { name: 'Spotify Account Data/Userdata.json', text: '{"username":"someone"}' },
];

describe('reading the zip', () => {
  it('reads the deflated history entries and skips the rest', async () => {
    const zip = await makeZip(EXPORT_ENTRIES);
    const entries = await readZipEntries(zip.buffer as ArrayBuffer, isHistoryFile);
    expect(entries.map((e) => e.name)).toEqual([
      'Spotify Account Data/StreamingHistory_music_0.json',
      'Spotify Extended Streaming History/Streaming_History_Audio_2024.json',
    ]);
    expect(JSON.parse(entries[0].text)).toEqual(ACCOUNT_DATA);
  });

  it('reads stored entries too', async () => {
    const zip = await makeZip(EXPORT_ENTRIES, { deflate: false });
    const entries = await readZipEntries(zip.buffer as ArrayBuffer, isHistoryFile);
    expect(JSON.parse(entries[1].text)).toEqual(EXTENDED);
  });

  it('a file that is not a zip says so', async () => {
    await expect(
      readZipEntries(new TextEncoder().encode('{"a": 1}').buffer as ArrayBuffer, () => true),
    ).rejects.toThrow(/not a zip/);
  });

  it('a whole export: the zip, or the loose json files, give the same plays', async () => {
    const zip = await makeZip(EXPORT_ENTRIES);
    const fromZip = await readSpotifyExport([new File([zip as BlobPart], 'my_spotify_data.zip')]);
    const fromFiles = await readSpotifyExport(
      EXPORT_ENTRIES.map((e) => new File([e.text], e.name.split('/').pop() ?? e.name)),
    );
    expect(fromZip.rows.map((r) => r.track)).toEqual(['Old Song', 'Song']);
    expect(fromFiles.rows).toEqual(fromZip.rows);
    expect(fromZip.tally).toMatchObject({ files: 2, kept: 2, short: 2, private: 1, podcast: 2 });
  });
});

describe('both shapes in one export', () => {
  it('drops the account-data plays the extended history already covers, keeps older ones', () => {
    const tally = emptyTally();
    const extended = parseHistoryJson(
      [
        { ...EXTENDED[0], ts: '2024-01-15T08:23:45Z' },
        { ...EXTENDED[0], ts: '2024-03-01T10:00:00Z' },
      ],
      tally,
    );
    const account = parseHistoryJson(
      [
        { ...ACCOUNT_DATA[0], endTime: '2024-01-15 08:23' },
        { ...ACCOUNT_DATA[0], endTime: '2023-06-01 09:00' },
      ],
      tally,
    );
    const kept = withoutOverlap([...account, ...extended], tally);
    expect(kept.map((r) => r.ended_at)).toEqual([
      '2023-06-01 09:00',
      '2024-01-15T08:23:45Z',
      '2024-03-01T10:00:00Z',
    ]);
    expect(tally.overlap).toBe(1);
    expect(tally.kept).toBe(3);
  });

  it('reads the two time shapes as the same clock', () => {
    expect(endedMs({ ended_at: '2024-01-15 08:23', coarse: true } as never)).toBe(
      Date.parse('2024-01-15T08:23:00Z'),
    );
  });

  it('sends the plays oldest first', async () => {
    const files = [
      new File(
        [JSON.stringify([{ ...EXTENDED[0], ts: '2024-05-01T00:00:00Z' }])],
        'endsong_1.json',
      ),
      new File(
        [JSON.stringify([{ ...EXTENDED[0], ts: '2020-05-01T00:00:00Z' }])],
        'endsong_0.json',
      ),
    ];
    const { rows } = await readSpotifyExport(files);
    expect(rows.map((r) => r.ended_at)).toEqual(['2020-05-01T00:00:00Z', '2024-05-01T00:00:00Z']);
  });
});

describe('batches and words', () => {
  it('splits rows into batches', () => {
    expect(batches([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
    expect(batches([], 2)).toEqual([]);
  });

  it('says what came in', () => {
    expect(
      summaryLine({
        imported: true,
        include_in_stats: true,
        plays: 12340,
        first_played: '2015-02-01 10:00:00',
        last_played: '2024-11-01 10:00:00',
        artists: 3100,
        matched: 9800,
        linked: 0,
      }),
    ).toBe('12,340 plays from 2015 to 2024, 3,100 artists, 9,800 matched to your library');
  });

  it('says what stayed on the device', () => {
    expect(leftOutLine({ ...emptyTally(), short: 2, podcast: 1, private: 1 })).toBe(
      'Left out: 2 plays under 30 seconds, 1 podcast or video, 1 private session play.',
    );
    expect(leftOutLine(emptyTally())).toBe('');
  });
});
