import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewFinding } from '../-review.api';

import { CompletenessChoice } from './completeness-choice';

afterEach(cleanup);
const finding = {
  id: 1,
  details: {
    source: 'musicbrainz',
    release_id: 'release',
    missing_tracks: [
      { title: 'First', wanted_key: 'first' },
      { title: 'Second', wanted_key: 'second' },
    ],
  },
} as unknown as ReviewFinding;

it('does not submit wanted tracks from an obsolete release or missing list', () => {
  const onChoose = vi.fn();
  const { rerender } = render(
    <CompletenessChoice finding={finding} busy={false} onChoose={onChoose} />,
  );
  fireEvent.click(screen.getByRole('checkbox', { name: 'First' }));
  rerender(
    <CompletenessChoice
      finding={{
        ...finding,
        details: {
          ...finding.details,
          missing_tracks: [{ title: 'Second', wanted_key: 'second' }],
        },
      }}
      busy={false}
      onChoose={onChoose}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Keep as partial' }));
  expect(onChoose).toHaveBeenLastCalledWith('partial:[]');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Second' }));
  rerender(
    <CompletenessChoice
      finding={{ ...finding, details: { ...finding.details, release_id: 'another' } }}
      busy={false}
      onChoose={onChoose}
    />,
  );
  expect(screen.getByRole('checkbox', { name: 'Second' })).not.toBeChecked();
});

it('freezes wanted-track selection while saving the album choice', () => {
  render(<CompletenessChoice finding={finding} busy onChoose={vi.fn()} />);
  expect(screen.getByRole('checkbox', { name: 'First' })).toHaveAttribute('aria-disabled', 'true');
  fireEvent.click(screen.getByRole('checkbox', { name: 'First' }));
  expect(screen.getByRole('checkbox', { name: 'First' })).not.toBeChecked();
  expect(screen.getByRole('button', { name: 'Keep as partial' })).toBeDisabled();
});
