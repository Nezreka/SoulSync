import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { compactRows } from './-discover.mixes';
import { useMixModal } from './-discover.use-mix-modal';

const tracks = [
  {
    name: 'BAILE INoLVIDABLE',
    artists: [{ name: 'Bad Bunny' }],
    album: { name: 'DeBÍ TiRAR MáS FOToS', images: [{ url: '/cover.jpg' }] },
    duration_ms: 367725,
  },
  {
    name: 'nursery',
    artists: ['bbno$'],
    album: { name: 'recess', images: [] },
    duration_ms: 146286,
  },
];

describe('Daily Mix selection metadata', () => {
  it('shows correct durations and preserves selected artists and albums through the real controller', () => {
    const { result } = renderHook(() =>
      useMixModal({
        daily_mix_3: { key: 'daily_mix_3', title: 'Daily Mix 3', subtitle: 'Made for you', tracks },
      }),
    );
    act(() => result.current.open('daily_mix_3'));
    expect(compactRows(result.current.tracks || []).map((row) => row.duration)).toEqual([
      '6:07',
      '2:26',
    ]);
    act(() => result.current.selectAll([0, 1]));
    const selection = result.current.downloadSelection();
    expect(selection.kind).toBe('ok');
    if (selection.kind !== 'ok') throw new Error('Expected a selected mix');
    expect(selection.tracks.map((track) => track.artists)).toEqual([['Bad Bunny'], ['bbno$']]);
    expect(selection.tracks.map((track) => track.duration_ms)).toEqual([367725, 146286]);
    expect(selection.tracks[0].album).toEqual(tracks[0].album);
    expect(compactRows(selection.tracks).map((row) => row.duration)).toEqual(['6:07', '2:26']);
  });
});
