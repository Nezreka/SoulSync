/** #2179: the words and counts of a bulk Keep Best over duplicates. */
import { describe, expect, it } from 'vitest';

import type { RepairFinding } from './-tools.types';

import { bulkFixLoopMessage, bulkFixRunMessage } from './-tools.core';
import {
  needsYouClause,
  needsYouLine,
  recordingPairs,
  tallyFindings,
  tallySummary,
} from './-tools.keep-best-bulk';

const row = (id: number, reasons: string[], withMove: string[] = reasons): RepairFinding =>
  ({
    id,
    job_id: 'native_duplicate_detector',
    finding_type: 'native_duplicate_tracks',
    severity: 'info',
    status: 'pending',
    title: `dupe ${id}`,
    needs_you: reasons.length ? { reasons, with_move: withMove } : null,
    details: {
      tracks: [
        { title: 'See You Space Cowboy', artist: 'The Seatbelts', format: 'ogg', album: 'LP-Box' },
        { title: 'SEE YOU SPACE COWBOYS', artist: 'The Seatbelts', format: 'flac' },
      ],
    },
  }) as RepairFinding;

const ROWS = [
  row(1, []),
  row(2, ['same_recording']),
  row(3, ['playlist'], []),
  row(4, ['same_recording', 'playlist'], ['same_recording']),
  row(5, ['protected']),
];

describe('tallyFindings', () => {
  it('counts what goes through and who needs you, by why', () => {
    expect(tallyFindings(ROWS, { move: false, confirm: false })).toEqual({
      total: 5,
      ready: 1,
      held: 4,
      needs: { same_recording: 2, playlist: 2, protected: 1 },
    });
  });

  it('moving playlist entries lets the playlist ones through', () => {
    expect(tallyFindings(ROWS, { move: true, confirm: false })).toEqual({
      total: 5,
      ready: 2,
      held: 3,
      needs: { same_recording: 2, protected: 1 },
    });
  });

  it('confirming only settles the naming, never a protected copy', () => {
    expect(tallyFindings(ROWS, { move: true, confirm: true })).toEqual({
      total: 5,
      ready: 4,
      held: 1,
      needs: { protected: 1 },
    });
  });
});

it('tallySummary reads the server summary for moving off and on', () => {
  const summary = {
    total: 10,
    ready: 6,
    ready_with_move: 8,
    needs_you: { playlist: 2, same_recording: 2 },
    needs_you_with_move: { same_recording: 2 },
  };
  expect(tallySummary(summary, false)).toEqual({
    total: 10,
    ready: 6,
    held: 4,
    needs: { playlist: 2, same_recording: 2 },
  });
  expect(tallySummary(summary, true).held).toBe(2);
});

it('a row says why it needs you, in plain words', () => {
  expect(needsYouLine(row(4, ['playlist', 'same_recording']))).toBe(
    'Needs you: confirm it is the same recording, and a copy is in a playlist the kept copy is not in',
  );
  expect(needsYouLine(row(1, []))).toBeNull();
});

it('the run clause names each reason with its count', () => {
  expect(needsYouClause({ playlist: 1 }, 1)).toBe(
    ', 1 needs you and stays open (1 with a copy in a playlist the kept copy is not in)',
  );
  expect(needsYouClause({}, 0)).toBe('');
});

it('lists the differently named pairs, and only those', () => {
  const pairs = recordingPairs(ROWS);
  expect(pairs.map((pair) => pair.id)).toEqual([2, 4]);
  expect(pairs[0].copies[0]).toEqual({
    title: 'See You Space Cowboy',
    artist: 'The Seatbelts',
    detail: 'OGG · LP-Box',
  });
});

describe('the run messages', () => {
  it('a Keep Best all says what it kept and who it left for you', () => {
    const { message, type } = bulkFixRunMessage({
      finding_type: 'native_duplicate_tracks',
      fixed: 3,
      skipped: 2,
      total: 5,
      needs_you: { same_recording: 1, playlist: 1 },
    });
    expect(message).toBe(
      'Kept best on 3, 2 need you and stay open (1 named differently, so you confirm it is the same recording; 1 with a copy in a playlist the kept copy is not in) of 5',
    );
    expect(type).toBe('success');
  });

  it('other runs read as before', () => {
    expect(bulkFixRunMessage({ fixed: 2, skipped: 1, total: 3 }).message).toBe(
      'Fixed 2, 1 skipped of 3',
    );
  });

  it('the selection loop says the same', () => {
    expect(
      bulkFixLoopMessage(1, 0, '', 1, 0, 0, { needs: { protected: 1 }, only: true }).message,
    ).toBe('Kept best on 1, 1 needs you and stays open (1 where every other copy is protected)');
  });
});
