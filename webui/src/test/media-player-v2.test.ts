import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * Player theater (media-player-v2.js) + the two surgical v1 hooks, tested
 * against the REAL source. The pure helpers and every visualization painter
 * are lifted out of the live file; the v1 sleep/crossfade changes are lifted
 * out of media-player.js.
 */

const v2 = readFileSync(resolve(process.cwd(), 'static/media-player-v2.js'), 'utf8');
const v1 = readFileSync(resolve(process.cwd(), 'static/media-player.js'), 'utf8');

/** Lift a top-level `const NAME = <balanced value>;` (string/template/comment aware). */
function extractConst(name: string, source: string): string {
  const m = new RegExp(`^const ${name} =`, 'm').exec(source);
  if (!m) throw new Error(`const ${name} not found`);
  let i = m.index + m[0].length;
  const stack: string[] = [];
  let quote: "'" | '"' | '`' | null = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (; i < source.length; i++) {
    const c = source[i];
    const n = source[i + 1];
    if (lineComment) {
      if (c === '\n') lineComment = false;
      continue;
    }
    if (blockComment) {
      if (c === '*' && n === '/') {
        blockComment = false;
        i++;
      }
      continue;
    }
    if (quote) {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (c === '\\') {
        escaped = true;
        continue;
      }
      if (c === quote) {
        quote = null;
        continue;
      }
      if (quote === '`' && c === '$' && n === '{') {
        stack.push('${');
        quote = null;
        i++;
        continue;
      }
      continue;
    }
    if (c === '/' && n === '/') {
      lineComment = true;
      i++;
      continue;
    }
    if (c === '/' && n === '*') {
      blockComment = true;
      i++;
      continue;
    }
    if (c === "'" || c === '"' || c === '`') {
      quote = c;
      continue;
    }
    if (c === '{' || c === '[' || c === '(') {
      stack.push(c);
      continue;
    }
    if (c === '}' || c === ']' || c === ')') {
      const top = stack.pop();
      const ok =
        (c === '}' && (top === '{' || top === '${')) ||
        (c === ']' && top === '[') ||
        (c === ')' && top === '(');
      if (!ok) throw new Error(`unbalanced close ${c} in const ${name}`);
      if (top === '${') quote = '`';
      continue;
    }
    if (c === ';' && stack.length === 0) return source.slice(m.index, i + 1);
  }
  throw new Error(`unterminated const ${name}`);
}

/** Build a callable harness from lifted v2 functions + consts. */
function v2Harness(fnNames: string[], constNames: string[] = []): Record<string, any> {
  const parts = [
    ...constNames.map((n) => extractConst(n, v2)),
    ...fnNames.map((n) => extractFunction(n, v2)),
  ];
  const names = [...fnNames, ...constNames];
  return new Function(`${parts.join('\n')}\nreturn { ${names.join(', ')} };`)();
}

// ---------------------------------------------------------------------------
// Playback speed
// ---------------------------------------------------------------------------

describe('playback speed helpers', () => {
  const { npv2ClampSpeed, npv2FormatSpeed } = v2Harness(
    ['npv2ClampSpeed', 'npv2FormatSpeed'],
    ['NPV2_SPEED_PRESETS'],
  );

  it('snaps to the nearest preset', () => {
    expect(npv2ClampSpeed(1.3)).toBe(1.25);
    expect(npv2ClampSpeed(1.4)).toBe(1.5);
    expect(npv2ClampSpeed(0.6)).toBe(0.5);
    expect(npv2ClampSpeed(1)).toBe(1);
  });

  it('clamps outside the 0.5–2 range and rejects garbage', () => {
    expect(npv2ClampSpeed(0)).toBe(0.5);
    expect(npv2ClampSpeed(99)).toBe(2);
    expect(npv2ClampSpeed(-3)).toBe(0.5);
    expect(npv2ClampSpeed(NaN)).toBe(1);
    expect(npv2ClampSpeed('fast')).toBe(1);
    expect(npv2ClampSpeed(undefined)).toBe(1);
  });

  it('accepts numeric strings', () => {
    expect(npv2ClampSpeed('1.5')).toBe(1.5);
  });

  it('formats with the × suffix', () => {
    expect(npv2FormatSpeed(1)).toBe('1×');
    expect(npv2FormatSpeed(2)).toBe('2×');
    expect(npv2FormatSpeed(1.25)).toBe('1.25×');
    expect(npv2FormatSpeed(0.75)).toBe('0.75×');
    expect(npv2FormatSpeed(99)).toBe('2×');
  });
});

// ---------------------------------------------------------------------------
// A-B loop state machine
// ---------------------------------------------------------------------------

describe('A-B loop', () => {
  const { npv2AbNext, npv2AbLabel } = v2Harness(['npv2AbNext', 'npv2AbLabel']);

  it('walks off → A → B → off', () => {
    const a = npv2AbNext({ mode: 'off', a: 0, b: 0 }, 12.5);
    expect(a).toEqual({ mode: 'a', a: 12.5, b: 0 });
    const b = npv2AbNext(a, 30);
    expect(b).toEqual({ mode: 'b', a: 12.5, b: 30 });
    expect(npv2AbNext(b, 31)).toEqual({ mode: 'off', a: 0, b: 0 });
  });

  it('re-arms A when B is at or before A (0.25s grace)', () => {
    const a = npv2AbNext({ mode: 'off', a: 0, b: 0 }, 10);
    const rearmed = npv2AbNext(a, 10.1);
    expect(rearmed.mode).toBe('a');
    expect(rearmed.a).toBe(10.1);
    const rearmed2 = npv2AbNext(a, 5);
    expect(rearmed2).toEqual({ mode: 'a', a: 5, b: 0 });
  });

  it('tolerates missing/garbage state', () => {
    expect(npv2AbNext(null, 7).mode).toBe('a');
    expect(npv2AbNext(undefined, -3).a).toBe(0);
    expect(npv2AbNext({ mode: 'b', a: 1, b: 2 }, 99).mode).toBe('off');
  });

  it('labels each state', () => {
    expect(npv2AbLabel({ mode: 'off', a: 0, b: 0 })).toBe('A–B loop');
    expect(npv2AbLabel({ mode: 'a', a: 1, b: 0 })).toBe('A–B · A set');
    expect(npv2AbLabel({ mode: 'b', a: 1, b: 2 })).toBe('A–B · looping');
    expect(npv2AbLabel(null)).toBe('A–B loop');
  });
});

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

describe('playback history', () => {
  const { npv2TrackIdentity, npv2HistoryPush } = v2Harness([
    'npv2TrackIdentity',
    'npv2HistoryPush',
  ]);

  const trackA = {
    title: 'Blue in Green',
    artist: 'Miles Davis',
    album: 'Kind of Blue',
    id: '1',
  };
  const trackB = {
    title: 'So What',
    artist: 'Miles Davis',
    album: 'Kind of Blue',
    id: '2',
  };

  it('identifies a track case-insensitively', () => {
    expect(npv2TrackIdentity(trackA)).toBe(
      npv2TrackIdentity({ ...trackA, title: 'BLUE IN GREEN' }),
    );
    expect(npv2TrackIdentity(trackA)).not.toBe(npv2TrackIdentity(trackB));
    expect(npv2TrackIdentity(null)).toBe('');
  });

  it('prepends newest-first', () => {
    const list = npv2HistoryPush(
      npv2HistoryPush([], { entry: trackA, ts: 1 }, 50),
      { entry: trackB, ts: 2 },
      50,
    );
    expect(list.map((h: any) => h.entry.title)).toEqual(['So What', 'Blue in Green']);
  });

  it('dedupes a consecutive repeat without dropping it from history depth', () => {
    const once = npv2HistoryPush([], { entry: trackA, ts: 1 }, 50);
    const twice = npv2HistoryPush(once, { entry: { ...trackA }, ts: 2 }, 50);
    expect(twice).toHaveLength(1);
    // ...but a repeat after another track is a real replay
    const afterB = npv2HistoryPush(
      npv2HistoryPush(once, { entry: trackB, ts: 3 }, 50),
      { entry: trackA, ts: 4 },
      50,
    );
    expect(afterB).toHaveLength(3);
  });

  it('caps the list', () => {
    let list: any[] = [];
    for (let i = 0; i < 10; i++)
      list = npv2HistoryPush(list, { entry: { ...trackA, id: String(i) }, ts: i }, 5);
    expect(list).toHaveLength(5);
    expect(list[0].entry.id).toBe('9');
  });

  it('ignores empty items and non-arrays', () => {
    expect(npv2HistoryPush([], null, 50)).toEqual([]);
    expect(npv2HistoryPush(null, { entry: trackA, ts: 1 }, 50)).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

describe('formatting helpers', () => {
  const { npv2TimeAgo, npv2FormatExt } = v2Harness(['npv2TimeAgo', 'npv2FormatExt']);

  it('formats relative time', () => {
    const now = 1_700_000_000_000;
    expect(npv2TimeAgo(now - 10_000, now)).toBe('just now');
    expect(npv2TimeAgo(now - 5 * 60_000, now)).toBe('5m ago');
    expect(npv2TimeAgo(now - 3 * 3_600_000, now)).toBe('3h ago');
    expect(npv2TimeAgo(now - 6 * 86_400_000, now)).toBe('6d ago');
    expect(typeof npv2TimeAgo(now - 60 * 86_400_000, now)).toBe('string');
  });

  it('extracts an uppercase extension', () => {
    expect(npv2FormatExt('/music/song.flac')).toBe('FLAC');
    expect(npv2FormatExt('track.MP3')).toBe('MP3');
    expect(npv2FormatExt('no-extension')).toBe('');
    expect(npv2FormatExt(null)).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Equalizer data
// ---------------------------------------------------------------------------

describe('equalizer presets', () => {
  const { npv2EqPresetNames, npv2EqPresetGains, npv2ClampDb } = v2Harness(
    ['npv2EqPresetNames', 'npv2EqPresetGains', 'npv2ClampDb'],
    ['NPV2_EQ_PRESETS', 'NPV2_EQ_FREQS', 'NPV2_EQ_RANGE_DB'],
  );

  it('ships 11 presets', () => {
    const names = npv2EqPresetNames();
    expect(names).toHaveLength(11);
    expect(names).toContain('Flat');
    expect(names).toContain('Bass Booster');
    expect(names).toContain('Podcast');
  });

  it('every preset has 10 in-range bands', () => {
    for (const name of npv2EqPresetNames()) {
      const g = npv2EqPresetGains(name);
      expect(g, name).toHaveLength(10);
      for (const v of g) expect(Math.abs(v) <= 12, `${name}=${v}`).toBe(true);
    }
  });

  it('Flat is all zeros', () => {
    expect(npv2EqPresetGains('Flat')).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
  });

  it('returns null for unknown presets and a copy for known ones', () => {
    expect(npv2EqPresetGains('Nope')).toBeNull();
    const g = npv2EqPresetGains('Rock')!;
    g[0] = 999;
    expect(npv2EqPresetGains('Rock')![0]).not.toBe(999);
  });

  it('clamps dB to ±12 and rounds to 0.5', () => {
    expect(npv2ClampDb(99)).toBe(12);
    expect(npv2ClampDb(-99)).toBe(-12);
    expect(npv2ClampDb(3.3)).toBe(3.5);
    expect(npv2ClampDb(NaN)).toBe(0);
    expect(npv2ClampDb('loud')).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Visualization themes + painter contract
// ---------------------------------------------------------------------------

describe('visualization themes', () => {
  const lifted = v2Harness(
    [
      'npv2ThemeList',
      'npv2ThemeIds',
      'npv2ThemeState',
      'npv2Bin',
      'npv2BinS',
      'npv2Css',
      'npv2Css2',
      'npv2PalA',
      'npv2Pal2',
      'npv2Q',
      'npv2BeatAmp',
      'npv2F',
      'npv2Ease',
      'npv2Onset',
      'npv2Spawn',
      'npv2ScopeTrace',
      'npv2Soft',
    ],
    ['NPV2_PAINT', 'NPV2_THEME_STATE', 'NPV2_SOFT'],
  );
  const { npv2ThemeList, npv2ThemeIds, NPV2_PAINT } = lifted;

  it('lists 18 themes with names and blurbs', () => {
    const list = npv2ThemeList();
    expect(list).toHaveLength(18);
    for (const t of list) {
      expect(t.id).toBeTruthy();
      expect(t.name).toBeTruthy();
      expect(t.blurb).toBeTruthy();
    }
    expect(npv2ThemeIds()).toContain('aurora');
    expect(npv2ThemeIds()).toContain('none');
    // the new WMP-oldschool + modern set
    for (const id of [
      'battery',
      'dotplane',
      'alchemy',
      'fountain',
      'kaleido',
      'warp',
      'nebula',
      'bloom',
    ]) {
      expect(npv2ThemeIds()).toContain(id);
    }
  });

  it('every theme except "none" has a painter', () => {
    for (const id of npv2ThemeIds()) {
      if (id === 'none') continue;
      expect(typeof NPV2_PAINT[id], `painter ${id}`).toBe('function');
    }
  });

  it('every painter renders a frame (and an idle frame) without throwing', () => {
    const grad = { addColorStop: () => {} };
    const stubCtx = () =>
      new Proxy(
        {},
        {
          get(_t, p) {
            if (p === 'createLinearGradient' || p === 'createRadialGradient') return () => grad;
            return () => {};
          },
          set() {
            return true;
          },
        },
      );
    const frame = (idle: boolean) => ({
      freq: Uint8Array.from({ length: 64 }, (_, i) => (i * 37) % 256),
      wave: Uint8Array.from({ length: 64 }, (_, i) => 128 + Math.round(100 * Math.sin(i / 4))),
      energy: idle ? 0.2 : 0.65,
      beat: 0,
      t: 2.5,
      idle,
      pal: { r: 120, g: 80, b: 200 },
    });
    for (const id of npv2ThemeIds()) {
      if (id === 'none') continue;
      expect(
        () => NPV2_PAINT[id](stubCtx(), 1280, 800, frame(false)),
        `painter ${id}`,
      ).not.toThrow();
      expect(
        () => NPV2_PAINT[id](stubCtx(), 1280, 800, frame(true)),
        `painter ${id} idle`,
      ).not.toThrow();
    }
  });

  it('every painter handles bass/mid/treble-heavy spectra and beat hits', () => {
    const grad = { addColorStop: () => {} };
    const stubCtx = () =>
      new Proxy(
        {},
        {
          get(_t, p) {
            if (p === 'createLinearGradient' || p === 'createRadialGradient') return () => grad;
            return () => {};
          },
          set() {
            return true;
          },
        },
      );
    const shaped = (hotFrom: number, hotTo: number, beat: number) => ({
      freq: Uint8Array.from({ length: 64 }, (_, i) => (i >= hotFrom && i <= hotTo ? 230 : 15)),
      wave: Uint8Array.from({ length: 64 }, (_, i) => 128 + Math.round(100 * Math.sin(i / 4))),
      energy: beat > 0 ? 0.9 : 0.6,
      beat,
      t: 2.5,
      idle: false,
      pal: { r: 120, g: 80, b: 200 },
    });
    const cases: Array<[string, ReturnType<typeof shaped>]> = [
      ['bass-heavy', shaped(0, 7, 0)],
      ['mid-heavy', shaped(20, 35, 0)],
      ['treble-heavy', shaped(50, 63, 0)],
      ['beat-hit', shaped(0, 15, 1)],
    ];
    for (const id of npv2ThemeIds()) {
      if (id === 'none') continue;
      for (const [label, S] of cases) {
        expect(
          () => NPV2_PAINT[id](stubCtx(), 1280, 800, S),
          `painter ${id} ${label}`,
        ).not.toThrow();
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Visual options (quality, energy, palettes, autocycle, dim)
// ---------------------------------------------------------------------------

describe('visual option helpers', () => {
  const {
    npv2QualitySpec,
    npv2AutocycleSeconds,
    npv2ClampVizEnergy,
    npv2ClampVizDim,
    npv2PaletteList,
    NPV2_PALETTES,
  } = v2Harness(
    [
      'npv2QualitySpec',
      'npv2AutocycleSeconds',
      'npv2ClampVizEnergy',
      'npv2ClampVizDim',
      'npv2PaletteList',
    ],
    ['NPV2_PALETTES'],
  );

  it('quality specs give a bounded dpr and a particle factor', () => {
    expect(npv2QualitySpec('auto')).toEqual({ dpr: 1.5, q: 1 });
    expect(npv2QualitySpec('high')).toEqual({ dpr: 2, q: 1.25 });
    expect(npv2QualitySpec('balanced')).toEqual({ dpr: 1.25, q: 0.8 });
    expect(npv2QualitySpec('lite')).toEqual({ dpr: 1, q: 0.5 });
    expect(npv2QualitySpec('nonsense')).toEqual({ dpr: 1.5, q: 1 });
  });

  it('autocycle parses track mode and positive intervals', () => {
    expect(npv2AutocycleSeconds('off')).toBe(0);
    expect(npv2AutocycleSeconds('track')).toBe(-1);
    expect(npv2AutocycleSeconds('30')).toBe(30);
    expect(npv2AutocycleSeconds('60')).toBe(60);
    expect(npv2AutocycleSeconds('300')).toBe(300);
    expect(npv2AutocycleSeconds('bogus')).toBe(0);
    expect(npv2AutocycleSeconds('0')).toBe(0);
  });

  it('clamps energy to 0.5–1.5 and dim to 0–0.6', () => {
    expect(npv2ClampVizEnergy(1)).toBe(1);
    expect(npv2ClampVizEnergy(0.1)).toBe(0.5);
    expect(npv2ClampVizEnergy(9)).toBe(1.5);
    expect(npv2ClampVizEnergy('loud')).toBe(1);
    expect(npv2ClampVizDim(0.3)).toBe(0.3);
    expect(npv2ClampVizDim(-2)).toBe(0);
    expect(npv2ClampVizDim(0.9)).toBe(0.6);
    expect(npv2ClampVizDim('dim')).toBe(0);
  });

  it('every palette has r/g/b and the list has an album-art default', () => {
    const list = npv2PaletteList();
    expect(list[0].id).toBe('auto');
    for (const p of list) {
      if (p.id === 'auto') continue;
      const c = NPV2_PALETTES[p.id];
      expect(c).toBeTruthy();
      expect(c.r).toBeGreaterThanOrEqual(0);
      expect(c.r).toBeLessThanOrEqual(255);
      expect(c.g).toBeGreaterThanOrEqual(0);
      expect(c.g).toBeLessThanOrEqual(255);
      expect(c.b).toBeGreaterThanOrEqual(0);
      expect(c.b).toBeLessThanOrEqual(255);
    }
  });
});

// ---------------------------------------------------------------------------
// Theme cycling: manual offers Off, automatic rotation skips it
// ---------------------------------------------------------------------------

describe('theme cycling', () => {
  const make = () => {
    const seen: string[] = [];
    const factory = new Function(
      'seen',
      [
        extractConst('NPV2', v2),
        'function npv2SetTheme(id) { seen.push(id); NPV2.theme = id; }',
        extractFunction('npv2ThemeList', v2),
        extractFunction('npv2ThemeIds', v2),
        extractFunction('npv2CycleTheme', v2),
        'return { npv2CycleTheme, NPV2 };',
      ].join('\n'),
    );
    return { api: factory(seen) as any, seen };
  };

  it('manual cycling includes the Off theme', () => {
    const { api, seen } = make();
    api.NPV2.theme = 'bloom';
    api.npv2CycleTheme();
    expect(seen).toEqual(['none']);
  });

  it('automatic cycling (skipOff) never lands on Off', () => {
    const { api, seen } = make();
    api.NPV2.theme = 'bloom';
    api.npv2CycleTheme(true);
    expect(seen).toEqual(['barscope']);
    // a full rotation visits every theme except none
    const { api: api2, seen: seen2 } = make();
    api2.NPV2.theme = 'barscope';
    for (let i = 0; i < 20; i++) api2.npv2CycleTheme(true);
    expect(seen2).not.toContain('none');
    expect(new Set(seen2).size).toBe(17);
  });

  it('automatic cycling from Off moves to the first visual', () => {
    const { api, seen } = make();
    api.NPV2.theme = 'none';
    api.npv2CycleTheme(true);
    expect(seen).toEqual(['barscope']);
  });
});

// ---------------------------------------------------------------------------
// v1 hook: configurable crossfade duration
// ---------------------------------------------------------------------------

describe('v1 crossfade duration hook', () => {
  const { npCrossfadeSeconds } = new Function(
    `${extractFunction('npCrossfadeSeconds', v1)}; return { npCrossfadeSeconds };`,
  )();

  beforeEach(() => localStorage.clear());

  it('defaults to 6s when nothing is stored', () => {
    expect(npCrossfadeSeconds()).toBe(6);
  });

  it('accepts the picker values 0/2/4/6/10/15', () => {
    for (const v of [0, 2, 4, 6, 10, 15]) {
      localStorage.setItem('soulsync-npv2-crossfade-secs', String(v));
      expect(npCrossfadeSeconds()).toBe(v);
    }
  });

  it('falls back to 6s on garbage', () => {
    for (const v of ['banana', '7', '-3', '', '6.5']) {
      localStorage.setItem('soulsync-npv2-crossfade-secs', v);
      expect(npCrossfadeSeconds()).toBe(6);
    }
  });
});

// ---------------------------------------------------------------------------
// v1 hook: extended sleep timer
// ---------------------------------------------------------------------------

describe('v1 sleep timer', () => {
  function sleepHarness() {
    document.body.innerHTML =
      '<button id="np-sleep-btn"><span id="np-sleep-label">Sleep</span></button>';
    const audioPlayer = { paused: false, pause: vi.fn() };
    const setPlayingState = vi.fn();
    const fns = ['npCancelCrossfade', 'npCycleSleepTimer', 'npClearSleepTimer', 'npFireSleepTimer']
      .map((n) => extractFunction(n, v1))
      .join('\n');
    const api = new Function(
      'document',
      'audioPlayer',
      'setPlayingState',
      `
      let npSleepMinutes = 0, npSleepTimerId = null, npSleepEndOfTrack = false;
      let npXfadeTimer = null, npXfadeAudio = null, npXfadeHandoff = null;
      let npXfadeActive = false, npXfadeMainVol = null;
      const npStopHandoff = () => {};
      ${fns}
      return {
        npCycleSleepTimer, npClearSleepTimer, npFireSleepTimer,
        state: () => ({ npSleepMinutes, npSleepEndOfTrack, hasTimer: npSleepTimerId !== null }),
        // test-only: simulate a crossfade in flight
        armXfade: (fakeAudio) => { npXfadeActive = true; npXfadeAudio = fakeAudio; },
        xfadeActive: () => npXfadeActive,
      };
    `,
    )(document, audioPlayer, setPlayingState);
    return {
      ...api,
      audioPlayer,
      setPlayingState,
      label: () => document.getElementById('np-sleep-label')!.textContent,
      btnActive: () => document.getElementById('np-sleep-btn')!.classList.contains('active'),
    };
  }

  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('cycles off → 15 → 30 → 45 → 60 → 90 → 120 → track end → off', () => {
    const h = sleepHarness();
    const expected = [
      'Sleep 15m',
      'Sleep 30m',
      'Sleep 45m',
      'Sleep 60m',
      'Sleep 90m',
      'Sleep 120m',
      'Sleep: track end',
      'Sleep',
    ];
    for (const label of expected) {
      h.npCycleSleepTimer();
      expect(h.label()).toBe(label);
    }
    expect(h.state()).toEqual({
      npSleepMinutes: 0,
      npSleepEndOfTrack: false,
      hasTimer: false,
    });
    expect(h.btnActive()).toBe(false);
  });

  it('marks the button active while armed', () => {
    const h = sleepHarness();
    h.npCycleSleepTimer();
    expect(h.btnActive()).toBe(true);
    expect(h.state().hasTimer).toBe(true);
  });

  it('the minute timer pauses playback and resets when it fires', () => {
    const h = sleepHarness();
    h.npCycleSleepTimer(); // 15m
    vi.advanceTimersByTime(15 * 60 * 1000);
    expect(h.audioPlayer.pause).toHaveBeenCalled();
    expect(h.setPlayingState).toHaveBeenCalledWith(false);
    expect(h.label()).toBe('Sleep');
    expect(h.state().npSleepMinutes).toBe(0);
  });

  it('end-of-track mode sets the flag with no minute timer', () => {
    const h = sleepHarness();
    for (let i = 0; i < 7; i++) h.npCycleSleepTimer();
    expect(h.label()).toBe('Sleep: track end');
    expect(h.state()).toEqual({
      npSleepMinutes: 0,
      npSleepEndOfTrack: true,
      hasTimer: false,
    });
  });

  it('firing pauses instead of stopping (queue survives)', () => {
    const h = sleepHarness();
    for (let i = 0; i < 7; i++) h.npCycleSleepTimer(); // arm track-end
    h.audioPlayer.paused = true; // already ended — pause() must not be forced
    h.npFireSleepTimer();
    expect(h.audioPlayer.pause).not.toHaveBeenCalled();
    expect(h.setPlayingState).toHaveBeenCalledWith(false);
    expect(h.state().npSleepEndOfTrack).toBe(false);
  });

  it('re-cycling clears the previous timer', () => {
    const h = sleepHarness();
    h.npCycleSleepTimer(); // 15m armed
    h.npCycleSleepTimer(); // 30m — the 15m timer must be gone
    vi.advanceTimersByTime(15 * 60 * 1000);
    expect(h.audioPlayer.pause).not.toHaveBeenCalled();
    vi.advanceTimersByTime(15 * 60 * 1000);
    expect(h.audioPlayer.pause).toHaveBeenCalled();
  });

  it('firing tears down an in-flight crossfade so no audio leaks', () => {
    const h = sleepHarness();
    const fakeXfade = { pause: vi.fn(), src: 'blob:fake', volume: 0.5 };
    h.armXfade(fakeXfade);
    expect(h.xfadeActive()).toBe(true);
    h.npFireSleepTimer();
    expect(fakeXfade.pause).toHaveBeenCalled();
    expect(h.xfadeActive()).toBe(false);
    expect(h.audioPlayer.pause).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Minimal fake DOM for the DOM-touching v2 controls
// ---------------------------------------------------------------------------

function npv2FakeClassList() {
  const s = new Set<string>();
  return {
    add: (...c: string[]) => {
      c.forEach((x) => s.add(x));
    },
    remove: (...c: string[]) => {
      c.forEach((x) => s.delete(x));
    },
    toggle: (c: string, force?: boolean) => {
      const want = force === undefined ? !s.has(c) : !!force;
      if (want) s.add(c);
      else s.delete(c);
      return want;
    },
    contains: (c: string) => s.has(c),
  };
}

function npv2FakeEl(id: string): any {
  const el: any = {
    id,
    classList: npv2FakeClassList(),
    children: [] as any[],
    parentNode: null as any,
    textContent: '',
    attrs: {} as Record<string, string>,
    setAttribute(k: string, v: string) {
      el.attrs[k] = v;
    },
    getAttribute(k: string) {
      return el.attrs[k] ?? null;
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    appendChild(child: any) {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = el;
      el.children.push(child);
      return child;
    },
    insertBefore(child: any, ref: any) {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = el;
      const i = el.children.indexOf(ref);
      if (i < 0) el.children.push(child);
      else el.children.splice(i, 0, child);
      return child;
    },
    removeChild(child: any) {
      el.children = el.children.filter((c: any) => c !== child);
      child.parentNode = null;
      return child;
    },
  };
  Object.defineProperty(el, 'nextSibling', {
    get() {
      if (!el.parentNode) return null;
      const sibs = el.parentNode.children;
      const i = sibs.indexOf(el);
      return i >= 0 && i + 1 < sibs.length ? sibs[i + 1] : null;
    },
  });
  return el;
}

function npv2FakeDocument(els: Record<string, any>): any {
  return {
    _els: els,
    fullscreenElement: null as any,
    getElementById(id: string) {
      return els[id] ?? null;
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

// ---------------------------------------------------------------------------
// Visuals side panel (immersive overlay keeps the show running)
// ---------------------------------------------------------------------------

describe('visuals side panel', () => {
  const build = () => {
    const home = npv2FakeEl('np-tabpanels');
    const historyPanel = npv2FakeEl('np-v2-history-panel');
    const sibling = npv2FakeEl('np-v2-details-panel');
    home.appendChild(historyPanel);
    home.appendChild(sibling);
    const els: Record<string, any> = {
      'np-tabpanels': home,
      'np-v2-history-panel': historyPanel,
      'np-v2-details-panel': sibling,
      'np-v2-sidepanel-body': npv2FakeEl('np-v2-sidepanel-body'),
      'np-v2-visuals-sidepanel': npv2FakeEl('np-v2-visuals-sidepanel'),
      'np-v2-sidepanel-title': npv2FakeEl('np-v2-sidepanel-title'),
      'np-v2-visuals-drawer': npv2FakeEl('np-v2-visuals-drawer'),
      'np-v2-visuals-panels': npv2FakeEl('np-v2-visuals-panels'),
      'np-queue-panel': npv2FakeEl('np-queue-panel'),
      'np-lyrics-panel': npv2FakeEl('np-lyrics-panel'),
      'np-v2-settings-panel': npv2FakeEl('np-v2-settings-panel'),
    };
    els['np-v2-visuals-sidepanel'].classList.add('hidden');
    const doc = npv2FakeDocument(els);
    const factory = new Function(
      'document',
      [
        extractConst('NPV2', v2),
        extractConst('NPV2_SIDE_PANELS', v2),
        extractConst('NPV2_TABS', v2),
        'let npv2SidePanelHome = null;',
        'let npv2SidePanelTab = null;',
        'function npv2RenderHistory() {}',
        'function npv2RenderDetails() {}',
        'function npv2VisualsModeOn() { return true; }',
        'function npv2VisualsClearIdle() {}',
        extractFunction('npv2OpenVisualsPanel', v2),
        extractFunction('npv2CloseVisualsPanel', v2),
        extractFunction('npv2SidePanelOpen', v2),
        extractFunction('npv2ToggleVisualsDrawer', v2),
        extractFunction('npv2SelectTab', v2),
        extractFunction('npv2VisualsWakeUI', v2),
        'return { npv2OpenVisualsPanel, npv2CloseVisualsPanel, npv2SidePanelOpen, NPV2 };',
      ].join('\n'),
    );
    return { api: factory(doc) as any, els, home, historyPanel, sibling };
  };

  it('opens the history panel into the side overlay', () => {
    const { api, els, historyPanel } = build();
    api.npv2OpenVisualsPanel('history');
    expect(api.npv2SidePanelOpen()).toBe(true);
    expect(historyPanel.parentNode).toBe(els['np-v2-sidepanel-body']);
    expect(historyPanel.classList.contains('hidden')).toBe(false);
    expect(els['np-v2-visuals-sidepanel'].classList.contains('hidden')).toBe(false);
    expect(els['np-v2-sidepanel-title'].textContent).toBe('History');
    expect(api.NPV2.tab).toBe('history');
  });

  it('toggling the same tab closes the panel', () => {
    const { api } = build();
    api.npv2OpenVisualsPanel('history');
    api.npv2OpenVisualsPanel('history');
    expect(api.npv2SidePanelOpen()).toBe(false);
  });

  it('closing restores the node to its original home and spot', () => {
    const { api, els, home, historyPanel, sibling } = build();
    api.npv2OpenVisualsPanel('history');
    api.npv2CloseVisualsPanel();
    expect(api.npv2SidePanelOpen()).toBe(false);
    expect(historyPanel.parentNode).toBe(home);
    expect(home.children[0]).toBe(historyPanel);
    expect(home.children[1]).toBe(sibling);
    expect(els['np-v2-visuals-sidepanel'].classList.contains('hidden')).toBe(true);
  });

  it('switching tabs moves the panel without a close round-trip', () => {
    const { api, els, historyPanel } = build();
    api.npv2OpenVisualsPanel('queue');
    api.npv2OpenVisualsPanel('history');
    expect(historyPanel.parentNode).toBe(els['np-v2-sidepanel-body']);
    expect(els['np-v2-sidepanel-title'].textContent).toBe('History');
  });

  it('ignores unknown tabs', () => {
    const { api } = build();
    api.npv2OpenVisualsPanel('nope');
    expect(api.npv2SidePanelOpen()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Fullscreen (true browser fullscreen, separate from immersive)
// ---------------------------------------------------------------------------

describe('fullscreen', () => {
  const build = (reject = false) => {
    const overlay = npv2FakeEl('np-modal-overlay');
    const btn = npv2FakeEl('np-v2-fullscreen-btn');
    const toasts: Array<{ msg: string; kind: string }> = [];
    const doc: any = npv2FakeDocument({
      'np-modal-overlay': overlay,
      'np-v2-fullscreen-btn': btn,
    });
    doc.fullscreenElement = null;
    doc.exitFullscreen = () => {
      doc.fullscreenElement = null;
      return Promise.resolve();
    };
    overlay.requestFullscreen = () => {
      if (reject) return Promise.reject(new Error('denied'));
      doc.fullscreenElement = overlay;
      return Promise.resolve();
    };
    const factory = new Function(
      'document',
      'showToast',
      [
        extractFunction('npv2Toast', v2),
        extractFunction('npv2SetFullscreen', v2),
        extractFunction('npv2SyncFullscreenBtn', v2),
        'return { npv2SetFullscreen, npv2SyncFullscreenBtn };',
      ].join('\n'),
    );
    const api = factory(doc, (msg: string, kind = 'info') => toasts.push({ msg, kind }));
    return { api, doc, overlay, btn, toasts };
  };

  it('enters fullscreen and syncs the button state', async () => {
    const { api, doc, overlay, btn } = build();
    api.npv2SetFullscreen(true);
    await Promise.resolve();
    expect(doc.fullscreenElement).toBe(overlay);
    api.npv2SyncFullscreenBtn();
    expect(btn.classList.contains('active')).toBe(true);
    expect(btn.getAttribute('aria-pressed')).toBe('true');
  });

  it('exits fullscreen', async () => {
    const { api, doc, overlay, btn } = build();
    doc.fullscreenElement = overlay;
    api.npv2SetFullscreen(false);
    await Promise.resolve();
    expect(doc.fullscreenElement).toBe(null);
    api.npv2SyncFullscreenBtn();
    expect(btn.classList.contains('active')).toBe(false);
    expect(btn.getAttribute('aria-pressed')).toBe('false');
  });

  it('toasts when the browser blocks fullscreen', async () => {
    const { api, doc, toasts } = build(true);
    api.npv2SetFullscreen(true);
    await new Promise((r) => setTimeout(r, 20));
    expect(doc.fullscreenElement).toBe(null);
    expect(toasts.length).toBe(1);
    expect(toasts[0].msg).toMatch(/blocked/i);
  });
});

// ---------------------------------------------------------------------------
// Button behaviors: EQ toggle, speed stepping
// ---------------------------------------------------------------------------

describe('EQ toggle button', () => {
  const build = () => {
    const btn = npv2FakeEl('np-v2-eq-btn');
    const doc = npv2FakeDocument({ 'np-v2-eq-btn': btn });
    const factory = new Function(
      'document',
      [
        extractConst('NPV2', v2),
        'function npv2AttachAudioGraph() { return true; }',
        'function npv2ApplyEq() {}',
        'function npv2PersistEq() {}',
        'function npv2RenderEqUI() {}',
        extractFunction('npv2SetEqOn', v2),
        extractFunction('npv2SyncEqBtn', v2),
        'return { npv2SetEqOn, NPV2 };',
      ].join('\n'),
    );
    return { api: factory(doc) as any, btn };
  };

  it('toggles EQ state and syncs the button active/aria state', () => {
    const { api, btn } = build();
    expect(api.NPV2.eq.on).toBe(false);
    api.npv2SetEqOn(true);
    expect(api.NPV2.eq.on).toBe(true);
    expect(btn.classList.contains('active')).toBe(true);
    expect(btn.getAttribute('aria-pressed')).toBe('true');
    api.npv2SetEqOn(false);
    expect(api.NPV2.eq.on).toBe(false);
    expect(btn.classList.contains('active')).toBe(false);
    expect(btn.getAttribute('aria-pressed')).toBe('false');
  });
});

describe('speed stepping ([ and ])', () => {
  const build = () => {
    const btn = npv2FakeEl('np-v2-speed-btn');
    const doc = npv2FakeDocument({ 'np-v2-speed-btn': btn });
    const factory = new Function(
      'document',
      [
        extractConst('NPV2', v2),
        extractConst('NPV2_SPEED_PRESETS', v2),
        extractFunction('npv2ClampSpeed', v2),
        extractFunction('npv2FormatSpeed', v2),
        'function npv2Set(k, v) {}',
        'function npv2AudioEl() { return null; }',
        'function npv2Toast() {}',
        extractFunction('npv2SetSpeed', v2),
        extractFunction('npv2CycleSpeed', v2),
        'return { npv2SetSpeed, npv2CycleSpeed, NPV2 };',
      ].join('\n'),
    );
    return { api: factory(doc) as any, btn };
  };

  it('steps through presets and clamps at the endpoints', () => {
    const { api, btn } = build();
    api.npv2SetSpeed(1, { silent: true });
    api.npv2CycleSpeed(1);
    expect(api.NPV2.speed).toBe(1.25);
    api.npv2CycleSpeed(-1);
    expect(api.NPV2.speed).toBe(1);
    expect(btn.classList.contains('active')).toBe(false);
    api.npv2CycleSpeed(1);
    api.npv2CycleSpeed(1);
    expect(api.NPV2.speed).toBe(1.5);
    expect(btn.classList.contains('active')).toBe(true);
    api.npv2SetSpeed(2, { silent: true });
    api.npv2CycleSpeed(1);
    expect(api.NPV2.speed).toBe(2);
    api.npv2SetSpeed(0.5, { silent: true });
    api.npv2CycleSpeed(-1);
    expect(api.NPV2.speed).toBe(0.5);
  });

  it('marks the button active only when speed differs from 1x', () => {
    const { api, btn } = build();
    api.npv2SetSpeed(1.5, { silent: true });
    expect(btn.classList.contains('active')).toBe(true);
    api.npv2SetSpeed(1, { silent: true });
    expect(btn.classList.contains('active')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Immersive mode toggle: the expand button must work in BOTH directions
// ---------------------------------------------------------------------------

describe('immersive mode toggle', () => {
  const build = () => {
    const modal = npv2FakeEl('np-modal');
    const overlay = npv2FakeEl('np-modal-overlay');
    const exit = npv2FakeEl('np-v2-visuals-exit');
    const expand = npv2FakeEl('np-v2-visuals-expand');
    const drawer = npv2FakeEl('np-v2-visuals-drawer');
    const panels = npv2FakeEl('np-v2-visuals-panels');
    exit.classList.add('hidden');
    drawer.classList.add('hidden');
    const els: Record<string, any> = {
      'np-modal-overlay': overlay,
      'np-v2-visuals-exit': exit,
      'np-v2-visuals-expand': expand,
      'np-v2-visuals-drawer': drawer,
      'np-v2-visuals-panels': panels,
    };
    const doc: any = {
      _els: els,
      getElementById: (id: string) => els[id] ?? null,
      querySelector: (sel: string) => (sel === '.np-modal' ? modal : null),
      querySelectorAll: () => [],
    };
    const factory = new Function(
      'document',
      [
        extractConst('NPV2', v2),
        'function npv2SetBgOn() {}',
        'function npv2SetTheme() {}',
        'function npv2SyncVisualsHeader() {}',
        'function npv2VizStart() {}',
        'function npv2VisualsWakeUI() {}',
        'function npv2VisualsClearIdle() {}',
        'function npv2CloseVisualsPanel() {}',
        extractFunction('npv2ModalOpen', v2),
        extractFunction('npv2VisualsModeOn', v2),
        extractFunction('npv2SetVisualsMode', v2),
        extractFunction('npv2ToggleVisualsDrawer', v2),
        'return { NPV2, npv2VisualsModeOn, npv2SetVisualsMode, npv2ToggleVisualsDrawer };',
      ].join('\n'),
    );
    return { api: factory(doc) as any, els, modal, overlay, exit, expand, drawer, panels };
  };

  it('entering syncs the expand button to the pressed state', () => {
    const { api, expand, exit } = build();
    api.npv2SetVisualsMode(true);
    expect(api.NPV2.visualsMode).toBe(true);
    expect(expand.classList.contains('active')).toBe(true);
    expect(expand.getAttribute('aria-pressed')).toBe('true');
    expect(expand.title).toContain('Exit immersive');
    expect(exit.classList.contains('hidden')).toBe(false);
  });

  it('exiting from the same button path clears the state again', () => {
    const { api, expand, exit } = build();
    api.npv2SetVisualsMode(true);
    // this is exactly what the expand button's click handler does
    api.npv2SetVisualsMode(!api.npv2VisualsModeOn());
    expect(api.NPV2.visualsMode).toBe(false);
    expect(api.npv2VisualsModeOn()).toBe(false);
    expect(expand.classList.contains('active')).toBe(false);
    expect(expand.getAttribute('aria-pressed')).toBe('false');
    expect(expand.title).toContain('Immersive visuals mode');
    expect(exit.classList.contains('hidden')).toBe(true);
  });

  it('the toggle expression flips both ways, repeatedly', () => {
    const { api } = build();
    expect(api.npv2VisualsModeOn()).toBe(false);
    api.npv2SetVisualsMode(!api.npv2VisualsModeOn());
    expect(api.npv2VisualsModeOn()).toBe(true);
    api.npv2SetVisualsMode(!api.npv2VisualsModeOn());
    expect(api.npv2VisualsModeOn()).toBe(false);
    api.npv2SetVisualsMode(!api.npv2VisualsModeOn());
    expect(api.npv2VisualsModeOn()).toBe(true);
  });

  it('visuals mode requires the modal to be open', () => {
    const { api, overlay } = build();
    overlay.classList.add('hidden'); // modal closed
    api.npv2SetVisualsMode(true);
    expect(api.npv2VisualsModeOn()).toBe(false);
    overlay.classList.remove('hidden');
    expect(api.npv2VisualsModeOn()).toBe(true);
  });

  it('exiting also closes the immersive drawer', () => {
    const { api, drawer } = build();
    api.npv2ToggleVisualsDrawer(true);
    expect(drawer.classList.contains('hidden')).toBe(false);
    api.npv2SetVisualsMode(true);
    api.npv2SetVisualsMode(false);
    expect(drawer.classList.contains('hidden')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Drawer outside click: clicks inside the modal but outside the drawer close it
// ---------------------------------------------------------------------------

describe('drawer outside click', () => {
  const build = () => {
    const drawer = npv2FakeEl('np-v2-visuals-drawer');
    const panels = npv2FakeEl('np-v2-visuals-panels');
    const inner = npv2FakeEl('np-v2-drawer-inner');
    const hamburgerIcon = npv2FakeEl('np-v2-panels-icon');
    const elsewhere = npv2FakeEl('np-v2-bg');
    drawer.appendChild(inner);
    panels.appendChild(hamburgerIcon);
    drawer.classList.add('hidden');
    // parentNode-chain contains(), like the DOM
    for (const root of [drawer, panels]) {
      root.contains = (node: any) => {
        let n = node;
        while (n) {
          if (n === root) return true;
          n = n.parentNode;
        }
        return false;
      };
    }
    const els: Record<string, any> = {
      'np-v2-visuals-drawer': drawer,
      'np-v2-visuals-panels': panels,
    };
    const doc = npv2FakeDocument(els);
    const factory = new Function(
      'document',
      [
        extractFunction('npv2ToggleVisualsDrawer', v2),
        extractFunction('npv2DrawerOutsideClick', v2),
        'return { npv2ToggleVisualsDrawer, npv2DrawerOutsideClick };',
      ].join('\n'),
    );
    return { api: factory(doc) as any, drawer, panels, inner, hamburgerIcon, elsewhere };
  };

  it('opens and closes via the toggle', () => {
    const { api, drawer, panels } = build();
    api.npv2ToggleVisualsDrawer(true);
    expect(drawer.classList.contains('hidden')).toBe(false);
    expect(panels.classList.contains('active')).toBe(true);
    api.npv2ToggleVisualsDrawer(false);
    expect(drawer.classList.contains('hidden')).toBe(true);
    expect(panels.classList.contains('active')).toBe(false);
  });

  it('a click inside the drawer keeps it open', () => {
    const { api, drawer, inner } = build();
    api.npv2ToggleVisualsDrawer(true);
    api.npv2DrawerOutsideClick({ target: inner });
    expect(drawer.classList.contains('hidden')).toBe(false);
  });

  it('a click on the hamburger keeps it open (it toggles itself)', () => {
    const { api, drawer, hamburgerIcon } = build();
    api.npv2ToggleVisualsDrawer(true);
    api.npv2DrawerOutsideClick({ target: hamburgerIcon });
    expect(drawer.classList.contains('hidden')).toBe(false);
  });

  it('a click elsewhere inside the modal closes it', () => {
    const { api, drawer, panels, elsewhere } = build();
    api.npv2ToggleVisualsDrawer(true);
    api.npv2DrawerOutsideClick({ target: elsewhere });
    expect(drawer.classList.contains('hidden')).toBe(true);
    expect(panels.classList.contains('active')).toBe(false);
  });

  it('does nothing when the drawer is already closed', () => {
    const { api, drawer } = build();
    expect(() => api.npv2DrawerOutsideClick({ target: drawer })).not.toThrow();
    expect(drawer.classList.contains('hidden')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Smoothed spectrum bins: fast attack, slow release
// ---------------------------------------------------------------------------

describe('smoothed spectrum bins', () => {
  const build = () => {
    const factory = new Function(
      [
        extractConst('NPV2_THEME_STATE', v2),
        extractFunction('npv2ThemeState', v2),
        extractFunction('npv2Bin', v2),
        extractFunction('npv2F', v2),
        extractFunction('npv2Ease', v2),
        extractFunction('npv2BinS', v2),
        'return { npv2BinS };',
      ].join('\n'),
    );
    const api = factory() as any;
    const freq = new Array(32).fill(0);
    const S = { freq, energy: 0.5, t: 0 };
    const setAll = (v: number) => freq.fill(Math.round(v * 255));
    return { api, S, setAll };
  };

  it('starts at zero and rises toward the target', () => {
    const { api, S, setAll } = build();
    setAll(1);
    const first = api.npv2BinS(S, 't1', 0, 8);
    expect(first).toBeGreaterThan(0);
    expect(first).toBeLessThan(1);
    const second = api.npv2BinS(S, 't1', 0, 8);
    expect(second).toBeGreaterThan(first);
  });

  it('attacks fast on rising energy', () => {
    const { api, S, setAll } = build();
    setAll(0.2);
    for (let i = 0; i < 10; i++) api.npv2BinS(S, 't2', 0, 8);
    setAll(1);
    const before = api.npv2BinS(S, 't2', 0, 8);
    // 0.55 attack: one frame covers more than half the remaining distance
    expect(before).toBeGreaterThan(0.2 + (1 - 0.2) * 0.5);
  });

  it('releases slowly on falling energy', () => {
    const { api, S, setAll } = build();
    setAll(1);
    for (let i = 0; i < 10; i++) api.npv2BinS(S, 't3', 0, 8);
    setAll(0);
    const after = api.npv2BinS(S, 't3', 0, 8);
    // 0.10 release: one frame keeps most of the value
    expect(after).toBeGreaterThan(0.8);
  });

  it('keeps independent state per key', () => {
    const { api, S, setAll } = build();
    setAll(1);
    api.npv2BinS(S, 'a', 0, 8);
    setAll(0);
    const fresh = api.npv2BinS(S, 'b', 0, 8);
    expect(fresh).toBe(0);
  });
});

describe('song-accurate analysis: log bands + spectral-flux beat', () => {
  const build = () => {
    const factory = new Function(
      [
        extractConst('NPV2_VIZ_BANDS', v2),
        extractConst('NPV2_VIZ_FMIN', v2),
        extractConst('NPV2_VIZ_FMAX', v2),
        extractConst('NPV2_VIZ_FFT', v2),
        extractFunction('npv2LogBandRanges', v2),
        extractFunction('npv2MapLogBands', v2),
        extractFunction('npv2FluxBeat', v2),
        'return { npv2LogBandRanges, npv2MapLogBands, npv2FluxBeat, NPV2_VIZ_BANDS, NPV2_VIZ_FMIN, NPV2_VIZ_FMAX, NPV2_VIZ_FFT };',
      ].join('\n'),
    );
    return factory() as any;
  };

  it('covers 40 Hz–16 kHz with contiguous, ordered bands', () => {
    const api = build();
    const ranges = api.npv2LogBandRanges(48000, 2048, 64, 40, 16000);
    expect(ranges).toHaveLength(64);
    // Band 0 starts at bin 1 because bin 0 is DC. At 48 kHz one 2048-point
    // bin is ~23 Hz, so the 40 Hz target is below FFT resolution and low
    // bands necessarily share/repeat bins; that is expected, not a bug.
    expect(ranges[0][0]).toBeGreaterThanOrEqual(1);
    expect(ranges[0][0] * (48000 / 2048)).toBeLessThan(60);
    // Last band reaches up toward 16 kHz.
    const lastHiHz = ranges[63][1] * (48000 / 2048);
    expect(lastHiHz).toBeGreaterThan(12000);
    expect(lastHiHz).toBeLessThanOrEqual(24000);
    // Ordered and gap-free: each band starts at/after the previous start and
    // not far past the previous end. Adjacent low bands may overlap because
    // they can share the same FFT bin(s).
    for (let b = 1; b < 64; b++) {
      expect(ranges[b][0]).toBeGreaterThanOrEqual(ranges[b - 1][0]);
      expect(ranges[b][1]).toBeGreaterThanOrEqual(ranges[b][0]);
      expect(ranges[b][0]).toBeLessThanOrEqual(ranges[b - 1][1] + 2);
    }
  });

  it('puts a 440 Hz tone in the right band and nowhere else', () => {
    const api = build();
    const sr = 48000,
      fft = 2048;
    const ranges = api.npv2LogBandRanges(sr, fft, 64, 40, 16000);
    const raw = new Array(fft / 2).fill(0);
    const toneBin = Math.round(440 / (sr / fft)); // ≈ 19
    raw[toneBin] = 255;
    const out = new Array(64).fill(0);
    api.npv2MapLogBands(raw, ranges, out);
    // The band(s) containing bin 19 read hot. (A tone can sit on a shared
    // boundary bin, lighting two adjacent bands — never more.)
    const hot = out.map((v: number, i: number) => (v > 0 ? i : -1)).filter((i: number) => i >= 0);
    expect(hot.length).toBeGreaterThanOrEqual(1);
    expect(hot.length).toBeLessThanOrEqual(2);
    for (const h of hot) {
      expect(ranges[h][0]).toBeLessThanOrEqual(toneBin);
      expect(ranges[h][1]).toBeGreaterThanOrEqual(toneBin);
    }
    // …and a 440 Hz tone does not leak into the bass or the top octave.
    expect(out[0]).toBe(0);
    expect(out[63]).toBe(0);
  });

  it('bass and treble land in opposite ends of the band array', () => {
    const api = build();
    const sr = 48000,
      fft = 2048;
    const ranges = api.npv2LogBandRanges(sr, fft, 64, 40, 16000);
    const raw = new Array(fft / 2).fill(0);
    raw[Math.round(55 / (sr / fft))] = 255; // A1, low bass
    raw[Math.round(12000 / (sr / fft))] = 255; // high treble
    const out = new Array(64).fill(0);
    api.npv2MapLogBands(raw, ranges, out);
    // Wide high bands average a pure tone down, so any positive reading counts.
    const bassBand = out.findIndex((v: number) => v > 0);
    const trebBand = out.findLastIndex((v: number) => v > 0);
    expect(bassBand).toBeLessThan(8);
    expect(trebBand).toBeGreaterThan(50);
    expect(trebBand).toBeGreaterThan(bassBand);
  });

  it('fires the beat on a sudden onset and not on a steady drone', () => {
    const api = build();
    const st = { prev: new Float32Array(64), hist: [], beat: 0 };
    const dt = 1 / 60;
    const quiet = new Array(64).fill(40);
    // Settle the history on a quiet drone — no false beats.
    for (let f = 0; f < 60; f++) api.npv2FluxBeat(st, quiet, dt);
    expect(st.beat).toBeLessThan(0.4);
    // Sudden broadband onset (a kick / snare hit): beat fires.
    const hit = quiet.map((v) => v + 120);
    const beat = api.npv2FluxBeat(st, hit, dt);
    expect(beat).toBeGreaterThan(0.9);
  });

  it('does not pin the beat on a constant loud bassline', () => {
    const api = build();
    const st = { prev: new Float32Array(64), hist: [], beat: 0 };
    const dt = 1 / 60;
    // Loud from the start: flux adapts, beat stays down after the attack.
    const loud = new Array(64).fill(200);
    for (let f = 0; f < 120; f++) api.npv2FluxBeat(st, loud, dt);
    expect(st.beat).toBeLessThan(0.4);
    // …but a *new* hit on top still registers.
    const hit = loud.map((v, i) => (i < 8 ? 255 : v));
    const beat = api.npv2FluxBeat(st, hit, dt);
    expect(beat).toBeGreaterThan(0.9);
  });

  it('decays the beat envelope back to zero', () => {
    const api = build();
    const st = { prev: new Float32Array(64), hist: [], beat: 1 };
    const flat = new Array(64).fill(40);
    for (let f = 0; f < 120; f++) api.npv2FluxBeat(st, flat, 1 / 60);
    expect(st.beat).toBe(0);
  });

  it('keeps digital silence from self-triggering', () => {
    const api = build();
    const st = { prev: new Float32Array(64), hist: [], beat: 0 };
    const silent = new Array(64).fill(0);
    for (let f = 0; f < 120; f++) api.npv2FluxBeat(st, silent, 1 / 60);
    expect(st.beat).toBe(0);
  });
});

describe('the frame loop owns no beat logic of its own', () => {
  // npv2VizFrame used to re-run the old bass-threshold trigger and decay the
  // beat AFTER npv2ReadAudio had already done spectral flux + decay. a steady
  // loud bassline re-fired it every few frames and every beat decayed twice
  // as fast. real frame, real audio read, real analyser + flux; only the web
  // audio nodes, canvas and rAF are stubs.
  const build = (bandValue: number) => {
    const factory = new Function(
      'stubs',
      [
        'const { npAudioContext, npAnalyser, requestAnimationFrame, document } = stubs;',
        'let isPlaying = true;',
        extractConst('NPV2_VIZ_BANDS', v2),
        extractConst('NPV2_VIZ_FMIN', v2),
        extractConst('NPV2_VIZ_FMAX', v2),
        extractConst('NPV2_VIZ_FFT', v2),
        'let npv2VizAnalyser = null, npv2VizBandRanges = null, npv2VizSr = 0, npv2VizRaw = null, npv2VizBeatSt = null;',
        extractFunction('npv2LogBandRanges', v2),
        extractFunction('npv2MapLogBands', v2),
        extractFunction('npv2FluxBeat', v2),
        extractFunction('npv2VizBeatState', v2),
        extractFunction('npv2EnsureVizAnalyser', v2),
        extractFunction('npv2IdleSynth', v2),
        extractFunction('npv2IsPlaying', v2),
        extractFunction('npv2ReadAudio', v2),
        'const NPV2_VIZ = { freq: new Uint8Array(NPV2_VIZ_BANDS), wave: new Uint8Array(64), energy: 0, idle: true, beat: 0, pal: { r: 1, g: 2, b: 3 }, q: 1 };',
        "const NPV2 = { bgOn: true, theme: 'aurora', vizLast: 0, vizT: 0, vizEnergy: 1, reduceMotion: false, vizPalette: 'auto', palette: { r: 1, g: 2, b: 3 }, vizAutocycle: '0', vizLastCycle: 0, canvas: { width: 10, height: 10 }, ctx: { clearRect() {} } };",
        'const NPV2_PALETTES = {}; const NPV2_PAINT = {};',
        extractConst('NPV2_TRAILS', v2),
        extractFunction('npv2F', v2),
        extractFunction('npv2Ease', v2),
        extractFunction('npv2Analogous', v2),
        extractFunction('npv2EaseColor', v2),
        'function npv2ArtImage() { return null; }',
        'function npv2DrawTrail() {}',
        'function npv2KeepTrail() {}',
        'function npv2ModalOpen() { return true; }',
        'function npv2SizeCanvas() { return true; }',
        'function npv2AutocycleSeconds() { return 0; }',
        'function npv2CycleTheme() {}',
        'function npv2UpdateWakeLock() {}',
        extractFunction('npv2VizFrame', v2),
        'return { npv2VizFrame, NPV2_VIZ, npv2VizBeatState };',
      ].join('\n'),
    );
    const analyserNode = {
      fftSize: 0,
      smoothingTimeConstant: 0,
      getByteFrequencyData(arr: Uint8Array) {
        arr.fill(bandValue);
      },
      getByteTimeDomainData(arr: Uint8Array) {
        arr.fill(128);
      },
      connect() {},
    };
    return factory({
      npAudioContext: { sampleRate: 48000, createAnalyser: () => analyserNode },
      npAnalyser: analyserNode,
      requestAnimationFrame: () => 1,
      document: { hidden: false },
    }) as any;
  };

  it('a steady loud bassline does not keep re-firing the beat', () => {
    const api = build(220); // bass well over the old 0.55 threshold, never changing
    let fires = 0;
    let prev = 0;
    for (let f = 1; f <= 240; f++) {
      api.npv2VizFrame(f * (1000 / 60));
      if (api.NPV2_VIZ.beat > 0.9 && prev < 0.9) fires++;
      prev = api.NPV2_VIZ.beat;
    }
    expect(fires).toBeLessThanOrEqual(1); // the opening attack, then nothing
    expect(api.NPV2_VIZ.beat).toBe(0);
  });

  it('decays a beat once per frame, not twice', () => {
    const api = build(40);
    api.npv2VizFrame(1000 / 60); // settle
    api.npv2VizBeatState().beat = 1; // a beat just fired in the flux detector
    api.npv2VizFrame(2 * (1000 / 60));
    // one decay of dt * 2.4 at 60 fps: 1 - 0.04
    expect(api.NPV2_VIZ.beat).toBeCloseTo(1 - (1 / 60) * 2.4, 3);
  });
});

// ---------------------------------------------------------------------------
// Best-in-class pass: frame-rate independence, once-per-beat effects, a real
// scope, spectrogram rows, the cover on the vinyl, real accent colors, trails
// ---------------------------------------------------------------------------

const PAINT_FNS = [
  'npv2ThemeList',
  'npv2ThemeIds',
  'npv2ThemeState',
  'npv2Bin',
  'npv2BinS',
  'npv2Css',
  'npv2Css2',
  'npv2PalA',
  'npv2Pal2',
  'npv2Q',
  'npv2BeatAmp',
  'npv2F',
  'npv2Ease',
  'npv2Onset',
  'npv2Spawn',
  'npv2ScopeTrace',
  'npv2Soft',
];

function paintHarness() {
  return v2Harness(PAINT_FNS, ['NPV2_PAINT', 'NPV2_THEME_STATE', 'NPV2_SOFT']);
}

function recordingCtx() {
  const calls: { name: string; args: unknown[] }[] = [];
  const grad = { addColorStop: () => {} };
  const ctx = new Proxy(
    {},
    {
      get(_t, p: string) {
        if (p === 'createLinearGradient' || p === 'createRadialGradient') return () => grad;
        return (...args: unknown[]) => {
          calls.push({ name: p, args });
        };
      },
      set() {
        return true;
      },
    },
  );
  return { ctx, calls };
}

const vizFrame = (over: Record<string, unknown> = {}) => ({
  freq: Uint8Array.from({ length: 64 }, (_, i) => (i < 8 ? 220 : 60)),
  wave: Uint8Array.from({ length: 64 }, (_, i) => 128 + Math.round(90 * Math.sin(i / 4))),
  energy: 0.6,
  beat: 0,
  onset: false,
  dt: 1 / 60,
  t: 1,
  idle: false,
  pal: { r: 120, g: 80, b: 200 },
  pal2: { r: 80, g: 160, b: 220 },
  ...over,
});

describe('frame-rate independence', () => {
  it('the frame factor follows dt and defaults to one frame', () => {
    const { npv2F, npv2Ease } = v2Harness(['npv2F', 'npv2Ease']);
    expect(npv2F({ dt: 1 / 60 })).toBeCloseTo(1, 5);
    expect(npv2F({ dt: 1 / 144 })).toBeCloseTo(60 / 144, 5);
    expect(npv2F({})).toBe(1);
    // two 120 Hz eases land where one 60 Hz ease does
    const k60 = npv2Ease(0.2, { dt: 1 / 60 });
    const k120 = npv2Ease(0.2, { dt: 1 / 120 });
    expect(1 - (1 - k120) * (1 - k120)).toBeCloseTo(k60, 6);
  });

  it('smoothed bins reach the same place in one second at 60 or 144 Hz', () => {
    const run = (hz: number) => {
      const { npv2BinS } = paintHarness();
      const S = { freq: new Array(64).fill(255), dt: 1 / hz };
      let v = 0;
      for (let i = 0; i < hz / 4; i++) v = npv2BinS(S, 'fr' + hz, 0, 8);
      return v;
    };
    expect(run(144)).toBeCloseTo(run(60), 2);
  });
});

describe('beat effects fire once per beat', () => {
  // a beat envelope stays high for several frames; only the first is the onset
  const beatFrames = (n: number) =>
    Array.from({ length: n }, (_, i) => vizFrame({ beat: 1 - i * 0.04, onset: i === 0 }));

  it('bloom adds its three rings once, not on every high frame', () => {
    const { NPV2_PAINT, NPV2_THEME_STATE } = paintHarness();
    for (const S of beatFrames(8)) NPV2_PAINT.bloom(recordingCtx().ctx, 1280, 800, S);
    expect(NPV2_THEME_STATE.bloom.rings.filter((r: any) => !r.gentle)).toHaveLength(3);
  });

  it('warp throws three jump streaks per beat', () => {
    const { NPV2_PAINT, NPV2_THEME_STATE } = paintHarness();
    const [first, ...rest] = beatFrames(8);
    NPV2_PAINT.warp(recordingCtx().ctx, 1280, 800, first);
    expect(NPV2_THEME_STATE.warp.jumps).toHaveLength(3);
    // the rest of the beat's high frames add none (they only age out)
    for (const S of rest) NPV2_PAINT.warp(recordingCtx().ctx, 1280, 800, S);
    expect(NPV2_THEME_STATE.warp.jumps.length).toBeLessThanOrEqual(3);
  });

  it('kaleidoscope flips once on a beat, then waits before flipping again', () => {
    const { NPV2_PAINT, NPV2_THEME_STATE } = paintHarness();
    for (const S of beatFrames(6)) NPV2_PAINT.kaleido(recordingCtx().ctx, 1280, 800, S);
    expect(NPV2_THEME_STATE.kaleido.dir).toBe(-1);
    // another beat half a second later is inside the cooldown
    for (let i = 0; i < 30; i++) NPV2_PAINT.kaleido(recordingCtx().ctx, 1280, 800, vizFrame());
    NPV2_PAINT.kaleido(recordingCtx().ctx, 1280, 800, vizFrame({ beat: 1, onset: true }));
    expect(NPV2_THEME_STATE.kaleido.dir).toBe(-1);
    // after the cooldown it flips back
    for (let i = 0; i < 120; i++) NPV2_PAINT.kaleido(recordingCtx().ctx, 1280, 800, vizFrame());
    NPV2_PAINT.kaleido(recordingCtx().ctx, 1280, 800, vizFrame({ beat: 1, onset: true }));
    expect(NPV2_THEME_STATE.kaleido.dir).toBe(1);
  });
});

describe('a real oscilloscope', () => {
  const sine = (phase: number, n = 2048, period = 300) =>
    Uint8Array.from({ length: n }, (_, i) =>
      Math.round(128 + 100 * Math.sin(((i + phase) / period) * 2 * Math.PI)),
    );

  it('triggers on a rising zero crossing so the wave stands still', () => {
    const { npv2ScopeTrace } = v2Harness(['npv2ScopeTrace']);
    const a = npv2ScopeTrace(sine(0), new Float32Array(256));
    const b = npv2ScopeTrace(sine(117), new Float32Array(256));
    expect(Math.abs(a[0])).toBeLessThan(0.05);
    expect(a[5]).toBeGreaterThan(a[0]); // rising
    let diff = 0;
    for (let i = 0; i < 256; i++) diff = Math.max(diff, Math.abs(a[i] - b[i]));
    expect(diff).toBeLessThan(0.08); // two phases, the same picture
  });

  it('reads the whole window, not 64 samples', () => {
    const { npv2ScopeTrace } = v2Harness(['npv2ScopeTrace']);
    // a 300-sample period: a 64-sample slice shows a fifth of one cycle,
    // the half window shows over three full cycles
    const out = npv2ScopeTrace(sine(0), new Float32Array(256));
    let crossings = 0;
    for (let i = 1; i < out.length; i++) if (out[i - 1] < 0 && out[i] >= 0) crossings++;
    expect(crossings).toBeGreaterThanOrEqual(2);
  });

  it('silence is a flat line, not noise', () => {
    const { npv2ScopeTrace } = v2Harness(['npv2ScopeTrace']);
    const out = npv2ScopeTrace(new Uint8Array(2048).fill(128), new Float32Array(64));
    expect(Array.from(out).every((v) => v === 0)).toBe(true);
  });
});

describe('dot plane is a 3D spectrogram', () => {
  it('the near row is now and rows behind it are earlier moments', () => {
    const { NPV2_PAINT, NPV2_THEME_STATE } = paintHarness();
    const loud = vizFrame({ freq: new Uint8Array(64).fill(240), dt: 0.1 });
    const quiet = vizFrame({ freq: new Uint8Array(64).fill(0), dt: 0.1 });
    for (let i = 0; i < 6; i++) NPV2_PAINT.dotplane(recordingCtx().ctx, 1280, 800, loud);
    for (let i = 0; i < 20; i++) NPV2_PAINT.dotplane(recordingCtx().ctx, 1280, 800, quiet);
    const rows = NPV2_THEME_STATE['dotplane-hist'].rows;
    expect(rows.length).toBe(16);
    expect(rows[0][0]).toBeLessThan(rows[rows.length - 1][0] + 1e-9);
    expect(rows[0][0]).toBeLessThan(0.1); // newest: quiet
  });
});

describe('vinyl wears the album cover', () => {
  it('draws the cover on the label when it has loaded', () => {
    const { NPV2_PAINT } = paintHarness();
    const art = { naturalWidth: 300 };
    const { ctx, calls } = recordingCtx();
    NPV2_PAINT.vinyl(ctx, 1280, 800, vizFrame({ art }));
    expect(calls.some((c) => c.name === 'drawImage' && c.args[0] === art)).toBe(true);
    expect(calls.some((c) => c.name === 'clip')).toBe(true);
  });

  it('falls back to the title initial without art', () => {
    const { NPV2_PAINT } = paintHarness();
    const { ctx, calls } = recordingCtx();
    NPV2_PAINT.vinyl(ctx, 1280, 800, vizFrame({ art: null, title: 'Numb' }));
    expect(calls.some((c) => c.name === 'fillText' && c.args[0] === 'N')).toBe(true);
  });
});

describe('soft layers', () => {
  it('without an offscreen canvas it draws straight onto the frame at full size', () => {
    const { npv2Soft, NPV2_SOFT } = paintHarness();
    NPV2_SOFT.test = null; // what a failed offscreen canvas caches
    const seen: number[][] = [];
    const ctx = { save() {}, restore() {} };
    npv2Soft(ctx, 1280, 800, 0.3, 'test', (c: unknown, w: number, h: number) => {
      expect(c).toBe(ctx);
      seen.push([w, h]);
    });
    expect(seen).toEqual([[1280, 800]]);
  });
});

describe('accent colors belong to the cover', () => {
  it('the analogous fallback stays in the same color family', () => {
    const { npv2Analogous } = v2Harness(['npv2Analogous']);
    const green = { r: 30, g: 200, b: 80 };
    const acc = npv2Analogous(green);
    // the old channel swap made a green cover's accent red-dominant
    expect(acc.r).toBeLessThan(acc.g);
  });

  it('v1 picks a hue-distinct second color from the cover when it has one', () => {
    const factory = new Function(
      [
        extractFunction('npHue', v1),
        extractFunction('npPickAccentColor', v1),
        'return { npPickAccentColor };',
      ].join('\n'),
    );
    const { npPickAccentColor } = factory() as any;
    const bin = (r: number, g: number, b: number, w: number) => ({
      r: r * 10,
      g: g * 10,
      b: b * 10,
      n: 10,
      w,
    });
    const red = bin(220, 40, 40, 10);
    const orange = bin(230, 120, 40, 6); // too close in hue to count
    const blue = bin(40, 80, 220, 4);
    const out = npPickAccentColor([red, orange, blue], red);
    expect(out[2]).toBeGreaterThan(out[0]); // blue wins
    // a one-color cover gets a neighbour, not a clash
    const solo = npPickAccentColor([red], red);
    expect(solo[0]).toBeGreaterThan(solo[2]);
  });
});

describe('real trails', () => {
  it('the previous frame comes back faded by keep, scaled for the frame rate', () => {
    const factory = new Function(
      'buf',
      [
        "const NPV2 = { theme: 'scope', reduceMotion: false };",
        extractFunction('npv2F', v2),
        'function npv2Buffer() { return buf; }',
        extractFunction('npv2DrawTrail', v2),
        'return { npv2DrawTrail };',
      ].join('\n'),
    );
    const buf = { c: {}, ready: true, theme: 'scope' };
    const { npv2DrawTrail } = factory(buf) as any;
    const alphas: number[] = [];
    const drawn: unknown[] = [];
    const ctx = {
      save() {},
      restore() {},
      translate() {},
      scale() {},
      rotate() {},
      drawImage(img: unknown) {
        drawn.push(img);
      },
      set globalAlpha(v: number) {
        alphas.push(v);
      },
    };
    npv2DrawTrail(ctx, 100, 100, { keep: 0.8 }, { dt: 1 / 60 });
    npv2DrawTrail(ctx, 100, 100, { keep: 0.8 }, { dt: 1 / 120 });
    expect(drawn).toEqual([buf.c, buf.c]);
    expect(alphas[0]).toBeCloseTo(0.8, 5);
    expect(alphas[1]).toBeCloseTo(Math.sqrt(0.8), 5);
  });

  it('a different theme never inherits the old trail', () => {
    const factory = new Function(
      'buf',
      [
        "const NPV2 = { theme: 'warp', reduceMotion: false };",
        extractFunction('npv2F', v2),
        'function npv2Buffer() { return buf; }',
        extractFunction('npv2DrawTrail', v2),
        'return { npv2DrawTrail };',
      ].join('\n'),
    );
    const { npv2DrawTrail } = factory({ c: {}, ready: true, theme: 'scope' }) as any;
    let drew = false;
    const ctx = {
      drawImage() {
        drew = true;
      },
    };
    npv2DrawTrail(ctx, 100, 100, { keep: 0.8 }, { dt: 1 / 60 });
    expect(drew).toBe(false);
  });
});

describe('the onset flag', () => {
  it('is true only on the frame the beat fires', () => {
    const factory = new Function(
      'stubs',
      [
        'const { npAudioContext, npAnalyser } = stubs;',
        'let isPlaying = true;',
        extractConst('NPV2_VIZ_BANDS', v2),
        extractConst('NPV2_VIZ_FMIN', v2),
        extractConst('NPV2_VIZ_FMAX', v2),
        extractConst('NPV2_VIZ_FFT', v2),
        'let npv2VizAnalyser = null, npv2VizBandRanges = null, npv2VizSr = 0, npv2VizRaw = null, npv2VizBeatSt = null;',
        extractFunction('npv2LogBandRanges', v2),
        extractFunction('npv2MapLogBands', v2),
        extractFunction('npv2FluxBeat', v2),
        extractFunction('npv2VizBeatState', v2),
        extractFunction('npv2EnsureVizAnalyser', v2),
        extractFunction('npv2IdleSynth', v2),
        extractFunction('npv2IsPlaying', v2),
        extractFunction('npv2ReadAudio', v2),
        'return { npv2ReadAudio };',
      ].join('\n'),
    );
    let level = 30;
    const node = {
      getByteFrequencyData(a: Uint8Array) {
        a.fill(level);
      },
      getByteTimeDomainData(a: Uint8Array) {
        a.fill(128);
      },
      connect() {},
    };
    const { npv2ReadAudio } = factory({
      npAudioContext: { sampleRate: 48000, createAnalyser: () => node },
      npAnalyser: node,
    }) as any;
    const S: any = { freq: new Uint8Array(64), wave: new Uint8Array(64), beat: 0 };
    const onsets: boolean[] = [];
    for (let f = 0; f < 60; f++) {
      npv2ReadAudio(S, f / 60, 1 / 60);
      onsets.push(S.onset);
    }
    level = 230; // a hit
    for (let f = 0; f < 10; f++) {
      npv2ReadAudio(S, 1 + f / 60, 1 / 60);
      onsets.push(S.onset);
    }
    expect(onsets.slice(60).filter(Boolean)).toHaveLength(1);
    expect(onsets[60]).toBe(true);
    expect(S.waveFull).toHaveLength(2048);
  });
});
