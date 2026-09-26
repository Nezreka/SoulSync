import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createShellBridge } from '@/test/shell-bridge';

import type { EnhancedAlbum } from '../-artist-detail.enhanced';

import { stopRedownloadProgress } from '../-artist-detail.redownload';
import { AlbumUpgradeModal, upgradableTracks } from './album-upgrade-modal';
import { EnhancedTrackTable } from './enhanced-track-table';

/** Quality upgrades on the artist page: the track badge and the album list. */

const ALBUM: EnhancedAlbum = {
  id: 7,
  title: 'SAW 85-92',
  upgradable_count: 1,
  tracks: [
    {
      id: 1,
      track_number: 1,
      title: 'Xtal',
      duration: 300_000,
      bitrate: 1000,
      file_path: '/music/Aphex/SAW/01 Xtal.flac',
    },
    {
      id: 2,
      track_number: 2,
      title: 'Tha',
      duration: 570_000,
      bitrate: 128,
      file_path: '/music/Aphex/SAW/02 Tha.mp3',
      quality_upgrade: { finding_id: 9, job_id: 'quality_upgrade', current: 'MP3 128kbps' },
    },
  ],
};

let bodies: { url: string; body: Record<string, unknown> }[] = [];

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  bodies = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : input.toString();
      bodies.push({
        url,
        body: JSON.parse(typeof init?.body === 'string' ? init.body : '{}'),
      });
      if (url.endsWith('search-metadata')) {
        return new Response(
          JSON.stringify({
            success: true,
            best_match: { source: 'itunes' },
            metadata_results: { itunes: [{ name: 'Tha', artist: 'Aphex Twin' }] },
          }),
        );
      }
      if (url.endsWith('search-sources')) {
        return new Response('{"source":"soulseek","candidates":[]}\n{"done":true}\n');
      }
      return new Response(JSON.stringify({ success: true }));
    }),
  );
});

afterEach(() => {
  stopRedownloadProgress();
  cleanup();
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
});

describe('upgradableTracks', () => {
  it('keeps only tracks with a pending quality finding', () => {
    expect(upgradableTracks(ALBUM).map((t) => t.title)).toEqual(['Tha']);
    expect(upgradableTracks({ id: 1 })).toEqual([]);
  });
});

describe('AlbumUpgradeModal', () => {
  it('lists what could be better and opens the redownload modal in upgrade mode', async () => {
    render(
      <AlbumUpgradeModal
        album={ALBUM}
        artistName="Aphex Twin"
        onReload={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText('Could be better')).toBeTruthy();
    expect(screen.getByText('now MP3 128kbps')).toBeTruthy();
    expect(screen.queryByText('Xtal')).toBeNull();

    fireEvent.click(screen.getByText('Find a better copy'));
    await screen.findByText('Upgrade Track');
    fireEvent.click(await screen.findByText('Search Download Sources →'));
    await waitFor(() => expect(bodies.some((b) => b.url.endsWith('search-sources'))).toBe(true));
    const search = bodies.find((b) => b.url.endsWith('search-sources'))!;
    expect(search.url).toContain('/api/library/track/2/');
    expect(search.body.upgrade).toBe(true);
  });

  it('says so when nothing is left to upgrade', () => {
    render(
      <AlbumUpgradeModal
        album={{ id: 1, title: 'Clean', tracks: [] }}
        artistName="x"
        onReload={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText('Nothing on this album needs an upgrade.')).toBeTruthy();
  });

  it('closes on Escape and on the close button', () => {
    const onClose = vi.fn();
    render(<AlbumUpgradeModal album={ALBUM} artistName="x" onReload={vi.fn()} onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(screen.getByLabelText('Close'));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe('the track row badge', () => {
  it('shows only on tracks that could be better, and opens upgrade mode', async () => {
    render(
      <EnhancedTrackTable
        album={ALBUM}
        isAdmin
        artist={{ id: 42, name: 'Aphex Twin' }}
        selected={new Set()}
        onSelectedChange={vi.fn()}
        onTrackEdited={vi.fn()}
        onTrackDeleted={vi.fn()}
        onAlbumPatched={vi.fn()}
        onReload={vi.fn()}
      />,
    );
    const badges = document.querySelectorAll('.enhanced-upgrade-badge');
    expect(badges).toHaveLength(1);
    expect(badges[0].getAttribute('title')).toContain('now MP3 128kbps');
    fireEvent.click(badges[0]);
    await screen.findByText('Upgrade Track');
  });

  it('a normal redownload stays a normal redownload', async () => {
    render(
      <EnhancedTrackTable
        album={{ ...ALBUM, tracks: [ALBUM.tracks![0]] }}
        isAdmin
        artist={{ id: 42, name: 'Aphex Twin' }}
        selected={new Set()}
        onSelectedChange={vi.fn()}
        onTrackEdited={vi.fn()}
        onTrackDeleted={vi.fn()}
        onAlbumPatched={vi.fn()}
        onReload={vi.fn()}
      />,
    );
    expect(document.querySelector('.enhanced-upgrade-badge')).toBeNull();
  });
});
