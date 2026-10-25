import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { mountedHoliday, mountHoliday, unmountHoliday } from './sidebar-holiday';

/** the decorations hang off the sidebar's own furniture */
function dom() {
  document.body.innerHTML = `
    <div class="sidebar" id="app-sidebar">
      <div class="sidebar-header"></div>
      <div class="sidebar-scroll">
        <div class="sidebar-spacer"></div>
        <div class="support-section"></div>
        <div class="status-section" data-music-only></div>
        <div class="status-section" data-video-only></div>
      </div>
    </div>`;
}

const DAY = { night: false, wind: 0 };
const sidebar = () => document.getElementById('app-sidebar')!;
const arts = (sel: string) =>
  [...document.querySelectorAll(`${sel} .sidebar-holiday img`)].map((i) => i.getAttribute('src'));

beforeEach(dom);
afterEach(() => {
  unmountHoliday();
  document.body.innerHTML = '';
});

describe('holiday decorations', () => {
  it('halloween: a cobweb in the header corner, a spider on the divider, the jack-o-lantern lighting the card', () => {
    mountHoliday('halloween', DAY);
    // the moon and the bats are painted on the sky now, not pasted on
    expect(arts('.sidebar-header')).toEqual([]);
    expect(document.querySelectorAll('.sidebar-header .holiday-cobweb svg path').length).toBe(11);
    expect(document.querySelectorAll('.sidebar-spacer .holiday-spider .holiday-silk').length).toBe(
      1,
    );
    expect(document.querySelectorAll('[data-music-only] .holiday-spill').length).toBe(1);
    // both sides' cards get them: only the visible one shows
    expect(arts('[data-music-only]')).toEqual([
      '/static/holidays/pumpkin-2.webp',
      '/static/holidays/pumpkin-3.webp',
    ]);
    expect(arts('[data-video-only]')).toEqual(arts('[data-music-only]'));
    expect(sidebar().classList.contains('has-holiday')).toBe(true);
  });

  it('thanksgiving: a turkey on the card, facing both ways', () => {
    mountHoliday('thanksgiving', DAY);
    expect(arts('[data-music-only]')).toEqual([
      '/static/holidays/turkey-right.webp',
      '/static/holidays/turkey-left.webp',
    ]);
    expect(arts('.sidebar-header')).toEqual([]);
  });

  it('lunar new year: two lanterns hung from the divider', () => {
    mountHoliday('lunar-new-year', DAY);
    expect(arts('.sidebar-spacer')).toEqual([
      '/static/holidays/lantern-1.webp',
      '/static/holidays/lantern-2.webp',
    ]);
  });

  it('christmas: santa crosses the header, ornaments hang from the divider, the tree lit on the card', () => {
    mountHoliday('christmas', DAY);
    expect(arts('.sidebar-header')).toEqual(['/static/holidays/sleigh.webp']);
    expect(arts('.sidebar-spacer')).toEqual([
      '/static/holidays/ornament-red.webp',
      '/static/holidays/ornament-blue.webp',
    ]);
    expect(arts('[data-music-only]')).toEqual(['/static/holidays/tree.webp']);
    // the star and the baubles, twinkling out of step
    const lights = document.querySelectorAll('[data-music-only] .holiday-light');
    expect(lights.length).toBe(11);
    expect(new Set([...lights].map((l) => (l as HTMLElement).style.animationDelay)).size).toBe(11);
  });

  it("new year's: a string of lights across the divider, twinkling out of step", () => {
    mountHoliday('new-year', DAY);
    const bulbs = [...document.querySelectorAll<HTMLElement>('.sidebar-spacer .holiday-bulb')];
    expect(bulbs.length).toBe(13);
    expect(new Set(bulbs.map((b) => b.style.animationDelay)).size).toBe(13);
    // they hang on the wire: highest at the ends of a swag, lowest mid-swag
    const tops = bulbs.map((b) => parseFloat(b.style.top));
    expect(Math.max(...tops)).toBeLessThanOrEqual(9);
    expect(Math.min(...tops)).toBeGreaterThanOrEqual(1);
  });

  it('every holiday lights its card and sets its tint', () => {
    for (const id of [
      'halloween',
      'thanksgiving',
      'christmas',
      'new-year',
      'lunar-new-year',
    ] as const) {
      mountHoliday(id, DAY);
      expect(sidebar().dataset.holiday).toBe(id);
      expect(document.querySelectorAll('[data-music-only] .holiday-spill').length).toBe(1);
    }
    mountHoliday(null, DAY);
    expect(sidebar().dataset.holiday).toBeUndefined();
  });

  it('christmas: snow settled on the card', () => {
    mountHoliday('christmas', DAY);
    expect(document.querySelectorAll('[data-music-only] .holiday-snowcap').length).toBe(1);
  });

  it('the big day is marked, and cleared after', () => {
    mountHoliday('halloween', { ...DAY, moment: true });
    expect(sidebar().classList.contains('holiday-moment')).toBe(true);
    mountHoliday('halloween', DAY);
    expect(sidebar().classList.contains('holiday-moment')).toBe(false);
    mountHoliday('halloween', { ...DAY, moment: true });
    unmountHoliday();
    expect(sidebar().classList.contains('holiday-moment')).toBe(false);
  });

  it('never takes clicks or screen-reader attention', () => {
    mountHoliday('halloween', DAY);
    for (const el of document.querySelectorAll('.sidebar-holiday')) {
      expect(el.getAttribute('aria-hidden')).toBe('true');
    }
  });

  it('night lights the candles; wind sets the sway', () => {
    mountHoliday('lunar-new-year', { night: true, wind: 1 });
    expect(sidebar().classList.contains('holiday-night')).toBe(true);
    expect(sidebar().style.getPropertyValue('--holiday-sway')).toBe('9.0deg');
    mountHoliday('lunar-new-year', { night: false, wind: 0 });
    expect(sidebar().classList.contains('holiday-night')).toBe(false);
    expect(sidebar().style.getPropertyValue('--holiday-sway')).toBe('2.0deg');
  });

  it('switching holidays swaps the art, never stacks it; null takes it all down', () => {
    mountHoliday('halloween', DAY);
    mountHoliday('halloween', DAY);
    expect(document.querySelectorAll('.sidebar-header .sidebar-holiday').length).toBe(1);
    mountHoliday('thanksgiving', DAY);
    expect(document.querySelectorAll('.holiday-cobweb').length).toBe(0);
    expect(mountedHoliday()).toBe('thanksgiving');
    mountHoliday(null, DAY);
    expect(document.querySelectorAll('.sidebar-holiday').length).toBe(0);
    expect(sidebar().classList.contains('has-holiday')).toBe(false);
  });
});

describe('the header corner', () => {
  function lineAt(left: number) {
    const header = document.querySelector('.sidebar-header')!;
    const line = document.createElement('button');
    line.id = 'sidebar-weather-line';
    const span = document.createElement('span');
    line.appendChild(span);
    header.appendChild(line);
    header.getBoundingClientRect = () => ({ left: 0, width: 240 }) as DOMRect;
    span.getBoundingClientRect = () => ({ left, width: 240 - left - 18 }) as DOMRect;
  }

  it('the corner art steps aside when a long weather line reaches it', () => {
    lineAt(40);
    mountHoliday('halloween', DAY);
    expect(sidebar().classList.contains('holiday-header-crowded')).toBe(true);
  });

  it('and stays when there is room', () => {
    lineAt(74);
    mountHoliday('halloween', DAY);
    expect(sidebar().classList.contains('holiday-header-crowded')).toBe(false);
  });
});
