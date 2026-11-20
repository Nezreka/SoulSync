import { describe, expect, it } from 'vitest';

import type { AdlDownload } from './-adl.types';

import { isUpgradeRow, upgradeLine } from './-adl.helpers';

const row = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({ task_id: 't', status: 'downloading', quality: '', ...over }) as AdlDownload;

describe('quality upgrade rows', () => {
  it('reads "MP3 320 → FLAC" once the new file is in', () => {
    expect(
      upgradeLine(
        row({ status: 'completed', quality: 'FLAC 16/44', upgrade_of: { label: 'MP3 320' } }),
      ),
    ).toBe('MP3 320 → FLAC');
  });

  it('says what it is replacing while it is still on its way', () => {
    expect(upgradeLine(row({ upgrade_of: { label: 'AAC 256' } }))).toBe('Replacing AAC 256');
    expect(upgradeLine(row({ upgrade_of: { format: 'mp3' } }))).toBe('Replacing MP3');
  });

  it('is nothing for an ordinary download', () => {
    expect(isUpgradeRow(row())).toBe(false);
    expect(isUpgradeRow(row({ upgrade_of: null }))).toBe(false);
    expect(upgradeLine(row())).toBeNull();
  });
});
