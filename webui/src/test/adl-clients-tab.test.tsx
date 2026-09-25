/**
 * the Clients tab: sub-tab per client, live health dots, progress rows,
 * actions — and the rule that a failed fetch SHOWS ITS ERROR instead of
 * sitting on "loading…" forever (the bug that shipped first).
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
      size: 1_000_000_000,
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

const SLSKD_OK = {
  success: true,
  configured: true,
  connected: true,
  items: [
    {
      id: 'd9',
      filename: 'Music\\Artist\\song.flac',
      username: 'peer1',
      state: 'InProgress',
      progress: 30,
      size: 100,
      transferred: 30,
      speed: 5,
    },
  ],
};

const SLSKD_WITH_UPLOADS = {
  ...SLSKD_OK,
  uploads: [
    {
      id: 'u1',
      filename: 'Shared\\give.flac',
      username: 'leecher9',
      state: 'InProgress',
      progress: 55,
      size: 200,
      transferred: 110,
      speed: 42,
    },
  ],
  counts: { downloads_completed: 250, uploads_completed: 14000 },
};

const UNCONFIGURED = { success: true, configured: false, connected: false, items: [] };

let toasts: string[] = [];

function mockAll({
  torrent = TORRENT_OK,
  usenet = UNCONFIGURED,
  slskd = SLSKD_OK,
}: Record<string, Record<string, unknown>> = {}) {
  server.use(
    http.get('/api/clients/torrent', () => HttpResponse.json(torrent)),
    http.get('/api/clients/usenet', () => HttpResponse.json(usenet)),
    http.get('/api/clients/slskd', () => HttpResponse.json(slskd)),
  );
}

function pill(container: HTMLElement, key: string) {
  return container.querySelector(`[data-client-tab="${key}"]`) as HTMLElement;
}

beforeEach(() => {
  toasts = [];
  window.showToast = vi.fn((message: string) => {
    toasts.push(message);
  });
  window.showConfirmDialog = vi.fn(() => Promise.resolve(true));
});

describe('AdlClientsTab', () => {
  it('renders three pills with health dots; soulseek opens first', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.querySelector('.adl-client-dot-ok')).not.toBeNull());
    expect(container.querySelectorAll('[data-client-tab]')).toHaveLength(3);
    // soulseek is the active tab: its transfer renders, filename basename first
    expect(container.textContent).toContain('song.flac');
    expect(container.textContent).toContain('from peer1');
    // usenet is unconfigured: gray dot, no count
    expect(pill(container, 'usenet').querySelector('.adl-client-dot-off')).not.toBeNull();
    expect(pill(container, 'usenet').textContent).not.toContain('(');
  });

  it('switching pills swaps the list', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('song.flac'));
    fireEvent.click(pill(container, 'torrent'));
    await waitFor(() => expect(container.textContent).toContain('Movie.2026.1080p.mkv'));
    expect(container.textContent).not.toContain('song.flac');
    expect(container.textContent).toContain('qBittorrent');
    expect(container.textContent).toContain('12 seeders');
  });

  it('rows carry a progress bar sized to the transfer', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('song.flac'));
    const fill = container.querySelector('.adl-client-progress-fill') as HTMLElement;
    // slskd reports 0-100 already
    expect(fill.style.width).toBe('30%');
    expect(container.textContent).toContain('30%');
  });

  it('a failed fetch shows its error text, never an eternal loading state', async () => {
    mockAll({});
    server.use(
      http.get('/api/clients/slskd', () =>
        HttpResponse.json({ success: false, error: 'boom from the bridge' }),
      ),
    );
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain("couldn't load"));
    expect(container.textContent).toContain('boom from the bridge');
    expect(container.textContent).not.toContain('loading…');
    expect(pill(container, 'soulseek').querySelector('.adl-client-dot-bad')).not.toBeNull();
  });

  it('an http 500 also surfaces instead of spinning', async () => {
    mockAll({});
    server.use(
      http.get('/api/clients/slskd', () =>
        HttpResponse.json({ error: 'internal' }, { status: 500 }),
      ),
    );
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain("couldn't load"));
  });

  it('an unreachable client reports the adapter error', async () => {
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
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
    await waitFor(() => expect(container.textContent).toContain('connection refused'));
    expect(container.textContent).toContain('unreachable');
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
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
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
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
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
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
    await waitFor(() => expect(container.textContent).toContain('Movie (2026)'));
    const removeBtn = [...container.querySelectorAll('.verif-act-del')][0];
    fireEvent.click(removeBtn as HTMLElement);
    await waitFor(() => expect(body).toBeTruthy());
    expect(body).toEqual({ id: 'HASH1', action: 'remove', delete_files: true });
  });

  it('slskd rows cancel with username and id', async () => {
    mockAll();
    let body: unknown;
    server.use(
      http.post('/api/clients/slskd/action', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true });
      }),
    );
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('song.flac'));
    const cancelBtn = [...container.querySelectorAll('.verif-act-del')].find(
      (b) => b.getAttribute('title') === 'Cancel this transfer in slskd',
    );
    fireEvent.click(cancelBtn as HTMLElement);
    await waitFor(() => expect(body).toBeTruthy());
    expect(body).toEqual({ id: 'd9', username: 'peer1', action: 'cancel', remove: true });
  });

  it('a card expands on click to show everything the client reports', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
    await waitFor(() => expect(container.textContent).toContain('Movie (2026)'));
    // collapsed: no detail grid
    expect(container.querySelector('.adl-client-details')).toBeNull();
    const card = container.querySelector('.adl-client-card') as HTMLElement;
    fireEvent.click(card);
    const details = container.querySelector('.adl-client-details') as HTMLElement;
    expect(details).not.toBeNull();
    expect(details.textContent).toContain('Hash');
    expect(details.textContent).toContain('HASH1');
    expect(details.textContent).toContain('Seeders');
    // click again folds it back up
    fireEvent.click(card);
    expect(container.querySelector('.adl-client-details')).toBeNull();
  });

  it('clicking an action button does not toggle the card', async () => {
    mockAll();
    server.use(
      http.post('/api/clients/torrent/action', () => HttpResponse.json({ success: true })),
    );
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
    await waitFor(() => expect(container.textContent).toContain('Movie (2026)'));
    const pauseBtn = [...container.querySelectorAll('.verif-act')].find(
      (b) => b.getAttribute('title') === 'Pause',
    );
    fireEvent.click(pauseBtn as HTMLElement);
    expect(container.querySelector('.adl-client-details')).toBeNull();
  });

  it('empty detail values are dropped from the grid', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('song.flac'));
    fireEvent.click(container.querySelector('.adl-client-card') as HTMLElement);
    const details = container.querySelector('.adl-client-details') as HTMLElement;
    // the slskd fixture has no file_path/soulsync - those labels must not render
    expect(details.textContent).toContain('Remote path');
    expect(details.textContent).toContain('Music\\Artist\\song.flac');
    expect(details.textContent).not.toContain('Local file');
    expect(details.textContent).not.toContain('SoulSync');
  });

  it('labels soulsync rows and external rows differently', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
    await waitFor(() => expect(container.textContent).toContain('Movie (2026)'));
    const owners = [...container.querySelectorAll('.adl-client-owner')].map((el) => el.textContent);
    expect(owners).toContain('Movie (2026)');
    expect(owners).toContain('external');
  });
});

describe('the toolbar', () => {
  it('search filters the list by name', async () => {
    mockAll({
      torrent: {
        ...TORRENT_OK,
        items: [
          ...TORRENT_OK.items,
          { ...TORRENT_OK.items[1], id: 'HASH3', name: 'Different.Show.mkv' },
        ],
      },
    });
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
    await waitFor(() => expect(container.textContent).toContain('Movie.2026.1080p.mkv'));
    fireEvent.change(container.querySelector('.adl-client-search') as HTMLElement, {
      target: { value: 'different' },
    });
    expect(container.textContent).toContain('Different.Show.mkv');
    expect(container.textContent).not.toContain('Movie.2026.1080p.mkv');
    expect(container.textContent).toContain('1 shown');
  });

  it('state chips filter and show counts', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
    await waitFor(() => expect(container.textContent).toContain('Movie.2026.1080p.mkv'));
    const chips = [...container.querySelectorAll('.adl-client-chip')].map((c) => c.textContent);
    expect(chips).toContain('downloading (1)');
    expect(chips).toContain('paused (1)');
    const pausedChip = [...container.querySelectorAll('.adl-client-chip')].find(
      (c) => c.textContent === 'paused (1)',
    );
    fireEvent.click(pausedChip as HTMLElement);
    expect(container.textContent).toContain('someone.elses.iso');
    expect(container.textContent).not.toContain('Movie.2026.1080p.mkv');
  });

  it('aggregates the visible download speed', async () => {
    mockAll();
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
    await waitFor(() => expect(container.textContent).toContain('shown'));
    expect(container.querySelector('.adl-client-aggregate')?.textContent).toContain('4.8 MB/s');
  });
});

describe('bulk actions', () => {
  it('pause all sends every visible id in one request', async () => {
    mockAll();
    let body: unknown;
    server.use(
      http.post('/api/clients/torrent/action', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true, done: 2, failed: [] });
      }),
    );
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
    await waitFor(() => expect(container.textContent).toContain('Movie.2026.1080p.mkv'));
    const pauseAll = [...container.querySelectorAll('button')].find(
      (b) => b.textContent === '⏸ Pause all',
    );
    fireEvent.click(pauseAll as HTMLElement);
    await waitFor(() => expect(body).toBeTruthy());
    expect(body).toEqual({ ids: ['HASH1', 'HASH2'], action: 'pause', delete_files: false });
    expect(toasts[0]).toBe('Pause all: 2 ok');
  });
});

describe('the add box', () => {
  it('sends a magnet to the torrent client and clears on success', async () => {
    mockAll();
    let body: unknown;
    server.use(
      http.post('/api/clients/torrent/add', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true, ref: 'NEWHASH' });
      }),
    );
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(pill(container, 'torrent')).not.toBeNull());
    fireEvent.click(pill(container, 'torrent'));
    // the input folds behind a reveal button since the redesign
    await waitFor(() => expect(container.querySelector('.adl-client-add-toggle')).not.toBeNull());
    fireEvent.click(container.querySelector('.adl-client-add-toggle') as HTMLElement);
    await waitFor(() => expect(container.querySelector('.adl-client-add-input')).not.toBeNull());
    const input = container.querySelector('.adl-client-add-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'magnet:?xt=urn:btih:abc' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(body).toBeTruthy());
    expect(body).toEqual({ url: 'magnet:?xt=urn:btih:abc' });
    // success folds the box back to its toggle
    await waitFor(() => expect(container.querySelector('.adl-client-add-toggle')).not.toBeNull());
    expect(container.querySelector('.adl-client-add-input')).toBeNull();
    expect(toasts[0]).toBe('Sent to the torrent client');
  });
});

describe('slskd extras', () => {
  it('uploads view lists who is pulling from this install, read-only', async () => {
    mockAll({ slskd: SLSKD_WITH_UPLOADS });
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('song.flac'));
    const upSwitch = [...container.querySelectorAll('.adl-client-chip')].find((c) =>
      c.textContent?.includes('uploads (1)'),
    );
    fireEvent.click(upSwitch as HTMLElement);
    await waitFor(() => expect(container.textContent).toContain('give.flac'));
    expect(container.textContent).toContain('to leecher9');
    // read-only: no cancel button on upload rows
    expect(
      [...container.querySelectorAll('.verif-act-del')].filter(
        (b) => b.getAttribute('title') === 'Cancel this transfer in slskd',
      ),
    ).toHaveLength(0);
    // the 14k completed uploads the server trimmed are named, not hidden
    expect(container.textContent).toContain('14000 completed trimmed');
  });

  it('clear completed asks slskd and reloads', async () => {
    mockAll({ slskd: SLSKD_WITH_UPLOADS });
    const hit = vi.fn();
    server.use(
      http.post('/api/clients/slskd/clear-completed', () => {
        hit();
        return HttpResponse.json({ success: true });
      }),
    );
    const { container } = render(<AdlClientsTab />);
    await waitFor(() => expect(container.textContent).toContain('song.flac'));
    const clearBtn = [...container.querySelectorAll('button')].find(
      (b) => b.textContent === '🧹 Clear completed',
    );
    fireEvent.click(clearBtn as HTMLElement);
    await waitFor(() => expect(hit).toHaveBeenCalled());
    expect(toasts[0]).toBe('Clear completed ok');
  });
});
