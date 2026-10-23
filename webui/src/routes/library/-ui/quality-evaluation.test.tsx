import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { LibraryV2Track } from '../-library-v2.types';

import { lib2Track } from '../-library-v2.test-fixtures';
import { TrackQualityProfileBadge } from './library-v2-page';

function track(overrides: Partial<LibraryV2Track> = {}): LibraryV2Track {
  return lib2Track({ id: 1, ...overrides });
}

describe('Library v2 quality evaluation state', () => {
  it('describes an untargeted format as a profile choice', () => {
    render(
      <TrackQualityProfileBadge
        track={track({
          meets_profile: false,
          upgrade_candidate: true,
          quality_issue: 'format_not_targeted',
        })}
      />,
    );
    expect(
      screen.getByTitle('Format not targeted by the effective quality profile'),
    ).toBeInTheDocument();
    expect(screen.queryByTitle("Below the album's quality profile")).not.toBeInTheDocument();
  });

  it('renders unknown quality as an explicit third state', () => {
    render(<TrackQualityProfileBadge track={track()} />);

    expect(screen.getByTitle('Quality unknown (scan the file to evaluate)')).toBeInTheDocument();
  });

  it('keeps known below-profile and upgrade states distinct', () => {
    const { rerender } = render(
      <TrackQualityProfileBadge track={track({ meets_profile: false, upgrade_candidate: true })} />,
    );
    expect(screen.getByTitle("Below the album's quality profile")).toBeInTheDocument();

    rerender(
      <TrackQualityProfileBadge track={track({ meets_profile: true, upgrade_candidate: true })} />,
    );
    expect(
      screen.getByTitle('A higher-quality version may be available (upgrade candidate)'),
    ).toBeInTheDocument();
  });
});
