import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #1910, the playlist download modal (cremonies):
 * - it sat on "Pending" for every track until Begin Analysis, because only
 *   the artist-album opener asked the library on open
 * - a mirror's modal said "by YouTube"
 * - Add to Wishlist queued every selected song, owned or not
 */

const DOWNLOADS = readFileSync(resolve(process.cwd(), 'static/downloads.js'), 'utf8');
const SERVICES = readFileSync(resolve(process.cwd(), 'static/sync-services.js'), 'utf8');
const WISHLIST = readFileSync(resolve(process.cwd(), 'static/wishlist-tools.js'), 'utf8');

describe('the playlist openers ask the library on open', () => {
  it('both the youtube-style and the generic opener run the preview', () => {
    expect(extractFunction('openDownloadMissingModalForYouTube', DOWNLOADS)).toContain(
      'previewModalOwnership(virtualPlaylistId)',
    );
    expect(extractFunction('openDownloadMissingModalForTidal', SERVICES)).toContain(
      'previewModalOwnership(virtualPlaylistId)',
    );
  });

  it('a mirror is not a youtube playlist', () => {
    const open = extractFunction('openDownloadMissingModalForYouTube', DOWNLOADS);
    const mirror = open.indexOf("startsWith('youtube_mirrored_') ? 'Playlist'");
    expect(mirror).toBeGreaterThan(-1);
    // checked before the catch-all YouTube
    expect(mirror).toBeLessThan(open.lastIndexOf("'YouTube';"));
  });

  it('the preview remembers which tracks are owned', () => {
    expect(extractFunction('previewModalOwnership', DOWNLOADS)).toContain('process.ownedIndices');
  });
});

type Proc = {
  status: string;
  tracks: Array<{ id: string; name: string; artists: string[] }>;
  ownedIndices?: Set<number>;
};

function loadAdd(processes: Record<string, Proc>, preview: (id: string) => Promise<void>) {
  const toast = vi.fn();
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const add = new Function(
    'activeDownloadProcesses',
    'showToast',
    'previewModalOwnership',
    'checkMusicRequestQuota',
    'getDownloadModalQualityProfileId',
    'closeDownloadMissingModal',
    'wishlistAddLabel',
    'announceWishlistRequest',
    '_withLockedAlbumType',
    [
      extractFunction('addModalTracksToWishlist', WISHLIST),
      'return addModalTracksToWishlist;',
    ].join('\n'),
  )(
    processes,
    toast,
    preview,
    async () => true,
    () => null,
    async () => {},
    () => 'Add to Wishlist',
    () => false,
    (a: unknown) => a,
  ) as (id: string) => Promise<void>;
  return { add, toast };
}

function postedNames(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.map((c) => JSON.parse(c[1].body).track.name);
}

const ID = 'youtube_mirrored_3';
const tracks = ['Owned', 'Missing A', 'Missing B'].map((name, i) => ({
  id: `dz${i}`,
  name,
  artists: ['Artist'],
}));

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('Add to Wishlist skips what you own', () => {
  it('leaves the owned track off, and says how many it skipped', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ success: true }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { add, toast } = loadAdd(
      { [ID]: { status: 'idle', tracks, ownedIndices: new Set([0]) } },
      async () => {},
    );
    await add(ID);
    expect(postedNames(fetchMock)).toEqual(['Missing A', 'Missing B']);
    expect(toast).toHaveBeenCalledWith('Added 2 tracks to wishlist (1 already owned)', 'success');
  });

  it('asks the library first when the preview has not answered yet', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ success: true }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const proc: Proc = { status: 'idle', tracks };
    const preview = vi.fn(async () => {
      proc.ownedIndices = new Set([1, 2]);
    });
    const { add } = loadAdd({ [ID]: proc }, preview);
    await add(ID);
    expect(preview).toHaveBeenCalledWith(ID);
    expect(postedNames(fetchMock)).toEqual(['Owned']);
  });

  it('Force Download All adds everything, owned or not', async () => {
    document.body.innerHTML = `<input type="checkbox" id="force-download-all-${ID}" checked>`;
    const fetchMock = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ success: true }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { add } = loadAdd(
      { [ID]: { status: 'idle', tracks, ownedIndices: new Set([0, 1, 2]) } },
      async () => {},
    );
    await add(ID);
    expect(postedNames(fetchMock)).toEqual(['Owned', 'Missing A', 'Missing B']);
  });

  it('a backend skip counts as owned, not as added', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ success: true, skipped: true }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { add, toast } = loadAdd(
      { [ID]: { status: 'idle', tracks, ownedIndices: new Set() } },
      async () => {},
    );
    await add(ID);
    expect(toast).toHaveBeenCalledWith('All 3 tracks already in your library', 'success');
  });

  it('nothing to add when every selected song is owned, and nothing is posted', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { add, toast } = loadAdd(
      { [ID]: { status: 'idle', tracks, ownedIndices: new Set([0, 1, 2]) } },
      async () => {},
    );
    await add(ID);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(
      'All 3 selected tracks are already in your library',
      'success',
    );
  });
});
