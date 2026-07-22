import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { BulkEditTracksModal } from './library-v2-page';

describe('BulkEditTracksModal', () => {
  it('applies only the checked fields to every selected track', async () => {
    const submitted: Array<{ trackId: string; body: unknown }> = [];
    const onSaved = vi.fn();
    server.use(
      http.patch(
        '/api/library/v2/metadata-overrides/track/:trackId',
        async ({ request, params }) => {
          submitted.push({ trackId: String(params.trackId), body: await request.json() });
          return HttpResponse.json({ success: true, overrides: {} });
        },
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <BulkEditTracksModal trackIds={[101, 102]} onClose={vi.fn()} onSaved={onSaved} />
      </QueryClientProvider>,
    );

    // Nothing checked yet -> apply button disabled.
    const applyButton = screen.getByRole('button', { name: /Apply to 2 tracks/ });
    expect(applyButton).toBeDisabled();

    fireEvent.click(screen.getByLabelText('Mood'));
    fireEvent.change(screen.getByLabelText('Mood value'), { target: { value: 'Chill' } });
    fireEvent.click(screen.getByLabelText('Explicit'));
    fireEvent.change(screen.getByLabelText('Explicit value'), { target: { value: 'no' } });

    expect(applyButton).not.toBeDisabled();
    fireEvent.click(applyButton);

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(submitted).toHaveLength(2);
    expect(submitted.map((s) => s.trackId).sort()).toEqual(['101', '102']);
    for (const { body } of submitted) {
      expect(body).toEqual({ set: { mood: 'Chill', explicit: false }, clear: [] });
    }
  });

  it('disables Apply while a checked bpm field holds an invalid value', () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <BulkEditTracksModal trackIds={[1]} onClose={vi.fn()} onSaved={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByLabelText('BPM'));
    const applyButton = screen.getByRole('button', { name: /Apply to 1 track/ });
    expect(applyButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText('BPM value'), { target: { value: '-5' } });
    expect(applyButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText('BPM value'), { target: { value: '90' } });
    expect(applyButton).not.toBeDisabled();
  });
});
