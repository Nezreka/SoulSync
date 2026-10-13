import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
const block = source.slice(
  source.indexOf('function switchLibraryMediaTab('),
  source.indexOf('// Settings redesign'),
);
let tabs: {
  switchLibraryMediaTab: (tab: Element) => void;
  handleLibraryMediaTabKey: (event: KeyboardEvent & { currentTarget: Element }) => void;
};
beforeEach(() => {
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  document.body.innerHTML = '<div id="settings-page"></div>';
  for (const card of parsed.querySelectorAll('.stg-media-card'))
    document.getElementById('settings-page')!.append(card.cloneNode(true));
  tabs = new Function(
    'document',
    `${block};return {switchLibraryMediaTab,handleLibraryMediaTabKey}`,
  )(document);
});
describe('Library settings media tabs', () => {
  it('switches only its own card and preserves unsaved fields', () => {
    const music = document.getElementById('transfer-path') as HTMLInputElement;
    music.value = '/unsaved/music';
    tabs.switchLibraryMediaTab(document.getElementById('folders-video-tab')!);
    expect(document.getElementById('folders-music-panel')!.hidden).toBe(true);
    expect(document.getElementById('folders-video-panel')!.hidden).toBe(false);
    expect(document.getElementById('organization-music-panel')!.hidden).toBe(false);
    tabs.switchLibraryMediaTab(document.getElementById('folders-music-tab')!);
    expect(document.getElementById('transfer-path')).toBe(music);
    expect(music.value).toBe('/unsaved/music');
  });
  it('supports arrow keys with a single active tab stop', () => {
    const music = document.getElementById('organization-music-tab')!;
    tabs.handleLibraryMediaTabKey({
      key: 'ArrowRight',
      currentTarget: music,
      preventDefault() {},
    } as unknown as KeyboardEvent & { currentTarget: Element });
    const books = document.getElementById('organization-audiobooks-tab')!;
    expect(document.activeElement).toBe(books);
    expect(books.getAttribute('aria-selected')).toBe('true');
    expect(music.tabIndex).toBe(-1);
  });
  it('orders the tabs music, audiobooks, podcasts, video on both cards', () => {
    for (const card of document.querySelectorAll('.stg-media-card'))
      expect([...card.querySelectorAll('[role="tab"]')].map((t) => t.textContent)).toEqual([
        'Music',
        'Audiobooks',
        'Podcasts',
        'Video',
      ]);
  });
  it('keeps all four sets of controls in exactly one panel each', () => {
    for (const [id, panel] of [
      ['transfer-path', 'folders-music-panel'],
      ['video-movies-path', 'folders-video-panel'],
      ['template-album-path', 'organization-music-panel'],
      ['vo-movie-template', 'organization-video-panel'],
      ['audiobooks-path', 'folders-audiobooks-panel'],
      ['podcasts-path', 'folders-podcasts-panel'],
      ['template-audiobook-path', 'organization-audiobooks-panel'],
      ['audiobook-recycle-deletes', 'organization-audiobooks-panel'],
      ['audiobook-format-first', 'organization-audiobooks-panel'],
      ['audiobook-marketplace', 'organization-audiobooks-panel'],
      ['audiobook-write-nfo', 'organization-audiobooks-panel'],
      ['template-podcast-path', 'organization-podcasts-panel'],
      ['podcast-media-format', 'organization-podcasts-panel'],
      ['download-path', 'folders-music-panel'],
      ['template-sample-path', 'organization-music-panel'],
      ['disc-label', 'organization-music-panel'],
    ]) {
      expect(document.querySelectorAll(`#${id}`).length).toBe(1);
      expect(document.getElementById(id)!.closest('[role="tabpanel"]')!.id).toBe(panel);
    }
  });
});
it('lets four tabs scroll at phone width instead of squeezing their labels', () => {
  // measured in chromium: at 100px minimum each the switcher was 435px wide in
  // a 375px viewport and pushed the page sideways. The first fix shared the
  // row (flex: 1; min-width: 0), which stopped the sideways push but squeezed
  // "Audiobooks" into a 68px tab and its label painted over "Podcasts"
  // (mobile CSS batch #2, RC2). Now the tabs keep their content size and the
  // switcher scrolls horizontally, like the app's other tab rails.
  const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
  // style.css has several max-width:600px blocks; take the one with the switcher.
  const starts = [...css.matchAll(/@media \(max-width: 600px\)/g)].map((m) => m.index);
  const at = starts.find((i) => css.slice(i, i + 2000).includes('.stg-media-switcher'));
  expect(at).toBeDefined();
  const block = css.slice(at, at! + 2000);
  expect(block).toMatch(/\.stg-media-switcher \{[^}]*overflow-x: auto/);
  expect(block).toMatch(/\.stg-media-switcher button \{[^}]*flex: 0 0 auto/);
  expect(block).toMatch(/\.stg-media-switcher button \{[^}]*white-space: nowrap/);
  expect(block).not.toMatch(/\.stg-media-switcher button \{[^}]*flex: 1;/);
});
