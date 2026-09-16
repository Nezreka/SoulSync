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
    } as KeyboardEvent & { currentTarget: Element });
    const video = document.getElementById('organization-video-tab')!;
    expect(document.activeElement).toBe(video);
    expect(video.getAttribute('aria-selected')).toBe('true');
    expect(music.tabIndex).toBe(-1);
  });
  it('keeps all four sets of controls in exactly one panel each', () => {
    for (const [id, panel] of [
      ['transfer-path', 'folders-music-panel'],
      ['video-movies-path', 'folders-video-panel'],
      ['template-album-path', 'organization-music-panel'],
      ['vo-movie-template', 'organization-video-panel'],
    ]) {
      expect(document.querySelectorAll(`#${id}`).length).toBe(1);
      expect(document.getElementById(id)!.closest('[role="tabpanel"]')!.id).toBe(panel);
    }
  });
});
