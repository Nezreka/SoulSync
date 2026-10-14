import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

const source = readFileSync(resolve(process.cwd(), 'static/pages-extra.js'), 'utf8');

async function render(
  match: { catalogue_id: number; server_id: string | null } | null,
  downloadStatus = 'wishlist',
) {
  const fetch = vi.fn(async () => ({
    json: async () => ({
      success: true,
      entry: {
        playlist_name: 'Verträumt',
        total_tracks: 1,
        tracks_found: 0,
        current_tracks_in_library: match ? 1 : 0,
        current_tracks_recognized: match?.server_id ? 1 : 0,
        track_results: [
          {
            name: 'One Species',
            artist: 'Melodysheep',
            status: 'not_found',
            source_track_id: 'sp-one',
            download_status: downloadStatus,
            current_library_match: match,
          },
        ],
      },
    }),
  }));
  const toast = vi.fn();
  // oxlint-disable-next-line typescript-eslint/no-implied-eval -- Execute the actual legacy modal.
  const open = new Function(
    'fetch',
    'showToast',
    'document',
    `
    const showLoadingOverlay=()=>{}, hideLoadingOverlay=()=>{}, requestAnimationFrame=fn=>fn();
    const _esc=s=>s;
    ${extractFunction('openSyncDetailModal', source)}
    return openSyncDetailModal;
  `,
  )(fetch, toast, document) as (id: number) => Promise<void>;
  await open(1);
  expect(toast).not.toHaveBeenCalled();
  return document.querySelector('.sync-detail-row');
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('sync details show the current library after a historic miss', () => {
  it('shows a now-recognized recording as matched without offering another download', async () => {
    const row = await render({ catalogue_id: 1692, server_id: 'nav-song' });
    expect(row).toHaveClass('matched');
    expect(row?.textContent).toContain('In library');
    expect(row?.querySelector('.sync-dl-wishlist-btn')).toBeNull();
    expect(document.body.textContent).toContain('Matched (1)');
    expect(document.body.textContent).toContain('Unmatched (0)');
  });
  it('separates owned files waiting for a scan from missing downloads', async () => {
    const row = await render({ catalogue_id: 1692, server_id: null });
    expect(row).toHaveClass('owned');
    expect(row).not.toHaveClass('unmatched');
    expect(row?.textContent).toContain('Awaiting server scan');
    expect(row?.querySelector('.sync-dl-wishlist-btn')).toBeNull();
  });
  it('keeps a currently missing song in unmatched', async () => {
    const row = await render(null);
    expect(row).toHaveClass('unmatched');
    expect(document.body.textContent).toContain('Unmatched (1)');
  });
  it('keeps downloaded filtering after replacing the old download icon', async () => {
    const row = await render({ catalogue_id: 1692, server_id: 'nav-song' }, 'completed');
    // oxlint-disable-next-line typescript-eslint/no-implied-eval -- Execute the actual legacy filter.
    const filter = new Function(
      'document',
      `${extractFunction('_syncDetailFilter', source)}; return _syncDetailFilter;`,
    )(document) as (button: Element, value: string) => void;
    filter(document.querySelector('[data-filter="downloaded"]')!, 'downloaded');
    expect((row as HTMLElement).style.display).not.toBe('none');
  });
});
