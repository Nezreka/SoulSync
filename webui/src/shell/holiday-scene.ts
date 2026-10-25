/**
 * a holiday's sky: what each holiday paints on the scene canvas, with the
 * weather. the decorations (sidebar-holiday.ts) are the props; this is the
 * air they sit in.
 *
 * - halloween: a harvest moon, bats, ground fog, will-o'-wisps; on the
 *   night itself a witch crosses the moon
 * - thanksgiving: leaves coming down, geese heading south, a hearth glow
 * - christmas: flurries all week, a bright star, lit windows below
 * - new year's: confetti and glints (the fireworks live in weather-scene)
 * - lunar new year: sky lanterns rising at night, plum petals by day
 *
 * pure drawing like weather-scene.ts: no dom, no timers. `top` is where the
 * sky starts showing (the header is opaque), so nothing hides behind it.
 * the moment (0..1) is the big day: more of everything.
 */

import type { HolidaySky } from './holidays';

import {
  leafSprite,
  puffSprite,
  softDot,
  type SceneConditions,
  type SceneLayer,
} from './weather-scene';

type Ctx = CanvasRenderingContext2D;
type Sprite = HTMLCanvasElement | null;
type Rng = () => number;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const smoothstep = (a: number, b: number, v: number) => {
  const k = clamp((v - a) / (b - a), 0, 1);
  return k * k * (3 - 2 * k);
};

/** something drifting down: a leaf, a petal, a flake, a bit of confetti */
interface Drifter {
  x: number;
  y: number;
  vy: number;
  sway: number;
  ph: number;
  size: number;
  rot: number;
  spin: number;
  flip: number;
  flipRate: number;
  a: number;
  kind: number;
}

/** something rising: a wisp, a sky lantern */
interface Riser {
  x: number;
  y: number;
  vy: number;
  ph: number;
  size: number;
  age: number;
  life: number;
}

interface Bat {
  x: number;
  y: number;
  vx: number;
  baseY: number;
  ph: number;
  flapRate: number;
  size: number;
  bob: number;
}

interface Flock {
  x: number;
  y: number;
  vx: number;
  birds: Array<[number, number, number]>;
}

interface Glint {
  x: number;
  y: number;
  age: number;
  life: number;
  size: number;
}

/** what the holiday's light looks like: a wash from the top by day, a glow from below by night */
interface Look {
  dayTop: string;
  dayBottom: string;
  night: string;
}

const LOOKS: Record<HolidaySky['id'], Look> = {
  halloween: {
    dayTop: '150,96,220,0.05',
    dayBottom: '255,138,48,0.075',
    night: '255,128,40,0.075',
  },
  thanksgiving: {
    dayTop: '255,190,110,0.06',
    dayBottom: '196,98,52,0.08',
    night: '236,150,70,0.07',
  },
  christmas: {
    dayTop: '206,224,255,0.05',
    dayBottom: '255,206,140,0.05',
    night: '255,200,120,0.06',
  },
  'new-year': {
    dayTop: '246,218,160,0.05',
    dayBottom: '246,218,160,0.06',
    night: '246,214,150,0.06',
  },
  'lunar-new-year': {
    dayTop: '236,64,52,0.045',
    dayBottom: '255,170,80,0.07',
    night: '236,80,52,0.08',
  },
};

const CONFETTI_RGB = ['246,214,150', '255,240,206', '214,222,240', '244,176,170', '255,200,110'];
const AUTUMN_LEAVES: Array<[string, string]> = [
  ['rgb(205,128,62)', 'rgba(120,60,20,0.7)'],
  ['rgb(214,164,72)', 'rgba(130,90,30,0.7)'],
  ['rgb(176,84,52)', 'rgba(100,40,20,0.7)'],
  ['rgb(150,70,44)', 'rgba(80,32,16,0.7)'],
];

/** the harvest moon, baked once: warm, a little mottled, darker at the limb */
function moonSprite(r: number): Sprite {
  if (typeof document === 'undefined') return null;
  const d = Math.ceil(r * 2 + 4);
  const c = document.createElement('canvas');
  c.width = d * 2;
  c.height = d * 2;
  const g = c.getContext('2d');
  if (!g) return null;
  g.scale(2, 2);
  const m = d / 2;
  const face = g.createRadialGradient(m - r * 0.32, m - r * 0.34, r * 0.1, m, m, r);
  face.addColorStop(0, 'rgba(255,238,206,1)');
  face.addColorStop(0.6, 'rgba(250,206,140,1)');
  face.addColorStop(1, 'rgba(230,148,78,1)');
  g.fillStyle = face;
  g.beginPath();
  g.arc(m, m, r, 0, Math.PI * 2);
  g.fill();
  g.save();
  // paint only on the disc already drawn
  g.globalCompositeOperation = 'source-atop';
  // the seas: soft darker patches, the man in the moon if you look for him
  const seas: Array<[number, number, number, number]> = [
    [-0.28, -0.18, 0.34, 0.2],
    [0.18, -0.3, 0.22, 0.16],
    [0.06, 0.12, 0.3, 0.18],
    [-0.34, 0.3, 0.2, 0.13],
    [0.36, 0.22, 0.16, 0.12],
  ];
  for (const [x, y, s, a] of seas) {
    const sg = g.createRadialGradient(m + x * r, m + y * r, 0, m + x * r, m + y * r, s * r);
    sg.addColorStop(0, `rgba(160,80,36,${a})`);
    sg.addColorStop(1, 'rgba(160,80,36,0)');
    g.fillStyle = sg;
    g.fillRect(0, 0, d, d);
  }
  const limb = g.createRadialGradient(m, m, r * 0.62, m, m, r);
  limb.addColorStop(0, 'rgba(110,40,16,0)');
  limb.addColorStop(1, 'rgba(110,40,16,0.32)');
  g.fillStyle = limb;
  g.fillRect(0, 0, d, d);
  g.restore();
  return c;
}

export function createHolidaySky(
  ctx: Ctx,
  cond: SceneConditions,
  rng: Rng,
  sky: HolidaySky,
): SceneLayer {
  const id = sky.id;
  const moment = clamp(sky.moment, 0, 1);
  const night = !cond.isDay;
  const look = LOOKS[id];
  const rand = (a: number, b: number) => a + rng() * (b - a);

  let w = 260;
  let h = 900;
  let top = 0;
  let dpr = 1;
  let built = false;

  // sprites, baked once per scene
  const glow = softDot('255,255,255');
  const ember = softDot('255,156,64');
  const warmDot = softDot('255,176,96');
  const flake = softDot('240,246,255');
  const fogPuff =
    id === 'halloween'
      ? puffSprite(night ? '118,100,150' : '150,140,170', rng, 520, 110, 30, 0.3, 12)
      : null;
  const leafArt: Sprite[] =
    id === 'thanksgiving' || id === 'halloween'
      ? AUTUMN_LEAVES.map(([f, r]) => leafSprite(f, r))
      : id === 'lunar-new-year'
        ? [
            leafSprite('rgb(246,190,204)', 'rgba(200,120,140,0.35)'),
            leafSprite('rgb(255,222,230)', 'rgba(210,150,160,0.3)'),
          ]
        : [];
  const moonR = 26;
  // by day it hangs pale in the sky, the way a real moon does
  const moon = id === 'halloween' ? moonSprite(moonR) : null;

  // a moon (or christmas's star) needs a sky to show in
  const clear = 1 - 0.55 * smoothstep(0.35, 1, cond.cloudCover);

  let wash: CanvasGradient | null = null;
  let moonX = 0;
  let moonY = 0;
  let drifters: Drifter[] = [];
  let risers: Riser[] = [];
  let fog: Array<{ x: number; y: number; scale: number; a: number; speed: number }> = [];
  let bats: Bat[] = [];
  let nextBat = rand(1, 4);
  let flock: Flock | null = null;
  let nextFlock = rand(4, 12);
  let witch: { x: number; y: number; vx: number; age: number } | null = null;
  let nextWitch = rand(6, 14);
  let glints: Glint[] = [];
  let nextGlint = rand(0.5, 2);

  const skyH = () => Math.max(1, h - top);
  const scale = () => clamp(Math.sqrt((w * skyH()) / (240 * 650)), 0.5, 1.3);

  /* ---------------- build ---------------- */

  function newDrifter(anywhere: boolean): Drifter {
    const base: Drifter = {
      x: rand(-10, w + 10),
      y: anywhere ? rand(top, h) : top - rand(8, 40),
      vy: 0,
      sway: 0,
      ph: rand(0, Math.PI * 2),
      size: 0,
      rot: rand(0, Math.PI * 2),
      spin: rand(-1.2, 1.2),
      flip: rand(0, Math.PI * 2),
      flipRate: rand(1.6, 3.6),
      a: 0,
      kind: 0,
    };
    if (id === 'christmas') {
      const near = rng() < 0.35;
      return {
        ...base,
        vy: near ? rand(18, 28) : rand(9, 16),
        sway: near ? 14 : 7,
        size: near ? rand(1.8, 2.8) : rand(0.8, 1.5),
        a: near ? rand(0.45, 0.7) : rand(0.22, 0.4),
      };
    }
    if (id === 'new-year') {
      return {
        ...base,
        vy: rand(14, 26),
        sway: rand(8, 16),
        size: rand(2.4, 3.6),
        a: rand(0.5, 0.8),
        kind: Math.floor(rng() * CONFETTI_RGB.length),
        flipRate: rand(3, 6),
      };
    }
    // leaves and petals
    const petal = id === 'lunar-new-year';
    return {
      ...base,
      vy: petal ? rand(10, 18) : rand(16, 28),
      sway: petal ? rand(12, 22) : rand(16, 30),
      size: petal ? rand(6, 8.5) : rand(9, 13),
      a: petal ? rand(0.5, 0.75) : rand(0.5, 0.72),
      kind: Math.floor(rng() * Math.max(1, leafArt.length)),
    };
  }

  function newRiser(anywhere: boolean): Riser {
    if (id === 'lunar-new-year') {
      return {
        x: rand(0.1, 0.9) * w,
        y: anywhere ? rand(top + skyH() * 0.2, h) : h + rand(10, 40),
        vy: -rand(9, 15),
        ph: rand(0, 6),
        size: rand(0.8, 1.15),
        age: anywhere ? rand(0, 30) : 0,
        life: Infinity,
      };
    }
    // a will-o'-wisp: up out of the fog, flickering, gone again
    return {
      x: rand(0.06, 0.94) * w,
      y: h - rand(10, skyH() * 0.18),
      vy: -rand(6, 12),
      ph: rand(0, 6),
      size: rand(0.8, 1.2),
      age: anywhere ? rand(0, 6) : 0,
      life: rand(6, 10),
    };
  }

  function build(): void {
    const k = scale();
    const more = 1 + moment * 0.7;
    // rising behind the sidebar's right edge, in the gap under the first
    // nav row: the section chevrons and badges stay clear of it
    moonX = w - 6;
    moonY = top + Math.min(70, skyH() * 0.11);

    drifters = [];
    let n = 0;
    if (id === 'thanksgiving' && cond.precip !== 'snow') n = 6;
    if (id === 'halloween' && !night && cond.precip === 'none') n = 3;
    if (id === 'lunar-new-year' && !night && cond.precip === 'none') n = 7;
    if (id === 'christmas' && cond.precip === 'none') n = 24;
    if (id === 'new-year' && cond.precip === 'none') n = 15;
    n = Math.round(n * more * k);
    for (let i = 0; i < n; i++) drifters.push(newDrifter(true));

    risers = [];
    let r = 0;
    if (id === 'halloween' && night) r = 4;
    if (id === 'lunar-new-year' && night) r = 5;
    r = Math.round(r * (1 + moment * 0.8) * Math.sqrt(k));
    for (let i = 0; i < r; i++) risers.push(newRiser(true));

    fog = [];
    if (fogPuff) {
      const banks = moment > 0.5 ? 6 : 4;
      for (let i = 0; i < banks; i++) {
        const high = i >= 4; // the big night: a second bank, higher up
        fog.push({
          x: rand(-0.7, 0.4) * w,
          y: high ? lerp(top, h, rand(0.5, 0.65)) : h - rand(0, skyH() * 0.14),
          scale: rand(0.85, 1.3),
          a: (high ? rand(0.08, 0.12) : rand(0.16, 0.26)) * (night ? 1 : 0.75),
          speed: rand(2.5, 6) * (i % 2 ? 1 : -0.7),
        });
      }
    }

    bats = [];
    glints = [];

    const [dayTop, dayBottom] = [look.dayTop, look.dayBottom];
    if (night) {
      // warm light from below: porch lights, a hearth, lanterns in the street
      wash = ctx.createLinearGradient(0, h - skyH() * 0.45, 0, h);
      wash.addColorStop(0, `rgba(${look.night.replace(/,[\d.]+$/, ',0')})`);
      wash.addColorStop(1, `rgba(${look.night})`);
    } else {
      wash = ctx.createLinearGradient(0, top, 0, h);
      wash.addColorStop(0, `rgba(${dayTop})`);
      wash.addColorStop(0.55, `rgba(${dayTop.replace(/,[\d.]+$/, ',0')})`);
      wash.addColorStop(1, `rgba(${dayBottom})`);
    }
  }

  /* ---------------- update ---------------- */

  function spawnBat(): void {
    const dir = rng() < 0.5 ? -1 : 1;
    // some cross the moon, where you can see them best
    const byMoon = moon && rng() < 0.55;
    const y = byMoon ? moonY + rand(-14, 14) : top + rand(0.04, 0.5) * skyH();
    bats.push({
      x: dir > 0 ? -16 : w + 16,
      y,
      vx: dir * rand(36, 58),
      baseY: y,
      ph: rand(0, 6),
      flapRate: rand(13, 17),
      size: rand(8, 11),
      bob: rand(5, 11),
    });
  }

  function update(dt: number, t: number): void {
    for (const d of drifters) {
      d.y += d.vy * dt;
      d.x += Math.sin(t * 0.8 + d.ph) * d.sway * dt;
      d.rot += d.spin * dt;
      d.flip += d.flipRate * dt;
      if (d.y > h + 14) Object.assign(d, newDrifter(false));
      if (d.x < -20) d.x = w + 16;
      if (d.x > w + 20) d.x = -16;
    }

    for (let i = 0; i < risers.length; i++) {
      const r = risers[i];
      r.age += dt;
      r.y += r.vy * dt;
      r.x += Math.sin(t * 0.5 + r.ph) * 5 * dt;
      if (r.age > r.life || r.y < top - 30) risers[i] = newRiser(false);
    }

    for (const f of fog) {
      f.x += f.speed * dt;
      const span = 520 * f.scale;
      if (f.x > w + 40) f.x = -span + 40;
      if (f.x < -span - 40) f.x = w - 40;
    }

    if (id === 'halloween' && night) {
      nextBat -= dt;
      const most = Math.round(lerp(2, 6, moment));
      if (nextBat <= 0 && bats.length < most) {
        spawnBat();
        // on the night itself they come in twos and threes
        if (moment > 0.5 && rng() < 0.6) spawnBat();
        nextBat = lerp(rand(5, 14), rand(1.5, 5), moment);
      }
      for (let i = bats.length - 1; i >= 0; i--) {
        const b = bats[i];
        b.x += b.vx * dt;
        // a bat never flies straight: it bobs and darts
        b.y = b.baseY + Math.sin(t * 2.3 + b.ph) * b.bob + Math.sin(t * 5.1 + b.ph * 2) * 2.5;
        if (b.x < -30 || b.x > w + 30) bats.splice(i, 1);
      }

      if (moment > 0.5) {
        nextWitch -= dt;
        if (!witch && nextWitch <= 0) {
          witch = { x: w + 30, y: moonY + 4, vx: -rand(30, 38), age: 0 };
        }
        if (witch) {
          witch.age += dt;
          witch.x += witch.vx * dt;
          witch.y = moonY + 4 + Math.sin(witch.age * 1.1) * 5;
          if (witch.x < -40) {
            witch = null;
            nextWitch = rand(30, 50);
          }
        }
      }
    }

    // geese heading south, by day
    if (id === 'thanksgiving' && !night && cond.precip === 'none') {
      nextFlock -= dt;
      if (!flock && nextFlock <= 0) {
        const dir = rng() < 0.5 ? -1 : 1;
        const size = 5 + Math.floor(rng() * 4);
        const birds: Array<[number, number, number]> = [[0, 0, rand(0, 6)]];
        for (let i = 1; i < size; i++) {
          const arm = i % 2 ? 1 : -1;
          const rank = Math.ceil(i / 2);
          birds.push([-rank * 9 * dir, arm * rank * 5.5, rand(0, 6)]);
        }
        flock = {
          x: dir > 0 ? -50 : w + 50,
          y: top + rand(0.06, 0.3) * skyH(),
          vx: dir * rand(22, 30),
          birds,
        };
      }
      if (flock) {
        flock.x += flock.vx * dt;
        if (flock.x < -90 || flock.x > w + 90) {
          flock = null;
          nextFlock = lerp(rand(30, 55), rand(14, 26), moment);
        }
      }
    }

    // new year's: glints, a sparkle here and there in the air
    if (id === 'new-year') {
      nextGlint -= dt;
      if (nextGlint <= 0) {
        glints.push({
          x: rand(0.08, 0.92) * w,
          y: top + rand(0.05, 0.9) * skyH(),
          age: 0,
          life: rand(0.9, 1.6),
          size: rand(5, 9),
        });
        nextGlint = lerp(rand(0.8, 2), rand(0.3, 0.9), moment);
      }
      for (let i = glints.length - 1; i >= 0; i--) {
        glints[i].age += dt;
        if (glints[i].age > glints[i].life) glints.splice(i, 1);
      }
    }
  }

  /* ---------------- draw ---------------- */

  function drawMoon(t: number): void {
    if (!moon || !glow) return;
    const d = moonR * 2 + 4;
    if (!night) {
      // washed out by daylight: added as light, never a dark disc
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = 0.12 * clear;
      ctx.drawImage(moon, moonX - d / 2, moonY - d / 2, d, d);
      ctx.restore();
      return;
    }
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    // the halo: wide and warm, breathing a touch
    const breath = 0.94 + 0.06 * Math.sin(t * 0.35);
    ctx.globalAlpha = 0.24 * clear * breath;
    ctx.drawImage(warmDot ?? glow, moonX - moonR * 5, moonY - moonR * 5, moonR * 10, moonR * 10);
    ctx.globalAlpha = 0.18 * clear;
    ctx.drawImage(
      warmDot ?? glow,
      moonX - moonR * 2.2,
      moonY - moonR * 2.2,
      moonR * 4.4,
      moonR * 4.4,
    );
    ctx.restore();
    ctx.globalAlpha = 0.86 * clear;
    ctx.drawImage(moon, moonX - d / 2, moonY - d / 2, d, d);
    ctx.globalAlpha = 1;
  }

  /** christmas: one bright star, a slow four-point glint */
  function drawStar(t: number): void {
    if (id !== 'christmas' || !night || !glow) return;
    const x = w - 44;
    const y = top + Math.min(70, skyH() * 0.11);
    const pulse = 0.82 + 0.18 * Math.sin(t * 0.9);
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = 0.35 * clear * pulse;
    ctx.drawImage(warmDot ?? glow, x - 26, y - 26, 52, 52);
    ctx.globalAlpha = clear;
    ctx.drawImage(glow, x - 3.5, y - 3.5, 7, 7);
    for (const [len, ang, a] of [
      [22, 0, 0.5],
      [22, Math.PI / 2, 0.5],
      [9, Math.PI / 4, 0.3],
      [9, -Math.PI / 4, 0.3],
    ] as Array<[number, number, number]>) {
      const L = len * pulse;
      const dx = Math.cos(ang) * L;
      const dy = Math.sin(ang) * L;
      const g = ctx.createLinearGradient(x - dx, y - dy, x + dx, y + dy);
      g.addColorStop(0, 'rgba(255,240,200,0)');
      g.addColorStop(0.5, `rgba(255,240,200,${(a * clear).toFixed(3)})`);
      g.addColorStop(1, 'rgba(255,240,200,0)');
      ctx.strokeStyle = g;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x - dx, y - dy);
      ctx.lineTo(x + dx, y + dy);
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawFog(t: number): void {
    if (!fogPuff) return;
    for (const f of fog) {
      ctx.globalAlpha = f.a * (0.8 + 0.2 * Math.sin(t * 0.17 + f.y));
      ctx.drawImage(fogPuff, f.x, f.y - 55 * f.scale, 520 * f.scale, 110 * f.scale);
    }
    ctx.globalAlpha = 1;
  }

  /** a bat: wings out to the tips, scalloped trailing edge, little ears */
  function batPath(x: number, y: number, s: number, flap: number): void {
    const lift = flap * s * 0.55;
    ctx.beginPath();
    for (const side of [-1, 1]) {
      ctx.moveTo(x, y - s * 0.12);
      ctx.quadraticCurveTo(x + side * s * 0.5, y - lift - s * 0.32, x + side * s, y - lift);
      ctx.quadraticCurveTo(
        x + side * s * 0.82,
        y - lift * 0.5 + s * 0.18,
        x + side * s * 0.66,
        y - lift * 0.35 + s * 0.12,
      );
      ctx.quadraticCurveTo(
        x + side * s * 0.54,
        y - lift * 0.2 + s * 0.26,
        x + side * s * 0.36,
        y + s * 0.14,
      );
      ctx.quadraticCurveTo(x + side * s * 0.2, y + s * 0.04, x, y + s * 0.2);
      ctx.closePath();
    }
    ctx.moveTo(x + s * 0.13, y);
    ctx.ellipse(x, y, s * 0.13, s * 0.26, 0, 0, Math.PI * 2);
    ctx.moveTo(x - s * 0.1, y - s * 0.2);
    ctx.lineTo(x - s * 0.07, y - s * 0.4);
    ctx.lineTo(x - s * 0.01, y - s * 0.22);
    ctx.moveTo(x + s * 0.1, y - s * 0.2);
    ctx.lineTo(x + s * 0.07, y - s * 0.4);
    ctx.lineTo(x + s * 0.01, y - s * 0.22);
  }

  /** dark shapes on a dark sky need a rim of light, or they vanish */
  function silhouette(rim: string): void {
    ctx.strokeStyle = rim;
    ctx.lineWidth = 1.1;
    ctx.lineJoin = 'round';
    ctx.stroke();
    ctx.fillStyle = 'rgba(12,8,18,0.94)';
    ctx.fill();
  }

  function drawBats(t: number): void {
    for (const b of bats) {
      const flap = Math.sin(t * b.flapRate + b.ph);
      // against the moon a bat is pure black; out in the dark, lit at the edge
      const nearMoon = moon
        ? 1 - smoothstep(moonR * 0.8, moonR * 3, Math.hypot(b.x - moonX, b.y - moonY))
        : 0;
      const edge = Math.min(b.x + 16, w + 16 - b.x) / 24;
      ctx.globalAlpha = clamp(edge, 0, 1);
      batPath(b.x, b.y, b.size, flap);
      silhouette(`rgba(255,176,120,${(0.32 - 0.2 * nearMoon).toFixed(3)})`);
    }
    ctx.globalAlpha = 1;
  }

  /** the witch on her broom, flying right to left across the moon */
  function drawWitch(): void {
    if (!witch) return;
    const s = 11;
    const { x, y } = witch;
    const cape = Math.sin(witch.age * 6) * s * 0.08;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(0.06);
    ctx.beginPath();
    // the broom: handle out front (left, she flies left), bristles behind
    ctx.moveTo(-s * 1.35, -s * 0.02);
    ctx.lineTo(s * 0.9, s * 0.08);
    ctx.lineTo(s * 0.9, s * 0.18);
    ctx.lineTo(-s * 1.35, s * 0.06);
    ctx.closePath();
    ctx.moveTo(s * 0.82, s * 0.02);
    ctx.lineTo(s * 1.55, -s * 0.22);
    ctx.quadraticCurveTo(s * 1.72, s * 0.14, s * 1.55, s * 0.5);
    ctx.lineTo(s * 0.82, s * 0.24);
    ctx.closePath();
    // her cloak, sitting on the broom, leaning into the flight
    ctx.moveTo(s * 0.42, s * 0.12);
    ctx.quadraticCurveTo(s * 0.5, -s * 0.5, s * 0.02, -s * 0.86);
    ctx.lineTo(-s * 0.22, -s * 0.78);
    ctx.quadraticCurveTo(-s * 0.3, -s * 0.3, -s * 0.36, s * 0.1);
    ctx.closePath();
    // cape streaming back
    ctx.moveTo(s * 0.05, -s * 0.8);
    ctx.quadraticCurveTo(s * 0.75, -s * 0.78, s * 1.15, -s * 0.36 + cape);
    ctx.quadraticCurveTo(s * 0.7, -s * 0.28, s * 0.36, -s * 0.12);
    ctx.closePath();
    // head and the hat, its point bent back in the wind
    ctx.moveTo(-s * 0.02 + s * 0.17, -s * 0.98);
    ctx.arc(-s * 0.02, -s * 0.98, s * 0.17, 0, Math.PI * 2);
    ctx.moveTo(-s * 0.4, -s * 1.08);
    ctx.lineTo(s * 0.36, -s * 1.12);
    ctx.lineTo(s * 0.3, -s * 1.02);
    ctx.lineTo(-s * 0.4, -s * 1.0);
    ctx.closePath();
    ctx.moveTo(-s * 0.2, -s * 1.08);
    ctx.quadraticCurveTo(-s * 0.05, -s * 1.55, s * 0.45, -s * 1.8);
    ctx.quadraticCurveTo(s * 0.2, -s * 1.45, s * 0.2, -s * 1.1);
    ctx.closePath();
    // a pointed boot, forward
    ctx.moveTo(-s * 0.2, s * 0.08);
    ctx.lineTo(-s * 0.62, s * 0.36);
    ctx.lineTo(-s * 0.48, s * 0.42);
    ctx.lineTo(-s * 0.06, s * 0.18);
    ctx.closePath();
    const edge = clamp(Math.min(x + 30, w + 30 - x) / 30, 0, 1);
    ctx.globalAlpha = edge;
    silhouette('rgba(255,176,120,0.16)');
    ctx.restore();
    ctx.globalAlpha = 1;
  }

  function drawFlock(t: number): void {
    if (!flock) return;
    const dir = Math.sign(flock.vx);
    ctx.strokeStyle = 'rgba(238,224,206,0.42)';
    ctx.lineWidth = 1.1;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    const edge = clamp(Math.min(flock.x + 60, w + 60 - flock.x) / 50, 0, 1);
    ctx.globalAlpha = edge;
    for (const [dx, dy, ph] of flock.birds) {
      const x = flock.x + dx;
      const y = flock.y + dy;
      const s = 4.2;
      const flap = Math.sin(t * 5.5 + ph);
      ctx.beginPath();
      ctx.moveTo(x - s, y - flap * s * 0.5);
      ctx.quadraticCurveTo(x - s * 0.4, y - s * 0.3 - flap * s * 0.1, x + dir * s * 0.15, y);
      ctx.quadraticCurveTo(x + s * 0.4, y - s * 0.3 - flap * s * 0.1, x + s, y - flap * s * 0.5);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  function drawDrifters(): void {
    if (!drifters.length) return;
    for (const d of drifters) {
      if (id === 'christmas') {
        if (!flake) continue;
        ctx.globalAlpha = d.a;
        const dd = d.size * 2.6;
        ctx.drawImage(flake, d.x - dd / 2, d.y - dd / 2, dd, dd);
        continue;
      }
      const face = Math.cos(d.flip);
      const c = Math.cos(d.rot);
      const sn = Math.sin(d.rot);
      const sy = Math.max(0.1, Math.abs(face));
      ctx.setTransform(dpr * c, dpr * sn, dpr * -sn * sy, dpr * c * sy, dpr * d.x, dpr * d.y);
      if (id === 'new-year') {
        // a scrap of foil: it flashes when it turns to the light
        ctx.globalAlpha = d.a * (0.45 + 0.55 * Math.abs(face));
        ctx.fillStyle = `rgb(${CONFETTI_RGB[d.kind]})`;
        ctx.fillRect(-d.size * 0.5, -d.size, d.size, d.size * 2);
      } else {
        const s = leafArt[d.kind];
        if (!s) continue;
        ctx.globalAlpha = d.a * (0.6 + 0.4 * Math.abs(face));
        const lw = d.size * 1.75;
        ctx.drawImage(s, -lw / 2, -d.size / 2, lw, d.size);
      }
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = 1;
  }

  function drawRisers(t: number): void {
    if (!risers.length) return;
    ctx.save();
    for (const r of risers) {
      if (id === 'lunar-new-year') {
        // a paper sky lantern: glowing from inside, smaller as it climbs away
        const far = 1 - smoothstep(top, h, r.y);
        const s = r.size * lerp(1, 0.55, far);
        const fade = smoothstep(top - 20, top + skyH() * 0.25, r.y);
        const flick = 0.88 + 0.12 * Math.sin(t * 7 + r.ph) * Math.sin(t * 3.1 + r.ph);
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = 0.38 * fade * flick;
        if (ember) ctx.drawImage(ember, r.x - 18 * s, r.y - 18 * s, 36 * s, 36 * s);
        ctx.globalCompositeOperation = 'source-over';
        ctx.globalAlpha = 0.9 * fade;
        const lw = 7 * s;
        const lh = 9 * s;
        const g = ctx.createLinearGradient(r.x, r.y - lh / 2, r.x, r.y + lh / 2);
        g.addColorStop(0, 'rgba(255,150,80,1)');
        g.addColorStop(0.7, 'rgba(255,214,140,1)');
        g.addColorStop(1, 'rgba(255,240,190,1)');
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.moveTo(r.x - lw * 0.5, r.y - lh / 2);
        ctx.lineTo(r.x + lw * 0.5, r.y - lh / 2);
        ctx.lineTo(r.x + lw * 0.36, r.y + lh / 2);
        ctx.lineTo(r.x - lw * 0.36, r.y + lh / 2);
        ctx.closePath();
        ctx.fill();
      } else {
        // a will-o'-wisp: a mote of cold fire that wanders up and goes out
        const life = r.age / r.life;
        const a =
          Math.sin(Math.PI * clamp(life, 0, 1)) *
          (0.6 + 0.4 * Math.sin(t * 9 + r.ph) * Math.sin(t * 4.3 + r.ph));
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = 0.55 * a;
        if (ember)
          ctx.drawImage(ember, r.x - 7 * r.size, r.y - 7 * r.size, 14 * r.size, 14 * r.size);
        ctx.globalAlpha = 0.9 * a;
        if (glow) ctx.drawImage(glow, r.x - 1.4, r.y - 1.4, 2.8, 2.8);
      }
    }
    ctx.restore();
  }

  function drawGlints(): void {
    if (!glints.length || !glow) return;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = 'rgba(255,236,190,0.8)';
    ctx.lineWidth = 0.8;
    for (const g of glints) {
      const k = Math.sin(Math.PI * (g.age / g.life));
      const s = g.size * k;
      ctx.globalAlpha = 0.7 * k;
      ctx.drawImage(glow, g.x - 2, g.y - 2, 4, 4);
      ctx.beginPath();
      ctx.moveTo(g.x - s, g.y);
      ctx.lineTo(g.x + s, g.y);
      ctx.moveTo(g.x, g.y - s);
      ctx.lineTo(g.x, g.y + s);
      ctx.stroke();
    }
    ctx.restore();
  }

  return {
    resize(nw: number, nh: number, ntop: number, ndpr: number): void {
      const rebuild =
        !built || Math.abs(nw - w) > 2 || Math.abs(nh - h) > 2 || Math.abs(ntop - top) > 2;
      w = Math.max(1, nw);
      h = Math.max(1, nh);
      top = clamp(ntop, 0, h * 0.8);
      dpr = ndpr;
      if (rebuild) {
        build();
        built = true;
      }
    },
    update,
    back(t: number): void {
      if (wash) {
        ctx.fillStyle = wash;
        ctx.fillRect(0, top, w, h - top);
      }
      drawMoon(t);
      drawStar(t);
    },
    front(t: number): void {
      drawFog(t);
      drawFlock(t);
      drawDrifters();
      drawRisers(t);
      drawBats(t);
      drawWitch();
      drawGlints();
    },
    stats(): Record<string, number> {
      return {
        drifters: drifters.length,
        risers: risers.length,
        fogBanks: fog.length,
        bats: bats.length,
        witch: witch ? 1 : 0,
        witchX: witch ? witch.x : -1,
        flock: flock ? flock.birds.length : 0,
        moon: moon ? 1 : 0,
      };
    },
  };
}
