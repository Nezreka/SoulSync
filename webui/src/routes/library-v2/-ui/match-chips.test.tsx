import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { LibraryV2MatchService } from '../-library-v2.types';

import { MatchChips } from './library-v2-page';

function service(overrides: Partial<LibraryV2MatchService> = {}): LibraryV2MatchService {
  return {
    service: 'spotify',
    label: 'Spotify',
    status: 'matched',
    external_id: 'sp1',
    last_attempted: null,
    legacy_entity_id: 5,
    available: true,
    ...overrides,
  };
}

describe('library v2 match chips (deep-dive A8)', () => {
  it('hides chips flagged unavailable', () => {
    render(
      <MatchChips
        entityType="artist"
        entityName="Drake"
        services={[
          service({ service: 'spotify', label: 'Spotify', available: true }),
          service({ service: 'tidal', label: 'Tidal', available: false }),
        ]}
      />,
    );

    expect(screen.getByText('Spotify: matched')).toBeInTheDocument();
    expect(screen.queryByText(/Tidal/)).not.toBeInTheDocument();
  });

  it('treats a missing `available` field as available (older cached responses)', () => {
    const { available: _unused, ...withoutAvailable } = service();
    render(<MatchChips entityType="artist" entityName="Drake" services={[withoutAvailable]} />);

    expect(screen.getByText('Spotify: matched')).toBeInTheDocument();
  });

  it('renders nothing when every chip is unavailable', () => {
    const { container } = render(
      <MatchChips
        entityType="artist"
        entityName="Drake"
        services={[service({ available: false })]}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
