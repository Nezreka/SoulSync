import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { extractFunction } from '../src/test/vanilla-extract';

test.use({hasTouch: true});

const read = (name: string) => readFileSync(`static/${name}`, 'utf8');
const downloads = read('downloads.js');
const start = downloads.indexOf('modal.innerHTML = `', downloads.indexOf('// Use the exact same modal HTML'));
const template = downloads.slice(start + 'modal.innerHTML = '.length, downloads.indexOf('\n    `;', start) + 6);
const helpers = extractFunction('generateDownloadModalHeroSection', read('sync-spotify.js')) +
  ['downloadModalQualityProfileSelectHtml', 'downloadModalQualityProfileSelectId', 'acquisitionQualityProfileSelectHtml']
    .map(name => extractFunction(name, read('shared-helpers.js'))).join('\n');
const lock = extractFunction('installDownloadModalScrollLock', downloads);

for (const viewport of [{width: 393, height: 852}, {width: 320, height: 568},
                        {width: 852, height: 393}, {width: 1440, height: 900}]) {
  test(`download modal rows and controls remain usable at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.setContent('<div style="height:3000px">Background</div><div class="download-missing-modal" style="display:flex"></div>');
    await page.addStyleTag({content: read('style.css') + '\n' + read('mobile.css')});
    await page.evaluate(({template, helpers, lock}) => {
      const render = new Function('type', `
        const escapeHtml = value => String(value), escapeForInlineJs = escapeHtml;
        const virtualPlaylistId = 'fixture', isDiscoverAlbum = false;
        const spotifyTracks = Array.from({length: 40}, (_, i) => ({name: 'Track ' + (i + 1), artists: ['Artist'], duration_ms: 180000}));
        const heroContext = {type, playlistId: virtualPlaylistId, trackCount: 40,
          playlist: {name: 'A playlist with a long title', owner: 'Listener'},
          album: {name: 'Album'}, artist: {name: 'Artist'}};
        const formatArtists = artists => artists.join(', '), formatDuration = () => '3:00', renderModalTrackPlayButton = () => '';
        ${helpers}
        return ${template};
      `);
      (window as any).renderFixture = render;
      document.querySelector('.download-missing-modal')!.innerHTML = render('playlist');
      new Function(`${lock}; installDownloadModalScrollLock();`)();
    }, {template, helpers, lock});
    for (const type of ['playlist', 'album', 'artist_album', 'wishlist']) {
      await page.evaluate(type => {
        document.querySelector('.download-missing-modal')!.innerHTML = (window as any).renderFixture(type);
      }, type);
      const table = page.locator('.download-tracks-table-container');
      expect((await table.boundingBox())!.height).toBeGreaterThanOrEqual(150);
      const first = page.locator('.track-select-cb').first();
      await first.scrollIntoViewIfNeeded();
      await first.tap();
      await expect(first).not.toBeChecked();
      const last = page.locator('.track-select-cb').last();
      await last.scrollIntoViewIfNeeded();
      await last.tap();
      await expect(last).not.toBeChecked();
      await page.getByRole('button', {name: 'Begin Analysis', exact: true}).scrollIntoViewIfNeeded();
      await expect(page.getByRole('button', {name: 'Begin Analysis', exact: true})).toBeInViewport();
      await page.getByRole('button', {name: 'Close', exact: true}).scrollIntoViewIfNeeded();
      await expect(page.getByRole('button', {name: 'Close', exact: true})).toBeInViewport();
    }
    await expect(page.locator('html')).toHaveClass(/download-modal-open/);
    expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).toBe('hidden');
    await page.evaluate(() => {
      const second = document.querySelector('.download-missing-modal')!.cloneNode(true) as HTMLElement;
      second.id = 'second'; document.body.append(second);
      (document.querySelector('.download-missing-modal') as HTMLElement).style.display = 'none';
    });
    await expect(page.locator('html')).toHaveClass(/download-modal-open/);
    await page.locator('#second').evaluate(node => node.remove());
    await expect(page.locator('html')).not.toHaveClass(/download-modal-open/);
    await page.locator('.download-missing-modal').evaluate((node: HTMLElement) => node.style.display = 'flex');
    await expect(page.locator('html')).toHaveClass(/download-modal-open/);
    await page.locator('.download-missing-modal').evaluate(node => node.remove());
    await expect(page.locator('html')).not.toHaveClass(/download-modal-open/);
  });
}
