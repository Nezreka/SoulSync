import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2ArtistDetail } from '../-library-v2.types';

import { ArtistSettingsModal } from './library-v2-page';

const artist: LibraryV2ArtistDetail = {
  id: 7,
  name: 'Drake',
  image_url: '/api/library/v2/artwork/artist/7',
  summary: null,
  style: null,
  mood: null,
  label: null,
  genres: ['hip hop'],
  monitored: true,
  monitor_new_items: 'all',
  quality_profile: {
    id: 1,
    name: 'Lossless',
    description: null,
    upgrade_policy: 'acceptable',
    upgrade_cutoff_index: 0,
    ranked_targets: [],
    repair_settings: {},
    repair_job_id: '',
    is_default: false,
  },
  quality_profile_source: 'artist',
  quality_profile_source_id: 7,
  quality_profile_explicit: true,
  albums: [],
  eps: [],
  singles: [],
  album_count: 0,
  single_count: 0,
  discography_count: 0,
  user_overrides: {},
};

const settings = {
  artist_id: 7,
  watchlist_row_id: 11,
  watchlist_name: 'Drake',
  watchlist_image_url: 'https://img/drake.jpg',
  provider_ids: { spotify: 'sp-drake', deezer: '246791' },
  monitor_new_items: 'all' as const,
  include_albums: true,
  include_eps: true,
  include_singles: true,
  include_live: false,
  include_remixes: false,
  include_acoustic: false,
  include_compilations: false,
  include_instrumentals: false,
  auto_download: true,
  lookback_days: 30,
  preferred_metadata_source: 'deezer',
};

function handlers() {
  server.use(
    http.get('/api/library/v2/artists/7/settings', () =>
      HttpResponse.json({
        success: true,
        settings,
        metadata_sources: ['spotify', 'deezer', 'musicbrainz'],
        global_metadata_source: 'spotify',
      }),
    ),
    http.get('/api/library/v2/quality-profiles', () =>
      HttpResponse.json({ success: true, profiles: [artist.quality_profile] }),
    ),
    http.get('/api/library/v2/artists/7/match-status', () =>
      HttpResponse.json({ success: true, services: [] }),
    ),
  );
}

function renderModal() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <ArtistSettingsModal artist={artist} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe('Library v2 Artist Settings', () => {
  it('edits the existing Watchlist settings through one combined contract', async () => {
    handlers();
    let submitted: unknown;
    server.use(
      http.put('/api/library/v2/artists/7/settings', async ({ request }) => {
        submitted = await request.json();
        return HttpResponse.json({
          success: true,
          settings: { ...settings, auto_download: false, include_singles: false },
          metadata_sources: ['spotify', 'deezer', 'musicbrainz'],
          global_metadata_source: 'spotify',
        });
      }),
    );

    renderModal();

    expect(await screen.findByText('Watchlist identity')).toBeInTheDocument();
    expect(screen.getByTitle('Copy spotify ID: sp-drake')).toBeInTheDocument();
    expect(await screen.findByText(/Effective: Lossless/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: /Auto-download new releases/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Singles' }));
    fireEvent.change(screen.getByLabelText('Preferred metadata provider'), {
      target: { value: 'spotify' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save future-release settings' }));

    expect(await screen.findByText('Watchlist Artist Settings saved.')).toBeInTheDocument();
    expect(submitted).toMatchObject({
      auto_download: false,
      include_singles: false,
      preferred_metadata_source: 'spotify',
      monitor_new_items: 'all',
    });
  });

  it('keeps existing-release Wanted actions separate from future-release settings', async () => {
    handlers();
    const submitted: unknown[] = [];
    server.use(
      http.post('/api/library/v2/artists/7/releases/monitor', async ({ request }) => {
        submitted.push(await request.json());
        return HttpResponse.json({ success: true, job_id: 'settings-monitor-job' });
      }),
      http.get('/api/library/v2/jobs/status', () =>
        HttpResponse.json({ running: false, error: null }),
      ),
    );

    renderModal();
    fireEvent.click(await screen.findByRole('button', { name: 'Monitor missing only' }));

    expect(await screen.findByText('Monitor missing releases applied.')).toBeInTheDocument();
    await waitFor(() => expect(submitted).toEqual([{ scope: 'missing', monitored: true }]));
  });
});
