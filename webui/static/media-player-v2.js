// ============================================================================
// SOULSYNC NOW PLAYING THEATER (media-player-v2.js)
//
// A best-in-class layer on top of media-player.js (v1). v1 owns playback,
// the queue, lyrics, crossfade, sleep and the mini player; this file owns the
// immersive experience:
//
//   - Full-bleed animated visual themes, incl. old-school Windows Media
//     Player throwbacks (Barscope, Oscilloscope, Waves, Plasma, Spikes)
//   - 10-band equalizer + loudness normalization (Web Audio, zero new deps)
//   - Playback speed, A-B loop, extended sleep timer UI
//   - Tabbed side panel: Queue / History / Lyrics / Details / Settings
//   - Playback history, per-track audio details, keyboard-shortcut overlay
//   - Screen wake-lock while playing
//
// DESIGN RULES (learned the hard way):
//   - v1 is never rewritten here. Integration happens through (a) reading v1
//     globals, (b) MutationObservers on v1 DOM, (c) two tiny v1 hooks
//     (crossfade duration, extended sleep steps) documented below.
//   - Every v1 reference is guarded with typeof checks: if v1 changes, the
//     theater degrades to a pretty shell instead of breaking playback.
//   - Pure logic lives in npv2* functions with no DOM access so vitest can
//     extract and test them directly (see webui/src/test/media-player-v2.test.ts).
// ============================================================================

'use strict';

// ---------------------------------------------------------------------------
// Storage (profile-scoped, mirrors v1's npAutoDownloadStorageKey pattern)
// ---------------------------------------------------------------------------

function npv2ProfileName() {
    try {
        const p = (typeof window !== 'undefined' && window._currentProfileName) || 'default';
        return String(p);
    } catch (e) { return 'default'; }
}

function npv2Key(name) {
    return 'soulsync-npv2-' + name + ':' + npv2ProfileName();
}

function npv2Get(name, fallback) {
    try {
        const v = localStorage.getItem(npv2Key(name));
        return v === null || v === undefined ? fallback : v;
    } catch (e) { return fallback; }
}

function npv2Set(name, value) {
    try { localStorage.setItem(npv2Key(name), String(value)); } catch (e) {}
}

// ---------------------------------------------------------------------------
// Pure helpers (no DOM — unit tested)
// ---------------------------------------------------------------------------

const NPV2_SPEED_PRESETS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

// Snap any value to the nearest supported playback speed.
function npv2ClampSpeed(v) {
    const n = Number(v);
    if (!isFinite(n)) return 1;
    let best = NPV2_SPEED_PRESETS[0];
    for (const p of NPV2_SPEED_PRESETS) {
        if (Math.abs(p - n) < Math.abs(best - n)) best = p;
    }
    return best;
}

function npv2FormatSpeed(v) {
    const n = npv2ClampSpeed(v);
    return (Number.isInteger(n) ? n.toFixed(0) : String(n)) + '×';
}

// A-B loop state machine. Pure: given {mode, a, b} and now, returns the next
// state. 'off' -> sets A -> 'a'; 'a' -> sets B (if valid) -> 'b' (looping);
// 'b' -> clears -> 'off'.
function npv2AbNext(state, now) {
    const s = state && typeof state === 'object' ? state : { mode: 'off', a: 0, b: 0 };
    const t = Math.max(0, Number(now) || 0);
    if (s.mode === 'off') return { mode: 'a', a: t, b: 0 };
    if (s.mode === 'a') {
        if (t <= s.a + 0.25) return { mode: 'a', a: t, b: 0 }; // re-arm A
        return { mode: 'b', a: s.a, b: t };
    }
    return { mode: 'off', a: 0, b: 0 };
}

function npv2AbLabel(state) {
    const s = state && typeof state === 'object' ? state : { mode: 'off' };
    if (s.mode === 'a') return 'A–B · A set';
    if (s.mode === 'b') return 'A–B · looping';
    return 'A–B loop';
}

// History: newest-first, dedupes consecutive repeats of the same track,
// capped. Pure so the cap/dedupe logic is tested, not just trusted.
function npv2TrackIdentity(t) {
    if (!t) return '';
    const bits = [t.title, t.artist, t.album, t.file_path || t.filename || '', t.id || ''].map(
        (x) => String(x === undefined || x === null ? '' : x).trim().toLowerCase());
    return bits.join('‖');
}

function npv2HistoryPush(list, item, cap) {
    const arr = Array.isArray(list) ? list.slice() : [];
    const limit = Math.max(1, Number(cap) || 50);
    if (!item) return arr;
    const id = npv2TrackIdentity(item.entry || item);
    if (arr.length && npv2TrackIdentity(arr[0].entry || arr[0]) === id) return arr;
    arr.unshift(item);
    return arr.slice(0, limit);
}

function npv2TimeAgo(ts, nowMs) {
    const now = Number(nowMs) || Date.now();
    const diff = Math.max(0, now - Number(ts));
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + 'h ago';
    const days = Math.floor(hrs / 24);
    if (days < 30) return days + 'd ago';
    return new Date(Number(ts)).toLocaleDateString();
}

function npv2FormatExt(path) {
    const m = /\.([a-z0-9]{2,5})$/i.exec(String(path || ''));
    return m ? m[1].toUpperCase() : '';
}

// ---------------------------------------------------------------------------
// Equalizer: bands, presets (pure data — unit tested)
// ---------------------------------------------------------------------------

const NPV2_EQ_FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
const NPV2_EQ_RANGE_DB = 12;

const NPV2_EQ_PRESETS = {
    'Flat':          [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    'Bass Booster':  [6, 5, 4, 2, 0, 0, 0, 0, 1, 2],
    'Treble Booster':[-1, 0, 0, 0, 0, 1, 2, 3, 4, 5],
    'Vocal':         [-2, -1, 0, 1, 3, 4, 4, 2, 0, -1],
    'Dance':         [5, 4, 3, 1, 0, -1, 1, 2, 3, 4],
    'Rock':          [4, 3, 2, 1, 0, 0, 1, 2, 3, 4],
    'Jazz':          [3, 2, 1, 0, 0, 0, 1, 2, 3, 2],
    'Classical':     [3, 2, 1, 0, 0, 0, -1, 1, 2, 3],
    'Acoustic':      [2, 1, 0, 1, 2, 2, 1, 0, 1, 2],
    'Electronic':    [4, 3, 2, 0, -1, 0, 1, 2, 3, 4],
    'Podcast':       [-3, -2, -1, 0, 2, 3, 3, 2, 0, -2],
};

function npv2EqPresetGains(name) {
    const g = NPV2_EQ_PRESETS[name];
    return Array.isArray(g) && g.length === NPV2_EQ_FREQS.length ? g.slice() : null;
}

function npv2EqPresetNames() {
    return Object.keys(NPV2_EQ_PRESETS);
}

// Clamp + round a dB value to the slider step.
function npv2ClampDb(v) {
    const n = Number(v);
    if (!isFinite(n)) return 0;
    return Math.max(-NPV2_EQ_RANGE_DB, Math.min(NPV2_EQ_RANGE_DB, Math.round(n * 2) / 2));
}

// ---------------------------------------------------------------------------
// Visual themes — the Windows Media Player throwback collection + modern vibes
//
// Every painter receives (ctx, w, h, S) where S = {
//   freq: Uint8Array(64) 0..255 log-spaced bands, 40 Hz–16 kHz,
//   wave: Uint8Array(64) 0..255 time-domain samples,
//   energy: 0..1 overall level, t: seconds, idle: bool (no real audio),
//   beat: 0..1 onset envelope (spectral flux), pal: {r,g,b} album-art palette,
//   css: 'r,g,b' string }
// Painters must be defensive: ctx may be a stub in tests.
// ---------------------------------------------------------------------------

// Curated palettes. 'auto' means "use the album-art palette".
const NPV2_PALETTES = {
    ultraviolet: { r: 138, g: 76, b: 246 },
    ocean: { r: 56, g: 150, b: 255 },
    ember: { r: 255, g: 112, b: 67 },
    forest: { r: 52, g: 199, b: 123 },
    rose: { r: 244, g: 63, b: 140 },
    mono: { r: 208, g: 210, b: 216 },
};

function npv2PaletteList() {
    return [
        { id: 'auto', name: 'Album art' },
        { id: 'ultraviolet', name: 'Ultraviolet' },
        { id: 'ocean', name: 'Ocean' },
        { id: 'ember', name: 'Ember' },
        { id: 'forest', name: 'Forest' },
        { id: 'rose', name: 'Rose' },
        { id: 'mono', name: 'Mono' },
    ];
}

// Canvas spec per quality setting. Pure — tested in vitest.
function npv2QualitySpec(name) {
    switch (name) {
        case 'high': return { dpr: 2, q: 1.25 };
        case 'balanced': return { dpr: 1.25, q: 0.8 };
        case 'lite': return { dpr: 1, q: 0.5 };
        default: return { dpr: 1.5, q: 1 }; // 'auto'
    }
}

// Autocycle interval in seconds. 'track' = -1 (change per track). Pure.
function npv2AutocycleSeconds(v) {
    if (v === 'track') return -1;
    const n = parseInt(v, 10);
    return isFinite(n) && n > 0 ? n : 0;
}

function npv2ClampVizEnergy(v) {
    v = parseFloat(v);
    if (!isFinite(v)) return 1;
    return Math.min(1.5, Math.max(0.5, v));
}

function npv2ClampVizDim(v) {
    v = parseFloat(v);
    if (!isFinite(v)) return 0;
    return Math.min(0.6, Math.max(0, v));
}

function npv2ThemeList() {
    return [
        { id: 'barscope', name: 'Barscope', blurb: 'WMP Bars, resurrected' },
        { id: 'scope', name: 'Oscilloscope', blurb: 'WMP Scope phosphor glow' },
        { id: 'waves', name: 'Waves', blurb: 'WMP Waves ribbons' },
        { id: 'plasma', name: 'Plasma', blurb: 'WMP Plasma blobs' },
        { id: 'spikes', name: 'Spikes', blurb: 'WMP Spikes radial burst' },
        { id: 'aurora', name: 'Aurora', blurb: 'Northern-lights drift' },
        { id: 'stardust', name: 'Stardust', blurb: 'Beat-reactive particles' },
        { id: 'vinyl', name: 'Vinyl', blurb: 'Spinning wax, tonearm and all' },
        { id: 'tunnel', name: 'Tunnel', blurb: 'Spectrum wormhole' },
        { id: 'battery', name: 'Battery', blurb: 'WMP Battery sparks rising' },
        { id: 'dotplane', name: 'Dot Plane', blurb: 'WMP dot grid pulsing' },
        { id: 'alchemy', name: 'Alchemy', blurb: 'WMP morphing blobs' },
        { id: 'fountain', name: 'Fountain', blurb: 'Particle fountain, beat-fed' },
        { id: 'kaleido', name: 'Kaleidoscope', blurb: 'Mirrored spectrum bloom' },
        { id: 'warp', name: 'Warp', blurb: 'Starfield at light speed' },
        { id: 'nebula', name: 'Nebula', blurb: 'Drifting deep-space clouds' },
        { id: 'bloom', name: 'Bloom', blurb: 'Beat rings expanding out' },
        { id: 'none', name: 'Off', blurb: 'Just the ambient glow' },
    ];
}

function npv2ThemeIds() {
    return npv2ThemeList().map((t) => t.id);
}

// Per-theme mutable scratch state, created lazily.
const NPV2_THEME_STATE = {};

function npv2ThemeState(id, init) {
    if (!NPV2_THEME_STATE[id]) NPV2_THEME_STATE[id] = init();
    return NPV2_THEME_STATE[id];
}

function npv2Bin(S, i, count) {
    // Average a slice of the analyser bands into `count` segments. The slice
    // start is proportional so painters with more segments than bands
    // (e.g. 110 spikes over 64 bands) still spread across the spectrum
    // instead of clamping every high segment onto the last band.
    const n = S.freq.length;
    const per = Math.max(1, Math.floor(n / count));
    let sum = 0;
    const start = Math.min(n - per, Math.floor(i * n / count));
    for (let b = 0; b < per && start + b < n; b++) sum += S.freq[start + b];
    return (sum / per) / 255;
}

// Smoothed spectrum bins — fast attack, slow release — so motion reads as
// musical instead of frame-jittery. Per-painter state keyed by `key`.
function npv2BinS(S, key, i, count) {
    const target = npv2Bin(S, i, count);
    const st = npv2ThemeState('bins-' + key, () => ({ v: [] }));
    while (st.v.length < count) st.v.push(0);
    const cur = st.v[i] || 0;
    const k = npv2Ease(target > cur ? 0.55 : 0.10, S);
    const nv = cur + (target - cur) * k;
    st.v[i] = nv;
    return nv;
}

function npv2Css(S, alpha) {
    return 'rgba(' + S.pal.r + ',' + S.pal.g + ',' + S.pal.b + ',' + alpha + ')';
}

// ---------------------------------------------------------------------------
// Song-accurate analysis — dedicated analyser + log bands + spectral-flux beat
//
// v1's shared analyser runs fftSize 64 (32 bins, each ~750 Hz wide at 48 kHz:
// kick, bass guitar and low toms all land in bin 0). That is fine for v1's
// mini spectrum, but a visualizer that should *reflect the song* needs real
// frequency resolution. So the v2 engine taps a dedicated analyser
// (fftSize 2048, gentle built-in smoothing — v2 does its own musical
// smoothing) off the shared one and maps its 1024 bins onto 64 log-spaced
// bands from 40 Hz to 16 kHz. Log spacing matches how we hear: each band is
// roughly a constant musical interval, so a vocal and a cymbal get the same
// visual weight as a kick.
//
// Beat detection is spectral flux, not a bass threshold: it measures how
// much the spectrum *rose* since the last frame and fires when that rise
// clearly exceeds its recent average. A fingerpicked guitar triggers it as
// reliably as a four-on-the-floor kick; a constant loud bassline does not
// pin it.
// ---------------------------------------------------------------------------

const NPV2_VIZ_BANDS = 64;
const NPV2_VIZ_FMIN = 40;     // Hz — below this is rumble, not music
const NPV2_VIZ_FMAX = 16000;  // Hz — above this is air few speakers reproduce
const NPV2_VIZ_FFT = 2048;

let npv2VizAnalyser = null;    // dedicated analyser, created lazily
let npv2VizBandRanges = null;  // [[loBin, hiBin]] per band for the current sr
let npv2VizSr = 0;
let npv2VizRaw = null;         // Uint8Array(1024) scratch
let npv2VizBeatSt = null;      // spectral-flux state

function npv2EnsureVizAnalyser() {
    if (npv2VizAnalyser) return npv2VizAnalyser;
    let AC = null, analyser = null;
    try {
        AC = (typeof npAudioContext !== 'undefined' && npAudioContext) || null;
        analyser = (typeof npAnalyser !== 'undefined' && npAnalyser) || null;
    } catch (e) { return null; }
    if (!AC || !analyser) return null;
    try {
        const va = AC.createAnalyser();
        va.fftSize = NPV2_VIZ_FFT;
        // Gentle built-in smoothing: v2 does its own fast-attack /
        // slow-release smoothing per painter, so the raw feed stays lively.
        va.smoothingTimeConstant = 0.5;
        // Tap after the shared analyser. An analyser node analyses whatever
        // flows into it — it needs no output connection to produce data.
        // Deliberately NOT connected to destination: the shared analyser
        // already feeds destination, so forwarding through this tap would
        // sum a second copy of the signal there (~+6 dB louder). This also
        // survives v2's EQ rewire (which only touches source->…->analyser).
        analyser.connect(va);
        npv2VizAnalyser = va;
        return va;
    } catch (e) { return null; }
}

// Log-spaced band edges over the FFT bins. Pure — tested in vitest.
// With a 2048-point FFT the low bands are narrower than one FFT bin, so
// adjacent low bands can share/repeat bins; that limited low-frequency
// resolution is expected and the mapping still stays ordered/gap-free.
function npv2LogBandRanges(sr, fftSize, bandCount, fMin, fMax) {
    const binHz = sr / fftSize;
    const maxBin = fftSize / 2 - 1;
    const ratio = Math.pow(fMax / fMin, 1 / bandCount);
    const ranges = [];
    for (let b = 0; b < bandCount; b++) {
        // Bin 0 is DC — start at 1 so silence stays silent.
        const lo = Math.min(maxBin, Math.max(1, Math.floor(fMin * Math.pow(ratio, b) / binHz)));
        const hi = Math.min(maxBin, Math.max(lo, Math.ceil(fMin * Math.pow(ratio, b + 1) / binHz) - 1));
        ranges.push([lo, hi]);
    }
    return ranges;
}

// Average the raw FFT bins into the log bands. Pure — tested in vitest.
function npv2MapLogBands(raw, ranges, out) {
    for (let b = 0; b < ranges.length; b++) {
        const lo = ranges[b][0], hi = ranges[b][1];
        let sum = 0, n = 0;
        for (let k = lo; k <= hi && k < raw.length; k++) { sum += raw[k]; n++; }
        out[b] = n ? Math.round(sum / n) : 0;
    }
    return out;
}

// Spectral-flux onset detection. `st` is {prev: Float32Array, hist: [], beat}
// and is resized defensively so tests can use any band count. Returns the
// 0..1 beat envelope. Pure apart from the state object — tested in vitest.
function npv2FluxBeat(st, bands, dt) {
    if (!st.prev || st.prev.length !== bands.length) st.prev = new Float32Array(bands.length);
    if (!Array.isArray(st.hist)) st.hist = [];
    // Flux: how much the spectrum rose since the last frame, 0..~1.
    let flux = 0;
    for (let i = 0; i < bands.length; i++) {
        const d = bands[i] - st.prev[i];
        if (d > 0) flux += d;
        st.prev[i] = bands[i];
    }
    flux /= bands.length * 255;
    st.hist.push(flux);
    if (st.hist.length > 86) st.hist.shift(); // ~1.4 s of context at 60 fps
    let mean = 0;
    for (let h = 0; h < st.hist.length; h++) mean += st.hist[h];
    mean /= Math.max(1, st.hist.length);
    // Onset when the rise clearly exceeds its recent average. The floor
    // keeps digital silence from self-triggering.
    if (st.beat < 0.35 && flux > Math.max(0.012, mean * 1.6)) st.beat = 1;
    st.beat = Math.max(0, st.beat - dt * 2.4);
    if (typeof st.beat !== 'number' || !isFinite(st.beat)) st.beat = 0;
    return st.beat;
}

function npv2VizBeatState() {
    if (!npv2VizBeatSt) npv2VizBeatSt = { prev: new Float32Array(NPV2_VIZ_BANDS), hist: [], beat: 0 };
    return npv2VizBeatSt;
}

// ---------------------------------------------------------------------------
// Painter helpers — defensive: the vitest stub only provides
// {freq, wave, energy, t, idle, pal}, so every extra S field has a fallback.
// ---------------------------------------------------------------------------

function npv2Q(S) {
    const q = S && S.q;
    return (typeof q === 'number' && isFinite(q) && q > 0) ? q : 1;
}

function npv2BeatAmp(S) {
    const b = S && S.beat;
    return (typeof b === 'number' && isFinite(b) && b > 0) ? Math.min(1, b) : 0;
}

function npv2Pal2(S) {
    if (S && S.pal2 && isFinite(S.pal2.r) && isFinite(S.pal2.g) && isFinite(S.pal2.b)) return S.pal2;
    return { r: 160, g: 140, b: 255 };
}

function npv2Css2(S, alpha) {
    const p = npv2Pal2(S);
    return 'rgba(' + Math.round(p.r) + ',' + Math.round(p.g) + ',' + Math.round(p.b) + ',' + alpha + ')';
}

// Format a palette object {r,g,b} as rgba() with the given alpha — lets
// painters reuse one palette color at several alphas without regex hacks.
function npv2PalA(p, alpha) {
    const c = (p && isFinite(p.r) && isFinite(p.g) && isFinite(p.b)) ? p : { r: 120, g: 80, b: 200 };
    return 'rgba(' + Math.round(c.r) + ',' + Math.round(c.g) + ',' + Math.round(c.b) + ',' + alpha + ')';
}

// Frame factor: how many 60 fps frames this frame stands for. Every per-frame
// step (decay, drift, spawn rate) multiplies by it, so motion runs at the same
// speed on a 60, 120 or 144 Hz screen. 1 when the frame carries no dt (tests).
function npv2F(S) {
    const dt = S && S.dt;
    return (typeof dt === 'number' && isFinite(dt) && dt > 0) ? Math.min(6, dt * 60) : 1;
}

// Per-frame easing factor k (tuned at 60 fps) for this frame's dt. Pure.
function npv2Ease(k, S) {
    return 1 - Math.pow(1 - k, npv2F(S));
}

// True only on the frame a beat fires. Spawn-once effects use this: checking
// `beat > x` fires on every frame the envelope stays high, 5-10 times a beat
// (more on a 144 Hz screen).
function npv2Onset(S) {
    return !!(S && S.onset === true);
}

// Probabilistic spawn count for `perFrame` items at this frame's dt: 0.3 per
// frame at 144 Hz still averages right instead of rounding to nothing.
function npv2Spawn(perFrame, S) {
    const n = Math.max(0, perFrame) * npv2F(S);
    const whole = Math.floor(n);
    return whole + (Math.random() < n - whole ? 1 : 0);
}

// Oscilloscope trace: trigger on the first rising zero crossing (with a
// little hysteresis, so noise can't trigger it) like a real scope, so the
// wave stands still frame to frame, then resample half the window into
// `out` as -1..1. Pure.
function npv2ScopeTrace(src, out) {
    const n = src ? src.length : 0;
    if (!n) { out.fill(0); return out; }
    const half = Math.max(2, Math.floor(n / 2));
    let start = 0, armed = false;
    for (let i = 0; i < half; i++) {
        if (src[i] < 124) armed = true;
        else if (armed && src[i] >= 128) { start = i; break; }
    }
    const span = Math.min(n - start, half);
    for (let p = 0; p < out.length; p++) {
        const idx = start + Math.floor((p * span) / out.length);
        out[p] = (src[Math.min(n - 1, idx)] - 128) / 128;
    }
    return out;
}

// Soft layers at low resolution. Clouds, blobs and glows are blurry by design,
// so drawing them at a third of the size and scaling up looks the same and
// costs ~10x less fill. Falls back to drawing straight onto ctx when no
// offscreen canvas exists (tests, ancient browsers).
const NPV2_SOFT = {};

function npv2Soft(ctx, w, h, scale, key, draw, mode) {
    let buf = NPV2_SOFT[key];
    if (buf === undefined) {
        buf = null;
        try {
            if (typeof document !== 'undefined' && document.createElement) {
                const c = document.createElement('canvas');
                const cx = c.getContext && c.getContext('2d');
                if (cx) buf = { c, ctx: cx };
            }
        } catch (e) { buf = null; }
        NPV2_SOFT[key] = buf;
    }
    if (!buf || typeof ctx.drawImage !== 'function') { draw(ctx, w, h); return; }
    const bw = Math.max(2, Math.round(w * scale)), bh = Math.max(2, Math.round(h * scale));
    if (buf.c.width !== bw || buf.c.height !== bh) { buf.c.width = bw; buf.c.height = bh; }
    buf.ctx.setTransform(1, 0, 0, 1, 0, 0);
    buf.ctx.globalCompositeOperation = 'source-over';
    buf.ctx.globalAlpha = 1;
    buf.ctx.clearRect(0, 0, bw, bh);
    draw(buf.ctx, bw, bh);
    ctx.save();
    if (mode) ctx.globalCompositeOperation = mode;
    // bilinear is plenty for content that is blurry by design; 'high'
    // resampling cost more than the gradients it replaced
    ctx.imageSmoothingEnabled = true;
    try { ctx.imageSmoothingQuality = 'low'; } catch (e) {}
    ctx.drawImage(buf.c, 0, 0, w, h);
    ctx.restore();
}

// Real persistence (MilkDrop-style feedback): themes listed here see the
// previous frame drawn back under the new one, faded by `keep` (per 60 fps
// frame) and nudged by `zoom` / `spin`, so motion leaves genuine trails
// instead of faked echo copies.
const NPV2_TRAILS = {
    scope: { keep: 0.80 },
    spikes: { keep: 0.70, zoom: 0.012, spin: 0.003 },
    warp: { keep: 0.74, zoom: 0.02 },
    fountain: { keep: 0.68 },
    bloom: { keep: 0.70, zoom: 0.008 },
    tunnel: { keep: 0.55, zoom: 0.03 },
};

const NPV2_PAINT = {
    // --- WMP Bars: mirrored bars firing from the center line ----------------
    barscope(ctx, w, h, S) {
        const st = npv2ThemeState('barscope', () => ({ peaks: [] }));
        const N = 56;
        const cy = h * 0.52;
        const maxH = h * 0.42;
        const beat = npv2BeatAmp(S);
        while (st.peaks.length < N) st.peaks.push(0);
        // faint backdrop wash so the bars sit in something
        const wash = ctx.createLinearGradient(0, 0, 0, h);
        wash.addColorStop(0, npv2Css(S, 0));
        wash.addColorStop(0.52, npv2Css(S, 0.10 + S.energy * 0.08));
        wash.addColorStop(1, npv2Css(S, 0));
        ctx.fillStyle = wash;
        ctx.fillRect(0, 0, w, h);
        const bw = w / N;
        for (let i = 0; i < N; i++) {
            const v = npv2BinS(S, 'barscope', i, N);
            st.peaks[i] = Math.max(v, st.peaks[i] - 0.008 * npv2F(S));
            const bh = Math.max(2, v * maxH);
            const x = i * bw + bw * 0.18;
            const ww = bw * 0.64;
            // bar body: palette core, accent + near-white at the tips
            const g = ctx.createLinearGradient(0, cy - bh, 0, cy + bh);
            g.addColorStop(0, npv2Css2(S, 0.95));
            g.addColorStop(0.42, npv2Css(S, 0.85));
            g.addColorStop(1, npv2Css(S, 0.10));
            ctx.fillStyle = g;
            if (ctx.roundRect) {
                ctx.beginPath();
                ctx.roundRect(x, cy - bh, ww, bh * 2, Math.min(ww * 0.4, 4));
                ctx.fill();
            } else {
                ctx.fillRect(x, cy - bh, ww, bh * 2);
            }
            // bright tip edges catch the light
            ctx.fillStyle = 'rgba(255,255,255,' + (0.25 + v * 0.55).toFixed(3) + ')';
            ctx.fillRect(x, cy - bh, ww, 1.5);
            ctx.fillRect(x, cy + bh - 1.5, ww, 1.5);
            // soft mirror shimmer below the line — sells the "stage"
            const sh = Math.min(bh * 0.5, h * 0.10);
            const mg = ctx.createLinearGradient(0, cy + bh, 0, cy + bh + sh);
            mg.addColorStop(0, npv2Css(S, (0.22 * v).toFixed(3)));
            mg.addColorStop(1, npv2Css(S, 0));
            ctx.fillStyle = mg;
            ctx.fillRect(x, cy + bh, ww, sh);
            // falling peak cap, pure WMP — flashes on the beat
            const py = st.peaks[i] * maxH;
            ctx.fillStyle = 'rgba(255,255,255,' + (0.22 + st.peaks[i] * 0.45 + beat * 0.3).toFixed(3) + ')';
            ctx.fillRect(x, cy - py - 2, ww, 2);
            ctx.fillRect(x, cy + py, ww, 2);
        }
        ctx.fillStyle = 'rgba(255,255,255,' + (0.10 + beat * 0.15).toFixed(3) + ')';
        ctx.fillRect(0, cy - 0.5, w, 1);
    },

    // --- WMP Scope: phosphor waveform ---------------------------------------
    // A real oscilloscope: the whole 2048-sample window, triggered on a rising
    // zero crossing so the wave stands still, resampled to a smooth trace.
    // The afterglow is real persistence (NPV2_TRAILS), not shifted copies.
    scope(ctx, w, h, S) {
        const cy = h * 0.52;
        const beat = npv2BeatAmp(S);
        const st = npv2ThemeState('scope', () => ({ gain: 1, pts: null }));
        // light backdrop: the trail system fades the old trace underneath
        ctx.fillStyle = 'rgba(2,6,4,0.30)';
        ctx.fillRect(0, 0, w, h);
        ctx.strokeStyle = 'rgba(255,255,255,0.05)';
        ctx.lineWidth = 1;
        for (let gy = 0.2; gy < 1; gy += 0.2) {
            ctx.beginPath(); ctx.moveTo(0, h * gy); ctx.lineTo(w, h * gy); ctx.stroke();
        }
        ctx.beginPath(); ctx.moveTo(w / 2, 0); ctx.lineTo(w / 2, h); ctx.stroke();
        // corner ticks frame the scope face
        ctx.strokeStyle = 'rgba(255,255,255,0.14)';
        const tk = 14;
        [[8, 8, 1, 1], [w - 8, 8, -1, 1], [8, h - 8, 1, -1], [w - 8, h - 8, -1, -1]].forEach(([tx, ty, sx, sy]) => {
            ctx.beginPath();
            ctx.moveTo(tx, ty + tk * sy); ctx.lineTo(tx, ty); ctx.lineTo(tx + tk * sx, ty);
            ctx.stroke();
        });
        const src = (S.waveFull && S.waveFull.length > 64) ? S.waveFull : S.wave;
        const n = Math.max(64, Math.round(384 * npv2Q(S)));
        if (!st.pts || st.pts.length !== n) st.pts = new Float32Array(n);
        npv2ScopeTrace(src, st.pts);
        // auto-gain: normalize to the observed peak so the trace always
        // fills the scope face, quiet track or loud, like a real scope
        let peak = 0;
        for (let i = 0; i < n; i++) { const d = Math.abs(st.pts[i]); if (d > peak) peak = d; }
        const tgt = Math.min((0.72 + S.energy * 0.28) / Math.max(peak * 2, 0.12), 6);
        st.gain += (tgt - st.gain) * npv2Ease(tgt > st.gain ? 0.25 : 0.04, S);
        const amp = h * 0.30 * st.gain;
        const trace = (alpha, width, glow) => {
            ctx.beginPath();
            for (let i = 0; i < n; i++) {
                const x = (i / (n - 1)) * w;
                const y = cy - st.pts[i] * amp;
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            ctx.strokeStyle = npv2Css(S, alpha);
            ctx.lineWidth = width;
            ctx.lineJoin = 'round';
            ctx.shadowColor = npv2Css(S, 0.8);
            ctx.shadowBlur = glow;
            ctx.stroke();
            ctx.shadowBlur = 0;
        };
        trace(0.16, 8, 0);                          // soft phosphor halo
        trace(0.9, 2, 14 + beat * 10);              // hot core with bloom
        ctx.strokeStyle = 'rgba(255,255,255,' + (0.35 + beat * 0.4).toFixed(3) + ')';
        ctx.lineWidth = 0.8;
        ctx.stroke();                               // white-hot filament on the core
        if (beat > 0.05) {
            ctx.fillStyle = 'rgba(255,255,255,' + (beat * 0.8).toFixed(3) + ')';
            ctx.beginPath(); ctx.arc(6, cy, 2 + beat * 3, 0, 6.2832); ctx.fill();
        }
    },

    // --- WMP Waves: layered translucent ribbons ------------------------------
    waves(ctx, w, h, S) {
        const beat = npv2BeatAmp(S);
        // deep water: dark top falling into a palette-tinted floor
        const deep = ctx.createLinearGradient(0, 0, 0, h);
        deep.addColorStop(0, '#02040a');
        deep.addColorStop(0.55, '#04060f');
        deep.addColorStop(1, npv2Css(S, 0.10 + S.energy * 0.10));
        ctx.fillStyle = deep;
        ctx.fillRect(0, 0, w, h);
        const layers = [
            { amp: 0.10, speed: 0.9, alpha: 0.16, yOff: 0.38, accent: true },
            { amp: 0.16, speed: 0.7, alpha: 0.30, yOff: 0.46, accent: false },
            { amp: 0.22, speed: 0.45, alpha: 0.22, yOff: 0.56, accent: true },
            { amp: 0.13, speed: 1.15, alpha: 0.40, yOff: 0.64, accent: false },
        ];
        layers.forEach((L, li) => {
            const e = (0.35 + S.energy * 1.4) * (1 + beat * 0.25);
            const steps = 90;
            const pts = [];
            for (let i = 0; i <= steps; i++) {
                const x = (i / steps) * w;
                const ph = i * 0.09 + S.t * L.speed * (li % 2 ? -1 : 1) * 2;
                const mod = 0.6 + 0.4 * npv2Bin(S, i % 32, 32);
                const y = h * L.yOff + Math.sin(ph) * h * L.amp * e * mod
                    + Math.sin(ph * 2.7 + li * 1.7) * h * L.amp * 0.35 * e;
                pts.push([x, y]);
            }
            // the front ribbon gets a filled body, not just a stroke
            if (li === 3) {
                const fg = ctx.createLinearGradient(0, h * (L.yOff - L.amp), 0, h);
                fg.addColorStop(0, L.accent ? npv2Css2(S, 0) : npv2Css(S, 0));
                fg.addColorStop(0.45, (L.accent ? npv2Css2(S, L.alpha * 0.55) : npv2Css(S, L.alpha * 0.55)));
                fg.addColorStop(1, (L.accent ? npv2Css2(S, 0.02) : npv2Css(S, 0.02)));
                ctx.beginPath();
                pts.forEach(([x, y], i) => { if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
                ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
                ctx.fillStyle = fg;
                ctx.fill();
            }
            ctx.beginPath();
            pts.forEach(([x, y], i) => { if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
            ctx.strokeStyle = L.accent ? npv2Css2(S, L.alpha) : npv2Css(S, L.alpha);
            ctx.lineWidth = 30 - li * 6;
            ctx.lineCap = 'round';
            if (li === 3) { ctx.shadowColor = npv2Css(S, 0.7); ctx.shadowBlur = 14; }
            ctx.stroke();
            ctx.shadowBlur = 0;
            // crest foam on the front ribbon
            if (li === 3) {
                ctx.beginPath();
                pts.forEach(([x, y], i) => { if (i === 0) ctx.moveTo(x, y - 8); else ctx.lineTo(x, y - 8); });
                ctx.strokeStyle = 'rgba(255,255,255,' + (0.10 + S.energy * 0.12).toFixed(3) + ')';
                ctx.lineWidth = 1.5;
                ctx.stroke();
            }
        });
    },

    // --- WMP Plasma: drifting energy blobs, additive --------------------------
    plasma(ctx, w, h, S) {
        const st = npv2ThemeState('plasma', () => ({ seeds: [0.7, 1.9, 3.1, 4.4, 5.6, 0.3, 2.5, 5.1], sparks: [] }));
        const beat = npv2BeatAmp(S);
        ctx.fillStyle = '#04040c';
        ctx.fillRect(0, 0, w, h);
        const R = Math.max(w, h) * 0.30;
        // blob positions first (full-res coordinates), shared by the soft
        // layer and the filaments
        const blobs = st.seeds.map((sd, i) => {
            const sp = 0.22 + i * 0.06;
            const x = w / 2 + Math.cos(S.t * sp + sd * 2.1) * w * 0.30 * (0.5 + S.energy * 0.7);
            const y = h / 2 + Math.sin(S.t * sp * 1.3 + sd * 3.7) * h * 0.28;
            const r = R * (0.55 + 0.45 * npv2BinS(S, 'plasma', Math.min(i * 5, 31), 32) + S.energy * 0.25) * (1 + beat * 0.35);
            st['p' + i] = { x, y };
            return { sd, i, x, y, r, accent: i % 3 === 2 };
        });
        const rotA = Math.sin(S.t * 0.05) * 0.35;
        // the blobs are pure glow: draw them at a third of the size
        npv2Soft(ctx, w, h, 0.34, 'plasma', (c, sw, sh) => {
            const k = sw / w;
            c.globalCompositeOperation = 'lighter';
            c.translate(sw / 2, sh / 2); c.rotate(rotA); c.translate(-sw / 2, -sh / 2);
            for (const bl of blobs) {
                const r = bl.r * k;
                const rx = r * (1 + 0.28 * Math.sin(S.t * 0.6 + bl.sd * 4));
                const ry = r * (1 - 0.22 * Math.sin(S.t * 0.6 + bl.sd * 4));
                const tilt = Math.sin(S.t * 0.3 + bl.sd) * 0.8;
                const col = bl.accent ? npv2Css2(S, 0.30) : npv2Css(S, 0.34);
                const clear = bl.accent ? npv2Css2(S, 0) : npv2Css(S, 0);
                c.save();
                c.translate(bl.x * k, bl.y * k);
                c.rotate(tilt);
                c.scale(rx / r, ry / r);
                const g = c.createRadialGradient(0, 0, 0, 0, 0, r);
                g.addColorStop(0, col);
                g.addColorStop(1, clear);
                c.fillStyle = g;
                c.beginPath(); c.arc(0, 0, r, 0, 6.2832); c.fill();
                const core = bl.accent ? npv2Css2(S, 0.55 + beat * 0.3) : npv2Css(S, 0.55 + beat * 0.3);
                const cg = c.createRadialGradient(0, 0, 0, 0, 0, r * 0.38);
                cg.addColorStop(0, core);
                cg.addColorStop(1, clear);
                c.fillStyle = cg;
                c.beginPath(); c.arc(0, 0, r * 0.38, 0, 6.2832); c.fill();
                c.restore();
            }
        }, 'lighter');
        ctx.globalCompositeOperation = 'lighter';
        ctx.save();
        ctx.translate(w / 2, h / 2);
        ctx.rotate(rotA);
        ctx.translate(-w / 2, -h / 2);
        // electric filaments between near neighbors — curved arcs that bow
        // and breathe; straight lines read as a constellation, not plasma
        ctx.lineWidth = 1.2;
        for (let i = 0; i < st.seeds.length; i++) {
            for (let j = i + 1; j < st.seeds.length; j++) {
                const a = st['p' + i], b = st['p' + j];
                if (!a || !b) continue;
                const dx = b.x - a.x, dy = b.y - a.y;
                const d = Math.hypot(dx, dy);
                if (d > R * 2.2 || d < 4) continue;
                const al = (1 - d / (R * 2.2)) * (0.10 + beat * 0.22);
                const bow = d * 0.28 * Math.sin(S.t * 1.1 + i * 2.1 + j * 1.3);
                const nx = -dy / d, ny = dx / d;
                ctx.strokeStyle = (i + j) % 3 === 0 ? npv2Css2(S, al.toFixed(3)) : npv2Css(S, al.toFixed(3));
                ctx.beginPath();
                ctx.moveTo(a.x, a.y);
                ctx.quadraticCurveTo((a.x + b.x) / 2 + nx * bow, (a.y + b.y) / 2 + ny * bow, b.x, b.y);
                ctx.stroke();
            }
        }
        ctx.restore();
        // bright sparks orbiting the blob field — tiny, fast, alive
        if (st.sparks.length < 14 && npv2Spawn(0.10 + S.energy * 0.15, S) > 0) {
            const a = Math.random() * 6.2832;
            st.sparks.push({ a, r: Math.max(w, h) * (0.18 + Math.random() * 0.22), sp: 0.6 + Math.random() * 1.4, life: 1 });
        }
        for (let i = st.sparks.length - 1; i >= 0; i--) {
            const sp = st.sparks[i];
            sp.a += sp.sp * 0.016 * npv2F(S);
            sp.life -= 0.012 * npv2F(S);
            if (sp.life <= 0) { st.sparks.splice(i, 1); continue; }
            const sx = w / 2 + Math.cos(sp.a) * sp.r;
            const sy = h / 2 + Math.sin(sp.a) * sp.r * 0.8;
            ctx.fillStyle = 'rgba(255,255,255,' + (sp.life * 0.7).toFixed(3) + ')';
            ctx.beginPath(); ctx.arc(sx, sy, 1.4, 0, 6.2832); ctx.fill();
        }
        ctx.globalCompositeOperation = 'source-over';
        // vignette to keep the edges cinematic
        const vg = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.35, w / 2, h / 2, Math.max(w, h) * 0.75);
        vg.addColorStop(0, 'rgba(0,0,0,0)');
        vg.addColorStop(1, 'rgba(0,0,0,0.45)');
        ctx.fillStyle = vg;
        ctx.fillRect(0, 0, w, h);
    },

    // --- WMP Spikes: radial burst ---------------------------------------------
    spikes(ctx, w, h, S) {
        const cx = w / 2, cy = h / 2;
        const N = 110;
        const base = Math.min(w, h) * 0.14;
        const maxL = Math.min(w, h) * 0.46;
        const beat = npv2BeatAmp(S);
        ctx.fillStyle = 'rgba(3,3,8,0.6)';
        ctx.fillRect(0, 0, w, h);
        // faint outer guide ring
        ctx.strokeStyle = npv2Css(S, 0.12);
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(cx, cy, base + maxL, 0, 6.2832); ctx.stroke();
        // ghost ring: shorter, fainter spikes counter-rotating behind the main burst
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(-S.t * 0.06);
        ctx.fillStyle = npv2Css(S, 0.10);
        for (let i = 0; i < N; i += 2) {
            const v = npv2BinS(S, 'spikes', (i + N / 2) % N, N);
            const len = base + (0.30 + v * 0.70) * maxL * 0.55 * (0.6 + S.energy * 0.6);
            const a = (i / N) * 6.2832;
            const halfW = 0.016;
            ctx.beginPath();
            ctx.moveTo(Math.cos(a - halfW) * base, Math.sin(a - halfW) * base);
            ctx.lineTo(Math.cos(a) * len, Math.sin(a) * len);
            ctx.lineTo(Math.cos(a + halfW) * base, Math.sin(a + halfW) * base);
            ctx.closePath(); ctx.fill();
        }
        ctx.restore();
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(S.t * 0.10 + beat * 0.15);
        for (let i = 0; i < N; i++) {
            const v = npv2BinS(S, 'spikes', i, N);
            // generous floor: the burst reads even on quiet passages, not
            // just when the music is hot
            const len = base + (0.30 + v * 0.70) * maxL * (0.6 + S.energy * 0.6);
            const a = (i / N) * 6.2832;
            const hot = v > 0.75;
            // tapered spike: a triangle reads as a burst, a line reads as a chart
            const halfW = hot ? 0.020 : 0.014;
            const ca = Math.cos(a), sa = Math.sin(a);
            ctx.fillStyle = hot ? npv2Css2(S, 0.9) : npv2Css(S, 0.22 + v * 0.6);
            ctx.beginPath();
            ctx.moveTo(ca * base - sa * halfW * base, sa * base + ca * halfW * base);
            ctx.lineTo(ca * len, sa * len);
            ctx.lineTo(ca * base + sa * halfW * base, sa * base - ca * halfW * base);
            ctx.closePath(); ctx.fill();
            if (v > 0.55) {
                ctx.fillStyle = 'rgba(255,255,255,' + (v * 0.5).toFixed(3) + ')';
                ctx.beginPath(); ctx.arc(ca * len, sa * len, 1.6, 0, 6.2832); ctx.fill();
            }
        }
        ctx.restore();
        // core: solid disc + beat shockwave ring
        const coreR = base * (0.7 + beat * 0.5);
        const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 2);
        cg.addColorStop(0, 'rgba(255,255,255,0.85)');
        cg.addColorStop(0.4, npv2Css(S, 0.7));
        cg.addColorStop(1, npv2Css(S, 0));
        ctx.fillStyle = cg;
        ctx.beginPath(); ctx.arc(cx, cy, coreR * 2, 0, 6.2832); ctx.fill();
        if (beat > 0.1) {
            ctx.strokeStyle = npv2Css(S, (beat * 0.6).toFixed(3));
            ctx.lineWidth = 2;
            ctx.beginPath(); ctx.arc(cx, cy, base + (1 - beat) * maxL * 0.9, 0, 6.2832); ctx.stroke();
        }
    },

    // --- Aurora: starfield + drifting light ribbons ---------------------------
    aurora(ctx, w, h, S) {
        const st = npv2ThemeState('aurora', () => {
            const stars = [];
            for (let i = 0; i < 150; i++) {
                stars.push({ x: Math.random(), y: Math.random() * 0.75, r: Math.random() * 1.4 + 0.3, p: Math.random() * 6.28, far: Math.random() < 0.4 });
            }
            const s = { stars, shoot: 0, shootX: 0, shootY: 0, nextShoot: 4 };
            for (let ri = 0; ri < 3; ri++)
                for (let k = 0; k < 8; k++) {
                    s['rayJ' + ri + '_' + k] = Math.random();
                    s['rayT' + ri + '_' + k] = Math.random();
                }
            return s;
        });
        const beat = npv2BeatAmp(S);
        // night-sky gradient
        const sky = ctx.createLinearGradient(0, 0, 0, h);
        sky.addColorStop(0, '#02030a');
        sky.addColorStop(0.6, '#060818');
        sky.addColorStop(1, '#0a0a18');
        ctx.fillStyle = sky;
        ctx.fillRect(0, 0, w, h);
        for (const s of st.stars) {
            const tw = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(S.t * (s.far ? 0.7 : 1.4) + s.p));
            ctx.fillStyle = 'rgba(255,255,255,' + (tw * (s.far ? 0.3 : 0.55)).toFixed(3) + ')';
            ctx.beginPath(); ctx.arc(s.x * w, s.y * h, s.r, 0, 6.2832); ctx.fill();
        }
        // occasional shooting star
        st.nextShoot -= 0.016 * npv2F(S);
        if (st.nextShoot <= 0 && st.shoot <= 0) {
            st.shoot = 1; st.shootX = Math.random() * w * 0.7 + w * 0.15; st.shootY = Math.random() * h * 0.25;
            st.nextShoot = 5 + Math.random() * 8;
        }
        if (st.shoot > 0) {
            st.shoot -= 0.03 * npv2F(S);
            const sx = st.shootX + (1 - st.shoot) * w * 0.18;
            const sy = st.shootY + (1 - st.shoot) * h * 0.10;
            const tg = ctx.createLinearGradient(sx, sy, sx - w * 0.12, sy - h * 0.07);
            tg.addColorStop(0, 'rgba(255,255,255,' + (st.shoot * 0.9).toFixed(3) + ')');
            tg.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.strokeStyle = tg;
            ctx.lineWidth = 2;
            ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(sx - w * 0.12, sy - h * 0.07); ctx.stroke();
        }
        const ribbons = [
            { y: 0.28, amp: 0.09, speed: 0.35, alpha: 0.30, accent: false },
            { y: 0.42, amp: 0.13, speed: 0.22, alpha: 0.24, accent: true },
            { y: 0.55, amp: 0.08, speed: 0.5, alpha: 0.16, accent: false },
        ];
        ribbons.forEach((R, ri) => {
            const pal = R.accent ? npv2Pal2(S) : (S && S.pal);
            const grad = ctx.createLinearGradient(0, h * (R.y - R.amp * 2), 0, h * (R.y + R.amp * 2));
            const aMid = (R.alpha * (0.5 + S.energy) * (1 + beat * 0.5)).toFixed(3);
            const aTop = (R.alpha * 0.3 * (0.5 + S.energy)).toFixed(3);
            // real auroras burn brightest along the sharp lower border and
            // diffuse upward — skew the gradient to match
            grad.addColorStop(0, npv2PalA(pal, 0));
            grad.addColorStop(0.45, npv2PalA(pal, aTop));
            grad.addColorStop(0.82, npv2PalA(pal, aMid));
            grad.addColorStop(1, npv2PalA(pal, 0));
            ctx.fillStyle = grad;
            ctx.beginPath();
            const steps = 70;
            const pts = [];
            for (let i = 0; i <= steps; i++) {
                const x = (i / steps) * w;
                // The melody shapes the curtain: smoothed spectrum across the
                // sky, so a bright synth line visibly lifts the edge where it
                // sits in the mix and a bass drop lets it sink.
                const sv = npv2BinS(S, 'aurora' + ri, i, steps);
                const y = h * R.y + Math.sin(i * 0.11 + S.t * R.speed * 2 + ri * 2.1) * h * R.amp * (0.6 + S.energy * 0.8)
                    + Math.sin(i * 0.031 - S.t * R.speed) * h * R.amp * 0.5
                    - (sv - 0.30) * h * 0.20;
                pts.push([x, y]);
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            ctx.lineTo(w, h * (R.y + R.amp * 3));
            ctx.lineTo(0, h * (R.y + R.amp * 3));
            ctx.closePath(); ctx.fill();
            // aurora rays: thin strands rising off the curtain, each with its
            // own jitter, tilt and breathing — the detail that makes it
            // read as a real aurora instead of a picket fence
            ctx.save();
            ctx.globalCompositeOperation = 'lighter';
            const rays = 8;
            for (let k = 0; k < rays; k++) {
                const fx = (k + 0.5) / rays + st['rayJ' + ri + '_' + k] * 0.04;
                const py = pts[Math.min(steps, Math.max(0, Math.round(fx * steps)))][1];
                const breathe = 0.7 + 0.3 * Math.sin(S.t * 1.3 + k * 2.4 + ri * 1.7);
                // Rays grow where the spectrum is hot at their position.
                const rv = npv2BinS(S, 'aurora-ray' + ri, k, rays);
                const rayLen = h * (0.08 + R.amp * 2.4) * breathe * (0.75 + st['rayJ' + ri + '_' + k] * 0.5) * (0.55 + rv * 0.9);
                const tilt = (st['rayT' + ri + '_' + k] - 0.5) * rayLen * 0.35;
                const rg = ctx.createLinearGradient(0, py, 0, py - rayLen);
                rg.addColorStop(0, npv2PalA(pal, (R.alpha * 0.9 * breathe).toFixed(3)));
                rg.addColorStop(1, npv2PalA(pal, 0));
                ctx.fillStyle = rg;
                const rw = Math.max(2, w * 0.003 + R.amp * w * (0.008 + st['rayJ' + ri + '_' + k] * 0.014));
                const rx = fx * w;
                ctx.beginPath();
                ctx.moveTo(rx - rw / 2, py);
                ctx.lineTo(rx - rw / 4 + tilt, py - rayLen);
                ctx.lineTo(rx + rw / 4 + tilt, py - rayLen);
                ctx.lineTo(rx + rw / 2, py);
                ctx.closePath(); ctx.fill();
            }
            ctx.restore();
        });
        // horizon glow
        const hg = ctx.createLinearGradient(0, h * 0.75, 0, h);
        hg.addColorStop(0, npv2Css(S, 0));
        hg.addColorStop(1, npv2Css(S, 0.16 + S.energy * 0.1));
        ctx.fillStyle = hg;
        ctx.fillRect(0, h * 0.75, w, h * 0.25);
    },

    // --- Stardust: beat-reactive particle field --------------------------------
    stardust(ctx, w, h, S) {
        const q = npv2Q(S);
        const st = npv2ThemeState('stardust', () => {
            const ps = [];
            for (let i = 0; i < 300; i++) {
                const hero = Math.random() < 0.08;
                ps.push({
                    x: Math.random(), y: Math.random(),
                    vx: (Math.random() - 0.5) * 0.0006, vy: (Math.random() - 0.5) * 0.0006,
                    r: hero ? 2.2 + Math.random() * 1.6 : Math.random() * 1.8 + 0.4,
                    p: Math.random() * 6.28,
                    depth: 0.3 + Math.random() * 0.7,
                    tint: hero ? (Math.random() < 0.5 ? 1 : 2) : 0,
                });
            }
            return { ps, rings: [], comets: [], idleComet: 8 };
        });
        const beat = npv2BeatAmp(S);
        ctx.fillStyle = '#030309';
        ctx.fillRect(0, 0, w, h);
        // faint nebula wash so the field has depth, not void
        const wash = (fx, fy, fr, accent, al) => {
            const g = ctx.createRadialGradient(w * fx, h * fy, 0, w * fx, h * fy, Math.max(w, h) * fr);
            g.addColorStop(0, accent ? npv2Css2(S, al) : npv2Css(S, al));
            g.addColorStop(1, accent ? npv2Css2(S, 0) : npv2Css(S, 0));
            ctx.fillStyle = g;
            ctx.fillRect(0, 0, w, h);
        };
        wash(0.25 + 0.1 * Math.sin(S.t * 0.05), 0.35, 0.55, false, (0.10 + S.energy * 0.08).toFixed(3));
        wash(0.75 + 0.1 * Math.cos(S.t * 0.04), 0.65, 0.6, true, (0.08 + S.energy * 0.07).toFixed(3));
        // beat shockwave
        const f = npv2F(S);
        if (npv2Onset(S) && st.rings.length < 3) {
            st.rings.push({ r: 20, x: 0.3 + Math.random() * 0.4, y: 0.3 + Math.random() * 0.4 });
        }
        for (let i = st.rings.length - 1; i >= 0; i--) {
            const rg = st.rings[i];
            rg.r += 9 * q * f;
            const a = Math.max(0, 0.5 - rg.r / (Math.max(w, h) * 0.6));
            if (a <= 0) { st.rings.splice(i, 1); continue; }
            ctx.strokeStyle = npv2Css(S, a.toFixed(3));
            ctx.lineWidth = 1.5;
            ctx.beginPath(); ctx.arc(rg.x * w, rg.y * h, rg.r, 0, 6.2832); ctx.stroke();
        }
        for (const p of st.ps) {
            const sp = (1 + S.energy * 3 + beat * 5) * p.depth * f;
            p.x = (p.x + p.vx * sp + 1) % 1;
            p.y = (p.y + p.vy * sp + 1) % 1;
            const tw = 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(S.t * 2 * p.depth + p.p));
            const a = ((0.45 + S.energy * 0.55) * tw + beat * 0.35 * p.depth) * (0.4 + p.depth * 0.6);
            const rr = p.r * (1 + S.energy * 1.2) * p.depth;
            // palette tint for the hero stars — defensive: the test harness
            // builds frames without palettes
            const p1 = (S && S.pal) || { r: 120, g: 80, b: 200 };
            const p2 = (S && S.pal2) || { r: 120, g: 80, b: 200 };
            const col = p.tint === 0 ? '255,255,255'
                : p.tint === 1 ? (p1.r + ',' + p1.g + ',' + p1.b)
                : (p2.r + ',' + p2.g + ',' + p2.b);
            ctx.fillStyle = 'rgba(' + col + ',' + Math.min(1, a).toFixed(3) + ')';
            ctx.beginPath(); ctx.arc(p.x * w, p.y * h, rr, 0, 6.2832); ctx.fill();
            // the brightest few get a cross sparkle
            if (p.r > 1.9 && tw > 0.85) {
                ctx.strokeStyle = 'rgba(' + col + ',' + (a * 0.5).toFixed(3) + ')';
                ctx.lineWidth = 1;
                const L = rr * 4;
                ctx.beginPath();
                ctx.moveTo(p.x * w - L, p.y * h); ctx.lineTo(p.x * w + L, p.y * h);
                ctx.moveTo(p.x * w, p.y * h - L); ctx.lineTo(p.x * w, p.y * h + L);
                ctx.stroke();
            }
        }
        // comets: bright heads with fading trails, born on strong beats —
        // plus a slow ambient one so the trail system shows in quiet passages
        st.idleComet -= 0.016 * f;
        if (st.idleComet <= 0 && st.comets.length < 2) {
            const p = st.ps[Math.floor(Math.random() * st.ps.length)];
            const sp = 0.004 + Math.random() * 0.004;
            const ang = Math.random() * 6.2832;
            st.comets.push({ x: p.x, y: p.y, vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp, life: 1 });
            st.idleComet = 9 + Math.random() * 6;
        }
        if (npv2Onset(S) && st.comets.length < 4 && Math.random() < 0.6) {
            const p = st.ps[Math.floor(Math.random() * st.ps.length)];
            const sp = 0.006 + Math.random() * 0.008;
            const ang = Math.random() * 6.2832;
            st.comets.push({ x: p.x, y: p.y, vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp, life: 1 });
        }
        for (let i = st.comets.length - 1; i >= 0; i--) {
            const c = st.comets[i];
            c.x = (c.x + c.vx * f + 1) % 1;
            c.y = (c.y + c.vy * f + 1) % 1;
            c.life -= 0.014 * f;
            if (c.life <= 0) { st.comets.splice(i, 1); continue; }
            for (let k = 0; k < 7; k++) {
                const tx = (((c.x - c.vx * k * 2) % 1) + 1) % 1;
                const ty = (((c.y - c.vy * k * 2) % 1) + 1) % 1;
                const a = c.life * 0.8 * Math.pow(1 - k / 7, 1.8);
                ctx.fillStyle = k === 0
                    ? 'rgba(255,255,255,' + a.toFixed(3) + ')'
                    : npv2Css(S, a.toFixed(3));
                ctx.beginPath();
                ctx.arc(tx * w, ty * h, (k === 0 ? 2.4 : 1.8) * (1 - k / 8), 0, 6.2832);
                ctx.fill();
            }
        }
        // palette-tinted motes drifting against the flow
        for (let i = 0; i < 26; i++) {
            const p = st.ps[(i * 7) % st.ps.length];
            ctx.fillStyle = (i % 3 === 0 ? npv2Css2(S, 0.5) : npv2Css(S, 0.5));
            ctx.beginPath(); ctx.arc(p.x * w, p.y * h, p.r * 2.1, 0, 6.2832); ctx.fill();
        }
    },

    // --- Vinyl: spinning wax ----------------------------------------------------
    vinyl(ctx, w, h, S) {
        const cx = w / 2, cy = h / 2 + Math.sin(S.t * 0.8) * 3;
        const R = Math.min(w, h) * 0.34;
        const st = npv2ThemeState('vinyl', () => ({ rot: 0 }));
        const beat = npv2BeatAmp(S);
        if (!S.idle) st.rot += (0.008 + S.energy * 0.014) * npv2F(S);
        else st.rot += 0.002 * npv2F(S);
        // drop shadow
        ctx.fillStyle = 'rgba(0,0,0,0.5)';
        ctx.beginPath(); ctx.ellipse(cx, cy + R * 1.02, R * 1.02, R * 0.12, 0, 0, 6.2832); ctx.fill();
        // record body
        const body = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R);
        body.addColorStop(0, '#0a0a0a');
        body.addColorStop(0.85, '#101010');
        body.addColorStop(1, '#1e1e1e');
        ctx.fillStyle = body;
        ctx.beginPath(); ctx.arc(cx, cy, R, 0, 6.2832); ctx.fill();
        // edge highlight
        ctx.strokeStyle = 'rgba(255,255,255,0.14)';
        ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(cx, cy, R - 1, 0, 6.2832); ctx.stroke();
        // grooves
        ctx.save();
        ctx.strokeStyle = 'rgba(255,255,255,0.055)';
        ctx.lineWidth = 1;
        for (let r = R * 0.36; r < R * 0.96; r += 5) {
            ctx.beginPath(); ctx.arc(cx, cy, r, 0, 6.2832); ctx.stroke();
        }
        // energy highlight arc sweeping the grooves — kept whisper-quiet so
        // it reads as light on wax, not a scratch
        const hl = st.rot % 6.2832;
        ctx.strokeStyle = npv2Css(S, (0.10 + S.energy * 0.18).toFixed(3));
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(cx, cy, R * 0.66, hl, hl + 1.1); ctx.stroke();
        // light sweep that rides the energy (rotates around the record center)
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(st.rot);
        const sweep = ctx.createLinearGradient(-R, -R, R, R);
        sweep.addColorStop(0.42, 'rgba(255,255,255,0)');
        sweep.addColorStop(0.5, 'rgba(255,255,255,' + (0.05 + S.energy * 0.10 + beat * 0.06).toFixed(3) + ')');
        sweep.addColorStop(0.58, 'rgba(255,255,255,0)');
        ctx.fillStyle = sweep;
        ctx.beginPath(); ctx.arc(0, 0, R, 0, 6.2832); ctx.fill();
        ctx.restore();
        ctx.restore();
        // dead-wax: the smooth ring between label and grooves
        ctx.strokeStyle = 'rgba(255,255,255,0.08)';
        ctx.lineWidth = Math.max(2, R * 0.02);
        ctx.beginPath(); ctx.arc(cx, cy, R * 0.345, 0, 6.2832); ctx.stroke();
        // label: the actual album cover, spinning with the record. falls back
        // to a palette disc + the title's initial while the art loads
        const LR = R * 0.32;
        const art = S.art;
        if (art && typeof ctx.drawImage === 'function') {
            ctx.save();
            ctx.translate(cx, cy);
            ctx.rotate(st.rot);
            ctx.beginPath(); ctx.arc(0, 0, LR, 0, 6.2832); ctx.clip();
            ctx.drawImage(art, -LR, -LR, LR * 2, LR * 2);
            // pressed-paper sheen so it sits ON the wax, not over it
            const sheen = ctx.createRadialGradient(-LR * 0.3, -LR * 0.35, 0, 0, 0, LR);
            sheen.addColorStop(0, 'rgba(255,255,255,0.10)');
            sheen.addColorStop(1, 'rgba(0,0,0,0.28)');
            ctx.fillStyle = sheen;
            ctx.fillRect(-LR, -LR, LR * 2, LR * 2);
            ctx.restore();
        } else {
            ctx.fillStyle = npv2Css(S, 0.92);
            ctx.beginPath(); ctx.arc(cx, cy, LR, 0, 6.2832); ctx.fill();
            ctx.fillStyle = 'rgba(0,0,0,0.78)';
            ctx.font = '700 ' + Math.round(R * 0.22) + 'px system-ui, sans-serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            const initial = (S.title || '♪').trim().charAt(0).toUpperCase() || '♪';
            ctx.fillText(initial, cx, cy + 1);
        }
        ctx.strokeStyle = 'rgba(0,0,0,0.35)';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(cx, cy, LR, 0, 6.2832); ctx.stroke();
        // spindle
        ctx.fillStyle = '#d8d8d8';
        ctx.beginPath(); ctx.arc(cx, cy, R * 0.035, 0, 6.2832); ctx.fill();
        // tonearm: pivots and tracks inward as the record plays
        const ax = cx + R * 1.18, ay = cy - R * 1.02;
        const trackK = 0.42 - (0.5 + 0.5 * Math.sin(S.t * 0.045)) * 0.16 - S.energy * 0.02;
        const tipX = cx + R * trackK, tipY = cy - R * 0.30 * (0.9 + trackK * 0.25);
        ctx.strokeStyle = 'rgba(0,0,0,0.4)';
        ctx.lineWidth = Math.max(4, R * 0.045);
        ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(ax + 3, ay + 4);
        ctx.lineTo(tipX + 3, tipY + 4); ctx.stroke();
        ctx.strokeStyle = 'rgba(220,220,220,0.8)';
        ctx.lineWidth = Math.max(3, R * 0.03);
        ctx.beginPath(); ctx.moveTo(ax, ay);
        ctx.lineTo(tipX, tipY); ctx.stroke();
        ctx.fillStyle = 'rgba(220,220,220,0.9)';
        ctx.beginPath(); ctx.arc(ax, ay, R * 0.07, 0, 6.2832); ctx.fill();
        ctx.fillStyle = 'rgba(120,120,120,0.9)';
        ctx.beginPath(); ctx.arc(ax, ay, R * 0.028, 0, 6.2832); ctx.fill();
        // floor reflection: squashed palette sheen under the wax
        const refl = ctx.createLinearGradient(0, cy + R, 0, cy + R * 1.7);
        refl.addColorStop(0, npv2Css(S, 0.16 + S.energy * 0.10));
        refl.addColorStop(1, npv2Css(S, 0));
        ctx.fillStyle = refl;
        ctx.beginPath();
        ctx.ellipse(cx, cy + R * 1.28, R * 0.95, R * 0.22, 0, 0, 6.2832);
        ctx.fill();
    },

    // --- Tunnel: spectrum wormhole ----------------------------------------------
    tunnel(ctx, w, h, S) {
        const cx = w / 2, cy = h / 2;
        const st = npv2ThemeState('tunnel', () => ({ z: 0, streaks: [] }));
        const beat = npv2BeatAmp(S);
        const f = npv2F(S);
        st.z = (st.z + (0.012 + S.energy * 0.03 + beat * 0.05) * f) % 1;
        // translucent: the trail feedback (NPV2_TRAILS) streaks the rings outward
        ctx.fillStyle = 'rgba(2,2,8,0.62)';
        ctx.fillRect(0, 0, w, h);
        // core glow the rings fly out of
        const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.min(w, h) * 0.16);
        core.addColorStop(0, 'rgba(255,255,255,' + (0.25 + beat * 0.5).toFixed(3) + ')');
        core.addColorStop(0.5, npv2Css(S, 0.35 + beat * 0.3));
        core.addColorStop(1, npv2Css(S, 0));
        ctx.fillStyle = core;
        ctx.beginPath(); ctx.arc(cx, cy, Math.min(w, h) * 0.16, 0, 6.2832); ctx.fill();
        const RINGS = 22, SEGS = 42;
        for (let r = 0; r < RINGS; r++) {
            const z = ((r / RINGS) + st.z) % 1;         // 0 = far, 1 = near
            const rad = Math.pow(z, 2.2) * Math.min(w, h) * 0.52 + 8;
            const alpha = Math.pow(z, 1.6) * 0.85;
            for (let sgi = 0; sgi < SEGS; sgi++) {
                const v = npv2BinS(S, 'tunnel', sgi, SEGS);
                const a0 = (sgi / SEGS) * 6.2832 + S.t * 0.05 * z;
                const a1 = ((sgi + 0.72) / SEGS) * 6.2832 + S.t * 0.05 * z;
                const rr = rad * (0.92 + v * 0.35);
                const accent = (r + sgi) % 7 === 0;
                ctx.strokeStyle = accent
                    ? npv2Css2(S, (alpha * (0.25 + v * 0.75)).toFixed(3))
                    : npv2Css(S, (alpha * (0.25 + v * 0.75)).toFixed(3));
                ctx.lineWidth = 1 + z * 3.2;
                ctx.beginPath();
                ctx.arc(cx, cy, rr, a0, a1);
                ctx.stroke();
            }
        }
        // speed streaks: bright radial lines rushing toward the viewer
        let born = npv2Spawn(0.06 + S.energy * 0.10, S) + (npv2Onset(S) ? 6 : 0);
        while (born-- > 0 && st.streaks.length < 26) {
            st.streaks.push({ a: Math.random() * 6.2832, z: 0.02, sp: 0.05 + Math.random() * 0.06 });
        }
        ctx.lineCap = 'round';
        for (let i = st.streaks.length - 1; i >= 0; i--) {
            const sk = st.streaks[i];
            sk.z += sk.sp * (0.5 + S.energy) * f;
            if (sk.z >= 1) { st.streaks.splice(i, 1); continue; }
            const r0 = Math.pow(sk.z, 2.2) * Math.min(w, h) * 0.52 + 8;
            const r1 = Math.pow(Math.min(1, sk.z + 0.09), 2.2) * Math.min(w, h) * 0.52 + 8;
            const a = Math.pow(sk.z, 1.6);
            ctx.strokeStyle = npv2Css(S, (a * 0.7).toFixed(3));
            ctx.lineWidth = 1 + sk.z * 3;
            ctx.beginPath();
            ctx.moveTo(cx + Math.cos(sk.a) * r0, cy + Math.sin(sk.a) * r0);
            ctx.lineTo(cx + Math.cos(sk.a) * r1, cy + Math.sin(sk.a) * r1);
            ctx.stroke();
        }
    },

    // --- Off ----------------------------------------------------------------------
    none() { /* the CSS ambient glow carries the vibe */ },

    // --- WMP Battery: sparks rise, beat kicks burst them -------------------------
    battery(ctx, w, h, S) {
        const q = npv2Q(S);
        const st = npv2ThemeState('battery', () => ({ parts: [], arcs: [], seed: Math.random() * 100, idleArc: 7 }));
        const beat = npv2BeatAmp(S);
        const cx = w / 2, horizon = h * 0.78;
        // full clear: no ghost bleed from the previous theme
        ctx.fillStyle = '#04060e';
        ctx.fillRect(0, 0, w, h);
        // faint depth haze above the horizon
        const haze = ctx.createLinearGradient(0, horizon - h * 0.25, 0, horizon);
        haze.addColorStop(0, npv2Css(S, 0));
        haze.addColorStop(1, npv2Css(S, (0.20 + S.energy * 0.18).toFixed(3)));
        ctx.fillStyle = haze;
        ctx.fillRect(0, horizon - h * 0.25, w, h * 0.25);
        // smoke wisps: large soft blobs rising slowly, barely there — depth
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        for (let wi = 0; wi < 3; wi++) {
            const wsd = st.seed + wi * 47.3;
            const wx = w * (0.3 + 0.4 * (0.5 + 0.5 * Math.sin(S.t * 0.07 + wsd)));
            const wy = horizon - h * (0.08 + 0.05 * Math.sin(S.t * 0.11 + wsd * 2));
            const wr = w * (0.10 + 0.03 * Math.sin(S.t * 0.09 + wsd * 3));
            const wg = ctx.createRadialGradient(wx, wy, 0, wx, wy, wr);
            wg.addColorStop(0, npv2Css(S, (0.05 + S.energy * 0.03).toFixed(3)));
            wg.addColorStop(1, npv2Css(S, 0));
            ctx.fillStyle = wg;
            ctx.beginPath(); ctx.arc(wx, wy, wr, 0, 6.2832); ctx.fill();
        }
        ctx.restore();
        // rising sparks, two depth layers
        const f = npv2F(S);
        const spawn = npv2Spawn((4 + Math.floor(S.energy * 10)) * q, S) + (npv2Onset(S) ? Math.round(160 * q) : 0);
        for (let i = 0; i < spawn; i++) {
            if (st.parts.length > Math.round(460 * q)) break;
            const far = Math.random() < 0.45;
            const big = Math.random() < 0.06;
            st.parts.push({
                x: cx + (Math.random() - 0.5) * w * 0.62,
                y: horizon + Math.random() * 6,
                vy: -(h * (0.22 + Math.random() * 0.5)) * (0.35 + S.energy),
                vx: (Math.random() - 0.5) * w * 0.05,
                life: 1,
                decay: 0.005 + Math.random() * 0.011,
                sz: far ? 0.7 + Math.random() * 1.1 : (big ? 3.2 : 1) + Math.random() * 2.4,
                far: far,
                ph: Math.random() * 6.28,
            });
        }
        for (let i = st.parts.length - 1; i >= 0; i--) {
            const p = st.parts[i];
            p.x += (p.vx + Math.sin(S.t * 2.6 + p.ph + p.y * 0.008) * 0.7) * f;
            p.y += p.vy * 0.016 * f;
            p.life -= p.decay * (1 + S.energy * 2) * f;
            if (p.life <= 0 || p.y < -12) { st.parts.splice(i, 1); continue; }
            const a = Math.min(1, p.life * 1.5) * (p.far ? 0.45 : 1);
            // ember ramp: white-hot core -> accent -> palette as it cools
            ctx.fillStyle = p.life > 0.66
                ? 'rgba(255,255,255,' + (a * 0.95).toFixed(3) + ')'
                : p.life > 0.33
                    ? npv2Css2(S, (a * 0.9).toFixed(3))
                    : npv2Css(S, (a * 0.9).toFixed(3));
            const r = p.sz * (0.5 + p.life * 0.9);
            ctx.beginPath();
            ctx.arc(p.x, p.y, r, 0, 6.2832);
            ctx.fill();
            // motion trail on near sparks: streaks read as rising, dots read as noise
            if (!p.far && p.life > 0.25) {
                ctx.strokeStyle = ctx.fillStyle;
                ctx.globalAlpha = 0.35 * Math.min(1, p.life);
                ctx.lineWidth = Math.max(1, r * 0.6);
                ctx.beginPath();
                ctx.moveTo(p.x, p.y + r * 0.5);
                ctx.lineTo(p.x - p.vx * 1.5, p.y - p.vy * 0.055);
                ctx.stroke();
                ctx.globalAlpha = 1;
            }
            // hot core dot on near sparks
            if (!p.far && p.life > 0.5 && r > 1.6) {
                ctx.fillStyle = 'rgba(255,255,255,' + (a * 0.8).toFixed(3) + ')';
                ctx.beginPath();
                ctx.arc(p.x, p.y, r * 0.35, 0, 6.2832);
                ctx.fill();
            }
        }
        // electric arcs: jagged discharge rising from the horizon — on hard
        // beats, plus a slow ambient one so the sky isn't empty in quiet parts
        st.idleArc -= 0.016 * f;
        if (st.idleArc <= 0 && st.arcs.length < 2) {
            st.idleArc = 8 + Math.random() * 6;
            st.arcs.push({ pts: null, life: 1, ambient: true });
        }
        if (npv2Onset(S) && st.arcs.length < 4) {
            st.arcs.push({ pts: null, life: 1, ambient: false });
        }
        for (let i = st.arcs.length - 1; i >= 0; i--) {
            const arc = st.arcs[i];
            if (!arc.pts) {
                const segs = 7 + Math.floor(Math.random() * 5);
                const pts = [{ x: cx + (Math.random() - 0.5) * w * 0.5, y: horizon }];
                for (let s2 = 1; s2 <= segs; s2++) {
                    const prev = pts[s2 - 1];
                    pts.push({
                        x: prev.x + (Math.random() - 0.5) * w * 0.035,
                        y: horizon - (h * (arc.ambient ? 0.22 : 0.34) * s2) / segs,
                    });
                }
                arc.pts = pts;
            }
        }
        ctx.lineWidth = Math.max(1, w * 0.0016);
        for (let i = st.arcs.length - 1; i >= 0; i--) {
            const arc = st.arcs[i];
            arc.life -= 0.09 * f;
            if (arc.life <= 0) { st.arcs.splice(i, 1); continue; }
            ctx.strokeStyle = 'rgba(255,255,255,' + (arc.life * 0.85).toFixed(3) + ')';
            ctx.beginPath();
            ctx.moveTo(arc.pts[0].x, arc.pts[0].y);
            for (let s2 = 1; s2 < arc.pts.length; s2++) ctx.lineTo(arc.pts[s2].x, arc.pts[s2].y);
            ctx.stroke();
            ctx.strokeStyle = npv2Css2(S, (arc.life * 0.5).toFixed(3));
            ctx.lineWidth = Math.max(2.5, w * 0.004);
            ctx.stroke();
            ctx.lineWidth = Math.max(1, w * 0.0016);
        }
        // horizon: crisp line + tight glow + reflection shimmer
        const flick = 0.35 + S.energy * 0.35 + Math.sin(S.t * 9 + st.seed) * 0.05 + beat * 0.3;
        const g = ctx.createLinearGradient(0, horizon - 26, 0, horizon + 40);
        g.addColorStop(0, npv2Css(S, 0));
        g.addColorStop(0.62, npv2Css(S, Math.max(0, flick * 0.5).toFixed(3)));
        g.addColorStop(0.72, 'rgba(255,255,255,' + Math.max(0, flick * 0.55).toFixed(3) + ')');
        g.addColorStop(0.82, npv2Css(S, Math.max(0, flick * 0.4).toFixed(3)));
        g.addColorStop(1, npv2Css(S, 0));
        ctx.fillStyle = g;
        ctx.fillRect(0, horizon - 26, w, 66);
    },

    // --- WMP Dot Plane: grid of dots breathing with the spectrum ------------------
    // Pseudo-3D: the grid recedes toward a horizon, dots swell and throw
    // light-pillars with the music — a field, not a spreadsheet.
    dotplane(ctx, w, h, S) {
        const cols = 28, rows = 16;
        const cw = w / cols;
        const beat = npv2BeatAmp(S);
        // a real 3D spectrogram: the near row is now, each row behind it is
        // a moment earlier, so the music scrolls away into the distance
        const hst = npv2ThemeState('dotplane-hist', () => ({ rows: [], acc: 0 }));
        const now = new Float32Array(cols);
        for (let gx = 0; gx < cols; gx++) now[gx] = npv2BinS(S, 'dotplane', gx, cols);
        hst.acc += (S && typeof S.dt === 'number' && S.dt > 0) ? S.dt : 0.016;
        if (!hst.rows.length || hst.acc >= 0.06) {
            hst.acc = 0;
            hst.rows.unshift(now);
            if (hst.rows.length > rows) hst.rows.length = rows;
        } else {
            hst.rows[0] = now;
        }
        ctx.fillStyle = '#040409';
        ctx.fillRect(0, 0, w, h);
        // floor glow under the horizon
        const horizon = h * 0.34;
        const fg = ctx.createLinearGradient(0, horizon, 0, h);
        fg.addColorStop(0, npv2Css(S, 0));
        fg.addColorStop(1, npv2Css(S, 0.12 + S.energy * 0.08));
        ctx.fillStyle = fg;
        ctx.fillRect(0, horizon, w, h - horizon);
        // crisp horizon line — where the far dots converge
        ctx.fillStyle = npv2Css(S, (0.25 + S.energy * 0.2).toFixed(3));
        ctx.fillRect(0, horizon - 0.75, w, 1.5);
        const bcx = w / 2;
        for (let gy = 0; gy < rows; gy++) {
            const p = gy / (rows - 1);                    // 0 = far, 1 = near
            const y = horizon + (h * 1.02 - horizon) * Math.pow(p, 1.9);
            const sc = 0.22 + 0.78 * Math.pow(p, 1.6);   // perspective scale
            const maxR = Math.min(cw, h / rows) * 0.42 * sc;
            for (let gx = 0; gx < cols; gx++) {
                const rowNow = hst.rows[Math.min(hst.rows.length - 1, rows - 1 - gy)];
                const v = rowNow ? rowNow[gx] : 0;
                // far columns converge toward center for the perspective read
                const x = bcx + (gx * cw + cw / 2 - bcx) * (0.45 + 0.55 * p);
                // beat wavefront radiating from the center, in depth space
                const d = Math.hypot((x - bcx) / w, (y - h / 2) / h);
                const wave = 0.5 + 0.5 * Math.sin(S.t * 1.7 + gx * 0.35 + gy * 0.5 - d * 6 + beat * 4);
                const r = Math.max(0.5, (v * 0.75 + wave * 0.25) * maxR * 1.15);
                const a = 0.22 + v * 0.7;
                const hot = v > 0.8;
                // light pillar rising off loud dots — the money detail
                if (v > 0.30) {
                    const ph = v * h * 0.16 * sc;
                    const pg = ctx.createLinearGradient(0, y, 0, y - ph);
                    pg.addColorStop(0, hot ? npv2Css2(S, (a * 0.5).toFixed(3)) : npv2Css(S, (a * 0.45).toFixed(3)));
                    pg.addColorStop(1, npv2Css(S, 0));
                    ctx.fillStyle = pg;
                    ctx.fillRect(x - r * 0.45, y - ph, r * 0.9, ph);
                }
                ctx.fillStyle = hot ? npv2Css2(S, Math.min(1, a + 0.2).toFixed(3)) : npv2Css(S, a.toFixed(3));
                ctx.beginPath();
                ctx.arc(x, y, Math.min(r, maxR * 1.15), 0, 6.2832);
                ctx.fill();
            }
        }
    },

    // --- WMP Alchemy: slow morphing metaball blobs --------------------------------
    alchemy(ctx, w, h, S) {
        const st = npv2ThemeState('alchemy', () => ({ seed: Math.random() * 100 }));
        const beat = npv2BeatAmp(S);
        const blobs = Math.max(4, Math.round(7 * npv2Q(S)));
        ctx.fillStyle = 'rgba(3,5,12,0.35)';
        ctx.fillRect(0, 0, w, h);
        const pts = [];
        npv2Soft(ctx, w, h, 0.34, 'alchemy', (c, sw) => {
        const k = sw / w;
        c.globalCompositeOperation = 'lighter';
        for (let i = 0; i < blobs; i++) {
            const accent = i % 3 === 2;
            const v = npv2BinS(S, 'alchemy', i, blobs);
            const px = st.seed + i * 13.7;
            const x = w * (0.5 + 0.38 * Math.sin(S.t * 0.21 + px) * Math.sin(S.t * 0.13 + px * 2));
            const y = h * (0.5 + 0.36 * Math.cos(S.t * 0.17 + px * 1.3));
            const r = Math.min(w, h) * (0.10 + v * 0.22 + S.energy * 0.06) * (1 + beat * 0.3);
            pts.push({ x, y, r, accent });
            const pal = accent ? npv2Pal2(S) : (S && S.pal);
            const g = c.createRadialGradient(x * k, y * k, 0, x * k, y * k, r * k);
            g.addColorStop(0, npv2PalA(pal, 0.5));
            g.addColorStop(0.6, npv2PalA(pal, 0.2));
            g.addColorStop(1, npv2PalA(pal, 0));
            c.fillStyle = g;
            c.beginPath();
            c.arc(x * k, y * k, r * k, 0, 6.2832);
            c.fill();
        }
        // bridges: soft glows where blobs near each other, so they read as
        // one merging fluid instead of separate circles
        for (let i = 0; i < pts.length; i++) {
            for (let j = i + 1; j < pts.length; j++) {
                const a = pts[i], b = pts[j];
                const d = Math.hypot(a.x - b.x, a.y - b.y);
                const touch = (a.r + b.r) * 1.25;
                if (d > touch || d < 1) continue;
                const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
                const mr = Math.min(a.r, b.r) * 0.7 * (1 - d / touch * 0.5);
                const pal = (a.accent || b.accent) ? npv2Pal2(S) : (S && S.pal);
                const al = 0.35 * (1 - d / touch);
                const bg = c.createRadialGradient(mx * k, my * k, 0, mx * k, my * k, mr * k);
                bg.addColorStop(0, npv2PalA(pal, al.toFixed(3)));
                bg.addColorStop(1, npv2PalA(pal, 0));
                c.fillStyle = bg;
                c.beginPath(); c.arc(mx * k, my * k, mr * k, 0, 6.2832); c.fill();
            }
        }
        }, 'lighter');
    },

    // --- Particle fountain: beat-fed spray from the bottom ------------------------
    fountain(ctx, w, h, S) {
        const q = npv2Q(S);
        const st = npv2ThemeState('fountain', () => ({ parts: [], sparks: [] }));
        const beat = npv2BeatAmp(S);
        const bass = npv2Bin(S, 0, 8) * 0.6 + npv2Bin(S, 1, 8) * 0.4;
        const cx = w / 2;
        const f = npv2F(S);
        const spawn = npv2Spawn((2 + Math.floor(bass * 10)) * q, S) + (npv2Onset(S) ? Math.round(160 * q) : 0);
        for (let i = 0; i < spawn && st.parts.length < Math.round(700 * q); i++) {
            const ang = -Math.PI / 2 + (Math.random() - 0.5) * 1.15;
            // velocity tuned so the spray crests around mid-screen even in
            // quiet passages: apex = v^2/2g with g = 0.9h/s^2, so v ≈ 0.95h
            const sp = h * (0.70 + Math.random() * 0.80) * (0.70 + bass * 0.9) * (1 + beat * 0.5);
            st.parts.push({
                x: cx + (Math.random() - 0.5) * w * 0.04,
                y: h * 0.98,
                px: 0, py: 0,
                vx: Math.cos(ang) * sp,
                vy: Math.sin(ang) * sp,
                life: 1,
                decay: 0.008 + Math.random() * 0.012,
                sz: 1.5 + Math.random() * 2.5,
            });
            const p = st.parts[st.parts.length - 1];
            p.px = p.x; p.py = p.y;
        }
        ctx.fillStyle = 'rgba(3,5,12,0.30)';
        ctx.fillRect(0, 0, w, h);
        ctx.lineCap = 'round';
        for (let i = st.parts.length - 1; i >= 0; i--) {
            const p = st.parts[i];
            p.px = p.x; p.py = p.y;
            const dt = 0.016 * f;
            p.vy += h * 0.9 * dt; // gravity
            p.vx += Math.sin(S.t * 1.5 + p.y * 0.01) * 0.35 * f; // wind
            p.x += p.vx * dt;
            p.y += p.vy * dt;
            p.life -= p.decay * f;
            if (p.life <= 0 || p.y > h + 10) {
                // landing splash: two quick horizontal sparks
                if (p.y > h * 0.88 && st.sparks.length < 120) {
                    for (let k = 0; k < 2; k++) {
                        st.sparks.push({
                            x: p.x, y: h * 0.985,
                            vx: (Math.random() - 0.5) * w * 0.35,
                            life: 0.5 + Math.random() * 0.4,
                        });
                    }
                }
                st.parts.splice(i, 1); continue;
            }
            const a = Math.min(1, p.life) * 0.85;
            // white-hot head, palette tail
            ctx.strokeStyle = p.life > 0.7
                ? 'rgba(255,255,255,' + a.toFixed(3) + ')'
                : npv2Css(S, a.toFixed(3));
            ctx.lineWidth = p.sz;
            ctx.beginPath();
            ctx.moveTo(p.px, p.py);
            ctx.lineTo(p.x, p.y);
            ctx.stroke();
        }
        // splash sparks skitter along the floor
        for (let i = st.sparks.length - 1; i >= 0; i--) {
            const sp = st.sparks[i];
            sp.x += sp.vx * 0.016 * f;
            sp.vx *= Math.pow(0.96, f);
            sp.life -= 0.03 * f;
            if (sp.life <= 0) { st.sparks.splice(i, 1); continue; }
            ctx.fillStyle = npv2Css(S, (sp.life * 0.9).toFixed(3));
            ctx.beginPath(); ctx.arc(sp.x, sp.y, 1.5, 0, 6.2832); ctx.fill();
        }
        // splash glow where the spray lands
        const sg = ctx.createLinearGradient(0, h * 0.9, 0, h);
        sg.addColorStop(0, npv2Css(S, 0));
        sg.addColorStop(1, npv2Css(S, (0.25 + bass * 0.4 + beat * 0.2).toFixed(3)));
        ctx.fillStyle = sg;
        ctx.fillRect(0, h * 0.9, w, h * 0.1);
    },

    // --- Kaleidoscope: mirrored spectrum wedges ------------------------------------
    kaleido(ctx, w, h, S) {
        const cx = w / 2, cy = h / 2;
        const beat = npv2BeatAmp(S);
        const st = npv2ThemeState('kaleido', () => ({ dir: 1, cool: 0 }));
        // flip the spin on a beat, at most every 2s. it used to test
        // beat > 0.9, true for several frames per beat, so it flipped an even
        // number of times and mostly never visibly reversed
        st.cool -= (S && typeof S.dt === 'number' && S.dt > 0) ? S.dt : 0.016;
        if (npv2Onset(S) && st.cool <= 0) { st.dir *= -1; st.cool = 2; }
        const bass = npv2Bin(S, 0, 8);
        const R = Math.min(w, h) * 0.46 * (1 + bass * 0.06);
        const rot = S.t * 0.12 * st.dir;
        const pulse = 1 + beat * 0.10;
        // deep backdrop + soft glow bed so the jewels bloom on darkness
        ctx.fillStyle = 'rgba(2,3,9,0.55)';
        ctx.fillRect(0, 0, w, h);
        const kg = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.5);
        kg.addColorStop(0, npv2Css(S, (0.12 + bass * 0.10).toFixed(3)));
        kg.addColorStop(1, npv2Css(S, 0));
        ctx.fillStyle = kg;
        ctx.fillRect(0, 0, w, h);

        // Jeweled spiral mandala: each wedge holds a spiral arm of glowing
        // dots — radius = frequency (bass near the core, treble near the
        // rim), size/brightness = the smoothed band value — mirrored around
        // the circle for true kaleidoscope symmetry. A second, dimmer
        // counter-rotating layer interleaves for depth.
        const p1 = S.pal, p2 = npv2Pal2(S);
        const segs = 12, bands = 30;
        const wedge = (Math.PI * 2) / segs;
        // color ramp palette -> accent across the spectrum, premixed once
        const ramp = [];
        for (let i = 0; i < bands; i++) {
            const t = i / (bands - 1);
            ramp.push('rgba(' +
                Math.round(p1.r + (p2.r - p1.r) * t) + ',' +
                Math.round(p1.g + (p2.g - p1.g) * t) + ',' +
                Math.round(p1.b + (p2.b - p1.b) * t) + ',');
        }
        ctx.save();
        ctx.translate(cx, cy);
        ctx.globalCompositeOperation = 'lighter';
        for (let layer = 0; layer < 2; layer++) {
            const lrot = layer === 0 ? rot : -rot * 1.6;
            const lr = layer === 0 ? 1 : 0.82; // inner layer sits slightly in
            const ldim = layer === 0 ? 1 : 0.45;
            for (let sgm = 0; sgm < segs; sgm++) {
                ctx.save();
                ctx.rotate(lrot + (sgm / segs) * Math.PI * 2);
                if (sgm % 2 === 1) ctx.scale(1, -1); // mirror
                let px = 0, py = 0, pv = 0;
                for (let i = 0; i < bands; i++) {
                    const v = npv2BinS(S, 'kaleido' + layer, i, bands);
                    // the music bends the spiral: hot bands swing outward
                    const ang = (i / bands) * wedge * 0.92 +
                        Math.sin(S.t * 0.9 + i * 0.55 + layer * 2.1) * 0.05 * v;
                    const rr = (0.10 + 0.84 * (i / bands)) * R * pulse * lr;
                    const x = Math.cos(ang) * rr, y = Math.sin(ang) * rr;
                    // filament connecting the jewels along the arm
                    if (i > 0 && (pv > 0.03 || v > 0.03)) {
                        const fa = Math.min(pv, v) * 0.5 * ldim * (1 + beat * 0.4);
                        if (fa > 0.02) {
                            ctx.strokeStyle = ramp[i] + fa.toFixed(3) + ')';
                            ctx.lineWidth = 1 + Math.min(pv, v) * 3;
                            ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(x, y); ctx.stroke();
                        }
                    }
                    if (v > 0.03) {
                        let a = (0.15 + v * 0.85) * ldim * (1 + beat * 0.5);
                        if (a > 1) a = 1;
                        const jr = (0.8 + v * 3.4) * (layer === 0 ? 1 : 0.7);
                        // halo + jewel core; hottest go white-hot
                        ctx.fillStyle = (v > 0.72 ? 'rgba(255,255,255,' : ramp[i]) + (a * 0.20).toFixed(3) + ')';
                        ctx.beginPath(); ctx.arc(x, y, jr * 2.4, 0, 6.2832); ctx.fill();
                        ctx.fillStyle = (v > 0.72 ? 'rgba(255,255,255,' : ramp[i]) + a.toFixed(3) + ')';
                        ctx.beginPath(); ctx.arc(x, y, jr, 0, 6.2832); ctx.fill();
                        // pinpoint sparkle on the brightest jewels
                        if (v > 0.6 && layer === 0) {
                            const tw = 0.5 + 0.5 * Math.sin(S.t * 4 + i * 1.7 + sgm);
                            ctx.fillStyle = 'rgba(255,255,255,' + (tw * v * 0.85).toFixed(3) + ')';
                            ctx.beginPath(); ctx.arc(x, y, jr * 0.45, 0, 6.2832); ctx.fill();
                        }
                    }
                    px = x; py = y; pv = v;
                }
                ctx.restore();
            }
        }
        ctx.restore();
        // rim of beat-pulsing accent jewels framing the mandala
        ctx.save();
        ctx.translate(cx, cy);
        ctx.globalCompositeOperation = 'lighter';
        ctx.rotate(-rot * 0.6);
        const jr2 = 2 + beat * 5;
        for (let sgm = 0; sgm < segs; sgm++) {
            const a = (sgm / segs) * Math.PI * 2;
            const x = Math.cos(a) * R * 1.02 * pulse, y = Math.sin(a) * R * 1.02 * pulse;
            ctx.fillStyle = npv2Css2(S, (0.25 + beat * 0.65).toFixed(3));
            ctx.beginPath(); ctx.arc(x, y, jr2 * 2.2, 0, 6.2832); ctx.fill();
            ctx.fillStyle = 'rgba(255,255,255,' + (0.35 + beat * 0.5).toFixed(3) + ')';
            ctx.beginPath(); ctx.arc(x, y, jr2 * 0.8, 0, 6.2832); ctx.fill();
        }
        ctx.restore();
        // breathing luminous core
        const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.20 * pulse);
        core.addColorStop(0, 'rgba(255,255,255,' + (0.55 + bass * 0.35).toFixed(3) + ')');
        core.addColorStop(0.4, npv2Css(S, (0.45 + bass * 0.30).toFixed(3)));
        core.addColorStop(1, npv2Css(S, 0));
        ctx.fillStyle = core;
        ctx.beginPath();
        ctx.arc(cx, cy, R * 0.20 * pulse, 0, 6.2832);
        ctx.fill();
    },

    // --- Warp: starfield rushing past, speed tied to energy -------------------------
    warp(ctx, w, h, S) {
        const st = npv2ThemeState('warp', () => ({
            stars: Array.from({ length: 320 }, () => ({
                x: Math.random() * 2 - 1, y: Math.random() * 2 - 1, z: Math.random(),
                tw: Math.random() * 6.28,
            })),
            jumps: [],
        }));
        const cx = w / 2, cy = h / 2;
        const beat = npv2BeatAmp(S);
        const speed = 0.008 + S.energy * 0.05 + beat * 0.03 + (S.idle ? 0.004 : 0);
        ctx.fillStyle = 'rgba(2,3,9,0.42)';
        ctx.fillRect(0, 0, w, h);
        // palette nebula wash behind the streaks, with a hot core at the
        // vanishing point so the flight direction reads
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.min(w, h) * 0.5);
        g.addColorStop(0, npv2Css(S, (0.34 + beat * 0.25).toFixed(3)));
        g.addColorStop(0.25, npv2Css(S, (0.16 + beat * 0.10).toFixed(3)));
        g.addColorStop(1, npv2Css(S, 0));
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, w, h);
        // perspective: k keeps stars on-screen for most of their flight —
        // they only leave the frame as they whip past the camera
        const proj = (zx, zy, z) => {
            const k = 0.75 / (z * 1.6 + 0.35);
            return [cx + zx * k * cx, cy + zy * k * cy];
        };
        for (const s of st.stars) {
            s.z -= speed * (0.4 + s.z) * npv2F(S);
            if (s.z <= 0.02) { s.x = Math.random() * 2 - 1; s.y = Math.random() * 2 - 1; s.z = 1; }
            const [sx, sy] = proj(s.x, s.y, s.z);
            const [px, py] = proj(s.x, s.y, s.z + speed * 3);
            const bright = 1 - s.z;
            const near = bright > 0.75;
            // far stars twinkle instead of just streaking
            const twk = near ? 1 : 0.6 + 0.4 * Math.sin(S.t * 3 + s.tw);
            ctx.strokeStyle = near
                ? npv2Css2(S, (bright * 0.9).toFixed(3))
                : 'rgba(255,255,255,' + (bright * 0.95 * twk).toFixed(3) + ')';
            ctx.lineWidth = Math.max(1, bright * 2.4);
            ctx.beginPath();
            ctx.moveTo(px, py);
            ctx.lineTo(sx, sy);
            ctx.stroke();
        }
        // jump streaks: on hard beats a few stars punch through extra long
        if (npv2Onset(S) && st.jumps.length < 7) {
            for (let k = 0; k < 3; k++) {
                st.jumps.push({ a: Math.random() * 6.2832, z: 0.25 + Math.random() * 0.3, life: 1 });
            }
        }
        for (let i = st.jumps.length - 1; i >= 0; i--) {
            const j = st.jumps[i];
            j.z -= 0.05 * npv2F(S);
            j.life -= 0.05 * npv2F(S);
            if (j.z <= 0.03 || j.life <= 0) { st.jumps.splice(i, 1); continue; }
            const r0 = (1 / j.z) * Math.min(w, h) * 0.5;
            const r1 = (1 / Math.min(1, j.z + 0.25)) * Math.min(w, h) * 0.5;
            const gg = ctx.createLinearGradient(
                cx + Math.cos(j.a) * r1, cy + Math.sin(j.a) * r1,
                cx + Math.cos(j.a) * r0, cy + Math.sin(j.a) * r0);
            gg.addColorStop(0, npv2Css2(S, (j.life * 0.85).toFixed(3)));
            gg.addColorStop(1, npv2Css2(S, 0));
            ctx.strokeStyle = gg;
            ctx.lineWidth = 2.5;
            ctx.beginPath();
            ctx.moveTo(cx + Math.cos(j.a) * r1, cy + Math.sin(j.a) * r1);
            ctx.lineTo(cx + Math.cos(j.a) * r0, cy + Math.sin(j.a) * r0);
            ctx.stroke();
        }
        // jump flash on the beat
        if (beat > 0.15) {
            const fg = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.min(w, h) * 0.3);
            fg.addColorStop(0, 'rgba(255,255,255,' + (beat * 0.35).toFixed(3) + ')');
            fg.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.fillStyle = fg;
            ctx.fillRect(0, 0, w, h);
        }
    },

    // --- Nebula: slow deep-space clouds ----------------------------------------------
    nebula(ctx, w, h, S) {
        const st = npv2ThemeState('nebula', () => ({ seed: Math.random() * 40, spark: 0, sx: 0, sy: 0 }));
        const beat = npv2BeatAmp(S);
        ctx.fillStyle = '#020309';
        ctx.fillRect(0, 0, w, h);
        const layers = [
            { n: 5, sp: 0.05, al: 0.44, sz: 0.42, accent: false },
            { n: 4, sp: 0.07, al: 0.34, sz: 0.34, accent: true },
            { n: 7, sp: 0.09, al: 0.30, sz: 0.30, accent: false },
            { n: 9, sp: 0.14, al: 0.24, sz: 0.20, accent: true },
        ];
        // ~25 screen-sized glows: pure softness, so a third of the size
        npv2Soft(ctx, w, h, 0.3, 'nebula', (c, sw, sh) => {
            for (const L of layers) {
                for (let i = 0; i < L.n; i++) {
                    const px = st.seed + i * 31.7 + L.sp * 57 + (L.accent ? 91 : 0);
                    const x = sw * (0.5 + 0.45 * Math.sin(S.t * L.sp + px));
                    const y = sh * (0.5 + 0.45 * Math.cos(S.t * L.sp * 0.8 + px * 1.7));
                    const r = Math.min(sw, sh) * L.sz * (0.8 + 0.4 * Math.sin(S.t * 0.3 + px)) * (1 + beat * 0.2);
                    const v = npv2BinS(S, 'nebula', i, L.n);
                    const pal = L.accent ? npv2Pal2(S) : (S && S.pal);
                    const aMid = (L.al * (0.6 + v) * (1 + beat * 0.8)).toFixed(3);
                    const g = c.createRadialGradient(x, y, 0, x, y, r);
                    g.addColorStop(0, npv2PalA(pal, aMid));
                    g.addColorStop(1, npv2PalA(pal, 0));
                    c.fillStyle = g;
                    c.beginPath();
                    c.arc(x, y, r, 0, 6.2832);
                    c.fill();
                }
            }
        });
        // filament wisps: short curved streams inside the clouds, drifting
        // slowly. Real nebulas are all filaments — this is the texture the
        // eye expects; kept short and bowed so they never read as scratches.
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        for (let fi = 0; fi < 5; fi++) {
            const wsd = st.seed + fi * 31.7;
            const wob = Math.sin(S.t * 0.10 + wsd) * 0.15;
            const x0 = w * (0.2 + 0.6 * (0.5 + 0.5 * Math.sin(wsd * 1.7)));
            const y0 = h * (0.25 + 0.5 * (0.5 + 0.5 * Math.cos(wsd * 2.3)));
            const len = Math.max(w, h) * (0.35 + 0.15 * Math.sin(wsd));
            const ang = wob + wsd;
            const dx = Math.cos(ang), dy = Math.sin(ang);
            const pal = fi % 2 ? npv2Pal2(S) : (S && S.pal);
            const wg = ctx.createLinearGradient(x0, y0, x0 + dx * len, y0 + dy * len);
            const wa = (0.04 + npv2BinS(S, 'nebula-w', fi, 5) * 0.08 + beat * 0.04).toFixed(3);
            wg.addColorStop(0, npv2PalA(pal, 0));
            wg.addColorStop(0.5, npv2PalA(pal, wa));
            wg.addColorStop(1, npv2PalA(pal, 0));
            ctx.strokeStyle = wg;
            ctx.lineWidth = 1.5 + npv2BinS(S, 'nebula-w', fi, 5) * 4;
            ctx.lineCap = 'round';
            const bow = 90 * Math.sin(S.t * 0.2 + wsd);
            ctx.beginPath();
            ctx.moveTo(x0, y0);
            ctx.quadraticCurveTo(
                x0 + dx * len * 0.5 - dy * bow,
                y0 + dy * len * 0.5 + dx * bow,
                x0 + dx * len, y0 + dy * len);
            ctx.stroke();
        }
        ctx.restore();
        // two depths of stars
        const sst = npv2ThemeState('nebula-stars', () => ({
            pts: Array.from({ length: 150 }, () => ({ x: Math.random(), y: Math.random(), p: Math.random() * 6.28, far: Math.random() < 0.5 })),
        }));
        for (const p of sst.pts) {
            const tw = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(S.t * (p.far ? 0.8 : 1.4) + p.p));
            ctx.fillStyle = 'rgba(255,255,255,' + (tw * (p.far ? 0.4 : 0.8)).toFixed(3) + ')';
            const sz = p.far ? 1.2 : 1.8;
            ctx.fillRect(p.x * w, p.y * h, sz, sz);
        }
        // occasional bright star with a cross sparkle
        st.spark -= 0.016 * npv2F(S);
        if (st.spark <= 0) {
            st.spark = 4 + Math.random() * 7;
            st.sx = Math.random(); st.sy = Math.random() * 0.8;
        }
        if (st.spark > 0 && st.spark < 1.2) {
            const a = Math.min(1, st.spark);
            const x = st.sx * w, y = st.sy * h;
            ctx.strokeStyle = 'rgba(255,255,255,' + (a * 0.8).toFixed(3) + ')';
            ctx.lineWidth = 1.5;
            const L = 9 * a + 2;
            ctx.beginPath();
            ctx.moveTo(x - L, y); ctx.lineTo(x + L, y);
            ctx.moveTo(x, y - L); ctx.lineTo(x, y + L);
            ctx.stroke();
            ctx.fillStyle = 'rgba(255,255,255,' + (a * 0.9).toFixed(3) + ')';
            ctx.beginPath(); ctx.arc(x, y, 2, 0, 6.2832); ctx.fill();
        }
    },

    // --- Bloom: beat rings expanding from center --------------------------------------
    bloom(ctx, w, h, S) {
        const q = npv2Q(S);
        const st = npv2ThemeState('bloom', () => ({ rings: [], idleT: 0, sparks: [] }));
        const cx = w / 2, cy = h / 2;
        const beat = npv2BeatAmp(S);
        const bass = npv2Bin(S, 0, 10) * 0.7 + npv2Bin(S, 1, 10) * 0.3;
        const f = npv2F(S);
        if (npv2Onset(S) && st.rings.length < Math.round(26 * q)) {
            // staggered echo: three rings per beat, petals offset
            for (let k = 0; k < 3; k++) {
                st.rings.push({ r: 8 - k * 7, w: 3 + bass * 5, rot: Math.random() * 6.28, petals: 10 + k * 4, accent: k === 1 });
            }
        }
        // idle breathing: a soft ring every few seconds even with no beats
        st.idleT += 0.016 * f;
        if (st.idleT > 2.5) {
            st.idleT = 0;
            st.rings.push({ r: 8, w: 2, rot: 0, petals: 12, accent: false, gentle: true });
        }
        ctx.fillStyle = 'rgba(3,4,10,0.4)';
        ctx.fillRect(0, 0, w, h);
        const maxR = Math.hypot(w, h) / 2;
        // ambient rosette: layered petals always turning, so the frame is never empty
        const baseR = Math.min(w, h) * 0.16 * (1 + bass * 0.35 + Math.sin(S.t * 1.4) * 0.04);
        for (let layer = 0; layer < 3; layer++) {
            const petals = 8 + layer * 4;
            const lr = baseR * (1 + layer * 0.55);
            const rot = S.t * (0.10 + layer * 0.05) * (layer % 2 ? -1 : 1);
            const la = 0.28 - layer * 0.055 + bass * 0.15;
            const lineCol = (layer === 1 ? npv2Css2(S, la.toFixed(3)) : npv2Css(S, la.toFixed(3)));
            const fillCol = (layer === 1 ? npv2Css2(S, (la * 0.30).toFixed(3)) : npv2Css(S, (la * 0.30).toFixed(3)));
            const seg = 6.2832 / petals;
            for (let p = 0; p < petals; p++) {
                // petal: luminous gradient body (bright heart, soft fade) plus outline, long axis radial
                const a0 = rot + p * seg;
                const px = cx + Math.cos(a0) * lr, py = cy + Math.sin(a0) * lr;
                const pr = lr * 0.30;
                const pg = ctx.createRadialGradient(px, py, 0, px, py, pr * 1.15);
                pg.addColorStop(0, lineCol);
                pg.addColorStop(0.5, fillCol);
                pg.addColorStop(1, npv2Css(S, 0));
                ctx.fillStyle = pg;
                ctx.beginPath();
                ctx.ellipse(px, py, pr, lr * 0.17, a0, 0, 6.2832);
                ctx.fill();
                ctx.strokeStyle = lineCol;
                ctx.lineWidth = 1.4;
                ctx.beginPath();
                ctx.ellipse(px, py, pr, lr * 0.17, a0, 0, 6.2832);
                ctx.stroke();
            }
        }
        for (let i = st.rings.length - 1; i >= 0; i--) {
            const rg = st.rings[i];
            rg.r += (4 + bass * 14) * 0.9 * (rg.gentle ? 0.6 : 1) * f;
            rg.rot += 0.004 * (i % 2 ? 1 : -1) * f;
            const a = Math.max(0, 1 - rg.r / maxR);
            if (a <= 0) { st.rings.splice(i, 1); continue; }
            const alpha = (a * (rg.gentle ? 0.45 : 0.9)).toFixed(3);
            ctx.strokeStyle = rg.accent ? npv2Css2(S, alpha) : npv2Css(S, alpha);
            ctx.lineWidth = rg.w * a + 0.8;
            // petal ring: arc segments with gaps, slowly counter-rotating
            const seg = 6.2832 / rg.petals;
            for (let p = 0; p < rg.petals; p++) {
                const a0 = rg.rot + p * seg;
                ctx.beginPath();
                ctx.arc(cx, cy, rg.r, a0, a0 + seg * 0.62);
                ctx.stroke();
                // shed sparkles off the petal tips while the ring is young
                if (rg.r < maxR * 0.55 && Math.random() < 0.06 * f && st.sparks.length < 140) {
                    const ta = a0 + seg * 0.31;
                    const sp = 30 + Math.random() * 60;
                    st.sparks.push({
                        x: cx + Math.cos(ta) * rg.r, y: cy + Math.sin(ta) * rg.r,
                        vx: Math.cos(ta) * sp, vy: Math.sin(ta) * sp,
                        life: 1, accent: rg.accent,
                    });
                }
            }
        }
        // sparkles drift outward and die
        for (let i = st.sparks.length - 1; i >= 0; i--) {
            const sp = st.sparks[i];
            sp.x += sp.vx * 0.016 * f; sp.y += sp.vy * 0.016 * f;
            sp.vx *= Math.pow(0.985, f); sp.vy *= Math.pow(0.985, f);
            sp.life -= 0.014 * f;
            if (sp.life <= 0) { st.sparks.splice(i, 1); continue; }
            ctx.fillStyle = sp.accent
                ? npv2Css2(S, (sp.life * 0.9).toFixed(3))
                : npv2Css(S, (sp.life * 0.9).toFixed(3));
            ctx.beginPath(); ctx.arc(sp.x, sp.y, 2.0, 0, 6.2832); ctx.fill();
        }
        // breathing core
        const core = 26 + bass * 60 + Math.sin(S.t * 2.2) * 8 + beat * 22;
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, core * 2.4);
        g.addColorStop(0, 'rgba(255,255,255,' + (0.35 + bass * 0.4).toFixed(3) + ')');
        g.addColorStop(0.4, npv2Css(S, 0.5));
        g.addColorStop(1, npv2Css(S, 0));
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(cx, cy, core * 2.4, 0, 6.2832);
        ctx.fill();
    },
};


// ---------------------------------------------------------------------------
// Runtime state
// ---------------------------------------------------------------------------

const NPV2 = {
    theme: 'aurora',
    bgOn: true,
    vizLastCycle: 0,        // NPV2.vizT at the last manual/auto theme change
    vizQuality: 'auto',     // auto | high | balanced | lite
    vizEnergy: 1,           // 0.5..1.5 visual responsiveness multiplier
    vizPalette: 'auto',     // auto (from album art) or a palette id
    vizDim: 0,              // 0..0.6 background dim for legibility
    vizAutocycle: 'off',    // off | track | 30 | 60 | 300 (seconds)
    reduceMotion: false,    // slow the visuals down for sensitive viewers
    speed: 1,
    loop: { mode: 'off', a: 0, b: 0 },
    eq: { attached: false, bands: null, comp: null, on: false, gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0], preset: 'Flat', normalize: false },
    wake: false,
    wakeLock: null,
    tab: 'queue',
    lastTrackId: '',
    palette: { r: 29, g: 185, b: 84 },
    palette2: null,         // the cover's second color (v1 extracts it)
    palCur: null,           // eased palette actually painted (fades on track change)
    pal2Cur: null,
    trail: null,            // {c, ctx, ready, theme} previous frame for NPV2_TRAILS
    fade: null,             // {c, ctx} the last frame of the previous theme
    fadeA: 0,               // its remaining opacity while it crossfades out
    vizRaf: 0,
    vizLast: 0,
    vizT: 0,
    canvas: null,
    ctx: null,
    visualsMode: false,
    uiHideTimer: 0,
};

function npv2GetG(name, fallback) {
    try {
        const v = localStorage.getItem('soulsync-npv2-' + name);
        return v === null || v === undefined ? fallback : v;
    } catch (e) { return fallback; }
}

function npv2SetG(name, value) {
    try { localStorage.setItem('soulsync-npv2-' + name, String(value)); } catch (e) {}
}

function npv2AudioEl() {
    try { return document.getElementById('audio-player') || null; } catch (e) { return null; }
}

function npv2IsPlaying() {
    try { return typeof isPlaying !== 'undefined' && !!isPlaying; } catch (e) { return false; }
}

function npv2CurrentTrack() {
    try { return (typeof currentTrack !== 'undefined' && currentTrack) || null; } catch (e) { return null; }
}

function npv2ModalOpen() {
    try {
        const o = document.getElementById('np-modal-overlay');
        return !!(o && !o.classList.contains('hidden'));
    } catch (e) { return false; }
}

function npv2Toast(msg, kind) {
    try { if (typeof showToast === 'function') showToast(msg, kind || 'info'); } catch (e) {}
}

// ---------------------------------------------------------------------------
// Visual engine
// ---------------------------------------------------------------------------

const NPV2_VIZ = {
    freq: new Uint8Array(NPV2_VIZ_BANDS), // 64 log-spaced bands, 40 Hz–16 kHz
    wave: new Uint8Array(64),
    energy: 0,
    idle: true,
    title: '',
    beat: 0,        // 0..1 beat envelope, set from spectral flux each frame
    onset: false,   // true only on the frame a beat fires
    dt: 0.016,      // this frame's seconds, for frame-rate-independent motion
    waveFull: null, // the whole 2048-sample window, for the scope
    art: null,      // the loaded cover <img>, for the vinyl label
    pal: { r: 29, g: 185, b: 84 },
    pal2: { r: 185, g: 84, b: 29 }, // accent palette: channel-rotated
    q: 1,           // quality factor for particle counts
};

function npv2IdleSynth(S, t) {
    for (let i = 0; i < S.freq.length; i++) {
        const v = 0.5 + 0.5 * Math.sin(t * 0.6 + i * 0.55) * Math.sin(t * 0.23 + i * 0.21);
        S.freq[i] = Math.round(38 + v * 52); // gentle, low — ambient, not a lie
    }
    for (let i = 0; i < S.wave.length; i++) {
        S.wave[i] = Math.round(128 + 26 * Math.sin((i / S.wave.length) * 6.2832 * 2 + t * 0.8));
    }
}

function npv2ReadAudio(S, t, dt) {
    if (typeof dt !== 'number' || !isFinite(dt) || dt <= 0) dt = 0.016;
    let idle = true;
    const prevBeat = S.beat || 0;
    try {
        if (typeof npAnalyser !== 'undefined' && npAnalyser && npv2IsPlaying()) {
            const va = npv2EnsureVizAnalyser();
            if (va) {
                // Song-accurate path: 2048-point FFT mapped to log bands.
                let sr = 48000;
                try { sr = npAudioContext.sampleRate || 48000; } catch (e) {}
                if (!npv2VizBandRanges || npv2VizSr !== sr) {
                    npv2VizBandRanges = npv2LogBandRanges(sr, NPV2_VIZ_FFT, S.freq.length, NPV2_VIZ_FMIN, NPV2_VIZ_FMAX);
                    npv2VizSr = sr;
                    npv2VizRaw = new Uint8Array(NPV2_VIZ_FFT / 2);
                }
                va.getByteFrequencyData(npv2VizRaw);
                va.getByteTimeDomainData(S.wave);
                if (!S.waveFull || S.waveFull.length !== NPV2_VIZ_FFT) S.waveFull = new Uint8Array(NPV2_VIZ_FFT);
                va.getByteTimeDomainData(S.waveFull);
                npv2MapLogBands(npv2VizRaw, npv2VizBandRanges, S.freq);
                let sum = 0;
                for (let i = 0; i < S.freq.length; i++) sum += S.freq[i];
                S.energy = sum / S.freq.length / 255;
                S.beat = npv2FluxBeat(npv2VizBeatState(), S.freq, dt);
            } else {
                // Fallback: the shared 32-bin analyser. Zero the tail first —
                // getByteFrequencyData only fills the first 32 bins.
                S.freq.fill(0);
                npAnalyser.getByteFrequencyData(S.freq);
                npAnalyser.getByteTimeDomainData(S.wave);
                let sum = 0;
                for (let i = 1; i < 32 && i < S.freq.length; i++) sum += S.freq[i];
                S.energy = sum / 31 / 255;
                // Legacy bass-threshold onset (only used when the dedicated
                // analyser could not be created).
                const bass = (S.freq[0] + S.freq[1] + S.freq[2]) / 3 / 255;
                if (bass > 0.55 && S.beat < 0.35) S.beat = 1;
                S.beat = Math.max(0, S.beat - dt * 2.4);
            }
            idle = false;
        }
    } catch (e) { /* analyser unavailable — idle synth below */ }
    if (idle) {
        npv2IdleSynth(S, t);
        S.energy = 0.22;
        S.beat = Math.max(0, (S.beat || 0) - dt * 2.4);
        S.waveFull = null; // the scope falls back to the synthesized wave
    }
    // a beat only ever jumps up when it fires; everything else decays
    S.onset = !idle && S.beat > prevBeat + 1e-6;
    S.dt = dt;
    S.idle = idle;
}

function npv2SizeCanvas() {
    const c = NPV2.canvas;
    if (!c) return false;
    const spec = npv2QualitySpec(NPV2.vizQuality);
    const dpr = Math.min(spec.dpr, (typeof window !== 'undefined' && window.devicePixelRatio) || 1);
    const w = Math.max(2, Math.floor(c.clientWidth * dpr));
    const h = Math.max(2, Math.floor(c.clientHeight * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    NPV2_VIZ.q = spec.q;
    return true;
}

function npv2VizFrame(now) {
    NPV2.vizRaf = requestAnimationFrame(npv2VizFrame);
    if (!NPV2.bgOn || NPV2.theme === 'none' || !npv2ModalOpen()) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    const ctx = NPV2.ctx;
    if (!ctx) return;
    const dt = Math.min(0.1, (now - NPV2.vizLast) / 1000 || 0.016);
    NPV2.vizLast = now;
    // reduce-motion slows the visual clock; painters read S.t
    NPV2.vizT += dt * (NPV2.reduceMotion ? 0.3 : 1);
    if (!npv2SizeCanvas()) return;
    const w = NPV2.canvas.width, h = NPV2.canvas.height;
    const S = NPV2_VIZ;
    npv2ReadAudio(S, NPV2.vizT, dt);
    // energy scaled by the user's visual energy; capped in reduce-motion
    S.energy = Math.min(1, S.energy * NPV2.vizEnergy);
    if (NPV2.reduceMotion) S.energy = Math.min(S.energy, 0.45);
    // the beat envelope is set AND decayed once, in npv2ReadAudio (spectral
    // flux, or the bass fallback without the dedicated analyser). a second
    // bass-threshold pass here re-fired on any steady loud bassline and
    // decayed every beat twice as fast
    S.t = NPV2.vizT;
    // palette: album art by default, or a curated override. the accent is
    // the cover's own second color when there is one. both ease toward their
    // target over ~1s, so a new song's colors fade in instead of snapping
    const curated = NPV2.vizPalette !== 'auto' && NPV2_PALETTES[NPV2.vizPalette];
    const pal = curated || NPV2.palette;
    const pal2 = (!curated && NPV2.palette2) || npv2Analogous(pal);
    NPV2.palCur = npv2EaseColor(NPV2.palCur, pal, S);
    NPV2.pal2Cur = npv2EaseColor(NPV2.pal2Cur, pal2, S);
    S.pal = NPV2.palCur;
    S.pal2 = NPV2.pal2Cur;
    S.art = npv2ArtImage();
    // automatic theme rotation
    const ac = npv2AutocycleSeconds(NPV2.vizAutocycle);
    if (ac > 0 && NPV2.vizT - NPV2.vizLastCycle >= ac) npv2CycleTheme(true);
    const paint = NPV2_PAINT[NPV2.theme];
    ctx.clearRect(0, 0, w, h);
    const trail = NPV2_TRAILS[NPV2.theme];
    if (trail) npv2DrawTrail(ctx, w, h, trail, S);
    try { if (typeof paint === 'function') paint(ctx, w, h, S); } catch (e) { /* a bad frame is not a broken player */ }
    if (trail) npv2KeepTrail(NPV2.canvas, w, h);
    // the previous theme's last frame fades out on top of the new one
    if (NPV2.fadeA > 0 && NPV2.fade) {
        ctx.save();
        ctx.globalAlpha = NPV2.fadeA;
        ctx.drawImage(NPV2.fade.c, 0, 0, w, h);
        ctx.restore();
        NPV2.fadeA = Math.max(0, NPV2.fadeA - S.dt / 0.8);
    }
    // wake-lock transition check, throttled to the frame loop
    npv2UpdateWakeLock();
}

// A palette's close neighbour (hue rotated ~35 degrees, lifted), for when
// there is no second cover color. In harmony with the first, never the old
// channel swap that turned a green cover's accent muddy red. Pure.
function npv2Analogous(p) {
    const t = (35 / 360) * 2 * Math.PI;
    const cos = Math.cos(t), sin = Math.sin(t);
    const k = (1 - cos) / 3, q = Math.sqrt(1 / 3) * sin;
    const c = (v) => Math.max(0, Math.min(255, Math.round(v * 1.12 + 10)));
    return {
        r: c(p.r * (cos + k) + p.g * (k - q) + p.b * (k + q)),
        g: c(p.r * (k + q) + p.g * (cos + k) + p.b * (k - q)),
        b: c(p.r * (k - q) + p.g * (k + q) + p.b * (cos + k)),
    };
}

// Ease a color toward its target over ~1s. Pure apart from the input.
function npv2EaseColor(cur, target, S) {
    if (!cur) return { r: target.r, g: target.g, b: target.b };
    const k = npv2Ease(0.06, S);
    return {
        r: cur.r + (target.r - cur.r) * k,
        g: cur.g + (target.g - cur.g) * k,
        b: cur.b + (target.b - cur.b) * k,
    };
}

// The theater's loaded cover image, or null while it loads / fails.
function npv2ArtImage() {
    try {
        const img = document.getElementById('np-album-art');
        return img && img.complete && img.naturalWidth > 0 && !/trans2\.png/.test(img.src || '') ? img : null;
    } catch (e) { return null; }
}

function npv2Buffer(slot, w, h) {
    let b = NPV2[slot];
    if (!b) {
        try {
            const c = document.createElement('canvas');
            const cx = c.getContext('2d');
            if (!cx) return null;
            b = { c, ctx: cx, ready: false };
            NPV2[slot] = b;
        } catch (e) { return null; }
    }
    if (b.c.width !== w || b.c.height !== h) { b.c.width = w; b.c.height = h; b.ready = false; }
    return b;
}

function npv2DrawTrail(ctx, w, h, trail, S) {
    const b = npv2Buffer('trail', w, h);
    if (!b || !b.ready || b.theme !== NPV2.theme) return;
    const f = npv2F(S);
    ctx.save();
    ctx.globalAlpha = Math.pow(trail.keep, f);
    ctx.translate(w / 2, h / 2);
    // reduce motion keeps the fade but drops the zoom / spin feedback
    if (!NPV2.reduceMotion) {
        const z = Math.pow(1 + (trail.zoom || 0), f);
        ctx.scale(z, z);
        if (trail.spin) ctx.rotate(trail.spin * f);
    }
    ctx.drawImage(b.c, -w / 2, -h / 2);
    ctx.restore();
}

function npv2KeepTrail(canvas, w, h) {
    const b = npv2Buffer('trail', w, h);
    if (!b || !canvas) return;
    b.ctx.globalCompositeOperation = 'copy';
    b.ctx.drawImage(canvas, 0, 0);
    b.ctx.globalCompositeOperation = 'source-over';
    b.ready = true;
    b.theme = NPV2.theme;
}

// ---------------------------------------------------------------------------
// Mini player glow: the same cover colors and the same analysis, at a whisper.
// Two soft glows breathe with the bass and drift; a faint spectrum runs along
// the bottom edge. Runs only while it is wanted (music playing, Visuals on,
// reduce motion off, tab visible, theater closed: the theater has its own).
// ---------------------------------------------------------------------------

const NPV2_MINI = { raf: 0, c: null, ctx: null, last: 0, t: 0, tick: 0, pal: null, pal2: null, palT: null, pal2T: null };

// Paint one mini frame. Pure apart from ctx: S is the shared analysis frame.
function npv2PaintMini(ctx, w, h, S, pal, pal2) {
    const t = (S && S.t) || 0;
    const beat = npv2BeatAmp(S);
    const bass = (S && S.freq && S.freq.length) ? (S.freq[0] + S.freq[1] + S.freq[2] + S.freq[3]) / 4 / 255 : 0.3;
    const lift = Math.min(1, bass * 0.8 + beat * 0.5);
    ctx.clearRect(0, 0, w, h);
    const glow = (x, y, r, p, a) => {
        const g = ctx.createRadialGradient(x, y, 0, x, y, r);
        g.addColorStop(0, npv2PalA(p, a.toFixed(3)));
        g.addColorStop(1, npv2PalA(p, 0));
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, w, h);
    };
    glow(w * (0.24 + 0.08 * Math.sin(t * 0.35)), h * 0.55, h * (1.3 + 0.4 * lift), pal, 0.22 + 0.26 * lift);
    glow(w * (0.78 + 0.07 * Math.cos(t * 0.28)), h * 0.45, h * (1.1 + 0.3 * lift), pal2, 0.16 + 0.16 * lift);
    // spectrum whisper: a thin row of bars along the bottom edge
    const n = 48;
    const bw = w / n;
    for (let i = 0; i < n; i++) {
        const v = npv2Bin(S, i, n);
        const bh = 1.5 + v * h * 0.16;
        ctx.fillStyle = 'rgba(255,255,255,' + (0.05 + v * 0.14).toFixed(3) + ')';
        ctx.fillRect(i * bw + 1, h - bh, Math.max(1, bw - 2), bh);
    }
}

// Should the mini glow be animating right now? Pure over its inputs.
function npv2MiniWanted(o) {
    return !!(o && o.present && !o.idle && o.bgOn && !o.reduceMotion && o.playing && !o.hidden && !o.theaterOpen);
}

function npv2MiniState() {
    let el = null;
    try { el = document.getElementById('media-player'); } catch (e) {}
    let hidden = false;
    try { hidden = !!document.hidden; } catch (e) {}
    return {
        el,
        present: !!el,
        idle: !el || el.classList.contains('idle'),
        bgOn: NPV2.bgOn,
        reduceMotion: NPV2.reduceMotion,
        playing: npv2IsPlaying(),
        hidden,
        theaterOpen: npv2ModalOpen(),
    };
}

// The cover colors media-player.js put on #media-player.
function npv2MiniReadPalette(el) {
    try {
        const cs = getComputedStyle(el);
        const read = (pfx) => {
            const r = parseInt(cs.getPropertyValue(pfx + '-r'), 10);
            const g = parseInt(cs.getPropertyValue(pfx + '-g'), 10);
            const b = parseInt(cs.getPropertyValue(pfx + '-b'), 10);
            return [r, g, b].every((n) => isFinite(n)) ? { r, g, b } : null;
        };
        NPV2_MINI.palT = read('--np-ambient') || NPV2.palette;
        NPV2_MINI.pal2T = read('--np-ambient2') || npv2Analogous(NPV2_MINI.palT);
    } catch (e) {}
}

function npv2MiniFrame(now) {
    const st = npv2MiniState();
    if (!npv2MiniWanted(st)) {
        NPV2_MINI.raf = 0;
        if (NPV2_MINI.c) NPV2_MINI.c.classList.remove('on');
        return;
    }
    NPV2_MINI.raf = requestAnimationFrame(npv2MiniFrame);
    const c = NPV2_MINI.c, ctx = NPV2_MINI.ctx;
    if (!c || !ctx) return;
    const dt = Math.min(0.1, (now - NPV2_MINI.last) / 1000 || 0.016);
    NPV2_MINI.last = now;
    NPV2_MINI.t += dt;
    // soft content: 1x pixels are plenty
    const w = Math.max(2, c.clientWidth), h = Math.max(2, c.clientHeight);
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const S = NPV2_VIZ;
    npv2ReadAudio(S, NPV2_MINI.t, dt);
    S.t = NPV2_MINI.t;
    S.dt = dt;
    if (!NPV2_MINI.palT || (NPV2_MINI.tick++ % 45) === 0) npv2MiniReadPalette(st.el);
    NPV2_MINI.pal = npv2EaseColor(NPV2_MINI.pal, NPV2_MINI.palT || NPV2.palette, S);
    NPV2_MINI.pal2 = npv2EaseColor(NPV2_MINI.pal2, NPV2_MINI.pal2T || npv2Analogous(NPV2.palette), S);
    try { npv2PaintMini(ctx, w, h, S, NPV2_MINI.pal, NPV2_MINI.pal2); } catch (e) { /* never break the player */ }
}

// Start the mini glow if it is wanted and not already running. Cheap; called
// from play / pause / visibility / theater close / Visuals toggles, plus a
// 1s safety net.
function npv2MiniKick() {
    try {
        if (!NPV2_MINI.c) {
            const c = document.getElementById('mp-viz');
            if (!c) return;
            NPV2_MINI.c = c;
            NPV2_MINI.ctx = c.getContext('2d');
        }
        const st = npv2MiniState();
        if (!npv2MiniWanted(st)) {
            NPV2_MINI.c.classList.remove('on');
            return;
        }
        NPV2_MINI.c.classList.add('on');
        if (!NPV2_MINI.raf) {
            NPV2_MINI.last = performance.now();
            NPV2_MINI.raf = requestAnimationFrame(npv2MiniFrame);
        }
    } catch (e) {}
}

function npv2MiniInit() {
    try {
        const a = npv2AudioEl();
        if (a) ['play', 'playing', 'pause', 'ended'].forEach((ev) => a.addEventListener(ev, () => setTimeout(npv2MiniKick, 0)));
        document.addEventListener('visibilitychange', npv2MiniKick);
        setInterval(npv2MiniKick, 1000);
        npv2MiniKick();
    } catch (e) {}
}

function npv2VizStart() {
    if (NPV2.vizRaf) return;
    try {
        NPV2.canvas = document.getElementById('np-v2-bg');
        NPV2.ctx = NPV2.canvas ? NPV2.canvas.getContext('2d') : null;
    } catch (e) { NPV2.ctx = null; }
    NPV2.vizLast = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
    NPV2.vizRaf = requestAnimationFrame(npv2VizFrame);
}

function npv2VizStop() {
    if (NPV2.vizRaf) { cancelAnimationFrame(NPV2.vizRaf); NPV2.vizRaf = 0; }
}

function npv2SetTheme(id, opts) {
    const ids = npv2ThemeIds();
    const next = ids.indexOf(id) >= 0 ? id : 'aurora';
    // crossfade: keep the outgoing theme's last frame and fade it out over
    // the new one, instead of a hard cut
    if (next !== NPV2.theme && NPV2.canvas && NPV2.vizRaf && NPV2.theme !== 'none') {
        const b = npv2Buffer('fade', NPV2.canvas.width, NPV2.canvas.height);
        if (b) {
            b.ctx.globalCompositeOperation = 'copy';
            b.ctx.drawImage(NPV2.canvas, 0, 0);
            b.ctx.globalCompositeOperation = 'source-over';
            NPV2.fadeA = 1;
        }
    }
    NPV2.theme = next;
    NPV2.vizLastCycle = NPV2.vizT; // autocycle restarts from a manual change
    npv2SetG('theme', next);
    try {
        document.querySelectorAll('#np-v2-theme-grid [data-theme]').forEach((b) => {
            b.classList.toggle('selected', b.getAttribute('data-theme') === next);
            b.setAttribute('aria-pressed', b.getAttribute('data-theme') === next ? 'true' : 'false');
        });
    } catch (e) {}
    const meta = npv2ThemeList().find((t) => t.id === next);
    if (!opts || !opts.silent) npv2Toast('Visuals: ' + (meta ? meta.name : next), 'info');
    npv2SyncVisualBtn();
    npv2SyncVisualsHeader();
}

function npv2CycleTheme(skipOff) {
    let ids = npv2ThemeIds();
    // automatic rotation never lands on the Off theme — a black screen
    // mid-rotation looks broken. Manual cycling still offers Off.
    if (skipOff) ids = ids.filter((id) => id !== 'none');
    if (!ids.length) return;
    let idx = ids.indexOf(NPV2.theme);
    if (idx < 0) idx = 0; else idx = (idx + 1) % ids.length;
    npv2SetTheme(ids[idx]);
}

function npv2SetBgOn(on) {
    NPV2.bgOn = !!on;
    npv2SetG('bg', NPV2.bgOn ? '1' : '0');
    try {
        const c = document.getElementById('np-v2-bg');
        if (c) c.style.display = NPV2.bgOn && NPV2.theme !== 'none' ? '' : 'none';
        const t = document.getElementById('np-v2-bg-toggle');
        if (t) { t.checked = NPV2.bgOn; }
    } catch (e) {}
    npv2MiniKick(); // the mini player follows the same Visuals switch
}

// --- Visual options: every one of these changes what the painters do --------

function npv2SetVizQuality(v) {
    const ok = ['auto', 'high', 'balanced', 'lite'].indexOf(v) >= 0 ? v : 'auto';
    NPV2.vizQuality = ok;
    npv2SetG('viz-quality', ok);
    try {
        const s = document.getElementById('np-v2-viz-quality');
        if (s) s.value = ok;
    } catch (e) {}
    npv2SizeCanvas(); // re-size immediately so the dpr change is visible
}

function npv2SetVizEnergy(v) {
    NPV2.vizEnergy = npv2ClampVizEnergy(v);
    npv2SetG('viz-energy', String(NPV2.vizEnergy));
    npv2SyncVizRange('np-v2-viz-energy', 'np-v2-viz-energy-val', NPV2.vizEnergy, (x) => x.toFixed(1) + '×');
}

function npv2SetVizPalette(v) {
    const ok = npv2PaletteList().some((p) => p.id === v) ? v : 'auto';
    NPV2.vizPalette = ok;
    npv2SetG('viz-palette', ok);
    try {
        const s = document.getElementById('np-v2-viz-palette');
        if (s) s.value = ok;
    } catch (e) {}
}

function npv2SetVizDim(v) {
    NPV2.vizDim = npv2ClampVizDim(v);
    npv2SetG('viz-dim', String(NPV2.vizDim));
    try {
        const c = document.getElementById('np-v2-bg');
        if (c) c.style.opacity = String(1 - NPV2.vizDim);
    } catch (e) {}
    npv2SyncVizRange('np-v2-viz-dim', 'np-v2-viz-dim-val', NPV2.vizDim, (x) => Math.round(x * 100) + '%');
}

function npv2SetVizAutocycle(v) {
    const ok = ['off', 'track', '30', '60', '300'].indexOf(v) >= 0 ? v : 'off';
    NPV2.vizAutocycle = ok;
    NPV2.vizLastCycle = NPV2.vizT;
    npv2SetG('viz-autocycle', ok);
    try {
        const s = document.getElementById('np-v2-viz-autocycle');
        if (s) s.value = ok;
    } catch (e) {}
}

function npv2SetReduceMotion(on) {
    NPV2.reduceMotion = !!on;
    npv2SetG('reduce-motion', NPV2.reduceMotion ? '1' : '0');
    try {
        const t = document.getElementById('np-v2-reduce-motion');
        if (t) t.checked = NPV2.reduceMotion;
    } catch (e) {}
    npv2MiniKick();
}

// Sync a range slider + its value label.
function npv2SyncVizRange(rangeId, valId, value, fmt) {
    try {
        const r = document.getElementById(rangeId);
        if (r) r.value = String(value);
        const l = document.getElementById(valId);
        if (l) l.textContent = fmt(value);
    } catch (e) {}
}

function npv2SyncVisualBtn() {
    try {
        const b = document.getElementById('np-v2-visual-btn');
        if (!b) return;
        const meta = npv2ThemeList().find((t) => t.id === NPV2.theme);
        b.title = 'Visuals: ' + (meta ? meta.name + ' — ' + meta.blurb : NPV2.theme) + ' (click to change)';
        const label = b.querySelector('span');
        if (label) label.textContent = meta ? meta.name : 'Visuals';
    } catch (e) {}
}

function npv2PrevTheme() {
    const ids = npv2ThemeIds();
    const i = ids.indexOf(NPV2.theme);
    npv2SetTheme(ids[(i - 1 + ids.length) % ids.length]);
}

// ---------------------------------------------------------------------------
// Full-modal visuals mode — the WMP/Plexamp-style takeover. The animated
// background covers the whole modal; transport + features float over it.
// Enter: click the album art, the expand button, or F. Exit: Esc, F, or the
// exit button. The chrome auto-hides after 3s idle.
// ---------------------------------------------------------------------------

function npv2VisualsModeOn() {
    return !!(NPV2.visualsMode && npv2ModalOpen());
}

function npv2SetVisualsMode(on) {
    const want = !!on;
    if (want === NPV2.visualsMode) return;
    NPV2.visualsMode = want;
    try {
        const modal = document.querySelector('.np-modal');
        if (modal) modal.classList.toggle('npv2-visuals-on', want);
        const exit = document.getElementById('np-v2-visuals-exit');
        if (exit) exit.classList.toggle('hidden', !want);
        const eb = document.getElementById('np-v2-visuals-expand');
        if (eb) {
            eb.classList.toggle('active', want);
            eb.setAttribute('aria-pressed', want ? 'true' : 'false');
            eb.title = want ? 'Exit immersive mode (F or Esc)' : 'Immersive visuals mode (F)';
        }
        if (want) {
            // entering: make sure the background is actually painting
            if (!NPV2.bgOn) npv2SetBgOn(true);
            if (NPV2.theme === 'none') npv2SetTheme('aurora', { silent: true });
            npv2SyncVisualsHeader();
            npv2VizStart();
            npv2VisualsWakeUI();
        } else {
            npv2VisualsClearIdle();
            if (modal) modal.classList.remove('npv2-ui-hidden');
            npv2CloseVisualsPanel();
            npv2ToggleVisualsDrawer(false);
        }
    } catch (e) {}
}

function npv2VisualsClearIdle() {
    try {
        if (NPV2.uiHideTimer) { clearTimeout(NPV2.uiHideTimer); NPV2.uiHideTimer = 0; }
    } catch (e) {}
}

// Any pointer activity wakes the chrome and restarts the 3s auto-hide.
function npv2VisualsWakeUI() {
    try {
        const modal = document.querySelector('.np-modal');
        if (!npv2VisualsModeOn() || !modal) return;
        modal.classList.remove('npv2-ui-hidden');
        npv2VisualsClearIdle();
        NPV2.uiHideTimer = setTimeout(() => {
            try {
                if (npv2VisualsModeOn()) modal.classList.add('npv2-ui-hidden');
            } catch (e) {}
        }, 3000);
    } catch (e) {}
}

function npv2SyncVisualsHeader() {
    try {
        const t = npv2CurrentTrack() || {};
        const title = document.getElementById('np-v2-visuals-title');
        const sub = document.getElementById('np-v2-visuals-artist');
        const theme = document.getElementById('np-v2-visuals-theme-name');
        if (title) title.textContent = t.title || NPV2_VIZ.title || 'Unknown Track';
        if (sub) {
            const bits = [t.artist, t.album].filter(Boolean);
            sub.textContent = bits.join(' — ') || 'Unknown Artist';
        }
        const meta = npv2ThemeList().find((x) => x.id === NPV2.theme);
        if (theme) theme.textContent = meta ? meta.name : NPV2.theme;
    } catch (e) {}
}

// True browser fullscreen for the player modal. This is separate from the
// immersive visuals mode (which only fills the modal): fullscreen fills the
// screen. The two compose — immersive visuals + fullscreen is the full show.
function npv2SetFullscreen(on) {
    const want = typeof on === 'boolean' ? on : !document.fullscreenElement;
    try {
        if (want && !document.fullscreenElement) {
            const el = document.getElementById('np-modal-overlay') || document.documentElement;
            const p = el.requestFullscreen && el.requestFullscreen();
            if (p && p.catch) p.catch(() => npv2Toast('Fullscreen was blocked by the browser', 'error'));
        } else if (!want && document.fullscreenElement) {
            const p = document.exitFullscreen && document.exitFullscreen();
            if (p && p.catch) p.catch(() => {});
        }
    } catch (e) {}
}

function npv2SyncFullscreenBtn() {
    try {
        const b = document.getElementById('np-v2-fullscreen-btn');
        if (!b) return;
        const on = !!document.fullscreenElement;
        b.classList.toggle('active', on);
        b.title = on ? 'Exit fullscreen (Esc)' : 'Fullscreen (fills the screen)';
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    } catch (e) {}
}

// Panels drawer for visuals mode: queue / history / lyrics / details /
// settings stay reachable while the visuals keep playing behind a side
// panel. The panel node is temporarily re-homed into the overlay and put
// back where it came from when the panel closes.
const NPV2_SIDE_PANELS = {
    queue: 'np-queue-panel',
    history: 'np-v2-history-panel',
    lyrics: 'np-lyrics-panel',
    details: 'np-v2-details-panel',
    settings: 'np-v2-settings-panel',
};
let npv2SidePanelHome = null;   // { tab, parent, next }
let npv2SidePanelTab = null;

function npv2OpenVisualsPanel(tab) {
    try {
        if (!NPV2_SIDE_PANELS[tab]) return;
        // toggling the same tab closes it
        if (npv2SidePanelTab === tab) { npv2CloseVisualsPanel(); return; }
        npv2CloseVisualsPanel();
        const node = document.getElementById(NPV2_SIDE_PANELS[tab]);
        const body = document.getElementById('np-v2-sidepanel-body');
        const panel = document.getElementById('np-v2-visuals-sidepanel');
        if (!node || !body || !panel) return;
        npv2SidePanelHome = { tab, parent: node.parentNode, next: node.nextSibling };
        npv2SidePanelTab = tab;
        body.appendChild(node);
        node.classList.remove('hidden');
        npv2SelectTab(tab);
        const names = { queue: 'Queue', history: 'History', lyrics: 'Lyrics', details: 'Details', settings: 'Settings' };
        const t = document.getElementById('np-v2-sidepanel-title');
        if (t) t.textContent = names[tab] || tab;
        panel.classList.remove('hidden');
        npv2ToggleVisualsDrawer(false);
        npv2VisualsWakeUI();
    } catch (e) {}
}

function npv2CloseVisualsPanel() {
    try {
        if (!npv2SidePanelHome) return;
        const node = document.getElementById(NPV2_SIDE_PANELS[npv2SidePanelHome.tab]);
        const home = npv2SidePanelHome;
        npv2SidePanelHome = null;
        npv2SidePanelTab = null;
        if (node && home.parent) {
            if (home.next && home.next.parentNode === home.parent) home.parent.insertBefore(node, home.next);
            else home.parent.appendChild(node);
        }
        const panel = document.getElementById('np-v2-visuals-sidepanel');
        if (panel) panel.classList.add('hidden');
    } catch (e) {}
}

function npv2SidePanelOpen() { return !!npv2SidePanelHome; }

function npv2ToggleVisualsDrawer(open) {
    try {
        const d = document.getElementById('np-v2-visuals-drawer');
        if (!d) return;
        const want = typeof open === 'boolean' ? open : d.classList.contains('hidden');
        d.classList.toggle('hidden', !want);
        const b = document.getElementById('np-v2-visuals-panels');
        if (b) b.classList.toggle('active', want);
    } catch (e) {}
}

// Closes the immersive drawer when a click lands outside both the drawer
// itself and the hamburger button that toggles it.
function npv2DrawerOutsideClick(e) {
    try {
        const d = document.getElementById('np-v2-visuals-drawer');
        if (!d || d.classList.contains('hidden')) return;
        if (d.contains(e.target)) return;
        const pb = document.getElementById('np-v2-visuals-panels');
        if (pb && pb.contains(e.target)) return;
        npv2ToggleVisualsDrawer(false);
    } catch (err) {}
}

// Clicking the album art enters visuals mode (instead of v1's small bars
// overlay). Capture on the overlay so we run before v1's own art handler.
function npv2InterceptArtClick() {
    try {
        const overlay = document.getElementById('np-modal-overlay');
        if (!overlay || overlay.__npv2ArtHook) return;
        overlay.__npv2ArtHook = true;
        overlay.addEventListener('click', (e) => {
            try {
                if (!npv2ModalOpen() || npv2VisualsModeOn()) return;
                const art = e.target && e.target.closest ? e.target.closest('#np-album-art-container') : null;
                if (!art) return;
                e.stopPropagation();
                e.preventDefault();
                npv2SetVisualsMode(true);
            } catch (err) {}
        }, true);
        // pointer activity keeps the visuals chrome awake
        const wake = () => npv2VisualsWakeUI();
        overlay.addEventListener('pointermove', wake, { passive: true });
        overlay.addEventListener('pointerdown', wake, { passive: true });
        // double-click the visuals goes true fullscreen
        const c = document.getElementById('np-v2-bg');
        if (c && !c.__npv2FsHook) {
            c.__npv2FsHook = true;
            c.addEventListener('dblclick', () => {
                if (npv2VisualsModeOn()) npv2SetFullscreen();
            });
        }
    } catch (e) {}
}

function npv2RefreshPalette() {
    try {
        const modal = document.querySelector('.np-modal');
        if (!modal) return;
        const cs = getComputedStyle(modal);
        const r = parseInt(cs.getPropertyValue('--np-ambient-r'), 10);
        const g = parseInt(cs.getPropertyValue('--np-ambient-g'), 10);
        const b = parseInt(cs.getPropertyValue('--np-ambient-b'), 10);
        if ([r, g, b].every((n) => isFinite(n))) NPV2.palette = { r, g, b };
        const r2 = parseInt(cs.getPropertyValue('--np-ambient2-r'), 10);
        const g2 = parseInt(cs.getPropertyValue('--np-ambient2-g'), 10);
        const b2 = parseInt(cs.getPropertyValue('--np-ambient2-b'), 10);
        NPV2.palette2 = [r2, g2, b2].every((n) => isFinite(n)) ? { r: r2, g: g2, b: b2 } : null;
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// Equalizer (Web Audio) — inserts into v1's analyser graph
// ---------------------------------------------------------------------------

function npv2AttachAudioGraph() {
    if (NPV2.eq.attached) { npv2ApplyEq(); return true; }
    let AC, src, analyser;
    try {
        AC = (typeof npAudioContext !== 'undefined' && npAudioContext) || null;
        src = (typeof npMediaSource !== 'undefined' && npMediaSource) || null;
        analyser = (typeof npAnalyser !== 'undefined' && npAnalyser) || null;
    } catch (e) { return false; }
    if (!AC || !src || !analyser) return false;
    try {
        const bands = NPV2_EQ_FREQS.map((f) => {
            const bq = AC.createBiquadFilter();
            bq.type = 'peaking';
            bq.frequency.value = f;
            bq.Q.value = 1.0;
            bq.gain.value = 0;
            return bq;
        });
        const comp = AC.createDynamicsCompressor();
        comp.threshold.value = 0;   // transparent until normalization is on
        comp.ratio.value = 1;
        comp.attack.value = 0.003;
        comp.release.value = 0.25;
        // Rewire: source -> bands -> compressor -> analyser -> destination.
        // v1 connected source->analyser directly; disconnect() with no args
        // drops only that edge (it has no other outgoing connections).
        src.disconnect();
        src.connect(bands[0]);
        for (let i = 0; i < bands.length - 1; i++) bands[i].connect(bands[i + 1]);
        bands[bands.length - 1].connect(comp);
        comp.connect(analyser);
        NPV2.eq.attached = true;
        NPV2.eq.bands = bands;
        NPV2.eq.comp = comp;
        npv2ApplyEq();
        return true;
    } catch (e) {
        // If we disconnected the source but failed to rebuild the chain,
        // restore v1's direct source->analyser wiring so audio never goes silent.
        try { src.disconnect(); } catch (e2) {}
        try { src.connect(analyser); } catch (e3) {}
        return false;
    }
}

function npv2ApplyEq() {
    const E = NPV2.eq;
    if (!E.attached || !E.bands) return;
    try {
        for (let i = 0; i < E.bands.length; i++) {
            E.bands[i].gain.value = E.on ? npv2ClampDb(E.gains[i]) : 0;
        }
        if (E.comp) {
            E.comp.threshold.value = E.normalize ? -18 : 0;
            E.comp.ratio.value = E.normalize ? 4 : 1;
        }
    } catch (e) {}
}

function npv2LoadEqSettings() {
    const E = NPV2.eq;
    E.on = npv2Get('eq-on', '0') === '1';
    E.normalize = npv2Get('eq-normalize', '0') === '1';
    E.preset = npv2Get('eq-preset', 'Flat') || 'Flat';
    try {
        const saved = JSON.parse(npv2Get('eq-gains', '[]') || '[]');
        if (Array.isArray(saved) && saved.length === NPV2_EQ_FREQS.length) {
            E.gains = saved.map(npv2ClampDb);
        } else {
            const p = npv2EqPresetGains(E.preset);
            E.gains = p || E.gains.slice();
        }
    } catch (e) {}
}

function npv2PersistEq() {
    const E = NPV2.eq;
    npv2Set('eq-on', E.on ? '1' : '0');
    npv2Set('eq-normalize', E.normalize ? '1' : '0');
    npv2Set('eq-preset', E.preset);
    npv2Set('eq-gains', JSON.stringify(E.gains));
}

function npv2SetEqBand(i, db) {
    const E = NPV2.eq;
    if (i < 0 || i >= NPV2_EQ_FREQS.length) return;
    E.gains[i] = npv2ClampDb(db);
    E.preset = 'Custom';
    if (!E.on) E.on = true;
    npv2AttachAudioGraph(); // builds the chain on first touch, then applies
    npv2ApplyEq();
    npv2PersistEq();
    npv2RenderEqUI();
}

function npv2SetEqPreset(name) {
    const gains = npv2EqPresetGains(name);
    if (!gains) return;
    const E = NPV2.eq;
    E.gains = gains;
    E.preset = name;
    if (!E.on && name !== 'Flat') E.on = true;
    npv2AttachAudioGraph();
    npv2ApplyEq();
    npv2PersistEq();
    npv2RenderEqUI();
}

function npv2SetEqOn(on) {
    NPV2.eq.on = !!on;
    npv2AttachAudioGraph();
    npv2ApplyEq();
    npv2PersistEq();
    npv2RenderEqUI();
    npv2SyncEqBtn();
}

function npv2SetNormalize(on) {
    NPV2.eq.normalize = !!on;
    npv2AttachAudioGraph();
    npv2ApplyEq();
    npv2PersistEq();
    npv2RenderEqUI();
    npv2Toast(on ? 'Loudness normalization on' : 'Loudness normalization off', 'info');
}

function npv2SyncEqBtn() {
    try {
        const b = document.getElementById('np-v2-eq-btn');
        if (b) {
            b.classList.toggle('active', NPV2.eq.on);
            b.setAttribute('aria-pressed', NPV2.eq.on ? 'true' : 'false');
        }
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// Playback speed
// ---------------------------------------------------------------------------

function npv2SetSpeed(v, opts) {
    NPV2.speed = npv2ClampSpeed(v);
    npv2Set('speed', String(NPV2.speed));
    const els = [npv2AudioEl()];
    try { els.push(document.getElementById('audio-player-xfade')); } catch (e) {}
    els.forEach((el) => { if (el) { try { el.playbackRate = NPV2.speed; } catch (e2) {} } });
    try {
        const btn = document.getElementById('np-v2-speed-btn');
        if (btn) {
            const label = btn.querySelector('span');
            if (label) label.textContent = npv2FormatSpeed(NPV2.speed);
            btn.classList.toggle('active', NPV2.speed !== 1);
            btn.title = 'Playback speed: ' + npv2FormatSpeed(NPV2.speed) + ' ([ / ] or shift+click to go slower)';
        }
        const sel = document.getElementById('np-v2-speed-select');
        if (sel) sel.value = String(NPV2.speed);
    } catch (e) {}
    if (!opts || !opts.silent) npv2Toast('Speed ' + npv2FormatSpeed(NPV2.speed), 'info');
}

function npv2CycleSpeed(dir) {
    const i = NPV2_SPEED_PRESETS.indexOf(NPV2.speed);
    const next = NPV2_SPEED_PRESETS[Math.max(0, Math.min(NPV2_SPEED_PRESETS.length - 1, i + dir))];
    npv2SetSpeed(next);
}

// Speed preset picker: one click on the speed button opens the full list so
// any speed is one tap away; the button no longer dead-ends at the presets.
function npv2ToggleSpeedMenu() {
    try {
        let m = document.getElementById('np-v2-speed-menu');
        if (m && !m.classList.contains('hidden')) { m.classList.add('hidden'); return; }
        if (!m) {
            m = document.createElement('div');
            m.id = 'np-v2-speed-menu';
            m.className = 'np-v2-speed-menu hidden';
            m.setAttribute('role', 'menu');
            m.setAttribute('aria-label', 'Playback speed');
            document.body.appendChild(m);
            m.addEventListener('click', (e) => {
                const b = e.target && e.target.closest ? e.target.closest('[data-speed]') : null;
                if (!b) return;
                npv2SetSpeed(parseFloat(b.getAttribute('data-speed')));
                m.classList.add('hidden');
            });
            document.addEventListener('click', (e) => {
                try {
                    const open = document.getElementById('np-v2-speed-menu');
                    if (!open || open.classList.contains('hidden')) return;
                    if (open.contains(e.target)) return;
                    const btn = document.getElementById('np-v2-speed-btn');
                    if (btn && btn.contains(e.target)) return;
                    open.classList.add('hidden');
                } catch (err) {}
            });
        }
        m.innerHTML = '';
        NPV2_SPEED_PRESETS.forEach((p) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.setAttribute('data-speed', String(p));
            b.setAttribute('role', 'menuitemradio');
            const cur = p === NPV2.speed;
            b.setAttribute('aria-checked', cur ? 'true' : 'false');
            b.className = 'np-v2-speed-item' + (cur ? ' active' : '');
            b.textContent = p + '×';
            m.appendChild(b);
        });
        const btn = document.getElementById('np-v2-speed-btn');
        if (btn) {
            const r = btn.getBoundingClientRect();
            m.style.left = Math.max(8, Math.min(window.innerWidth - 96, r.left)) + 'px';
            m.style.top = (r.bottom + 6) + 'px';
        }
        m.classList.remove('hidden');
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// A-B loop
// ---------------------------------------------------------------------------

function npv2AbToggle() {
    const el = npv2AudioEl();
    const now = (el && isFinite(el.currentTime)) ? el.currentTime : 0;
    const prev = NPV2.loop.mode;
    NPV2.loop = npv2AbNext(NPV2.loop, now);
    if (NPV2.loop.mode === 'b' && NPV2.loop.b - NPV2.loop.a < 1) {
        // A 1-second loop is almost always a mis-tap; keep A armed instead.
        NPV2.loop = { mode: 'a', a: NPV2.loop.a, b: 0 };
        npv2Toast('Loop end too close to start — A re-armed', 'info');
    } else if (NPV2.loop.mode !== prev) {
        npv2Toast(npv2AbLabel(NPV2.loop), 'info');
    }
    npv2RenderAbUI();
}

function npv2AbReset() {
    NPV2.loop = { mode: 'off', a: 0, b: 0 };
    npv2RenderAbUI();
}

function npv2LoopTick() {
    if (NPV2.loop.mode !== 'b') return;
    // A crossfade owns the main element's tail — don't fight its fade-out.
    try { if (typeof npXfadeActive !== 'undefined' && npXfadeActive) return; } catch (e) {}
    const el = npv2AudioEl();
    if (!el || !isFinite(el.duration) || !isFinite(el.currentTime)) return;
    if (el.currentTime >= NPV2.loop.b || el.currentTime < NPV2.loop.a - 1) {
        try { el.currentTime = NPV2.loop.a; } catch (e) {}
    }
}

function npv2RenderAbUI() {
    try {
        const btn = document.getElementById('np-v2-ab-btn');
        if (btn) {
            btn.classList.toggle('active', NPV2.loop.mode !== 'off');
            btn.classList.toggle('looping', NPV2.loop.mode === 'b');
            btn.title = npv2AbLabel(NPV2.loop);
            btn.setAttribute('aria-pressed', NPV2.loop.mode !== 'off' ? 'true' : 'false');
        }
        const ind = document.getElementById('np-v2-ab-indicator');
        if (ind) {
            if (NPV2.loop.mode === 'off') {
                ind.classList.add('hidden');
            } else {
                ind.classList.remove('hidden');
                const fmt = (typeof formatTime === 'function') ? formatTime : (s) => String(Math.round(s)) + 's';
                ind.textContent = NPV2.loop.mode === 'a'
                    ? 'A ' + fmt(NPV2.loop.a) + ' →'
                    : '⟳ ' + fmt(NPV2.loop.a) + ' – ' + fmt(NPV2.loop.b);
            }
        }
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// Queue enhancements: "play next" per row + clear-played
// ---------------------------------------------------------------------------

function npv2QueuePlayNext(idx) {
    try {
        if (typeof npQueue === 'undefined' || typeof npQueueIndex === 'undefined') return;
        if (typeof npReorderQueue !== 'function') return;
        const cur = npQueueIndex;
        if (idx === cur || idx < 0 || idx >= npQueue.length) return;
        // Final home is cur+1; account for the removal shift when idx < cur+1.
        let to = idx <= cur ? cur : cur + 1;
        if (to >= npQueue.length) to = npQueue.length - 1;
        npReorderQueue(idx, to);
        npv2Toast('Queued to play next', 'success');
    } catch (e) {}
}

function npv2ClearPlayed() {
    try {
        if (typeof npQueue === 'undefined' || typeof npQueueIndex === 'undefined') return;
        if (typeof renderNpQueue !== 'function') return;
        if (npQueueIndex > 0) {
            const n = npQueueIndex;
            npQueue.splice(0, n);
            npQueueIndex = 0;
            renderNpQueue();
            if (typeof updateNpPrevNextButtons === 'function') updateNpPrevNextButtons();
            npv2Toast('Cleared ' + n + ' played track' + (n === 1 ? '' : 's'), 'success');
        } else {
            npv2Toast('Nothing played yet in this queue', 'info');
        }
    } catch (e) {}
}

function npv2DecorateQueueRow(item) {
    try {
        if (!item || item.hasAttribute('data-npv2-decorated')) return;
        const actions = item.querySelector('.np-queue-item-actions');
        if (!actions) return;
        const idx = Number(item.getAttribute('data-qindex'));
        if (!isFinite(idx)) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'np-queue-item-playnext';
        btn.title = 'Play next';
        btn.setAttribute('aria-label', 'Play next after current track');
        btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>';
        btn.addEventListener('click', (e) => { e.stopPropagation(); npv2QueuePlayNext(idx); });
        // Play-next sits before the move/remove cluster.
        actions.insertBefore(btn, actions.firstChild);
        item.setAttribute('data-npv2-decorated', '1');
    } catch (e) {}
}

function npv2DecorateQueue() {
    try {
        const list = document.getElementById('np-queue-list');
        if (!list) return;
        list.querySelectorAll('.np-queue-item').forEach(npv2DecorateQueueRow);
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

function npv2LoadHistory() {
    try {
        const raw = npv2Get('history', '[]');
        const arr = JSON.parse(raw || '[]');
        return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
}

function npv2SaveHistory(list) {
    npv2Set('history', JSON.stringify(list.slice(0, 50)));
}

function npv2PushHistory(track) {
    if (!track) return;
    const slim = {
        title: track.title, artist: track.artist, album: track.album,
        file_path: track.file_path || track.filename || '',
        filename: track.file_path || track.filename || '',
        is_library: !!(track.file_path || track.filename),
        id: track.id, artist_id: track.artist_id, album_id: track.album_id,
        image_url: track.image_url, bitrate: track.bitrate, sample_rate: track.sample_rate,
        duration: track.duration,
    };
    const list = npv2HistoryPush(npv2LoadHistory(), { entry: slim, ts: Date.now() }, 50);
    npv2SaveHistory(list);
    npv2RenderHistory();
}

function npv2RenderHistory() {
    try {
        const listEl = document.getElementById('np-v2-history-list');
        const emptyEl = document.getElementById('np-v2-history-empty');
        const countEl = document.getElementById('np-v2-history-count');
        if (!listEl) return;
        const list = npv2LoadHistory();
        if (countEl) countEl.textContent = list.length ? '(' + list.length + ')' : '';
        listEl.innerHTML = '';
        if (!list.length) {
            if (emptyEl) emptyEl.classList.remove('hidden');
            return;
        }
        if (emptyEl) emptyEl.classList.add('hidden');
        const now = Date.now();
        list.forEach((h) => {
            const t = h.entry || {};
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'np-v2-history-item';
            const art = document.createElement(t.image_url ? 'img' : 'span');
            if (t.image_url) {
                art.className = 'np-v2-history-art';
                art.src = t.image_url;
                art.alt = '';
                art.onerror = () => { art.style.visibility = 'hidden'; };
            } else {
                art.className = 'np-v2-history-art np-v2-history-art-fallback';
                art.textContent = '♪';
            }
            const info = document.createElement('span');
            info.className = 'np-v2-history-info';
            const title = document.createElement('span');
            title.className = 'np-v2-history-title';
            title.textContent = t.title || 'Unknown Track';
            const sub = document.createElement('span');
            sub.className = 'np-v2-history-sub';
            sub.textContent = (t.artist || 'Unknown Artist') + ' · ' + npv2TimeAgo(h.ts, now);
            info.appendChild(title); info.appendChild(sub);
            row.appendChild(art); row.appendChild(info);
            row.title = 'Play ' + (t.title || 'track');
            row.addEventListener('click', () => {
                try {
                    if (typeof window.playTrackList === 'function') window.playTrackList([t], 'History');
                    else if (typeof playTrackList === 'function') playTrackList([t], 'History');
                } catch (e) {}
            });
            listEl.appendChild(row);
        });
    } catch (e) {}
}

function npv2ClearHistory() {
    npv2SaveHistory([]);
    npv2RenderHistory();
    npv2Toast('Playback history cleared', 'info');
}

// ---------------------------------------------------------------------------
// Details tab
// ---------------------------------------------------------------------------

function npv2RenderDetails() {
    try {
        const rowsEl = document.getElementById('np-v2-details-rows');
        if (!rowsEl) return;
        const t = npv2CurrentTrack();
        const el = npv2AudioEl();
        const dur = el && isFinite(el.duration) && el.duration > 0 ? el.duration
            : (t && isFinite(Number(t.duration)) ? Number(t.duration) : 0);
        const fmt = (typeof formatTime === 'function') ? formatTime : (s) => Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
        const rows = [
            ['Title', (t && t.title) || '—'],
            ['Artist', (t && t.artist) || '—'],
            ['Album', (t && t.album) || '—'],
            ['Duration', dur > 0 ? fmt(dur) : '—'],
            ['Format', npv2FormatExt(t && (t.file_path || t.filename)) || 'Stream'],
            ['Bitrate', t && t.bitrate ? t.bitrate + ' kbps' : '—'],
            ['Sample rate', t && t.sample_rate ? (Number(t.sample_rate) / 1000).toFixed(1) + ' kHz' : '—'],
            ['Speed', npv2FormatSpeed(NPV2.speed)],
            ['Source', (t && (t.source || t.metadata_source)) || (t && t.is_library ? 'Library' : '—')],
            ['File', (t && (t.file_path || t.filename)) || '—'],
        ];
        rowsEl.innerHTML = '';
        rows.forEach(([k, v]) => {
            const r = document.createElement('div');
            r.className = 'np-v2-detail-row';
            const kk = document.createElement('span');
            kk.className = 'np-v2-detail-k';
            kk.textContent = k;
            const vv = document.createElement('span');
            vv.className = 'np-v2-detail-v';
            vv.textContent = String(v);
            vv.title = String(v);
            r.appendChild(kk); r.appendChild(vv);
            rowsEl.appendChild(r);
        });
    } catch (e) {}
}

function npv2CopyText(text, label) {
    const done = () => npv2Toast((label || 'Copied') + ' to clipboard', 'success');
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, () => npv2Toast('Copy failed', 'error'));
        } else {
            const ta = document.createElement('textarea');
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            done();
        }
    } catch (e) { npv2Toast('Copy failed', 'error'); }
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

const NPV2_TABS = ['queue', 'history', 'lyrics', 'details', 'settings'];

function npv2SelectTab(name) {
    if (NPV2_TABS.indexOf(name) < 0) name = 'queue';
    NPV2.tab = name;
    try {
        document.querySelectorAll('[data-npv2-tab]').forEach((b) => {
            const active = b.getAttribute('data-npv2-tab') === name;
            b.classList.toggle('active', active);
            b.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        const panels = {
            queue: 'np-queue-panel',
            history: 'np-v2-history-panel',
            lyrics: 'np-lyrics-panel',
            details: 'np-v2-details-panel',
            settings: 'np-v2-settings-panel',
        };
        Object.keys(panels).forEach((k) => {
            const p = document.getElementById(panels[k]);
            if (p) p.classList.toggle('hidden', k !== name);
        });
        // Tabbing into lyrics/queue auto-expands the v1 collapsible body so
        // the tab is never an empty header.
        if (name === 'lyrics') {
            const body = document.getElementById('np-lyrics-body');
            const panel = document.getElementById('np-lyrics-panel');
            if (body) body.classList.remove('hidden');
            if (panel) panel.classList.remove('collapsed');
        }
        if (name === 'queue') {
            const body = document.getElementById('np-queue-body');
            if (body) body.classList.remove('hidden');
        }
        if (name === 'history') npv2RenderHistory();
        if (name === 'details') npv2RenderDetails();
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// Settings tab construction
// ---------------------------------------------------------------------------

function npv2El(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
}

function npv2BuildThemePicker() {
    try {
        const grid = document.getElementById('np-v2-theme-grid');
        if (!grid || grid.children.length) return;
        npv2ThemeList().forEach((t) => {
            const b = npv2El('button', 'npv2-theme-sw', null);
            b.type = 'button';
            b.setAttribute('data-theme', t.id);
            b.title = t.name + ' — ' + t.blurb;
            b.setAttribute('aria-pressed', t.id === NPV2.theme ? 'true' : 'false');
            if (t.id === NPV2.theme) b.classList.add('selected');
            const sw = npv2El('span', 'npv2-theme-swatch theme-' + t.id);
            const nm = npv2El('span', 'npv2-theme-name', t.name);
            b.appendChild(sw); b.appendChild(nm);
            b.addEventListener('click', () => npv2SetTheme(t.id));
            grid.appendChild(b);
        });
    } catch (e) {}
}

function npv2BuildEqUI() {
    try {
        const wrap = document.getElementById('np-v2-eq-sliders');
        if (!wrap || wrap.children.length) return;
        NPV2_EQ_FREQS.forEach((f, i) => {
            const row = npv2El('div', 'npv2-eq-row');
            const lab = npv2El('span', 'npv2-eq-freq', f >= 1000 ? (f / 1000) + 'k' : String(f));
            const slider = document.createElement('input');
            slider.type = 'range';
            slider.min = String(-NPV2_EQ_RANGE_DB);
            slider.max = String(NPV2_EQ_RANGE_DB);
            slider.step = '0.5';
            slider.value = String(NPV2.eq.gains[i] || 0);
            slider.className = 'npv2-eq-slider';
            slider.setAttribute('aria-label', 'EQ ' + f + ' Hz');
            slider.addEventListener('input', () => npv2SetEqBand(i, Number(slider.value)));
            slider.addEventListener('dblclick', () => npv2SetEqBand(i, 0));
            const val = npv2El('span', 'npv2-eq-val', (NPV2.eq.gains[i] > 0 ? '+' : '') + (NPV2.eq.gains[i] || 0));
            val.id = 'npv2-eq-val-' + i;
            row.appendChild(lab); row.appendChild(slider); row.appendChild(val);
            wrap.appendChild(row);
        });
    } catch (e) {}
}

function npv2RenderEqUI() {
    try {
        const E = NPV2.eq;
        const tgl = document.getElementById('np-v2-eq-toggle');
        if (tgl) tgl.checked = E.on;
        const norm = document.getElementById('np-v2-normalize-toggle');
        if (norm) norm.checked = E.normalize;
        const preset = document.getElementById('np-v2-eq-preset');
        if (preset) {
            if (!preset.options.length) {
                npv2EqPresetNames().forEach((n) => {
                    const o = document.createElement('option');
                    o.value = n; o.textContent = n;
                    preset.appendChild(o);
                });
                const custom = document.createElement('option');
                custom.value = 'Custom'; custom.textContent = 'Custom';
                preset.appendChild(custom);
            }
            preset.value = E.preset;
        }
        const wrap = document.getElementById('np-v2-eq-sliders');
        if (wrap) {
            const sliders = wrap.querySelectorAll('.npv2-eq-slider');
            sliders.forEach((s, i) => {
                s.value = String(E.gains[i] || 0);
                const val = document.getElementById('npv2-eq-val-' + i);
                if (val) val.textContent = (E.gains[i] > 0 ? '+' : '') + (E.gains[i] || 0);
            });
            wrap.classList.toggle('disabled', !E.on);
        }
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// Shortcuts overlay
// ---------------------------------------------------------------------------

function npv2ToggleShortcuts(show) {
    try {
        const o = document.getElementById('np-v2-shorts-overlay');
        if (!o) return;
        const next = typeof show === 'boolean' ? show : o.classList.contains('hidden');
        o.classList.toggle('hidden', !next);
        const b = document.getElementById('np-v2-shorts-btn');
        if (b) b.classList.toggle('active', next);
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// Wake lock
// ---------------------------------------------------------------------------

function npv2UpdateWakeLock() {
    try {
        const want = NPV2.wake && npv2IsPlaying() && npv2ModalOpen();
        if (want && !NPV2.wakeLock && navigator.wakeLock) {
            navigator.wakeLock.request('screen').then((lock) => {
                NPV2.wakeLock = lock;
                lock.addEventListener('release', () => { NPV2.wakeLock = null; });
            }).catch(() => {});
        } else if (!want && NPV2.wakeLock) {
            NPV2.wakeLock.release().catch(() => {});
            NPV2.wakeLock = null;
        }
    } catch (e) {}
}

function npv2SetWake(on) {
    NPV2.wake = !!on;
    npv2Set('wake', NPV2.wake ? '1' : '0');
    try {
        const t = document.getElementById('np-v2-wake-toggle');
        if (t) t.checked = NPV2.wake;
    } catch (e) {}
    npv2UpdateWakeLock();
    npv2Toast(NPV2.wake ? 'Screen will stay awake while playing' : 'Wake lock off', 'info');
}

// ---------------------------------------------------------------------------
// Track-change + modal lifecycle (driven by observers — no v1 edits)
// ---------------------------------------------------------------------------

function npv2OnTrackChanged(track) {
    try {
        const id = npv2TrackIdentity(track);
        if (!id || id === NPV2.lastTrackId) return;
        NPV2.lastTrackId = id;
        NPV2_VIZ.title = (track && track.title) || '';
        npv2PushHistory(track);
        npv2RenderDetails();
        npv2SyncVisualsHeader();
        npv2AbReset();
        npv2SetSpeed(NPV2.speed, { silent: true }); // re-stamp playbackRate (covers the xfade element too)
        // Per-track visual rotation: a fresh visual for a fresh song.
        if (NPV2.vizAutocycle === 'track' && npv2ModalOpen() && NPV2.bgOn) npv2CycleTheme(true);
        // The Web Audio context is created lazily on first play; if EQ was
        // switched on before that, this is where the graph finally exists.
        if (NPV2.eq.on || NPV2.eq.normalize) npv2AttachAudioGraph();
        // Palette follows the album-art glow v1 extracts (may lag one frame).
        setTimeout(npv2RefreshPalette, 350);
    } catch (e) {}
}

function npv2WatchTrackTitle() {
    try {
        const el = document.getElementById('np-track-title');
        if (!el) return;
        let scheduled = false;
        const onMut = () => {
            if (scheduled) return;
            scheduled = true;
            setTimeout(() => {
                scheduled = false;
                npv2OnTrackChanged(npv2CurrentTrack());
            }, 120);
        };
        new MutationObserver(onMut).observe(el, { childList: true, characterData: true, subtree: true });
    } catch (e) {}
}

function npv2WatchModal() {
    try {
        const overlay = document.getElementById('np-modal-overlay');
        if (!overlay) return;
        new MutationObserver(() => {
            if (npv2ModalOpen()) {
                npv2VizStart();
                npv2RefreshPalette();
                npv2OnTrackChanged(npv2CurrentTrack());
                if (NPV2.tab === 'details') npv2RenderDetails();
                if (NPV2.tab === 'history') npv2RenderHistory();
            } else {
                npv2VizStop();
                npv2MiniKick();
                npv2ToggleShortcuts(false);
                npv2UpdateWakeLock();
                // closing the theater backs out of immersive mode too
                if (NPV2.visualsMode) npv2SetVisualsMode(false);
            }
        }).observe(overlay, { attributes: true, attributeFilter: ['class'] });
    } catch (e) {}
}

function npv2WatchQueue() {
    try {
        const list = document.getElementById('np-queue-list');
        if (!list) return;
        npv2DecorateQueue();
        new MutationObserver(npv2DecorateQueue).observe(list, { childList: true });
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function npv2WireTransport() {
    const on = (id, fn) => {
        try {
            const el = document.getElementById(id);
            if (el) el.addEventListener('click', (e) => { e.stopPropagation(); fn(e); });
        } catch (e) {}
    };
    on('np-v2-speed-btn', (e) => {
        // click opens the preset picker; shift+click steps down quickly
        if (e && e.shiftKey) { npv2CycleSpeed(-1); return; }
        npv2ToggleSpeedMenu();
    });
    on('np-v2-eq-btn', (e) => {
        // click toggles the EQ; shift+click opens Settings for fine-tuning
        if (e && e.shiftKey) { npv2SelectTab('settings'); return; }
        npv2SetEqOn(!NPV2.eq.on);
    });
    on('np-v2-ab-btn', npv2AbToggle);
    on('np-v2-visual-btn', (e) => {
        // click opens the visual chooser in Settings; shift+click quick-cycles
        if (e && e.shiftKey) { npv2CycleTheme(); return; }
        npv2SelectTab('settings');
        try {
            const g = document.getElementById('np-v2-theme-grid');
            if (g) g.scrollIntoView({ block: 'start', behavior: 'smooth' });
        } catch (err) {}
    });
    on('np-v2-shorts-btn', () => npv2ToggleShortcuts());
    on('np-v2-shorts-close', () => npv2ToggleShortcuts(false));
    on('np-queue-clear-played', npv2ClearPlayed);
    on('np-v2-history-clear', npv2ClearHistory);
    on('np-v2-copy-info', () => {
        const t = npv2CurrentTrack();
        if (!t) return;
        npv2CopyText([t.title, t.artist, t.album].filter(Boolean).join(' — ') || 'Unknown track', 'Track info');
    });
    on('np-v2-copy-path', () => {
        const t = npv2CurrentTrack();
        const p = t && (t.file_path || t.filename);
        if (p) npv2CopyText(p, 'File path');
        else npv2Toast('No file path for this track', 'info');
    });
    on('np-v2-reset-settings', () => {
        try {
            const drop = [];
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                if (k && k.indexOf('soulsync-npv2-') === 0) drop.push(k);
            }
            drop.forEach((k) => localStorage.removeItem(k));
        } catch (e) {}
        location.reload();
    });

    try {
        const tabs = document.querySelectorAll('[data-npv2-tab]');
        tabs.forEach((b) => b.addEventListener('click', () => npv2SelectTab(b.getAttribute('data-npv2-tab'))));
    } catch (e) {}

    // Settings controls
    const bindToggle = (id, fn) => {
        try {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', () => fn(el.checked));
        } catch (e) {}
    };
    bindToggle('np-v2-bg-toggle', (on) => npv2SetBgOn(on));
    bindToggle('np-v2-eq-toggle', (on) => npv2SetEqOn(on));
    bindToggle('np-v2-normalize-toggle', (on) => npv2SetNormalize(on));
    bindToggle('np-v2-wake-toggle', (on) => npv2SetWake(on));
    bindToggle('np-v2-reduce-motion', (on) => npv2SetReduceMotion(on));

    // Full-modal visuals mode wiring
    try {
        npv2InterceptArtClick();
        const exit = document.getElementById('np-v2-visuals-exit');
        if (exit) exit.addEventListener('click', () => npv2SetVisualsMode(false));
        const tp = document.getElementById('np-v2-visuals-theme-prev');
        if (tp) tp.addEventListener('click', () => npv2PrevTheme());
        const tn = document.getElementById('np-v2-visuals-theme-next');
        if (tn) tn.addEventListener('click', () => npv2CycleTheme());
        const expand = document.getElementById('np-v2-visuals-expand');
        if (expand) expand.addEventListener('click', () => npv2SetVisualsMode(!npv2VisualsModeOn()));
        const fsb = document.getElementById('np-v2-fullscreen-btn');
        if (fsb) fsb.addEventListener('click', () => npv2SetFullscreen());
        // Immersive-mode drawer: panels without leaving the visuals
        const pv = document.getElementById('np-v2-visuals-panels');
        if (pv) pv.addEventListener('click', () => npv2ToggleVisualsDrawer());
        // Clicking anywhere outside the open drawer closes it (the hamburger
        // is a toggle, not a dead-end).
        document.addEventListener('click', npv2DrawerOutsideClick);
        const vf = document.getElementById('np-v2-visuals-fullscreen');
        if (vf) vf.addEventListener('click', () => npv2SetFullscreen());
        document.querySelectorAll('#np-v2-visuals-drawer [data-tab]').forEach((b) => {
            b.addEventListener('click', () => {
                npv2OpenVisualsPanel(b.getAttribute('data-tab'));
            });
        });
        const spc = document.getElementById('np-v2-sidepanel-close');
        if (spc) spc.addEventListener('click', () => npv2CloseVisualsPanel());
    } catch (e) {}

    try {
        const preset = document.getElementById('np-v2-eq-preset');
        if (preset) preset.addEventListener('change', () => npv2SetEqPreset(preset.value));
        const xfade = document.getElementById('np-v2-xfade-secs');
        if (xfade) {
            xfade.value = npv2GetG('crossfade-secs', '6');
            xfade.addEventListener('change', () => {
                npv2SetG('crossfade-secs', xfade.value);
                npv2Toast('Crossfade: ' + xfade.value + 's', 'success');
            });
        }
        const spd = document.getElementById('np-v2-speed-select');
        if (spd) {
            spd.value = String(NPV2.speed);
            spd.addEventListener('change', () => npv2SetSpeed(Number(spd.value)));
        }
        // Visual option controls
        const vizq = document.getElementById('np-v2-viz-quality');
        if (vizq) {
            vizq.value = NPV2.vizQuality;
            vizq.addEventListener('change', () => npv2SetVizQuality(vizq.value));
        }
        const vize = document.getElementById('np-v2-viz-energy');
        if (vize) {
            vize.value = String(NPV2.vizEnergy);
            vize.addEventListener('input', () => npv2SetVizEnergy(vize.value));
        }
        const vizp = document.getElementById('np-v2-viz-palette');
        if (vizp) {
            vizp.value = NPV2.vizPalette;
            vizp.addEventListener('change', () => npv2SetVizPalette(vizp.value));
        }
        const vizd = document.getElementById('np-v2-viz-dim');
        if (vizd) {
            vizd.value = String(NPV2.vizDim);
            vizd.addEventListener('input', () => npv2SetVizDim(vizd.value));
        }
        const vizac = document.getElementById('np-v2-viz-autocycle');
        if (vizac) {
            vizac.value = NPV2.vizAutocycle;
            vizac.addEventListener('change', () => npv2SetVizAutocycle(vizac.value));
        }
    } catch (e) {}
}

function npv2HandleKeys(event) {
    // v2 shortcuts only live while the theater is open (v1 owns the global
    // space/arrows/m/n/p set). '?' overlay gets Esc-first-close via capture.
    if (event.type === 'keydown' && event.key === 'Escape') {
        try {
            // In browser fullscreen the browser owns Esc: let it exit
            // fullscreen natively and don't swallow the key.
            if (document.fullscreenElement) return;
            const o = document.getElementById('np-v2-shorts-overlay');
            if (o && !o.classList.contains('hidden') && npv2ModalOpen()) {
                event.stopPropagation();
                event.preventDefault();
                npv2ToggleShortcuts(false);
                return;
            }
            // Esc backs out of full visuals mode before anything else.
            if (npv2VisualsModeOn()) {
                event.stopPropagation();
                event.preventDefault();
                // Close the side panel first, then the drawer, then the mode.
                if (npv2SidePanelOpen()) {
                    npv2CloseVisualsPanel();
                    return;
                }
                const d = document.getElementById('np-v2-visuals-drawer');
                if (d && !d.classList.contains('hidden')) {
                    npv2ToggleVisualsDrawer(false);
                    return;
                }
                npv2SetVisualsMode(false);
                return;
            }
        } catch (e) {}
        return;
    }
    if (!npv2ModalOpen()) return;
    const tag = (document.activeElement && document.activeElement.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || (document.activeElement && document.activeElement.isContentEditable)) return;
    switch (event.key) {
        case 'l':
        case 'L':
            event.preventDefault();
            npv2AbToggle();
            break;
        case '?':
            event.preventDefault();
            npv2ToggleShortcuts();
            break;
        case '[':
            event.preventDefault();
            npv2CycleSpeed(-1);
            break;
        case ']':
            event.preventDefault();
            npv2CycleSpeed(1);
            break;
        case 'v':
        case 'V':
            event.preventDefault();
            if (event.shiftKey) npv2PrevTheme(); else npv2CycleTheme();
            break;
        case 'f':
        case 'F':
            event.preventDefault();
            npv2SetVisualsMode(!npv2VisualsModeOn());
            break;
        default:
            return;
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function npv2Init() {
    try {
        if (NPV2.initialized) return;
        NPV2.initialized = true;
        // Load persisted state
        const theme = npv2GetG('theme', 'aurora');
        NPV2.theme = npv2ThemeIds().indexOf(theme) >= 0 ? theme : 'aurora';
        NPV2.bgOn = npv2GetG('bg', '1') !== '0';
        NPV2.speed = npv2ClampSpeed(parseFloat(npv2Get('speed', '1')));
        NPV2.wake = npv2Get('wake', '0') === '1';
        // Visual options (all profile-scoped via npv2GetG, like theme/bg)
        NPV2.vizQuality = npv2GetG('viz-quality', 'auto');
        NPV2.vizEnergy = npv2ClampVizEnergy(npv2GetG('viz-energy', '1'));
        NPV2.vizPalette = npv2GetG('viz-palette', 'auto');
        NPV2.vizDim = npv2ClampVizDim(npv2GetG('viz-dim', '0'));
        NPV2.vizAutocycle = npv2GetG('viz-autocycle', 'off');
        NPV2.reduceMotion = npv2GetG('reduce-motion', '0') === '1';
        npv2LoadEqSettings();

        npv2BuildThemePicker();
        npv2BuildEqUI();
        npv2RenderEqUI();
        npv2WireTransport();
        npv2SelectTab('queue');
        npv2SetBgOn(NPV2.bgOn);
        npv2SetVizQuality(NPV2.vizQuality);
        npv2SetVizEnergy(NPV2.vizEnergy);
        npv2SetVizPalette(NPV2.vizPalette);
        npv2SetVizDim(NPV2.vizDim);
        npv2SetVizAutocycle(NPV2.vizAutocycle);
        npv2SetReduceMotion(NPV2.reduceMotion);
        npv2SyncVisualBtn();
        npv2SyncEqBtn();
        npv2SetSpeed(NPV2.speed, { silent: true });
        try {
            const wt = document.getElementById('np-v2-wake-toggle');
            if (wt) wt.checked = NPV2.wake;
        } catch (e) {}

        npv2WatchTrackTitle();
        npv2WatchModal();
        npv2MiniInit();
        npv2WatchQueue();

        // Keyboard: capture phase so Esc can close the shortcuts overlay
        // before v1's bubble-phase handler closes the whole modal.
        document.addEventListener('keydown', npv2HandleKeys, true);
        // Fullscreen state sync: the browser owns Esc, so track it here.
        document.addEventListener('fullscreenchange', npv2SyncFullscreenBtn);

        // Media loop hooks (own listeners — v1's stay untouched)
        const el = npv2AudioEl();
        if (el) {
            el.addEventListener('timeupdate', npv2LoopTick);
            el.addEventListener('ratechange', () => {
                // Something else changed the rate (e.g. OS media keys);
                // adopt it rather than fight it.
                try {
                    if (isFinite(el.playbackRate) && el.playbackRate !== NPV2.speed) {
                        NPV2.speed = npv2ClampSpeed(el.playbackRate);
                        npv2Set('speed', String(NPV2.speed));
                        npv2SetSpeed(NPV2.speed, { silent: true });
                    }
                } catch (e) {}
            });
        }

        // The Web Audio graph only exists after v1 builds it on first play;
        // keep trying (cheaply) until the EQ chain is attached. Only needed
        // when EQ/normalize was persisted on; the toggle handlers attach
        // directly when the user switches it on later.
        let eqPollTries = 0;
        const eqPoll = setInterval(() => {
            try {
                eqPollTries++;
                if (NPV2.eq.attached || (!NPV2.eq.on && !NPV2.eq.normalize) || eqPollTries > 60) {
                    clearInterval(eqPoll);
                    return;
                }
                if (npv2AttachAudioGraph()) clearInterval(eqPoll);
            } catch (e) {}
        }, 2000);

        // If the modal is already open (init raced it), start the visuals.
        if (npv2ModalOpen()) npv2VizStart();

        window.npV2 = {
            version: 2,
            setTheme: npv2SetTheme,
            cycleTheme: npv2CycleTheme,
            prevTheme: npv2PrevTheme,
            setSpeed: npv2SetSpeed,
            setEqPreset: npv2SetEqPreset,
            selectTab: npv2SelectTab,
            attachAudioGraph: npv2AttachAudioGraph,
            themeIds: npv2ThemeIds,
            setVisualsMode: npv2SetVisualsMode,
        setFullscreen: npv2SetFullscreen,
        setVizQuality: npv2SetVizQuality,
        setVizEnergy: npv2SetVizEnergy,
        setVizPalette: npv2SetVizPalette,
        setVizDim: npv2SetVizDim,
        setVizAutocycle: npv2SetVizAutocycle,
        setReduceMotion: npv2SetReduceMotion,
            visualsModeOn: npv2VisualsModeOn,
        };
    } catch (e) {
        // The theater must never break the underlying player.
        try { console.warn('player theater init failed:', e); } catch (e2) {}
    }
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', npv2Init);
    } else {
        npv2Init();
    }
}
