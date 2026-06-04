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
        { container: '.mb-button-container',              color: [186, 85, 211], id: 'musicbrainz' },
        { container: '.audiodb-button-container',          color: [0, 188, 212],  id: 'audiodb' },
        { container: '.deezer-button-container',           color: [162, 56, 255], id: 'deezer' },
        { container: '.spotify-enrich-button-container',   color: [30, 215, 96],  id: 'spotify-enrichment' },
        { container: '.itunes-enrich-button-container',    color: [251, 91, 137], id: 'itunes-enrichment' },
        { container: '.lastfm-enrich-button-container',    color: [213, 16, 7],   id: 'lastfm-enrichment' },
        { container: '.genius-enrich-button-container',    color: [255, 255, 100], id: 'genius-enrichment' },
        { container: '.tidal-enrich-button-container',     color: [180, 180, 255], id: 'tidal-enrichment' },
        { container: '.qobuz-enrich-button-container',     color: [1, 112, 239],  id: 'qobuz-enrichment' },
        { container: '.discogs-button-container',          color: [180, 180, 180], id: 'discogs' },
        { container: '.amazon-enrich-button-container',    color: [255, 153, 0],  id: 'amazon-enrichment' },
        { container: '.similar-artists-enrich-button-container', color: [168, 85, 247], id: 'similar_artists' },
        { container: '.hydrabase-button-container',        color: [200, 200, 200], id: 'hydrabase' },
        { container: '.soulid-button-container',          color: [29, 185, 84], rainbow: true, id: 'soulid' },
        { container: '.repair-button-container',           color: [180, 130, 255], rainbow: true, id: 'repair' },
        { container: '.em-manage-btn',                     color: [168, 85, 247], hub: true },
    ];

    const ERROR_COLOR = [255, 80, 80];   // pulses fired on real worker errors
    const PULSE_CAP = 8;                  // max pulses queued per status update
    const PULSE_DRAIN = 2;                // pulses released into flight per frame

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
                btn: el.matches('button') ? el : el.querySelector('button'),
                id: def.id || null,
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
                statusSeen: false,    // has a real WS status arrived for this worker?
                lastProcessed: 0,     // cumulative matched+not_found seen last update
                lastErrors: 0,        // cumulative error count seen last update
                pendingWork: 0,       // brand-colour pulses queued to fly to the hub
                pendingErr: 0,        // red pulses queued (real errors)
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

    // ── Glow sprite cache ──
    // Radial gradients are the expensive part of canvas glows. Bake one soft
    // glow sprite per colour into an offscreen canvas and blit it with
    // drawImage — a single cheap GPU copy instead of allocating a gradient
    // every frame. Colours are quantised to 8-step buckets to bound the cache
    // (the tint shift is imperceptible in a glow, and keeps the rainbow path
    // from minting a new sprite every frame).
    const GLOW_SIZE = 64;
    const _glowCache = new Map();

    function getGlowSprite(r, g, b) {
        const qr = r & ~7, qg = g & ~7, qb = b & ~7;
        const key = (qr << 16) | (qg << 8) | qb;
        let spr = _glowCache.get(key);
        if (spr) return spr;

        spr = document.createElement('canvas');
        spr.width = spr.height = GLOW_SIZE;
        const gctx = spr.getContext('2d');
        const c = GLOW_SIZE / 2;
        const grad = gctx.createRadialGradient(c, c, 0, c, c, c);
        grad.addColorStop(0, `rgba(${qr}, ${qg}, ${qb}, 1)`);
        grad.addColorStop(1, `rgba(${qr}, ${qg}, ${qb}, 0)`);
        gctx.fillStyle = grad;
        gctx.fillRect(0, 0, GLOW_SIZE, GLOW_SIZE);

        _glowCache.set(key, spr);
        return spr;
    }

    // Blit a cached glow of the given radius/alpha centred at (x, y)
    function drawGlow(ctx, x, y, radius, r, g, b, alpha) {
        if (alpha <= 0 || radius <= 0) return;
        ctx.globalAlpha = alpha;
        ctx.drawImage(getGlowSprite(r, g, b), x - radius, y - radius, radius * 2, radius * 2);
        ctx.globalAlpha = 1;
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

            // Spark glow (cached sprite)
            drawGlow(ctx, s.x, s.y, radius * 3, r, g, b, alpha * 0.4);

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

            drawGlow(ctx, x, y, radius * 3, r, g, b, alpha * 0.5);

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

        // Check active state every 30 frames (button ref is cached at init)
        if (frameCount % 30 === 0) {
            orbs.forEach(orb => {
                orb.visible = orb.el.offsetParent !== null;
                orb.active = orb.btn ? orb.btn.classList.contains('active') : false;
            });
        }

        const visibleOrbs = orbs.filter(o => o.visible);
        const hub = visibleOrbs.find(o => o.hub);

        if (state === 'orbs' || state === 'collapsing') {
            updatePhysics(visibleOrbs, w, h);
        } else if (state === 'expanding') {
            updateExpanding(visibleOrbs, w, h);
        }

        // Sparks (ambient aura while active) + inbound pulses to the hub.
        // Pulses are event-driven: one per real item matched / error reported,
        // drained a couple per frame so bursts stagger nicely up the spoke.
        for (const orb of visibleOrbs) {
            if (orb.hub) continue;

            if (orb.active && Math.random() < SPARK_RATE) {
                emitSpark(orb, orb.rainbow ? getRainbowColor(time) : null);
            }

            if (!hub) continue;

            if (orb.statusSeen) {
                let drained = 0;
                while (orb.pendingWork > 0 && drained < PULSE_DRAIN) {
                    emitInflow(orb, orb.rainbow ? getRainbowColor(time) : null);
                    orb.pendingWork--; drained++;
                }
                while (orb.pendingErr > 0 && drained < PULSE_DRAIN) {
                    emitInflow(orb, ERROR_COLOR);
                    orb.pendingErr--; drained++;
                }
            } else if (orb.active && Math.random() < INFLOW_RATE) {
                // No real status yet — keep the old ambient trickle as fallback
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

                // Wide ambient glow — brighter + wider with energy (cached sprite)
                const glowR = hubR * (4 + energy * 1.5);
                drawGlow(ctx, orb.x, orb.y, glowR, r, g, b, 0.18 + energy * 0.18 + slow * 0.12);

                if (hubImageReady) {
                    // SoulSync logo as the nucleus — fit to the pulsing radius while
                    // preserving the image's natural aspect ratio (no stretch)
                    const natW = hubImage.naturalWidth || 1;
                    const natH = hubImage.naturalHeight || 1;
                    const fit = (hubR * 3.2) / Math.max(natW, natH);
                    const dw = natW * fit;
                    const dh = natH * fit;
                    ctx.save();
                    ctx.globalAlpha = Math.min(1, 0.85 + energy * 0.15 + slow * 0.1);
                    ctx.drawImage(hubImage, orb.x - dw / 2, orb.y - dh / 2, dw, dh);
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
            drawGlow(ctx, orb.x, orb.y, glowRadius, r, g, b, glowAlpha);

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

    // ── Real telemetry → pulses ──
    // Fed by the WebSocket enrichment status pushes (see core.js). We diff the
    // cumulative counters between updates and queue one inbound pulse per real
    // item processed (brand colour) or error (red). No status yet → the loop
    // falls back to an ambient trickle so active orbs still animate.
    function onStatus(id, data) {
        if (!id || !data) return;
        const orb = orbs.find(o => o.id === id);
        if (!orb) return;

        const s = data.stats || {};
        const num = (v) => (typeof v === 'number' && isFinite(v) ? v : 0);
        // "processed" = every flavour of completed item across the worker zoo
        const processed = num(s.matched) + num(s.not_found) + num(s.repaired)
                        + num(s.synced) + num(s.scanned);
        const errors = num(s.errors);

        if (!orb.statusSeen) {
            // First sample is just a baseline — don't dump the whole backlog
            orb.statusSeen = true;
            orb.lastProcessed = processed;
            orb.lastErrors = errors;
            return;
        }

        const dWork = processed - orb.lastProcessed;
        const dErr = errors - orb.lastErrors;
        orb.lastProcessed = processed;
        orb.lastErrors = errors;
        if (dWork > 0) orb.pendingWork = Math.min(PULSE_CAP, orb.pendingWork + dWork);
        if (dErr > 0) orb.pendingErr = Math.min(PULSE_CAP, orb.pendingErr + dErr);
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

        window.workerOrbs = { setPage, onStatus };

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
