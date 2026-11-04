import { describe, expect, it } from 'vitest';

import { formatWantedFileQuality } from './library-v2-page';

describe('formatWantedFileQuality (§64 I2 cutoff-unmet quality summary)', () => {
  it('returns null for a missing file', () => {
    expect(formatWantedFileQuality(undefined)).toBeNull();
  });

  it('formats format + bit depth/sample rate resolution', () => {
    expect(
      formatWantedFileQuality({
        format: 'flac',
        bitrate: null,
        sample_rate: 44100,
        bit_depth: 16,
        quality_tier: 'lossless',
      }),
    ).toBe('FLAC · 16bit/44.1kHz');
  });

  it('falls back to bitrate when no bit depth/sample rate is known', () => {
    expect(
      formatWantedFileQuality({
        format: 'mp3',
        bitrate: 128,
        sample_rate: null,
        bit_depth: null,
        quality_tier: 'lossy_low',
      }),
    ).toBe('MP3 · 128kbps');
  });

  it('shows only format when no resolution data is present at all', () => {
    expect(
      formatWantedFileQuality({
        format: 'aac',
        bitrate: null,
        sample_rate: null,
        bit_depth: null,
        quality_tier: 'unknown',
      }),
    ).toBe('AAC');
  });
});
