/**
 * holiday decorations: your art, placed where it belongs instead of pasted
 * on. they live in the page, attached to the sidebar's own furniture, so
 * they scroll with it and sit on it:
 *
 * - header: a little scene in its empty bottom-left corner, under the
 *   profile row, left of the weather line
 * - footer: hung from the divider above Support, perched on the top edge of
 *   the Service Status card
 *
 * motion is css (sway, flicker, flap, a turkey's strut), off under reduced
 * motion. the wind sets how far the lanterns sway; night lights the
 * candles. clicks always pass through.
 */

import type { HolidayId } from './holidays';

const ROOT_CLASS = 'has-holiday';
const DECO_CLASS = 'sidebar-holiday';
const ART = '/static/holidays/';

export interface HolidayMood {
  night: boolean;
  /** 0..1, how much the wind shows */
  wind: number;
}

function img(name: string, cls: string, alt = ''): HTMLImageElement {
  const el = document.createElement('img');
  el.src = `${ART}${name}.webp`;
  el.alt = alt;
  el.className = cls;
  el.decoding = 'async';
  el.draggable = false;
  return el;
}

function deco(slot: string, holiday: HolidayId, ...children: HTMLElement[]): HTMLDivElement {
  const el = document.createElement('div');
  el.className = `${DECO_CLASS} ${DECO_CLASS}--${slot}`;
  el.dataset.holiday = holiday;
  el.setAttribute('aria-hidden', 'true');
  el.append(...children);
  return el;
}

/** a carved pumpkin: the art plus a candle glow inside, lit at night */
function pumpkin(name: string, size: string, carved: boolean): HTMLElement {
  const wrap = document.createElement('span');
  wrap.className = `holiday-pumpkin holiday-pumpkin--${size}${carved ? ' is-carved' : ''}`;
  if (carved) {
    const glow = document.createElement('span');
    glow.className = 'holiday-candle';
    wrap.appendChild(glow);
  }
  wrap.appendChild(img(name, 'holiday-art'));
  return wrap;
}

function lantern(name: string, side: 'left' | 'right', kind = ''): HTMLElement {
  const wrap = document.createElement('span');
  wrap.className = `holiday-lantern holiday-lantern--${side}${kind ? ` holiday-lantern--${kind}` : ''}`;
  const cord = document.createElement('span');
  cord.className = 'holiday-cord';
  const glow = document.createElement('span');
  glow.className = 'holiday-lantern-glow';
  wrap.append(cord, glow, img(name, 'holiday-art'));
  return wrap;
}

function bat(name: string, cls: string): HTMLElement {
  const flier = document.createElement('span');
  flier.className = cls;
  const wings = document.createElement('span');
  wings.className = 'holiday-bat-wings';
  wings.appendChild(img(name, 'holiday-art'));
  flier.appendChild(wings);
  return flier;
}

/**
 * the tree's lights: tiny glows set over its baubles and candles (as a
 * fraction of the art's box), twinkling out of step at night
 */
const TREE_LIGHTS: Array<[number, number, string]> = [
  [0.5, 0.06, 'star'],
  [0.42, 0.24, 'gold'],
  [0.62, 0.29, 'red'],
  [0.3, 0.4, 'blue'],
  [0.56, 0.44, 'gold'],
  [0.74, 0.5, 'red'],
  [0.38, 0.56, 'gold'],
  [0.24, 0.66, 'red'],
  [0.6, 0.64, 'blue'],
  [0.8, 0.7, 'gold'],
  [0.46, 0.74, 'red'],
];

function tree(): HTMLElement {
  const wrap = document.createElement('span');
  wrap.className = 'holiday-tree';
  wrap.appendChild(img('tree', 'holiday-art'));
  TREE_LIGHTS.forEach(([x, y, color], i) => {
    const light = document.createElement('span');
    light.className = `holiday-light holiday-light--${color}`;
    light.style.left = `${(x * 100).toFixed(1)}%`;
    light.style.top = `${(y * 100).toFixed(1)}%`;
    light.style.animationDelay = `${(-i * 0.73).toFixed(2)}s`;
    wrap.appendChild(light);
  });
  return wrap;
}

function sleigh(): HTMLElement {
  const flier = document.createElement('span');
  flier.className = 'holiday-sleigh';
  flier.appendChild(img('sleigh', 'holiday-art'));
  return flier;
}

/** the turkey faces the way it walks: one art each way, swapped at the turns */
function turkey(): HTMLElement {
  const walker = document.createElement('span');
  walker.className = 'holiday-turkey';
  const bob = document.createElement('span');
  bob.className = 'holiday-turkey-bob';
  bob.append(
    img('turkey-right', 'holiday-art holiday-turkey-right'),
    img('turkey-left', 'holiday-art holiday-turkey-left'),
  );
  walker.appendChild(bob);
  return walker;
}

/** what each holiday puts where */
function build(
  holiday: HolidayId,
): Array<{ anchor: 'header' | 'divider' | 'card'; el: HTMLElement }> {
  switch (holiday) {
    case 'halloween':
      return [
        {
          anchor: 'header',
          el: deco(
            'header',
            holiday,
            pumpkin('pumpkin-1', 'sm', true),
            bat('bat-1', 'holiday-bat holiday-bat--cross'),
          ),
        },
        {
          anchor: 'divider',
          el: deco('divider', holiday, bat('bat-2', 'holiday-bat holiday-bat--roost')),
        },
        {
          anchor: 'card',
          el: deco(
            'card',
            holiday,
            pumpkin('pumpkin-2', 'md', false),
            pumpkin('pumpkin-3', 'lg', true),
          ),
        },
      ];
    case 'thanksgiving':
      return [{ anchor: 'card', el: deco('card', holiday, turkey()) }];
    case 'christmas':
      return [
        { anchor: 'header', el: deco('header', holiday, sleigh()) },
        {
          anchor: 'divider',
          el: deco(
            'divider',
            holiday,
            lantern('ornament-red', 'left', 'ornament'),
            lantern('ornament-blue', 'right', 'ornament'),
          ),
        },
        { anchor: 'card', el: deco('card', holiday, tree()) },
      ];
    case 'new-year':
      // the fireworks are painted on the scene canvas, no art to hang
      return [];
    case 'lunar-new-year':
      return [
        {
          anchor: 'divider',
          el: deco('divider', holiday, lantern('lantern-1', 'left'), lantern('lantern-2', 'right')),
        },
      ];
  }
}

function anchors(): Record<'header' | 'divider' | 'card', HTMLElement[]> {
  const sidebar = document.getElementById('app-sidebar');
  const all = (sel: string) => (sidebar ? [...sidebar.querySelectorAll<HTMLElement>(sel)] : []);
  return {
    header: all('.sidebar-header'),
    divider: all('.sidebar-spacer'),
    // both sides' status cards: only the visible one shows anyway, and a
    // switch between music and video needs no remount
    card: all('.status-section'),
  };
}

let mounted: HolidayId | null = null;

/** put a holiday's decorations up, or null to take them down */
export function mountHoliday(holiday: HolidayId | null, mood: HolidayMood): void {
  const sidebar = document.getElementById('app-sidebar');
  if (!sidebar) return;
  if (holiday !== mounted) {
    unmountHoliday();
    if (holiday) {
      const at = anchors();
      for (const { anchor, el } of build(holiday)) {
        at[anchor].forEach((host, i) =>
          host.appendChild(i === 0 ? el : (el.cloneNode(true) as HTMLElement)),
        );
      }
      sidebar.classList.add(ROOT_CLASS);
      mounted = holiday;
    }
  }
  if (mounted) setHolidayMood(mood);
}

/** night lights the candles; wind sets the sway */
export function setHolidayMood(mood: HolidayMood): void {
  const sidebar = document.getElementById('app-sidebar');
  if (!sidebar) return;
  sidebar.classList.toggle('holiday-night', mood.night);
  const wind = Math.max(0, Math.min(1, mood.wind));
  sidebar.style.setProperty('--holiday-sway', `${(2 + wind * 7).toFixed(1)}deg`);
  sidebar.style.setProperty('--holiday-sway-time', `${(4.2 - wind * 1.8).toFixed(2)}s`);
  guardHeaderCorner(sidebar);
}

/** room the header's corner art needs left of the weather line, in px */
const HEADER_CORNER = 56;

/**
 * the header art sits in the corner left of the weather line. a long
 * condition ("Thunderstorm with slight hail") can stretch the line into
 * it; then the art steps aside rather than sit on the text
 */
export function guardHeaderCorner(
  sidebar: HTMLElement | null = document.getElementById('app-sidebar'),
): void {
  if (!sidebar) return;
  const header = sidebar.querySelector('.sidebar-header');
  const line = document.getElementById('sidebar-weather-line');
  const text = line?.querySelector('span') ?? line;
  if (!header || !text) return;
  const h = header.getBoundingClientRect();
  const t = text.getBoundingClientRect();
  if (t.width === 0) return; // not laid out (hidden, collapsed): leave it be
  sidebar.classList.toggle('holiday-header-crowded', t.left - h.left < HEADER_CORNER);
}

export function unmountHoliday(): void {
  document.querySelectorAll(`.${DECO_CLASS}`).forEach((el) => el.remove());
  const sidebar = document.getElementById('app-sidebar');
  sidebar?.classList.remove(ROOT_CLASS, 'holiday-night', 'holiday-header-crowded');
  sidebar?.style.removeProperty('--holiday-sway');
  sidebar?.style.removeProperty('--holiday-sway-time');
  mounted = null;
}

export function mountedHoliday(): HolidayId | null {
  return mounted;
}
