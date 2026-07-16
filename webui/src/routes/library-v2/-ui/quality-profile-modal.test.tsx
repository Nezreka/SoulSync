import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { QualityProfileModal } from './quality-profile-modal';

describe('library v2 quality-profile mutation', () => {
  it('shows a rejected assignment and retries the selected profile', async () => {
    let attempts = 0;
    const submitted: unknown[] = [];
    const onClose = vi.fn();
    server.use(
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({
          success: true,
          profiles: [
            {
              id: 9,
              name: 'Lossless',
              description: 'Keep FLAC',
              upgrade_policy: 'until_top',
              upgrade_cutoff_index: 0,
              ranked_targets: [],
              repair_job_id: '',
              repair_settings: {},
              is_default: false,
            },
          ],
        }),
      ),
      http.post('/api/library/v2/albums/42/quality-profile', async ({ request }) => {
        attempts += 1;
        submitted.push(await request.json());
        return HttpResponse.json(
          attempts === 1
            ? { success: false, error: 'Profile assignment failed' }
            : { success: true },
        );
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <QualityProfileModal
          entity="albums"
          id={42}
          currentProfileId={1}
          title="Selected Release"
          onClose={onClose}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Lossless/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Profile assignment failed');
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(attempts).toBe(2);
    expect(submitted).toEqual([
      { quality_profile_id: 9, inherit: false, cascade: true, monitor_existing: false },
      { quality_profile_id: 9, inherit: false, cascade: true, monitor_existing: false },
    ]);
  });

  it('shows effective provenance and can clear an explicit override', async () => {
    let submitted: unknown;
    server.use(
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({
          success: true,
          profiles: [
            {
              id: 9,
              name: 'Lossless',
              description: 'Keep FLAC',
              upgrade_policy: 'until_top',
              upgrade_cutoff_index: 0,
              ranked_targets: [],
              repair_job_id: '',
              repair_settings: {},
              is_default: false,
            },
          ],
        }),
      ),
      http.post('/api/library/v2/albums/42/quality-profile', async ({ request }) => {
        submitted = await request.json();
        return HttpResponse.json({ success: true });
      }),
    );
    const queryClient = createTestQueryClient();
    const onClose = vi.fn();
    render(
      <QueryClientProvider client={queryClient}>
        <QualityProfileModal
          entity="albums"
          id={42}
          currentProfileId={9}
          currentProfileSource="album"
          currentProfileExplicit
          title="Selected Release"
          onClose={onClose}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Effective: Lossless (Album override)')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Use inherited profile' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(submitted).toEqual({ inherit: true, cascade: true, monitor_existing: false });
  });
});
