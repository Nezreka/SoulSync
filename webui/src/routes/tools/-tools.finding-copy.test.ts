import { describe, expect, it } from 'vitest';

import { describeFinding, listWords, tagCheckLabel } from './-tools.finding-copy';

const scan = (reference: Record<string, unknown>, checks: Record<string, string>) => ({
  finding_type: 'library_retag',
  title: 'Metadata issues',
  description: 'genre: mismatch',
  details: { validation: { reference, checks } },
});

describe('describeFinding: a tag-scan finding', () => {
  // every one is stored as "Metadata issues" / "genre: mismatch"
  it('leads with the track and says what differs as a sentence', () => {
    expect(
      describeFinding(
        scan(
          { title: 'Wolves', artist_name: 'Crystal Castles', album_title: 'Wolves EP' },
          { title: 'correct', genre: 'mismatch', year: 'mismatch', cover: 'missing' },
        ),
        'Apply Tags',
      ),
    ).toEqual({
      title: 'Wolves',
      sub: 'Crystal Castles · Wolves EP',
      problem:
        'Genre and year in the file do not match the library. Cover is missing from the file.',
      fix: "Apply Tags writes the library's genre, year and cover into the file. Nothing else in it changes.",
    });
  });

  it('one field reads in the singular', () => {
    const copy = describeFinding(scan({ title: 'Song' }, { genre: 'mismatch' }), 'Apply Tags');
    expect(copy.problem).toBe('Genre in the file does not match the library.');
  });

  it("prefers the track's own artist credit over the album artist", () => {
    const copy = describeFinding(
      scan(
        { title: 'Song', artist_name: 'Philthy Rich', track_artist: 'Philthy Rich, Fetty Wap' },
        { genre: 'mismatch' },
      ),
    );
    expect(copy.sub).toBe('Philthy Rich, Fetty Wap');
  });

  it('keeps what the job wrote when the finding names no track or no fault', () => {
    for (const finding of [
      scan({ artist_name: 'Somebody' }, { genre: 'mismatch' }),
      scan({ title: 'Song' }, { genre: 'correct' }),
    ]) {
      const copy = describeFinding(finding, 'Apply Tags');
      expect(copy.title).toBe('Metadata issues');
      expect(copy.problem).toBe('genre: mismatch');
      expect(copy.fix).toBe('');
    }
  });
});

describe('describeFinding: plain words per type', () => {
  it('missing lyrics drops the quoted title and the file extension', () => {
    expect(
      describeFinding(
        {
          finding_type: 'missing_lyrics',
          title: 'Missing lyrics: Brown Eyed Girl',
          description: '"Brown Eyed Girl" by Alex Isley has no .lrc — lyrics found on LRClib.',
          details: {
            track_title: 'Brown Eyed Girl',
            artist: 'Alex Isley',
            album_title: 'Brown Eyed Girl',
          },
        },
        'Apply Lyrics',
      ),
    ).toEqual({
      title: 'Brown Eyed Girl',
      sub: 'Alex Isley · Brown Eyed Girl',
      problem: 'No lyrics saved for this track. LRClib has them.',
      fix: 'Apply Lyrics saves the lyrics beside the track and inside its tags.',
    });
  });

  it('an orphan file is its file name, its folder and what it is', () => {
    const copy = describeFinding(
      {
        finding_type: 'orphan_file',
        title: 'Orphan file: 01 - vibrance.mp3',
        description: 'Audio file is not tracked in the database. Measured quality: lossy.',
        file_path: 'H:\\\\Music\\\\.diedlonely\\\\.diedlonely - vibrance\\\\01 - vibrance.mp3',
        details: {
          folder: 'H:\\\\Music\\\\.diedlonely\\\\.diedlonely - vibrance',
          format: 'mp3',
          bitrate: 128,
        },
      },
      'Review & Move',
    );
    expect(copy.title).toBe('01 - vibrance.mp3');
    expect(copy.sub).toBe('.diedlonely - vibrance');
    expect(copy.problem).toBe(
      'This audio file is on disk but not in your library. It is MP3 128 kbps.',
    );
    expect(copy.fix).toContain('staging');
  });

  it('missing cover art says where the cover is missing and who has one', () => {
    const copy = describeFinding(
      {
        finding_type: 'missing_cover_art',
        title: 'Artwork storage gaps: Sprung',
        details: {
          album_title: 'Sprung',
          artist: 'Baron Grand',
          db_missing: false,
          embed_missing: true,
          sidecar_missing: true,
          found_artwork_url: 'https://x/front',
          artwork_source: 'caa',
        },
      },
      'Apply Art',
    );
    expect(copy.title).toBe('Sprung');
    expect(copy.sub).toBe('Baron Grand');
    expect(copy.problem).toBe(
      'No cover in the audio files and the album folder. Cover Art Archive has one.',
    );
  });

  it('an edition finding names the closest release without row ids or ratios', () => {
    const copy = describeFinding(
      {
        finding_type: 'canonical_version',
        title: 'Edition: Bear Hug (Remixes)',
        description: 'discogs release 3209456: 81% fit, 7 owned tracks against 5 release tracks.',
        details: {
          album_title: 'Bear Hug (Remixes)',
          artist_name: 'The 2 Bears',
          file_track_count: 7,
          candidates: [{ source: 'discogs', score: 0.8095, track_count: 5 }],
        },
      },
      'Pin Version',
    );
    expect(copy.problem).toBe(
      'Several editions match this album and none is chosen. The closest is a Discogs release at 81% (you have 7 tracks, it lists 5).',
    );
    expect(copy.fix).toBe(
      'Pin Version records that edition as the one you have. No file is touched.',
    );
  });

  it('a preview clip says both lengths', () => {
    const copy = describeFinding({
      finding_type: 'short_preview_track',
      title: 'Preview clip: Aerodynamic',
      details: { title: 'Aerodynamic', file_duration_s: 30, expected_duration_s: 212 },
    });
    expect(copy.problem).toBe(
      'This file is 30 seconds long. The real track runs 212 seconds, so this is a preview clip.',
    );
  });
});

describe('describeFinding: the fix line is never a guess', () => {
  const corrupt = {
    finding_type: 'corrupt_audio',
    title: 'Corrupt: Voyager',
    details: { title: 'Voyager' },
  };

  it('a fresh-copy fix is described only when the button is one', () => {
    expect(describeFinding(corrupt, 'Re-download').fix).toBe(
      'Re-download lets you pick where to get a fresh copy from.',
    );
    // a file the library does not know can only be deleted, and the row says so
    expect(describeFinding(corrupt, 'Delete File').fix).toBe(
      'Delete File removes this file. It asks first.',
    );
    expect(describeFinding(corrupt, 'Something Else').fix).toBe('');
  });

  it('a finding with no fix button gets no fix line', () => {
    expect(describeFinding(corrupt, null).fix).toBe('');
    expect(describeFinding(scan({ title: 'S' }, { genre: 'mismatch' })).fix).toBe('');
  });

  it('a type nobody wrote copy for keeps its stored words', () => {
    expect(
      describeFinding(
        {
          finding_type: 'brand_new_type',
          title: 'Something odd',
          description: 'As the job wrote it.',
          details: { artist: 'A', album: 'B' },
        },
        'Fix',
      ),
    ).toEqual({ title: 'Something odd', sub: 'A · B', problem: 'As the job wrote it.', fix: '' });
  });
});

describe('words', () => {
  it('joins a list the way a sentence does', () => {
    expect(listWords([])).toBe('');
    expect(listWords(['Genre'])).toBe('Genre');
    expect(listWords(['Genre', 'Year'])).toBe('Genre and Year');
    expect(listWords(['Genre', 'Year', 'Cover'])).toBe('Genre, Year and Cover');
  });

  it('names a tag check, and one it has no word for by its key', () => {
    expect(tagCheckLabel('albumartist')).toBe('Album artist');
    expect(tagCheckLabel('artwork_sidecar')).toBe('Folder cover');
    expect(tagCheckLabel('some_new_check')).toBe('Some new check');
  });
});

describe('describeFinding: a library that could not be reached (#2005)', () => {
  it('says how much, where, and what answering does', () => {
    const copy = describeFinding(
      {
        finding_type: 'library_unreachable',
        title: "1204 of 1300 files can't be reached",
        description: 'x',
        details: { outage: { unreachable: 1204, checked: 1300, example_path: '/music/a.flac' } },
      },
      'They are gone',
    );
    expect(copy.title).toBe("1,204 of 1,300 files can't be reached");
    expect(copy.sub).toBe('/music/a.flac');
    expect(copy.problem).toContain('disconnected drive');
    expect(copy.fix).toBe(
      'If you deleted them yourself, They are gone marks them missing and lists each one under Dead Files.',
    );
  });
});
