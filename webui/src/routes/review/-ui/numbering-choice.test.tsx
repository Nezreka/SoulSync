import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { AlbumAssignment } from '../-review.api';

import { NumberingChoice } from './numbering-choice';

afterEach(cleanup);

it('opens numbering choices before changing the plan', () => {
  const onChoose = vi.fn();
  render(
    <NumberingChoice
      scheme="Numbered by release disc"
      busy={false}
      onChoose={onChoose}
      assignments={[
        {
          file_path: '/song.flac',
          current_title: 'Song',
          current_disc_number: 1,
          current_track_number: 12,
          slot_disc_number: 2,
          slot_track_number: 1,
        } as AlbumAssignment,
      ]}
    />,
  );
  expect(screen.queryByRole('button', { name: 'Keep current numbering' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Change…' }));
  expect(onChoose).not.toHaveBeenCalled();
  expect(screen.getByRole('dialog')).toHaveTextContent('2.1');
  fireEvent.click(screen.getByRole('button', { name: 'Keep current numbering' }));
  expect(onChoose).toHaveBeenCalledWith('dismiss');
});
