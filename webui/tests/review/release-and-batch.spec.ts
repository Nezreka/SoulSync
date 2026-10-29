import { expect, test } from '@playwright/test';
import { http, HttpResponse } from 'msw';
import { reviewFixture } from '../fixtures/review';
import { installShell, ORIGIN } from '../visual/harness';

for (const width of [390, 1800]) {
  test(`${width}px unidentified album uses local file inventory`, async ({page}, info) => {
    await page.setViewportSize({width, height: 1000});
    await installShell(page, reviewFixture(false, review => {
      review.album_plan!.state = 'unidentified';
      review.album_plan!.source = '';
      review.album_plan!.release_id = '';
      review.album_plan!.candidates = [];
      review.album_plan!.assignments[0] = {status:'uncertain', file_path:'/music/Glass/01 - Études.mp3', file:'01 - Études.mp3', current_title:'Études, No. 5', file_duration_ms:190000, acoustid:{status:'no_match'}};
      review.items[0].finding_type = 'reconciliation_unresolved';
      review.items[0].file_path = null;
      review.items[0].details = {attempted_releases: [], checked_files: [{file:'01 - Études.mp3', file_path:'/music/Glass/01 - Études.mp3', audio_status:'no_match'}]};
    }));
    await page.goto(`${ORIGIN}/review?mode=all`);
    const panel = page.getByRole('region', {name:'Release not identified'});
    await expect(panel).toContainText('1 local file found');
    await expect(panel.getByRole('button',{name:/Play local file/})).toBeVisible();
    await expect(page.getByText('No track assignment data is available for this album.')).toHaveCount(0);
    expect(await panel.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({path: info.outputPath('unidentified.png')});
    await panel.getByRole('button',{name:'Identify release…'}).click();
    await expect(page.getByRole('textbox',{name:'MusicBrainz release MBID or URL'})).toBeVisible();
  });

  test(`${width}px multi-album search preserves explicit selection and offers folder review`, async ({page}, info) => {
    await page.setViewportSize({width, height:1000});
    await installShell(page, [http.get(`${ORIGIN}/api/repair/review-queue/apply-ready`, () => HttpResponse.json({
      signature:'october-batch', action_count:2, file_actions:0, albums:[
        {case_id:'album:1:fixture', album:'Northbound', artist:'First artist', actions:[{id:1,description:'Update metadata'}]},
        {case_id:'album:2:fixture', album:'Southbound', artist:'Second artist', actions:[{id:2,description:'Update metadata'}]},
      ]
    })), ...reviewFixture(true)]);
    await page.goto(`${ORIGIN}/review?mode=all`);
    await page.getByRole('button', {name:/^Apply ready changes/}).first().click();
    const dialog = page.getByRole('dialog', {name:'Apply ready album plans'});
    await dialog.getByRole('textbox', {name:'Search ready albums'}).fill('Second artist');
    await expect(dialog).toContainText('2 of 2 albums selected');
    await expect(dialog.getByRole('checkbox',{name:/Northbound/})).toHaveCount(0);
    await dialog.getByRole('button',{name:'Select only shown albums'}).click();
    await expect(dialog).toContainText('1 of 2 albums selected');
    await dialog.locator('summary').filter({hasText:'Southbound'}).click();
    await expect(dialog.getByRole('button',{name:'Review folder changes'})).toBeVisible();
    expect(await dialog.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({path:info.outputPath('batch-selection.png')});
  });
}
