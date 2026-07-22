import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

interface ArtistListResponse {
  success: boolean;
  artists: Array<{ id: number; name: string }>;
}

interface ArtistDetailResponse {
  success: boolean;
  artist: {
    name: string;
    albums: Array<{ id: number; title: string }>;
    eps: Array<{ id: number; title: string }>;
    singles: Array<{ id: number; title: string }>;
  };
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function selectAdmin(request: APIRequestContext, baseURL: string) {
  const response = await request.post(new URL('/api/profiles/select', baseURL).toString(), {
    data: { profile_id: 1 },
  });
  expect(response.ok()).toBe(true);
}

async function closeDialog(page: Page, heading: string | RegExp) {
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: heading })).toBeVisible();
  await dialog.getByTitle('Close').click();
  await expect(dialog).toBeHidden();
}

async function dismissFirstRunSetup(page: Page) {
  const skip = page.getByRole('button', { name: 'Skip Setup' });
  const library = page.getByRole('heading', { name: 'Library' });
  await Promise.race([
    skip.waitFor({ state: 'visible', timeout: 10_000 }),
    library.waitFor({ state: 'visible', timeout: 10_000 }),
  ]);
  if (await skip.isVisible()) {
    await skip.click();
    await expect(skip).toBeHidden();
  }
}

test('Library v2 Phase C/D actions open their real Docker UI flows', async ({
  page,
  request,
  baseURL,
}) => {
  if (!baseURL) test.skip();
  await selectAdmin(request, baseURL!);

  const artistsResponse = await request.get(
    new URL('/api/library/v2/artists?page=1&sort=name&monitored=all', baseURL!).toString(),
  );
  expect(artistsResponse.ok()).toBe(true);
  const artists = (await artistsResponse.json()) as ArtistListResponse;
  expect(artists.success).toBe(true);
  expect(artists.artists.length).toBeGreaterThan(0);
  const firstArtist = artists.artists[0];

  const detailResponse = await request.get(
    new URL(`/api/library/v2/artists/${firstArtist.id}`, baseURL!).toString(),
  );
  expect(detailResponse.ok()).toBe(true);
  const detail = (await detailResponse.json()) as ArtistDetailResponse;
  const firstRelease = [...detail.artist.albums, ...detail.artist.eps, ...detail.artist.singles][0];
  expect(firstRelease).toBeTruthy();

  await page.goto(new URL('/library-v2', baseURL!).toString(), { waitUntil: 'domcontentloaded' });
  await dismissFirstRunSetup(page);
  await expect(page.getByRole('heading', { name: 'Library' })).toBeVisible();
  await page
    .getByRole('button', { name: new RegExp(escapeRegExp(firstArtist.name), 'i') })
    .first()
    .click();
  await expect(page).toHaveURL(new RegExp(`artist=${firstArtist.id}`));
  await expect(page.getByRole('heading', { name: firstArtist.name })).toBeVisible();

  await page.getByRole('button', { name: 'Preview Retag', exact: true }).first().click();
  await closeDialog(page, new RegExp(`Preview Retag.*${escapeRegExp(firstArtist.name)}`, 'i'));

  await page.getByRole('button', { name: 'Maintenance', exact: true }).click();
  const maintenance = page.getByRole('dialog');
  await expect(maintenance.getByRole('heading', { name: 'Maintenance' })).toBeVisible();
  await expect(
    maintenance.getByText(/legacy catalog|legacy \+ files|Library v2/i).first(),
  ).toBeVisible();
  await maintenance.getByTitle('Close').click();

  await page.getByRole('button', { name: 'Manage Tracks', exact: true }).click();
  await closeDialog(page, /Manage Tracks/);

  await page.getByRole('button', { name: 'History', exact: true }).click();
  await closeDialog(page, 'History');

  await page.getByRole('button', { name: 'Edit Metadata', exact: true }).click();
  await closeDialog(page, new RegExp(`Edit.*${escapeRegExp(firstArtist.name)}`, 'i'));

  await page.getByRole('button', { name: 'Monitoring', exact: true }).click();
  await closeDialog(page, 'Artist Monitoring');

  await page.getByRole('button', { name: 'Quality Profile', exact: true }).first().click();
  await closeDialog(page, 'Quality Profile');

  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  const deleteDialog = page.getByRole('dialog');
  await expect(deleteDialog).toContainText('Files on disk are not deleted');
  await deleteDialog.getByRole('button', { name: 'Cancel' }).click();

  await page.getByText(firstRelease.title, { exact: true }).first().click();
  await expect(page.getByRole('columnheader', { name: 'File' })).toBeVisible();

  await page.getByTitle('Edit release (correct the album/EP/single type)').first().click();
  await closeDialog(page, new RegExp(`Edit.*${escapeRegExp(firstRelease.title)}`, 'i'));

  await page.getByTitle('Remove album from library (files stay on disk)').first().click();
  const albumDeleteDialog = page.getByRole('dialog');
  await expect(albumDeleteDialog.getByRole('heading', { name: 'Delete Album' })).toBeVisible();
  await albumDeleteDialog.getByRole('button', { name: 'Cancel' }).click();

  await page.getByTitle('Open album detail').first().click();
  await expect(page).toHaveURL(new RegExp(`album=${firstRelease.id}`));
  await expect(page.getByRole('heading', { name: firstRelease.title })).toBeVisible();
  await page.getByTitle('Edit metadata').click();
  await closeDialog(page, new RegExp(`Edit.*${escapeRegExp(firstRelease.title)}`, 'i'));

  await page
    .getByRole('button', { name: new RegExp(`←.*${escapeRegExp(firstArtist.name)}`, 'i') })
    .click();
  await expect(page.getByRole('heading', { name: firstArtist.name })).toBeVisible();

  await page.getByRole('button', { name: 'Interactive Search', exact: true }).first().click();
  await closeDialog(page, 'Interactive Search');

  await page.getByRole('button', { name: 'Manual Import', exact: true }).click();
  await expect(page).toHaveURL(/\/import\/album$/);
  await expect(page.getByRole('heading', { name: 'Import Music' })).toBeVisible();
});

test('Library v2 section navigation reaches the shared playlist integration', async ({
  page,
  request,
  baseURL,
}) => {
  if (!baseURL) test.skip();
  await selectAdmin(request, baseURL!);
  await page.goto(new URL('/library-v2', baseURL!).toString(), { waitUntil: 'domcontentloaded' });
  await dismissFirstRunSetup(page);
  await page.getByRole('button', { name: 'Playlists', exact: true }).click();
  await expect(page).toHaveURL(/section=playlists/);
  await expect(page.getByPlaceholder('Filter playlists…')).toBeVisible();
  await expect(
    page.getByText(/mirrored playlists|No mirrored playlists yet/).first(),
  ).toBeVisible();
});
