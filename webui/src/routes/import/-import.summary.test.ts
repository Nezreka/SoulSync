import { describe, expect, it } from 'vitest';

import type { ImportTrackDisposition } from './-import.types';

import { summarizeQueueEntry } from './-ui/import-shared';

function disposition(
  track_name: string,
  decision: ImportTrackDisposition['decision'],
): ImportTrackDisposition {
  return { track_name, file_name: `${track_name}.mp3`, decision, reason: 'test' };
}

describe('summarizeQueueEntry', () => {
  it('renders the detailed breakdown when dispositions exist', () => {
    const dispositions = [
      disposition('A', 'imported'),
      disposition('B', 'imported'),
      disposition('C', 'skipped_owned'),
      disposition('D', 'upgraded'),
    ];
    expect(summarizeQueueEntry(4, dispositions, 3)).toBe(
      '2 imported · 1 upgraded · 1 already owned',
    );
  });

  it('falls back to the old counts when no dispositions were recorded', () => {
    expect(summarizeQueueEntry(5, [], 3)).toBe('3 of 5 imported');
  });

  it('counts failures', () => {
    const dispositions = [disposition('A', 'imported'), disposition('B', 'error')];
    expect(summarizeQueueEntry(2, dispositions, 1)).toBe('1 imported · 1 failed');
  });

  it('handles an all-skipped batch', () => {
    const dispositions = [disposition('A', 'skipped_owned'), disposition('B', 'skipped_owned')];
    expect(summarizeQueueEntry(2, dispositions, 0)).toBe('2 already owned');
  });
});
