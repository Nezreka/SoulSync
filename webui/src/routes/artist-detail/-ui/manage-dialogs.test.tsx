import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ArtPicker, ART_CUSTOM_URL_DEBOUNCE_MS } from './art-picker';
import { SmartDeleteDialog, ALBUM_DELETE_COPY, TRACK_DELETE_COPY } from './smart-delete-dialog';
import { SourceInfoPopover } from './source-info-popover';

/**
 * Wave 1 of the library.js Enhanced-action port: the delete dialogs, the
 * source-info popover, and the art picker. These pin the choice values the
 * callers act on and the states a live user hits (empty sources, dead images,
 * the blacklist confirm), not the styling.
 */

const noop = () => {};

afterEach(() => vi.unstubAllGlobals());

describe('SmartDeleteDialog', () => {
  it('track copy offers db_only and delete_file', () => {
    const chosen: string[] = [];
    render(
      <SmartDeleteDialog
        copy={TRACK_DELETE_COPY}
        onChoose={(c) => chosen.push(c)}
        onClose={noop}
      />,
    );
    screen.getByText('Remove from Library').click();
    screen.getByText('Delete File Too').click();
    expect(chosen).toEqual(['db_only', 'delete_file']);
  });

  it('album copy offers delete_files and closes on Escape', () => {
    const chosen: string[] = [];
    let closed = 0;
    render(
      <SmartDeleteDialog
        copy={ALBUM_DELETE_COPY}
        onChoose={(c) => chosen.push(c)}
        onClose={() => {
          closed += 1;
        }}
      />,
    );
    screen.getByText('Delete Files Too').click();
    expect(chosen).toEqual(['delete_files']);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(closed).toBe(1);
  });
});

describe('SourceInfoPopover', () => {
  it('renders the most recent download and blacklists after the confirm', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('source-info')) {
          return new Response(
            JSON.stringify({
              success: true,
              downloads: [
                {
                  source_service: 'soulseek',
                  source_username: 'peer42',
                  source_filename: 'dir/01 Song.flac',
                  status: 'completed',
                },
              ],
            }),
          );
        }
        return new Response(JSON.stringify({ success: true }));
      }),
    );
    const confirm = vi.fn(async (_opts: { title: string; message: string }) => true);
    const toast = vi.fn();
    window.showConfirmDialog = confirm as never;
    window.showToast = toast as never;
    try {
      render(<SourceInfoPopover trackId={1} trackTitle="Song" anchor={null} onClose={noop} />);
      await screen.findByText('🔍 Soulseek');
      expect(screen.getByText('peer42')).toBeTruthy();

      screen.getByText('⛔ Blacklist This Source').click();
      await waitFor(() => expect(confirm).toHaveBeenCalled());
      // The vanilla's confirm names the file and the peer (3310).
      expect(String(confirm.mock.calls[0]?.[0]?.message)).toContain('"01 Song.flac" from peer42');
      await screen.findByText('⛔ Blacklisted');
      expect(toast).toHaveBeenCalledWith('Source blacklisted', 'success');
    } finally {
      delete window.showConfirmDialog;
      delete window.showToast;
    }
  });

  it('says so when source tracking has nothing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ success: true, downloads: [] }))),
    );
    render(<SourceInfoPopover trackId={1} trackTitle="Song" anchor={null} onClose={noop} />);
    await screen.findByText(/Source tracking starts with new downloads/);
  });
});

describe('ArtPicker', () => {
  it('debounces the custom URL at the vanilla interval (1812)', () => {
    expect(ART_CUSTOM_URL_DEBOUNCE_MS).toBe(350);
  });

  it('selecting a tile arms Apply; applying reports the artist side-effects and hands back the url', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('art-options')) {
          return new Response(
            JSON.stringify({ candidates: [{ url: 'https://img/1.jpg', source: 'spotify' }] }),
          );
        }
        return new Response(
          JSON.stringify({ success: true, server_updated: true, disk_written: true }),
        );
      }),
    );
    const toast = vi.fn();
    window.showToast = toast as never;
    const applied: string[] = [];
    try {
      render(
        <ArtPicker
          target={{ kind: 'artist', id: 9 }}
          currentUrl="https://img/current.jpg"
          subtitle="Artist · applies everywhere"
          onApplied={(u) => applied.push(u)}
          onClose={noop}
        />,
      );
      await screen.findByText('spotify');
      // The current photo leads the grid, display-only (1907-1919).
      expect(screen.getByText('current')).toBeTruthy();

      const apply = screen.getByText('Apply') as HTMLButtonElement;
      expect(apply.disabled).toBe(true);
      screen.getByText('spotify').closest('button')!.click();
      await waitFor(() =>
        expect((screen.getByText('Apply') as HTMLButtonElement).disabled).toBe(false),
      );
      screen.getByText('Apply').click();
      await waitFor(() => expect(applied).toEqual(['https://img/1.jpg']));
      expect(toast).toHaveBeenCalledWith(
        'Artist photo updated (also updated: server, artist.jpg)',
        'success',
      );
    } finally {
      delete window.showToast;
    }
  });

  it('a grid whose images all die says so instead of sitting blank', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ candidates: [{ url: 'https://img/dead.jpg', source: 'deezer' }] }),
          ),
      ),
    );
    const { container } = render(
      <ArtPicker target={{ kind: 'album', id: 5 }} subtitle="s" onApplied={noop} onClose={noop} />,
    );
    await screen.findByText('deezer');
    fireEvent.error(container.querySelector('.art-picker-tile img')!);
    await screen.findByText(/none of the images would load/);
  });
});
