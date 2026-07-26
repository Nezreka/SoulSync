import { describe, expect, it } from 'vitest';

import { describeRejection } from './acquisition-rejection';

describe('acquisition review rejection lines', () => {
  it('names the missing track instead of only its code', () => {
    expect(
      describeRejection({
        code: 'missing_expected_track',
        expected_key: '2:5',
        disc_number: 2,
        track_number: 5,
        expected_title: 'Blue Monday',
      }),
    ).toEqual({ label: 'missing expected track', detail: 'Disc 2 · Track 5 · Blue Monday' });
  });

  it('omits the disc prefix for a single-disc bundle', () => {
    expect(
      describeRejection({
        code: 'missing_expected_track',
        disc_number: 1,
        track_number: 3,
        expected_title: 'Temptation',
      }).detail,
    ).toBe('Track 3 · Temptation');
  });

  it('falls back to the expected key when the title is missing', () => {
    expect(describeRejection({ code: 'missing_expected_track', expected_key: '1:9' }).detail).toBe(
      '1:9',
    );
  });

  it('points an unmatched file at its path', () => {
    expect(
      describeRejection({
        code: 'unmatched_file',
        relative_path: 'CD1/07 - Bonus.flac',
        title: 'Bonus',
      }),
    ).toEqual({ label: 'unmatched file', detail: 'CD1/07 - Bonus.flac' });
  });

  it('explains an ambiguous position with its reason', () => {
    expect(
      describeRejection({
        code: 'ambiguous_position',
        relative_path: '05 - Track.flac',
        track_number: 5,
        reason: 'multi_disc_bundle_without_disc_number',
      }).detail,
    ).toBe('05 - Track.flac · multi disc bundle without disc number');
  });

  it('shows the similarity that was too close to call', () => {
    expect(
      describeRejection({
        code: 'ambiguous_title',
        relative_path: '02 - Ceremony.flac',
        similarity: 0.914,
      }).detail,
    ).toBe('02 - Ceremony.flac · 91% similar');
  });

  it('shows how confident the low-confidence match was', () => {
    expect(
      describeRejection({
        code: 'low_confidence',
        relative_path: '03 - Denial.flac',
        expected_key: '1:3',
        confidence: 0.62,
      }).detail,
    ).toBe('03 - Denial.flac · 62% confidence');
  });

  it('leaves a tracklist-wide rejection without a per-file detail', () => {
    expect(describeRejection({ code: 'no_expected_tracklist' })).toEqual({
      label: 'no expected tracklist',
      detail: '',
    });
  });

  it('never renders a raw object for an unknown or malformed rejection', () => {
    expect(describeRejection({} as never)).toEqual({ label: 'Unresolved match', detail: '' });
    expect(
      describeRejection({ code: 'something_new', relative_path: 'x/y.flac' } as never).detail,
    ).toBe('x/y.flac');
    // A server that ever answers with a nested value must not reach the DOM as
    // "[object Object]" — the old String() call did exactly that.
    expect(
      describeRejection({
        code: { nested: true },
        relative_path: ['a'],
      } as never),
    ).toEqual({ label: 'Unresolved match', detail: '' });
  });
});
