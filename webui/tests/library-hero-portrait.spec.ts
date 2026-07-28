import { expect, test, type APIRequestContext } from '@playwright/test';

interface ArtistListResponse {
  success: boolean;
  artists: Array<{ id: number; name: string }>;
}

async function selectAdmin(request: APIRequestContext, baseURL: string) {
  const response = await request.post(new URL('/api/profiles/select', baseURL).toString(), {
    data: { profile_id: 1 },
  });
  expect(response.ok()).toBe(true);
}

/** A 64x64 PNG — small enough that an intrinsically-sized column would collapse
 *  to its floor. Inline so the test never depends on a provider's art. */
const TINY_PNG =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAACklEQVR4nGNgAAAAAgAB5SfeAAAAAElFTkSuQmCC';

/** The rich artist header's portrait must be sized by the layout, not by the
 *  photo. `.artist-image-container` is a `flex-shrink: 0` item, so without a
 *  width of its own it takes its content's intrinsic width — and a percentage
 *  width on the `<img>` contributes nothing to intrinsic sizing. The header
 *  therefore changed shape from artist to artist depending on what pixel size
 *  the provider happened to ship, which is what users saw on installations
 *  whose cached art is smaller than the column.
 */
test('the artist hero portrait keeps its size when the photo does not', async ({
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
  expect(artists.artists.length).toBeGreaterThan(0);

  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(
    new URL(`/library?artist=${artists.artists[0].id}&header=rich`, baseURL!).toString(),
    { waitUntil: 'domcontentloaded' },
  );

  const portrait = page.locator('.artist-hero-section .artist-image').first();
  await expect(portrait).toBeVisible();
  const before = (await portrait.boundingBox())!.width;
  expect(before).toBeGreaterThan(300);

  const swapped = await portrait.evaluate(
    (img, tiny) =>
      new Promise<{ natural: number }>((resolve) => {
        const el = img as HTMLImageElement;
        el.addEventListener('load', () => resolve({ natural: el.naturalWidth }), { once: true });
        el.src = tiny;
      }),
    TINY_PNG,
  );
  expect(swapped.natural).toBe(64);

  const after = (await portrait.boundingBox())!.width;
  expect(Math.round(after)).toBe(Math.round(before));
});
