import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

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

function renderWithClient(node: React.ReactElement) {
  server.use(
    http.get('/api/library/v2/ui-preferences', () =>
      HttpResponse.json({
        success: true,
        preferences: { track_table: { visible_match_providers: {} } },
      }),
    ),
  );
  const queryClient = createTestQueryClient();
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

describe('library v2 match chips (deep-dive A8)', () => {
  it('hides chips flagged unavailable', () => {
    renderWithClient(
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
    renderWithClient(
      <MatchChips entityType="artist" entityName="Drake" services={[withoutAvailable]} />,
    );

    expect(screen.getByText('Spotify: matched')).toBeInTheDocument();
  });

  it('renders nothing when every chip is unavailable', () => {
    const { container } = renderWithClient(
      <MatchChips
        entityType="artist"
        entityName="Drake"
        services={[service({ available: false })]}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
