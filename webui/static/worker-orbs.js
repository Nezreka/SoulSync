// ============================================================
// SoulSync — Worker Orbs
// Dashboard header buttons shrink to floating orbs, expand on hover
// ============================================================

(function () {
    'use strict';

    // Disable on mobile
    if (window.innerWidth <= 768 || /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent)) return;

    // ── Worker definitions with brand colors ──
    const WORKER_DEFS = [
        { container: '.mb-button-container',              color: [186, 85, 211] },
        { container: '.audiodb-button-container',          color: [0, 188, 212]  },
        { container: '.deezer-button-container',           color: [162, 56, 255] },
        { container: '.spotify-enrich-button-container',   color: [30, 215, 96]  },
        { container: '.itunes-enrich-button-container',    color: [251, 91, 137] },
        { container: '.lastfm-enrich-button-container',    color: [213, 16, 7]   },
        { container: '.genius-enrich-button-container',    color: [255, 255, 100] },
        { container: '.tidal-enrich-button-container',     color: [180, 180, 255] },
        { container: '.qobuz-enrich-button-container',     color: [1, 112, 239]  },
        { container: '.discogs-button-container',          color: [180, 180, 180] },
        { container: '.amazon-enrich-button-container',    color: [255, 153, 0]  },
        { container: '.similar-artists-enrich-button-container', color: [168, 85, 247] },
        { container: '.hydrabase-button-container',        color: [200, 200, 200] },
        { container: '.soulid-button-container',          color: [29, 185, 84], rainbow: true },
        { container: '.repair-button-container',           color: [180, 130, 255], rainbow: true },
        { container: '.em-manage-btn',                     color: [168, 85, 247], hub: true },
    ];

    const ORB_RADIUS = 7;
    const ORB_DIAMETER = ORB_RADIUS * 2;
    const CONNECTION_DIST = 70;
    const LERP_SPEED = 0.08;
    const EXPAND_STAGGER = 35;
    const MAX_SPARKS = 60;       // global spark pool cap
    const SPARK_RATE = 0.12;     // chance per frame per active orb to emit
    const MAX_INFLOWS = 48;      // hub inbound-pulse pool cap
    const INFLOW_RATE = 0.05;    // chance per frame per active orb to send a pulse inward

    let dashboardHeader = null;
    let headerActions = null;
    let canvas = null;
    let ctx = null;
    let orbs = [];
    let sparks = [];             // particle emissions from active orbs
    let inflows = [];            // pulses traveling from active orbs into the hub
    let state = 'idle';
    let animFrame = null;
    let onDashboard = false;
    let expandProgress = 0;
    let staggerTimers = [];
    let collapseDelay = null;
    const COLLAPSE_DELAY_MS = 7000;

    // SoulSync logo, drawn as the hub/nucleus once loaded
    let hubImage = null;
    let hubImageReady = false;

    // ── Init ──

    function init() {
        dashboardHeader = document.querySelector('#dashboard-page .dashboard-header');
        headerActions = document.querySelector('#dashboard-page .header-actions');
        if (!dashboardHeader || !headerActions) return;

        if (!hubImage) {
            hubImage = new Image();
            hubImage.onload = () => { hubImageReady = true; };
            hubImage.src = '/static/trans2.png';
        }

        canvas = document.createElement('canvas');
        canvas.className = 'worker-orb-canvas';
        canvas.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:5;';
        dashboardHeader.appendChild(canvas);
        ctx = canvas.getContext('2d');

        WORKER_DEFS.forEach((def, i) => {
            const el = headerActions.querySelector(def.container);
            if (!el) return;

            orbs.push({
                el,
                color: def.color,
                rainbow: def.rainbow || false,
                hub: def.hub || false,
                index: i,
                x: 0, y: 0,
                vx: (Math.random() - 0.5) * 0.6,
                vy: (Math.random() - 0.5) * 0.6,
                homeX: 0, homeY: 0,
                visible: true,
                phase: Math.random() * Math.PI * 2,
                active: false,
            });
        });

        computeHomes();
        scatterOrbs();

        dashboardHeader.addEventListener('mouseenter', onMouseEnter);
        dashboardHeader.addEventListener('mouseleave', onMouseLeave);
        window.addEventListener('resize', onResize);
        document.addEventListener('visibilitychange', onVisibility);
    }

    function computeHomes() {
        if (!dashboardHeader || !headerActions) return;
        const headerRect = dashboardHeader.getBoundingClientRect();

        orbs.forEach(orb => {
            const elRect = orb.el.getBoundingClientRect();
            orb.homeX = (elRect.left - headerRect.left) + elRect.width / 2;
            orb.homeY = (elRect.top - headerRect.top) + elRect.height / 2;
            orb.visible = orb.el.offsetParent !== null;
        });
    }

    function scatterOrbs() {
        if (!canvas) return;
        const w = canvas.clientWidth || 600;
        const h = canvas.clientHeight || 80;

        orbs.forEach(orb => {
            if (!orb.visible) return;
            orb.x = ORB_RADIUS + Math.random() * (w - ORB_DIAMETER);
            orb.y = ORB_RADIUS + Math.random() * (h - ORB_DIAMETER);
        });
    }

    function resizeCanvas() {
        if (!canvas) return;
        canvas.width = canvas.clientWidth;
        canvas.height = canvas.clientHeight;
    }

    // ── Rainbow color cycle (matches repair button's CSS rainbow) ──

    const RAINBOW = [
        [255, 0, 0],
        [255, 136, 0],
        [255, 255, 0],
        [0, 255, 0],
        [0, 136, 255],
        [136, 0, 255],
    ];

    function getRainbowColor(time) {
        const t = ((time * 0.33) % 1 + 1) % 1; // ~3s cycle to match CSS 3s
        const idx = t * RAINBOW.length;
        const i = Math.floor(idx);
        const f = idx - i;
        const a = RAINBOW[i % RAINBOW.length];
        const b = RAINBOW[(i + 1) % RAINBOW.length];
        return [
            Math.round(a[0] + (b[0] - a[0]) * f),
            Math.round(a[1] + (b[1] - a[1]) * f),
            Math.round(a[2] + (b[2] - a[2]) * f),
        ];
    }

    // ── Spark system ──

    function emitSpark(orb, colorOverride) {
        if (sparks.length >= MAX_SPARKS) return;
        const angle = Math.random() * Math.PI * 2;
        const speed = 0.4 + Math.random() * 0.8;
        sparks.push({
            x: orb.x,
            y: orb.y,
            vx: Math.cos(angle) * speed,
            vy: Math.sin(angle) * speed,
            life: 1.0,           // 1.0 → 0.0
            decay: 0.012 + Math.random() * 0.012,
            color: colorOverride || orb.color,
            radius: 1.5 + Math.random() * 1.5,
        });
    }

    function updateSparks() {
        for (let i = sparks.length - 1; i >= 0; i--) {
            const s = sparks[i];
            s.x += s.vx;
            s.y += s.vy;
            s.vx *= 0.98;
            s.vy *= 0.98;
            s.life -= s.decay;
            if (s.life <= 0) {
                sparks.splice(i, 1);
            }
        }
    }

    function drawSparks(ctx) {
        for (const s of sparks) {
            const [r, g, b] = s.color;
            const alpha = s.life * 0.6;
            const radius = s.radius * s.life;

            // Spark glow
            const glow = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, radius * 3);
            glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${alpha * 0.4})`);
            glow.addColorStop(1, 'rgba(0,0,0,0)');
            ctx.beginPath();
            ctx.arc(s.x, s.y, radius * 3, 0, Math.PI * 2);
            ctx.fillStyle = glow;
            ctx.fill();

            // Spark core
            ctx.beginPath();
            ctx.arc(s.x, s.y, radius, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
            ctx.fill();
        }
    }

    // ── Inbound pulses (active worker → hub) ──
    // Each carries an active worker's color into the nucleus, so the hub
    // visibly "collects" the output of whatever is running.

    function emitInflow(orb, color) {
        if (inflows.length >= MAX_INFLOWS) return;
        inflows.push({
            orb,                        // source orb (positions resolved live)
            color: color || orb.color,
            t: 0,                       // 0 at source → 1 at hub
            speed: 0.012 + Math.random() * 0.01,
        });
    }

    function updateInflows() {
        for (let i = inflows.length - 1; i >= 0; i--) {
            inflows[i].t += inflows[i].speed;
            if (inflows[i].t >= 1) inflows.splice(i, 1);
        }
    }

    function drawInflows(ctx, hub) {
        if (!hub) return;
        for (const p of inflows) {
            const [r, g, b] = p.color;
            // Ease toward hub so pulses accelerate as they arrive
            const e = p.t * p.t;
            const x = p.orb.x + (hub.x - p.orb.x) * e;
            const y = p.orb.y + (hub.y - p.orb.y) * e;
            const alpha = 0.55 * (1 - Math.abs(p.t - 0.5) * 0.6); // fade in/out at the ends
            const radius = 2.2;

            const glow = ctx.createRadialGradient(x, y, 0, x, y, radius * 3);
            glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${alpha * 0.5})`);
            glow.addColorStop(1, 'rgba(0,0,0,0)');
            ctx.beginPath();
            ctx.arc(x, y, radius * 3, 0, Math.PI * 2);
            ctx.fillStyle = glow;
            ctx.fill();

            ctx.beginPath();
            ctx.arc(x, y, radius, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
            ctx.fill();
        }
    }

    // ── State machine ──

    function enterOrbState() {
        if (state === 'orbs') return;
        state = 'orbs';
        expandProgress = 0;

        orbs.forEach(orb => {
            orb.el.classList.add('worker-orb-hidden');
        });

        canvas.style.opacity = '1';
        canvas.style.display = '';
        resizeCanvas();
        startLoop();
    }

    function enterExpandedState() {
        state = 'expanded';
        expandProgress = 1;

        clearStaggerTimers();
        orbs.forEach((orb, i) => {
            const t = setTimeout(() => {
                orb.el.classList.remove('worker-orb-hidden');
                orb.el.classList.add('worker-orb-reveal');
            }, i * EXPAND_STAGGER);
            staggerTimers.push(t);
        });

        canvas.style.opacity = '0';
        setTimeout(() => {
            if (state === 'expanded') {
                canvas.style.display = 'none';
                stopLoop();
            }
        }, 400);
    }

    function enterCollapsingState() {
        state = 'collapsing';
        clearStaggerTimers();

        const total = orbs.length;
        orbs.forEach((orb, i) => {
            const t = setTimeout(() => {
                orb.el.classList.remove('worker-orb-reveal');
                orb.el.classList.add('worker-orb-hidden');
            }, (total - 1 - i) * 20);
            staggerTimers.push(t);
        });

        canvas.style.display = '';
        canvas.style.opacity = '1';
        resizeCanvas();
        computeHomes();
        inflows = [];   // drop in-flight pulses; positions are about to jump
        orbs.forEach(orb => {
            orb.x = orb.homeX;
            orb.y = orb.homeY;
            orb.vx = (Math.random() - 0.5) * 0.4;
            orb.vy = (Math.random() - 0.5) * 0.4;
        });

        startLoop();

        setTimeout(() => {
            if (state === 'collapsing') {
                state = 'orbs';
                expandProgress = 0;
            }
        }, total * 20 + 100);
    }

    function clearStaggerTimers() {
        staggerTimers.forEach(t => clearTimeout(t));
        staggerTimers = [];
    }

    // ── Events ──

    function onMouseEnter() {
        if (!onDashboard) return;
        // Cancel any pending collapse
        if (collapseDelay) {
            clearTimeout(collapseDelay);
            collapseDelay = null;
        }
        if (state === 'orbs' || state === 'collapsing') {
            state = 'expanding';
            expandProgress = 0;
        }
    }

    function onMouseLeave() {
        if (!onDashboard) return;
        if (state === 'expanded' || state === 'expanding') {
            // Delay before collapsing back to orbs
            if (collapseDelay) clearTimeout(collapseDelay);
            collapseDelay = setTimeout(() => {
                collapseDelay = null;
                if (state === 'expanded' || state === 'expanding') {
                    enterCollapsingState();
                }
            }, COLLAPSE_DELAY_MS);
        }
    }

    function onResize() {
        computeHomes();
        resizeCanvas();
        const w = canvas ? canvas.width : 600;
        const h = canvas ? canvas.height : 80;
        orbs.forEach(orb => {
            orb.x = Math.max(ORB_RADIUS, Math.min(w - ORB_RADIUS, orb.x));
            orb.y = Math.max(ORB_RADIUS, Math.min(h - ORB_RADIUS, orb.y));
        });
    }

    function onVisibility() {
        if (document.hidden) {
            stopLoop();
        } else if (onDashboard && (state === 'orbs' || state === 'expanding' || state === 'collapsing')) {
            startLoop();
        }
    }

    // ── Animation loop ──

    let frameCount = 0;

    function startLoop() {
        if (animFrame) return;
        tick();
    }

    function stopLoop() {
        if (animFrame) {
            cancelAnimationFrame(animFrame);
            animFrame = null;
        }
    }

    function tick() {
        animFrame = requestAnimationFrame(tick);
        if (!canvas || !ctx) return;

        frameCount++;
        const time = frameCount / 60;
        const w = canvas.width;
        const h = canvas.height;

        if (w === 0 || h === 0) {
            resizeCanvas();
            return;
        }

        // Check active state every 30 frames
        if (frameCount % 30 === 0) {
            orbs.forEach(orb => {
                orb.visible = orb.el.offsetParent !== null;
                const btn = orb.el.querySelector('button');
                orb.active = btn ? btn.classList.contains('active') : false;
            });
        }

        const visibleOrbs = orbs.filter(o => o.visible);
        const hub = visibleOrbs.find(o => o.hub);

        if (state === 'orbs' || state === 'collapsing') {
            updatePhysics(visibleOrbs, w, h);
        } else if (state === 'expanding') {
            updateExpanding(visibleOrbs, w, h);
        }

        // Emit sparks from active orbs, plus pulses inbound to the hub
        for (const orb of visibleOrbs) {
            if (orb.hub || !orb.active) continue;
            if (Math.random() < SPARK_RATE) {
                emitSpark(orb, orb.rainbow ? getRainbowColor(time) : null);
            }
            if (hub && Math.random() < INFLOW_RATE) {
                emitInflow(orb, orb.rainbow ? getRainbowColor(time) : null);
            }
        }
        updateSparks();
        updateInflows();

        // Draw
        ctx.clearRect(0, 0, w, h);

        drawConnections(ctx, visibleOrbs, time);
        drawSparks(ctx);
        drawInflows(ctx, hub);
        drawOrbs(ctx, visibleOrbs, time);
    }

    // ── Physics ──

    function updatePhysics(visible, w, h) {
        const cx = w * 0.5;
        const cy = h * 0.5;

        for (const orb of visible) {
            // The hub is a nucleus — it settles at canvas center and stays put
            // while every worker orb drifts around it. No jitter, strong pull home.
            if (orb.hub) {
                orb.vx += (cx - orb.x) * 0.02;
                orb.vy += (cy - orb.y) * 0.02;
                orb.vx *= 0.85;
                orb.vy *= 0.85;
                orb.x += orb.vx;
                orb.y += orb.vy;
                continue;
            }

            // Active orbs drift faster
            const driftStrength = orb.active ? 0.04 : 0.02;
            orb.vx += (Math.random() - 0.5) * driftStrength;
            orb.vy += (Math.random() - 0.5) * driftStrength;

            // Subtle gravity toward center — keeps orbs loosely grouped
            const gx = cx - orb.x;
            const gy = cy - orb.y;
            const gDist = Math.sqrt(gx * gx + gy * gy);
            if (gDist > 1) {
                const gStrength = 0.004;
                orb.vx += (gx / gDist) * gStrength;
                orb.vy += (gy / gDist) * gStrength;

                // Orbital rotation — a tangential nudge (perpendicular to the
                // pull home) so the cluster slowly revolves around the nucleus
                // like electrons round an atom. Stronger when the orb is active.
                const tStrength = orb.active ? 0.008 : 0.005;
                orb.vx += (-gy / gDist) * tStrength;
                orb.vy += (gx / gDist) * tStrength;
            }

            // Damping
            orb.vx *= 0.993;
            orb.vy *= 0.993;

            // Speed cap — active orbs move a bit faster
            const maxSpeed = orb.active ? 0.8 : 0.5;
            const speed = Math.sqrt(orb.vx * orb.vx + orb.vy * orb.vy);
            if (speed > maxSpeed) {
                const scale = maxSpeed / speed;
                orb.vx *= scale;
                orb.vy *= scale;
            }

            // Soft repulsion from other orbs
            for (const other of visible) {
                if (other === orb) continue;
                const dx = orb.x - other.x;
                const dy = orb.y - other.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 35 && dist > 0.1) {
                    const force = 0.03 * (1 - dist / 35);
                    orb.vx += (dx / dist) * force;
                    orb.vy += (dy / dist) * force;
                }
            }

            // Move
            orb.x += orb.vx;
            orb.y += orb.vy;

            // Boundary bounce
            if (orb.x < ORB_RADIUS) { orb.x = ORB_RADIUS; orb.vx *= -0.7; }
            if (orb.x > w - ORB_RADIUS) { orb.x = w - ORB_RADIUS; orb.vx *= -0.7; }
            if (orb.y < ORB_RADIUS) { orb.y = ORB_RADIUS; orb.vy *= -0.7; }
            if (orb.y > h - ORB_RADIUS) { orb.y = h - ORB_RADIUS; orb.vy *= -0.7; }
        }
    }

    function updateExpanding(visible) {
        let allClose = true;

        for (const orb of visible) {
            const dx = orb.homeX - orb.x;
            const dy = orb.homeY - orb.y;
            orb.x += dx * LERP_SPEED;
            orb.y += dy * LERP_SPEED;

            orb.vx *= 0.9;
            orb.vy *= 0.9;

            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist > 3) allClose = false;
        }

        expandProgress = Math.min(1, expandProgress + 0.03);

        if (allClose || expandProgress >= 1) {
            enterExpandedState();
        }
    }

    // ── Drawing ──

    function drawOrbs(ctx, visible, time) {
        for (const orb of visible) {
            const [r, g, b] = orb.rainbow ? getRainbowColor(time) : orb.color;

            // ── The hub: an energy-reactive nucleus ──
            // Calm + dim when nothing's running; bigger, brighter and faster
            // the more workers are active. The animation reads as a gauge.
            if (orb.hub) {
                const workers = visible.filter(o => !o.hub);
                const activeCount = workers.filter(o => o.active).length;
                const energy = workers.length ? activeCount / workers.length : 0; // 0..1

                const beatSpeed = 1.0 + energy * 1.8;            // faster heartbeat when busy
                const slow = 0.5 + 0.5 * Math.sin(time * beatSpeed);
                const hubR = (ORB_RADIUS + 3 + energy * 4) + slow * (2 + energy * 2);

                // Wide ambient glow — brighter + wider with energy
                const glowR = hubR * (4 + energy * 1.5);
                const halo = ctx.createRadialGradient(orb.x, orb.y, 0, orb.x, orb.y, glowR);
                halo.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${0.18 + energy * 0.18 + slow * 0.12})`);
                halo.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.beginPath();
                ctx.arc(orb.x, orb.y, glowR, 0, Math.PI * 2);
                ctx.fillStyle = halo;
                ctx.fill();

                if (hubImageReady) {
                    // SoulSync logo as the nucleus — sized to the pulsing radius,
                    // brightness lifts a touch with energy
                    const imgSize = hubR * 3.2;
                    ctx.save();
                    ctx.globalAlpha = Math.min(1, 0.85 + energy * 0.15 + slow * 0.1);
                    ctx.drawImage(hubImage, orb.x - imgSize / 2, orb.y - imgSize / 2, imgSize, imgSize);
                    ctx.restore();
                } else {
                    // Fallback while the logo loads: solid bright core + highlight
                    ctx.beginPath();
                    ctx.arc(orb.x, orb.y, hubR, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${0.8 + energy * 0.15})`;
                    ctx.fill();
                    ctx.beginPath();
                    ctx.arc(orb.x, orb.y, hubR * 0.5, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(255, 255, 255, ${0.3 + energy * 0.25 + slow * 0.2})`;
                    ctx.fill();
                }

                // Expanding heartbeat ring(s) — radiate faster + brighter with energy
                const ringSpeed = 0.5 + energy * 0.9;
                const rings = 1 + Math.round(energy * 2);        // 1..3 rings
                for (let k = 0; k < rings; k++) {
                    const ringPhase = (time * ringSpeed + k / rings) % 1;
                    const ringR = hubR + ringPhase * hubR * 2.5;
                    ctx.beginPath();
                    ctx.arc(orb.x, orb.y, ringR, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${(1 - ringPhase) * (0.25 + energy * 0.2)})`;
                    ctx.lineWidth = 1.5;
                    ctx.stroke();
                }
                continue;
            }

            const pulse = 0.5 + 0.5 * Math.sin(time * 2 + orb.phase);

            // Active orbs are larger and breathe — size oscillates
            let baseRadius = orb.active ? ORB_RADIUS + 3 : ORB_RADIUS;
            if (orb.active) {
                baseRadius += 2 * Math.sin(time * 3 + orb.phase);
            }

            // Scale up during expand transition
            const currentRadius = state === 'expanding'
                ? baseRadius + expandProgress * 4
                : baseRadius;

            // Inactive orbs are dimmer
            const activeMult = orb.active ? 1.0 : 0.45;

            // Outer glow — much larger and brighter for active
            const glowRadius = orb.active ? currentRadius * 5 : currentRadius * 3;
            const glowAlpha = orb.active
                ? (0.25 + pulse * 0.2) * activeMult
                : (0.06 + pulse * 0.03) * activeMult;
            const glow = ctx.createRadialGradient(orb.x, orb.y, 0, orb.x, orb.y, glowRadius);
            glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${glowAlpha})`);
            glow.addColorStop(1, 'rgba(0,0,0,0)');
            ctx.beginPath();
            ctx.arc(orb.x, orb.y, glowRadius, 0, Math.PI * 2);
            ctx.fillStyle = glow;
            ctx.fill();

            // Core
            const coreAlpha = orb.active
                ? 0.85 + pulse * 0.15
                : (0.3 + pulse * 0.08) * activeMult;
            ctx.beginPath();
            ctx.arc(orb.x, orb.y, Math.max(1, currentRadius), 0, Math.PI * 2);
            ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${coreAlpha})`;
            ctx.fill();

            // Inactive: subtle border ring so they're visible against dark backgrounds
            if (!orb.active) {
                ctx.beginPath();
                ctx.arc(orb.x, orb.y, currentRadius, 0, Math.PI * 2);
                ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${0.3 + pulse * 0.1})`;
                ctx.lineWidth = 1;
                ctx.stroke();
            }

            // Active: expanding pulse ring that fades
            if (orb.active) {
                // Inner ring — tight, bright
                const ring1 = currentRadius + 2 + pulse * 3;
                ctx.beginPath();
                ctx.arc(orb.x, orb.y, ring1, 0, Math.PI * 2);
                ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${0.25 + pulse * 0.15})`;
                ctx.lineWidth = 1;
                ctx.stroke();

                // Outer ring — wide, faint, slower pulse
                const pulse2 = 0.5 + 0.5 * Math.sin(time * 1.2 + orb.phase + 1);
                const ring2 = currentRadius + 6 + pulse2 * 6;
                ctx.beginPath();
                ctx.arc(orb.x, orb.y, ring2, 0, Math.PI * 2);
                ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${0.06 + pulse2 * 0.06})`;
                ctx.lineWidth = 0.5;
                ctx.stroke();
            }
        }
    }

    function drawConnections(ctx, visible, time) {
        // Hub spokes — the nucleus is wired to every worker orb, full length,
        // so it always reads as the center that "manages" the cluster.
        const hub = visible.find(o => o.hub);
        if (hub) {
            const [hr, hg, hb] = hub.color;
            for (const orb of visible) {
                if (orb === hub) continue;
                const dx = hub.x - orb.x;
                const dy = hub.y - orb.y;
                const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                // Gentle traveling pulse along each spoke
                const flow = 0.5 + 0.5 * Math.sin(time * 2 - dist * 0.05);
                const alpha = 0.10 + flow * 0.10 + (orb.active ? 0.06 : 0);
                ctx.beginPath();
                ctx.moveTo(hub.x, hub.y);
                ctx.lineTo(orb.x, orb.y);
                ctx.strokeStyle = `rgba(${hr}, ${hg}, ${hb}, ${alpha})`;
                ctx.lineWidth = orb.active ? 1.0 : 0.6;
                ctx.stroke();
            }
        }

        for (let i = 0; i < visible.length; i++) {
            for (let j = i + 1; j < visible.length; j++) {
                const a = visible[i], b = visible[j];
                if (a.hub || b.hub) continue; // hub spokes handled above
                const dx = a.x - b.x;
                const dy = a.y - b.y;
                const dist = Math.sqrt(dx * dx + dy * dy);

                if (dist < CONNECTION_DIST) {
                    // Connections between active orbs are brighter
                    const activePair = a.active && b.active;
                    const anyActive = a.active || b.active;
                    const baseAlpha = activePair ? 0.3 : (anyActive ? 0.2 : 0.15);
                    const alpha = (1 - dist / CONNECTION_DIST) * baseAlpha;

                    const [r1, g1, b1] = a.rainbow ? getRainbowColor(time) : a.color;
                    const [r2, g2, b2] = b.rainbow ? getRainbowColor(time) : b.color;
                    const mr = (r1 + r2) >> 1;
                    const mg = (g1 + g2) >> 1;
                    const mb = (b1 + b2) >> 1;

                    ctx.beginPath();
                    ctx.moveTo(a.x, a.y);
                    ctx.lineTo(b.x, b.y);
                    ctx.strokeStyle = `rgba(${mr}, ${mg}, ${mb}, ${alpha})`;
                    ctx.lineWidth = anyActive ? 0.8 : 0.5;
                    ctx.stroke();
                }
            }
        }
    }

    // ── Page awareness ──

    function isEnabled() {
        return window._workerOrbsEnabled !== false;
    }

    function setPage(pageId) {
        const wasDashboard = onDashboard;
        onDashboard = (pageId === 'dashboard') && isEnabled();

        if (onDashboard && !wasDashboard) {
            computeHomes();
            resizeCanvas();
            sparks = [];
            enterOrbState();
        } else if (!onDashboard && wasDashboard) {
            if (collapseDelay) { clearTimeout(collapseDelay); collapseDelay = null; }
            stopLoop();
            state = 'idle';
            sparks = [];
            orbs.forEach(orb => {
                orb.el.classList.remove('worker-orb-hidden', 'worker-orb-reveal');
            });
            if (canvas) {
                canvas.style.display = 'none';
                canvas.style.opacity = '0';
            }
        }
    }

    // ── Bootstrap ──

    function bootstrap() {
        init();
        if (!dashboardHeader) return;

        window.workerOrbs = { setPage };

        const activePage = document.querySelector('.page.active');
        if (activePage && activePage.id === 'dashboard-page' && isEnabled()) {
            setTimeout(() => {
                computeHomes();
                resizeCanvas();
                enterOrbState();
                onDashboard = true;
            }, 300);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootstrap);
    } else {
        setTimeout(bootstrap, 100);
    }

})();
