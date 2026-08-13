import { describe, expect, it } from 'vitest';

import {
  applyCompletionEvent,
  completionFromEvent,
  completionStreamPayload,
  emptyStreamCounts,
  isEventOwned,
  parseSseFrames,
  tallyEvent,
} from './-artist-detail.completion';

describe('isEventOwned', () => {
  it('treats anything but missing/error as owned, partials included', () => {
    expect(isEventOwned({ status: 'partial' })).toBe(true);
    expect(isEventOwned({ status: 'completed' })).toBe(true);
    expect(isEventOwned({ status: 'missing' })).toBe(false);
    expect(isEventOwned({ status: 'error' })).toBe(false);
  });
});

describe('completionFromEvent', () => {
  it('returns the NUMBER 0 when not owned, not an object', () => {
    // Downstream branches on typeof; an empty object would take the wrong path.
    expect(completionFromEvent({ status: 'missing', owned_tracks: 0 })).toBe(0);
    expect(completionFromEvent({ status: 'error' })).toBe(0);
  });

  it('clamps a library with MORE tracks than expected to 100%', () => {
    // Bonus tracks would otherwise render "13/12" and 108%.
    expect(completionFromEvent({ status: 'ok', owned_tracks: 13, expected_tracks: 12 })).toEqual({
      owned_tracks: 13,
      total_tracks: 13,
      percentage: 100,
      missing_tracks: -1,
    });
  });

  it('reports a genuine partial with the server percentage', () => {
    expect(
      completionFromEvent({
        status: 'partial',
        owned_tracks: 9,
        expected_tracks: 12,
        completion_percentage: 75,
      }),
    ).toEqual({ owned_tracks: 9, total_tracks: 12, percentage: 75, missing_tracks: 3 });
  });

  it('is complete at exactly the expected count', () => {
    const c = completionFromEvent({ status: 'ok', owned_tracks: 12, expected_tracks: 12 });
    expect(c).toMatchObject({ total_tracks: 12, percentage: 100, missing_tracks: 0 });
  });

  it('treats 0 owned as NOT complete even when 0 were expected', () => {
    // `owned > 0` guards this: 0 >= 0 would otherwise read as complete.
    expect(completionFromEvent({ status: 'ok', owned_tracks: 0, expected_tracks: 0 })).toEqual({
      owned_tracks: 0,
      total_tracks: 0,
      percentage: 100,
      missing_tracks: 0,
    });
  });

  it('falls back to owned/owned when nothing is expected', () => {
    expect(completionFromEvent({ status: 'ok', owned_tracks: 5 })).toEqual({
      owned_tracks: 5,
      total_tracks: 5,
      percentage: 100,
      missing_tracks: 0,
    });
  });
});

describe('applyCompletionEvent', () => {
  const disc = {
    albums: [
      { id: 1, title: 'A', owned: null },
      { id: 2, title: 'B', owned: null },
    ],
    singles: [{ id: 3, title: 'C', owned: null }],
  };

  it('updates only the matching release', () => {
    const next = applyCompletionEvent(disc, {
      id: 2,
      status: 'ok',
      owned_tracks: 5,
      expected_tracks: 5,
    });
    expect(next.albums?.[0].owned).toBeNull();
    expect(next.albums?.[1].owned).toBe(true);
  });

  it('matches a numeric id against its string form', () => {
    // The vanilla looked the card up with a [data-release-id="..."] selector,
    // so 1 and "1" were the same card.
    const next = applyCompletionEvent(
      { albums: [{ id: '1', owned: null }] },
      { id: 1, status: 'ok' },
    );
    expect(next.albums?.[0].owned).toBe(true);
  });

  it('searches every bucket, not just albums', () => {
    const next = applyCompletionEvent(disc, { id: 3, status: 'missing' });
    expect(next.singles?.[0].owned).toBe(false);
  });

  it('returns the SAME object when nothing matched, so React does not re-render', () => {
    expect(applyCompletionEvent(disc, { id: 999, status: 'ok' })).toBe(disc);
    expect(applyCompletionEvent(disc, {})).toBe(disc);
  });

  it('does not mutate the input', () => {
    applyCompletionEvent(disc, { id: 1, status: 'ok', owned_tracks: 1, expected_tracks: 1 });
    expect(disc.albums[0].owned).toBeNull();
  });
});

describe('tallyEvent', () => {
  it('counts every event in total but only owned ones in owned', () => {
    const counts = emptyStreamCounts();
    tallyEvent(counts, { category: 'albums', status: 'ok' });
    tallyEvent(counts, { category: 'albums', status: 'missing' });
    expect(counts.total.albums).toBe(2);
    expect(counts.owned.albums).toBe(1);
    // "missing" is derived, never counted separately.
    expect(counts.total.albums - counts.owned.albums).toBe(1);
  });

  it('accumulates formats from owned releases only', () => {
    const counts = emptyStreamCounts();
    tallyEvent(counts, { category: 'albums', status: 'ok', formats: ['FLAC', 'MP3'] });
    tallyEvent(counts, { category: 'albums', status: 'missing', formats: ['WAV'] });
    expect([...counts.formats].sort()).toEqual(['FLAC', 'MP3']);
  });

  it('ignores an event with no or unknown category', () => {
    const counts = emptyStreamCounts();
    tallyEvent(counts, { status: 'ok' });
    tallyEvent(counts, { category: 'bogus' as never, status: 'ok' });
    expect(counts.total).toEqual({ albums: 0, eps: 0, singles: 0 });
  });
});

describe('parseSseFrames', () => {
  it('carries an incomplete trailing line forward', () => {
    // A frame split across two chunks is otherwise dropped silently.
    const first = parseSseFrames('data: {"id":1}\ndata: {"id":2');
    expect(first.events).toEqual([{ id: 1 }]);
    expect(first.rest).toBe('data: {"id":2');

    const second = parseSseFrames(first.rest + '}\n');
    expect(second.events).toEqual([{ id: 2 }]);
  });

  it('ignores lines that are not data frames', () => {
    expect(parseSseFrames(': keep-alive\nevent: ping\ndata: {"id":1}\n').events).toEqual([
      { id: 1 },
    ]);
  });

  it('ignores a non-data line even when its tail happens to parse as JSON', () => {
    // Dropping the prefix check and relying on JSON.parse throwing is not
    // enough: `event: 1`.slice(6) is " 1", which parses to the number 1 and
    // would be emitted as an event.
    expect(parseSseFrames('event: 1\nretry: 500\ndata: {"id":2}\n').events).toEqual([{ id: 2 }]);
  });

  it('skips a malformed frame instead of stalling the stream', () => {
    const { events } = parseSseFrames('data: {bad json\ndata: {"id":2}\n');
    expect(events).toEqual([{ id: 2 }]);
  });
});

describe('completionStreamPayload', () => {
  it('sends every bucket and the source, defaulting empties', () => {
    expect(
      completionStreamPayload('Aphex Twin', { albums: [{ id: 1 }], source: 'spotify' }),
    ).toEqual({
      artist_name: 'Aphex Twin',
      albums: [{ id: 1 }],
      eps: [],
      singles: [],
      source: 'spotify',
    });
    expect(completionStreamPayload('X', {}).source).toBeNull();
  });
});
