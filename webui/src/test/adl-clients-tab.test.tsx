/**
 * the Clients tab: three sections, health states, actions, and the
 * soulsync-vs-external labeling.
 */

import { render, waitFor, fireEvent } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AdlClientsTab } from '@/routes/active-downloads/-ui/adl-clients';
import { server } from '@/test/msw';

const TORRENT_OK = {
  success: true,
  configured: true,
  type: 'qbittorrent',
  connected: true,
  items: [
    {
      id: 'HASH1',
      name: 'Movie.2026.1080p.mkv',
      state: 'downloading',
      progress: 0.42,
      size: 1000_000_000,
      downloaded: 420_000_000,
      download_speed: 5_000_000,
      upload_speed: 0,
      seeders: 12,
      soulsync: { kind: 'movie', title: 'Movie (2026)' },
    },
    {
      id: 'HASH2',
      name: 'someone.elses.iso',
      state: 'paused',
      progress: 1,
      size: 1,
      downloaded: 1,
      download_speed: 0,
      upload_speed: 0,
    },
  ],
};

const EMPTY_UNCONFIGURED = {
  success: true,
  configured: false,
  connected: false,
  items: [],
};

let toasts: string[] = [];

function mockAll({
  torrent = TORRENT_OK,
  usenet = EMPTY_UNCONFIGURED,
  slskd = EMPTY_UNCONFIGURED,
}: Record<string, unknown> = {}) {
  server.use(
    http.get('/api/clients/torrent', () => HttpResponse.json(torrent)),
    http.get('/api/clients/usenet', () => HttpResponse.json(usenet)),
    http.get('/api/clients/slskd', () => HttpResponse.json(slskd)),
  );
}

beforeEach(() => {
  toasts = [];
  window.showToast = vi.fn((message: string) => {
    toasts.push(message);
  });
  window.showConfirmDialog = vi.fn(() => Promise.resolve(true));
});

describe('AdlClientsTab', () => {
  it('renders three sections with honest health states', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => {
      expect(container.textContent).toContain('connected');
    });
    const sections = container.querySelectorAll('.adl-client-section');
    expect(sections).toHaveLength(3);
    expect(container.textContent).toContain('Soulseek');
    expect(container.textContent).toContain('Torrents');
    expect(container.textContent).toContain('Usenet');
    // the two unconfigured clients say so instead of pretending to be empty
    expect(container.textContent).toContain('not configured');
    expect(container.textContent).toContain('qBittorrent');
  });

  it('labels soulsync-dispatched rows and external rows differently', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('Movie (2026)'));
    const owners = [...container.querySelectorAll('.adl-client-owner')].map(
      (el) => el.textContent,
    );
    expect(owners).toContain('Movie (2026)');
    expect(owners).toContain('external');
  });

  it('an unreachable client reports its error, not an empty list', async () => {
    mockAll({
      torrent: {
        success: true,
        configured: true,
        type: 'qbittorrent',
        connected: false,
        error: 'connection refused',
        items: [],
      },
    });
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('unreachable'));
    expect(container.textContent).toContain('connection refused');
  });

  it('pause fires the action endpoint for the right torrent', async () => {
    mockAll();
    let body: unknown;
    server.use(
      http.post('/api/clients/torrent/action', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true });
      }),
    );
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('Movie (2026)'));
    const pauseBtn = [...container.querySelectorAll('.verif-act')].find(
      (b) => b.getAttribute('title') === 'Pause',
    );
    fireEvent.click(pauseBtn as HTMLElement);
    await waitFor(() => expect(body).toBeTruthy());
    expect(body).toEqual({ id: 'HASH1', action: 'pause', delete_files: false });
    expect(toasts[0]).toBe('Pause ok');
  });

  it('a paused row offers resume instead of pause', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('someone.elses.iso'));
    const titles = [...container.querySelectorAll('.verif-act')].map((b) =>
      b.getAttribute('title'),
    );
    expect(titles).toContain('Resume');
  });

  it('remove asks about the files and carries the answer', async () => {
    mockAll();
    let body: unknown;
    server.use(
      http.post('/api/clients/torrent/action', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true });
      }),
    );
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('Movie (2026)'));
    const removeBtn = [...container.querySelectorAll('.verif-act-del')][0];
    fireEvent.click(removeBtn as HTMLElement);
    await waitFor(() => expect(body).toBeTruthy());
    expect(body).toEqual({ id: 'HASH1', action: 'remove', delete_files: true });
  });

  it('slskd rows cancel with username and id', async () => {
    mockAll({
      slskd: {
        success: true,
        configured: true,
        connected: true,
        items: [
          {
            id: 'd9',
            filename: 'Music/song.flac',
            username: 'peer1',
            state: 'InProgress',
            progress: 30,
            size: 100,
            transferred: 30,
            speed: 5,
          },
        ],
      },
    });
    let body: unknown;
    server.use(
      http.post('/api/clients/slskd/action', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true });
      }),
    );
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('song.flac'));
    expect(container.textContent).toContain('from peer1');
    const cancelBtn = [...container.querySelectorAll('.verif-act-del')].find(
      (b) => b.getAttribute('title') === 'Cancel this transfer in slskd',
    );
    fireEvent.click(cancelBtn as HTMLElement);
    await waitFor(() => expect(body).toBeTruthy());
    expect(body).toEqual({ id: 'd9', username: 'peer1', action: 'cancel', remove: true });
  });
});
