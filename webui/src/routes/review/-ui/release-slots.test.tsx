import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { AssignmentEvidence, ReleaseComparison } from './reconciliation-evidence';

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
  expect(screen.getByText('30')).toBeTruthy();
  expect(screen.getAllByText('Mrs. Rita')).toHaveLength(2);
  expect(screen.getByText('Missing')).toBeTruthy();
  expect(screen.queryByText('1 audio track missing')).toBeNull();
});

it('distinguishes a cancelled missing-track request from an active download', () => {
  const { rerender } = render(
    <AssignmentEvidence
      row={{ status: 'missing', slot_title: 'Missing song', fulfillment_state: 'cancelled' }}
      album={{ album: 'Album', folder: '/music/Album' } as never}
    />,
  );
  expect(screen.getByText(/The request was cancelled/)).toBeTruthy();
  expect(screen.queryByText(/Waiting for a verified local file/)).toBeNull();
  rerender(
    <AssignmentEvidence
      row={{ status: 'missing', slot_title: 'Missing song', fulfillment_state: 'wishlisted' }}
      album={{ album: 'Album', folder: '/music/Album' } as never}
    />,
  );
  expect(screen.getByText(/Waiting for a verified local file/)).toBeTruthy();
});

it('loads exact MusicBrainz release artwork for persisted candidates without image URLs', () => {
  const id = '6c0de14f-703b-4ce7-a8cd-2997790ec7e2';
  const { container } = render(
    <ReleaseComparison
      selected={0}
      onSelect={() => {}}
      candidates={[
        { source: 'musicbrainz', release_id: id, release_title: 'Exact edition', image_url: '' },
      ]}
    />,
  );
  const image = container.querySelector('img');
  expect(image).toBeTruthy();
  expect(decodeURIComponent(image!.getAttribute('src')!)).toContain(
    `coverartarchive.org/release/${id}/front-250`,
  );
});
