import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

// #2022: an album opened from an artist's page preselected the app default,
// not the profile the artist is set to.
const source = readFileSync(resolve(process.cwd(), 'static/shared-helpers.js'), 'utf8');
const body = extractFunction('hydrateArtistAlbumQualityProfile', source);

function load(response: unknown) {
  const fetchMock = vi.fn((_url: string) =>
    response instanceof Error
      ? Promise.reject(response)
      : Promise.resolve({ json: () => Promise.resolve(response) }),
  );
  const preselect = vi.fn();
  const plain = vi.fn();
  const fn = new Function(
    'fetch',
    'setDownloadModalQualityProfile',
    'hydrateDownloadModalQualityProfileSelect',
    `${body}; return hydrateArtistAlbumQualityProfile;`,
  )(fetchMock, preselect, plain);
  return { fn, fetchMock, preselect, plain };
}

it("preselects the artist's profile", async () => {
  const { fn, fetchMock, preselect, plain } = load({ success: true, quality_profile_id: 4 });
  await fn('artist_album_sp1_a1', { id: 'sp1', name: 'Foxx', source: 'spotify' }, { id: 'a1' });
  expect(preselect).toHaveBeenCalledWith('artist_album_sp1_a1', 4);
  expect(plain).not.toHaveBeenCalled();
  const url = String(fetchMock.mock.calls[0][0]);
  expect(url).toContain('/api/artist/sp1/quality-profile?');
  expect(url).toContain('name=Foxx');
  expect(url).toContain('source=spotify');
  expect(url).toContain('album_id=a1');
});

it('keeps the default when the artist has none', async () => {
  const { fn, preselect, plain } = load({ success: true, quality_profile_id: null });
  await fn('v', { id: 'sp1', name: 'Foxx' }, { id: 'a1' });
  expect(preselect).not.toHaveBeenCalled();
  expect(plain).toHaveBeenCalledWith('v');
});

it('keeps the default when the lookup fails', async () => {
  const { fn, preselect, plain } = load(new Error('offline'));
  await fn('v', { id: 'sp1', name: 'Foxx' }, null);
  expect(preselect).not.toHaveBeenCalled();
  expect(plain).toHaveBeenCalledWith('v');
});

it('the artist album modal uses it', () => {
  const open = extractFunction('openDownloadMissingModalForArtistAlbum', source);
  expect(open).toContain('hydrateArtistAlbumQualityProfile(virtualPlaylistId, artist, album)');
});
