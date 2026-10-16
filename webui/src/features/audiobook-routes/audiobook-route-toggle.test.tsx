import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AudiobookRouteToggle } from './audiobook-route-toggle';

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

const MOVE_PREVIEW = {
  success: true,
  applied: false,
  moved: 0,
  skipped: 0,
  failed: 0,
  items: [{ src: '/m/a', dst: '/b/a', action: 'move' }],
};

/** A tiny server: `routed` is its state, every request is recorded. */
function stubServer(initiallyRouted: boolean, preview = MOVE_PREVIEW, folderConfigured = true) {
  let routed = initiallyRouted;
  const requests: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const request = input as Request;
      const path = new URL(request.url).pathname.replace('/api/audiobook-routes', '') || '/';
      const text = await request.text();
      const body = text ? JSON.parse(text) : null;
      requests.push(`${request.method} ${path}${body ? ' ' + JSON.stringify(body) : ''}`);
      if (request.method === 'GET')
        return jsonResponse({
          success: true,
          artists: [],
          audiobook_folder_configured: folderConfigured,
        });
      if (path === '/check')
        return jsonResponse({ success: true, routed: routed ? ['Bobby & Max'] : [] });
      if (path.endsWith('/refresh'))
        return jsonResponse({ success: true, written: 1, unchanged: 0 });
      if (path.endsWith('/move')) {
        return jsonResponse(
          (body as { apply: boolean }).apply
            ? {
                ...preview,
                applied: true,
                moved: 1,
                items: [{ ...preview.items[0], action: 'moved' }],
              }
            : preview,
        );
      }
      routed = request.method === 'POST';
      return jsonResponse({ success: true });
    }),
  );
  return requests;
}

function renderToggle() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AudiobookRouteToggle name="Bobby & Max" />
    </QueryClientProvider>,
  );
}

describe('AudiobookRouteToggle', () => {
  beforeEach(() => {
    window.showToast = vi.fn();
  });

  afterEach(() => {
    cleanup();
    Reflect.deleteProperty(window, 'showConfirmDialog');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows the current state once the server has answered', async () => {
    stubServer(true);
    renderToggle();
    const button = await screen.findByRole('button', { name: /audiobook/i });
    await waitFor(() => expect(button.getAttribute('aria-pressed')).toBe('true'));
    expect(button.textContent).toContain('Audiobook');
  });

  it('routes the artist, then offers to move what is already in the music library', async () => {
    const requests = stubServer(false);
    const confirm = vi.fn(async () => true);
    window.showConfirmDialog = confirm;
    renderToggle();
    const button = await screen.findByRole('button', { name: /treat as audiobook/i });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(button);

    await waitFor(() => expect(requests.some((r) => r.endsWith('/refresh'))).toBe(true));
    expect(confirm).toHaveBeenCalledOnce();
    expect(JSON.stringify(confirm.mock.calls[0])).toContain('Move 1 album folder(s)');
    const order = requests.filter((r) => !r.startsWith('POST /check') && !r.startsWith('GET'));
    expect(order).toEqual([
      'POST / {"name":"Bobby & Max"}',
      'POST /Bobby%20%26%20Max/move {"apply":false}',
      'POST /Bobby%20%26%20Max/move {"apply":true}',
      // last, so a moved album is already there when its metadata.opf is written
      'POST /Bobby%20%26%20Max/refresh',
    ]);
  });

  it('leaves the albums alone when the move is declined', async () => {
    const requests = stubServer(false);
    window.showConfirmDialog = vi.fn(async () => false);
    renderToggle();
    const button = await screen.findByRole('button', { name: /treat as audiobook/i });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(button);

    await waitFor(() => expect(requests.some((r) => r.endsWith('/refresh'))).toBe(true));
    expect(requests.some((r) => r.includes('"apply":true'))).toBe(false);
  });

  it('does not ask about moving when there is nothing to move', async () => {
    stubServer(false, { ...MOVE_PREVIEW, items: [] });
    const confirm = vi.fn(async () => true);
    window.showConfirmDialog = confirm;
    renderToggle();
    const button = await screen.findByRole('button', { name: /treat as audiobook/i });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(button);

    await waitFor(() => expect(window.showToast).toHaveBeenCalled());
    expect(confirm).not.toHaveBeenCalled();
  });

  it('stops routing with one click and never offers a move', async () => {
    const requests = stubServer(true);
    const confirm = vi.fn(async () => true);
    window.showConfirmDialog = confirm;
    renderToggle();
    const button = await screen.findByRole('button', { name: /audiobook/i });
    await waitFor(() => expect(button.getAttribute('aria-pressed')).toBe('true'));

    fireEvent.click(button);

    await waitFor(() => expect(requests.some((r) => r.startsWith('DELETE'))).toBe(true));
    expect(confirm).not.toHaveBeenCalled();
    expect(requests.some((r) => r.includes('/move') || r.includes('/refresh'))).toBe(false);
  });

  it('is disabled with an explanation until an audiobook folder is set', async () => {
    const requests = stubServer(false, MOVE_PREVIEW, false);
    renderToggle();
    const button = (await screen.findByRole('button', {
      name: /treat as audiobook/i,
    })) as HTMLButtonElement;
    await waitFor(() => expect(button.title).toContain('Set the audiobook folder first'));
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(requests.some((r) => r.startsWith('POST / '))).toBe(false);
  });

  it('can still switch an artist off when the folder setting has gone', async () => {
    stubServer(true, MOVE_PREVIEW, false);
    renderToggle();
    const button = (await screen.findByRole('button', { name: /audiobook/i })) as HTMLButtonElement;
    await waitFor(() => expect(button.getAttribute('aria-pressed')).toBe('true'));
    expect(button.disabled).toBe(false);
  });
});
