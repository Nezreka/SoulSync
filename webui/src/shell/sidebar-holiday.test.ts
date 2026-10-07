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
  it('halloween: a pumpkin and a bat in the header, a roosting bat, pumpkins on the status card', () => {
    mountHoliday('halloween', DAY);
    expect(arts('.sidebar-header')).toEqual([
      '/static/holidays/pumpkin-1.webp',
      '/static/holidays/bat-1.webp',
    ]);
    expect(arts('.sidebar-spacer')).toEqual(['/static/holidays/bat-2.webp']);
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
    expect(arts('.sidebar-header')).toEqual([]);
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
