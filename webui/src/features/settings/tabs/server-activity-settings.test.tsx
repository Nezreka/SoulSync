/**
 * Settings > Connections > Server Activity: the upload cap, the location
 * lookup, play log retention with its clear button, and the stop messages,
 * each saved on its own through /api/server-activity/settings. A bad value is
 * refused and the field goes back; a non-admin gets nothing drawn.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { ServerActivitySettingsCard } from './server-activity-settings';

const BASE = {
  upload_mbps: 40,
  geo_lookup: true,
  retention_days: 0,
  stop_presets: ['Server maintenance, back soon.'],
};

function serve(initial = BASE) {
  let saved = { ...initial };
  const posts: Record<string, unknown>[] = [];
  let cleared = 0;
  server.use(
    http.get('*/api/server-activity/settings', () =>
      HttpResponse.json({ ok: true, settings: saved }),
    ),
    http.post('*/api/server-activity/settings', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      posts.push(body);
      if (
        body.upload_mbps !== undefined &&
        body.upload_mbps !== null &&
        Number(body.upload_mbps) <= 0
      ) {
        return HttpResponse.json(
          { ok: false, error: 'upload speed must be a number of Mbps' },
          { status: 400 },
        );
      }
      saved = { ...saved, ...body };
      return HttpResponse.json({ ok: true, settings: saved });
    }),
    http.post('*/api/server-activity/plays/clear', () => {
      cleared += 1;
      return HttpResponse.json({ ok: true, removed: 412 });
    }),
  );
  return { posts, cleared: () => cleared };
}

afterEach(() => {
  delete window.showConfirmDialog;
  delete window.showToast;
});

describe('server activity settings', () => {
  it('loads what is saved', async () => {
    serve();
    render(<ServerActivitySettingsCard />);
    expect(await screen.findByLabelText('Upload speed')).toHaveValue(40);
    expect(screen.getByLabelText('Show where remote viewers are')).toBeChecked();
    expect(screen.getByLabelText('Keep plays for')).toHaveValue('0');
    expect(screen.getByRole('list', { name: 'Stop messages', hidden: true })).toHaveTextContent(
      'Server maintenance, back soon.',
    );
  });

  it('saves the upload cap on leaving the field, empty meaning no bar', async () => {
    const { posts } = serve();
    render(<ServerActivitySettingsCard />);
    const field = await screen.findByLabelText('Upload speed');
    fireEvent.change(field, { target: { value: '' } });
    fireEvent.blur(field);
    await waitFor(() => expect(posts).toEqual([{ upload_mbps: null }]));
  });

  it('a refused value is said and the field goes back', async () => {
    window.showToast = vi.fn();
    serve();
    render(<ServerActivitySettingsCard />);
    const field = await screen.findByLabelText('Upload speed');
    fireEvent.change(field, { target: { value: '-3' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'upload speed must be a number of Mbps',
        'error',
      ),
    );
    expect(field).toHaveValue(40);
  });

  it('the lookup switch, the retention and the presets save on their own', async () => {
    const { posts } = serve();
    render(<ServerActivitySettingsCard />);
    fireEvent.click(await screen.findByLabelText('Show where remote viewers are'));
    await waitFor(() => expect(posts.at(-1)).toEqual({ geo_lookup: false }));
    fireEvent.change(screen.getByLabelText('Keep plays for'), { target: { value: '90' } });
    await waitFor(() => expect(posts.at(-1)).toEqual({ retention_days: 90 }));
    fireEvent.change(screen.getByPlaceholderText('Add a message'), {
      target: { value: 'Movie night is over.' },
    });
    fireEvent.keyDown(screen.getByPlaceholderText('Add a message'), { key: 'Enter' });
    await waitFor(() =>
      expect(posts.at(-1)).toEqual({
        stop_presets: ['Server maintenance, back soon.', 'Movie night is over.'],
      }),
    );
    const list = screen.getByRole('list', { name: 'Stop messages', hidden: true });
    fireEvent.click(
      within(list).getByRole('button', {
        name: 'Remove "Server maintenance, back soon."',
        hidden: true,
      }),
    );
    await waitFor(() => expect(posts.at(-1)).toEqual({ stop_presets: ['Movie night is over.'] }));
  });

  it('clears the play log only after the house confirm', async () => {
    window.showToast = vi.fn();
    window.showConfirmDialog = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const { cleared } = serve();
    render(<ServerActivitySettingsCard />);
    const button = await screen.findByRole('button', { name: 'Clear play log', hidden: true });
    fireEvent.click(button);
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalledTimes(1));
    expect(cleared()).toBe(0);
    fireEvent.click(button);
    await waitFor(() => expect(cleared()).toBe(1));
    expect(window.showConfirmDialog).toHaveBeenLastCalledWith(
      expect.objectContaining({ destructive: true, confirmText: 'Clear play log' }),
    );
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Cleared 412 plays from the log.', 'success'),
    );
  });

  it('draws nothing for a profile that is not an admin (#2155)', async () => {
    let asked = false;
    server.use(
      http.get('*/api/server-activity/settings', () => {
        asked = true;
        return HttpResponse.json(
          { success: false, error: 'Admin access required' },
          { status: 403 },
        );
      }),
    );
    const { container } = render(<ServerActivitySettingsCard />);
    await waitFor(() => expect(asked).toBe(true));
    expect(container).toBeEmptyDOMElement();
  });
});
