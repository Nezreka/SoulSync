import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { extractFunction } from '../src/test/vanilla-extract';
const source = readFileSync('static/media-player.js', 'utf8');
const functions = ['renderNpQueue', 'npFocusQueueAction', 'npAnnounceQueue'].map(name => extractFunction(name, source)).join('\n');
for (const width of [320, 390, 1400]) {
  test(`queue controls stay in their row at ${width}px`, async ({page}) => {
    await page.setViewportSize({width, height: 800});
    await page.setContent('<main style="padding:16px"><div id="np-queue-list"></div></main>');
    await page.addStyleTag({content: readFileSync('static/style.css','utf8') + '\n' + readFileSync('static/mobile.css','utf8')});
    await page.evaluate(functions => {
      new Function(`
        let npQueue = [
          {title:'Yuma',artist:'Neon Beach',file_path:'/a',duration:123},
          {title:'A very long title with a featured artist and extended remix',artist:'Artist',playback_status:'missing'},
          {title:'Track without duration',artist:'Artist',file_path:'/c'}
        ], npQueueIndex = -1;
        const npQueueStatusLabel = () => 'Missing', formatTime = () => '2:03',
          npUpdateUpNext=()=>{}, npPersistQueue=()=>{}, playQueueItem=()=>{}, npReorderQueue=()=>{}, removeFromQueue=()=>{},
          npQueueDragStart=()=>{}, npQueueDragOver=()=>{}, npQueueDrop=()=>{}, npQueueDragEnd=()=>{};
        ${functions}; renderNpQueue();
      `)();
    }, functions);
    const rows = await page.locator('.np-queue-item').evaluateAll(rows => rows.map(row => {
      const bounds = row.getBoundingClientRect();
      return {height: bounds.height, actions: [...row.querySelectorAll('.np-queue-item-actions button')].map(button => {
        const box = button.getBoundingClientRect();
        return {inside:box.top >= bounds.top && box.bottom <= bounds.bottom && box.right <= bounds.right,
                top:box.top, width:box.width};
      })};
    }));
    for (const row of rows) {
      expect(row.height).toBeLessThanOrEqual(85);
      expect(row.actions).toHaveLength(3);
      expect(new Set(row.actions.map(action => action.top)).size).toBe(1);
      for (const action of row.actions) { expect(action.inside).toBe(true); expect(action.width).toBeGreaterThanOrEqual(36); }
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}
