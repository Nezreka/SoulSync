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
//   freq: Uint8Array(32) 0..255 frequency magnitudes,
//   wave: Uint8Array(64) 0..255 time-domain samples,
//   energy: 0..1 overall level, t: seconds, idle: bool (no real audio),
//   pal: {r,g,b} album-art palette, css: 'r,g,b' string }
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
    // Average a slice of the 32 analyser bins into `count` segments.
    const n = S.freq.length;
    const per = Math.max(1, Math.floor(n / count));
    let sum = 0;
    const start = Math.min(n - 1, i * per);
    for (let b = 0; b < per && start + b < n; b++) sum += S.freq[start + b];
    return (sum / per) / 255;
}

function npv2Css(S, alpha) {
    return 'rgba(' + S.pal.r + ',' + S.pal.g + ',' + S.pal.b + ',' + alpha + ')';
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
            const v = npv2Bin(S, i, N);
            st.peaks[i] = Math.max(v, st.peaks[i] - 0.008);
            const bh = Math.max(2, v * maxH);
            const x = i * bw + bw * 0.18;
            const g = ctx.createLinearGradient(0, cy - bh, 0, cy + bh);
            g.addColorStop(0, npv2Css2(S, 0.95));
            g.addColorStop(0.45, npv2Css(S, 0.85));
            g.addColorStop(1, npv2Css(S, 0.10));
            ctx.fillStyle = g;
            ctx.fillRect(x, cy - bh, bw * 0.64, bh * 2);
            // falling peak cap, pure WMP — flashes on the beat
            const py = st.peaks[i] * maxH;
            ctx.fillStyle = 'rgba(255,255,255,' + (0.22 + st.peaks[i] * 0.45 + beat * 0.3).toFixed(3) + ')';
            ctx.fillRect(x, cy - py - 2, bw * 0.64, 2);
            ctx.fillRect(x, cy + py, bw * 0.64, 2);
        }
        ctx.fillStyle = 'rgba(255,255,255,' + (0.10 + beat * 0.15).toFixed(3) + ')';
        ctx.fillRect(0, cy - 0.5, w, 1);
    },

    // --- WMP Scope: phosphor waveform ---------------------------------------
    scope(ctx, w, h, S) {
        const cy = h * 0.52;
        const beat = npv2BeatAmp(S);
        ctx.fillStyle = 'rgba(2,6,4,0.55)';
        ctx.fillRect(0, 0, w, h);
        ctx.strokeStyle = 'rgba(255,255,255,0.05)';
        ctx.lineWidth = 1;
        for (let gy = 0.2; gy < 1; gy += 0.2) {
            ctx.beginPath(); ctx.moveTo(0, h * gy); ctx.lineTo(w, h * gy); ctx.stroke();
        }
        ctx.beginPath(); ctx.moveTo(w / 2, 0); ctx.lineTo(w / 2, h); ctx.stroke();
        const n = S.wave.length;
        const trace = (ampScale, alpha, width, glow) => {
            ctx.beginPath();
            for (let i = 0; i < n; i++) {
                const x = (i / (n - 1)) * w;
                const a = S.wave[Math.max(0, i - 1)] / 255 - 0.5;
                const b = S.wave[i] / 255 - 0.5;
                const c = S.wave[Math.min(n - 1, i + 1)] / 255 - 0.5;
                const y = cy - ((a + b * 2 + c) / 4) * 2 * h * 0.30 * ampScale * (0.35 + S.energy * 1.3);
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
        trace(1, 0.14, 7, 0);                    // soft phosphor halo
        trace(1, 0.85, 2, 16);                   // hot core with bloom
        trace(1.35, 0.20, 1, 0);                 // faint overtone shimmer
        // beat tick: a bright dot riding the trace start
        if (beat > 0.05) {
            ctx.fillStyle = 'rgba(255,255,255,' + (beat * 0.8).toFixed(3) + ')';
            ctx.beginPath(); ctx.arc(6, cy, 2 + beat * 3, 0, 6.2832); ctx.fill();
        }
    },

    // --- WMP Waves: layered translucent ribbons ------------------------------
    waves(ctx, w, h, S) {
        const beat = npv2BeatAmp(S);
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
        const st = npv2ThemeState('plasma', () => ({ seeds: [0.7, 1.9, 3.1, 4.4, 5.6, 0.3, 2.5, 5.1] }));
        const beat = npv2BeatAmp(S);
        ctx.fillStyle = '#04040c';
        ctx.fillRect(0, 0, w, h);
        ctx.globalCompositeOperation = 'lighter';
        const R = Math.max(w, h) * 0.30;
        ctx.save();
        ctx.translate(w / 2, h / 2);
        ctx.rotate(Math.sin(S.t * 0.05) * 0.35);
        ctx.translate(-w / 2, -h / 2);
        st.seeds.forEach((sd, i) => {
            const accent = i % 3 === 2;
            const sp = 0.22 + i * 0.06;
            const x = w / 2 + Math.cos(S.t * sp + sd * 2.1) * w * 0.30 * (0.5 + S.energy * 0.7);
            const y = h / 2 + Math.sin(S.t * sp * 1.3 + sd * 3.7) * h * 0.28;
            const r = R * (0.55 + 0.45 * npv2Bin(S, i * 5, 32) + S.energy * 0.25) * (1 + beat * 0.35);
            const col = accent ? npv2Css2(S, 0.30) : npv2Css(S, 0.34);
            const clear = accent ? npv2Css2(S, 0) : npv2Css(S, 0);
            // soft halo
            const g = ctx.createRadialGradient(x, y, 0, x, y, r);
            g.addColorStop(0, col);
            g.addColorStop(1, clear);
            ctx.fillStyle = g;
            ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fill();
            // hot core: tight, brighter — this is what reads as plasma
            const core = accent ? npv2Css2(S, 0.55 + beat * 0.3) : npv2Css(S, 0.55 + beat * 0.3);
            const coreClear = accent ? npv2Css2(S, 0) : npv2Css(S, 0);
            const cg = ctx.createRadialGradient(x, y, 0, x, y, r * 0.38);
            cg.addColorStop(0, core);
            cg.addColorStop(1, coreClear);
            ctx.fillStyle = cg;
            ctx.beginPath(); ctx.arc(x, y, r * 0.38, 0, 6.2832); ctx.fill();
            st['p' + i] = { x, y };
        });
        // electric filaments between near neighbors
        ctx.lineWidth = 1.2;
        for (let i = 0; i < st.seeds.length; i++) {
            for (let j = i + 1; j < st.seeds.length; j++) {
                const a = st['p' + i], b = st['p' + j];
                if (!a || !b) continue;
                const d = Math.hypot(a.x - b.x, a.y - b.y);
                if (d > R * 2.2) continue;
                const al = (1 - d / (R * 2.2)) * (0.10 + beat * 0.22);
                ctx.strokeStyle = (i + j) % 3 === 0 ? npv2Css2(S, al.toFixed(3)) : npv2Css(S, al.toFixed(3));
                ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
            }
        }
        ctx.restore();
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
        const base = Math.min(w, h) * 0.10;
        const maxL = Math.min(w, h) * 0.36;
        const beat = npv2BeatAmp(S);
        ctx.fillStyle = 'rgba(3,3,8,0.6)';
        ctx.fillRect(0, 0, w, h);
        // faint outer guide ring
        ctx.strokeStyle = npv2Css(S, 0.12);
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(cx, cy, base + maxL, 0, 6.2832); ctx.stroke();
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(S.t * 0.10 + beat * 0.15);
        for (let i = 0; i < N; i++) {
            const v = npv2Bin(S, i, N);
            const len = base + v * maxL * (0.4 + S.energy);
            const a = (i / N) * 6.2832;
            const x2 = Math.cos(a) * len, y2 = Math.sin(a) * len;
            const x1 = Math.cos(a) * base, y1 = Math.sin(a) * base;
            const hot = v > 0.75;
            ctx.strokeStyle = hot ? npv2Css2(S, 0.9) : npv2Css(S, 0.18 + v * 0.6);
            ctx.lineWidth = hot ? 3 : 2;
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
            if (v > 0.55) {
                ctx.fillStyle = 'rgba(255,255,255,' + (v * 0.5).toFixed(3) + ')';
                ctx.beginPath(); ctx.arc(x2, y2, 1.6, 0, 6.2832); ctx.fill();
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
            return { stars, shoot: 0, shootX: 0, shootY: 0, nextShoot: 4 };
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
        st.nextShoot -= 0.016;
        if (st.nextShoot <= 0 && st.shoot <= 0) {
            st.shoot = 1; st.shootX = Math.random() * w * 0.7 + w * 0.15; st.shootY = Math.random() * h * 0.25;
            st.nextShoot = 5 + Math.random() * 8;
        }
        if (st.shoot > 0) {
            st.shoot -= 0.03;
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
            grad.addColorStop(0, npv2PalA(pal, 0));
            grad.addColorStop(0.5, npv2PalA(pal, aMid));
            grad.addColorStop(1, npv2PalA(pal, 0));
            ctx.fillStyle = grad;
            ctx.beginPath();
            const steps = 70;
            for (let i = 0; i <= steps; i++) {
                const x = (i / steps) * w;
                const y = h * R.y + Math.sin(i * 0.11 + S.t * R.speed * 2 + ri * 2.1) * h * R.amp * (0.6 + S.energy * 0.8)
                    + Math.sin(i * 0.031 - S.t * R.speed) * h * R.amp * 0.5;
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            ctx.lineTo(w, h * (R.y + R.amp * 3));
            ctx.lineTo(0, h * (R.y + R.amp * 3));
            ctx.closePath(); ctx.fill();
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
                ps.push({
                    x: Math.random(), y: Math.random(),
                    vx: (Math.random() - 0.5) * 0.0006, vy: (Math.random() - 0.5) * 0.0006,
                    r: Math.random() * 1.8 + 0.4, p: Math.random() * 6.28,
                    depth: 0.3 + Math.random() * 0.7,
                });
            }
            return { ps, rings: [] };
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
        if (beat > 0.85 && st.rings.length < 3) {
            st.rings.push({ r: 20, x: 0.3 + Math.random() * 0.4, y: 0.3 + Math.random() * 0.4 });
        }
        for (let i = st.rings.length - 1; i >= 0; i--) {
            const rg = st.rings[i];
            rg.r += 9 * q;
            const a = Math.max(0, 0.5 - rg.r / (Math.max(w, h) * 0.6));
            if (a <= 0) { st.rings.splice(i, 1); continue; }
            ctx.strokeStyle = npv2Css(S, a.toFixed(3));
            ctx.lineWidth = 1.5;
            ctx.beginPath(); ctx.arc(rg.x * w, rg.y * h, rg.r, 0, 6.2832); ctx.stroke();
        }
        for (const p of st.ps) {
            const sp = (1 + S.energy * 3 + beat * 5) * p.depth;
            p.x = (p.x + p.vx * sp + 1) % 1;
            p.y = (p.y + p.vy * sp + 1) % 1;
            const tw = 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(S.t * 2 * p.depth + p.p));
            const a = ((0.45 + S.energy * 0.55) * tw + beat * 0.35 * p.depth) * (0.4 + p.depth * 0.6);
            const rr = p.r * (1 + S.energy * 1.2) * p.depth;
            ctx.fillStyle = 'rgba(255,255,255,' + Math.min(1, a).toFixed(3) + ')';
            ctx.beginPath(); ctx.arc(p.x * w, p.y * h, rr, 0, 6.2832); ctx.fill();
            // the brightest few get a cross sparkle
            if (p.r > 1.9 && tw > 0.85) {
                ctx.strokeStyle = 'rgba(255,255,255,' + (a * 0.5).toFixed(3) + ')';
                ctx.lineWidth = 1;
                const L = rr * 4;
                ctx.beginPath();
                ctx.moveTo(p.x * w - L, p.y * h); ctx.lineTo(p.x * w + L, p.y * h);
                ctx.moveTo(p.x * w, p.y * h - L); ctx.lineTo(p.x * w, p.y * h + L);
                ctx.stroke();
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
        if (!S.idle) st.rot += 0.008 + S.energy * 0.014;
        else st.rot += 0.002;
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
        // energy highlight arc sweeping the grooves
        const hl = st.rot % 6.2832;
        ctx.strokeStyle = npv2Css(S, (0.25 + S.energy * 0.4).toFixed(3));
        ctx.lineWidth = 3;
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
        // label: palette disc, dark ring, initial
        ctx.fillStyle = npv2Css(S, 0.92);
        ctx.beginPath(); ctx.arc(cx, cy, R * 0.32, 0, 6.2832); ctx.fill();
        ctx.strokeStyle = 'rgba(0,0,0,0.35)';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(cx, cy, R * 0.32, 0, 6.2832); ctx.stroke();
        ctx.fillStyle = 'rgba(0,0,0,0.78)';
        ctx.font = '700 ' + Math.round(R * 0.22) + 'px system-ui, sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        const initial = (S.title || '♪').trim().charAt(0).toUpperCase() || '♪';
        ctx.fillText(initial, cx, cy + 1);
        // spindle
        ctx.fillStyle = '#d8d8d8';
        ctx.beginPath(); ctx.arc(cx, cy, R * 0.035, 0, 6.2832); ctx.fill();
        // tonearm
        const ax = cx + R * 1.55, ay = cy - R * 1.35;
        ctx.strokeStyle = 'rgba(0,0,0,0.4)';
        ctx.lineWidth = Math.max(4, R * 0.045);
        ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(ax + 3, ay + 4);
        ctx.lineTo(cx + R * 0.42 + 3, cy - R * 0.30 + 4); ctx.stroke();
        ctx.strokeStyle = 'rgba(220,220,220,0.8)';
        ctx.lineWidth = Math.max(3, R * 0.03);
        ctx.beginPath(); ctx.moveTo(ax, ay);
        ctx.lineTo(cx + R * 0.42, cy - R * 0.30); ctx.stroke();
        ctx.fillStyle = 'rgba(220,220,220,0.9)';
        ctx.beginPath(); ctx.arc(ax, ay, R * 0.07, 0, 6.2832); ctx.fill();
        ctx.fillStyle = 'rgba(120,120,120,0.9)';
        ctx.beginPath(); ctx.arc(ax, ay, R * 0.028, 0, 6.2832); ctx.fill();
    },

    // --- Tunnel: spectrum wormhole ----------------------------------------------
    tunnel(ctx, w, h, S) {
        const cx = w / 2, cy = h / 2;
        const st = npv2ThemeState('tunnel', () => ({ z: 0 }));
        const beat = npv2BeatAmp(S);
        st.z = (st.z + 0.012 + S.energy * 0.03 + beat * 0.05) % 1;
        ctx.fillStyle = '#020208';
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
                const v = npv2Bin(S, sgi, SEGS);
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
    },

    // --- Off ----------------------------------------------------------------------
    none() { /* the CSS ambient glow carries the vibe */ },

    // --- WMP Battery: sparks rise, beat kicks burst them -------------------------
    battery(ctx, w, h, S) {
        const q = npv2Q(S);
        const st = npv2ThemeState('battery', () => ({ parts: [], arcs: [], seed: Math.random() * 100 }));
        const beat = npv2BeatAmp(S);
        const cx = w / 2, horizon = h * 0.78;
        // full clear: no ghost bleed from the previous theme
        ctx.fillStyle = '#04060e';
        ctx.fillRect(0, 0, w, h);
        // faint depth haze above the horizon
        const haze = ctx.createLinearGradient(0, horizon - h * 0.25, 0, horizon);
        haze.addColorStop(0, npv2Css(S, 0));
        haze.addColorStop(1, npv2Css(S, (0.10 + S.energy * 0.12).toFixed(3)));
        ctx.fillStyle = haze;
        ctx.fillRect(0, horizon - h * 0.25, w, h * 0.25);
        // rising sparks, two depth layers
        const spawn = Math.round((2 + Math.floor(S.energy * 7) + (beat > 0.7 ? 30 : 0)) * q);
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
            p.x += p.vx + Math.sin(S.t * 2.6 + p.ph + p.y * 0.008) * 0.7;
            p.y += p.vy * 0.016;
            p.life -= p.decay * (1 + S.energy * 2);
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
            // hot core dot on near sparks
            if (!p.far && p.life > 0.5 && r > 1.6) {
                ctx.fillStyle = 'rgba(255,255,255,' + (a * 0.8).toFixed(3) + ')';
                ctx.beginPath();
                ctx.arc(p.x, p.y, r * 0.35, 0, 6.2832);
                ctx.fill();
            }
        }
        // electric arcs on hard beats: jagged discharge rising from the horizon
        if (beat > 0.72 && st.arcs.length < 4) {
            const segs = 7 + Math.floor(Math.random() * 5);
            const pts = [{ x: cx + (Math.random() - 0.5) * w * 0.5, y: horizon }];
            for (let s2 = 1; s2 <= segs; s2++) {
                const prev = pts[s2 - 1];
                pts.push({
                    x: prev.x + (Math.random() - 0.5) * w * 0.035,
                    y: horizon - (h * 0.34 * s2) / segs,
                });
            }
            st.arcs.push({ pts: pts, life: 1 });
        }
        ctx.lineWidth = Math.max(1, w * 0.0016);
        for (let i = st.arcs.length - 1; i >= 0; i--) {
            const arc = st.arcs[i];
            arc.life -= 0.09;
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
    dotplane(ctx, w, h, S) {
        const cols = 28, rows = 16;
        const cw = w / cols, rh = h / rows;
        const maxR = Math.min(cw, rh) * 0.42;
        const beat = npv2BeatAmp(S);
        ctx.fillStyle = '#040409';
        ctx.fillRect(0, 0, w, h);
        const bcx = w / 2, bcy = h / 2;
        const maxD = Math.hypot(bcx, bcy);
        for (let gy = 0; gy < rows; gy++) {
            for (let gx = 0; gx < cols; gx++) {
                const v = npv2Bin(S, gx, cols);
                const x = gx * cw + cw / 2, y = gy * rh + rh / 2;
                // beat wavefront radiating from the center
                const d = Math.hypot(x - bcx, y - bcy) / maxD;
                const wave = 0.5 + 0.5 * Math.sin(S.t * 1.7 + gx * 0.35 + gy * 0.5 - d * 6 + beat * 4);
                const r = Math.max(0.6, (v * 0.75 + wave * 0.25) * maxR * 1.15);
                const a = 0.10 + v * 0.75;
                const hot = v > 0.8;
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
        ctx.globalCompositeOperation = 'lighter';
        for (let i = 0; i < blobs; i++) {
            const accent = i % 3 === 2;
            const v = npv2Bin(S, i, blobs);
            const px = st.seed + i * 13.7;
            const x = w * (0.5 + 0.38 * Math.sin(S.t * 0.21 + px) * Math.sin(S.t * 0.13 + px * 2));
            const y = h * (0.5 + 0.36 * Math.cos(S.t * 0.17 + px * 1.3));
            const r = Math.min(w, h) * (0.10 + v * 0.22 + S.energy * 0.06) * (1 + beat * 0.3);
            const pal = accent ? npv2Pal2(S) : (S && S.pal);
            const g = ctx.createRadialGradient(x, y, 0, x, y, r);
            g.addColorStop(0, npv2PalA(pal, 0.5));
            g.addColorStop(0.6, npv2PalA(pal, 0.2));
            g.addColorStop(1, npv2PalA(pal, 0));
            ctx.fillStyle = g;
            ctx.beginPath();
            ctx.arc(x, y, r, 0, 6.2832);
            ctx.fill();
        }
        ctx.globalCompositeOperation = 'source-over';
    },

    // --- Particle fountain: beat-fed spray from the bottom ------------------------
    fountain(ctx, w, h, S) {
        const q = npv2Q(S);
        const st = npv2ThemeState('fountain', () => ({ parts: [] }));
        const beat = npv2BeatAmp(S);
        const bass = npv2Bin(S, 0, 8) * 0.6 + npv2Bin(S, 1, 8) * 0.4;
        const cx = w / 2;
        const spawn = Math.round((2 + Math.floor(bass * 10) + (beat > 0.6 ? 26 : 0)) * q);
        for (let i = 0; i < spawn && st.parts.length < Math.round(700 * q); i++) {
            const ang = -Math.PI / 2 + (Math.random() - 0.5) * 0.9;
            const sp = h * (0.35 + Math.random() * 0.55) * (0.5 + bass) * (1 + beat * 0.6);
            st.parts.push({
                x: cx + (Math.random() - 0.5) * w * 0.04,
                y: h * 0.98,
                px: 0, py: 0,
                vx: Math.cos(ang) * sp,
                vy: Math.sin(ang) * sp,
                life: 1,
                decay: 0.008 + Math.random() * 0.012,
                sz: 1 + Math.random() * 2.2,
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
            p.vy += h * 0.9 * 0.016; // gravity
            p.x += p.vx * 0.016;
            p.y += p.vy * 0.016;
            p.life -= p.decay;
            if (p.life <= 0 || p.y > h + 10) { st.parts.splice(i, 1); continue; }
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
        const R = Math.min(w, h) * 0.48;
        const segs = 16, N = 20;
        const beat = npv2BeatAmp(S);
        const st = npv2ThemeState('kaleido', () => ({ dir: 1 }));
        if (beat > 0.9) st.dir *= -1; // flip spin on hard beats
        const rot = S.t * 0.12 * st.dir;
        const pulse = 1 + beat * 0.12;
        ctx.fillStyle = 'rgba(2,3,9,0.5)';
        ctx.fillRect(0, 0, w, h);
        for (let sgm = 0; sgm < segs; sgm++) {
            const mirror = sgm % 2 === 1;
            const a0 = rot + (sgm / segs) * 6.2832;
            const a1 = rot + ((sgm + 1) / segs) * 6.2832;
            for (let i = 0; i < N; i++) {
                const src = mirror ? N - 1 - i : i;
                const v = npv2Bin(S, src, N);
                const r0 = (i / N) * R * pulse;
                const r1 = ((i + 1) / N) * R * pulse;
                ctx.beginPath();
                ctx.arc(cx, cy, r1, a0, a1);
                ctx.arc(cx, cy, Math.max(r0, 1), a1, a0, true);
                ctx.closePath();
                const accent = (sgm + i) % 5 === 0;
                ctx.fillStyle = accent ? npv2Css2(S, (0.05 + v * 0.55).toFixed(3)) : npv2Css(S, (0.05 + v * 0.6).toFixed(3));
                ctx.fill();
            }
        }
        // beat flash wash
        if (beat > 0.05) {
            ctx.fillStyle = npv2Css(S, (beat * 0.10).toFixed(3));
            ctx.beginPath(); ctx.arc(cx, cy, R * pulse, 0, 6.2832); ctx.fill();
        }
        // breathing core with orbit rings
        const bass = npv2Bin(S, 0, 8);
        ctx.fillStyle = npv2Css(S, (0.5 + bass * 0.4).toFixed(3));
        ctx.beginPath();
        ctx.arc(cx, cy, (3 + bass * 10) * pulse, 0, 6.2832);
        ctx.fill();
        ctx.strokeStyle = npv2Css2(S, 0.4);
        ctx.lineWidth = 1.5;
        for (let k = 1; k <= 2; k++) {
            ctx.beginPath();
            ctx.arc(cx, cy, (14 + bass * 22) * k * pulse + Math.sin(S.t * 2 + k) * 4, 0, 6.2832);
            ctx.stroke();
        }
    },

    // --- Warp: starfield rushing past, speed tied to energy -------------------------
    warp(ctx, w, h, S) {
        const st = npv2ThemeState('warp', () => ({
            stars: Array.from({ length: 240 }, () => ({
                x: Math.random() * 2 - 1, y: Math.random() * 2 - 1, z: Math.random(),
            })),
        }));
        const cx = w / 2, cy = h / 2;
        const beat = npv2BeatAmp(S);
        const speed = 0.008 + S.energy * 0.05 + beat * 0.03 + (S.idle ? 0.004 : 0);
        ctx.fillStyle = 'rgba(2,3,9,0.42)';
        ctx.fillRect(0, 0, w, h);
        // palette nebula wash behind the streaks
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.min(w, h) * 0.5);
        g.addColorStop(0, npv2Css(S, 0.20 + beat * 0.15));
        g.addColorStop(1, npv2Css(S, 0));
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, w, h);
        for (const s of st.stars) {
            s.z -= speed * (0.4 + s.z);
            if (s.z <= 0.02) { s.x = Math.random() * 2 - 1; s.y = Math.random() * 2 - 1; s.z = 1; }
            const sx = cx + (s.x / s.z) * cx;
            const sy = cy + (s.y / s.z) * cy;
            const px = cx + (s.x / (s.z + speed * 3)) * cx;
            const py = cy + (s.y / (s.z + speed * 3)) * cy;
            const bright = 1 - s.z;
            const near = bright > 0.75;
            ctx.strokeStyle = near
                ? npv2Css2(S, (bright * 0.9).toFixed(3))
                : 'rgba(255,255,255,' + (bright * 0.85).toFixed(3) + ')';
            ctx.lineWidth = Math.max(1, bright * 2.4);
            ctx.beginPath();
            ctx.moveTo(px, py);
            ctx.lineTo(sx, sy);
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
            { n: 5, sp: 0.05, al: 0.30, sz: 0.42, accent: false },
            { n: 4, sp: 0.07, al: 0.22, sz: 0.34, accent: true },
            { n: 7, sp: 0.09, al: 0.20, sz: 0.30, accent: false },
            { n: 9, sp: 0.14, al: 0.14, sz: 0.20, accent: true },
        ];
        for (const L of layers) {
            for (let i = 0; i < L.n; i++) {
                const px = st.seed + i * 31.7 + L.sp * 57 + (L.accent ? 91 : 0);
                const x = w * (0.5 + 0.45 * Math.sin(S.t * L.sp + px));
                const y = h * (0.5 + 0.45 * Math.cos(S.t * L.sp * 0.8 + px * 1.7));
                const r = Math.min(w, h) * L.sz * (0.8 + 0.4 * Math.sin(S.t * 0.3 + px)) * (1 + beat * 0.2);
                const v = npv2Bin(S, i, L.n);
                const pal = L.accent ? npv2Pal2(S) : (S && S.pal);
                const aMid = (L.al * (0.5 + v)).toFixed(3);
                const g = ctx.createRadialGradient(x, y, 0, x, y, r);
                g.addColorStop(0, npv2PalA(pal, aMid));
                g.addColorStop(1, npv2PalA(pal, 0));
                ctx.fillStyle = g;
                ctx.beginPath();
                ctx.arc(x, y, r, 0, 6.2832);
                ctx.fill();
            }
        }
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
        st.spark -= 0.016;
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
        const st = npv2ThemeState('bloom', () => ({ rings: [], idleT: 0 }));
        const cx = w / 2, cy = h / 2;
        const beat = npv2BeatAmp(S);
        const bass = npv2Bin(S, 0, 10) * 0.7 + npv2Bin(S, 1, 10) * 0.3;
        if (beat > 0.75 && st.rings.length < Math.round(26 * q)) {
            // staggered echo: three rings per beat, petals offset
            for (let k = 0; k < 3; k++) {
                st.rings.push({ r: 8 - k * 7, w: 3 + bass * 5, rot: Math.random() * 6.28, petals: 10 + k * 4, accent: k === 1 });
            }
        }
        // idle breathing: a soft ring every few seconds even with no beats
        st.idleT += 0.016;
        if (st.idleT > 4) {
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
            const la = (0.16 - layer * 0.035 + bass * 0.12).toFixed(3);
            ctx.strokeStyle = layer === 1 ? npv2Css2(S, la) : npv2Css(S, la);
            ctx.lineWidth = 1.4;
            const seg = 6.2832 / petals;
            for (let p = 0; p < petals; p++) {
                // petal: arc bulging outward from the ring
                const a0 = rot + p * seg;
                const px = cx + Math.cos(a0) * lr, py = cy + Math.sin(a0) * lr;
                const pr = lr * 0.42;
                ctx.beginPath();
                ctx.arc(px, py, pr, a0 - 1.25, a0 + 1.25);
                ctx.stroke();
            }
        }
        for (let i = st.rings.length - 1; i >= 0; i--) {
            const rg = st.rings[i];
            rg.r += (4 + bass * 14) * 0.9 * (rg.gentle ? 0.6 : 1);
            rg.rot += 0.004 * (i % 2 ? 1 : -1);
            const a = Math.max(0, 1 - rg.r / maxR);
            if (a <= 0) { st.rings.splice(i, 1); continue; }
            const alpha = (a * (rg.gentle ? 0.35 : 0.7)).toFixed(3);
            ctx.strokeStyle = rg.accent ? npv2Css2(S, alpha) : npv2Css(S, alpha);
            ctx.lineWidth = rg.w * a + 0.6;
            // petal ring: arc segments with gaps, slowly counter-rotating
            const seg = 6.2832 / rg.petals;
            for (let p = 0; p < rg.petals; p++) {
                const a0 = rg.rot + p * seg;
                ctx.beginPath();
                ctx.arc(cx, cy, rg.r, a0, a0 + seg * 0.62);
                ctx.stroke();
            }
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
    freq: new Uint8Array(32),
    wave: new Uint8Array(64),
    energy: 0,
    idle: true,
    title: '',
    beat: 0,        // 0..1 beat envelope, set from the bass bins each frame
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

function npv2ReadAudio(S, t) {
    let idle = true;
    try {
        if (typeof npAnalyser !== 'undefined' && npAnalyser && npv2IsPlaying()) {
            npAnalyser.getByteFrequencyData(S.freq);
            npAnalyser.getByteTimeDomainData(S.wave);
            idle = false;
        }
    } catch (e) { /* analyser unavailable — idle synth below */ }
    if (idle) npv2IdleSynth(S, t);
    let sum = 0;
    for (let i = 1; i < S.freq.length; i++) sum += S.freq[i];
    S.energy = idle ? 0.22 : sum / (S.freq.length - 1) / 255;
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
    npv2ReadAudio(S, NPV2.vizT);
    // energy scaled by the user's visual energy; capped in reduce-motion
    S.energy = Math.min(1, S.energy * NPV2.vizEnergy);
    if (NPV2.reduceMotion) S.energy = Math.min(S.energy, 0.45);
    // beat envelope: a hard bass onset sets it to 1, it decays from there
    const bass = (S.freq[0] + S.freq[1] + S.freq[2]) / 3 / 255;
    if (!S.idle && bass > 0.55 && S.beat < 0.35) S.beat = 1;
    S.beat = Math.max(0, S.beat - dt * 2.4);
    S.t = NPV2.vizT;
    // palette: album art by default, or a curated override
    const pal = (NPV2.vizPalette !== 'auto' && NPV2_PALETTES[NPV2.vizPalette]) ? NPV2_PALETTES[NPV2.vizPalette] : NPV2.palette;
    S.pal = pal;
    S.pal2 = { r: pal.g, g: pal.b, b: pal.r };
    // automatic theme rotation
    const ac = npv2AutocycleSeconds(NPV2.vizAutocycle);
    if (ac > 0 && NPV2.vizT - NPV2.vizLastCycle >= ac) npv2CycleTheme(true);
    const paint = NPV2_PAINT[NPV2.theme];
    ctx.clearRect(0, 0, w, h);
    try { if (typeof paint === 'function') paint(ctx, w, h, S); } catch (e) { /* a bad frame is not a broken player */ }
    // wake-lock transition check, throttled to the frame loop
    npv2UpdateWakeLock();
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
        if (expand) expand.addEventListener('click', () => npv2SetVisualsMode(true));
        const fsb = document.getElementById('np-v2-fullscreen-btn');
        if (fsb) fsb.addEventListener('click', () => npv2SetFullscreen());
        // Immersive-mode drawer: panels without leaving the visuals
        const pv = document.getElementById('np-v2-visuals-panels');
        if (pv) pv.addEventListener('click', () => npv2ToggleVisualsDrawer());
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
