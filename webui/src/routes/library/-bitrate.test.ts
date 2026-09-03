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
