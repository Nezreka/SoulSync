import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { ReleaseComparison } from './reconciliation-evidence';
it('renders a missing repeated title at its release position without a duplicate footer list', () => {
  render(
    <ReleaseComparison
      selected={0}
      onSelect={() => {}}
      view="detail"
      candidates={[
        {
          source: 'musicbrainz',
          release_id: 'release',
          release_title: 'Album',
          missing: 1,
          pairs: [
            {
              slot_title: 'Mrs. Rita',
              track_number: 3,
              disc_number: 1,
              confidence: 'clear',
              file: '03.flac',
            },
          ],
          missing_slots: [
            { slot_title: 'Mrs. Rita', track_number: 30, disc_number: 1, missing: true },
          ],
          missing_tracks: ['Mrs. Rita'],
          extra_files: [],
        },
      ]}
    />,
  );
  expect(screen.getByText('1.30')).toBeTruthy();
  expect(screen.getAllByText('Mrs. Rita')).toHaveLength(2);
  expect(screen.getByText('Missing')).toBeTruthy();
  expect(screen.queryByText('1 audio track missing')).toBeNull();
});
