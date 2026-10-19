import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewFinding } from '../-review.api';

import { MissingTracksChoice } from './missing-tracks-choice';
afterEach(cleanup);
it('requests only selected, currently actionable missing tracks and permits changing completeness', () => {
  const onRequest = vi.fn(),
    onChangeScope = vi.fn();
  const findings = [
    { id: 1, title: 'First', details: { missing_tracks: [{ name: 'First' }] } },
    { id: 2, title: 'Second', details: { missing_tracks: [{ name: 'Second' }] } },
    { id: 3, title: 'Requested', details: { fulfillment_state: 'wishlisted' } },
  ] as Pick<ReviewFinding, 'id' | 'details' | 'title'>[];
  render(
    <MissingTracksChoice
      findings={findings}
      busy={false}
      onRequest={onRequest}
      onChangeScope={onChangeScope}
    />,
  );
  expect(screen.queryByRole('checkbox', { name: 'Requested' })).toBeNull();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Second' }));
  fireEvent.click(screen.getByRole('button', { name: 'Request selected tracks (1)' }));
  expect(onRequest).toHaveBeenCalledWith([2]);
  fireEvent.click(screen.getByRole('button', { name: 'Change album completeness' }));
  expect(onChangeScope).toHaveBeenCalledOnce();
});

it('describes partial-album wanted tracks accurately', () => {
  render(
    <MissingTracksChoice
      scope="partial"
      findings={[]}
      busy={false}
      onRequest={vi.fn()}
      onChangeScope={vi.fn()}
    />,
  );
  expect(screen.getByText(/Only tracks marked as wanted for this partial album/)).toBeTruthy();
  expect(screen.queryByText(/this complete album/)).toBeNull();
});
