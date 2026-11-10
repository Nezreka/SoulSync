/**
 * Rotating playlists (#2039): the words.
 *
 * This setting ends in deleted files, so what the sheet and the confirm say
 * has to match what the cleaner will do. Dry run and the off states are the
 * ones a wrong sentence would hurt.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  fetchRotationPreview,
  rotationConfirmMessage,
  rotationStatus,
  rotationSummary,
  type RotationPreview,
} from './-sync.rotation';

const base: RotationPreview = {
  rotating: true,
  cleaner_enabled: true,
  maintenance_enabled: true,
  dry_run: true,
  rotation_grace: '1d',
  membership_known: true,
  downloads: 20,
  left: 14,
  removing: 9,
  kept: 5,
  kept_reasons: { played: 3, other_playlist: 2 },
};

describe('rotationSummary', () => {
  it('counts what left, what goes and why the rest stays', () => {
    expect(rotationSummary(base)).toBe(
      '14 downloaded songs have left · 9 will be removed · 5 kept (in another playlist, played)',
    );
  });

  it('says would, not will, before the toggle is on', () => {
    expect(rotationSummary({ ...base, rotating: false })).toContain('9 would be removed');
  });

  it('handles one song', () => {
    expect(rotationSummary({ ...base, left: 1, removing: 1, kept: 0, kept_reasons: {} })).toBe(
      '1 downloaded song has left · 1 will be removed',
    );
  });

  it('says so when nothing was downloaded or nothing left', () => {
    expect(rotationSummary({ ...base, downloads: 0 })).toMatch(/Nothing downloaded/);
    expect(rotationSummary({ ...base, left: 0 })).toMatch(/No downloaded songs have left/);
  });

  it('does not count when the track list cannot be read', () => {
    const line = rotationSummary({ ...base, membership_known: false });
    expect(line).toMatch(/Can't tell/);
    expect(line).not.toMatch(/removed/);
  });

  it('is empty with no preview', () => {
    expect(rotationSummary(null)).toBe('');
  });
});

describe('rotationStatus', () => {
  it('is silent until the playlist is rotating', () => {
    expect(rotationStatus({ ...base, rotating: false })).toBe('');
    expect(rotationStatus(null)).toBe('');
  });

  it('says removals wait for approval on dry run', () => {
    expect(rotationStatus(base)).toMatch(/Dry run is on.*approve.*a day after/);
  });

  it('says files are deleted when dry run is off', () => {
    const line = rotationStatus({ ...base, dry_run: false, rotation_grace: 'none' });
    expect(line).toMatch(/deleted automatically, on the next cleaner run/);
  });

  it('says nothing runs when the cleaner or maintenance is off', () => {
    expect(rotationStatus({ ...base, cleaner_enabled: false })).toMatch(/Cleaner is off/);
    expect(rotationStatus({ ...base, maintenance_enabled: false })).toMatch(/paused/);
  });

  it('falls back to a day for a wait it does not know', () => {
    expect(rotationStatus({ ...base, rotation_grace: 'soon' })).toMatch(/a day after/);
  });
});

describe('rotationConfirmMessage', () => {
  it('names the keep rules every time', () => {
    const text = rotationConfirmMessage('Mix', null);
    expect(text).toContain('"Mix"');
    expect(text).toMatch(/played, favourited, rated or still have in another playlist is kept/);
    // no preview: assume dry run, the default, and never claim auto delete
    expect(text).toMatch(/Dry run is on/);
  });

  it('says the cleaner gets turned on, only when it is off', () => {
    expect(rotationConfirmMessage('Mix', { ...base, cleaner_enabled: false })).toMatch(
      /turns on the Expired Download Cleaner/,
    );
    expect(rotationConfirmMessage('Mix', base)).not.toMatch(/turns on/);
  });

  it('warns when files will be deleted without review', () => {
    expect(rotationConfirmMessage('Mix', { ...base, dry_run: false })).toMatch(
      /Dry run is off, so files are deleted automatically/,
    );
  });

  it('counts what already left', () => {
    expect(rotationConfirmMessage('Mix', base)).toMatch(/14 downloaded songs have already left, 9/);
    expect(rotationConfirmMessage('Mix', { ...base, left: 0 })).not.toMatch(/already left/);
  });
});

describe('fetchRotationPreview', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('returns the preview', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => base });
    vi.stubGlobal('fetch', fetchMock);
    expect(await fetchRotationPreview(7)).toEqual(base);
    expect(fetchMock).toHaveBeenCalledWith('/api/mirrored-playlists/7/rotation');
  });

  it('is null on any failure, so the sheet just shows no numbers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
    expect(await fetchRotationPreview(7)).toBeNull();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    expect(await fetchRotationPreview(7)).toBeNull();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ error: 'nope' }) }),
    );
    expect(await fetchRotationPreview(7)).toBeNull();
  });
});
