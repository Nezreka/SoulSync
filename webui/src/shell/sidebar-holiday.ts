/**
 * holiday decorations: the props. every holiday gets the same four layers,
 * so none of them is just a sticker:
 *
 * - sky: painted on the scene canvas (holiday-scene.ts)
 * - tint: `data-holiday` on the sidebar retunes the header glow, the active
 *   nav item and the weather glyph (sidebar-weather.css)
 * - one hero prop, lit: it throws its light onto the card it sits on
 * - the moment: the big day itself gets more (`holiday-moment`)
 *
 * the props live in the page, attached to the sidebar's own furniture, so
 * they scroll with it and sit on it: the header's empty bottom-left corner,
 * the divider above Support, the top edge of the Service Status card.
 *
 * motion is css, off under reduced motion. the wind sets how far things
 * sway; night lights the candles. clicks always pass through.
 */

import type { HolidayId } from './holidays';

const ROOT_CLASS = 'has-holiday';
const DECO_CLASS = 'sidebar-holiday';
const ART = '/static/holidays/';

export interface HolidayMood {
  night: boolean;
  /** 0..1, how much the wind shows */
  wind: number;
  /** the big day itself */
  moment?: boolean;
}

const SVG_NS = 'http://www.w3.org/2000/svg';

function svg(viewBox: string, cls: string, paint: string): SVGSVGElement {
  const el = document.createElementNS(SVG_NS, 'svg');
  el.setAttribute('viewBox', viewBox);
  el.setAttribute('class', cls);
  el.setAttribute('aria-hidden', 'true');
  el.innerHTML = paint;
  return el;
}

function span(cls: string): HTMLSpanElement {
  const el = document.createElement('span');
  el.className = cls;
  return el;
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

/**
 * the hero's light on the card: a warm wash over its top corner and a lit
 * rim along the edge, flickering with the flame when it's a flame
 */
function spill(): HTMLElement {
  const wrap = span('holiday-spill');
  wrap.append(span('holiday-spill-wash'), span('holiday-spill-rim'));
  return wrap;
}

/** a cobweb in the header's bottom-left corner, threads catching the light */
function cobweb(): HTMLElement {
  // spokes fan out of the corner (0,60); each ring sags back toward it
  const spokes = [0, 16, 33, 50, 68, 90].map((deg) => (deg * Math.PI) / 180);
  const at = (r: number, a: number) =>
    `${(Math.cos(a) * r).toFixed(1)} ${(60 - Math.sin(a) * r).toFixed(1)}`;
  let paint = spokes.map((a) => `<path d="M0 60 L${at(60, a)}"/>`).join('');
  for (const r of [11, 20, 30, 40, 51]) {
    let d = `M${at(r, spokes[0])}`;
    for (let i = 1; i < spokes.length; i++) {
      const mid = (spokes[i - 1] + spokes[i]) / 2;
      d += ` Q${at(r * 0.84, mid)} ${at(r, spokes[i])}`;
    }
    paint += `<path d="${d}"/>`;
  }
  const wrap = span('holiday-cobweb');
  wrap.appendChild(svg('0 0 60 60', 'holiday-cobweb-art', `<g>${paint}</g>`));
  return wrap;
}

/** a spider on its silk, hung from the divider: it lets itself down now and then */
function spider(): HTMLElement {
  const wrap = span('holiday-spider');
  const body = span('holiday-spider-body');
  body.appendChild(
    svg(
      '0 0 20 20',
      'holiday-spider-art',
      // eight legs, bent at the knee, then the body: a round abdomen, a small head
      `<g class="holiday-spider-legs">${[
        'M8.6 9 L4 6.4 L1.6 9.6',
        'M8.4 10.2 L3.4 9.4 L1 13',
        'M8.6 11.2 L4 12.6 L2.6 16.6',
        'M9 12 L6.2 15.4 L5.8 19',
        'M11.4 9 L16 6.4 L18.4 9.6',
        'M11.6 10.2 L16.6 9.4 L19 13',
        'M11.4 11.2 L16 12.6 L17.4 16.6',
        'M11 12 L13.8 15.4 L14.2 19',
      ]
        .map((d) => `<path d="${d}"/>`)
        .join('')}</g>` +
        '<ellipse cx="10" cy="7.6" rx="2" ry="1.8"/><ellipse cx="10" cy="12.2" rx="3.3" ry="3.9"/>',
    ),
  );
  wrap.append(span('holiday-silk'), body);
  return wrap;
}

/** snow settled along an edge: soft drifts, thicker in places */
function snowcap(): HTMLElement {
  return span('holiday-snowcap');
}

/**
 * new year's: a string of warm bulbs draped across the divider in three
 * swags, twinkling out of step
 */
const GARLAND_SWAGS = 3;
const GARLAND_BULBS = 13;

function garland(): HTMLElement {
  const wrap = span('holiday-garland');
  const sag = (f: number) => {
    const u = (f * GARLAND_SWAGS) % 1;
    return 1 + 8 * 4 * u * (1 - u);
  };
  let d = 'M0 1';
  for (let i = 0; i < GARLAND_SWAGS; i++) {
    const x0 = (i * 200) / GARLAND_SWAGS;
    const x1 = ((i + 1) * 200) / GARLAND_SWAGS;
    d += ` Q${((x0 + x1) / 2).toFixed(1)} 17 ${x1.toFixed(1)} 1`;
  }
  const wire = svg(
    '0 0 200 14',
    'holiday-garland-wire',
    `<path d="${d}" vector-effect="non-scaling-stroke"/>`,
  );
  // stretch to the divider's width; the bulbs are placed by percent, so they ride the wire
  wire.setAttribute('preserveAspectRatio', 'none');
  wrap.appendChild(wire);
  for (let i = 0; i < GARLAND_BULBS; i++) {
    const f = (i + 0.5) / GARLAND_BULBS;
    const bulb = span(`holiday-bulb holiday-bulb--${['gold', 'warm', 'champagne'][i % 3]}`);
    bulb.style.left = `${(f * 100).toFixed(2)}%`;
    bulb.style.top = `${sag(f).toFixed(1)}px`;
    bulb.style.animationDelay = `${(-i * 0.61).toFixed(2)}s`;
    wrap.appendChild(bulb);
  }
  return wrap;
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
      // the moon, the bats and the fog are on the sky; here: the web in
      // the corner, the spider on its thread, the jack-o'-lantern lighting
      // the card
      return [
        { anchor: 'header', el: deco('header', holiday, cobweb()) },
        { anchor: 'divider', el: deco('divider', holiday, spider()) },
        {
          anchor: 'card',
          el: deco(
            'card',
            holiday,
            spill(),
            pumpkin('pumpkin-2', 'md', false),
            pumpkin('pumpkin-3', 'lg', true),
          ),
        },
      ];
    case 'thanksgiving':
      return [{ anchor: 'card', el: deco('card', holiday, spill(), turkey()) }];
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
        { anchor: 'card', el: deco('card', holiday, spill(), snowcap(), tree()) },
      ];
    case 'new-year':
      // the fireworks and the confetti are on the sky; the lights are strung here
      return [
        { anchor: 'divider', el: deco('divider', holiday, garland()) },
        { anchor: 'card', el: deco('card', holiday, spill()) },
      ];
    case 'lunar-new-year':
      return [
        {
          anchor: 'divider',
          el: deco('divider', holiday, lantern('lantern-1', 'left'), lantern('lantern-2', 'right')),
        },
        { anchor: 'card', el: deco('card', holiday, spill()) },
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
      sidebar.dataset.holiday = holiday;
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
  sidebar.classList.toggle('holiday-moment', !!mood.moment);
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
  sidebar?.classList.remove(
    ROOT_CLASS,
    'holiday-night',
    'holiday-moment',
    'holiday-header-crowded',
  );
  if (sidebar) delete sidebar.dataset.holiday;
  sidebar?.style.removeProperty('--holiday-sway');
  sidebar?.style.removeProperty('--holiday-sway-time');
  mounted = null;
}

export function mountedHoliday(): HolidayId | null {
  return mounted;
}
