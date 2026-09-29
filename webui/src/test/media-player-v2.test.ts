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
    ],
    ['NPV2_PAINT', 'NPV2_THEME_STATE'],
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
      freq: Uint8Array.from({ length: 32 }, (_, i) => (i * 37) % 256),
      wave: Uint8Array.from({ length: 64 }, (_, i) => 128 + Math.round(100 * Math.sin(i / 4))),
      energy: idle ? 0.2 : 0.65,
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
