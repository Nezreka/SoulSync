/** the Quality pills read back in words (settings-bic phase 3) */

import { describe, expect, it } from 'vitest';

import { EMPTY_LADDER, type Ladder } from '../quality';
import {
  audiobookSentence,
  musicSentence,
  targetWords,
  videoSentence,
  youtubeSentence,
} from './words';

const ladder = (over: Partial<Ladder>): Ladder => ({ ...EMPTY_LADDER, ...over });
const FLAC24 = { format: 'flac', bit_depth: 24 };
const FLAC = { format: 'flac' };
const MP3 = { format: 'mp3', min_bitrate: 320 };

describe('music', () => {
  it('says the targets in turn and when upgrades stop', () => {
    expect(
      musicSentence(
        'Balanced',
        ladder({
          targets: [FLAC24, FLAC, MP3],
          upgradePolicy: 'until_cutoff',
          cutoff: 1,
          fallback: false,
        }),
      ),
    ).toBe(
      'Balanced takes FLAC 24-bit, then FLAC, then MP3 at 320 or better. It keeps upgrading until it has FLAC.',
    );
    expect(
      musicSentence('Strict', ladder({ targets: [FLAC], upgradePolicy: 'none', fallback: false })),
    ).toBe('Strict takes FLAC only. It never replaces a file you have.');
    expect(
      musicSentence(
        'Any',
        ladder({
          targets: [FLAC24, FLAC, MP3, { format: 'aac' }, { format: 'ogg' }],
          upgradePolicy: 'acceptable',
          fallback: true,
        }),
      ),
    ).toBe(
      'Any takes FLAC 24-bit, then FLAC, then MP3 at 320 or better and 2 more. When none of them turns up it takes what there is. It stops upgrading once a file matches any of them.',
    );
  });

  it('an empty list says what that means', () => {
    expect(
      musicSentence(
        'Empty',
        ladder({ targets: [], fallback: true, upgradePolicy: 'until_cutoff' }),
      ),
    ).toBe('Empty has no targets yet, so it takes whatever turns up.');
    expect(
      musicSentence(
        'Empty',
        ladder({ targets: [], fallback: false, upgradePolicy: 'until_cutoff' }),
      ),
    ).toBe('Empty has no targets yet, so it takes nothing.');
    expect(targetWords({ format: 'opus', min_bitrate: 160 })).toBe('OPUS at 160 or better');
  });
});

describe('video and YouTube', () => {
  it('the switched-on qualities and the cutoff', () => {
    expect(
      videoSentence({ name: 'Default', tiers: ['Remux · 4K', 'WEB-DL · 1080p'], cutoff: '2160p' }),
    ).toBe('Default takes Remux · 4K, then WEB-DL · 1080p. It stops upgrading at 4K.');
    expect(videoSentence({ name: 'Anime', tiers: ['WEB-DL · 1080p'], cutoff: '' })).toBe(
      'Anime takes WEB-DL · 1080p. It never stops upgrading.',
    );
    expect(videoSentence({ name: 'Off', tiers: [], cutoff: null })).toBe(
      'Off has every quality switched off, so nothing is grabbed.',
    );
  });

  it('YouTube', () => {
    expect(youtubeSentence({ max_resolution: 'best', container: 'mkv', video_codec: 'any' })).toBe(
      'Followed channels download the best there is, as MKV.',
    );
    expect(youtubeSentence({})).toBe('Followed channels download up to 1080p, as MP4.');
  });
});

describe('audiobooks', () => {
  it('format, the allowed ones and the bitrate floor and ceiling', () => {
    expect(
      audiobookSentence({ first: 'mp3', allowed: ['mp3'], of: 6, minKbps: 32, maxKbps: 128 }),
    ).toBe('Audiobooks prefer MP3, and only MP3 is allowed. Releases between 32 and 128 kbps.');
    expect(audiobookSentence({ first: 'm4b', allowed: [], of: 6, minKbps: 0, maxKbps: 0 })).toBe(
      'Audiobooks prefer M4B, and every format is allowed.',
    );
  });
});
