/**
 * the sidebar's weather scene. it paints the sky as it actually is: how
 * cloudy, day or night, where the sun sits, which way the wind blows and
 * how hard it gusts, how heavy the rain or snow falls.
 *
 * pure drawing: no dom, no timers. the caller owns the canvas and the
 * animation loop and calls frame(dt, t). the playground uses this file as is.
 *
 * layers, back to front: sky light (sun glow, rays, moon, stars), clouds,
 * fog, rain or snow, wind streamlines and leaves, lightning.
 *
 * everything stays faint. it sits behind the nav, so it's a mood, never
 * the subject.
 */

export type SkyKind = 'clear' | 'partly' | 'cloudy' | 'overcast' | 'fog';
export type PrecipKind = 'none' | 'rain' | 'snow';
export type Season = 'spring' | 'summer' | 'autumn' | 'winter';

export interface SceneConditions {
  sky: SkyKind;
  precip: PrecipKind;
  /** how hard it falls, 0..1 */
  intensity: number;
  thunder: boolean;
  isDay: boolean;
  /** 0..1 */
  cloudCover: number;
  windMph: number;
  gustMph: number;
  /** meteorological, the direction the wind comes FROM. null when unknown */
  windFromDeg: number | null;
  /** 0 at sunrise, 1 at sunset. null when unknown */
  dayPhase: number | null;
  /** 1 right at sunrise or sunset, 0 an hour or more away */
  twilight: number;
  season: Season;
}

/** the parts of the weather snapshot the scene reads */
export interface SceneWeatherInput {
  utc_offset_seconds: number;
  current: {
    weather_code: number | null;
    wind_speed?: number | null;
    wind_gusts?: number | null;
    wind_direction?: number | null;
    is_day?: boolean | null;
    cloud_cover?: number | null;
    precipitation?: number | null;
  };
  daily?: Array<{ sunrise?: string | null; sunset?: string | null }>;
}

/* ------------------------------------------------------------------ */
/* reading the weather                                                 */
/* ------------------------------------------------------------------ */

const RAIN_INTENSITY: Record<number, number> = {
  51: 0.2,
  53: 0.3,
  55: 0.42,
  56: 0.25,
  57: 0.42,
  61: 0.4,
  63: 0.65,
  65: 0.95,
  66: 0.45,
  67: 0.8,
  80: 0.45,
  81: 0.72,
  82: 1,
  95: 0.75,
  96: 0.85,
  99: 1,
};
const SNOW_INTENSITY: Record<number, number> = {
  71: 0.3,
  73: 0.58,
  75: 0.92,
  77: 0.25,
  85: 0.5,
  86: 0.9,
};
const THUNDER_CODES = new Set([95, 96, 99]);

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const smoothstep = (a: number, b: number, v: number) => {
  const k = clamp((v - a) / (b - a), 0, 1);
  return k * k * (3 - 2 * k);
};

export function seasonForMonth(month0: number, southern = false): Season {
  const m = southern ? (month0 + 6) % 12 : month0;
  if (m >= 2 && m <= 4) return 'spring';
  if (m >= 5 && m <= 7) return 'summer';
  if (m >= 8 && m <= 10) return 'autumn';
  return 'winter';
}

/** "2026-10-06T07:14" (location-local, no offset) -> minutes since midnight */
function localMinutes(iso: string | null | undefined): number | null {
  const m = /T(\d{2}):(\d{2})/.exec(iso ?? '');
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

/**
 * the snapshot -> what to paint. older cached snapshots don't carry the
 * scene fields, every one of them has a sane fallback.
 */
export function conditionsFromWeather(
  w: SceneWeatherInput,
  nowMs: number = Date.now(),
  latitude = 0,
): SceneConditions {
  const cur = w.current;
  const code = cur.weather_code ?? 0;
  const local = new Date(nowMs + (w.utc_offset_seconds || 0) * 1000);
  const nowMin = local.getUTCHours() * 60 + local.getUTCMinutes();

  let precip: PrecipKind = 'none';
  let intensity = 0;
  if (code in RAIN_INTENSITY) {
    precip = 'rain';
    intensity = RAIN_INTENSITY[code];
  } else if (code in SNOW_INTENSITY) {
    precip = 'snow';
    intensity = SNOW_INTENSITY[code];
  }
  // the measured amount sharpens the code's guess (mm in the last 15 min)
  if (precip !== 'none' && typeof cur.precipitation === 'number' && cur.precipitation > 0) {
    intensity = clamp(lerp(intensity, cur.precipitation / 4, 0.4), 0.15, 1);
  }

  let sky: SkyKind;
  if (code === 45 || code === 48) sky = 'fog';
  else if (precip !== 'none' || code === 3) sky = 'overcast';
  else if (code === 2) sky = 'partly';
  else if (code === 1) sky = 'clear';
  else sky = 'clear';

  const coverFallback: Record<SkyKind, number> = {
    clear: code === 1 ? 0.18 : 0.04,
    partly: 0.5,
    cloudy: 0.7,
    overcast: 0.92,
    fog: 0.7,
  };
  let cloudCover =
    typeof cur.cloud_cover === 'number' ? clamp(cur.cloud_cover / 100, 0, 1) : coverFallback[sky];
  if (sky === 'partly' && cloudCover > 0.75) sky = 'cloudy';

  const sunrise = localMinutes(w.daily?.[0]?.sunrise);
  const sunset = localMinutes(w.daily?.[0]?.sunset);
  const isDay =
    typeof cur.is_day === 'boolean'
      ? cur.is_day
      : sunrise != null && sunset != null
        ? nowMin >= sunrise && nowMin < sunset
        : nowMin >= 6 * 60 && nowMin < 19 * 60;

  let dayPhase: number | null = null;
  let twilight = 0;
  if (sunrise != null && sunset != null && sunset > sunrise) {
    dayPhase = clamp((nowMin - sunrise) / (sunset - sunrise), 0, 1);
    const nearest = Math.min(Math.abs(nowMin - sunrise), Math.abs(nowMin - sunset));
    twilight = 1 - smoothstep(0, 60, nearest);
  }
  if (precip === 'rain' || precip === 'snow') cloudCover = Math.max(cloudCover, 0.8);

  return {
    sky,
    precip,
    intensity,
    thunder: THUNDER_CODES.has(code),
    isDay,
    cloudCover,
    windMph: Math.max(0, cur.wind_speed ?? 0),
    gustMph: Math.max(cur.wind_gusts ?? 0, cur.wind_speed ?? 0),
    windFromDeg: typeof cur.wind_direction === 'number' ? cur.wind_direction : null,
    dayPhase,
    twilight,
    season: seasonForMonth(local.getUTCMonth(), latitude < 0),
  };
}

/**
 * the sidebar is a side view: rain falls down, so only the wind's east-west
 * part moves things across. +1 blows left to right. a north or south wind
 * still drifts (never a dead-still sideways 0), it just leans one way less.
 */
export function windSide(fromDeg: number | null): number {
  if (fromDeg == null) return 1;
  const toward = ((fromDeg + 180) * Math.PI) / 180;
  const east = Math.sin(toward);
  const sign = east < 0 ? -1 : 1;
  return sign * lerp(0.45, 1, Math.abs(east));
}

/** 0..1, how much the wind should show. calm under ~8 mph */
export function windStrength(c: Pick<SceneConditions, 'windMph' | 'gustMph'>): number {
  return clamp((Math.max(c.windMph, c.gustMph * 0.7) - 8) / 28, 0, 1);
}

/**
 * gusts: slow swells between the steady wind and the gust speed, with calm
 * stretches between. 0..1, smooth, never periodic to the eye (two
 * incommensurate waves).
 */
export function gustEnvelope(t: number): number {
  const raw = 0.5 + 0.5 * (0.62 * Math.sin(t * 0.37) + 0.38 * Math.sin(t * 0.83 + 1.3));
  return Math.pow(smoothstep(0.38, 1, raw), 1.3);
}

/* ------------------------------------------------------------------ */
/* the engine                                                          */
/* ------------------------------------------------------------------ */

export interface WeatherSceneEngine {
  resize(w: number, h: number, dpr: number): void;
  /** dt and t in seconds */
  frame(dt: number, t: number): void;
  /** particle counts, for tests and the playground */
  stats(): Record<string, number>;
}

type Ctx = CanvasRenderingContext2D;
type Sprite = HTMLCanvasElement | null;
type Rng = () => number;

/** a reference sidebar is 260 x 900; smaller ones get fewer particles */
function areaScale(w: number, h: number): number {
  return clamp(Math.sqrt((w * h) / (260 * 900)), 0.4, 1.25);
}

function bake(w: number, h: number, paint: (g: Ctx) => void): Sprite {
  if (typeof document === 'undefined') return null;
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const g = c.getContext('2d');
  if (!g) return null;
  paint(g);
  return c;
}

function softDot(rgb: string): Sprite {
  return bake(32, 32, (g) => {
    const grad = g.createRadialGradient(16, 16, 0, 16, 16, 16);
    grad.addColorStop(0, `rgba(${rgb},1)`);
    grad.addColorStop(0.35, `rgba(${rgb},0.6)`);
    grad.addColorStop(1, `rgba(${rgb},0)`);
    g.fillStyle = grad;
    g.fillRect(0, 0, 32, 32);
  });
}

/**
 * stamp soft puffs, then blur the lot so no single puff shows. a cloud you
 * can count the circles in looks cheap. `flat` squashes it toward a
 * stratus band (overcast, fog). where canvas filters aren't supported the
 * puffs are already soft, it's just a little lumpier
 */
function puffSprite(
  rgb: string,
  rng: Rng,
  w: number,
  h: number,
  puffs: number,
  flat: number,
  blur: number,
): Sprite {
  const raw = bake(w, h, (g) => {
    for (let i = 0; i < puffs; i++) {
      const a = rng() * Math.PI * 2;
      const rr = Math.sqrt(rng());
      const x = w / 2 + Math.cos(a) * rr * w * 0.34;
      const y = h * 0.55 + Math.sin(a) * rr * h * 0.16 * flat - (1 - rr) * h * 0.1 * flat;
      const r = lerp(h * 0.18, h * 0.36, rng()) * (1 - rr * 0.3);
      g.save();
      g.translate(x, y);
      // the gradient lives in the translated space, centered on the puff
      const grad = g.createRadialGradient(0, 0, 0, 0, 0, r);
      grad.addColorStop(0, `rgba(${rgb},0.5)`);
      grad.addColorStop(0.55, `rgba(${rgb},0.2)`);
      grad.addColorStop(1, `rgba(${rgb},0)`);
      g.fillStyle = grad;
      g.scale(1, lerp(1, 0.55, 1 - flat));
      g.fillRect(-r, -r, r * 2, r * 2);
      g.restore();
    }
  });
  if (!raw) return null;
  return bake(w, h, (g) => {
    if ('filter' in g) g.filter = `blur(${blur}px)`;
    g.drawImage(raw, 0, 0);
  });
}

function cloudSprite(rgb: string, rng: Rng, stratus: boolean): Sprite {
  return stratus
    ? puffSprite(rgb, rng, 420, 120, 26, 0.45, 10)
    : puffSprite(rgb, rng, 360, 150, 22, 1, 8);
}

/** a long low fog bank */
function fogSprite(rgb: string, rng: Rng): Sprite {
  return puffSprite(rgb, rng, 520, 110, 30, 0.3, 12);
}

/** a rain streak: transparent tail fading into a bright head at the bottom */
function streakSprite(rgb: string): Sprite {
  return bake(4, 64, (g) => {
    const grad = g.createLinearGradient(0, 0, 0, 64);
    grad.addColorStop(0, `rgba(${rgb},0)`);
    grad.addColorStop(0.7, `rgba(${rgb},0.55)`);
    grad.addColorStop(1, `rgba(${rgb},1)`);
    g.fillStyle = grad;
    g.fillRect(1, 0, 2, 64);
  });
}

/** a leaf, pointed both ends, with a midrib. drawn edge-on it flips */
function leafSprite(fill: string, rib: string): Sprite {
  return bake(28, 16, (g) => {
    g.beginPath();
    g.moveTo(2, 8);
    g.quadraticCurveTo(12, -1, 26, 8);
    g.quadraticCurveTo(12, 17, 2, 8);
    g.closePath();
    g.fillStyle = fill;
    g.fill();
    g.strokeStyle = rib;
    g.lineWidth = 0.8;
    g.beginPath();
    g.moveTo(3, 8);
    g.lineTo(25, 8);
    g.stroke();
  });
}

const LEAF_COLORS: Record<Season, Array<[string, string]>> = {
  autumn: [
    ['rgb(205,128,62)', 'rgba(120,60,20,0.7)'],
    ['rgb(214,164,72)', 'rgba(130,90,30,0.7)'],
    ['rgb(176,84,52)', 'rgba(100,40,20,0.7)'],
  ],
  spring: [
    ['rgb(150,186,112)', 'rgba(70,100,50,0.7)'],
    ['rgb(232,196,206)', 'rgba(150,110,120,0.6)'], // a petal now and then
  ],
  summer: [
    ['rgb(126,164,98)', 'rgba(60,90,45,0.7)'],
    ['rgb(156,178,104)', 'rgba(80,100,50,0.7)'],
  ],
  winter: [['rgb(150,128,104)', 'rgba(90,70,50,0.7)']],
};

interface Drop {
  x: number;
  y: number;
  vx: number;
  vy: number;
  len: number;
  a: number;
  near: boolean;
  ph: number;
}

interface Stream {
  x: number;
  y: number;
  /** heading, radians. eases toward the flow instead of snapping to it */
  heading: number;
  age: number;
  life: number;
  speed: number;
  /** radians left to turn in a curl, 0 when riding straight */
  curlLeft: number;
  curlRate: number;
  /** ring of the last TRAIL positions, x0,y0,x1,y1,... */
  trail: Float32Array;
  head: number;
  count: number;
  /** seconds since the last trail point, so trails don't depend on fps */
  since: number;
}

interface Leaf {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  spin: number;
  rot: number;
  flip: number;
  flipRate: number;
  sprite: number;
}

interface Cloud {
  x: number;
  y: number;
  scale: number;
  a: number;
  speed: number;
  sprite: number;
}

interface Star {
  x: number;
  y: number;
  r: number;
  a: number;
  rate: number;
  ph: number;
}

interface Satellite {
  x: number;
  y: number;
  vx: number;
  vy: number;
  age: number;
  /** seconds between glints */
  period: number;
  ph: number;
  glint?: number;
}

/** the bright ones you can pick out without a telescope */
const PLANETS: Array<[string, string]> = [
  ['venus', '255,244,214'],
  ['jupiter', '246,232,204'],
  ['mars', '255,178,140'],
];

const TRAIL = 72;
/** a trail point every 1/60 s, whatever the display's refresh rate */
const TRAIL_STEP = 1 / 60;
const STREAM_RGB = '214,226,244';

export function createWeatherScene(
  ctx: Ctx,
  cond: SceneConditions,
  rng: Rng = Math.random,
): WeatherSceneEngine {
  let w = 260;
  let h = 900;
  let dpr = 1;

  const rand = (a: number, b: number) => a + rng() * (b - a);
  const side = windSide(cond.windFromDeg);
  const strength = windStrength(cond);
  const gustRatio = clamp(cond.gustMph / Math.max(cond.windMph, 1), 1, 2.4);
  const night = !cond.isDay;
  const storm = cond.thunder;

  // sprites, baked once per scene
  const cloudRgb = storm
    ? '96,104,124'
    : night
      ? '118,130,158'
      : cond.precip !== 'none'
        ? '150,160,178'
        : '214,222,236';
  const stratus = cond.sky === 'overcast' || cond.sky === 'fog' || cond.precip !== 'none';
  const clouds: Sprite[] = [0, 1, 2].map(() => cloudSprite(cloudRgb, rng, stratus));
  const cloudW = stratus ? 420 : 360;
  const cloudH = stratus ? 120 : 150;
  const fog = cond.sky === 'fog' ? fogSprite(night ? '150,160,180' : '205,210,220', rng) : null;
  const streakFar = cond.precip === 'rain' ? streakSprite('150,184,224') : null;
  const streakNear = cond.precip === 'rain' ? streakSprite('186,212,242') : null;
  const flake = cond.precip === 'snow' ? softDot('238,244,252') : null;
  const starDot = night ? softDot('226,234,255') : null;
  const leafPalette = LEAF_COLORS[cond.season];
  const leaves: Sprite[] = strength > 0 ? leafPalette.map(([f, r]) => leafSprite(f, r)) : [];

  let cloudList: Cloud[] = [];
  let fogBanks: Cloud[] = [];
  let drops: Drop[] = [];
  let streams: Stream[] = [];
  let leafList: Leaf[] = [];
  let stars: Star[] = [];
  let shooting: { x: number; y: number; vx: number; vy: number; age: number } | null = null;
  let nextShooting = rand(8, 20);
  // a clear night's two quiet wanderers: a planet that holds still and
  // doesn't twinkle (that's how you tell it from a star), and a satellite
  // gliding over, glinting as it tumbles
  let planet: { x: number; y: number; sprite: Sprite } | null = null;
  let satellite: Satellite | null = null;
  let nextSatellite = rand(2, 6);
  let satelliteGlow = 0;
  let flash = 0; // lightning, 0..1
  let flashX = 0.5;
  let nextFlash = rand(4, 9);
  let flashSeq: number[] = [];
  let built = false;
  let gustNow = 1;
  /** summed sideways travel of the wind ribbons, for tests and the playground */
  let driftX = 0;
  // light that never changes within a scene: built once per size, not per frame
  let lightGlow: CanvasGradient | null = null;
  let ceiling: CanvasGradient | null = null;
  let mist: CanvasGradient | null = null;
  let sunX = 0;
  let sunY = 0;
  let sunRgb = '255,208,150';

  /* ---------------- build ---------------- */

  function build(): void {
    const k = areaScale(w, h);
    const cover = cond.cloudCover;

    // clouds: count and weight follow the real cover. two depths: far ones
    // smaller, dimmer and slower, so the sky has room in it
    const nClouds = cover < 0.08 ? 0 : Math.round(lerp(1, 8, cover) * Math.sqrt(k));
    cloudList = [];
    for (let i = 0; i < nClouds; i++) {
      const far = i % 2 === 1;
      cloudList.push({
        x: rand(-0.4, 1.1) * w,
        y: rand(-0.06, far ? 0.32 : 0.55) * h,
        scale: far ? rand(0.45, 0.7) : rand(0.75, 1.15),
        a: (far ? rand(0.08, 0.12) : rand(0.12, 0.19)) * lerp(0.75, 1.15, cover),
        speed: (far ? 0.55 : 1) * (3 + cond.windMph * 0.55),
        sprite: i % 3,
      });
    }

    fogBanks = [];
    if (fog) {
      for (let i = 0; i < 6; i++) {
        fogBanks.push({
          x: rand(-0.6, 0.6) * w,
          y: lerp(0.08, 0.98, i / 5 + rand(-0.04, 0.04)) * h,
          scale: rand(0.8, 1.3),
          a: rand(0.14, 0.22),
          speed: rand(3, 7) * (i % 2 ? 1 : 0.6),
          sprite: 0,
        });
      }
    }

    drops = [];
    if (cond.precip === 'rain') {
      const n = Math.round(lerp(24, 120, cond.intensity) * k);
      const lean = side * Math.min(cond.windMph * 6, 240);
      for (let i = 0; i < n; i++) {
        const near = rng() < 0.4;
        const fall = (near ? rand(600, 780) : rand(420, 540)) * (0.85 + 0.3 * cond.intensity);
        drops.push({
          x: rand(-60, w + 60),
          y: rand(-h * 0.1, h),
          vx: lean * (near ? 1 : 0.8) * rand(0.9, 1.1),
          vy: fall,
          len: near ? rand(20, 34) + cond.intensity * 16 : rand(10, 18) + cond.intensity * 8,
          a: near ? rand(0.16, 0.28) : rand(0.08, 0.16),
          near,
          ph: 0,
        });
      }
    } else if (cond.precip === 'snow') {
      const n = Math.round(lerp(26, 104, cond.intensity) * k);
      const drift = side * Math.min(cond.windMph * 1.8, 80);
      for (let i = 0; i < n; i++) {
        const near = rng() < 0.32;
        drops.push({
          x: rand(0, w),
          y: rand(0, h),
          vx: drift * (near ? 1 : 0.6) + rand(-6, 6),
          vy: near ? rand(26, 50) : rand(12, 26),
          len: near ? rand(1.8, 3.4) : rand(0.7, 1.4), // radius
          a: near ? rand(0.3, 0.55) : rand(0.16, 0.32),
          near,
          ph: rand(0, Math.PI * 2),
        });
      }
    }

    // wind: a handful of long ribbons in a breeze, more when it really
    // blows. dry weather only: under rain or snow the slant and the drift
    // already say which way and how hard, and ribbons across streaks clash
    streams = [];
    if (strength > 0 && cond.precip === 'none') {
      const n = Math.round(lerp(6, 18, strength) * k);
      for (let i = 0; i < n; i++) streams.push(newStream(true));
    }

    leafList = [];
    if (strength > 0.38 && leaves.length && cond.precip === 'none') {
      const n = Math.round(lerp(1, 5, smoothstep(0.38, 1, strength)) * Math.sqrt(k));
      for (let i = 0; i < n; i++) leafList.push(newLeaf(true));
    }

    stars = [];
    planet = null;
    if (night && cond.cloudCover < 0.6) {
      const [, rgb] = PLANETS[Math.floor(rng() * PLANETS.length)];
      planet = { x: rand(0.18, 0.82) * w, y: rand(0.07, 0.3) * h, sprite: softDot(rgb) };
    }
    if (night) {
      const clearness = 1 - smoothstep(0.2, 0.95, cond.cloudCover);
      const n = Math.round(72 * k * clearness);
      for (let i = 0; i < n; i++) {
        const bright = rng() < 0.16;
        stars.push({
          x: rand(0, w),
          y: Math.pow(rng(), 1.5) * h * 0.82, // thicker toward the top
          r: bright ? rand(1.8, 2.8) : rand(0.7, 1.4),
          a: bright ? rand(0.6, 0.95) : rand(0.3, 0.6),
          rate: rand(0.4, 1.4),
          ph: rand(0, Math.PI * 2),
        });
      }
    }
  }

  function newStream(anywhere: boolean): Stream {
    const s: Stream = {
      x: anywhere ? rand(0, w) : side > 0 ? rand(-30, -5) : rand(w + 5, w + 30),
      y: rand(0.02, 0.98) * h,
      heading: side > 0 ? 0 : Math.PI,
      age: anywhere ? rand(0, 2.5) : 0,
      life: rand(3.2, 6),
      speed: rand(0.8, 1.2),
      curlLeft: 0,
      curlRate: 0,
      trail: new Float32Array(TRAIL * 2),
      head: 0,
      count: 0,
      since: 0,
    };
    return s;
  }

  function newLeaf(anywhere: boolean): Leaf {
    return {
      x: anywhere ? rand(0, w) : side > 0 ? -16 : w + 16,
      y: rand(0.05, 0.95) * h,
      vx: 0,
      vy: 0,
      size: rand(10, 15),
      spin: rand(-1.4, 1.4),
      rot: rand(0, Math.PI * 2),
      flip: rand(0, Math.PI * 2),
      flipRate: rand(2.2, 5),
      sprite: Math.floor(rng() * leaves.length),
    };
  }

  /* ---------------- the wind field ---------------- */

  /** flow angle at a point: along the wind, with long slow swells that
   * flatten out the harder it blows */
  function flowAngle(x: number, y: number, t: number): number {
    const base = side > 0 ? 0 : Math.PI;
    const wander = lerp(0.62, 0.3, strength);
    const n =
      0.55 * Math.sin(y * 0.0062 + x * 0.0105 - t * 0.42) +
      0.3 * Math.sin(x * 0.0043 - y * 0.0089 + t * 0.23 + 2.1) +
      0.15 * Math.sin((x + y) * 0.017 + t * 0.61);
    return base + n * wander;
  }

  /** shortest signed turn from a to b */
  function turn(a: number, b: number): number {
    let d = (b - a) % (Math.PI * 2);
    if (d > Math.PI) d -= Math.PI * 2;
    if (d < -Math.PI) d += Math.PI * 2;
    return d;
  }

  /* ---------------- update ---------------- */

  function update(dt: number, t: number): void {
    const gust = gustEnvelope(t);
    const gustSpeed = 1 + (gustRatio - 1) * gust;
    gustNow = gustSpeed;

    for (const c of cloudList) {
      c.x += side * c.speed * gustSpeed * dt;
      const span = cloudW * c.scale;
      if (side > 0 && c.x > w + span * 0.2) c.x = -span;
      if (side < 0 && c.x < -span) c.x = w + span * 0.2;
    }
    for (const f of fogBanks) {
      f.x += side * f.speed * dt;
      const span = 520 * f.scale;
      if (f.x > w + 40) f.x = -span + 40;
      if (f.x < -span - 40) f.x = w - 40;
    }

    if (cond.precip === 'rain') {
      for (const d of drops) {
        d.x += d.vx * gustSpeed * dt;
        d.y += d.vy * dt;
        if (d.y - d.len > h) {
          d.y = rand(-60, -10);
          d.x = rand(-60, w + 60) - d.vx * 0.4;
        }
      }
    } else if (cond.precip === 'snow') {
      for (const d of drops) {
        const sway = Math.sin(t * (d.near ? 0.9 : 0.6) + d.ph) * (d.near ? 12 : 6);
        d.x += (d.vx * gustSpeed + sway) * dt;
        d.y += d.vy * dt;
        if (d.y > h + 6) {
          d.y = -6;
          d.x = rand(-20, w + 20);
        }
        if (d.x > w + 10) d.x = -10;
        if (d.x < -10) d.x = w + 10;
      }
    }

    // the sidebar is narrow: slow ribbons read as air, fast ones as noise.
    // length, not speed, is what says how hard it blows
    const base = (28 + cond.windMph * 3.1) * gustSpeed;
    for (let i = 0; i < streams.length; i++) {
      const s = streams[i];
      s.age += dt;
      if (s.curlLeft > 0) {
        // a gust curls a ribbon into one loop, the classic wind stroke
        const step = Math.abs(s.curlRate) * dt;
        s.heading += Math.sign(s.curlRate) * Math.min(step, s.curlLeft);
        s.curlLeft = Math.max(0, s.curlLeft - step);
      } else {
        s.heading += turn(s.heading, flowAngle(s.x, s.y, t)) * Math.min(1, dt * 2.4);
        if (
          gust > 0.55 &&
          s.age > 0.8 &&
          s.age < s.life - 2 &&
          rng() < dt * 0.05 * (0.5 + strength)
        ) {
          s.curlLeft = Math.PI * 2;
          // curl up and over, against the direction of travel
          s.curlRate = (side > 0 ? -1 : 1) * rand(2.6, 3.4);
        }
      }
      const v = base * s.speed;
      driftX += Math.cos(s.heading) * v * dt;
      s.x += Math.cos(s.heading) * v * dt;
      s.y += Math.sin(s.heading) * v * dt;
      s.since += dt;
      if (s.since >= TRAIL_STEP || s.count === 0) {
        s.since = Math.min(s.since - TRAIL_STEP, TRAIL_STEP);
        s.trail[s.head * 2] = s.x;
        s.trail[s.head * 2 + 1] = s.y;
        s.head = (s.head + 1) % TRAIL;
        s.count = Math.min(TRAIL, s.count + 1);
      }
      const gone = side > 0 ? s.x > w + 60 : s.x < -60;
      if (s.age > s.life || gone || s.y < -60 || s.y > h + 60) streams[i] = newStream(false);
    }

    for (let i = 0; i < leafList.length; i++) {
      const l = leafList[i];
      const ang = flowAngle(l.x, l.y, t);
      const v = base * 0.8;
      // leaves lag the air and bob, they don't ride it like a rail
      l.vx += (Math.cos(ang) * v - l.vx) * Math.min(1, dt * 1.6);
      l.vy +=
        (Math.sin(ang) * v * 0.6 + Math.sin(t * 1.7 + l.flip) * 18 - l.vy) * Math.min(1, dt * 1.2);
      l.x += l.vx * dt;
      l.y += l.vy * dt;
      l.rot += l.spin * gustSpeed * dt;
      l.flip += l.flipRate * gustSpeed * dt;
      const gone = side > 0 ? l.x > w + 20 : l.x < -20;
      if (gone || l.y > h + 20 || l.y < -20) leafList[i] = newLeaf(false);
    }

    if (night && stars.length && cond.cloudCover < 0.5) {
      nextShooting -= dt;
      if (!shooting && nextShooting <= 0) {
        const dir = rng() < 0.5 ? -1 : 1;
        shooting = {
          x: rand(0.2, 0.8) * w,
          y: rand(0.02, 0.3) * h,
          vx: dir * rand(260, 340),
          vy: rand(110, 160),
          age: 0,
        };
        nextShooting = rand(22, 55);
      }
      if (shooting) {
        shooting.age += dt;
        shooting.x += shooting.vx * dt;
        shooting.y += shooting.vy * dt;
        if (shooting.age > 0.95) shooting = null;
      }

      nextSatellite -= dt;
      if (!satellite && nextSatellite <= 0) {
        const dir = rng() < 0.5 ? -1 : 1;
        const speed = (w + 40) / rand(22, 34); // a slow, steady crossing
        satellite = {
          x: dir > 0 ? -10 : w + 10,
          y: rand(0.05, 0.55) * h,
          vx: dir * speed,
          vy: speed * rand(-0.14, 0.14),
          age: 0,
          period: rand(1.6, 3),
          ph: rand(0, 3),
        };
      }
      satelliteGlow = 0;
      if (satellite) {
        const sat = satellite;
        sat.age += dt;
        sat.x += sat.vx * dt;
        sat.y += sat.vy * dt;
        if (sat.x < -20 || sat.x > w + 20 || sat.y < -20 || sat.y > h + 20) {
          satellite = null;
          nextSatellite = rand(18, 40);
        } else {
          // a glint every period: a quick swell and back, like sunlight
          // catching a tumbling panel
          const p = (sat.age + sat.ph) % sat.period;
          const near = Math.min(p, sat.period - p);
          const glint = Math.exp(-((near / 0.1) ** 2));
          const edge = Math.min(sat.x + 10, w + 10 - sat.x) / 30;
          satelliteGlow = (0.4 + 0.6 * glint) * clamp(edge, 0, 1) * smoothstep(0, 1.2, sat.age);
          sat.glint = glint;
        }
      }
    }

    if (storm) {
      nextFlash -= dt;
      if (nextFlash <= 0 && flashSeq.length === 0) {
        // a flicker, a dip, then the main flash. soft, never a strobe
        flashSeq = [0.55, 0.12, 1];
        flashX = rand(0.15, 0.85);
        nextFlash = rand(7, 18);
      }
      if (flashSeq.length && flash < 0.04) flash = flashSeq.shift() ?? 0;
      flash *= Math.exp(-dt * (flashSeq.length ? 14 : 4.5));
    }
  }

  /* ---------------- draw ---------------- */

  function drawSprite(s: Sprite, x: number, y: number, sw: number, sh: number): void {
    if (s) ctx.drawImage(s, x, y, sw, sh);
  }

  function drawSky(t: number): void {
    const cover = cond.cloudCover;
    if (!night) {
      // the sun's light: warm glow at the top, sliding with the day.
      // gold at noon, rose near sunrise and sunset, muted under cloud
      const sx = sunX;
      const sy = sunY;
      const warm = cond.twilight;
      const rgb = sunRgb;
      if (lightGlow) {
        ctx.fillStyle = lightGlow;
        ctx.fillRect(0, 0, w, h);
      }

      // rays, only when the sun actually gets through
      const rays = 1 - smoothstep(0.25, 0.6, cover);
      if (rays > 0.01) {
        ctx.save();
        ctx.translate(sx, sy);
        ctx.globalCompositeOperation = 'lighter';
        const len = h * 0.75;
        for (let i = 0; i < 7; i++) {
          const ang = Math.PI / 2 + (i - 3) * 0.2 + Math.sin(t * 0.05 + i * 1.7) * 0.06;
          const spread = 0.035 + (i % 3) * 0.012;
          const pulse = 0.6 + 0.4 * Math.sin(t * 0.21 + i * 2.3);
          const a = 0.022 * rays * pulse * (1 + warm * 0.4);
          const lg = ctx.createLinearGradient(0, 0, Math.cos(ang) * len, Math.sin(ang) * len);
          lg.addColorStop(0, `rgba(${rgb},${a.toFixed(4)})`);
          lg.addColorStop(1, `rgba(${rgb},0)`);
          ctx.fillStyle = lg;
          ctx.beginPath();
          ctx.moveTo(0, 0);
          ctx.lineTo(Math.cos(ang - spread) * len, Math.sin(ang - spread) * len);
          ctx.lineTo(Math.cos(ang + spread) * len, Math.sin(ang + spread) * len);
          ctx.closePath();
          ctx.fill();
        }
        ctx.restore();
      }
    } else {
      if (lightGlow) {
        ctx.fillStyle = lightGlow;
        ctx.fillRect(0, 0, w, h);
      }

      if (starDot && stars.length) {
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        for (const s of stars) {
          const tw = 0.62 + 0.38 * Math.sin(t * s.rate + s.ph);
          ctx.globalAlpha = s.a * tw;
          const d = s.r * 2.4;
          drawSprite(starDot, s.x - d / 2, s.y - d / 2, d, d);
        }
        if (planet?.sprite) {
          // steady: a planet's light doesn't twinkle, only breathes a touch
          const clear = 1 - smoothstep(0.2, 0.6, cond.cloudCover);
          const breath = 0.95 + 0.05 * Math.sin(t * 0.3);
          ctx.globalAlpha = 0.34 * clear * breath;
          drawSprite(planet.sprite, planet.x - 12, planet.y - 12, 24, 24);
          ctx.globalAlpha = clear * breath;
          drawSprite(planet.sprite, planet.x - 3.2, planet.y - 3.2, 6.4, 6.4);
        }
        if (satellite && satelliteGlow > 0.01) {
          const g = satellite.glint ?? 0;
          // between glints a faint steady point; at a glint it flares
          // brighter than any star, with a brief halo
          if (g > 0.05) {
            const halo = 6 + 12 * g;
            ctx.globalAlpha = satelliteGlow * 0.45 * g;
            drawSprite(starDot, satellite.x - halo / 2, satellite.y - halo / 2, halo, halo);
          }
          const d = 2.2 + 3.4 * g;
          ctx.globalAlpha = Math.min(1, satelliteGlow * (1 + 0.4 * g));
          drawSprite(starDot, satellite.x - d / 2, satellite.y - d / 2, d, d);
        }
        if (shooting) {
          const life = shooting.age / 0.95;
          const a = Math.sin(Math.PI * life) * 0.55;
          const m = Math.hypot(shooting.vx, shooting.vy);
          const tx = shooting.x - (shooting.vx / m) * 80;
          const ty = shooting.y - (shooting.vy / m) * 80;
          const lg = ctx.createLinearGradient(shooting.x, shooting.y, tx, ty);
          lg.addColorStop(0, `rgba(236,242,255,${a.toFixed(3)})`);
          lg.addColorStop(1, 'rgba(236,242,255,0)');
          ctx.globalAlpha = 1;
          ctx.strokeStyle = lg;
          ctx.lineWidth = 1.1;
          ctx.lineCap = 'round';
          ctx.beginPath();
          ctx.moveTo(shooting.x, shooting.y);
          ctx.lineTo(tx, ty);
          ctx.stroke();
        }
        ctx.restore();
        ctx.globalAlpha = 1;
      }
    }

    if (ceiling) {
      ctx.fillStyle = ceiling;
      ctx.fillRect(0, 0, w, h);
    }
  }

  /** the light that holds still: sun or moon glow, a heavy sky's ceiling,
   * the mist under a downpour. built once per size */
  function buildLight(): void {
    const cover = cond.cloudCover;
    if (!night) {
      // the sun's light: warm glow at the top, sliding with the day.
      // gold at noon, rose near sunrise and sunset, muted under cloud
      const warm = cond.twilight;
      sunX = lerp(0.12, 0.88, cond.dayPhase ?? 0.5) * w;
      sunY = -0.04 * h;
      sunRgb = `255,${Math.round(lerp(208, 150, warm))},${Math.round(lerp(150, 122, warm))}`;
      const glow = lerp(0.16, 0.035, smoothstep(0.1, 0.95, cover)) * (1 + warm * 0.5);
      const r = Math.max(w, h * 0.45) * 1.2;
      lightGlow = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, r);
      lightGlow.addColorStop(0, `rgba(${sunRgb},${glow.toFixed(3)})`);
      lightGlow.addColorStop(0.45, `rgba(${sunRgb},${(glow * 0.35).toFixed(3)})`);
      lightGlow.addColorStop(1, `rgba(${sunRgb},0)`);
    } else {
      // moonlight, a cool soft glow up top
      const mx = 0.24 * w;
      const my = 0.06 * h;
      const a = lerp(0.085, 0.03, cover);
      lightGlow = ctx.createRadialGradient(mx, my, 0, mx, my, w * 0.95);
      lightGlow.addColorStop(0, `rgba(176,196,240,${a.toFixed(3)})`);
      lightGlow.addColorStop(1, 'rgba(176,196,240,0)');
    }
    // a heavy sky presses down from the top
    ceiling = null;
    if (cover > 0.55) {
      const a = (cover - 0.55) * (storm ? 0.5 : 0.3);
      const rgb = storm ? '28,32,44' : night ? '34,40,56' : '70,80,98';
      ceiling = ctx.createLinearGradient(0, 0, 0, h * 0.6);
      ceiling.addColorStop(0, `rgba(${rgb},${a.toFixed(3)})`);
      ceiling.addColorStop(1, `rgba(${rgb},0)`);
    }
    // heavy rain mists up near the bottom
    mist = null;
    if (cond.precip === 'rain' && cond.intensity > 0.5) {
      const a = (cond.intensity - 0.5) * 0.12;
      mist = ctx.createLinearGradient(0, h * 0.6, 0, h);
      mist.addColorStop(0, 'rgba(170,190,215,0)');
      mist.addColorStop(1, `rgba(170,190,215,${a.toFixed(3)})`);
    }
  }

  function drawClouds(): void {
    for (const c of cloudList) {
      const s = clouds[c.sprite];
      if (!s) continue;
      ctx.globalAlpha = c.a;
      drawSprite(s, c.x, c.y, cloudW * c.scale, cloudH * c.scale);
    }
    ctx.globalAlpha = 1;
  }

  function drawFog(t: number): void {
    if (!fog) return;
    ctx.fillStyle = night ? 'rgba(150,160,180,0.045)' : 'rgba(205,210,220,0.055)';
    ctx.fillRect(0, 0, w, h);
    for (const f of fogBanks) {
      ctx.globalAlpha = f.a * (0.75 + 0.25 * Math.sin(t * 0.13 + f.y));
      drawSprite(fog, f.x, f.y - 55 * f.scale, 520 * f.scale, 110 * f.scale);
    }
    ctx.globalAlpha = 1;
  }

  function drawRain(): void {
    if (cond.precip !== 'rain') return;
    for (const d of drops) {
      const sprite = d.near ? streakNear : streakFar;
      if (!sprite) continue;
      const vx = d.vx * gustNow;
      const m = Math.hypot(vx, d.vy) || 1;
      const ux = vx / m;
      const uy = d.vy / m;
      const lw = d.near ? 1.3 : 0.9;
      ctx.globalAlpha = d.a;
      // the sprite's +y runs tail to head; turn it to the drop's heading
      ctx.setTransform(dpr * uy, dpr * -ux, dpr * ux, dpr * uy, dpr * d.x, dpr * d.y);
      ctx.drawImage(sprite, -lw, -d.len, lw * 2, d.len);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = 1;
    if (mist) {
      ctx.fillStyle = mist;
      ctx.fillRect(0, h * 0.6, w, h * 0.4);
    }
  }

  function drawSnow(): void {
    if (cond.precip !== 'snow' || !flake) return;
    for (const d of drops) {
      ctx.globalAlpha = d.a;
      const dd = d.len * 2.6;
      drawSprite(flake, d.x - dd / 2, d.y - dd / 2, dd, dd);
    }
    ctx.globalAlpha = 1;
  }

  function drawWind(t: number): void {
    if (!streams.length && !leafList.length) return;
    const gust = gustEnvelope(t);
    const base = lerp(0.13, 0.27, strength) * (0.75 + 0.45 * gust);
    const maxW = lerp(1.1, 1.7, strength);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    // each ribbon is a brushstroke: hairline at the tail, fullest just
    // behind the head, drawing back to a point at the tip. drawn in chunks
    // old to new; the life fade keeps it from popping in or out
    const CHUNKS = 10;
    const per = Math.ceil(TRAIL / CHUNKS);
    for (const s of streams) {
      if (s.count < 3) continue;
      const life = smoothstep(0, 0.8, s.age) * (1 - smoothstep(s.life - 1.1, s.life, s.age));
      if (life <= 0.01) continue;
      const start = (s.head - s.count + TRAIL) % TRAIL;
      for (let c = 0; c < CHUNKS; c++) {
        const from = c * per;
        const to = Math.min(s.count - 1, from + per);
        if (to <= from) continue;
        const k = (c + 1) / CHUNKS;
        const body = Math.sin(Math.PI * Math.pow(k, 0.62) * 0.94);
        ctx.strokeStyle = `rgba(${STREAM_RGB},${(base * life * Math.pow(k, 1.15)).toFixed(4)})`;
        ctx.lineWidth = 0.2 + maxW * body;
        ctx.beginPath();
        for (let i = from; i <= to; i++) {
          const idx = ((start + i) % TRAIL) * 2;
          if (i === from) ctx.moveTo(s.trail[idx], s.trail[idx + 1]);
          else ctx.lineTo(s.trail[idx], s.trail[idx + 1]);
        }
        // the head rides the live position, smooth between samples
        if (to === s.count - 1) ctx.lineTo(s.x, s.y);
        ctx.stroke();
      }
    }

    for (const l of leafList) {
      const s = leaves[l.sprite];
      if (!s) continue;
      const face = Math.cos(l.flip);
      // edge-on it thins to a sliver and darkens, face-on it catches light
      ctx.globalAlpha = 0.42 + 0.3 * Math.abs(face);
      const c = Math.cos(l.rot);
      const sn = Math.sin(l.rot);
      const sy = Math.max(0.08, Math.abs(face));
      ctx.setTransform(dpr * c, dpr * sn, dpr * -sn * sy, dpr * c * sy, dpr * l.x, dpr * l.y);
      const lw = l.size * 1.75;
      ctx.drawImage(s, -lw / 2, -l.size / 2, lw, l.size);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = 1;
  }

  function drawLightning(): void {
    if (!storm || flash < 0.01) return;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const x = flashX * w;
    const g = ctx.createRadialGradient(x, h * 0.05, 0, x, h * 0.05, h * 0.9);
    g.addColorStop(0, `rgba(196,210,255,${(0.2 * flash).toFixed(3)})`);
    g.addColorStop(0.5, `rgba(170,186,240,${(0.07 * flash).toFixed(3)})`);
    g.addColorStop(1, 'rgba(170,186,240,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
    ctx.restore();
  }

  /* ---------------- api ---------------- */

  return {
    resize(nw: number, nh: number, ndpr: number): void {
      const rebuild = !built || Math.abs(nw - w) > 2 || Math.abs(nh - h) > 2;
      w = Math.max(1, nw);
      h = Math.max(1, nh);
      dpr = ndpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (rebuild) {
        build();
        built = true;
      }
      buildLight();
    },
    frame(dt: number, t: number): void {
      update(dt, t);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      drawSky(t);
      drawClouds();
      drawFog(t);
      drawRain();
      drawSnow();
      drawWind(t);
      drawLightning();
    },
    stats(): Record<string, number> {
      return {
        clouds: cloudList.length,
        fog: fogBanks.length,
        drops: drops.length,
        streams: streams.length,
        leaves: leafList.length,
        stars: stars.length,
        planet: planet ? 1 : 0,
        satellite: satelliteGlow,
        flash,
        driftX,
      };
    },
  };
}

/* ------------------------------------------------------------------ */
/* presets: every sky, for the settings preview and the playground     */
/* ------------------------------------------------------------------ */

export interface ScenePreset {
  label: string;
  /** what the weather line says it is */
  condition: string;
  /** a WMO code for the line's glyph */
  code: number;
  /** the line shows the wind glyph */
  windy?: boolean;
  cond: Partial<SceneConditions>;
}

const PRESET_BASE: SceneConditions = {
  sky: 'clear',
  precip: 'none',
  intensity: 0,
  thunder: false,
  isDay: true,
  cloudCover: 0.04,
  windMph: 4,
  gustMph: 6,
  windFromDeg: 250,
  dayPhase: 0.5,
  twilight: 0,
  season: 'autumn',
};

export const SCENE_PRESETS: Record<string, ScenePreset> = {
  'clear-noon': { label: 'Clear noon', condition: 'Clear sky', code: 0, cond: {} },
  'golden-hour': {
    label: 'Golden hour',
    condition: 'Clear sky',
    code: 0,
    cond: { cloudCover: 0.1, windMph: 6, gustMph: 9, dayPhase: 0.97, twilight: 0.9 },
  },
  'clear-night': {
    label: 'Clear night',
    condition: 'Clear sky',
    code: 0,
    cond: { isDay: false, windMph: 3, gustMph: 5 },
  },
  'partly-cloudy': {
    label: 'Partly cloudy',
    condition: 'Partly cloudy',
    code: 2,
    cond: { sky: 'partly', cloudCover: 0.5, windMph: 8, gustMph: 12, dayPhase: 0.4 },
  },
  overcast: {
    label: 'Overcast',
    condition: 'Overcast',
    code: 3,
    cond: { sky: 'overcast', cloudCover: 0.95, windMph: 7, gustMph: 11 },
  },
  fog: {
    label: 'Fog',
    condition: 'Fog',
    code: 45,
    cond: { sky: 'fog', cloudCover: 0.7, windMph: 2, gustMph: 4, dayPhase: 0.15 },
  },
  drizzle: {
    label: 'Drizzle',
    condition: 'Light drizzle',
    code: 51,
    cond: {
      sky: 'overcast',
      precip: 'rain',
      intensity: 0.22,
      cloudCover: 0.9,
      windMph: 6,
      gustMph: 9,
    },
  },
  downpour: {
    label: 'Downpour',
    condition: 'Heavy rain',
    code: 65,
    cond: {
      sky: 'overcast',
      precip: 'rain',
      intensity: 0.95,
      cloudCover: 1,
      windMph: 22,
      gustMph: 36,
    },
  },
  thunderstorm: {
    label: 'Thunderstorm',
    condition: 'Thunderstorm',
    code: 95,
    cond: {
      sky: 'overcast',
      precip: 'rain',
      intensity: 0.8,
      thunder: true,
      isDay: false,
      cloudCover: 1,
      windMph: 18,
      gustMph: 32,
    },
  },
  snowfall: {
    label: 'Snowfall',
    condition: 'Moderate snow',
    code: 73,
    cond: {
      sky: 'overcast',
      precip: 'snow',
      intensity: 0.4,
      cloudCover: 0.9,
      windMph: 5,
      gustMph: 8,
    },
  },
  blizzard: {
    label: 'Blizzard',
    condition: 'Heavy snow',
    code: 75,
    cond: {
      sky: 'overcast',
      precip: 'snow',
      intensity: 0.95,
      cloudCover: 1,
      windMph: 30,
      gustMph: 46,
    },
  },
  breezy: {
    label: 'Breezy',
    condition: 'Breezy',
    code: 1,
    windy: true,
    cond: { cloudCover: 0.12, windMph: 17, gustMph: 26, dayPhase: 0.6 },
  },
  gale: {
    label: 'Gale',
    condition: 'Gale',
    code: 2,
    windy: true,
    cond: { sky: 'partly', cloudCover: 0.35, windMph: 34, gustMph: 52, dayPhase: 0.45 },
  },
  'windy-night': {
    label: 'Windy night',
    condition: 'Windy',
    code: 0,
    windy: true,
    cond: { cloudCover: 0.08, windMph: 28, gustMph: 40, isDay: false, windFromDeg: 80 },
  },
};

/**
 * a preset's full conditions. `date` pretends it's another day: the season
 * follows it now, the holiday scenes will too.
 */
export function presetConditions(
  key: string,
  date?: Date | null,
  latitude = 0,
): SceneConditions | null {
  const p = SCENE_PRESETS[key];
  if (!p) return null;
  const when = date ?? new Date();
  return {
    ...PRESET_BASE,
    season: seasonForMonth(when.getUTCMonth(), latitude < 0),
    ...p.cond,
  };
}
