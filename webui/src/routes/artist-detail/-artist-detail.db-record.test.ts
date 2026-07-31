import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  copyRecordText,
  downloadRecord,
  jsonHighlightTokens,
  matchesRecordFilter,
  recordFileName,
  recordFooterStats,
  recordRows,
  showsDbRecordButton,
} from './-artist-detail.db-record';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('showsDbRecordButton', () => {
  it('is library-artists-only', () => {
    expect(showsDbRecordButton({ id: 42 }, false)).toBe(true);
    // No DB row to inspect for an artist that only exists on a source.
    expect(showsDbRecordButton({ id: 'sp1' }, true)).toBe(false);
    expect(showsDbRecordButton({}, false)).toBe(false);
    expect(showsDbRecordButton(undefined, false)).toBe(false);
  });
});

describe('recordRows', () => {
  it('renders every empty shape as a single "null" row', () => {
    const rows = recordRows({ a: null, b: undefined, c: '' });
    expect(rows.every((r) => r.isEmpty && r.text === 'null')).toBe(true);
  });

  it('copies an EMPTY value as empty, not as the word null', () => {
    // Copying "null" would be a lie about what the column holds.
    expect(recordRows({ a: null })[0].copyValue).toBe('');
  });

  it('shows objects as compact JSON and copies the same string', () => {
    const [row] = recordRows({ meta: { x: 1 } });
    expect(row.isJson).toBe(true);
    expect(row.text).toBe('{"x":1}');
    expect(row.copyValue).toBe('{"x":1}');
  });

  it('stringifies scalars, including a falsy 0 and false', () => {
    const rows = recordRows({ n: 0, b: false });
    expect(rows.map((r) => [r.text, r.isEmpty])).toEqual([
      ['0', false],
      ['false', false],
    ]);
  });

  it('indexes both the field NAME and its value for filtering', () => {
    const [row] = recordRows({ Spotify_ID: 'ABC123' });
    expect(row.filterKey).toBe('spotify_id abc123');
  });
});

describe('matchesRecordFilter', () => {
  const [row] = recordRows({ spotify_id: 'ABC123' });

  it('matches on the field name or the value, case-insensitively', () => {
    expect(matchesRecordFilter(row, 'SPOT')).toBe(true);
    expect(matchesRecordFilter(row, 'abc')).toBe(true);
    expect(matchesRecordFilter(row, 'deezer')).toBe(false);
  });

  it('shows everything for an empty or whitespace query', () => {
    expect(matchesRecordFilter(row, '')).toBe(true);
    expect(matchesRecordFilter(row, '   ')).toBe(true);
  });
});

describe('recordFooterStats', () => {
  it('counts fields, and sources by match_status === matched', () => {
    const stats = recordFooterStats({
      artist_id: 42,
      counts: { albums: 12, tracks: 140 },
      record: {
        spotify_match_status: 'matched',
        deezer_match_status: 'unmatched',
        itunes_match_status: 'matched',
        name: 'Aphex Twin',
      },
    });
    expect(stats.fields).toBe(4);
    expect(stats.matched).toBe(2);
    expect(stats.albums).toBe('12');
    expect(stats.tracks).toBe('140');
    expect(stats.id).toBe('42');
  });

  it('counts only match_status FIELDS, not any field whose value is "matched"', () => {
    // A free-text column that happens to say "matched" is not a matched source.
    expect(
      recordFooterStats({
        record: {
          spotify_artist_id: 'abc',
          spotify_match_status: 'pending',
          notes: 'matched',
          last_action: 'matched',
        },
      }).matched,
    ).toBe(0);
  });

  it('shows an en dash for an unknown count, not zero', () => {
    const stats = recordFooterStats({ record: {} });
    expect(stats.albums).toBe('–');
    expect(stats.tracks).toBe('–');
  });

  it('keeps a real zero as zero', () => {
    expect(recordFooterStats({ counts: { albums: 0 }, record: {} }).albums).toBe('0');
  });
});

describe('jsonHighlightTokens', () => {
  const tokens = jsonHighlightTokens({ name: 'Aphex', count: 12, ok: true, missing: null });
  const classOf = (text: string) => tokens.find((t) => t.text === text)?.className;

  it('classes keys apart from string values', () => {
    // A key is a string token FOLLOWED by a colon — that colon is part of the
    // match, which is why the two look different.
    expect(classOf('"name":')).toBe('tok-key');
    expect(classOf('"Aphex"')).toBe('tok-str');
  });

  it('classes numbers, booleans and nulls', () => {
    expect(classOf('12')).toBe('tok-num');
    expect(classOf('true')).toBe('tok-bool');
    expect(classOf('null')).toBe('tok-null');
  });

  it('loses nothing — the tokens rejoin into the original JSON', () => {
    const value = { a: [1, 2], b: 'x"y', c: -1.5e3 };
    expect(
      jsonHighlightTokens(value)
        .map((t) => t.text)
        .join(''),
    ).toBe(JSON.stringify(value, null, 2));
  });

  it('survives an empty record', () => {
    expect(
      jsonHighlightTokens({})
        .map((t) => t.text)
        .join(''),
    ).toBe('{}');
  });
});

describe('recordFileName', () => {
  it('replaces unsafe characters with underscores', () => {
    expect(recordFileName('AC/DC: Live!')).toBe('AC_DC_Live__db_record.json');
  });

  it('caps the artist part at 60 characters', () => {
    const name = recordFileName('a'.repeat(200));
    expect(name).toBe(`${'a'.repeat(60)}_db_record.json`);
  });

  it('falls back to "artist" for an empty name', () => {
    expect(recordFileName('')).toBe('artist_db_record.json');
  });

  it('does NOT fall back for a name that sanitises to punctuation', () => {
    // '!!!' collapses to a single underscore, which is truthy, so the vanilla's
    // `|| 'artist'` never fires. Reproduced rather than tidied.
    expect(recordFileName('!!!')).toBe('__db_record.json');
  });
});

describe('copyRecordText', () => {
  it('uses the async clipboard in a secure context', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('isSecureContext', true);

    await copyRecordText('hello');
    expect(writeText).toHaveBeenCalledWith('hello');
  });

  it('falls back to a textarea over plain http EVEN WITH a clipboard available', async () => {
    // SoulSync is usually served over http on a LAN, so this is the COMMON
    // path. The browser still exposes navigator.clipboard there — it just
    // rejects — so the secure-context check is what actually routes this.
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('isSecureContext', false);
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec as never;

    await copyRecordText('hello');
    expect(writeText).not.toHaveBeenCalled();
    expect(exec).toHaveBeenCalledWith('copy');
    // The textarea is removed again rather than left in the DOM.
    expect(document.querySelector('textarea')).toBeNull();
  });

  it('falls back when the clipboard write REJECTS', async () => {
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    vi.stubGlobal('isSecureContext', true);
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec as never;

    await copyRecordText('hello');
    expect(exec).toHaveBeenCalledWith('copy');
  });

  it('copies an empty string rather than the text "null"', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('isSecureContext', true);

    await copyRecordText(null);
    expect(writeText).toHaveBeenCalledWith('');
  });
});

describe('downloadRecord', () => {
  it('saves pretty-printed JSON under the safe name and cleans up', () => {
    const createObjectURL = vi.fn().mockReturnValue('blob:x');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });
    vi.useFakeTimers();

    const name = downloadRecord({ a: 1 }, 'Aphex Twin');

    expect(name).toBe('Aphex_Twin_db_record.json');
    expect(createObjectURL).toHaveBeenCalled();
    // The anchor is not left behind in the body.
    expect(document.querySelector('a[download]')).toBeNull();

    vi.advanceTimersByTime(1000);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:x');
    vi.useRealTimers();
  });
});
