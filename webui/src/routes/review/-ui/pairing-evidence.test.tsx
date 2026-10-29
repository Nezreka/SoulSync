import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';

import { PairingEvidence } from './pairing-evidence';

afterEach(cleanup);

it('shows matcher checks with their actual decision explanation', () => {
  render(
    <PairingEvidence
      checks={[
        {
          check: 'Artist credit',
          local: 'Noah Kahan',
          release: 'Noah Kahan & Hozier',
          result: 'conflicts',
        },
        {
          check: 'Album assignment',
          local: '3 files',
          release: '3 slots',
          result: 'resolved',
          detail: 'Other files fit their own slots',
        },
        { check: 'Decision', result: 'conflicts', detail: 'Listen for a track boundary error' },
      ]}
    />,
  );
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.getAllByText('Differs')).toHaveLength(1);
  fireEvent.click(screen.getByText('Artist credit'));
  expect(screen.getByText('Noah Kahan & Hozier')).toBeVisible();
  fireEvent.click(screen.getByText('Album assignment'));
  fireEvent.click(screen.getByText('Assessment'));
  expect(screen.getByText('Other files fit their own slots')).toBeVisible();
  expect(screen.getByText('Listen for a track boundary error')).toBeVisible();
});
