// ============================================================
// SoulSync — Page Particle System
// Single canvas, per-page particle behaviors
// ============================================================

(function () {
    'use strict';

    const canvas = document.getElementById('page-particles-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    let animFrame = null;
    let frameCount = 0;
    let extras = {}; // temp ref swapped during initExtras calls

    // Transition: converge → burst between presets

    // ── Helpers ──

    function getAccentRGB() {
        const s = getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb').trim();
        return s || '29, 185, 84';
    }

    // Shift an "r, g, b" accent string by a hue offset (degrees), cached per base color
    let _shiftCache = { base: '', shifts: {} };
    function shiftAccent(rgbStr, hueDeg) {
        if (hueDeg === 0) return rgbStr;
        // Check cache
        if (_shiftCache.base !== rgbStr) _shiftCache = { base: rgbStr, shifts: {} };
        const key = Math.round(hueDeg);
        if (key in _shiftCache.shifts) return _shiftCache.shifts[key];

        const [r, g, b] = rgbStr.split(',').map(s => parseInt(s.trim()));
        const rn = r / 255, gn = g / 255, bn = b / 255;
        const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn);
        let h = 0, s = 0;
        const l = (max + min) / 2;
        if (max !== min) {
            const d = max - min;
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            if (max === rn) h = ((gn - bn) / d + (gn < bn ? 6 : 0)) / 6;
            else if (max === gn) h = ((bn - rn) / d + 2) / 6;
            else h = ((rn - gn) / d + 4) / 6;
        }
        h = ((h * 360 + hueDeg) % 360 + 360) % 360;
        const c = (1 - Math.abs(2 * l - 1)) * s;
        const x = c * (1 - Math.abs((h / 60) % 2 - 1));
        const m = l - c / 2;
        let r1, g1, b1;
        if (h < 60) { r1 = c; g1 = x; b1 = 0; }
        else if (h < 120) { r1 = x; g1 = c; b1 = 0; }
        else if (h < 180) { r1 = 0; g1 = c; b1 = x; }
        else if (h < 240) { r1 = 0; g1 = x; b1 = c; }
        else if (h < 300) { r1 = x; g1 = 0; b1 = c; }
        else { r1 = c; g1 = 0; b1 = x; }
        const result = `${Math.round((r1 + m) * 255)}, ${Math.round((g1 + m) * 255)}, ${Math.round((b1 + m) * 255)}`;
        _shiftCache.shifts[key] = result;
        return result;
    }

    function resize() {
        const dpr = 1; // keep 1:1 for performance
        canvas.width = canvas.clientWidth * dpr;
        canvas.height = canvas.clientHeight * dpr;
    }

    window.addEventListener('resize', resize);
    resize();

    // ── Preset Definitions ──
    // Each preset: { count, init(p, i), update(p, time, i), draw(p, ctx, accent, time),
    //   optional: initExtras(), drawGlobal(ctx, particles, accent, time, extras) }

    const PRESETS = {

        // ── DASHBOARD — network nodes, connections, data packets, shooting stars ──
        dashboard: {
            count: 50,
            init(p, i) {
                p.x = Math.random() * canvas.width;
                p.y = Math.random() * canvas.height;
                p.vx = (Math.random() - 0.5) * 0.3;
                p.vy = (Math.random() - 0.5) * 0.3;
                p.phase = Math.random() * Math.PI * 2;
                // Hub nodes — ~10% are larger, brighter, attract more connections
                p.isHub = i < 5;
                p.radius = p.isHub ? (3 + Math.random() * 2) : (1.5 + Math.random() * 1.5);
                // Color offset — hue shift ±30° from accent for variety
                p.hueShift = (Math.random() - 0.5) * 60;
            },
            update(p) {
                p.x += p.vx;
                p.y += p.vy;
                if (p.x < 15 || p.x > canvas.width - 15) p.vx *= -1;
                if (p.y < 15 || p.y > canvas.height - 15) p.vy *= -1;
                p.x = Math.max(5, Math.min(canvas.width - 5, p.x));
                p.y = Math.max(5, Math.min(canvas.height - 5, p.y));
            },
            draw(p, ctx, accent, time) {
                const pulse = 0.5 + 0.5 * Math.sin(time * 1.5 + p.phase);
                const col = shiftAccent(accent, p.hueShift);

                // Glow — hubs get bigger, brighter glow
                const glowSize = p.isHub ? p.radius * 6 : p.radius * 4;
                const glowAlpha = p.isHub ? (0.18 + pulse * 0.12) : (0.10 + pulse * 0.07);
                const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glowSize);
                glow.addColorStop(0, `rgba(${col}, ${glowAlpha})`);
                glow.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(p.x, p.y, glowSize, 0, Math.PI * 2);
                ctx.fillStyle = glow;
                ctx.fill();

                // Core
                const coreAlpha = p.isHub ? (0.5 + pulse * 0.3) : (0.3 + pulse * 0.2);
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${col}, ${coreAlpha})`;
                ctx.fill();

                // Hub ring
                if (p.isHub) {
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, p.radius + 2 + pulse * 2, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(${col}, ${0.08 + pulse * 0.06})`;
                    ctx.lineWidth = 0.8;
                    ctx.stroke();
                }
            },
            initExtras() {
                extras.packets = [];
                extras.shootingStars = [];
                extras.activeLines = {}; // "i-j" → glow remaining frames
                extras.connectionDist = 180;
            },
            drawGlobal(ctx, parts, accent, time, layerExtras) {
                const ex = layerExtras || extras;
                if (!ex.packets) { ex.packets = []; ex.shootingStars = []; ex.activeLines = {}; ex.connectionDist = 180; }
                const dist = ex.connectionDist;

                // Decay active line glows
                for (const key in ex.activeLines) {
                    ex.activeLines[key] -= 0.02;
                    if (ex.activeLines[key] <= 0) delete ex.activeLines[key];
                }

                // Connections — hubs connect further, active lines glow brighter
                for (let i = 0; i < parts.length; i++) {
                    for (let j = i + 1; j < parts.length; j++) {
                        const maxDist = (parts[i].isHub || parts[j].isHub) ? dist * 1.3 : dist;
                        const dx = parts[j].x - parts[i].x;
                        const dy = parts[j].y - parts[i].y;
                        const d = Math.sqrt(dx * dx + dy * dy);
                        if (d < maxDist) {
                            const key = `${i}-${j}`;
                            const baseAlpha = (1 - d / maxDist) * 0.10;
                            const glowBoost = ex.activeLines[key] || 0;
                            const lineCol = shiftAccent(accent, (parts[i].hueShift + parts[j].hueShift) * 0.5);

                            ctx.beginPath();
                            ctx.moveTo(parts[i].x, parts[i].y);
                            ctx.lineTo(parts[j].x, parts[j].y);
                            ctx.strokeStyle = `rgba(${lineCol}, ${baseAlpha + glowBoost * 0.25})`;
                            ctx.lineWidth = glowBoost > 0 ? 1.2 : 0.7;
                            ctx.stroke();
                        }
                    }
                }

                // Spawn packets — prefer hub connections
                if (frameCount % 60 === 0 && parts.length >= 2) {
                    // Pick a hub if possible, otherwise random
                    const hubs = parts.reduce((arr, p, i) => { if (p.isHub) arr.push(i); return arr; }, []);
                    const a = hubs.length > 0 && Math.random() < 0.6
                        ? hubs[Math.floor(Math.random() * hubs.length)]
                        : Math.floor(Math.random() * parts.length);
                    let b = a, best = dist * 1.5;
                    for (let i = 0; i < parts.length; i++) {
                        if (i === a) continue;
                        const dx = parts[i].x - parts[a].x;
                        const dy = parts[i].y - parts[a].y;
                        const d = Math.sqrt(dx * dx + dy * dy);
                        if (d < best) { best = d; b = i; }
                    }
                    if (b !== a) {
                        ex.packets.push({ from: a, to: b, t: 0, trail: [], hue: parts[a].hueShift });
                        // Light up this connection
                        const key = a < b ? `${a}-${b}` : `${b}-${a}`;
                        ex.activeLines[key] = 1;
                    }
                }

                // Draw packets with comet trail
                for (let i = ex.packets.length - 1; i >= 0; i--) {
                    const pkt = ex.packets[i];
                    pkt.t += 0.02;
                    if (pkt.t >= 1) { ex.packets.splice(i, 1); continue; }
                    const fa = parts[pkt.from], ta = parts[pkt.to];
                    const px = fa.x + (ta.x - fa.x) * pkt.t;
                    const py = fa.y + (ta.y - fa.y) * pkt.t;

                    // Store trail points
                    pkt.trail.push({ x: px, y: py });
                    if (pkt.trail.length > 8) pkt.trail.shift();

                    const col = shiftAccent(accent, pkt.hue);

                    // Draw trail
                    for (let t = 0; t < pkt.trail.length - 1; t++) {
                        const alpha = (t / pkt.trail.length) * 0.3;
                        const width = (t / pkt.trail.length) * 2;
                        ctx.beginPath();
                        ctx.moveTo(pkt.trail[t].x, pkt.trail[t].y);
                        ctx.lineTo(pkt.trail[t + 1].x, pkt.trail[t + 1].y);
                        ctx.strokeStyle = `rgba(${col}, ${alpha})`;
                        ctx.lineWidth = width;
                        ctx.lineCap = 'round';
                        ctx.stroke();
                    }

                    // Head glow
                    const g = ctx.createRadialGradient(px, py, 0, px, py, 8);
                    g.addColorStop(0, `rgba(${col}, 0.6)`);
                    g.addColorStop(0.4, `rgba(${col}, 0.15)`);
                    g.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(px, py, 8, 0, Math.PI * 2);
                    ctx.fillStyle = g;
                    ctx.fill();

                    // Bright core
                    ctx.beginPath();
                    ctx.arc(px, py, 1.8, 0, Math.PI * 2);
                    ctx.fillStyle = 'rgba(255,255,255,0.9)';
                    ctx.fill();
                }

                // Shooting stars — spawn occasionally
                if (frameCount % 200 === 0 || (frameCount % 120 === 0 && Math.random() < 0.3)) {
                    const edge = Math.random();
                    let sx, sy, angle;
                    if (edge < 0.5) {
                        // From left/right
                        sx = edge < 0.25 ? -10 : canvas.width + 10;
                        sy = Math.random() * canvas.height * 0.6;
                        angle = edge < 0.25 ? (Math.random() * 0.4 + 0.1) : (Math.PI - Math.random() * 0.4 - 0.1);
                    } else {
                        // From top
                        sx = Math.random() * canvas.width;
                        sy = -10;
                        angle = Math.random() * 0.6 + 0.4 + (Math.random() < 0.5 ? 0 : Math.PI * 0.3);
                    }
                    const speed = 3 + Math.random() * 4;
                    ex.shootingStars.push({
                        x: sx, y: sy,
                        vx: Math.cos(angle) * speed,
                        vy: Math.sin(angle) * speed,
                        trail: [],
                        life: 1,
                        hue: (Math.random() - 0.5) * 80
                    });
                }

                // Draw shooting stars
                for (let i = ex.shootingStars.length - 1; i >= 0; i--) {
                    const star = ex.shootingStars[i];
                    star.x += star.vx;
                    star.y += star.vy;
                    star.life -= 0.012;
                    star.trail.push({ x: star.x, y: star.y });
                    if (star.trail.length > 20) star.trail.shift();

                    if (star.life <= 0 || star.x < -50 || star.x > canvas.width + 50 ||
                        star.y < -50 || star.y > canvas.height + 50) {
                        ex.shootingStars.splice(i, 1);
                        continue;
                    }

                    const col = shiftAccent(accent, star.hue);

                    // Trail
                    for (let t = 0; t < star.trail.length - 1; t++) {
                        const frac = t / star.trail.length;
                        const alpha = frac * 0.5 * star.life;
                        ctx.beginPath();
                        ctx.moveTo(star.trail[t].x, star.trail[t].y);
                        ctx.lineTo(star.trail[t + 1].x, star.trail[t + 1].y);
                        ctx.strokeStyle = `rgba(${col}, ${alpha})`;
                        ctx.lineWidth = frac * 2.5;
                        ctx.lineCap = 'round';
                        ctx.stroke();
                    }

                    // Head
                    const sg = ctx.createRadialGradient(star.x, star.y, 0, star.x, star.y, 5);
                    sg.addColorStop(0, `rgba(255, 255, 255, ${0.8 * star.life})`);
                    sg.addColorStop(0.3, `rgba(${col}, ${0.5 * star.life})`);
                    sg.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(star.x, star.y, 5, 0, Math.PI * 2);
                    ctx.fillStyle = sg;
                    ctx.fill();
                }
            }
        },

        // ── SYNC — orbiting rings with sync arrows ──
        sync: {
            count: 40,
            init(p, i) {
                // Assign each particle to an orbital ring
                const ringCount = 4;
                p.ring = i % ringCount;
                const ringRadii = [0.08, 0.15, 0.23, 0.32]; // fraction of min(w,h)
                p.orbitRadius = ringRadii[p.ring];
                p.angle = (i / Math.ceil(40 / ringCount)) * Math.PI * 2 + Math.random() * 0.3;
                // Alternating directions per ring
                p.speed = (p.ring % 2 === 0 ? 1 : -1) * (0.004 + p.ring * 0.002);
                p.radius = 1.5 + Math.random() * 1.5;
                p.hueShift = (p.ring - 1.5) * 25; // spread hues across rings
                p.phase = Math.random() * Math.PI * 2;
                // Compute initial x/y
                const cx = canvas.width * 0.5, cy = canvas.height * 0.45;
                const r = p.orbitRadius * Math.min(canvas.width, canvas.height);
                p.x = cx + Math.cos(p.angle) * r;
                p.y = cy + Math.sin(p.angle) * r * 0.5; // elliptical
            },
            update(p) {
                p.angle += p.speed;
                const cx = canvas.width * 0.5, cy = canvas.height * 0.45;
                const r = p.orbitRadius * Math.min(canvas.width, canvas.height);
                p.x = cx + Math.cos(p.angle) * r;
                p.y = cy + Math.sin(p.angle) * r * 0.5;
            },
            draw(p, ctx, accent, time) {
                const pulse = 0.5 + 0.5 * Math.sin(time * 2 + p.phase);
                const col = shiftAccent(accent, p.hueShift);
                // Depth — particles "behind" center are dimmer
                const depth = Math.sin(p.angle);
                const depthAlpha = 0.3 + (depth * 0.5 + 0.5) * 0.5;

                const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.radius * 4);
                glow.addColorStop(0, `rgba(${col}, ${(0.12 + pulse * 0.08) * depthAlpha})`);
                glow.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius * 4, 0, Math.PI * 2);
                ctx.fillStyle = glow;
                ctx.fill();

                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius * (0.8 + depth * 0.2), 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${col}, ${(0.3 + pulse * 0.25) * depthAlpha})`;
                ctx.fill();
            },
            initExtras() {
                extras.syncArrowAngle = 0;
                extras.transfers = []; // data pulses jumping between rings
                extras.arcs = []; // energy arcs between particles
                extras.transferTimer = 0;
            },
            drawGlobal(ctx, parts, accent, time, layerExtras) {
                const ex = layerExtras || extras;
                if (ex.syncArrowAngle === undefined) {
                    ex.syncArrowAngle = 0; ex.transfers = []; ex.arcs = []; ex.transferTimer = 0;
                }
                const cx = canvas.width * 0.5, cy = canvas.height * 0.45;
                const minDim = Math.min(canvas.width, canvas.height);
                const ringRadii = [0.08, 0.15, 0.23, 0.32];

                // Draw faint orbital paths — pulsing
                for (let r = 0; r < ringRadii.length; r++) {
                    const rx = ringRadii[r] * minDim;
                    const ry = rx * 0.5;
                    const col = shiftAccent(accent, (r - 1.5) * 25);
                    const ringPulse = 0.03 + 0.025 * Math.sin(time * 1.2 + r * 1.5);
                    ctx.beginPath();
                    ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(${col}, ${ringPulse})`;
                    ctx.lineWidth = 0.8;
                    ctx.stroke();
                }

                // Spawn data transfers between rings
                ex.transferTimer++;
                if (ex.transferTimer >= 50 + Math.floor(Math.random() * 30)) {
                    ex.transferTimer = 0;
                    // Pick two particles on different rings
                    const ringA = Math.floor(Math.random() * 4);
                    let ringB = ringA;
                    while (ringB === ringA) ringB = Math.floor(Math.random() * 4);
                    const fromParts = parts.filter(p => p.ring === ringA);
                    const toParts = parts.filter(p => p.ring === ringB);
                    if (fromParts.length && toParts.length) {
                        const from = fromParts[Math.floor(Math.random() * fromParts.length)];
                        const to = toParts[Math.floor(Math.random() * toParts.length)];
                        ex.transfers.push({
                            fx: from.x, fy: from.y,
                            tx: to.x, ty: to.y,
                            fromRef: from, toRef: to,
                            t: 0,
                            hue: (from.hueShift + to.hueShift) * 0.5,
                            trail: []
                        });
                    }
                }

                // Draw transfers — arc through center
                for (let i = ex.transfers.length - 1; i >= 0; i--) {
                    const tr = ex.transfers[i];
                    tr.t += 0.025;
                    // Update endpoints to track moving particles
                    tr.fx = tr.fromRef.x; tr.fy = tr.fromRef.y;
                    tr.tx = tr.toRef.x; tr.ty = tr.toRef.y;

                    if (tr.t >= 1) { ex.transfers.splice(i, 1); continue; }

                    // Bezier through center
                    const t = tr.t;
                    const mt = 1 - t;
                    const px = mt * mt * tr.fx + 2 * mt * t * cx + t * t * tr.tx;
                    const py = mt * mt * tr.fy + 2 * mt * t * cy + t * t * tr.ty;

                    tr.trail.push({ x: px, y: py });
                    if (tr.trail.length > 8) tr.trail.shift();

                    const col = shiftAccent(accent, tr.hue);

                    // Trail
                    for (let s = 0; s < tr.trail.length - 1; s++) {
                        const frac = s / tr.trail.length;
                        ctx.beginPath();
                        ctx.moveTo(tr.trail[s].x, tr.trail[s].y);
                        ctx.lineTo(tr.trail[s + 1].x, tr.trail[s + 1].y);
                        ctx.strokeStyle = `rgba(${col}, ${frac * 0.3})`;
                        ctx.lineWidth = frac * 2;
                        ctx.lineCap = 'round';
                        ctx.stroke();
                    }

                    // Head
                    const hg = ctx.createRadialGradient(px, py, 0, px, py, 6);
                    hg.addColorStop(0, `rgba(255,255,255, 0.5)`);
                    hg.addColorStop(0.3, `rgba(${col}, 0.3)`);
                    hg.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(px, py, 6, 0, Math.PI * 2);
                    ctx.fillStyle = hg;
                    ctx.fill();
                }

                // Energy arcs — brief flickers between close particles on adjacent rings
                if (frameCount % 8 === 0) {
                    // Clean old arcs
                    for (let i = ex.arcs.length - 1; i >= 0; i--) {
                        ex.arcs[i].life -= 0.08;
                        if (ex.arcs[i].life <= 0) ex.arcs.splice(i, 1);
                    }
                    // Maybe spawn new arc
                    if (ex.arcs.length < 3 && Math.random() < 0.3) {
                        const a = Math.floor(Math.random() * parts.length);
                        let bestDist = 120, bestIdx = -1;
                        for (let j = 0; j < parts.length; j++) {
                            if (j === a || parts[j].ring === parts[a].ring) continue;
                            if (Math.abs(parts[j].ring - parts[a].ring) > 1) continue;
                            const dx = parts[j].x - parts[a].x, dy = parts[j].y - parts[a].y;
                            const d = Math.sqrt(dx * dx + dy * dy);
                            if (d < bestDist) { bestDist = d; bestIdx = j; }
                        }
                        if (bestIdx >= 0) {
                            ex.arcs.push({ a, b: bestIdx, life: 1 });
                        }
                    }
                }

                // Draw arcs
                for (const arc of ex.arcs) {
                    const pa = parts[arc.a], pb = parts[arc.b];
                    const col = shiftAccent(accent, (pa.hueShift + pb.hueShift) * 0.5);
                    ctx.beginPath();
                    ctx.moveTo(pa.x, pa.y);
                    const mx = (pa.x + pb.x) * 0.5 + (Math.random() - 0.5) * 6;
                    const my = (pa.y + pb.y) * 0.5 + (Math.random() - 0.5) * 6;
                    ctx.quadraticCurveTo(mx, my, pb.x, pb.y);
                    ctx.strokeStyle = `rgba(${col}, ${arc.life * 0.2})`;
                    ctx.lineWidth = arc.life * 1.5;
                    ctx.lineCap = 'round';
                    ctx.stroke();
                    // Bright core
                    ctx.strokeStyle = `rgba(255,255,255, ${arc.life * 0.08})`;
                    ctx.lineWidth = arc.life * 0.5;
                    ctx.stroke();
                }

                // Center sync arrows — two curved arrows rotating
                ex.syncArrowAngle += 0.008;
                const arrowR = minDim * 0.035;
                ctx.save();
                ctx.translate(cx, cy);
                ctx.rotate(ex.syncArrowAngle);
                for (let a = 0; a < 2; a++) {
                    ctx.save();
                    ctx.rotate(a * Math.PI);
                    ctx.beginPath();
                    ctx.arc(0, 0, arrowR, -0.9, 0.9);
                    ctx.strokeStyle = `rgba(${accent}, 0.15)`;
                    ctx.lineWidth = 1.5;
                    ctx.lineCap = 'round';
                    ctx.stroke();
                    // Arrowhead
                    const tx = arrowR * Math.cos(0.9), ty = arrowR * Math.sin(0.9);
                    ctx.beginPath();
                    ctx.moveTo(tx - 4, ty - 3);
                    ctx.lineTo(tx, ty);
                    ctx.lineTo(tx + 1, ty - 5);
                    ctx.stroke();
                    ctx.restore();
                }
                ctx.restore();

                // Center glow — pulses with transfer activity
                const activity = Math.min(1, ex.transfers.length * 0.3);
                const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, minDim * 0.06);
                cg.addColorStop(0, `rgba(${accent}, ${0.08 + activity * 0.1})`);
                cg.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(cx, cy, minDim * 0.06, 0, Math.PI * 2);
                ctx.fillStyle = cg;
                ctx.fill();
            }
        },

        // ── SEARCH — radar/sonar pulse with scanning particles ──
        search: {
            count: 45,
            init(p, i) {
                // Scatter particles across canvas
                p.x = Math.random() * canvas.width;
                p.y = Math.random() * canvas.height;
                p.baseX = p.x;
                p.baseY = p.y;
                p.radius = 1 + Math.random() * 1.5;
                p.phase = Math.random() * Math.PI * 2;
                p.hueShift = (Math.random() - 0.5) * 50;
                // Slow drift
                p.vx = (Math.random() - 0.5) * 0.15;
                p.vy = (Math.random() - 0.5) * 0.15;
                // Lit state — brightens when radar sweeps over
                p.lit = 0;
                // Some particles are "result" nodes — slightly bigger, brighter
                p.isResult = i < 8;
                if (p.isResult) p.radius = 2 + Math.random() * 1.5;
            },
            update(p) {
                // Gentle drift
                p.x += p.vx;
                p.y += p.vy;
                // Soft bounds — bounce
                if (p.x < 10 || p.x > canvas.width - 10) p.vx *= -1;
                if (p.y < 10 || p.y > canvas.height - 10) p.vy *= -1;
                p.x = Math.max(5, Math.min(canvas.width - 5, p.x));
                p.y = Math.max(5, Math.min(canvas.height - 5, p.y));
                // Decay lit state
                if (p.lit > 0) p.lit = Math.max(0, p.lit - 0.015);
            },
            draw(p, ctx, accent, time) {
                const pulse = 0.5 + 0.5 * Math.sin(time * 1.2 + p.phase);
                const col = shiftAccent(accent, p.hueShift);
                const litBoost = p.lit;

                // Glow — bigger when lit
                const glowSize = p.radius * (3 + litBoost * 5);
                const glowAlpha = (p.isResult ? 0.08 : 0.05) + litBoost * 0.25;
                const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glowSize);
                glow.addColorStop(0, `rgba(${col}, ${glowAlpha})`);
                glow.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(p.x, p.y, glowSize, 0, Math.PI * 2);
                ctx.fillStyle = glow;
                ctx.fill();

                // Core
                const coreAlpha = (p.isResult ? 0.25 : 0.15) + pulse * 0.1 + litBoost * 0.45;
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius * (1 + litBoost * 0.3), 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${col}, ${coreAlpha})`;
                ctx.fill();

                // Result ring when lit
                if (p.isResult && litBoost > 0.1) {
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, p.radius + 3 + litBoost * 4, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(${col}, ${litBoost * 0.2})`;
                    ctx.lineWidth = 0.6;
                    ctx.stroke();
                }
            },
            initExtras() {
                extras.rings = []; // expanding radar rings
                extras.spawnTimer = 0;
                extras.scanAngle = 0;
            },
            drawGlobal(ctx, parts, accent, time, layerExtras) {
                const ex = layerExtras || extras;
                if (!ex.rings) { ex.rings = []; ex.spawnTimer = 0; ex.scanAngle = 0; }

                const cx = canvas.width * 0.5, cy = canvas.height * 0.45;
                const maxRadius = Math.max(canvas.width, canvas.height) * 0.7;

                // Spawn rings periodically
                ex.spawnTimer++;
                if (ex.spawnTimer >= 120) { // every ~2 seconds
                    ex.rings.push({
                        radius: 0,
                        maxRadius: maxRadius,
                        alpha: 0.35,
                        hue: (Math.random() - 0.5) * 40
                    });
                    ex.spawnTimer = 0;
                }

                // Update and draw rings
                for (let i = ex.rings.length - 1; i >= 0; i--) {
                    const ring = ex.rings[i];
                    ring.radius += 2.5;
                    ring.alpha = 0.35 * (1 - ring.radius / ring.maxRadius);

                    if (ring.alpha <= 0 || ring.radius > ring.maxRadius) {
                        ex.rings.splice(i, 1);
                        continue;
                    }

                    const col = shiftAccent(accent, ring.hue);

                    // Ring stroke
                    ctx.beginPath();
                    ctx.arc(cx, cy, ring.radius, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(${col}, ${ring.alpha * 0.5})`;
                    ctx.lineWidth = 1.5;
                    ctx.stroke();

                    // Soft inner glow on ring edge
                    const ringGlow = ctx.createRadialGradient(cx, cy, Math.max(0, ring.radius - 15), cx, cy, ring.radius + 5);
                    ringGlow.addColorStop(0, 'rgba(0,0,0,0)');
                    ringGlow.addColorStop(0.7, `rgba(${col}, ${ring.alpha * 0.08})`);
                    ringGlow.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(cx, cy, ring.radius + 5, 0, Math.PI * 2);
                    ctx.fillStyle = ringGlow;
                    ctx.fill();

                    // Light up particles near this ring's edge
                    for (const p of parts) {
                        const dx = p.x - cx, dy = p.y - cy;
                        const dist = Math.sqrt(dx * dx + dy * dy);
                        const diff = Math.abs(dist - ring.radius);
                        if (diff < 25) {
                            p.lit = Math.max(p.lit, (1 - diff / 25) * ring.alpha * 2.5);
                        }
                    }
                }

                // Rotating scan line — faint sweep
                ex.scanAngle += 0.012;
                const scanLen = maxRadius * 0.6;
                const sx = cx + Math.cos(ex.scanAngle) * scanLen;
                const sy = cy + Math.sin(ex.scanAngle) * scanLen;
                const scanGrad = ctx.createLinearGradient(cx, cy, sx, sy);
                scanGrad.addColorStop(0, `rgba(${accent}, 0.06)`);
                scanGrad.addColorStop(0.7, `rgba(${accent}, 0.02)`);
                scanGrad.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.lineTo(sx, sy);
                ctx.strokeStyle = scanGrad;
                ctx.lineWidth = 1;
                ctx.stroke();

                // Scan wedge — faint arc trailing the scan line
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.arc(cx, cy, scanLen, ex.scanAngle - 0.3, ex.scanAngle, false);
                ctx.closePath();
                const wedgeGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, scanLen);
                wedgeGrad.addColorStop(0, `rgba(${accent}, 0.03)`);
                wedgeGrad.addColorStop(0.5, `rgba(${accent}, 0.01)`);
                wedgeGrad.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.fillStyle = wedgeGrad;
                ctx.fill();

                // Center dot — radar origin
                const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, 12);
                cg.addColorStop(0, `rgba(${accent}, 0.15)`);
                cg.addColorStop(0.5, `rgba(${accent}, 0.05)`);
                cg.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(cx, cy, 12, 0, Math.PI * 2);
                ctx.fillStyle = cg;
                ctx.fill();

                ctx.beginPath();
                ctx.arc(cx, cy, 2, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${accent}, 0.3)`;
                ctx.fill();

                // Crosshair lines — very subtle
                ctx.strokeStyle = `rgba(${accent}, 0.04)`;
                ctx.lineWidth = 0.5;
                ctx.beginPath();
                ctx.moveTo(cx - scanLen * 0.4, cy);
                ctx.lineTo(cx + scanLen * 0.4, cy);
                ctx.moveTo(cx, cy - scanLen * 0.4);
                ctx.lineTo(cx, cy + scanLen * 0.4);
                ctx.stroke();

                // Range circles — faint concentric guides
                for (let r = 1; r <= 3; r++) {
                    const guideR = scanLen * r * 0.3;
                    ctx.beginPath();
                    ctx.arc(cx, cy, guideR, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(${accent}, 0.025)`;
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            }
        },

        // ── DISCOVER — drifting constellation starfield ──
        discover: {
            count: 60,
            init(p, i) {
                p.x = Math.random() * canvas.width;
                p.y = Math.random() * canvas.height;
                // Slow parallax drift — different layers move at different speeds
                p.layer = Math.random(); // 0 = far/dim, 1 = near/bright
                p.vx = (0.05 + p.layer * 0.15) * (Math.random() < 0.5 ? 1 : -1);
                p.vy = -0.02 - p.layer * 0.08; // gentle upward drift
                p.radius = 0.5 + p.layer * 2;
                p.phase = Math.random() * Math.PI * 2;
                p.hueShift = (Math.random() - 0.5) * 70;
                // Twinkle speed — far stars twinkle faster
                p.twinkleSpeed = 1.5 + (1 - p.layer) * 2;
                // Some are "constellation" anchor stars — brighter, connected
                p.isAnchor = i < 15;
                if (p.isAnchor) {
                    p.radius = 1.5 + p.layer * 2;
                    p.vx *= 0.4;
                    p.vy *= 0.4;
                }
            },
            update(p) {
                p.x += p.vx;
                p.y += p.vy;
                // Wrap around edges
                if (p.x < -20) p.x = canvas.width + 20;
                if (p.x > canvas.width + 20) p.x = -20;
                if (p.y < -20) p.y = canvas.height + 20;
                if (p.y > canvas.height + 20) p.y = -20;
            },
            draw(p, ctx, accent, time) {
                const twinkle = 0.4 + 0.6 * Math.pow(Math.sin(time * p.twinkleSpeed + p.phase), 2);
                const col = shiftAccent(accent, p.hueShift);
                const brightness = (0.3 + p.layer * 0.7) * twinkle;

                // Soft glow
                const glowR = p.radius * (3 + p.layer * 3);
                const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glowR);
                glow.addColorStop(0, `rgba(${col}, ${brightness * 0.25})`);
                glow.addColorStop(0.5, `rgba(${col}, ${brightness * 0.06})`);
                glow.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(p.x, p.y, glowR, 0, Math.PI * 2);
                ctx.fillStyle = glow;
                ctx.fill();

                // Core star
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius * (0.6 + twinkle * 0.4), 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${col}, ${brightness})`;
                ctx.fill();

                // Cross-spike on bright anchor stars
                if (p.isAnchor && twinkle > 0.7) {
                    const spikeLen = p.radius * 4 * twinkle;
                    const spikeAlpha = (twinkle - 0.7) * brightness * 0.5;
                    ctx.strokeStyle = `rgba(${col}, ${spikeAlpha})`;
                    ctx.lineWidth = 0.5;
                    ctx.beginPath();
                    ctx.moveTo(p.x - spikeLen, p.y);
                    ctx.lineTo(p.x + spikeLen, p.y);
                    ctx.moveTo(p.x, p.y - spikeLen);
                    ctx.lineTo(p.x, p.y + spikeLen);
                    ctx.stroke();
                }
            },
            initExtras() {
                extras.constellations = []; // groups of connected anchor indices
                extras.shootingStars = [];
                extras.nebulae = [];
                // Create 3 nebula regions
                for (let i = 0; i < 3; i++) {
                    extras.nebulae.push({
                        x: Math.random() * canvas.width,
                        y: Math.random() * canvas.height,
                        radius: 80 + Math.random() * 120,
                        hue: (Math.random() - 0.5) * 80,
                        drift: (Math.random() - 0.5) * 0.15
                    });
                }
            },
            drawGlobal(ctx, parts, accent, time, layerExtras) {
                const ex = layerExtras || extras;
                if (!ex.nebulae) { ex.nebulae = []; ex.shootingStars = []; ex.constellations = []; }

                // Draw nebula clouds — very subtle colored regions
                for (const neb of ex.nebulae) {
                    neb.x += neb.drift;
                    if (neb.x < -200) neb.x = canvas.width + 200;
                    if (neb.x > canvas.width + 200) neb.x = -200;

                    const col = shiftAccent(accent, neb.hue);
                    const pulse = 0.7 + 0.3 * Math.sin(time * 0.3 + neb.hue);
                    const ng = ctx.createRadialGradient(neb.x, neb.y, 0, neb.x, neb.y, neb.radius);
                    ng.addColorStop(0, `rgba(${col}, ${0.035 * pulse})`);
                    ng.addColorStop(0.5, `rgba(${col}, ${0.015 * pulse})`);
                    ng.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(neb.x, neb.y, neb.radius, 0, Math.PI * 2);
                    ctx.fillStyle = ng;
                    ctx.fill();
                }

                // Constellation lines — connect nearby anchor stars
                const anchors = parts.filter(p => p.isAnchor);
                const conDist = 180;
                for (let i = 0; i < anchors.length; i++) {
                    for (let j = i + 1; j < anchors.length; j++) {
                        const dx = anchors[j].x - anchors[i].x;
                        const dy = anchors[j].y - anchors[i].y;
                        const d = Math.sqrt(dx * dx + dy * dy);
                        if (d < conDist) {
                            const alpha = (1 - d / conDist) * 0.08;
                            const col = shiftAccent(accent, (anchors[i].hueShift + anchors[j].hueShift) * 0.5);
                            ctx.beginPath();
                            ctx.moveTo(anchors[i].x, anchors[i].y);
                            ctx.lineTo(anchors[j].x, anchors[j].y);
                            ctx.strokeStyle = `rgba(${col}, ${alpha})`;
                            ctx.lineWidth = 0.5;
                            ctx.stroke();
                        }
                    }
                }

                // Shooting stars — occasional streaks
                if (frameCount % 180 === 0 || (frameCount % 100 === 0 && Math.random() < 0.25)) {
                    const sx = Math.random() * canvas.width;
                    const angle = Math.PI * 0.6 + Math.random() * 0.4;
                    ex.shootingStars.push({
                        x: sx, y: -10,
                        vx: Math.cos(angle) * (4 + Math.random() * 3),
                        vy: Math.sin(angle) * (4 + Math.random() * 3),
                        trail: [],
                        life: 1,
                        hue: (Math.random() - 0.5) * 60
                    });
                }

                for (let i = ex.shootingStars.length - 1; i >= 0; i--) {
                    const star = ex.shootingStars[i];
                    star.x += star.vx;
                    star.y += star.vy;
                    star.life -= 0.015;
                    star.trail.push({ x: star.x, y: star.y });
                    if (star.trail.length > 15) star.trail.shift();

                    if (star.life <= 0 || star.x < -50 || star.x > canvas.width + 50 ||
                        star.y > canvas.height + 50) {
                        ex.shootingStars.splice(i, 1);
                        continue;
                    }

                    const col = shiftAccent(accent, star.hue);
                    for (let t = 0; t < star.trail.length - 1; t++) {
                        const frac = t / star.trail.length;
                        ctx.beginPath();
                        ctx.moveTo(star.trail[t].x, star.trail[t].y);
                        ctx.lineTo(star.trail[t + 1].x, star.trail[t + 1].y);
                        ctx.strokeStyle = `rgba(${col}, ${frac * 0.4 * star.life})`;
                        ctx.lineWidth = frac * 2;
                        ctx.lineCap = 'round';
                        ctx.stroke();
                    }

                    const sg = ctx.createRadialGradient(star.x, star.y, 0, star.x, star.y, 4);
                    sg.addColorStop(0, `rgba(255,255,255, ${0.7 * star.life})`);
                    sg.addColorStop(0.4, `rgba(${col}, ${0.4 * star.life})`);
                    sg.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(star.x, star.y, 4, 0, Math.PI * 2);
                    ctx.fillStyle = sg;
                    ctx.fill();
                }
            }
        },

        // ── ARTISTS — sound ripples / audio wave field ──
        artists: {
            count: 50,
            init(p, i) {
                // Grid-ish scatter with jitter
                const cols = 10, rows = 5;
                const col = i % cols, row = Math.floor(i / cols);
                p.baseX = (col + 0.5) / cols * canvas.width + (Math.random() - 0.5) * 40;
                p.baseY = (row + 0.5) / rows * canvas.height + (Math.random() - 0.5) * 40;
                p.x = p.baseX;
                p.y = p.baseY;
                p.radius = 1.2 + Math.random() * 1.3;
                p.phase = Math.random() * Math.PI * 2;
                p.hueShift = (Math.random() - 0.5) * 50;
                p.displacement = 0; // vertical displacement from ripples
            },
            update(p, time) {
                // Reset displacement each frame, drawGlobal will set it
                p.displacement = 0;
                p.x = p.baseX;
                p.y = p.baseY;
            },
            draw(p, ctx, accent, time) {
                const col = shiftAccent(accent, p.hueShift);
                const energy = Math.abs(p.displacement);
                const alpha = 0.15 + energy * 0.6;
                const r = p.radius * (1 + energy * 0.5);

                // Glow — stronger when displaced
                const glowR = r * (3 + energy * 4);
                const glow = ctx.createRadialGradient(p.x, p.y + p.displacement * 15, 0, p.x, p.y + p.displacement * 15, glowR);
                glow.addColorStop(0, `rgba(${col}, ${0.06 + energy * 0.2})`);
                glow.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(p.x, p.y + p.displacement * 15, glowR, 0, Math.PI * 2);
                ctx.fillStyle = glow;
                ctx.fill();

                // Core — offset by displacement
                ctx.beginPath();
                ctx.arc(p.x, p.y + p.displacement * 15, r, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${col}, ${alpha})`;
                ctx.fill();
            },
            initExtras() {
                extras.emitters = [];
                // 3–4 ripple emitters at random positions
                const count = 3 + Math.floor(Math.random() * 2);
                for (let i = 0; i < count; i++) {
                    extras.emitters.push({
                        x: 0.15 * canvas.width + Math.random() * 0.7 * canvas.width,
                        y: 0.15 * canvas.height + Math.random() * 0.7 * canvas.height,
                        rings: [],
                        timer: Math.floor(Math.random() * 80),
                        interval: 70 + Math.floor(Math.random() * 40),
                        hue: (i - 1.5) * 35
                    });
                }
                extras.waveLines = [];
            },
            drawGlobal(ctx, parts, accent, time, layerExtras) {
                const ex = layerExtras || extras;
                if (!ex.emitters) { ex.emitters = []; ex.waveLines = []; }
                const maxR = Math.max(canvas.width, canvas.height) * 0.6;

                // Update emitters — spawn rings
                for (const em of ex.emitters) {
                    em.timer++;
                    if (em.timer >= em.interval) {
                        em.rings.push({ radius: 0, alpha: 0.3 });
                        em.timer = 0;
                    }

                    const col = shiftAccent(accent, em.hue);

                    // Draw emitter center — subtle pulsing dot
                    const pulse = 0.5 + 0.5 * Math.sin(time * 2 + em.hue);
                    const eg = ctx.createRadialGradient(em.x, em.y, 0, em.x, em.y, 15);
                    eg.addColorStop(0, `rgba(${col}, ${0.1 + pulse * 0.08})`);
                    eg.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(em.x, em.y, 15, 0, Math.PI * 2);
                    ctx.fillStyle = eg;
                    ctx.fill();

                    // Update and draw rings
                    for (let i = em.rings.length - 1; i >= 0; i--) {
                        const ring = em.rings[i];
                        ring.radius += 1.8;
                        ring.alpha = 0.25 * (1 - ring.radius / maxR);

                        if (ring.alpha <= 0 || ring.radius > maxR) {
                            em.rings.splice(i, 1);
                            continue;
                        }

                        // Ring arc
                        ctx.beginPath();
                        ctx.arc(em.x, em.y, ring.radius, 0, Math.PI * 2);
                        ctx.strokeStyle = `rgba(${col}, ${ring.alpha * 0.4})`;
                        ctx.lineWidth = 1.5 * (1 - ring.radius / maxR);
                        ctx.stroke();

                        // Displace particles near ring edge
                        for (const p of parts) {
                            const dx = p.baseX - em.x, dy = p.baseY - em.y;
                            const dist = Math.sqrt(dx * dx + dy * dy);
                            const diff = Math.abs(dist - ring.radius);
                            if (diff < 30) {
                                const wave = Math.cos((diff / 30) * Math.PI * 0.5);
                                p.displacement += wave * ring.alpha * 2.5;
                            }
                        }
                    }
                }

                // Horizontal wave lines — subtle sine waves across canvas
                ctx.lineWidth = 0.4;
                for (let w = 0; w < 3; w++) {
                    const yBase = canvas.height * (0.25 + w * 0.25);
                    const col = shiftAccent(accent, (w - 1) * 30);
                    ctx.beginPath();
                    for (let x = 0; x < canvas.width; x += 4) {
                        const y = yBase + Math.sin(x * 0.008 + time * (0.8 + w * 0.3) + w) * 12;
                        if (x === 0) ctx.moveTo(x, y);
                        else ctx.lineTo(x, y);
                    }
                    ctx.strokeStyle = `rgba(${col}, 0.04)`;
                    ctx.stroke();
                }
            }
        },

        // ── AUTOMATIONS — electric / lightning circuit ──
        automations: {
            count: 40,
            init(p, i) {
                p.x = Math.random() * canvas.width;
                p.y = Math.random() * canvas.height;
                p.vx = (Math.random() - 0.5) * 0.25;
                p.vy = (Math.random() - 0.5) * 0.25;
                p.radius = 1 + Math.random() * 1.5;
                p.phase = Math.random() * Math.PI * 2;
                p.hueShift = (Math.random() - 0.5) * 40;
                p.charge = 0; // lights up when struck by lightning
                // Some are relay nodes — bigger, attract bolts
                p.isRelay = i < 6;
                if (p.isRelay) p.radius = 2.5 + Math.random() * 1.5;
            },
            update(p) {
                p.x += p.vx;
                p.y += p.vy;
                if (p.x < 10 || p.x > canvas.width - 10) p.vx *= -1;
                if (p.y < 10 || p.y > canvas.height - 10) p.vy *= -1;
                p.x = Math.max(5, Math.min(canvas.width - 5, p.x));
                p.y = Math.max(5, Math.min(canvas.height - 5, p.y));
                if (p.charge > 0) p.charge = Math.max(0, p.charge - 0.02);
            },
            draw(p, ctx, accent, time) {
                const pulse = 0.5 + 0.5 * Math.sin(time * 2 + p.phase);
                const col = shiftAccent(accent, p.hueShift);
                const energy = p.charge;

                // Electric glow — bigger when charged
                const glowR = p.radius * (3 + energy * 6);
                const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glowR);
                glow.addColorStop(0, `rgba(${col}, ${(p.isRelay ? 0.12 : 0.06) + energy * 0.35})`);
                glow.addColorStop(0.6, `rgba(${col}, ${energy * 0.08})`);
                glow.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(p.x, p.y, glowR, 0, Math.PI * 2);
                ctx.fillStyle = glow;
                ctx.fill();

                // Core
                const coreAlpha = (p.isRelay ? 0.3 : 0.15) + pulse * 0.1 + energy * 0.5;
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius * (1 + energy * 0.3), 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${col}, ${coreAlpha})`;
                ctx.fill();

                // Relay ring
                if (p.isRelay) {
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, p.radius + 3 + pulse * 2, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(${col}, ${0.06 + energy * 0.15})`;
                    ctx.lineWidth = 0.6;
                    ctx.stroke();
                }
            },
            initExtras() {
                extras.bolts = [];
                extras.sparks = [];
                extras.boltTimer = 0;
            },
            drawGlobal(ctx, parts, accent, time, layerExtras) {
                const ex = layerExtras || extras;
                if (!ex.bolts) { ex.bolts = []; ex.sparks = []; ex.boltTimer = 0; }

                // Spawn lightning bolts between relay nodes
                ex.boltTimer++;
                if (ex.boltTimer >= 45 + Math.floor(Math.random() * 30)) {
                    ex.boltTimer = 0;
                    const relays = [];
                    const normals = [];
                    parts.forEach((p, i) => { if (p.isRelay) relays.push(i); else normals.push(i); });

                    if (relays.length >= 2 || parts.length >= 2) {
                        let a, b;
                        if (relays.length >= 2 && Math.random() < 0.6) {
                            // Relay to relay
                            a = relays[Math.floor(Math.random() * relays.length)];
                            b = relays[Math.floor(Math.random() * relays.length)];
                            if (a === b) b = relays[(relays.indexOf(a) + 1) % relays.length];
                        } else {
                            // Relay to nearest normal
                            a = relays.length > 0 ? relays[Math.floor(Math.random() * relays.length)] : Math.floor(Math.random() * parts.length);
                            let bestD = Infinity;
                            b = a;
                            for (let i = 0; i < parts.length; i++) {
                                if (i === a) continue;
                                const dx = parts[i].x - parts[a].x, dy = parts[i].y - parts[a].y;
                                const d = dx * dx + dy * dy;
                                if (d < bestD && d < 300 * 300) { bestD = d; b = i; }
                            }
                        }
                        if (a !== b) {
                            // Build jagged bolt path
                            const pa = parts[a], pb = parts[b];
                            const segs = 6 + Math.floor(Math.random() * 4);
                            const path = [{ x: pa.x, y: pa.y }];
                            for (let s = 1; s < segs; s++) {
                                const t = s / segs;
                                const mx = pa.x + (pb.x - pa.x) * t;
                                const my = pa.y + (pb.y - pa.y) * t;
                                const jitter = 25 * (1 - Math.abs(t - 0.5) * 2);
                                path.push({
                                    x: mx + (Math.random() - 0.5) * jitter * 2,
                                    y: my + (Math.random() - 0.5) * jitter * 2
                                });
                            }
                            path.push({ x: pb.x, y: pb.y });
                            ex.bolts.push({
                                path,
                                life: 1,
                                hue: (parts[a].hueShift + parts[b].hueShift) * 0.5,
                                branch: Math.random() < 0.4 // some bolts branch
                            });
                            parts[a].charge = 1;
                            parts[b].charge = 1;

                            // Sparks at endpoints
                            for (let s = 0; s < 4; s++) {
                                ex.sparks.push({
                                    x: pb.x, y: pb.y,
                                    vx: (Math.random() - 0.5) * 3,
                                    vy: (Math.random() - 0.5) * 3,
                                    life: 0.6 + Math.random() * 0.4,
                                    hue: parts[b].hueShift
                                });
                            }
                        }
                    }
                }

                // Draw bolts
                for (let i = ex.bolts.length - 1; i >= 0; i--) {
                    const bolt = ex.bolts[i];
                    bolt.life -= 0.04;
                    if (bolt.life <= 0) { ex.bolts.splice(i, 1); continue; }

                    const col = shiftAccent(accent, bolt.hue);
                    const alpha = bolt.life;

                    // Main bolt
                    ctx.beginPath();
                    ctx.moveTo(bolt.path[0].x, bolt.path[0].y);
                    for (let s = 1; s < bolt.path.length; s++) {
                        ctx.lineTo(bolt.path[s].x, bolt.path[s].y);
                    }
                    ctx.strokeStyle = `rgba(${col}, ${alpha * 0.6})`;
                    ctx.lineWidth = 2 * alpha;
                    ctx.lineCap = 'round';
                    ctx.lineJoin = 'round';
                    ctx.stroke();

                    // Bright core
                    ctx.strokeStyle = `rgba(255,255,255, ${alpha * 0.4})`;
                    ctx.lineWidth = 0.8 * alpha;
                    ctx.stroke();

                    // Glow along bolt
                    ctx.strokeStyle = `rgba(${col}, ${alpha * 0.15})`;
                    ctx.lineWidth = 6 * alpha;
                    ctx.stroke();

                    // Branch bolt
                    if (bolt.branch && bolt.path.length > 3) {
                        const branchIdx = 2 + Math.floor(Math.random() * (bolt.path.length - 3));
                        const bp = bolt.path[branchIdx];
                        ctx.beginPath();
                        ctx.moveTo(bp.x, bp.y);
                        let bx = bp.x, by = bp.y;
                        for (let s = 0; s < 3; s++) {
                            bx += (Math.random() - 0.5) * 30;
                            by += (Math.random() - 0.5) * 30;
                            ctx.lineTo(bx, by);
                        }
                        ctx.strokeStyle = `rgba(${col}, ${alpha * 0.3})`;
                        ctx.lineWidth = 1 * alpha;
                        ctx.stroke();
                        bolt.branch = false; // only draw branch once
                    }
                }

                // Draw sparks
                for (let i = ex.sparks.length - 1; i >= 0; i--) {
                    const sp = ex.sparks[i];
                    sp.x += sp.vx;
                    sp.y += sp.vy;
                    sp.vx *= 0.95;
                    sp.vy *= 0.95;
                    sp.life -= 0.03;
                    if (sp.life <= 0) { ex.sparks.splice(i, 1); continue; }

                    const col = shiftAccent(accent, sp.hue);
                    ctx.beginPath();
                    ctx.arc(sp.x, sp.y, 1.2 * sp.life, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(${col}, ${sp.life * 0.8})`;
                    ctx.fill();
                }

                // Ambient circuit traces — faint grid lines
                ctx.strokeStyle = `rgba(${accent}, 0.015)`;
                ctx.lineWidth = 0.5;
                const spacing = 80;
                for (let x = spacing; x < canvas.width; x += spacing) {
                    const wobble = Math.sin(x * 0.01 + time * 0.5) * 5;
                    ctx.beginPath();
                    ctx.moveTo(x + wobble, 0);
                    ctx.lineTo(x - wobble, canvas.height);
                    ctx.stroke();
                }
                for (let y = spacing; y < canvas.height; y += spacing) {
                    const wobble = Math.cos(y * 0.01 + time * 0.5) * 5;
                    ctx.beginPath();
                    ctx.moveTo(0, y + wobble);
                    ctx.lineTo(canvas.width, y - wobble);
                    ctx.stroke();
                }
            }
        },

        // ── LIBRARY — vinyl turntable grooves ──
        library: {
            count: 50,
            init(p, i) {
                const cx = canvas.width * 0.5, cy = canvas.height * 0.5;
                const minDim = Math.min(canvas.width, canvas.height);
                // Distribute across grooves — 8 concentric rings
                const grooveCount = 8;
                p.groove = i % grooveCount;
                p.grooveRadius = (0.06 + p.groove * 0.045) * minDim;
                p.angle = (i / Math.ceil(50 / grooveCount)) * Math.PI * 2 + Math.random() * 0.5;
                // All rotate same direction (clockwise), outer grooves slightly slower
                p.speed = 0.003 - p.groove * 0.0002;
                p.radius = 1 + Math.random() * 1.2;
                p.phase = Math.random() * Math.PI * 2;
                p.hueShift = (p.groove - 3.5) * 12;
                p.x = cx + Math.cos(p.angle) * p.grooveRadius;
                p.y = cy + Math.sin(p.angle) * p.grooveRadius;
            },
            update(p) {
                p.angle += p.speed;
                const cx = canvas.width * 0.5, cy = canvas.height * 0.5;
                p.x = cx + Math.cos(p.angle) * p.grooveRadius;
                p.y = cy + Math.sin(p.angle) * p.grooveRadius;
            },
            draw(p, ctx, accent, time) {
                const pulse = 0.5 + 0.5 * Math.sin(time * 1.5 + p.phase);
                const col = shiftAccent(accent, p.hueShift);

                const glowR = p.radius * 4;
                const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glowR);
                glow.addColorStop(0, `rgba(${col}, ${0.1 + pulse * 0.08})`);
                glow.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(p.x, p.y, glowR, 0, Math.PI * 2);
                ctx.fillStyle = glow;
                ctx.fill();

                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${col}, ${0.25 + pulse * 0.2})`;
                ctx.fill();
            },
            initExtras() {
                extras.tonearmAngle = -0.4;
                extras.spinHighlight = 0;
                extras.needleRipples = [];
                extras.rippleTimer = 0;
                extras.noteParticles = []; // tiny notes floating off the needle
            },
            drawGlobal(ctx, parts, accent, time, layerExtras) {
                const ex = layerExtras || extras;
                if (ex.tonearmAngle === undefined) {
                    ex.tonearmAngle = -0.4; ex.spinHighlight = 0;
                    ex.needleRipples = []; ex.rippleTimer = 0; ex.noteParticles = [];
                }
                const cx = canvas.width * 0.5, cy = canvas.height * 0.5;
                const minDim = Math.min(canvas.width, canvas.height);

                // Vinyl grooves — concentric rings with subtle wobble
                const grooveCount = 8;
                for (let g = 0; g < grooveCount; g++) {
                    const r = (0.06 + g * 0.045) * minDim;
                    const col = shiftAccent(accent, (g - 3.5) * 12);
                    // Groove brightness pulses slightly
                    const gPulse = 0.03 + 0.015 * Math.sin(time * 0.8 + g * 0.7);
                    ctx.beginPath();
                    ctx.arc(cx, cy, r, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(${col}, ${gPulse})`;
                    ctx.lineWidth = 0.8;
                    ctx.stroke();
                }

                // Outer rim
                const outerR = (0.06 + grooveCount * 0.045 + 0.02) * minDim;
                ctx.beginPath();
                ctx.arc(cx, cy, outerR, 0, Math.PI * 2);
                ctx.strokeStyle = `rgba(${accent}, 0.06)`;
                ctx.lineWidth = 1.5;
                ctx.stroke();

                // Center label — warm glow
                const labelR = 0.04 * minDim;
                const lg = ctx.createRadialGradient(cx, cy, 0, cx, cy, labelR);
                lg.addColorStop(0, `rgba(${accent}, 0.15)`);
                lg.addColorStop(0.6, `rgba(${accent}, 0.06)`);
                lg.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(cx, cy, labelR, 0, Math.PI * 2);
                ctx.fillStyle = lg;
                ctx.fill();

                // Spindle dot
                ctx.beginPath();
                ctx.arc(cx, cy, 2.5, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${accent}, 0.3)`;
                ctx.fill();

                // Light reflection — rotating highlight arc
                ex.spinHighlight += 0.006;
                const hlAngle = ex.spinHighlight;
                const hlR = 0.22 * minDim;
                ctx.beginPath();
                ctx.arc(cx, cy, hlR, hlAngle - 0.4, hlAngle + 0.4);
                const hlGrad = ctx.createRadialGradient(
                    cx + Math.cos(hlAngle) * hlR * 0.5,
                    cy + Math.sin(hlAngle) * hlR * 0.5, 0,
                    cx + Math.cos(hlAngle) * hlR * 0.5,
                    cy + Math.sin(hlAngle) * hlR * 0.5, hlR * 0.6
                );
                hlGrad.addColorStop(0, `rgba(255,255,255, 0.03)`);
                hlGrad.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.strokeStyle = hlGrad;
                ctx.lineWidth = hlR * 0.8;
                ctx.stroke();

                // Tonearm — pivot at top-right, needle rests on groove 5
                const armPivotX = cx + outerR * 0.9;
                const armPivotY = cy - outerR * 0.75;
                const needleGroove = 5;
                const needleR = (0.06 + needleGroove * 0.045) * minDim;
                // Needle sits on the groove at a fixed angle from center
                const needleAngle = -0.6 + Math.sin(time * 0.3) * 0.01; // subtle bob
                const armEndX = cx + Math.cos(needleAngle) * needleR;
                const armEndY = cy + Math.sin(needleAngle) * needleR;

                // Arm line — straight from pivot to needle
                ctx.beginPath();
                ctx.moveTo(armPivotX, armPivotY);
                ctx.lineTo(armEndX, armEndY);
                ctx.strokeStyle = `rgba(${accent}, 0.1)`;
                ctx.lineWidth = 1.8;
                ctx.lineCap = 'round';
                ctx.stroke();

                // Headshell — small wider section at the end
                const hsLen = 12;
                const hsDx = (armEndX - armPivotX), hsDy = (armEndY - armPivotY);
                const hsNorm = Math.sqrt(hsDx * hsDx + hsDy * hsDy);
                const hsX = armEndX - (hsDx / hsNorm) * hsLen;
                const hsY = armEndY - (hsDy / hsNorm) * hsLen;
                ctx.beginPath();
                ctx.moveTo(hsX, hsY);
                ctx.lineTo(armEndX, armEndY);
                ctx.strokeStyle = `rgba(${accent}, 0.15)`;
                ctx.lineWidth = 3;
                ctx.stroke();

                // Pivot dot
                ctx.beginPath();
                ctx.arc(armPivotX, armPivotY, 3, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${accent}, 0.12)`;
                ctx.fill();

                // Needle glow — on the groove
                const ng = ctx.createRadialGradient(armEndX, armEndY, 0, armEndX, armEndY, 8);
                ng.addColorStop(0, `rgba(${accent}, 0.3)`);
                ng.addColorStop(0.5, `rgba(${accent}, 0.1)`);
                ng.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(armEndX, armEndY, 8, 0, Math.PI * 2);
                ctx.fillStyle = ng;
                ctx.fill();

                // Sound wave ripples — concentric arcs expanding FROM the needle outward
                ex.rippleTimer++;
                if (ex.rippleTimer >= 30) {
                    ex.rippleTimer = 0;
                    ex.needleRipples.push({
                        radius: 0, alpha: 0.3,
                        hue: (Math.random() - 0.5) * 30
                    });
                }

                const maxRippleR = outerR * 0.7;
                for (let i = ex.needleRipples.length - 1; i >= 0; i--) {
                    const rip = ex.needleRipples[i];
                    rip.radius += 1.8;
                    rip.alpha = 0.25 * (1 - rip.radius / maxRippleR);
                    if (rip.alpha <= 0 || rip.radius > maxRippleR) {
                        ex.needleRipples.splice(i, 1);
                        continue;
                    }
                    const col = shiftAccent(accent, rip.hue);
                    // Full circle ripple centered on needle
                    ctx.beginPath();
                    ctx.arc(armEndX, armEndY, rip.radius, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(${col}, ${rip.alpha * 0.35})`;
                    ctx.lineWidth = 1 * (1 - rip.radius / maxRippleR);
                    ctx.stroke();

                    // Brighten groove particles near ripple edge
                    for (const p of parts) {
                        const dx = p.x - armEndX, dy = p.y - armEndY;
                        const dist = Math.sqrt(dx * dx + dy * dy);
                        if (Math.abs(dist - rip.radius) < 15) {
                            p.phase = time * 1.5;
                        }
                    }
                }

                // Floating note particles — tiny shapes drifting from needle area
                if (frameCount % 60 === 0) {
                    ex.noteParticles.push({
                        x: armEndX + (Math.random() - 0.5) * 15,
                        y: armEndY,
                        vx: (Math.random() - 0.5) * 0.8,
                        vy: -0.5 - Math.random() * 0.8,
                        life: 1,
                        size: 3 + Math.random() * 3,
                        hue: (Math.random() - 0.5) * 40,
                        type: Math.floor(Math.random() * 2) // 0 = eighth note, 1 = beam pair
                    });
                }

                for (let i = ex.noteParticles.length - 1; i >= 0; i--) {
                    const n = ex.noteParticles[i];
                    n.x += n.vx;
                    n.y += n.vy;
                    n.vx += (Math.random() - 0.5) * 0.02;
                    n.life -= 0.008;
                    if (n.life <= 0) { ex.noteParticles.splice(i, 1); continue; }

                    const col = shiftAccent(accent, n.hue);
                    const a = n.life * 0.3;
                    const s = n.size;

                    if (n.type === 0) {
                        // Eighth note — circle head + stem + flag
                        ctx.beginPath();
                        ctx.arc(n.x, n.y, s * 0.35, 0, Math.PI * 2);
                        ctx.fillStyle = `rgba(${col}, ${a})`;
                        ctx.fill();
                        ctx.beginPath();
                        ctx.moveTo(n.x + s * 0.35, n.y);
                        ctx.lineTo(n.x + s * 0.35, n.y - s);
                        ctx.strokeStyle = `rgba(${col}, ${a})`;
                        ctx.lineWidth = 0.8;
                        ctx.stroke();
                        // Flag
                        ctx.beginPath();
                        ctx.moveTo(n.x + s * 0.35, n.y - s);
                        ctx.quadraticCurveTo(n.x + s * 0.8, n.y - s * 0.6, n.x + s * 0.35, n.y - s * 0.4);
                        ctx.strokeStyle = `rgba(${col}, ${a * 0.8})`;
                        ctx.stroke();
                    } else {
                        // Beamed pair — two note heads connected
                        ctx.beginPath();
                        ctx.arc(n.x, n.y, s * 0.3, 0, Math.PI * 2);
                        ctx.arc(n.x + s * 0.6, n.y, s * 0.3, 0, Math.PI * 2);
                        ctx.fillStyle = `rgba(${col}, ${a})`;
                        ctx.fill();
                        // Stems
                        ctx.beginPath();
                        ctx.moveTo(n.x + s * 0.3, n.y);
                        ctx.lineTo(n.x + s * 0.3, n.y - s * 0.9);
                        ctx.moveTo(n.x + s * 0.9, n.y);
                        ctx.lineTo(n.x + s * 0.9, n.y - s * 0.9);
                        ctx.strokeStyle = `rgba(${col}, ${a})`;
                        ctx.lineWidth = 0.8;
                        ctx.stroke();
                        // Beam
                        ctx.beginPath();
                        ctx.moveTo(n.x + s * 0.3, n.y - s * 0.9);
                        ctx.lineTo(n.x + s * 0.9, n.y - s * 0.9);
                        ctx.lineWidth = 1.2;
                        ctx.stroke();
                    }
                }
            }
        },

        // ── IMPORT — data stream flowing into a central vortex ──
        import: {
            count: 55,
            init(p, i) {
                const cx = canvas.width * 0.5, cy = canvas.height * 0.45;
                // Spawn from edges, will flow toward center
                p.radius = 0.8 + Math.random() * 1.3;
                p.hueShift = (Math.random() - 0.5) * 50;
                p.phase = Math.random() * Math.PI * 2;
                p.speed = 0.8 + Math.random() * 1.2;
                p.absorbed = false;
                // Stream column — particles fall in vertical streams
                p.stream = i % 7;
                p.streamX = canvas.width * (0.1 + (p.stream / 6) * 0.8);
                // Stagger vertically
                p.x = p.streamX + (Math.random() - 0.5) * 20;
                p.y = -Math.random() * canvas.height;
                // Pull radius — when close to center, spiral in
                p.pullDist = 120 + Math.random() * 60;
                p.spiralAngle = Math.atan2(p.y - cy, p.x - cx);
                p.trail = [];
            },
            update(p) {
                const cx = canvas.width * 0.5, cy = canvas.height * 0.45;
                const dx = cx - p.x, dy = cy - p.y;
                const dist = Math.sqrt(dx * dx + dy * dy);

                if (dist < p.pullDist) {
                    // Spiral inward
                    p.spiralAngle += 0.08;
                    const pullStrength = 1 - dist / p.pullDist;
                    const spiralR = dist * (1 - pullStrength * 0.04);
                    p.x = cx + Math.cos(p.spiralAngle) * spiralR;
                    p.y = cy + Math.sin(p.spiralAngle) * spiralR;

                    if (dist < 8) {
                        // Absorbed — reset from top
                        p.x = p.streamX + (Math.random() - 0.5) * 20;
                        p.y = -10 - Math.random() * 100;
                        p.spiralAngle = Math.atan2(p.y - cy, p.x - cx);
                        p.trail = [];
                    }
                } else {
                    // Fall downward in stream
                    p.x += Math.sin(p.phase + p.y * 0.005) * 0.3;
                    p.y += p.speed;
                    // Update spiral angle as we approach
                    p.spiralAngle = Math.atan2(p.y - cy, p.x - cx);
                }

                // Trail
                p.trail.push({ x: p.x, y: p.y });
                if (p.trail.length > 6) p.trail.shift();

                // Reset if off screen
                if (p.y > canvas.height + 30 && dist > p.pullDist) {
                    p.x = p.streamX + (Math.random() - 0.5) * 20;
                    p.y = -10 - Math.random() * 50;
                    p.trail = [];
                }
            },
            draw(p, ctx, accent, time) {
                const cx = canvas.width * 0.5, cy = canvas.height * 0.45;
                const dist = Math.sqrt((p.x - cx) ** 2 + (p.y - cy) ** 2);
                const nearCenter = Math.max(0, 1 - dist / p.pullDist);
                const col = shiftAccent(accent, p.hueShift);
                const pulse = 0.5 + 0.5 * Math.sin(time * 2 + p.phase);

                // Trail
                for (let t = 0; t < p.trail.length - 1; t++) {
                    const frac = t / p.trail.length;
                    ctx.beginPath();
                    ctx.moveTo(p.trail[t].x, p.trail[t].y);
                    ctx.lineTo(p.trail[t + 1].x, p.trail[t + 1].y);
                    ctx.strokeStyle = `rgba(${col}, ${frac * 0.15 * (1 + nearCenter)})`;
                    ctx.lineWidth = frac * 1.5;
                    ctx.lineCap = 'round';
                    ctx.stroke();
                }

                // Glow — intensifies near center
                const glowR = p.radius * (3 + nearCenter * 4);
                const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glowR);
                glow.addColorStop(0, `rgba(${col}, ${0.08 + nearCenter * 0.25 + pulse * 0.05})`);
                glow.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(p.x, p.y, glowR, 0, Math.PI * 2);
                ctx.fillStyle = glow;
                ctx.fill();

                // Core
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius * (1 + nearCenter * 0.5), 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${col}, ${0.2 + nearCenter * 0.5 + pulse * 0.1})`;
                ctx.fill();
            },
            initExtras() {
                extras.portalPulse = 0;
            },
            drawGlobal(ctx, parts, accent, time, layerExtras) {
                const ex = layerExtras || extras;
                if (ex.portalPulse === undefined) ex.portalPulse = 0;
                const cx = canvas.width * 0.5, cy = canvas.height * 0.45;
                const minDim = Math.min(canvas.width, canvas.height);

                // Portal at center — pulsing vortex
                ex.portalPulse += 0.02;

                // Concentric portal rings
                for (let r = 0; r < 4; r++) {
                    const radius = 10 + r * 12;
                    const rot = time * (1.5 - r * 0.3) * (r % 2 === 0 ? 1 : -1);
                    const alpha = 0.08 - r * 0.015;
                    const col = shiftAccent(accent, r * 20 - 30);

                    ctx.beginPath();
                    ctx.arc(cx, cy, radius, rot, rot + Math.PI * 1.5);
                    ctx.strokeStyle = `rgba(${col}, ${alpha})`;
                    ctx.lineWidth = 1.5 - r * 0.2;
                    ctx.lineCap = 'round';
                    ctx.stroke();
                }

                // Center glow — pulses with absorptions
                const gPulse = 0.8 + 0.2 * Math.sin(ex.portalPulse * 3);
                const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, 40);
                cg.addColorStop(0, `rgba(${accent}, ${0.15 * gPulse})`);
                cg.addColorStop(0.4, `rgba(${accent}, ${0.05 * gPulse})`);
                cg.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(cx, cy, 40, 0, Math.PI * 2);
                ctx.fillStyle = cg;
                ctx.fill();

                // Stream guide lines — faint vertical lines showing data streams
                for (let s = 0; s < 7; s++) {
                    const sx = canvas.width * (0.1 + (s / 6) * 0.8);
                    const col = shiftAccent(accent, (s - 3) * 15);

                    // Fade line from top toward center
                    const grad = ctx.createLinearGradient(sx, 0, sx, cy);
                    grad.addColorStop(0, `rgba(${col}, 0.03)`);
                    grad.addColorStop(0.7, `rgba(${col}, 0.015)`);
                    grad.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.moveTo(sx, 0);
                    // Curve toward center
                    ctx.quadraticCurveTo(sx, cy * 0.7, cx, cy);
                    ctx.strokeStyle = grad;
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }

                // Outer absorption ring — faint
                const absR = 120 + Math.sin(time * 0.8) * 10;
                ctx.beginPath();
                ctx.arc(cx, cy, absR, 0, Math.PI * 2);
                ctx.strokeStyle = `rgba(${accent}, 0.025)`;
                ctx.lineWidth = 0.5;
                ctx.setLineDash([4, 8]);
                ctx.stroke();
                ctx.setLineDash([]);
            }
        },

        // ── SETTINGS — interlocking gears / clockwork ──
        settings: {
            count: 45,
            init(p, i) {
                // Assign particles to gears
                const gearDefs = [
                    { cx: 0.35, cy: 0.4, r: 0.16, teeth: 12, dir: 1 },
                    { cx: 0.62, cy: 0.38, r: 0.13, teeth: 10, dir: -1 },
                    { cx: 0.48, cy: 0.65, r: 0.10, teeth: 8, dir: 1 },
                    { cx: 0.25, cy: 0.68, r: 0.08, teeth: 6, dir: -1 },
                    { cx: 0.72, cy: 0.62, r: 0.09, teeth: 7, dir: 1 },
                ];
                p.gear = i % gearDefs.length;
                const g = gearDefs[p.gear];
                const minDim = Math.min(canvas.width, canvas.height);
                p.gearCx = g.cx * canvas.width;
                p.gearCy = g.cy * canvas.height;
                p.gearR = g.r * minDim;
                p.gearTeeth = g.teeth;
                p.gearDir = g.dir;
                // Position along gear rim
                p.angle = (i / Math.ceil(45 / gearDefs.length)) * Math.PI * 2 + Math.random() * 0.3;
                p.speed = g.dir * (0.003 + (1 / g.teeth) * 0.008);
                p.radius = 1 + Math.random() * 1.2;
                p.phase = Math.random() * Math.PI * 2;
                p.hueShift = (p.gear - 2) * 20;
                p.x = p.gearCx + Math.cos(p.angle) * p.gearR;
                p.y = p.gearCy + Math.sin(p.angle) * p.gearR;
            },
            update(p) {
                p.angle += p.speed;
                // Tooth bump — particle radius wobbles with gear teeth
                const toothPhase = (p.angle * p.gearTeeth) % (Math.PI * 2);
                p.toothBump = Math.max(0, Math.sin(toothPhase)) * 0.15;
                const r = p.gearR * (1 + p.toothBump);
                p.x = p.gearCx + Math.cos(p.angle) * r;
                p.y = p.gearCy + Math.sin(p.angle) * r;
            },
            draw(p, ctx, accent, time) {
                const pulse = 0.5 + 0.5 * Math.sin(time * 1.5 + p.phase);
                const col = shiftAccent(accent, p.hueShift);
                const bump = p.toothBump || 0;

                const glowR = p.radius * (3 + bump * 6);
                const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glowR);
                glow.addColorStop(0, `rgba(${col}, ${0.1 + bump * 0.2 + pulse * 0.05})`);
                glow.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(p.x, p.y, glowR, 0, Math.PI * 2);
                ctx.fillStyle = glow;
                ctx.fill();

                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius * (1 + bump * 0.5), 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${col}, ${0.2 + bump * 0.35 + pulse * 0.1})`;
                ctx.fill();
            },
            initExtras() {
                extras.gearDefs = [
                    { cx: 0.35, cy: 0.4, r: 0.16, teeth: 12, dir: 1 },
                    { cx: 0.62, cy: 0.38, r: 0.13, teeth: 10, dir: -1 },
                    { cx: 0.48, cy: 0.65, r: 0.10, teeth: 8, dir: 1 },
                    { cx: 0.25, cy: 0.68, r: 0.08, teeth: 6, dir: -1 },
                    { cx: 0.72, cy: 0.62, r: 0.09, teeth: 7, dir: 1 },
                ];
                extras.gearAngles = extras.gearDefs.map(() => 0);
            },
            drawGlobal(ctx, parts, accent, time, layerExtras) {
                const ex = layerExtras || extras;
                if (!ex.gearDefs) {
                    ex.gearDefs = [
                        { cx: 0.35, cy: 0.4, r: 0.16, teeth: 12, dir: 1 },
                        { cx: 0.62, cy: 0.38, r: 0.13, teeth: 10, dir: -1 },
                        { cx: 0.48, cy: 0.65, r: 0.10, teeth: 8, dir: 1 },
                        { cx: 0.25, cy: 0.68, r: 0.08, teeth: 6, dir: -1 },
                        { cx: 0.72, cy: 0.62, r: 0.09, teeth: 7, dir: 1 },
                    ];
                    ex.gearAngles = ex.gearDefs.map(() => 0);
                }
                const minDim = Math.min(canvas.width, canvas.height);

                // Draw each gear
                for (let g = 0; g < ex.gearDefs.length; g++) {
                    const gd = ex.gearDefs[g];
                    const gcx = gd.cx * canvas.width;
                    const gcy = gd.cy * canvas.height;
                    const gr = gd.r * minDim;
                    const col = shiftAccent(accent, (g - 2) * 20);

                    // Rotate gear angle
                    ex.gearAngles[g] += gd.dir * (0.003 + (1 / gd.teeth) * 0.008);
                    const rot = ex.gearAngles[g];

                    // Gear teeth outline
                    const toothH = gr * 0.15;
                    const toothW = (Math.PI * 2) / gd.teeth * 0.35;
                    ctx.beginPath();
                    for (let t = 0; t < gd.teeth; t++) {
                        const a = rot + (t / gd.teeth) * Math.PI * 2;
                        // Outer tooth corners
                        const a1 = a - toothW, a2 = a + toothW;
                        const outerR = gr + toothH;
                        // Valley between teeth
                        const va = a + (1 / gd.teeth) * Math.PI;

                        if (t === 0) {
                            ctx.moveTo(gcx + Math.cos(a1) * outerR, gcy + Math.sin(a1) * outerR);
                        }
                        ctx.lineTo(gcx + Math.cos(a2) * outerR, gcy + Math.sin(a2) * outerR);
                        // Down to rim
                        const nextA = rot + ((t + 1) / gd.teeth) * Math.PI * 2 - toothW;
                        ctx.lineTo(gcx + Math.cos(a2) * gr, gcy + Math.sin(a2) * gr);
                        ctx.lineTo(gcx + Math.cos(nextA) * gr, gcy + Math.sin(nextA) * gr);
                        ctx.lineTo(gcx + Math.cos(nextA) * outerR, gcy + Math.sin(nextA) * outerR);
                    }
                    ctx.closePath();
                    ctx.strokeStyle = `rgba(${col}, 0.06)`;
                    ctx.lineWidth = 0.8;
                    ctx.stroke();

                    // Inner ring
                    ctx.beginPath();
                    ctx.arc(gcx, gcy, gr * 0.4, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(${col}, 0.05)`;
                    ctx.lineWidth = 0.8;
                    ctx.stroke();

                    // Spokes
                    for (let s = 0; s < 4; s++) {
                        const sa = rot + s * Math.PI * 0.5;
                        ctx.beginPath();
                        ctx.moveTo(gcx + Math.cos(sa) * gr * 0.15, gcy + Math.sin(sa) * gr * 0.15);
                        ctx.lineTo(gcx + Math.cos(sa) * gr * 0.4, gcy + Math.sin(sa) * gr * 0.4);
                        ctx.strokeStyle = `rgba(${col}, 0.04)`;
                        ctx.lineWidth = 1;
                        ctx.stroke();
                    }

                    // Center axle
                    const ag = ctx.createRadialGradient(gcx, gcy, 0, gcx, gcy, gr * 0.12);
                    ag.addColorStop(0, `rgba(${col}, 0.12)`);
                    ag.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(gcx, gcy, gr * 0.12, 0, Math.PI * 2);
                    ctx.fillStyle = ag;
                    ctx.fill();

                    // Axle dot
                    ctx.beginPath();
                    ctx.arc(gcx, gcy, 2, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(${col}, 0.25)`;
                    ctx.fill();
                }
            }
        },

        // ── HELP — fireflies / lanterns illuminating the dark ──
        help: {
            count: 30,
            init(p, i) {
                p.x = Math.random() * canvas.width;
                p.y = Math.random() * canvas.height;
                p.vx = (Math.random() - 0.5) * 0.3;
                p.vy = (Math.random() - 0.5) * 0.3;
                p.phase = Math.random() * Math.PI * 2;
                p.hueShift = (Math.random() - 0.5) * 40;
                // Fireflies blink — each has its own rhythm
                p.blinkSpeed = 0.6 + Math.random() * 1.2;
                p.blinkOffset = Math.random() * Math.PI * 2;
                p.radius = 1.5 + Math.random() * 1.5;
                // Lanterns are bigger, steadier, brighter
                p.isLantern = i < 6;
                if (p.isLantern) {
                    p.radius = 3 + Math.random() * 2;
                    p.blinkSpeed = 0.2 + Math.random() * 0.3; // slower pulse
                    p.vx *= 0.4;
                    p.vy *= 0.4;
                }
                // Wander target — fireflies change direction
                p.wanderAngle = Math.random() * Math.PI * 2;
                p.wanderTimer = 0;
            },
            update(p) {
                // Wander — change direction periodically
                p.wanderTimer++;
                if (p.wanderTimer > 80 + Math.random() * 60) {
                    p.wanderTimer = 0;
                    p.wanderAngle += (Math.random() - 0.5) * 2;
                }
                const wander = p.isLantern ? 0.003 : 0.008;
                p.vx += Math.cos(p.wanderAngle) * wander;
                p.vy += Math.sin(p.wanderAngle) * wander;
                // Dampen
                p.vx *= 0.98;
                p.vy *= 0.98;
                p.x += p.vx;
                p.y += p.vy;
                // Soft bounds
                if (p.x < 30) p.wanderAngle = 0;
                if (p.x > canvas.width - 30) p.wanderAngle = Math.PI;
                if (p.y < 30) p.wanderAngle = Math.PI * 0.5;
                if (p.y > canvas.height - 30) p.wanderAngle = -Math.PI * 0.5;
                p.x = Math.max(10, Math.min(canvas.width - 10, p.x));
                p.y = Math.max(10, Math.min(canvas.height - 10, p.y));
            },
            draw(p, ctx, accent, time) {
                const col = shiftAccent(accent, p.hueShift);

                if (p.isLantern) {
                    // Lantern — large steady warm glow
                    const pulse = 0.7 + 0.3 * Math.sin(time * p.blinkSpeed + p.blinkOffset);

                    // Big light pool
                    const poolR = p.radius * 12;
                    const pool = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, poolR);
                    pool.addColorStop(0, `rgba(${col}, ${0.06 * pulse})`);
                    pool.addColorStop(0.3, `rgba(${col}, ${0.03 * pulse})`);
                    pool.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, poolR, 0, Math.PI * 2);
                    ctx.fillStyle = pool;
                    ctx.fill();

                    // Bright core glow
                    const coreR = p.radius * 4;
                    const core = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, coreR);
                    core.addColorStop(0, `rgba(${col}, ${0.3 * pulse})`);
                    core.addColorStop(0.4, `rgba(${col}, ${0.12 * pulse})`);
                    core.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, coreR, 0, Math.PI * 2);
                    ctx.fillStyle = core;
                    ctx.fill();

                    // Center bright point
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, p.radius * 0.6, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(255, 255, 255, ${0.15 * pulse})`;
                    ctx.fill();

                    ctx.beginPath();
                    ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(${col}, ${0.35 * pulse})`;
                    ctx.fill();
                } else {
                    // Firefly — blinks on and off
                    const raw = Math.sin(time * p.blinkSpeed + p.blinkOffset);
                    const blink = Math.pow(Math.max(0, raw), 3); // sharp on, quick off

                    if (blink > 0.01) {
                        // Light pool when lit
                        const poolR = p.radius * 8 * blink;
                        const pool = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, poolR);
                        pool.addColorStop(0, `rgba(${col}, ${0.12 * blink})`);
                        pool.addColorStop(0.5, `rgba(${col}, ${0.04 * blink})`);
                        pool.addColorStop(1, 'rgba(0,0,0,0)');
                        ctx.beginPath();
                        ctx.arc(p.x, p.y, poolR, 0, Math.PI * 2);
                        ctx.fillStyle = pool;
                        ctx.fill();

                        // Bright core
                        ctx.beginPath();
                        ctx.arc(p.x, p.y, p.radius * (0.5 + blink * 0.5), 0, Math.PI * 2);
                        ctx.fillStyle = `rgba(${col}, ${0.2 + blink * 0.5})`;
                        ctx.fill();

                        // White center flash
                        if (blink > 0.5) {
                            ctx.beginPath();
                            ctx.arc(p.x, p.y, p.radius * 0.3, 0, Math.PI * 2);
                            ctx.fillStyle = `rgba(255,255,255, ${(blink - 0.5) * 0.5})`;
                            ctx.fill();
                        }
                    } else {
                        // Barely visible when dark
                        ctx.beginPath();
                        ctx.arc(p.x, p.y, p.radius * 0.5, 0, Math.PI * 2);
                        ctx.fillStyle = `rgba(${col}, 0.04)`;
                        ctx.fill();
                    }
                }
            },
            initExtras() {
                extras.trails = {};
            },
            drawGlobal(ctx, parts, accent, time, layerExtras) {
                const ex = layerExtras || extras;
                if (!ex.trails) ex.trails = {};

                // Faint trails behind fireflies — short luminous paths
                for (let i = 0; i < parts.length; i++) {
                    const p = parts[i];
                    if (p.isLantern) continue;
                    const key = i;
                    if (!ex.trails[key]) ex.trails[key] = [];
                    ex.trails[key].push({ x: p.x, y: p.y });
                    if (ex.trails[key].length > 10) ex.trails[key].shift();

                    const raw = Math.sin(time * p.blinkSpeed + p.blinkOffset);
                    const blink = Math.pow(Math.max(0, raw), 3);
                    if (blink < 0.1) continue;

                    const trail = ex.trails[key];
                    const col = shiftAccent(accent, p.hueShift);
                    for (let t = 0; t < trail.length - 1; t++) {
                        const frac = t / trail.length;
                        ctx.beginPath();
                        ctx.moveTo(trail[t].x, trail[t].y);
                        ctx.lineTo(trail[t + 1].x, trail[t + 1].y);
                        ctx.strokeStyle = `rgba(${col}, ${frac * 0.06 * blink})`;
                        ctx.lineWidth = frac * 1.5;
                        ctx.lineCap = 'round';
                        ctx.stroke();
                    }
                }
            }
        },

        // ── NONE — fallback for pages without a preset ──
        none: {
            count: 0,
            init() {},
            update() {},
            draw() {}
        }
    };

    // ── Transition System ──
    // Phases: 'normal' | 'converge' | 'burst'
    // converge: old particles move toward center, globalDraw fades out
    // burst: new particles move from center to target positions
    // normal: standard preset behavior

    const TRANSITION_SPEED = 0.025; // 0→1 per frame, ~40 frames = ~0.67s

    let currentLayer = null;     // { preset, particles, extras, name }
    let transitionState = null;  // null or { phase, progress, oldParticles, newLayer, cx, cy }

    function initLayer(presetName) {
        const preset = PRESETS[presetName] || PRESETS.none;
        const parts = [];
        const layerExtras = {};
        resize();
        for (let i = 0; i < preset.count; i++) {
            const p = {};
            preset.init(p, i);
            parts.push(p);
        }
        if (preset.initExtras) {
            const saved = extras;
            extras = layerExtras;
            preset.initExtras();
            extras = saved;
        }
        return { preset, particles: parts, extras: layerExtras, name: presetName };
    }

    function loop() {
        animFrame = requestAnimationFrame(loop);

        const w = canvas.width, h = canvas.height;
        if (w === 0 || h === 0) { resize(); return; }

        ctx.clearRect(0, 0, w, h);
        const accent = getAccentRGB();
        const time = Date.now() * 0.001;
        frameCount++;

        const cx = w * 0.5, cy = h * 0.5;

        if (transitionState) {
            const ts = transitionState;
            ts.progress = Math.min(1, ts.progress + TRANSITION_SPEED);

            if (ts.phase === 'converge') {
                // Draw old particles converging to center
                const t = ts.progress;
                const ease = t * t; // ease-in
                const alpha = 1 - t * 0.5; // fade slightly

                ctx.globalAlpha = alpha;
                for (const p of ts.oldParticles) {
                    // Lerp from current position toward center
                    const dx = cx - p.ox;
                    const dy = cy - p.oy;
                    p.x = p.ox + dx * ease;
                    p.y = p.oy + dy * ease;
                }

                // Draw with old preset (no global effects during converge — faded)
                const oldPreset = ts.oldPreset;
                if (oldPreset) {
                    ctx.globalAlpha = alpha * (1 - ease * 0.8);
                    if (oldPreset.drawGlobal && ts.oldExtras) {
                        oldPreset.drawGlobal(ctx, ts.oldParticles, accent, time, ts.oldExtras);
                    }
                    ctx.globalAlpha = alpha;
                    for (const p of ts.oldParticles) {
                        oldPreset.draw(p, ctx, accent, time);
                    }
                }
                ctx.globalAlpha = 1;

                // Center glow builds as particles converge
                const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, 30 + ease * 20);
                cg.addColorStop(0, `rgba(${accent}, ${ease * 0.4})`);
                cg.addColorStop(0.5, `rgba(${accent}, ${ease * 0.15})`);
                cg.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(cx, cy, 30 + ease * 20, 0, Math.PI * 2);
                ctx.fillStyle = cg;
                ctx.fill();

                if (ts.progress >= 1) {
                    // Switch to burst phase
                    ts.phase = 'burst';
                    ts.progress = 0;
                    // Store target positions for new particles
                    currentLayer = ts.newLayer;
                    for (const p of currentLayer.particles) {
                        p.tx = p.x;
                        p.ty = p.y;
                        p.x = cx;
                        p.y = cy;
                    }
                }

            } else if (ts.phase === 'burst') {
                const t = ts.progress;
                const ease = 1 - Math.pow(1 - t, 3); // ease-out

                // Center flash that fades
                const flashAlpha = Math.max(0, (1 - t) * 0.5);
                if (flashAlpha > 0.01) {
                    const fg = ctx.createRadialGradient(cx, cy, 0, cx, cy, 50);
                    fg.addColorStop(0, `rgba(${accent}, ${flashAlpha})`);
                    fg.addColorStop(0.4, `rgba(${accent}, ${flashAlpha * 0.3})`);
                    fg.addColorStop(1, 'rgba(0,0,0,0)');
                    ctx.beginPath();
                    ctx.arc(cx, cy, 50, 0, Math.PI * 2);
                    ctx.fillStyle = fg;
                    ctx.fill();
                }

                // Lerp new particles from center to target
                const layer = currentLayer;
                for (const p of layer.particles) {
                    p.x = cx + (p.tx - cx) * ease;
                    p.y = cy + (p.ty - cy) * ease;
                }

                // Draw new preset (global effects fade in)
                ctx.globalAlpha = ease;
                if (layer.preset.drawGlobal) {
                    layer.preset.drawGlobal(ctx, layer.particles, accent, time, layer.extras);
                }
                ctx.globalAlpha = 0.5 + ease * 0.5; // particles visible earlier
                for (const p of layer.particles) {
                    layer.preset.draw(p, ctx, accent, time);
                }
                ctx.globalAlpha = 1;

                if (ts.progress >= 1) {
                    // Restore true positions and enter normal
                    for (const p of layer.particles) {
                        p.x = p.tx;
                        p.y = p.ty;
                        delete p.tx;
                        delete p.ty;
                    }
                    transitionState = null;
                }
            }

        } else if (currentLayer && currentLayer.preset.count > 0) {
            // Normal rendering
            const layer = currentLayer;
            for (let i = 0; i < layer.particles.length; i++) {
                layer.preset.update(layer.particles[i], time, i);
            }
            if (layer.preset.drawGlobal) {
                layer.preset.drawGlobal(ctx, layer.particles, accent, time, layer.extras);
            }
            for (let i = 0; i < layer.particles.length; i++) {
                layer.preset.draw(layer.particles[i], ctx, accent, time);
            }
        } else {
            // Nothing to draw — stop loop
            cancelAnimationFrame(animFrame);
            animFrame = null;
            ctx.clearRect(0, 0, w, h);
            return;
        }
    }

    // ── Public API ──

    function setPreset(name) {
        // Already showing this preset? Skip.
        if (currentLayer && currentLayer.name === name) return;

        const newLayer = initLayer(name);
        const isNone = (PRESETS[name] || PRESETS.none).count === 0;

        if (currentLayer && currentLayer.preset.count > 0 && !isNone) {
            // Transition: converge old → burst new
            // Snapshot old particle positions
            const oldParticles = currentLayer.particles.map(p => ({ ...p, ox: p.x, oy: p.y }));
            transitionState = {
                phase: 'converge',
                progress: 0,
                oldParticles,
                oldPreset: currentLayer.preset,
                oldExtras: currentLayer.extras,
                newLayer
            };
        } else if (!isNone) {
            // No old layer (first load or from 'none') — burst from center
            currentLayer = newLayer;
            const cx = canvas.width * 0.5, cy = canvas.height * 0.5;
            for (const p of currentLayer.particles) {
                p.tx = p.x;
                p.ty = p.y;
                p.x = cx;
                p.y = cy;
            }
            transitionState = {
                phase: 'burst',
                progress: 0,
                oldParticles: [],
                oldPreset: null,
                oldExtras: null,
                newLayer: currentLayer
            };
        } else {
            // Switching to 'none' — just fade out
            if (currentLayer) {
                // Quick converge to center then stop
                const oldParticles = currentLayer.particles.map(p => ({ ...p, ox: p.x, oy: p.y }));
                transitionState = {
                    phase: 'converge',
                    progress: 0,
                    oldParticles,
                    oldPreset: currentLayer.preset,
                    oldExtras: currentLayer.extras,
                    newLayer: initLayer('none')
                };
            }
            currentLayer = null;
        }

        // Ensure loop is running
        if (!animFrame) loop();
    }

    function stop() {
        if (animFrame) {
            cancelAnimationFrame(animFrame);
            animFrame = null;
        }
        currentLayer = null;
        transitionState = null;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    // Page ID → preset name mapping
    const PAGE_PRESETS = {
        dashboard: 'dashboard',
        sync: 'sync',
        downloads: 'search',
        discover: 'discover',
        artists: 'artists',
        automations: 'automations',
        library: 'library',
        import: 'import',
        settings: 'settings',
        help: 'help',
    };

    // Listen for page changes from script.js
    window.pageParticles = {
        setPage(pageId) {
            const presetName = PAGE_PRESETS[pageId] || 'none';
            setPreset(presetName);
        },
        stop
    };

    // Auto-start for initial page
    requestAnimationFrame(() => {
        const activePage = document.querySelector('.page.active');
        if (activePage) {
            const pageId = activePage.id.replace('-page', '');
            window.pageParticles.setPage(pageId);
        }
    });

})();
