import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2ArtistSummary } from '../-library-v2.types';

import { ArtistCard } from './library-v2-page';

const artist: LibraryV2ArtistSummary = {
  id: 7,
  name: 'Independent Controls',
  image_url: null,
  genres: [],
  monitored: false,
  monitor_new_items: 'all',
  quality_profile_id: 1,
  added_at: null,
  album_count: 2,
  single_count: 1,
  track_count: 10,
  tracks_present: 8,
  tracks_missing: 2,
  user_overrides: {},
};

describe('library v2 artist card semantics', () => {
  it('keeps card navigation and monitoring as sibling buttons', async () => {
    const onOpen = vi.fn();
    let monitorWrites = 0;
    server.use(
      http.post('/api/library/v2/artists/7/monitor', () => {
        monitorWrites += 1;
        return HttpResponse.json({ success: true });
      }),
    );
    const queryClient = createTestQueryClient();
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <ArtistCard artist={artist} onOpen={onOpen} />
      </QueryClientProvider>,
    );

    const open = screen.getByRole('button', { name: 'Open Independent Controls' });
    const monitor = screen.getByRole('button', { name: 'Start monitoring' });

    expect(container.querySelector('button button')).toBeNull();
    expect(open.contains(monitor)).toBe(false);

    open.focus();
    expect(open).toHaveFocus();
    fireEvent.click(open);
    expect(onOpen).toHaveBeenCalledWith(7);

    fireEvent.click(monitor);
    expect(onOpen).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(monitorWrites).toBe(1));
  });
});
