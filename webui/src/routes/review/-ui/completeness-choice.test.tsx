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

it('collapses large collections while retaining selections during a search', () => {
  const onChoose = vi.fn();
  render(
    <CompletenessChoice
      finding={
        {
          id: 1,
          details: {
            missing_tracks: Array.from({ length: 14 }, (_, index) => ({
              source_track_id: `track-${index}`,
              title: `Song ${index}`,
              track_number: index + 1,
              disc_number: index < 7 ? 1 : 2,
            })),
          },
        } as unknown as ReviewFinding
      }
      busy={false}
      onChoose={onChoose}
    />,
  );
  const summary = screen.getByText('14 missing tracks · 0 selected');
  expect(summary.closest('details')).not.toHaveAttribute('open');
  fireEvent.click(summary);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Song 0', hidden: true }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Find missing tracks' }), {
    target: { value: 'Song 13' },
  });
  expect(screen.queryByText('Song 0')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Keep as partial · 1 wanted' }));
  expect(onChoose).toHaveBeenCalledWith('partial:["id:track-0:1:1"]');
});
