import { describe, expect, it } from 'vitest';

import type { AdlDownload } from './-adl.types';
import type { VdlDownload } from './-vdl.types';

import {
  dayLabel,
  formatTag,
  isHighQuality,
  musicJourney,
  parseStamp,
  transferEta,
  vdlBackups,
  videoJourney,
} from './-dl.format';

const row = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({
    task_id: 't1',
    title: 'Xtal',
    artist: 'Aphex Twin',
    album: 'SAW',
    artwork: '',
    status: 'downloading',
    progress: 0,
    error: null,
    verification_status: null,
    batch_id: '',
    batch_name: '',
    batch_source: '',
    playlist_id: 'p1',
    track_index: 0,
    batch_total: 1,
    timestamp: 0,
    priority: 0,
    quality: '',
    is_persistent_history: false,
    ...over,
  }) as AdlDownload;

const states = (steps: { state: string }[]) => steps.map((s) => s.state);

describe('parseStamp', () => {
  it('reads a bare sqlite stamp as utc, not local time', () => {
    expect(parseStamp('2026-10-21 03:01:04')).toBe(Date.UTC(2026, 9, 21, 3, 1, 4));
  });

  it('reads epoch seconds and keeps ms as they are', () => {
    expect(parseStamp(1_700_000_000)).toBe(1_700_000_000_000);
    expect(parseStamp(1_700_000_000_000)).toBe(1_700_000_000_000);
  });

  it('gives null for nothing, zero and junk', () => {
    expect(parseStamp(null)).toBeNull();
    expect(parseStamp('')).toBeNull();
    expect(parseStamp(0)).toBeNull();
    expect(parseStamp('not a date')).toBeNull();
  });
});

describe('dayLabel', () => {
  it('says Today and Yesterday', () => {
    const now = Date.now();
    expect(dayLabel(now - 1000, now)).toBe('Today');
    expect(dayLabel(now - 86_400_000, now)).toBe('Yesterday');
    expect(dayLabel(null, now)).toBe('Earlier');
  });
});

describe('formatTag', () => {
  it('turns podcast mime types into formats and leaves real tags alone', () => {
    expect(formatTag('audio/mpeg')).toBe('MP3');
    expect(formatTag('audio/mp4')).toBe('M4A');
    expect(formatTag('FLAC 16/44.1')).toBe('FLAC 16/44.1');
    expect(formatTag('')).toBe('');
  });

  it('only lights up lossless and 4k', () => {
    expect(isHighQuality('FLAC 24/96')).toBe(true);
    expect(isHighQuality('2160p WEB-DL')).toBe(true);
    expect(isHighQuality('MP3 320')).toBe(false);
  });
});

describe('transferEta', () => {
  it('works it out from bytes left and speed', () => {
    expect(transferEta(row({ live_detail: { size: 1000, bytes: 400, speed: 10 } }))).toBe('1m');
    expect(transferEta(row({ live_detail: { size: 1000, bytes: 1000, speed: 10 } }))).toBe('');
    expect(transferEta(row({ live_detail: { size: 1000 } }))).toBe('');
  });
});

describe('musicJourney', () => {
  const now = Date.now();

  it('marks where a download is right now', () => {
    const steps = musicJourney(
      row({
        status: 'downloading',
        download_source: 'Soulseek',
        live_detail: { candidate_count: 6, candidate_index: 0 },
      }),
      null,
      now,
    );
    expect(states(steps)).toEqual(['done', 'now', 'todo', 'todo']);
    expect(steps[0].title).toBe('Found on Soulseek');
    expect(steps[0].detail).toContain('candidate 1 of 6');
  });

  it('reads picked confidence as a percent whichever scale it comes in', () => {
    const frac = musicJourney(row({ live_detail: { picked: { confidence: 0.96 } } }), null, now);
    expect(frac[0].detail).toBe('96% match');
    const pct = musicJourney(row({ live_detail: { picked: { confidence: 96 } } }), null, now);
    expect(pct[0].detail).toBe('96% match');
  });

  it('stops a not-found row at the search, with why', () => {
    const steps = musicJourney(
      row({ status: 'not_found', error: 'Not found on 4 sources' }),
      {
        decision: { rejected_counts: { duration_mismatch: 5, match_weak: 4 } },
      } as never,
      now,
    );
    expect(states(steps)).toEqual(['fail']);
    expect(steps[0].detail).toBe(
      'Not found on 4 sources. Rejected: 5 wrong length · 4 weak match.',
    );
  });

  it('tells a quarantine apart from a failed transfer', () => {
    const q = musicJourney(
      row({ status: 'failed', error: 'Integrity check failed: too short' }),
      null,
      now,
    );
    expect(q[q.length - 1]).toMatchObject({ key: 'verify', state: 'fail', title: 'Quarantined' });
    const f = musicJourney(row({ status: 'failed', error: 'peer went offline' }), null, now);
    expect(f[f.length - 1]).toMatchObject({ key: 'download', state: 'fail' });
  });

  it('flags an unverified import as a warning, not a failure', () => {
    const steps = musicJourney(
      row({ status: 'completed', verification_status: 'unverified', download_source: 'Deezer' }),
      null,
      now,
    );
    expect(states(steps)).toEqual(['done', 'warn', 'done']);
  });

  it('leaves out the verify step when acoustid never looked', () => {
    const steps = musicJourney(row({ status: 'completed' }), null, now);
    expect(steps.map((s) => s.key)).toEqual(['download', 'import']);
  });

  it('calls a podcast saved, not imported', () => {
    const steps = musicJourney(row({ status: 'completed', event_type: 'podcast' }), null, now);
    expect(steps[steps.length - 1].title).toBe('Saved');
  });

  it('says an owned track was never downloaded', () => {
    expect(musicJourney(row({ status: 'already_owned' }), null, now)[0].title).toBe(
      'Already in your library',
    );
  });
});

describe('videoJourney', () => {
  const now = Date.UTC(2026, 9, 20, 12, 0, 0);
  const v = (over: Partial<VdlDownload> = {}): VdlDownload => ({
    id: 1,
    kind: 'movie',
    title: 'Dune',
    status: 'downloading',
    progress: 50,
    size_bytes: 2 * 1024 ** 3,
    created_at: '2026-10-20 11:00:00',
    ...over,
  });

  it('counts the backup releases lined up', () => {
    expect(vdlBackups(v({ candidates: '[{"a":1},{"b":2}]' }))).toBe(2);
    expect(vdlBackups(v({ candidates: 'garbage' }))).toBe(0);
    const steps = videoJourney(v({ candidates: '[{}]', release_title: 'Dune.2160p' }), now);
    expect(steps[0].detail).toBe('Dune.2160p · 1 backup release lined up');
  });

  it('says a stuck download is stuck', () => {
    const steps = videoJourney(v({ progress_at: '2026-10-20 11:30:00' }), now);
    expect(steps[1].title).toBe('Downloading, but stuck');
    const fresh = videoJourney(v({ progress_at: '2026-10-20 11:58:00' }), now);
    expect(fresh[1].title).toBe('Downloading');
  });

  it('puts an import failure on the import step and a failed grab on the download', () => {
    expect(states(videoJourney(v({ status: 'import_failed', error: 'x' }), now))).toEqual([
      'done',
      'done',
      'fail',
    ]);
    expect(states(videoJourney(v({ status: 'failed', error: 'x' }), now))).toEqual([
      'done',
      'fail',
    ]);
  });

  it('warns on a finished copy still on upgrade watch', () => {
    const steps = videoJourney(v({ status: 'completed', upgrade_watch: true }), now);
    expect(steps[2]).toMatchObject({ state: 'warn', title: 'In your library' });
  });
});
