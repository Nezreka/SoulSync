import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { LibraryV2Track } from '../-library-v2.types';

import { TrackQualityProfileBadge } from './library-v2-page';

function track(overrides: Partial<LibraryV2Track> = {}): LibraryV2Track {
  return {
    id: 1,
    title: 'Track',
    track_number: 1,
    disc_number: 1,
    duration: null,
    isrc: null,
    monitored: true,
    quality_profile_id: 1,
    canonical_track_id: null,
    artists: [],
    file: {
      path: '/music/track.flac',
      size: null,
      bitrate: null,
      sample_rate: null,
      bit_depth: null,
      format: null,
      quality_tier: 'unknown',
      verification_status: null,
      import_status: null,
      source: null,
      file_state: null,
    },
    file_status: 'present',
    metadata_gaps: [],
    meets_profile: null,
    upgrade_candidate: null,
    ...overrides,
  };
}

describe('Library v2 quality evaluation state', () => {
  it('renders unknown quality as an explicit third state', () => {
    render(<TrackQualityProfileBadge track={track()} />);

    expect(screen.getByText('quality unknown')).toHaveAttribute(
      'title',
      'Scan the file to evaluate its quality profile',
    );
  });

  it('keeps known below-profile and upgrade states distinct', () => {
    const { rerender } = render(
      <TrackQualityProfileBadge track={track({ meets_profile: false, upgrade_candidate: true })} />,
    );
    expect(screen.getByText('below profile')).toBeInTheDocument();

    rerender(
      <TrackQualityProfileBadge track={track({ meets_profile: true, upgrade_candidate: true })} />,
    );
    expect(screen.getByText('upgrade ↑')).toBeInTheDocument();
  });
});
