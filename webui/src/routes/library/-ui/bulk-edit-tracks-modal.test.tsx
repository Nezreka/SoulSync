import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { BulkEditTracksModal, LibraryV2CanWriteContext } from './library-v2-page';

function renderModal(trackIds: number[], onSaved = vi.fn()) {
  const queryClient = createTestQueryClient();
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  render(
    <QueryClientProvider client={queryClient}>
      <LibraryV2CanWriteContext.Provider value>
        <BulkEditTracksModal trackIds={trackIds} onClose={vi.fn()} onSaved={onSaved} />
      </LibraryV2CanWriteContext.Provider>
    </QueryClientProvider>,
  );
  return { invalidate, onSaved };
}

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

    renderModal([101, 102], onSaved);

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
    renderModal([1]);

    fireEvent.click(screen.getByLabelText('BPM'));
    const applyButton = screen.getByRole('button', { name: /Apply to 1 track/ });
    expect(applyButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText('BPM value'), { target: { value: '-5' } });
    expect(applyButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText('BPM value'), { target: { value: '90' } });
    expect(applyButton).not.toBeDisabled();
  });

  it('invalidates partial success and retries only failed track ids', async () => {
    const calls: string[] = [];
    let rejectTrack2 = true;
    server.use(
      http.patch('/api/library/v2/metadata-overrides/track/:trackId', ({ params }) => {
        const id = String(params.trackId);
        calls.push(id);
        if (id === '2' && rejectTrack2) {
          return HttpResponse.json({ success: false, error: 'conflict' }, { status: 409 });
        }
        return HttpResponse.json({ success: true, overrides: {} });
      }),
    );
    const { invalidate, onSaved } = renderModal([1, 2]);
    fireEvent.click(screen.getByLabelText('Mood'));
    fireEvent.change(screen.getByLabelText('Mood value'), { target: { value: 'Calm' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply to 2 tracks' }));

    expect(await screen.findByRole('button', { name: 'Retry 1 track' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Updated 1 track(s) (1); failed 1 (2)');
    expect(invalidate).toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();

    rejectTrack2 = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry 1 track' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(calls).toEqual(['1', '2', '2']);
  });
});
