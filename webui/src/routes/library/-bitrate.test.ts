import { describe, expect, it } from 'vitest';

import { bitrateKbps, formatBitrate, isVariableBitrate } from './-bitrate';

/**
 * A bitrate number means two different things depending on the codec, and the
 * library was printing both the same way.
 *
 * A 128 kbps MP3 really is 128 kbps for every frame. A 128 kbps Opus file is an
 * AVERAGE — the encoder spends more on hard passages and less on silence, and
 * the number in the tag is whatever the whole file worked out to. Printing them
 * identically invites the comparison "this Opus is worse than that MP3", which
 * is not what the number says: Opus at 128 sounds closer to MP3 at 192.
 *
 * Upstream marks these with a leading `~` and a tooltip (82110a7bb). It reads
 * the codec out of the FILE PATH because its rows carry no format column;
 * Library v2 stores `format` on the file row, so this reads that instead — a
 * path with no extension, or one that lies, cannot mislead it.
 */

describe('bitrateKbps', () => {
  it('leaves a kbit/s number alone', () => {
    expect(bitrateKbps(320)).toBe(320);
  });

  it('converts a bits/s number', () => {
    expect(bitrateKbps(320_000)).toBe(320);
  });

  it('keeps a hi-res lossless rate in kbit/s (FE-08)', () => {
    // 24/192 stereo FLAC runs into the thousands of kbit/s; the old 5,000
    // threshold rendered exactly those files as "9 kbps".
    expect(bitrateKbps(9200)).toBe(9200);
  });

  it('has no opinion about nothing', () => {
    expect(bitrateKbps(null)).toBeNull();
    expect(bitrateKbps(0)).toBeNull();
    expect(bitrateKbps(Number.NaN)).toBeNull();
  });

  // A five-figure kbit/s number is not a mislabelled bit/s one when the codec
  // is lossless -- it is what multichannel and DSD actually measure. The
  // magnitude-only threshold divided these by 1,000 and printed "28 kbps".
  it.each([
    ['24/192 5.1 PCM', 27_648, 'flac'],
    ['DSD512 stereo', 45_158, 'dsf'],
    ['32/384 8-channel', 98_304, 'wav'],
  ])('keeps %s in kbit/s', (_label, value, format) => {
    expect(bitrateKbps(value as number, format as string)).toBe(value);
  });

  it('still converts a lossless bits/s number', () => {
    // A 16/44.1 stereo FLAC is ~900 kbit/s; in bit/s it clears the lossless
    // ceiling by a wide margin, so raising that ceiling costs nothing here.
    expect(bitrateKbps(900_000, 'flac')).toBe(900);
  });

  it('holds a lossy format to the stereo-shaped threshold', () => {
    // Nothing lossy reaches 25,000 kbit/s, so a number that big is bit/s
    // whatever the container claims.
    expect(bitrateKbps(320_000, 'mp3')).toBe(320);
    expect(bitrateKbps(320, 'mp3')).toBe(320);
  });
});

describe('isVariableBitrate', () => {
  it.each(['opus', 'OPUS', 'ogg', 'vorbis', 'aac', 'm4a', 'wma'])(
    'knows %s carries an average',
    (format) => {
      expect(isVariableBitrate(format)).toBe(true);
    },
  );

  it.each(['flac', 'FLAC', 'wav', 'aiff', 'alac'])('does not mark lossless %s', (format) => {
    // A lossless file's bitrate varies too, but it is not a quality setting —
    // marking it would put a `~` on every FLAC in the library for no reason.
    expect(isVariableBitrate(format)).toBe(false);
  });

  it('leaves MP3 alone', () => {
    // Most MP3s are CBR and the tag does not reliably say. Upstream has a
    // per-file bitrate_vbr flag to override this; the catalogue stores no such
    // column, and guessing would mark the majority of a library wrongly.
    expect(isVariableBitrate('mp3')).toBe(false);
  });

  it('says no rather than guessing when the format is missing', () => {
    expect(isVariableBitrate(null)).toBe(false);
    expect(isVariableBitrate('')).toBe(false);
    expect(isVariableBitrate(undefined)).toBe(false);
  });
});

describe('formatBitrate', () => {
  it('prints a constant rate plainly', () => {
    expect(formatBitrate(320, 'mp3')).toEqual({ label: '320 kbps', title: undefined });
  });

  it('marks an average so it is not read as a constant', () => {
    expect(formatBitrate(128, 'opus')).toEqual({
      label: '~128 kbps',
      title: 'Average bitrate (VBR)',
    });
  });

  it('converts the unit before deciding how to print it', () => {
    expect(formatBitrate(128_000, 'opus').label).toBe('~128 kbps');
  });

  it('has nothing to say without a number', () => {
    expect(formatBitrate(null, 'opus')).toEqual({ label: null, title: undefined });
    expect(formatBitrate(0, 'flac')).toEqual({ label: null, title: undefined });
  });
});
