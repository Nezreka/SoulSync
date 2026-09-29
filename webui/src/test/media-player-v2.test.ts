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

  const trackA = { title: 'Blue in Green', artist: 'Miles Davis', album: 'Kind of Blue', id: '1' };
  const trackB = { title: 'So What', artist: 'Miles Davis', album: 'Kind of Blue', id: '2' };

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
    ['npv2ThemeList', 'npv2ThemeIds', 'npv2ThemeState', 'npv2Bin', 'npv2Css'],
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
    expect(h.state()).toEqual({ npSleepMinutes: 0, npSleepEndOfTrack: false, hasTimer: false });
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
    expect(h.state()).toEqual({ npSleepMinutes: 0, npSleepEndOfTrack: true, hasTimer: false });
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
