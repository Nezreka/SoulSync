import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { UnifiedFileRemovalDialog } from './library-v2-page';

function preview(unsafeCount = 0) {
  return {
    success: true,
    entity: 'albums',
    entity_id: 42,
    title: 'Views',
    configured_roots: ['/music'],
    files: [
      {
        file_ids: [101],
        track_ids: [55],
        stored_paths: ['/music/Drake/Views/01 - One Dance.flac'],
        path: '/music/Drake/Views/01 - One Dance.flac',
        root: unsafeCount ? null : '/music',
        size: 4096,
        deletable: unsafeCount === 0,
        reason: unsafeCount ? 'outside_configured_library_roots' : null,
        album_title: 'Views',
        track_titles: ['One Dance'],
      },
    ],
    file_count: 1,
    deletable_count: unsafeCount ? 0 : 1,
    unsafe_count: unsafeCount,
    total_size: unsafeCount ? 0 : 4096,
    preview_token: 'safe-token',
  };
}

function renderDialog(props: Partial<React.ComponentProps<typeof UnifiedFileRemovalDialog>> = {}) {
  const onDone = vi.fn();
  const onCancel = vi.fn();
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <UnifiedFileRemovalDialog
        entity="albums"
        eid={42}
        fileIds={[101]}
        onDone={onDone}
        onCancel={onCancel}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onDone, onCancel };
}

describe('unified Library v2 file-removal dialog', () => {
  it('defaults to database-only removal and keeps the disk command separate', async () => {
    let submitted: unknown;
    server.use(
      http.get('/api/library/v2/albums/42/file-delete-preview', () => HttpResponse.json(preview())),
      http.post('/api/library/v2/albums/42/file-remove', async ({ request }) => {
        submitted = await request.json();
        return HttpResponse.json({
          success: true,
          operation: {
            id: 'db-only',
            status: 'completed',
            mode: 'database_only',
            actor: 'user',
            actor_profile_id: 1,
            file_count: 1,
            total_size: 4096,
            items: [],
          },
        });
      }),
    );
    const { onDone } = renderDialog();

    const removeButton = screen.getByRole('button', { name: 'Remove from library database' });
    await waitFor(() => expect(removeButton).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Reveal full path' }));
    expect(screen.getByRole('button', { name: 'Collapse full path' })).toHaveTextContent('Hide');
    fireEvent.click(removeButton);

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(submitted).toEqual({ file_ids: [101] });
  });

  it('requires destructive confirmation and deletes the whole entity only after files', async () => {
    const calls: string[] = [];
    server.use(
      http.get('/api/library/v2/albums/42/file-delete-preview', () => HttpResponse.json(preview())),
      http.post('/api/library/v2/albums/42/file-delete', async ({ request }) => {
        calls.push('files');
        expect(await request.json()).toEqual({ preview_token: 'safe-token' });
        return HttpResponse.json({
          success: true,
          operation: {
            id: 'permanent',
            status: 'completed',
            mode: 'permanent',
            actor: 'user',
            actor_profile_id: 1,
            file_count: 1,
            total_size: 4096,
            items: [],
          },
        });
      }),
      http.delete('/api/library/v2/albums/42', () => {
        calls.push('entity');
        return HttpResponse.json({ success: true });
      }),
    );
    const { onDone } = renderDialog({
      fileIds: undefined,
      removeWholeEntity: true,
      title: 'Views',
    });

    fireEvent.click(await screen.findByRole('radio', { name: /Permanently delete files/ }));
    const submit = screen.getByRole('button', { name: 'Permanently delete' });
    expect(submit).toBeDisabled();
    fireEvent.click(
      screen.getByRole('checkbox', { name: /I understand this permanently deletes/ }),
    );
    fireEvent.click(submit);

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(calls).toEqual(['files', 'entity']);
  });

  it('allows database-only removal when permanent deletion is root-blocked', async () => {
    server.use(
      http.get('/api/library/v2/albums/42/file-delete-preview', () =>
        HttpResponse.json(preview(1)),
      ),
      http.post('/api/library/v2/albums/42/file-remove', () =>
        HttpResponse.json({
          success: true,
          operation: {
            id: 'safe-db-only',
            status: 'completed',
            mode: 'database_only',
            actor: 'user',
            actor_profile_id: 1,
            file_count: 1,
            total_size: 0,
            items: [],
          },
        }),
      ),
    );
    const { onDone } = renderDialog();

    expect(await screen.findByText(/Permanent deletion is blocked for 1/)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Permanently delete files/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Remove from library database' }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  });
});
