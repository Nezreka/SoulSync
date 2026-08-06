/**
 * The server tab's list + disambiguation, against pages-extra.js 12-243.
 * Classes and copy are asserted as literals — they are the vanilla's.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ServerDisambigModal, ServerPlaylistList } from './server-playlist-list';

let responder: (url: string) => unknown = () => ({});

function stubFetch(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => new Response(JSON.stringify(responder(url)))),
  );
}

const SERVER = {
  success: true,
  server_type: 'plex',
  playlists: [
    { id: '1', name: 'Road Trip', track_count: 40 },
    { id: '2', name: 'Deep Cuts', track_count: 12 },
  ],
};

beforeEach(() => {
  stubFetch();
  vi.stubGlobal('showToast', vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ServerPlaylistList', () => {
  it('splits into the two sections with the vanilla headers and counts', async () => {
    responder = (url) =>
      url === '/api/server/playlists'
        ? SERVER
        : url === '/api/mirrored-playlists'
          ? [{ name: 'Road Trip' }]
          : [];
    render(<ServerPlaylistList onOpenCompare={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    expect(screen.getByText('Synced Playlists')).toBeInTheDocument();
    expect(screen.getByText('Other Server Playlists')).toBeInTheDocument();
    expect(document.querySelectorAll('.server-pl-section')).toHaveLength(2);
    expect(document.querySelector('.server-pl-section-unsynced')).not.toBeNull();
    // The synced card gets the badge and the 'Open Editor' action.
    expect(screen.getByText('Synced')).toBeInTheDocument();
    expect(screen.getByText('Open Editor')).toBeInTheDocument();
    expect(screen.getByText('View Tracks')).toBeInTheDocument();
    expect(document.querySelector('.server-pl-unsynced')).not.toBeNull();
    // The title takes the server type (76-78).
    expect(document.querySelector('#server-tab-title')?.textContent).toBe(
      'Server Playlists (Plex)',
    );
  });

  it('numbers the Other grid AFTER the synced one, so hues never restart (145)', async () => {
    responder = (url) =>
      url === '/api/server/playlists'
        ? SERVER
        : url === '/api/mirrored-playlists'
          ? [{ name: 'Road Trip' }]
          : [];
    render(<ServerPlaylistList onOpenCompare={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Deep Cuts')).toBeInTheDocument());
    const cards = document.querySelectorAll('.server-pl-card');
    expect((cards[0] as HTMLElement).style.getPropertyValue('--card-hue')).toBe('200');
    // index 1, not 0 — the second section continues the count.
    expect((cards[1] as HTMLElement).style.getPropertyValue('--card-hue')).toBe('237');
  });

  it('renders the placeholder the backend sent, not a generic one (54-56)', async () => {
    responder = (url) =>
      url === '/api/server/playlists' ? { success: false, error: 'Plex unreachable' } : [];
    render(<ServerPlaylistList onOpenCompare={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Plex unreachable')).toBeInTheDocument());
  });

  it('has its own copy for an empty server (80-82)', async () => {
    responder = (url) => (url === '/api/server/playlists' ? { success: true, playlists: [] } : []);
    render(<ServerPlaylistList onOpenCompare={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByText('No playlists found on your media server.')).toBeInTheDocument(),
    );
  });

  it('one mirrored match opens compare directly (172-174)', async () => {
    const onOpenCompare = vi.fn();
    responder = (url) =>
      url === '/api/server/playlists'
        ? SERVER
        : url === '/api/mirrored-playlists'
          ? [{ id: 9, name: 'Road Trip', source: 'spotify' }]
          : [];
    render(<ServerPlaylistList onOpenCompare={onOpenCompare} />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(onOpenCompare).toHaveBeenCalled());
    expect(onOpenCompare.mock.calls[0][1]).toMatchObject({ id: 9 });
  });

  it('no mirrored match opens the server-only view with null (175-177)', async () => {
    const onOpenCompare = vi.fn();
    responder = (url) => (url === '/api/server/playlists' ? SERVER : []);
    render(<ServerPlaylistList onOpenCompare={onOpenCompare} />);
    await waitFor(() => expect(screen.getByText('Deep Cuts')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Deep Cuts'));
    await waitFor(() => expect(onOpenCompare).toHaveBeenCalled());
    expect(onOpenCompare.mock.calls[0][1]).toBeNull();
  });

  it('several matches disambiguate, then RE-FETCH the pick (185-243)', async () => {
    const onOpenCompare = vi.fn();
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        urls.push(url);
        if (url === '/api/server/playlists') return new Response(JSON.stringify(SERVER));
        if (url === '/api/mirrored-playlists') {
          return new Response(
            JSON.stringify([
              { id: 9, name: 'Road Trip', source: 'spotify', track_count: 40, owner: 'boulder' },
              { id: 10, name: 'Road Trip', source: 'tidal', track_count: 38 },
            ]),
          );
        }
        if (url === '/api/mirrored-playlists/10') {
          return new Response(JSON.stringify({ id: 10, name: 'Road Trip', source: 'tidal' }));
        }
        return new Response(JSON.stringify([]));
      }),
    );
    render(<ServerPlaylistList onOpenCompare={onOpenCompare} />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Road Trip'));

    await waitFor(() => expect(document.querySelector('#server-disambig-overlay')).not.toBeNull());
    // The vanilla's exact subtitle (191).
    expect(document.querySelector('#server-disambig-subtitle')?.textContent).toBe(
      '“Road Trip” was found on 2 sources. Which one do you want to compare against?',
    );
    // This tab's OWN icon table (193) — spotify is a green circle here.
    expect(document.querySelectorAll('.server-disambig-icon')[0].textContent).toBe('🟢');
    expect(document.querySelectorAll('.server-disambig-icon')[1].textContent).toBe('🌊');
    expect(screen.getByText('by boulder')).toBeInTheDocument();
    expect(screen.getByText('40 tracks')).toBeInTheDocument();

    fireEvent.click(document.querySelectorAll('.server-disambig-card')[1]);
    await waitFor(() => expect(onOpenCompare).toHaveBeenCalled());
    // The pick is re-fetched in full, not passed straight through (237-239).
    expect(urls).toContain('/api/mirrored-playlists/10');
    expect(onOpenCompare.mock.calls[0][1]).toMatchObject({ id: 10, source: 'tidal' });
    expect(document.querySelector('#server-disambig-overlay')).toBeNull();
  });

  it('Escape and the backdrop both close the disambiguation (220-222)', async () => {
    responder = (url) =>
      url === '/api/server/playlists'
        ? SERVER
        : url === '/api/mirrored-playlists'
          ? [
              { id: 9, name: 'Road Trip' },
              { id: 10, name: 'Road Trip' },
            ]
          : [];
    render(<ServerPlaylistList onOpenCompare={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(document.querySelector('#server-disambig-overlay')).not.toBeNull());
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(document.querySelector('#server-disambig-overlay')).toBeNull());

    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(document.querySelector('#server-disambig-overlay')).not.toBeNull());
    // A click INSIDE must not close it.
    fireEvent.click(document.querySelector('.server-disambig-modal') as Element);
    expect(document.querySelector('#server-disambig-overlay')).not.toBeNull();
    fireEvent.click(document.querySelector('#server-disambig-overlay') as Element);
    await waitFor(() => expect(document.querySelector('#server-disambig-overlay')).toBeNull());
  });

  it('keeps the vanilla refresh-button id and refetches', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        calls.push(url);
        return new Response(JSON.stringify(url === '/api/server/playlists' ? SERVER : []));
      }),
    );
    render(<ServerPlaylistList onOpenCompare={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    const before = calls.filter((u) => u === '/api/server/playlists').length;
    expect(document.querySelector('#server-refresh-btn')).not.toBeNull();
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() =>
      expect(calls.filter((u) => u === '/api/server/playlists').length).toBe(before + 1),
    );
  });
});

describe('ServerDisambigModal — direct (185-223)', () => {
  const NOW = Date.UTC(2026, 7, 6, 12, 0, 0);
  const CANDIDATES = [
    {
      id: 9,
      name: 'Road Trip',
      source: 'spotify',
      track_count: 40,
      owner: 'boulder',
      mirrored_at: '2026-08-06T11:00:00Z',
    },
    { id: 10, name: 'Road Trip', source: 'navidrome', track_count: 0 },
  ];

  it('uses ITS icon table, with the clipboard for anything unlisted (193, 196)', () => {
    render(
      <ServerDisambigModal
        playlistName="Road Trip"
        candidates={CANDIDATES}
        now={NOW}
        onClose={vi.fn()}
        onPick={vi.fn()}
      />,
    );
    const icons = document.querySelectorAll('.server-disambig-icon');
    expect(icons[0].textContent).toBe('🟢');
    // navidrome is not in this table — 196's fallback.
    expect(icons[1].textContent).toBe('📋');
  });

  it('renders the details row, omitting the owner when there is none (206)', () => {
    render(
      <ServerDisambigModal
        playlistName="Road Trip"
        candidates={CANDIDATES}
        now={NOW}
        onClose={vi.fn()}
        onPick={vi.fn()}
      />,
    );
    expect(screen.getByText('by boulder')).toBeInTheDocument();
    expect(screen.getAllByText(/by /)).toHaveLength(1);
    // 205: a missing count renders 0, not blank.
    expect(screen.getByText('0 tracks')).toBeInTheDocument();
    expect(screen.getByText('Mirrored 1h ago')).toBeInTheDocument();
  });

  it('hands back the candidate that was clicked', () => {
    const onPick = vi.fn();
    render(
      <ServerDisambigModal
        playlistName="Road Trip"
        candidates={CANDIDATES}
        now={NOW}
        onClose={vi.fn()}
        onPick={onPick}
      />,
    );
    fireEvent.click(document.querySelectorAll('.server-disambig-card')[1]);
    expect(onPick).toHaveBeenCalledWith(CANDIDATES[1]);
  });
});
