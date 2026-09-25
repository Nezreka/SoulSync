// INITIALIZATION
// ===============================
let navigationEpoch = 0;
let _optimisticNavPageId = null;

// Schedule heavy per-page init during browser idle time so navigation paints and
// the page becomes scrollable first. timeout caps the delay so content still loads
// promptly. Falls back to a macrotask in browsers without requestIdleCallback.
function _scheduleHeavyInit(fn) {
    if (typeof requestIdleCallback === 'function') {
        requestIdleCallback(fn, { timeout: 200 });
    } else {
        setTimeout(fn, 0);
    }
}

function notifyPageWillChange(nextPageId) {
    const fromPageId = typeof currentPage === 'string' ? currentPage : null;
    if (fromPageId === nextPageId) return;

    window.dispatchEvent(
        new CustomEvent(PAGE_WILL_CHANGE_EVENT, {
            detail: {
                fromPageId,
                toPageId: nextPageId,
            },
        }),
    );
}

// ---- Accent Color System ----

function getAccentFallbackColors() {
    let accent = localStorage.getItem('soulsync-accent') || '#1db954';
    if (!/^#[0-9a-fA-F]{6}$/.test(accent)) accent = '#1db954';
    // Compute a lighter variant for the second color
    const r = parseInt(accent.slice(1, 3), 16), g = parseInt(accent.slice(3, 5), 16), b = parseInt(accent.slice(5, 7), 16);
    const lighter = '#' + [Math.min(r + 20, 255), Math.min(g + 30, 255), Math.min(b + 12, 255)]
        .map(v => v.toString(16).padStart(2, '0')).join('');
    return [accent, lighter];
}

function applyAccentColor(hex) {
    // Validate hex format — reject corrupt values
    if (typeof hex !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(hex)) {
        hex = '#1db954'; // fallback to default
    }
    // Convert hex to RGB
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);

    // Convert RGB to HSL
    const rn = r / 255, gn = g / 255, bn = b / 255;
    const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn);
    const l = (max + min) / 2;
    let h = 0, s = 0;
    if (max !== min) {
        const d = max - min;
        s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
        if (max === rn) h = ((gn - bn) / d + (gn < bn ? 6 : 0)) / 6;
        else if (max === gn) h = ((bn - rn) / d + 2) / 6;
        else h = ((rn - gn) / d + 4) / 6;
    }

    // Compute light variant: +16% lightness
    const lightL = Math.min(l + 0.16, 0.95);
    // Compute neon variant: high lightness + boosted saturation
    const neonL = Math.min(l + 0.30, 0.95);
    const neonS = Math.min(s + 0.1, 1.0);

    function hslToRgb(h, s, l) {
        if (s === 0) { const v = Math.round(l * 255); return [v, v, v]; }
        const hue2rgb = (p, q, t) => {
            if (t < 0) t += 1; if (t > 1) t -= 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        };
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        return [Math.round(hue2rgb(p, q, h + 1 / 3) * 255),
        Math.round(hue2rgb(p, q, h) * 255),
        Math.round(hue2rgb(p, q, h - 1 / 3) * 255)];
    }

    const light = hslToRgb(h, s, lightL);
    const neon = hslToRgb(h, neonS, neonL);

    const root = document.documentElement.style;
    root.setProperty('--accent-rgb', `${r}, ${g}, ${b}`);
    root.setProperty('--accent-light-rgb', `${light[0]}, ${light[1]}, ${light[2]}`);
    root.setProperty('--accent-neon-rgb', `${neon[0]}, ${neon[1]}, ${neon[2]}`);

    // Store for instant restore on next page load
    localStorage.setItem('soulsync-accent', hex);

    // Update preview swatch if it exists
    const swatch = document.getElementById('accent-preview-swatch');
    if (swatch) swatch.style.background = hex;
}

function applyParticlesSetting(enabled) {
    const canvas = document.getElementById('page-particles-canvas');
    if (canvas) canvas.style.display = enabled ? '' : 'none';
    if (window.pageParticles) {
        if (enabled) {
            // React-owned pages have no .page.active node — the shell's
            // currentPage covers both worlds.
            const activePage = document.querySelector('.page.active');
            const activeId = activePage
                ? activePage.id.replace('-page', '')
                : (typeof currentPage !== 'undefined' ? currentPage : null);
            if (activeId) {
                window.pageParticles.setPage(activeId);
            }
        } else {
            window.pageParticles.stop();
        }
    }
    window._particlesEnabled = enabled;
    localStorage.setItem('soulsync-particles', String(enabled));
}

function applyWorkerOrbsSetting(enabled) {
    window._workerOrbsEnabled = enabled;
    localStorage.setItem('soulsync-worker-orbs', String(enabled));
    if (window.workerOrbs) {
        if (enabled) {
            // The dashboard is React-rendered (no .page.active node) — the
            // shell's currentPage is the truth for both worlds.
            if (typeof currentPage !== 'undefined' && currentPage === 'dashboard') {
                window.workerOrbs.setPage('dashboard');
            }
        } else {
            window.workerOrbs.setPage('_disabled');
        }
    }
}

function initAccentColorListeners() {
    const presetSelect = document.getElementById('accent-preset');
    const customGroup = document.getElementById('custom-color-group');
    const customPicker = document.getElementById('accent-custom-color');
    if (!presetSelect) return;

    presetSelect.addEventListener('change', () => {
        const val = presetSelect.value;
        if (val === 'custom') {
            if (customGroup) customGroup.style.display = '';
            if (customPicker) applyAccentColor(customPicker.value);
        } else {
            if (customGroup) customGroup.style.display = 'none';
            applyAccentColor(val);
        }
    });

    if (customPicker) {
        customPicker.addEventListener('input', () => {
            applyAccentColor(customPicker.value);
        });
    }

    // Particles toggle — apply immediately on change
    const particlesCheckbox = document.getElementById('particles-enabled');
    if (particlesCheckbox) {
        particlesCheckbox.addEventListener('change', () => {
            applyParticlesSetting(particlesCheckbox.checked);
        });
    }

    // Worker orbs toggle — apply immediately on change
    const workerOrbsCheckbox = document.getElementById('worker-orbs-enabled');
    if (workerOrbsCheckbox) {
        workerOrbsCheckbox.addEventListener('change', () => {
            applyWorkerOrbsSetting(workerOrbsCheckbox.checked);
        });
    }

    // Reduce effects toggle — apply immediately on change
    const reduceEffectsCheckbox = document.getElementById('reduce-effects-enabled');
    if (reduceEffectsCheckbox) {
        reduceEffectsCheckbox.addEventListener('change', () => {
            applyReduceEffects(reduceEffectsCheckbox.checked);
        });
    }

    // Max Performance toggle — apply immediately on change
    const maxPerfCheckbox = document.getElementById('max-performance-enabled');
    if (maxPerfCheckbox) {
        maxPerfCheckbox.addEventListener('change', () => {
            applyMaxPerformance(maxPerfCheckbox.checked);
        });
    }
}

function applyReduceEffects(enabled) {
    if (enabled) {
        document.body.classList.add('reduce-effects');
    } else {
        document.body.classList.remove('reduce-effects');
    }
    window._reduceEffectsActive = enabled;
    localStorage.setItem('soulsync-reduce-effects', enabled ? '1' : '0');

    // Reduce Visual Effects is a full performance switch: also halt the canvas
    // animation loops (particles + worker orbs), not just CSS effects.
    const pcanvas = document.getElementById('page-particles-canvas');
    if (enabled) {
        if (window.pageParticles) window.pageParticles.stop();
        if (pcanvas) pcanvas.style.display = 'none';
        if (window.workerOrbs) window.workerOrbs.setPage('_disabled');
    } else {
        // Restore only what the user's own toggles still allow.
        const activePage = document.querySelector('.page.active');
        const activeId = activePage
            ? activePage.id.replace('-page', '')
            : (typeof currentPage !== 'undefined' ? currentPage : null);
        if (window._particlesEnabled !== false) {
            if (pcanvas) pcanvas.style.display = '';
            if (window.pageParticles && activeId) window.pageParticles.setPage(activeId);
        }
        if (window._workerOrbsEnabled !== false && window.workerOrbs && activeId) {
            window.workerOrbs.setPage(activeId);
        }
    }
}

// Max Performance overrides Worker Orbs / Particles / Reduce Effects, so while it's
// on we lock those checkboxes (greyed + visually off) and restore them when it's
// off. We never fire their change handlers, so the user's real saved prefs
// (window._workerOrbsEnabled / _particlesEnabled / the reduce-effects localStorage)
// stay intact — saving reads those, not these forced-off boxes.
function _syncMaxPerfDependentToggles(maxPerfOn) {
    const ids = ['worker-orbs-enabled', 'particles-enabled', 'reduce-effects-enabled'];
    ids.forEach(id => {
        const cb = document.getElementById(id);
        if (!cb) return;
        const group = cb.closest('.form-group');
        if (maxPerfOn) {
            cb.disabled = true;
            cb.checked = false;
            if (group) group.classList.add('setting-overridden');
        } else {
            cb.disabled = false;
            if (group) group.classList.remove('setting-overridden');
            // Restore each box to the user's real per-device preference.
            if (id === 'worker-orbs-enabled') cb.checked = window._workerOrbsEnabled !== false;
            else if (id === 'particles-enabled') cb.checked = window._particlesEnabled === true;
            else if (id === 'reduce-effects-enabled') cb.checked = localStorage.getItem('soulsync-reduce-effects') === '1';
        }
    });
}

// Max Performance — the nuclear low-power switch for software-rendered / no-GPU
// setups (e.g. Docker). Superset of Reduce Visual Effects: body.max-performance CSS
// kills the expensive GPU properties AND all animation/transitions, while here we
// halt every JS canvas loop (particles + worker orbs; cursor-glow + API sparks gate
// on window._maxPerfActive themselves).
function applyMaxPerformance(enabled) {
    if (enabled) {
        document.body.classList.add('max-performance');
    } else {
        document.body.classList.remove('max-performance');
    }
    window._maxPerfActive = enabled;
    localStorage.setItem('soulsync-max-performance', enabled ? '1' : '0');

    const pcanvas = document.getElementById('page-particles-canvas');
    if (enabled) {
        if (window.pageParticles) window.pageParticles.stop();
        if (pcanvas) pcanvas.style.display = 'none';
        if (window.workerOrbs) window.workerOrbs.setPage('_disabled');
    } else {
        // Restore whatever the user's own toggles (and reduce-effects) still allow.
        const reduce = window._reduceEffectsActive === true;
        const activePage = document.querySelector('.page.active');
        const activeId = activePage
            ? activePage.id.replace('-page', '')
            : (typeof currentPage !== 'undefined' ? currentPage : null);
        if (!reduce && window._particlesEnabled !== false) {
            if (pcanvas) pcanvas.style.display = '';
            if (window.pageParticles && activeId) window.pageParticles.setPage(activeId);
        }
        if (window._workerOrbsEnabled !== false && window.workerOrbs && activeId) {
            window.workerOrbs.setPage(activeId);
        }
    }
    _syncMaxPerfDependentToggles(enabled);
}

// Bootstrap accent and reduce-effects from localStorage instantly (prevents flash)
(function () {
    // Auto performance mode on likely-weak hardware. Only acts when this device has
    // NO stored preference yet (null) — so it runs at most once and never overrides
    // a choice the user (or a prior auto-run) made. Device-scoped via localStorage on
    // purpose: a weak laptop shouldn't flip the server setting for the user's other
    // machines. Mobile already disables these effects elsewhere, so skip it here.
    if (localStorage.getItem('soulsync-reduce-effects') === null) {
        const ua = navigator.userAgent || '';
        const isMobile = window.innerWidth <= 768 || /Mobi|Android|iPhone|iPad|iPod/i.test(ua);
        const cores = navigator.hardwareConcurrency || 0;   // widely supported
        const mem = navigator.deviceMemory || 0;            // Chromium only; 0 elsewhere
        // Conservative — avoid flagging capable machines: <=2 cores, or <=2GB, or a
        // low-mid box that's low on BOTH (<=4 cores AND <=4GB). A 4-core/8GB laptop
        // (mem>4) is NOT flagged; Firefox/Safari (mem unknown) only trip on <=2 cores.
        const weak = !isMobile && (
            (cores > 0 && cores <= 2) ||
            (mem > 0 && mem <= 2) ||
            (cores > 0 && cores <= 4 && mem > 0 && mem <= 4)
        );
        if (weak) {
            localStorage.setItem('soulsync-reduce-effects', '1');
            window._autoPerfModeApplied = true;   // show the explainer toast once the UI is up
        }
    }

    if (window._autoPerfModeApplied) {
        // Toast lives in downloads.js (loaded separately) — retry until it's defined.
        const fireToast = (tries) => {
            if (typeof showToast === 'function') {
                showToast('Performance mode is on — this looks like a lower-power device. ' +
                          'Turn effects back on in Settings → Appearance.', 'info');
            } else if (tries < 40) {
                setTimeout(() => fireToast(tries + 1), 250);
            }
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => fireToast(0));
        } else {
            fireToast(0);
        }
    }

    const reduceEffectsSaved = localStorage.getItem('soulsync-reduce-effects');
    if (reduceEffectsSaved === '1') {
        document.body.classList.add('reduce-effects');
        window._reduceEffectsActive = true;
    } else if (reduceEffectsSaved === '0') {
        document.body.classList.remove('reduce-effects');
        window._reduceEffectsActive = false;
    } else if (window._reduceEffectsActive) {
        document.body.classList.add('reduce-effects');
    }
    // Max Performance — device-scoped (localStorage wins over the server default,
    // same as reduce-effects). The window flag is seeded server-side in index.html
    // for a flash-free first paint; localStorage reconciles it here.
    const maxPerfSaved = localStorage.getItem('soulsync-max-performance');
    if (maxPerfSaved === '1') {
        document.body.classList.add('max-performance');
        window._maxPerfActive = true;
    } else if (maxPerfSaved === '0') {
        document.body.classList.remove('max-performance');
        window._maxPerfActive = false;
    } else if (window._maxPerfActive) {
        document.body.classList.add('max-performance');
    }
    const saved = localStorage.getItem('soulsync-accent');
    if (saved) applyAccentColor(saved);
    // Bootstrap particles setting from localStorage — OFF by default (continuous
    // full-page canvas = real GPU cost); only on when the user explicitly enabled it.
    const particlesSaved = localStorage.getItem('soulsync-particles');
    if (particlesSaved === 'true') {
        window._particlesEnabled = true;
    } else if (particlesSaved === 'false') {
        window._particlesEnabled = false;
    } else if (typeof window._particlesEnabled !== 'boolean') {
        window._particlesEnabled = false;
    }
    if (!window._particlesEnabled) {
        const canvas = document.getElementById('page-particles-canvas');
        if (canvas) canvas.style.display = 'none';
    }
    // Bootstrap worker orbs setting from localStorage
    const workerOrbsSaved = localStorage.getItem('soulsync-worker-orbs');
    if (workerOrbsSaved === 'false') {
        window._workerOrbsEnabled = false;
    } else if (workerOrbsSaved === 'true') {
        window._workerOrbsEnabled = true;
    } else if (typeof window._workerOrbsEnabled !== 'boolean') {
        window._workerOrbsEnabled = true;
    }
})();

async function bootstrapServerAppearanceSettings() {
    try {
        const response = await fetch('/api/settings', { credentials: 'same-origin' });
        const settings = await response.json();
        if (!response.ok || !settings || typeof settings !== 'object' || settings.error) return;

        const appearance = settings.ui_appearance || {};
        const preset = appearance.accent_preset || '#1db954';
        const custom = appearance.accent_color || '#1db954';
        const accent = preset === 'custom' ? custom : preset;
        applyAccentColor(accent);

        if (Object.prototype.hasOwnProperty.call(appearance, 'particles_enabled')) {
            applyParticlesSetting(appearance.particles_enabled !== false);
        }
        if (Object.prototype.hasOwnProperty.call(appearance, 'worker_orbs_enabled')) {
            applyWorkerOrbsSetting(appearance.worker_orbs_enabled !== false);
        }
        if (localStorage.getItem('soulsync-reduce-effects') === null) {
            applyReduceEffects(appearance.reduce_effects === true);
        }
    } catch (error) {
        console.warn('Could not bootstrap appearance settings:', error);
    }
}

bootstrapServerAppearanceSettings();

// ── Password-manager autofill suppression ──────────────────────────────
// Bitwarden / 1Password / LastPass etc. attach an inline autofill overlay to
// every <input>/<select>/<textarea> and REBUILD it on every DOM mutation. This
// app mutates the DOM continuously (live service status, download/automation
// progress bars, the per-second "next run" countdown, innerHTML hub rebuilds),
// so the managers' whole-document MutationObserver storms the main thread. A
// captured DevTools trace (2026-06-29) showed Bitwarden's
// bootstrap-autofill-overlay.js (setupOverlayOnField / setupOverlayListeners)
// using ~6× the CPU of the entire SoulSync app — almost the whole freeze.
//
// None of these fields are credentials (they're search boxes, filters, config),
// so we mark them ignored and the managers skip them: once a field carries the
// ignore hint, the overlay is never (re)attached, so the mutation→re-setup storm
// stops. Real sign-in fields (password type + the auth overlays) are left alone
// so the user can still autofill the login / PIN screen. Purely additive data-*
// attributes — no functional effect on the app, and a no-op for any manager that
// doesn't honour them.
(function suppressPasswordManagerAutofill() {
    const SKIP_CONTAINERS = ['#login-overlay', '#launch-pin-overlay', '#profile-pin-dialog'];
    const isCredentialField = (el) => {
        if (el.type === 'password') return true;
        return SKIP_CONTAINERS.some(sel => typeof el.closest === 'function' && el.closest(sel));
    };
    const IGNORE_ATTRS = ['data-bwignore', 'data-1p-ignore', 'data-lpignore', 'data-form-type'];
    const tag = (el) => {
        if (el.dataset.pmTagged) return;            // tagged once — never touch again
        if (isCredentialField(el)) return;          // leave real login fields for the manager
        el.dataset.pmTagged = '1';
        el.setAttribute('data-bwignore', 'true');   // Bitwarden
        el.setAttribute('data-1p-ignore', '');      // 1Password
        el.setAttribute('data-lpignore', 'true');   // LastPass
        el.setAttribute('data-form-type', 'other'); // Dashlane
        if (!el.hasAttribute('autocomplete')) el.setAttribute('autocomplete', 'off');
    };
    const sweep = () => {
        document.querySelectorAll(
            'input:not([data-pm-tagged]),textarea:not([data-pm-tagged]),select:not([data-pm-tagged])'
        ).forEach(tag);
    };

    // Debounce: a burst of DOM mutations triggers at most one sweep per idle slot.
    // The `:not([data-pm-tagged])` selector makes the steady-state sweep a no-op
    // (it only ever processes freshly-added inputs), and our own attribute writes
    // don't re-arm the observer (it watches childList, not attributes).
    let pending = false, observer = null, disabled = false;
    const scheduleSweep = () => {
        if (disabled || pending) return;
        pending = true;
        const run = () => { pending = false; if (!disabled) sweep(); };
        if (typeof requestIdleCallback === 'function') requestIdleCallback(run, { timeout: 400 });
        else setTimeout(run, 300);
    };

    const startObserving = () => {
        if (observer) return;
        observer = new MutationObserver(scheduleSweep);
        observer.observe(document.body, { childList: true, subtree: true });
    };
    const start = () => { sweep(); startObserving(); };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();

    // Benchmark hook (not used by the app): toggle the suppression at runtime so a
    // before/after can be measured without rebuilding. disable() strips the ignore
    // hints + stops the observer, so password managers re-attach their autofill
    // overlay — i.e. the pre-fix "before" behaviour. enable() re-tags + resumes.
    window.__pmSuppress = {
        disable() {
            disabled = true;
            if (observer) { observer.disconnect(); observer = null; }
            document.querySelectorAll('[data-pm-tagged]').forEach((el) => {
                IGNORE_ATTRS.forEach((a) => el.removeAttribute(a));
                delete el.dataset.pmTagged;
            });
        },
        enable() {
            disabled = false;
            sweep();
            startObserving();
        },
        get isActive() { return !disabled; },
    };
})();

// ── Profile System ─────────────────────────────────────────────
let currentProfile = null;
let profileLoginMode = false;
const PROFILE_CONTEXT_CHANGED_EVENT = 'ss:webui-profile-context-changed';

function notifyProfileContextChanged() {
    window.dispatchEvent(new CustomEvent(PROFILE_CONTEXT_CHANGED_EVENT));
}

function setCurrentProfile(profile) {
    currentProfile = profile;
    // Script-scoped let — unreachable from React modules, so the name is
    // mirrored (the window._socketConnected pattern). The dashboard's
    // hello strip greets by it.
    window._currentProfileName = (profile && profile.name) || '';
    updateProfileIndicator();
    notifyProfileContextChanged();
}

// the path the browser opened, read before react's "/" redirect rewrites it
const PF_BOOT_PATH = (window.SoulSyncURL?.strip(window.location.pathname) ?? window.location.pathname);

// with 2+ profiles every data api answers 401 profile_required to a browser
// that hasn't picked one. a page that's already open gets that when its
// profile is deleted: reload once so the picker shows. once a minute at
// most, so a server that keeps saying no can't spin us.
(function reloadWhenProfileIsGone() {
    const inner = window.fetch;
    if (typeof inner !== 'function') return;
    let reloading = false;
    window.fetch = function (...args) {
        return inner.apply(this, args).then((res) => {
            if (res && res.status === 401 && currentProfile && !reloading) {
                res.clone().json().then((body) => {
                    if (!body || !body.profile_required || reloading) return;
                    let last = 0;
                    try { last = Number(sessionStorage.getItem('ss_profile_required_reload')) || 0; } catch (e) { /* ignore */ }
                    if (Date.now() - last < 60000) return;
                    try { sessionStorage.setItem('ss_profile_required_reload', String(Date.now())); } catch (e) { /* ignore */ }
                    reloading = true;
                    window.location.reload();
                }).catch(() => { /* not json, not ours */ });
            }
            return res;
        });
    };
})();

// Temporary compatibility shim until existing profile rows are migrated to
// the current page ids.
const LEGACY_PROFILE_PAGE_ALIASES = {
    downloads: 'search',
    artists: 'search',
};

function normalizeProfilePageId(pageId) {
    return LEGACY_PROFILE_PAGE_ALIASES[pageId] || pageId;
}

function normalizeProfilePageList(pageIds) {
    if (!Array.isArray(pageIds)) return pageIds;
    return pageIds.map(normalizeProfilePageId);
}

// always a music page the profile can open: the react router and the denied
// page bounce both land here, so a home they can't see would loop or render
// the page they were refused. video homes are handled at boot
// (profileVideoHomePage).
function getProfileHomePage() {
    if (!currentProfile) return 'dashboard';
    const home = currentProfile.home_page ? normalizeProfilePageId(currentProfile.home_page) : '';
    if (home && !home.startsWith('video-') && isPageAllowed(home)) return home;
    if (currentProfile.is_admin) return 'dashboard';
    if (isPageAllowed('discover')) return 'discover';
    // discover is switched off for them: the first page they do have
    const firstAllowed = PROFILE_PAGE_GROUPS[0].pages.find(pageId => isPageAllowed(pageId));
    return firstAllowed || 'help';
}

// a video page picked as home, when the profile can still reach it
function profileVideoHomePage() {
    if (!currentProfile || !currentProfile.home_page) return '';
    const home = String(currentProfile.home_page);
    if (!home.startsWith('video-')) return '';
    if (profileAllowedSides() === 'music') return '';
    return isPageAllowed(home) ? home : '';
}

function isPageAllowed(pageId) {
    if (!currentProfile) return true;
    if (currentProfile.id === 1) return true;
    const normalizedPageId = normalizeProfilePageId(pageId);
    if (normalizedPageId === 'help' || normalizedPageId === 'issues') return true;
    // requests: admins answer them, profiles without download rights make them
    if (normalizedPageId === 'requests') return !!currentProfile.is_admin || !canDownload();
    if (normalizedPageId === 'settings') return currentProfile.is_admin;
    if (normalizedPageId === 'artist-detail') {
        const ap = normalizeProfilePageList(currentProfile.allowed_pages);
        if (!ap) return true;
        return ap.includes('library') || ap.includes('search');
    }
    if (normalizedPageId === 'label-detail') {
        const ap = normalizeProfilePageList(currentProfile.allowed_pages);
        if (!ap) return true;
        return ap.includes('search') || ap.includes('watchlist') || ap.includes('library');
    }
    const ap = normalizeProfilePageList(currentProfile.allowed_pages);
    if (!ap) return true; // null = all pages
    if (ap.includes(normalizedPageId)) return true;
    return false;
}

function canDownload() {
    if (!currentProfile) return true;
    if (currentProfile.id === 1) return true;
    return currentProfile.can_download !== false && currentProfile.can_download !== 0;
}

function getCurrentProfileContext() {
    if (!currentProfile) return null;
    return {
        profileId: currentProfile.id,
        isAdmin: !!currentProfile.is_admin,
        // who's signed in, for the My Account header
        name: currentProfile.name || '',
        avatarColor: currentProfile.avatar_color || '',
        avatarUrl: currentProfile.avatar_url || '',
    };
}

function activatePage(pageId, options = {}) {
    const forceReload = options.forceReload === true;
    const pageElement = document.getElementById(`${pageId}-page`);
    const isPageVisible = pageElement ? pageElement.classList.contains('active') : false;

    if (!forceReload && pageId === currentPage && isPageVisible) return;

    showLegacyPage(pageId);
    setActivePageChrome(pageId);
    loadPageData(pageId);
}

function renderProfileAvatar(el, profile) {
    // Renders avatar as image (if avatar_url set) or colored initial fallback
    // Preserves existing classes, ensures 'profile-avatar' is present
    if (!el.classList.contains('profile-avatar') && !el.classList.contains('profile-indicator-avatar') && !el.classList.contains('pf-avatar')) {
        el.className = 'profile-avatar';
    }
    const initial = String(profile.name || '?').charAt(0).toUpperCase();
    el.style.background = profile.avatar_color || '#6366f1';
    el.textContent = '';
    if (profile.avatar_url) {
        const img = document.createElement('img');
        img.src = profile.avatar_url;
        img.alt = '';
        img.className = 'profile-avatar-img';
        img.onerror = () => {
            img.remove();
            el.textContent = initial;
        };
        el.appendChild(img);
    } else {
        el.textContent = initial;
    }
}

async function initProfileSystem() {
    try {
        // Check if a session already has a profile selected
        const currentRes = await fetch('/api/profiles/current');
        const currentData = await currentRes.json();
        profileLoginMode = !!currentData.login_mode;
        // Login mode: show the sign-in screen and defer everything else until
        // the user authenticates.
        if (currentData.login_required) {
            showLoginScreen();
            return false;
        }
        if (currentData.success && currentData.profile) {
            setCurrentProfile(currentData.profile);

            // Login mode → reveal the Sign out button in the profile bar.
            if (currentData.login_mode) {
                const lb = document.getElementById('logout-btn');
                if (lb) lb.style.display = '';
            }

            // Check if launch PIN is required
            if (currentData.launch_pin_required) {
                showLaunchPinScreen();
                return false; // Defer app init until PIN verified
            }

            return true; // Profile already selected, skip picker
        }

        // Fetch all profiles
        const res = await fetch('/api/profiles');
        const data = await res.json();
        const profiles = data.profiles || [];

        if (profiles.length === 0) {
            // No profiles yet — auto-select admin profile 1
            await selectProfile(1);
            return true;
        }

        if (profiles.length === 1) {
            // Only one profile — always auto-select (PIN only matters with multiple profiles)
            await selectProfile(profiles[0].id);

            // Re-check for launch PIN after auto-select
            const recheck = await fetch('/api/profiles/current');
            const recheckData = await recheck.json();
            if (recheckData.launch_pin_required) {
                showLaunchPinScreen();
                return false;
            }

            return true;
        }

        // Multiple profiles or PIN required — show picker
        showProfilePicker(profiles);
        return false; // App init deferred until profile selected
    } catch (e) {
        console.error('Profile init error:', e);
        return true; // Fall through to normal init
    }
}

// ── Login Screen (username/password mode) ──────────────────────────────

function showLoginScreen() {
    const overlay = document.getElementById('login-overlay');
    if (!overlay) return;
    // Hide the entire app while locked, so removing the overlay (Safari "Hide
    // Distracting Items", devtools) reveals nothing — not even the empty chrome.
    // initApp() reveals it again on a successful sign-in (#852).
    document.body.classList.add('app-locked');
    overlay.style.display = 'flex';
    const u = document.getElementById('login-username');
    if (u) setTimeout(() => u.focus(), 50);
}

async function submitLogin() {
    const username = (document.getElementById('login-username')?.value || '').trim();
    const password = document.getElementById('login-password')?.value || '';
    const errEl = document.getElementById('login-error');
    const btn = document.getElementById('login-submit');
    const showErr = (msg) => { if (errEl) { errEl.textContent = msg; errEl.style.display = 'block'; } };
    if (errEl) errEl.style.display = 'none';
    if (!username || !password) { showErr('Enter your username and password'); return; }
    if (btn) { btn.disabled = true; btn.textContent = 'Signing in...'; }
    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        const data = await res.json();
        if (data.success) {
            window.location.reload();   // authenticated → reload into the app
        } else {
            showErr(res.status === 429 ? 'Too many attempts — wait a moment.' : (data.error || 'Sign in failed'));
            if (btn) { btn.disabled = false; btn.textContent = 'Sign in'; }
        }
    } catch (e) {
        showErr('Connection error');
        if (btn) { btn.disabled = false; btn.textContent = 'Sign in'; }
    }
}

async function soulsyncLogout() {
    try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) { /* reload anyway */ }
    window.location.reload();
}

function showLoginRecovery() {
    const entry = document.getElementById('login-entry');
    const rec = document.getElementById('login-recovery');
    if (entry) entry.style.display = 'none';
    if (rec) rec.style.display = 'block';
    const u = document.getElementById('recovery-username');
    const lu = document.getElementById('login-username');
    if (u && lu && lu.value) u.value = lu.value;
    const errEl = document.getElementById('recovery-error');
    if (errEl) errEl.style.display = 'none';
}

function showLoginEntry() {
    const entry = document.getElementById('login-entry');
    const rec = document.getElementById('login-recovery');
    if (rec) rec.style.display = 'none';
    if (entry) entry.style.display = 'block';
}

async function fetchRecoveryQuestion() {
    const username = (document.getElementById('recovery-username')?.value || '').trim();
    const errEl = document.getElementById('recovery-error');
    const section = document.getElementById('recovery-answer-section');
    const qText = document.getElementById('recovery-question-text');
    const showErr = (m) => { if (errEl) { errEl.textContent = m; errEl.style.display = 'block'; } };
    if (errEl) errEl.style.display = 'none';
    if (!username) { showErr('Enter your username'); return; }
    try {
        const res = await fetch('/api/auth/recovery-question?username=' + encodeURIComponent(username));
        const data = await res.json();
        if (data.success && data.question) {
            if (qText) qText.textContent = data.question;
            if (section) section.style.display = 'block';
        } else {
            showErr('No recovery question is set for that account.');
        }
    } catch (e) { showErr('Connection error'); }
}

async function submitRecoveryReset() {
    const username = (document.getElementById('recovery-username')?.value || '').trim();
    const answer = document.getElementById('recovery-answer')?.value || '';
    const newPassword = document.getElementById('recovery-new-password')?.value || '';
    const confirmPassword = document.getElementById('recovery-new-password-confirm')?.value || '';
    const errEl = document.getElementById('recovery-error');
    const showErr = (m) => { if (errEl) { errEl.textContent = m; errEl.style.display = 'block'; } };
    if (errEl) errEl.style.display = 'none';
    if (!answer || !newPassword) { showErr('Enter your answer and a new password'); return; }
    if (newPassword.length < 6) { showErr('New password must be at least 6 characters'); return; }
    if (newPassword !== confirmPassword) { showErr('Passwords do not match'); return; }
    try {
        const res = await fetch('/api/auth/recovery-reset', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, answer, new_password: newPassword }),
        });
        const data = await res.json();
        if (data.success) { window.location.reload(); }
        else { showErr(res.status === 429 ? 'Too many attempts — wait a moment.' : (data.error || 'Reset failed')); }
    } catch (e) { showErr('Connection error'); }
}

// ── Launch PIN Lock Screen ─────────────────────────────────────────────

function showLaunchPinScreen() {
    const overlay = document.getElementById('launch-pin-overlay');
    if (!overlay) return;
    // Hide the whole app while locked — bypassing the overlay reveals nothing (#852).
    document.body.classList.add('app-locked');
    overlay.style.display = 'flex';

    const input = document.getElementById('launch-pin-input');
    const submit = document.getElementById('launch-pin-submit');
    const error = document.getElementById('launch-pin-error');

    input.value = '';
    error.style.display = 'none';
    setTimeout(() => input.focus(), 100);

    const doSubmit = async () => {
        const pin = input.value.trim();
        if (!pin) return;

        submit.disabled = true;
        submit.textContent = 'Verifying...';

        try {
            const res = await fetch('/api/profiles/verify-launch-pin', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pin })
            });
            const data = await res.json();

            if (data.success) {
                // Server session flag set by verify endpoint — consumed on next /api/profiles/current call
                overlay.style.display = 'none';
                initApp(); // Now safe to load the full app
            } else {
                error.textContent = data.error || 'Invalid PIN';
                error.style.display = 'block';
                input.value = '';
                input.focus();
                // Shake animation
                overlay.querySelector('.launch-pin-container').classList.add('shake');
                setTimeout(() => overlay.querySelector('.launch-pin-container').classList.remove('shake'), 500);
            }
        } catch (e) {
            error.textContent = 'Connection error';
            error.style.display = 'block';
        }

        submit.disabled = false;
        submit.textContent = 'Unlock';
    };

    // Remove old listeners to prevent stacking
    const newSubmit = submit.cloneNode(true);
    submit.parentNode.replaceChild(newSubmit, submit);
    newSubmit.addEventListener('click', doSubmit);

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doSubmit();
    });
}

// ── Security Settings Helpers ──────────────────────────────────────────

async function saveLoginPassword() {
    const input = document.getElementById('security-login-password');
    const confirmInput = document.getElementById('security-login-password-confirm');
    const msg = document.getElementById('security-login-password-msg');
    const password = input?.value || '';
    const confirm = confirmInput?.value || '';
    const show = (text, ok) => {
        if (!msg) return;
        msg.textContent = text;
        msg.style.color = ok ? '#4caf50' : '#ff5252';
        msg.style.display = 'block';
    };
    if (!password || password.length < 6) { show('Password must be at least 6 characters', false); return; }
    if (password !== confirm) { show('Passwords do not match', false); return; }
    try {
        const res = await fetch('/api/profiles/1/set-password', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password }),
        });
        const data = await res.json();
        if (data.success) {
            show('Admin login password saved', true);
            if (input) { input.value = ''; input.placeholder = 'Enter a new password to change it'; }
            if (confirmInput) confirmInput.value = '';
            updateRequireLoginGate(true);   // Step 1 done → unlock Step 3
            const st = document.getElementById('security-login-password-status');
            if (st) st.style.display = 'block';
        }
        else show(data.error || 'Failed to save password', false);
    } catch (e) { show('Connection error', false); }
}

// Lock/unlock the "Require login" toggle based on whether the admin has a
// password — makes the prerequisite (anti-lockout) visible instead of a
// surprise 400 on save.
function updateRequireLoginGate(hasPassword) {
    const toggle = document.getElementById('security-require-login');
    const wrap = document.getElementById('security-login-toggle-wrap');
    const help = document.getElementById('security-require-login-help');
    if (!toggle) return;
    toggle.disabled = !hasPassword;
    if (!hasPassword) toggle.checked = false;
    if (wrap) wrap.classList.toggle('security-locked', !hasPassword);
    if (help) {
        help.innerHTML = hasPassword
            ? 'Replaces the profile picker + PIN with a sign-in screen. Best for instances exposed to the internet.'
            : '🔒 Set the admin password in <strong>Step 1</strong> first — then you can turn this on.';
    }
}

// Reflect already-saved login credentials. Passwords are never sent to the
// browser, so instead of an empty field (which looks unset after a refresh) we
// show that one is set and pre-fill the saved recovery question.
function applyLoginSavedState(profile) {
    const hasPassword = profile?.has_password || false;
    const hasRecovery = profile?.has_recovery || false;
    const question = profile?.recovery_question || '';

    const pwStatus = document.getElementById('security-login-password-status');
    const pwField = document.getElementById('security-login-password');
    const pwConfirm = document.getElementById('security-login-password-confirm');
    if (pwStatus) pwStatus.style.display = hasPassword ? 'block' : 'none';
    if (hasPassword) {
        if (pwField) pwField.placeholder = 'Enter a new password to change it';
        if (pwConfirm) pwConfirm.placeholder = 'Confirm new password';
    }

    const recStatus = document.getElementById('security-recovery-status');
    const recSel = document.getElementById('security-recovery-question');
    const recCustom = document.getElementById('security-recovery-custom');
    const recAnswer = document.getElementById('security-recovery-answer');
    if (recStatus) {
        recStatus.style.display = hasRecovery ? 'block' : 'none';
        recStatus.textContent = hasRecovery
            ? ('✓ Recovery question saved' + (question ? ': “' + question + '”' : ''))
            : '';
    }
    if (hasRecovery) {
        if (recSel && question) {
            recSel.value = question;            // preset options default value = their text
            if (recSel.value !== question) {    // not a preset → custom question
                recSel.value = '__custom__';
                if (recCustom) { recCustom.style.display = 'block'; recCustom.value = question; }
            }
        }
        if (recAnswer) recAnswer.placeholder = 'Enter a new answer to change it';
    }
}

function handleRecoveryQuestionChange() {
    const sel = document.getElementById('security-recovery-question');
    const custom = document.getElementById('security-recovery-custom');
    if (sel && custom) custom.style.display = (sel.value === '__custom__') ? 'block' : 'none';
}

async function saveRecoveryQuestion() {
    const sel = document.getElementById('security-recovery-question');
    const custom = document.getElementById('security-recovery-custom');
    const answer = document.getElementById('security-recovery-answer')?.value || '';
    const msg = document.getElementById('security-recovery-msg');
    const show = (text, ok) => {
        if (!msg) return;
        msg.textContent = text;
        msg.style.color = ok ? '#4caf50' : '#ff5252';
        msg.style.display = 'block';
    };
    let question = sel?.value || '';
    if (question === '__custom__') question = (custom?.value || '').trim();
    if (!question) { show('Pick or type a question', false); return; }
    if (!answer.trim()) { show('Enter an answer', false); return; }
    try {
        const res = await fetch('/api/profiles/1/set-recovery', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, answer }),
        });
        const data = await res.json();
        if (data.success) {
            show('Recovery question saved', true);
            const a = document.getElementById('security-recovery-answer');
            if (a) { a.value = ''; a.placeholder = 'Enter a new answer to change it'; }
            const rst = document.getElementById('security-recovery-status');
            if (rst) { rst.style.display = 'block'; rst.textContent = '✓ Recovery question saved: “' + question + '”'; }
        }
        else show(data.error || 'Failed to save', false);
    } catch (e) { show('Connection error', false); }
}

async function saveSecurityPin() {
    const pin = document.getElementById('security-new-pin').value;
    const confirm = document.getElementById('security-confirm-pin').value;
    const msg = document.getElementById('security-pin-msg');

    const pinProblem = profilePinError(pin || '');
    if (pinProblem) {
        msg.textContent = pinProblem;
        msg.style.display = 'block';
        msg.style.color = '#ff5252';
        return;
    }
    if (pin !== confirm) {
        msg.textContent = 'PINs do not match';
        msg.style.display = 'block';
        msg.style.color = '#ff5252';
        return;
    }

    try {
        const res = await fetch('/api/profiles/1/set-pin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin })
        });
        const data = await res.json();

        if (data.success) {
            msg.textContent = 'PIN saved! You can now enable the lock screen.';
            msg.style.color = '#4caf50';
            msg.style.display = 'block';

            // Update UI — hide setup, show change, enable toggle
            document.getElementById('security-pin-setup').style.display = 'none';
            document.getElementById('security-change-pin-section').style.display = 'block';
            document.getElementById('security-require-pin').disabled = false;

            // Clear inputs
            document.getElementById('security-new-pin').value = '';
            document.getElementById('security-confirm-pin').value = '';
        } else {
            msg.textContent = data.error || 'Failed to save PIN';
            msg.style.color = '#ff5252';
            msg.style.display = 'block';
        }
    } catch (e) {
        msg.textContent = 'Connection error';
        msg.style.color = '#ff5252';
        msg.style.display = 'block';
    }
}

function handleSecurityPinToggle(checkbox) {
    // If trying to enable but no PIN, show the setup section
    if (checkbox.checked) {
        const setupSection = document.getElementById('security-pin-setup');
        if (setupSection.style.display !== 'none' || checkbox.disabled) {
            checkbox.checked = false;
            setupSection.style.display = 'block';
            document.getElementById('security-new-pin').focus();
            return;
        }
    }
    // Auto-save this setting
    saveSettings(true);
}

function showChangeSecurityPin() {
    document.getElementById('security-pin-setup').style.display = 'block';
    document.getElementById('security-new-pin').focus();
}

// ── Forgot PIN Recovery ────────────────────────────────────────────────

function showForgotPinView() {
    document.getElementById('launch-pin-entry').style.display = 'none';
    document.getElementById('launch-pin-recovery').style.display = 'block';
    document.getElementById('launch-recovery-input').value = '';
    document.getElementById('launch-recovery-error').style.display = 'none';
    setTimeout(() => document.getElementById('launch-recovery-input').focus(), 100);
}

function showPinEntryView() {
    document.getElementById('launch-pin-recovery').style.display = 'none';
    document.getElementById('launch-pin-entry').style.display = 'block';
    setTimeout(() => document.getElementById('launch-pin-input').focus(), 100);
}

async function submitRecoveryCredential() {
    const input = document.getElementById('launch-recovery-input');
    const error = document.getElementById('launch-recovery-error');
    const btn = document.getElementById('launch-recovery-submit');
    const credential = input.value.trim();

    if (!credential) return;

    btn.disabled = true;
    btn.textContent = 'Verifying...';
    error.style.display = 'none';

    try {
        const res = await fetch('/api/profiles/reset-pin-via-credential', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ credential })
        });
        const data = await res.json();

        if (data.success) {
            document.getElementById('launch-pin-overlay').style.display = 'none';
            initApp();
            setTimeout(() => showToast('PIN cleared. You can set a new one in Settings → Advanced.', 'success'), 1000);
        } else {
            error.textContent = data.error || 'Credential not recognized';
            error.style.display = 'block';
            input.value = '';
            input.focus();
            document.getElementById('launch-pin-container').classList.add('shake');
            setTimeout(() => document.getElementById('launch-pin-container').classList.remove('shake'), 500);
        }
    } catch (e) {
        error.textContent = 'Connection error';
        error.style.display = 'block';
    }

    btn.disabled = false;
    btn.textContent = 'Verify & Reset PIN';
}

// ── Profiles: shared bits ──────────────────────────────────────────────
// the picker, pin entry, quick switch and manage sheet all build their
// markup here; profiles.css is the look.

// one rule for every pin a person sets: 4 to 20 digits
const PROFILE_PIN_MIN = 4;
const PROFILE_PIN_MAX = 20;
const PROFILE_COLORS = [
    ['#6366f1', 'Indigo'], ['#ec4899', 'Pink'], ['#10b981', 'Green'], ['#f59e0b', 'Amber'],
    ['#3b82f6', 'Blue'], ['#ef4444', 'Red'], ['#8b5cf6', 'Purple'], ['#14b8a6', 'Teal'],
];
const PF_ICONS = {
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    more: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>',
    back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 4H8l-7 8 7 8h13a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z"/><path d="m18 9-6 6M12 9l6 6"/></svg>',
    go: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    people: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18.5 20a6.5 6.5 0 0 0-3-5.5"/></svg>',
    signout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/></svg>',
};

let _pfUid = 0;
let _pfProfilesCache = [];

function profilePinError(pin) {
    if (!/^\d*$/.test(pin)) return 'Use digits only';
    if (pin.length < PROFILE_PIN_MIN) return `Use at least ${PROFILE_PIN_MIN} digits`;
    if (pin.length > PROFILE_PIN_MAX) return `Use ${PROFILE_PIN_MAX} digits at most`;
    return '';
}

function pfEl(tag, attrs = {}, children = []) {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([key, value]) => {
        if (value === null || value === undefined || value === false) return;
        if (key === 'class') el.className = value;
        else if (key === 'text') el.textContent = value;
        else if (key === 'html') el.innerHTML = value;
        else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
        else el.setAttribute(key, value === true ? '' : value);
    });
    (Array.isArray(children) ? children : [children]).forEach(child => {
        if (child !== null && child !== undefined && child !== false) el.append(child);
    });
    return el;
}

function pfAvatar(profile, sizeClass = '') {
    const el = pfEl('span', { class: ('pf-avatar ' + sizeClass).trim(), 'aria-hidden': 'true' });
    renderProfileAvatar(el, profile);
    return el;
}

function pfIsOwner(p) { return !!p && Number(p.id) === 1; }
function pfNoDownloads(p) { return !!p && !p.is_admin && (p.can_download === false || p.can_download === 0); }

// the one quiet line under a name on the picker. other people's rows only
// carry id/name/avatar/is_admin/has_pin/has_password, so everything past
// that is optional.
function profileMetaLine(p) {
    const bits = [];
    if (pfIsOwner(p)) bits.push('Owner');
    else if (p.is_admin) bits.push('Admin');
    if (p.library_mode === 'own') bits.push('Own library');
    if (pfNoDownloads(p)) bits.push('Asks for downloads');
    if (p.has_pin) bits.push('PIN');
    return bits.slice(0, 2).join(' · ');
}

function pfOwnerName(profiles) {
    const owner = (profiles || []).find(pfIsOwner) || (profiles || []).find(p => p.is_admin);
    return owner ? owner.name : 'an admin';
}

async function pfFetchProfiles() {
    const res = await fetch('/api/profiles');
    const data = await res.json();
    _pfProfilesCache = data.profiles || [];
    return data;
}

async function pfReadJson(res) {
    try { return await res.json(); } catch (e) { return {}; }
}

// ── layers: every profile modal goes through here, so escape, the focus
// trap and giving focus back all work the same way ───────────────────────

const _pfLayers = [];

function pfFocusables(root) {
    return Array.from(root.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
    )).filter(el => el.getClientRects().length > 0);
}

function pfOpenLayer(el, { onClose = null, escape = true, focus = null } = {}) {
    const existing = _pfLayers.find(entry => entry.el === el);
    if (existing) {
        existing.onClose = onClose;
        existing.escape = escape;
    } else {
        _pfLayers.push({ el, onClose, escape, returnTo: document.activeElement });
    }
    el.style.display = 'flex';
    document.body.classList.add('pf-modal-open');
    setTimeout(() => {
        const target = (typeof focus === 'function' ? focus() : focus) || pfFocusables(el)[0];
        if (target && el.contains(target)) target.focus();
    }, 40);
}

function pfCloseLayer(el) {
    const index = _pfLayers.findIndex(entry => entry.el === el);
    el.style.display = 'none';
    if (index < 0) return;
    const [entry] = _pfLayers.splice(index, 1);
    if (!_pfLayers.length) document.body.classList.remove('pf-modal-open');
    if (entry.returnTo && document.contains(entry.returnTo) && typeof entry.returnTo.focus === 'function') {
        try { entry.returnTo.focus(); } catch (e) { /* ignore */ }
    }
    if (entry.onClose) entry.onClose();
}

document.addEventListener('keydown', (e) => {
    const top = _pfLayers[_pfLayers.length - 1];
    if (!top) return;
    // the confirm dialog sits above every profile layer; it keeps its own keys
    const confirmOverlay = document.getElementById('confirm-modal-overlay');
    if (confirmOverlay && !confirmOverlay.classList.contains('hidden')) return;
    if (e.key === 'Escape') {
        if (document.querySelector('.pf-menu, .pf-popover')) return;   // an open menu closes first
        if (top.escape) {
            e.preventDefault();
            pfCloseLayer(top.el);
        }
        return;
    }
    if (e.key !== 'Tab') return;
    const items = pfFocusables(top.el);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    if (!top.el.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}, true);

// a dialog card inside a fresh layer, removed from the page when it closes
function pfModal({ title, subtitle = '', size = '', layerClass = 'pf-layer--editor', onClose = null, dismissable = true }) {
    const titleId = 'pf-modal-title-' + (++_pfUid);
    const layer = pfEl('div', { class: 'pf-layer ' + layerClass });
    const card = pfEl('div', {
        class: ('pf-modal ' + (size ? 'pf-modal--' + size : '')).trim(),
        role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId,
    });
    const titleEl = pfEl('h2', { class: 'pf-modal-title', id: titleId, text: title });
    const subEl = pfEl('p', { class: 'pf-modal-sub', text: subtitle });
    if (!subtitle) subEl.style.display = 'none';
    const closeBtn = pfEl('button', { type: 'button', class: 'pf-icon-btn', 'aria-label': 'Close', html: PF_ICONS.close });
    const head = pfEl('div', { class: 'pf-modal-head' }, [pfEl('div', { class: 'pf-modal-titles' }, [titleEl, subEl]), closeBtn]);
    const body = pfEl('div', { class: 'pf-modal-body' });
    const foot = pfEl('div', { class: 'pf-modal-foot' });
    card.append(head, body, foot);
    layer.append(card);
    document.body.append(layer);

    const close = () => pfCloseLayer(layer);
    closeBtn.addEventListener('click', close);
    if (dismissable) {
        layer.addEventListener('mousedown', (e) => { if (e.target === layer) close(); });
    }
    const open = (focus) => pfOpenLayer(layer, {
        focus,
        onClose: () => { layer.remove(); if (onClose) onClose(); },
    });
    return { layer, card, head, titleEl, subEl, body, foot, close, open };
}

// a switch: a title, one line saying what it does, and the toggle
function pfSwitch({ title, desc = '', checked = false, disabled = false, onChange = null }) {
    const id = 'pf-switch-' + (++_pfUid);
    const descId = id + '-desc';
    const input = pfEl('input', {
        type: 'checkbox', role: 'switch', id, class: 'pf-switch-input',
        'aria-describedby': desc ? descId : null,
    });
    input.checked = !!checked;
    input.disabled = !!disabled;
    const descEl = pfEl('span', { class: 'pf-switch-desc', id: descId, text: desc });
    const row = pfEl('label', { class: 'pf-switch-row' + (disabled ? ' is-disabled' : ''), for: id }, [
        pfEl('span', { class: 'pf-switch-text' }, [pfEl('span', { class: 'pf-switch-title', text: title }), descEl]),
        input,
        pfEl('span', { class: 'pf-switch-track', 'aria-hidden': 'true' }, pfEl('span', { class: 'pf-switch-thumb' })),
    ]);
    if (onChange) input.addEventListener('change', () => onChange(input.checked));
    return {
        row, input,
        get checked() { return input.checked; },
        set(value) { input.checked = !!value; },
        setDesc(text) { descEl.textContent = text; },
        setDisabled(value) { input.disabled = !!value; row.classList.toggle('is-disabled', !!value); },
    };
}

// a small menu of actions anchored to a button (the ⋯ on a card)
function pfOpenMenu(anchor, items) {
    pfCloseMenus();
    const menu = pfEl('div', { class: 'pf-menu', role: 'menu' });
    items.forEach(item => {
        if (item === 'sep') { menu.append(pfEl('div', { class: 'pf-menu-sep', role: 'separator' })); return; }
        const btn = pfEl('button', {
            type: 'button', role: 'menuitem', tabindex: '-1',
            class: 'pf-menu-item' + (item.danger ? ' pf-menu-item--danger' : ''),
        }, [item.icon ? pfEl('span', { html: item.icon, style: 'display:contents' }) : null,
            pfEl('span', { class: 'pf-menu-item-text', text: item.label })]);
        btn.addEventListener('click', () => { pfCloseMenus(false); item.run(); });
        menu.append(btn);
    });
    document.body.append(menu);
    const rect = anchor.getBoundingClientRect();
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    let left = Math.min(rect.right - width, window.innerWidth - width - 16);
    left = Math.max(16, left);
    let top = rect.bottom + 6;
    if (top + height > window.innerHeight - 16) top = Math.max(16, rect.top - height - 6);
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
    anchor.setAttribute('aria-expanded', 'true');
    _pfWireMenu(menu, anchor, () => pfCloseMenus());
    return menu;
}

let _pfMenuCleanup = null;

function pfCloseMenus(restoreFocus = true) {
    if (_pfMenuCleanup) {
        const cleanup = _pfMenuCleanup;
        _pfMenuCleanup = null;
        cleanup(restoreFocus);
    }
}

// arrow keys between items, escape and tab and outside clicks close
function _pfWireMenu(menu, anchor, close) {
    const items = () => Array.from(menu.querySelectorAll('[role="menuitem"]'));
    const onKey = (e) => {
        const list = items();
        const index = list.indexOf(document.activeElement);
        if (e.key === 'ArrowDown') { e.preventDefault(); list[(index + 1) % list.length]?.focus(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); list[(index - 1 + list.length) % list.length]?.focus(); }
        else if (e.key === 'Home') { e.preventDefault(); list[0]?.focus(); }
        else if (e.key === 'End') { e.preventDefault(); list[list.length - 1]?.focus(); }
        else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
        else if (e.key === 'Tab') { close(); }
    };
    const onDown = (e) => {
        if (!menu.contains(e.target) && !anchor.contains(e.target)) close();
    };
    const onScroll = (e) => { if (!menu.contains(e.target)) close(); };
    menu.addEventListener('keydown', onKey);
    setTimeout(() => {
        document.addEventListener('mousedown', onDown, true);
        document.addEventListener('touchstart', onDown, true);
    }, 0);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', onScroll, true);
    _pfMenuCleanup = (restoreFocus) => {
        document.removeEventListener('mousedown', onDown, true);
        document.removeEventListener('touchstart', onDown, true);
        window.removeEventListener('resize', close);
        window.removeEventListener('scroll', onScroll, true);
        menu.remove();
        anchor.setAttribute('aria-expanded', 'false');
        if (restoreFocus && document.contains(anchor)) anchor.focus();
    };
    setTimeout(() => items()[0]?.focus(), 0);
}

// ── Picker ─────────────────────────────────────────────────────────────

function showProfilePicker(profiles, canCancel = false) {
    _pfProfilesCache = profiles || [];
    const overlay = document.getElementById('profile-picker-overlay');
    const grid = document.getElementById('profile-picker-grid');
    const actions = document.getElementById('profile-picker-actions');
    const isAdmin = !!(currentProfile && currentProfile.is_admin);

    grid.innerHTML = '';
    _pfProfilesCache.forEach(p => {
        const meta = profileMetaLine(p);
        const isCurrent = !!(currentProfile && currentProfile.id === p.id);
        const tile = pfEl('button', {
            type: 'button',
            class: 'pf-tile' + (isCurrent ? ' is-current' : ''),
            'aria-label': p.name + (meta ? ', ' + meta : '') + (isCurrent ? ', current profile' : ''),
        }, [
            pfAvatar(p, 'pf-avatar--tile'),
            pfEl('span', { class: 'pf-tile-name', text: p.name }),
            pfEl('span', { class: 'pf-tile-meta', text: meta }),
        ]);
        tile.addEventListener('click', () => handleProfileClick(p, _pfProfilesCache.length));
        grid.append(tile);
    });

    if (isAdmin) {
        const add = pfEl('button', { type: 'button', class: 'pf-tile pf-tile--add' }, [
            pfEl('span', { class: 'pf-add-square', html: PF_ICONS.plus, 'aria-hidden': 'true' }),
            pfEl('span', { class: 'pf-tile-name', text: 'Add profile' }),
            pfEl('span', { class: 'pf-tile-meta', text: '' }),
        ]);
        add.addEventListener('click', () => openProfileEditor({ mode: 'create' }));
        grid.append(add);
    }

    actions.innerHTML = '';
    if (isAdmin) {
        actions.append(pfEl('button', { type: 'button', class: 'pf-pill', id: 'manage-profiles-btn', text: 'Manage profiles', onclick: () => openProfileManager() }));
    } else if (currentProfile && canCancel) {
        actions.append(pfEl('button', { type: 'button', class: 'pf-pill', text: 'Edit my profile', onclick: () => openProfileEditor({ mode: 'self' }) }));
    }
    if (canCancel) {
        actions.append(pfEl('button', { type: 'button', class: 'pf-pill', text: 'Cancel', onclick: () => hideProfilePicker() }));
    }
    actions.style.display = actions.childElementCount ? '' : 'none';

    const main = document.querySelector('.main-container');
    if (main) main.style.display = 'none';
    pfOpenLayer(overlay, {
        escape: !!canCancel,
        focus: () => grid.querySelector('.pf-tile.is-current') || grid.querySelector('.pf-tile'),
        onClose: () => {
            const container = document.querySelector('.main-container');
            if (container) container.style.display = 'flex';
        },
    });
}

function hideProfilePicker() {
    const overlay = document.getElementById('profile-picker-overlay');
    pfCloseLayer(overlay);
    overlay.style.display = 'none';
    const main = document.querySelector('.main-container');
    if (main) main.style.display = 'flex';
}

async function handleProfileClick(profile, profileCount = 0) {
    pfCloseMenus(false);
    // the pin only matters when there is someone to switch between
    if (!profileCount) {
        try { profileCount = ((await pfFetchProfiles()).profiles || []).length; } catch (e) { profileCount = 1; }
    }
    if (currentProfile && profile.id === currentProfile.id) {
        hideProfilePicker();
        return;
    }
    if (profileLoginMode && currentProfile) {
        showPinDialog(profile, 'password');
    } else if (profile.has_pin && profileCount > 1) {
        showPinDialog(profile, 'pin');
    } else {
        const ok = await selectProfile(profile.id);
        if (!ok) {
            if (typeof showToast === 'function') showToast(`Couldn't open ${profile.name}`, 'error');
            return;
        }
        // always a fresh load: with 2+ profiles everything the page fetched
        // before a card was picked got 401 profile_required (theme, settings,
        // react queries), and it has to load again as this profile
        window.location.reload();
    }
}

// ── PIN entry ──────────────────────────────────────────────────────────

// the pin's length is remembered after a right pin on this browser, so the
// next time it can go through the moment the last digit lands. until then
// it waits for enter: guessing 4 would burn a try on every longer pin.
function _pfPinLengthKey(profileId) { return 'ss_pin_len_' + profileId; }

function _pfKnownPinLength(profileId) {
    try { return parseInt(localStorage.getItem(_pfPinLengthKey(profileId)) || '0', 10) || 0; } catch (e) { return 0; }
}

function showPinDialog(profile, mode = 'pin') {
    const layer = document.getElementById('profile-pin-dialog');
    const isPassword = mode === 'password';
    const knownLength = isPassword ? 0 : _pfKnownPinLength(profile.id);
    let busy = false;
    let lockTimer = null;

    layer.innerHTML = '';
    layer.setAttribute('role', 'dialog');
    layer.setAttribute('aria-modal', 'true');
    layer.setAttribute('aria-labelledby', 'profile-pin-title');

    const card = pfEl('div', { class: 'pf-pin-card' });
    card.append(pfAvatar(profile, 'pf-avatar--lg'));
    card.append(pfEl('h2', {
        class: 'pf-pin-title', id: 'profile-pin-title',
        text: isPassword ? `Enter ${profile.name}'s password` : `Enter ${profile.name}'s PIN`,
    }));

    const error = pfEl('p', { class: 'pf-pin-error', id: 'profile-pin-error', role: 'alert', 'aria-live': 'assertive' });
    let input;
    let entry = null;
    let dots = null;
    const keys = [];

    if (isPassword) {
        input = pfEl('input', {
            type: 'password', id: 'profile-pin-input', class: 'pf-input pf-pin-password',
            autocomplete: 'current-password', maxlength: '200', 'aria-label': `${profile.name}'s password`,
            'aria-describedby': 'profile-pin-error',
        });
        card.append(input, error);
        const go = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--primary', text: 'Continue', style: 'margin-top:8px;width:100%' });
        go.addEventListener('click', () => submit());
        keys.push(go);
        card.append(go);
    } else {
        input = pfEl('input', {
            type: 'password', id: 'profile-pin-input', class: 'pf-pin-input',
            inputmode: 'numeric', autocomplete: 'off', maxlength: '64',
            'aria-label': `${profile.name}'s PIN`, 'aria-describedby': 'profile-pin-error',
        });
        dots = pfEl('div', { class: 'pf-pin-dots', 'aria-hidden': 'true' });
        entry = pfEl('div', { class: 'pf-pin-entry' }, [dots, input]);
        card.append(entry, error);

        const keypad = pfEl('div', { class: 'pf-keypad' });
        ['1', '2', '3', '4', '5', '6', '7', '8', '9'].forEach(d => keypad.append(_key(d)));
        const del = pfEl('button', { type: 'button', class: 'pf-key pf-key--quiet', 'aria-label': 'Delete', html: PF_ICONS.back });
        del.addEventListener('click', () => { input.value = input.value.slice(0, -1); onInput(); });
        keys.push(del);
        keypad.append(del, _key('0'));
        const go = pfEl('button', { type: 'button', class: 'pf-key pf-key--go', 'aria-label': 'Unlock', html: PF_ICONS.go });
        go.addEventListener('click', () => submit());
        keys.push(go);
        keypad.append(go);
        card.append(keypad);
    }

    function _key(digit) {
        const key = pfEl('button', { type: 'button', class: 'pf-key', text: digit, 'aria-label': digit });
        key.addEventListener('click', () => {
            if (input.value.length >= 64) return;
            input.value += digit;
            onInput();
            if (finePointer) input.focus();
        });
        keys.push(key);
        return key;
    }

    // how to get back in when the pin is gone: the owner proves it with a
    // service credential, everyone else asks the owner
    const foot = pfEl('div', { class: 'pf-pin-foot' });
    if (!isPassword) {
        if (pfIsOwner(profile)) {
            foot.append(pfEl('button', { type: 'button', class: 'pf-link', text: 'Forgot PIN?', onclick: () => showProfileForgotPin(profile) }));
        } else {
            foot.append(pfEl('span', { text: `Forgot? Ask ${pfOwnerName(_pfProfilesCache)} to reset it` }));
        }
    }
    foot.append(pfEl('button', { type: 'button', class: 'pf-link', text: 'Cancel', onclick: () => pfCloseLayer(layer) }));
    card.append(foot);
    layer.append(card);

    function renderDots() {
        if (!dots) return;
        const count = Math.max(knownLength || PROFILE_PIN_MIN, input.value.length);
        dots.innerHTML = '';
        for (let i = 0; i < count; i++) {
            dots.append(pfEl('span', { class: 'pf-pin-dot' + (i < input.value.length ? ' is-filled' : '') }));
        }
    }

    function setError(text) {
        error.textContent = text;
        if (entry) entry.classList.toggle('is-error', !!text);
    }

    function onInput() {
        if (error.textContent && !lockTimer) setError('');
        renderDots();
        if (knownLength && input.value.length === knownLength) submit();
    }

    function setLocked(locked) {
        input.disabled = locked;
        keys.forEach(k => { k.disabled = locked; });
    }

    function lockFor(seconds) {
        let left = Math.max(1, seconds);
        setLocked(true);
        const tick = () => {
            setError(`Too many tries, wait ${left}s`);
            if (left <= 0) {
                clearInterval(lockTimer);
                lockTimer = null;
                setLocked(false);
                setError('');
                input.focus();
            }
            left -= 1;
        };
        clearInterval(lockTimer);
        tick();
        lockTimer = setInterval(tick, 1000);
    }

    function shake() {
        if (!entry) return;
        entry.classList.remove('is-shaking');
        void entry.offsetWidth;
        entry.classList.add('is-shaking');
    }

    async function submit() {
        const secret = input.value;
        if (!secret || busy || lockTimer) return;
        busy = true;
        try {
            const res = await fetch('/api/profiles/select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(isPassword
                    ? { profile_id: profile.id, password: secret }
                    : { profile_id: profile.id, pin: secret }),
            });
            const data = await pfReadJson(res);
            if (res.ok && data.success) {
                if (!isPassword) {
                    try { localStorage.setItem(_pfPinLengthKey(profile.id), String(secret.length)); } catch (e) { /* ignore */ }
                }
                // a fresh load, same reason as the no-pin pick above
                window.location.reload();
                return;
            }
            input.value = '';
            renderDots();
            if (res.status === 429) {
                lockFor(parseInt(res.headers.get('Retry-After') || '60', 10) || 60);
            } else {
                shake();
                const wrong = isPassword ? 'Wrong password, try again' : 'Wrong PIN, try again';
                setError(/invalid/i.test(data.error || '') ? wrong : (data.error || wrong));
                input.focus();
            }
        } catch (e) {
            setError('Connection error, try again');
        } finally {
            busy = false;
        }
    }

    input.addEventListener('input', onInput);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); submit(); }
    });
    // digits typed while a key has focus still count
    layer.onkeydown = (e) => {
        if (isPassword || e.target === input || input.disabled || e.ctrlKey || e.metaKey || e.altKey) return;
        if (/^[0-9]$/.test(e.key)) { e.preventDefault(); input.value += e.key; onInput(); }
        else if (e.key === 'Backspace') { e.preventDefault(); input.value = input.value.slice(0, -1); onInput(); }
    };
    renderDots();

    // on a phone the keypad is the keyboard; only a real pointer gets the
    // input focused (and so the system keyboard stays down)
    const finePointer = !window.matchMedia || window.matchMedia('(pointer: fine)').matches;
    pfOpenLayer(layer, {
        focus: () => (isPassword || finePointer) ? input : keys[0],
        onClose: () => { clearInterval(lockTimer); lockTimer = null; },
    });
}

// the owner's way back in: a service credential clears the owner's pin.
// members are told to ask instead, the server refuses them here.
function showProfileForgotPin(profile) {
    const layer = document.getElementById('profile-pin-dialog');
    const target = profile || { id: 1, name: pfOwnerName(_pfProfilesCache) };
    layer.innerHTML = '';
    const card = pfEl('div', { class: 'pf-pin-card' });
    card.append(pfAvatar(target, 'pf-avatar--lg'));
    card.append(pfEl('h2', { class: 'pf-pin-title', id: 'profile-pin-title', text: `Reset ${target.name}'s PIN` }));
    card.append(pfEl('p', {
        class: 'pf-modal-sub',
        text: 'Paste any API key or token SoulSync already has (Plex token, Spotify secret, and so on). The PIN is cleared and the lock screen turns off.',
    }));
    const input = pfEl('input', {
        type: 'password', id: 'profile-recovery-input', class: 'pf-input', maxlength: '200',
        autocomplete: 'off', placeholder: 'API key or token', 'aria-label': 'API key or token',
        style: 'margin-top:20px', 'aria-describedby': 'profile-recovery-error',
    });
    const error = pfEl('p', { class: 'pf-pin-error', id: 'profile-recovery-error', role: 'alert', 'aria-live': 'assertive' });
    const go = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--primary', text: 'Clear the PIN', style: 'margin-top:8px;width:100%' });
    const back = pfEl('button', { type: 'button', class: 'pf-link', text: 'Back', onclick: () => showPinDialog(target, 'pin') });
    card.append(input, error, go, pfEl('div', { class: 'pf-pin-foot' }, back));
    layer.append(card);

    const submit = async () => {
        const credential = input.value.trim();
        if (!credential) { error.textContent = 'Paste a key or token first'; return; }
        go.disabled = true;
        error.textContent = '';
        try {
            const res = await fetch('/api/profiles/reset-pin-via-credential', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ credential, profile_id: target.id }),
            });
            const data = await pfReadJson(res);
            if (res.ok && data.success) {
                try { localStorage.removeItem(_pfPinLengthKey(target.id)); } catch (e) { /* ignore */ }
                pfCloseLayer(layer);
                if (typeof showToast === 'function') showToast('PIN cleared. Set a new one from your profile.', 'success');
                handleProfileClick({ ...target, has_pin: false }, _pfProfilesCache.length);
                return;
            }
            if (res.status === 429) {
                const wait = parseInt(res.headers.get('Retry-After') || '60', 10) || 60;
                error.textContent = `Too many tries, wait ${wait}s`;
            } else {
                error.textContent = data.error || "That doesn't match anything SoulSync has";
            }
            input.value = '';
            input.focus();
        } catch (e) {
            error.textContent = 'Connection error, try again';
        }
        go.disabled = false;
    };
    go.addEventListener('click', submit);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    pfOpenLayer(layer, { focus: input });
}

async function selectProfile(profileId) {
    try {
        const oldProfileId = currentProfile ? currentProfile.id : null;
        const res = await fetch('/api/profiles/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_id: profileId })
        });
        const data = await res.json();
        if (data.success) {
            setCurrentProfile(data.profile);
            // Join profile-scoped WebSocket room for watchlist/wishlist count updates
            if (socket && socket.connected) {
                socket.emit('profile:join', { profile_id: profileId, old_profile_id: oldProfileId });
            }
            // Invalidate ListenBrainz cache on profile switch (each profile has their own playlists)
            _invalidateListenBrainzCache();
        }
        return data.success;
    } catch (e) {
        console.error('Error selecting profile:', e);
        return false;
    }
}

// ── Quick switch: the small menu above the sidebar profile ─────────────

async function toggleProfileQuickSwitch() {
    const button = document.getElementById('profile-indicator-button');
    if (!button) return;
    if (button.getAttribute('aria-expanded') === 'true') { pfCloseMenus(); return; }
    let profiles = _pfProfilesCache;
    try { profiles = (await pfFetchProfiles()).profiles || []; } catch (e) { /* use what we had */ }
    openProfileQuickSwitch(button, profiles);
}

function openProfileQuickSwitch(button, profiles) {
    pfCloseMenus(false);
    const me = currentProfile;
    if (!me) return;
    const others = profiles.filter(p => p.id !== me.id);
    const menu = pfEl('div', { class: 'pf-popover', id: 'profile-quick-switch', role: 'menu', 'aria-label': 'Profiles' });

    const item = (children, run, extra = '') => {
        const btn = pfEl('button', { type: 'button', role: 'menuitem', tabindex: '-1', class: 'pf-menu-item ' + extra }, children);
        btn.addEventListener('click', () => { pfCloseMenus(false); run(); });
        return btn;
    };
    const label = (text, icon) => [pfEl('span', { html: icon, style: 'display:contents' }), pfEl('span', { class: 'pf-menu-item-text', text })];

    if (others.length) {
        menu.append(pfEl('div', { class: 'pf-menu-label', text: 'SWITCH TO', 'aria-hidden': 'true' }));
        others.forEach(p => {
            const meta = profileLoginMode ? 'Password' : (p.has_pin ? 'PIN' : '');
            menu.append(item([
                pfAvatar(p, 'pf-avatar--sm'),
                pfEl('span', { class: 'pf-menu-item-text' }, [
                    pfEl('span', { class: 'pf-menu-item-name', text: p.name }),
                    meta ? pfEl('span', { class: 'pf-menu-item-meta', text: meta }) : null,
                ]),
            ], () => handleProfileClick(p, profiles.length)));
        });
        menu.append(pfEl('div', { class: 'pf-menu-sep', role: 'separator' }));
    }
    menu.append(item(label('Edit my profile', PF_ICONS.edit), () => openProfileEditor({ mode: 'self' })));
    // my account is the member's own media server login + music services;
    // the admin runs on the app's accounts, so it's theirs only
    if (!me.is_admin && typeof window.openMyAccountsModal === 'function') {
        menu.append(item(label('Profile & account', PF_ICONS.user), () => window.openMyAccountsModal()));
    }
    if (me.is_admin) {
        menu.append(item(label('Manage profiles', PF_ICONS.people), () => openProfileManager()));
    }
    if (profileLoginMode) {
        menu.append(pfEl('div', { class: 'pf-menu-sep', role: 'separator' }));
        menu.append(item(label('Sign out', PF_ICONS.signout), () => soulsyncLogout()));
    }

    document.body.append(menu);
    const rect = button.getBoundingClientRect();
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    const left = Math.max(16, Math.min(rect.left, window.innerWidth - width - 16));
    let top = rect.top - height - 8;
    if (top < 16) top = Math.min(rect.bottom + 8, window.innerHeight - height - 16);
    menu.style.left = left + 'px';
    menu.style.top = Math.max(16, top) + 'px';
    button.setAttribute('aria-expanded', 'true');
    _pfWireMenu(menu, button, () => pfCloseMenus());
}

function updateProfileIndicator() {
    const indicator = document.getElementById('profile-indicator');
    if (!currentProfile || !indicator) return;

    const avatar = document.getElementById('profile-indicator-avatar');
    const name = document.getElementById('profile-indicator-name');

    renderProfileAvatar(avatar, currentProfile);
    name.textContent = currentProfile.name;
    indicator.style.display = 'flex';

    // Service Status quick-switch is admin-only — drop the clickable affordance
    // for non-admins so it doesn't look interactive.
    const statusSection = document.querySelector('.status-section--clickable');
    if (statusSection) statusSection.classList.toggle('status-section--locked', !currentProfile.is_admin);

    // My Account (your media server identity + your own music services) is
    // inert for the admin, who uses the app's accounts and the full Settings
    // page. hidden for the admin, shown to everyone else.
    const myAccountsBtn = document.getElementById('my-accounts-btn');
    if (myAccountsBtn) myAccountsBtn.style.display = currentProfile.is_admin ? 'none' : '';

    // the avatar + name is a real button that opens the quick switch menu
    const indicatorButton = document.getElementById('profile-indicator-button');
    if (indicatorButton) {
        indicatorButton.onclick = (e) => { e.stopPropagation(); toggleProfileQuickSwitch(); };
        indicatorButton.setAttribute('aria-label', `${currentProfile.name}, switch profile`);
    }

    // Filter sidebar pages based on profile permissions
    document.querySelectorAll('.nav-button[data-page]').forEach(btn => {
        const page = btn.getAttribute('data-page');
        if (page === 'hydrabase') return; // Managed by dev mode toggle
        if (page === 'settings') {
            // Settings always gated by is_admin
            btn.style.display = currentProfile.is_admin ? '' : 'none';
        } else if (page === 'requests') {
            btn.style.display = (currentProfile.is_admin || !canDownload()) ? '' : 'none';
        } else if (page === 'help' || page === 'issues') {
            btn.style.display = ''; // Always visible
        } else if (currentProfile.id === 1) {
            btn.style.display = ''; // Root admin sees all
        } else {
            // old rows still carry retired page ids (downloads, artists)
            const ap = normalizeProfilePageList(currentProfile.allowed_pages);
            btn.style.display = (!ap || ap.includes(page)) ? '' : 'none';
        }
    });

    // Video side — same model. Control surfaces (Import, Settings, Automations) are
    // admin-only; the Overlay Studio launcher is admin-only via a body class (robust
    // to the dashboard re-rendering it); everything else is a per-profile page toggle
    // sharing the same allowed_pages list. Help/Issues always visible.
    const VIDEO_ADMIN_ONLY = ['video-import', 'video-settings', 'video-automations'];
    document.querySelectorAll('.video-nav .nav-button[data-video-page]').forEach(btn => {
        const page = btn.getAttribute('data-video-page');
        if (page === 'video-help' || page === 'video-issues') { btn.style.display = ''; return; }
        if (VIDEO_ADMIN_ONLY.includes(page)) { btn.style.display = currentProfile.is_admin ? '' : 'none'; return; }
        if (currentProfile.id === 1) { btn.style.display = ''; return; }
        const ap = currentProfile.allowed_pages;
        btn.style.display = (!ap || ap.includes(page)) ? '' : 'none';
    });
    document.body.classList.toggle('video-admin', !!currentProfile.is_admin);

    // Toggle download capability
    if (canDownload()) {
        document.body.classList.remove('downloads-disabled');
    } else {
        document.body.classList.add('downloads-disabled');
    }

    // Per-profile SIDE access (music | video | both): a single-side profile
    // never sees the Music↔Video switcher — they just live on their side.
    // Forcing the side here (boot + every profile switch) also covers a stale
    // localStorage side from a previous profile on the same browser. The video
    // API is enforced server-side too; this is the visible half.
    const sides = profileAllowedSides();
    const sideToggle = document.querySelector('.side-toggle');
    if (sideToggle) sideToggle.style.display = sides === 'both' ? '' : 'none';
    // Keep the pre-paint flash guard in sync: the html-level class (seeded from
    // this cache by the inline <head> script) hides the switcher on the NEXT
    // reload before the profile has even been fetched.
    document.documentElement.classList.toggle('side-locked', sides !== 'both');
    try { localStorage.setItem('ss_allowed_sides', sides); } catch (e) { /* ignore */ }
    if (sides !== 'both' &&
            document.body.getAttribute('data-side') !== sides &&
            typeof window._switchAppSide === 'function') {
        window._switchAppSide(sides, { force: true });
    }
}

// Per-profile side access — 'music' | 'video' | 'both'. Admins always both;
// non-admins default to music unless explicitly granted (mirrors the server's
// get_profile resolution, so a stale payload can't widen access).
function profileAllowedSides() {
    if (!currentProfile || currentProfile.is_admin || currentProfile.id === 1) return 'both';
    const s = currentProfile.allowed_sides;
    return (s === 'video' || s === 'both') ? s : 'music';
}

// =====================
// PERSONAL SETTINGS MODAL
// =====================

function _invalidateListenBrainzCache() {
    if (typeof listenbrainzPlaylistsLoaded !== 'undefined') listenbrainzPlaylistsLoaded = false;
    if (typeof listenbrainzPlaylistsCache !== 'undefined') {
        try { Object.keys(listenbrainzPlaylistsCache).forEach(k => delete listenbrainzPlaylistsCache[k]); } catch (e) { }
    }
    if (typeof listenbrainzTracksCache !== 'undefined') {
        try { Object.keys(listenbrainzTracksCache).forEach(k => delete listenbrainzTracksCache[k]); } catch (e) { }
    }
}

const PROFILE_PAGE_LABELS = {
    dashboard: 'Dashboard',
    sync: 'Sync',
    search: 'Search',
    discover: 'Discover',
    watchlist: 'Watchlist',
    wishlist: 'Wishlist',
    automations: 'Automations',
    'active-downloads': 'Downloads',
    library: 'Library',
    stats: 'Listening Stats',
    'playlist-explorer': 'Playlist Explorer',
    import: 'Import',
    tools: 'Tools',
    hydrabase: 'Hydrabase',
    issues: 'Issues',
    podcasts: 'Podcasts',
    audiobooks: 'Audiobooks',
    help: 'Help & Docs',
    settings: 'Settings',
    'artist-detail': 'Artist Detail',
    'video-dashboard': 'Video · Dashboard',
    'video-search': 'Video · Search',
    'video-discover': 'Video · Discover',
    'video-library': 'Video · Library',
    'video-watchlist': 'Video · Watchlist',
    'video-wishlist': 'Video · Wishlist',
    'video-downloads': 'Video · Downloads',
    'video-calendar': 'Video · Calendar',
    'video-tools': 'Video · Tools',
};

function getProfilePageLabel(pageId) {
    return PROFILE_PAGE_LABELS[pageId] || pageId.split('-').map(part => part ? part[0].toUpperCase() + part.slice(1) : part).join(' ');
}

// every page a profile can be given, by side. help and issues are always on
// and never listed. podcasts and audiobooks live with music.
const PROFILE_PAGE_GROUPS = [
    {
        side: 'music', label: 'Music',
        pages: ['dashboard', 'sync', 'podcasts', 'audiobooks', 'search', 'discover', 'watchlist', 'wishlist',
            'automations', 'active-downloads', 'library', 'stats', 'playlist-explorer', 'import'],
    },
    {
        side: 'video', label: 'Movies & TV',
        pages: ['video-dashboard', 'video-search', 'video-discover', 'video-library', 'video-watchlist',
            'video-wishlist', 'video-downloads', 'video-calendar', 'video-tools'],
    },
];

function profilePageSide(pageId) {
    if (pageId === 'help' || pageId === 'issues') return 'shared';
    return String(pageId).startsWith('video-') ? 'video' : 'music';
}

function _pfSidesAllow(sides, side) {
    return side === 'shared' || sides === 'both' || sides === side;
}

function _pfPlainPageLabel(pageId) {
    return getProfilePageLabel(pageId).replace(/^Video · /, '');
}

// the pages a home page can be: what the sides allow, narrowed by the
// page list when there is one. video pages keep their side in the label.
function profileHomeOptions(sides, allowedPages) {
    const allowed = Array.isArray(allowedPages) ? new Set(normalizeProfilePageList(allowedPages)) : null;
    const options = [];
    PROFILE_PAGE_GROUPS.forEach(group => {
        if (!_pfSidesAllow(sides, group.side)) return;
        group.pages.forEach(pageId => {
            if (allowed && !allowed.has(pageId)) return;
            options.push({ value: pageId, label: getProfilePageLabel(pageId) });
        });
    });
    options.push({ value: 'help', label: getProfilePageLabel('help') });
    return options;
}

// ── Manage profiles ────────────────────────────────────────────────────

let _pfManageState = null;

function _pfSidesOf(p) {
    if (p.is_admin) return 'both';
    return (p.allowed_sides === 'video' || p.allowed_sides === 'both') ? p.allowed_sides : 'music';
}

function _pfRoleLine(p) {
    if (pfIsOwner(p)) return 'Owner';
    return p.is_admin ? 'Admin' : 'Member';
}

function _pfSummary(p, loginMode) {
    if (p.is_admin) return { text: 'Everything, including settings' + (p.has_pin ? ' · PIN' : ''), warn: '' };
    const sides = _pfSidesOf(p);
    const bits = [sides === 'both' ? 'Music & movies' : sides === 'video' ? 'Movies & TV only' : 'Music only'];
    bits.push(pfNoDownloads(p) ? 'asks for downloads' : 'downloads');
    if (Array.isArray(p.allowed_pages)) bits.push(`${p.allowed_pages.length} pages`);
    if (p.library_mode === 'own') bits.push('own library');
    if (p.has_pin) bits.push('PIN');
    const warn = loginMode && !p.has_password ? "can't sign in yet, needs a login password" : '';
    return { text: bits.join(' · '), warn };
}

async function openProfileManager() {
    pfCloseMenus(false);
    const layer = document.getElementById('profile-manage-panel');
    layer.innerHTML = '';
    const modal = pfEl('div', { class: 'pf-modal pf-modal--wide', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'profile-manage-title' });
    const addBtn = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--primary', id: 'create-profile-btn', html: PF_ICONS.plus + '<span>Add profile</span>' });
    addBtn.addEventListener('click', () => openProfileEditor({ mode: 'create' }));
    const closeBtn = pfEl('button', { type: 'button', class: 'pf-icon-btn', id: 'profile-manage-close', 'aria-label': 'Close', html: PF_ICONS.close });
    closeBtn.addEventListener('click', () => pfCloseLayer(layer));
    modal.append(pfEl('div', { class: 'pf-modal-head' }, [
        pfEl('div', { class: 'pf-modal-titles' }, [
            pfEl('h2', { class: 'pf-modal-title', id: 'profile-manage-title', text: 'Profiles' }),
            pfEl('p', { class: 'pf-modal-sub', text: 'Who uses SoulSync here, and what each of them can do.' }),
        ]),
        addBtn, closeBtn,
    ]));
    const body = pfEl('div', { class: 'pf-modal-body', id: 'pf-manage-list' });
    modal.append(body);
    layer.append(modal);
    layer.onmousedown = (e) => { if (e.target === layer) pfCloseLayer(layer); };
    pfOpenLayer(layer, {
        focus: addBtn,
        onClose: () => {
            _pfManageState = null;
            // the picker, when it's up, shows the new names too
            const picker = document.getElementById('profile-picker-overlay');
            if (picker && picker.style.display !== 'none') {
                pfFetchProfiles().then(d => showProfilePicker(d.profiles || [], !!currentProfile)).catch(() => {});
            }
        },
    });
    await loadProfileManageList();
}

async function loadProfileManageList() {
    const list = document.getElementById('pf-manage-list');
    if (!list) return;
    let data;
    try {
        data = await pfFetchProfiles();
    } catch (e) {
        list.innerHTML = '';
        list.append(pfEl('p', { class: 'pf-form-error', text: "Couldn't load profiles. Check the connection and try again." }));
        return;
    }
    const profiles = data.profiles || [];
    let loginMode = profileLoginMode;
    try { loginMode = !!(await (await fetch('/api/profiles/current')).json()).login_mode; } catch (e) { /* keep what we had */ }
    _pfManageState = {
        profiles, loginMode,
        librarySupported: data.own_library_supported !== false,
        libraryHint: data.own_library_root_hint || '',
    };

    list.innerHTML = '';
    if (loginMode) {
        list.append(pfEl('div', { class: 'pf-note pf-note--warn', style: 'margin-bottom:18px', text: 'Login mode is on. Everyone needs a login password to sign in; set one from the ⋯ menu on their card.' }));
    }
    // with other people around, an open owner profile is an open door
    const me = currentProfile;
    if (me && pfIsOwner(me) && profiles.length > 1) {
        const mine = profiles.find(p => p.id === me.id);
        if (mine && !mine.has_pin) {
            const add = pfEl('button', { type: 'button', class: 'pf-btn', id: 'set-admin-pin-btn', text: 'Add a PIN' });
            add.addEventListener('click', () => openProfilePinModal(mine));
            list.append(pfEl('div', { class: 'pf-nudge', id: 'admin-pin-section' }, [
                pfEl('div', {}, [pfEl('strong', { text: 'Lock your profile' }), pfEl('span', { text: 'Anyone here can open yours and change everything. A PIN stops that.' })]),
                add,
            ]));
        }
    }

    const grid = pfEl('div', { class: 'pf-cards' });
    profiles.forEach(p => grid.append(_pfManageCard(p, loginMode)));
    list.append(grid);
}

function _pfManageCard(p, loginMode) {
    const me = currentProfile || {};
    const isSelf = me.id === p.id;
    // profile 1 is only ever changed by itself
    const canEdit = !pfIsOwner(p) || pfIsOwner(me);
    const summary = _pfSummary(p, loginMode && !p.is_admin);
    const role = pfEl('span', { class: 'pf-card-role' }, [
        _pfRoleLine(p),
        isSelf ? pfEl('span', { class: 'pf-you', text: ' · You' }) : null,
    ]);
    const main = pfEl('button', {
        type: 'button', class: 'pf-card-main', disabled: !canEdit,
        title: canEdit ? `Edit ${p.name}` : null,
    }, [
        pfAvatar(p, 'pf-avatar--md'),
        pfEl('span', { class: 'pf-card-name', text: p.name }),
        role,
        pfEl('span', { class: 'pf-card-summary' }, [
            summary.text,
            summary.warn ? pfEl('span', { class: 'pf-warn', text: ' · ' + summary.warn }) : null,
        ]),
    ]);
    main.addEventListener('click', () => openProfileEditor({ mode: isSelf ? 'self' : 'edit', profile: p }));
    const card = pfEl('div', { class: 'pf-card' + (isSelf ? ' is-current' : '') }, main);

    const items = [];
    if (canEdit) items.push({ label: 'Edit', icon: PF_ICONS.edit, run: () => openProfileEditor({ mode: isSelf ? 'self' : 'edit', profile: p }) });
    if (!isSelf && !pfIsOwner(p) && p.has_pin) items.push({ label: 'Reset PIN', run: () => resetProfilePin(p) });
    if (!isSelf && !p.is_admin) items.push({ label: p.has_password ? 'Change login password' : 'Set login password', run: () => openProfilePasswordModal(p) });
    if (!isSelf && !pfIsOwner(p)) {
        if (items.length) items.push('sep');
        items.push({ label: 'Delete', danger: true, run: () => deleteProfile(p) });
    }
    if (items.length) {
        const more = pfEl('button', {
            type: 'button', class: 'pf-icon-btn pf-card-more', html: PF_ICONS.more,
            'aria-label': `More for ${p.name}`, 'aria-haspopup': 'menu', 'aria-expanded': 'false',
        });
        more.addEventListener('click', (e) => {
            e.stopPropagation();
            if (more.getAttribute('aria-expanded') === 'true') pfCloseMenus();
            else pfOpenMenu(more, items);
        });
        card.append(more);
    }
    return card;
}

async function resetProfilePin(p) {
    const ok = await showConfirmDialog({
        title: `Reset ${p.name}'s PIN?`,
        message: `${p.name} can open their profile without a PIN until they set a new one.`,
        confirmText: 'Reset PIN',
    });
    if (!ok) return;
    try {
        const res = await fetch(`/api/profiles/${p.id}/set-pin`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin: '' }),
        });
        const data = await pfReadJson(res);
        if (!res.ok || !data.success) throw new Error(data.error || "Couldn't reset the PIN");
        try { localStorage.removeItem(_pfPinLengthKey(p.id)); } catch (e) { /* ignore */ }
        showToast(`${p.name}'s PIN is reset`, 'success');
        loadProfileManageList();
    } catch (e) {
        showToast(e.message || 'Connection error', 'error');
    }
}

async function deleteProfile(p) {
    const ok = await showConfirmDialog({
        title: `Delete ${p.name}?`,
        message: 'Their wishlist, watchlist, history and connected accounts go with them. Open issues they reported stay with you.',
        confirmText: 'Delete',
        destructive: true,
    });
    if (!ok) return;
    try {
        const res = await fetch(`/api/profiles/${p.id}`, { method: 'DELETE' });
        const data = await pfReadJson(res);
        if (!res.ok || !data.success) throw new Error(data.error || `Couldn't delete ${p.name}`);
        showToast(`${p.name} is deleted`, 'success');
    } catch (e) {
        showToast(e.message || 'Connection error', 'error');
    }
    loadProfileManageList();
}

// a login password for someone else (login mode's sign-in, not the pin)
function openProfilePasswordModal(p) {
    const modal = pfModal({
        title: `${p.name}'s login password`,
        subtitle: 'Used to sign in when login mode is on. The PIN is separate.',
        size: 'small', layerClass: 'pf-layer--small',
    });
    const pw = pfEl('input', { type: 'password', class: 'pf-input', autocomplete: 'new-password', maxlength: '200', 'aria-label': 'New password', placeholder: 'New password' });
    const confirm = pfEl('input', { type: 'password', class: 'pf-input', autocomplete: 'new-password', maxlength: '200', 'aria-label': 'Confirm password', placeholder: 'Confirm password', style: 'margin-top:10px' });
    const error = pfEl('p', { class: 'pf-form-error', role: 'alert', 'aria-live': 'polite' });
    modal.body.append(pfEl('div', { class: 'pf-field' }, [pw, confirm]), error);

    const post = async (password) => {
        const res = await fetch(`/api/profiles/${encodeURIComponent(p.id)}/set-password`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }),
        });
        const data = await pfReadJson(res);
        if (!res.ok || !data.success) throw new Error(data.error || "Couldn't save the password");
    };
    if (p.has_password) {
        const remove = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--quiet', text: 'Remove password', style: 'margin-right:auto' });
        remove.addEventListener('click', async () => {
            remove.disabled = true;
            try { await post(''); modal.close(); showToast('Login password removed', 'info'); loadProfileManageList(); }
            catch (e) { error.textContent = e.message; remove.disabled = false; }
        });
        modal.foot.append(remove);
    }
    const cancel = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--quiet', text: 'Cancel', onclick: () => modal.close() });
    const save = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--primary', text: 'Save password' });
    save.addEventListener('click', async () => {
        error.textContent = '';
        if (pw.value.length < 4) { error.textContent = 'Use at least 4 characters'; pw.focus(); return; }
        if (pw.value !== confirm.value) { error.textContent = "The passwords don't match"; confirm.focus(); return; }
        save.disabled = true;
        try { await post(pw.value); modal.close(); showToast(`Login password set for ${p.name}`, 'success'); loadProfileManageList(); }
        catch (e) { error.textContent = e.message; save.disabled = false; }
    });
    confirm.addEventListener('keydown', (e) => { if (e.key === 'Enter') save.click(); });
    modal.foot.append(cancel, save);
    modal.open(pw);
}

// set a pin on your own profile (the manage sheet's nudge)
function openProfilePinModal(p) {
    const modal = pfModal({ title: 'Add a PIN', subtitle: `${PROFILE_PIN_MIN} to ${PROFILE_PIN_MAX} digits. You'll type it when you open your profile.`, size: 'small', layerClass: 'pf-layer--small' });
    const pin = pfEl('input', { type: 'password', class: 'pf-input', inputmode: 'numeric', autocomplete: 'new-password', maxlength: String(PROFILE_PIN_MAX), 'aria-label': 'New PIN', placeholder: 'New PIN' });
    const confirm = pfEl('input', { type: 'password', class: 'pf-input', inputmode: 'numeric', autocomplete: 'new-password', maxlength: String(PROFILE_PIN_MAX), 'aria-label': 'Confirm PIN', placeholder: 'Confirm PIN', style: 'margin-top:10px' });
    const error = pfEl('p', { class: 'pf-form-error', role: 'alert', 'aria-live': 'polite' });
    modal.body.append(pfEl('div', { class: 'pf-field' }, [pin, confirm]), error);
    const save = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--primary', text: 'Save PIN' });
    save.addEventListener('click', async () => {
        const problem = profilePinError(pin.value);
        if (problem) { error.textContent = problem; pin.focus(); return; }
        if (pin.value !== confirm.value) { error.textContent = "The PINs don't match"; confirm.focus(); return; }
        save.disabled = true;
        try {
            const res = await fetch(`/api/profiles/${p.id}/set-pin`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin: pin.value }),
            });
            const data = await pfReadJson(res);
            if (!res.ok || !data.success) throw new Error(data.error || "Couldn't save the PIN");
            if (currentProfile && currentProfile.id === p.id) currentProfile.has_pin = true;
            modal.close();
            showToast('PIN saved', 'success');
            loadProfileManageList();
        } catch (e) {
            error.textContent = e.message || 'Connection error';
            save.disabled = false;
        }
    });
    confirm.addEventListener('keydown', (e) => { if (e.key === 'Enter') save.click(); });
    modal.foot.append(pfEl('button', { type: 'button', class: 'pf-btn pf-btn--quiet', text: 'Cancel', onclick: () => modal.close() }), save);
    modal.open(pin);
}

// ── The profile editor: add, edit someone, or edit yourself ───────────
// add is two steps (who, then what they can do); edit shows the same two
// as tabs. content rating filters don't exist yet, so no preset claims one.

const PROFILE_PRESETS = [
    { id: 'adult', name: 'Adult', desc: 'Music and movies, downloads anything', sides: 'both', can_download: true },
    { id: 'teen', name: 'Teen', desc: 'Music and movies, asks before downloading', sides: 'both', can_download: false },
    { id: 'kids', name: 'Kids', desc: 'Asks before downloading. Pick their pages below', sides: 'both', can_download: false },
    { id: 'guest', name: 'Guest', desc: 'Listens to music, nothing else', sides: 'music', can_download: false },
];

async function openProfileEditor({ mode = 'create', profile = null } = {}) {
    pfCloseMenus(false);
    const me = currentProfile;
    if (!me) return;
    if (mode === 'self') profile = { ...me, ...(_pfProfilesCache.find(p => p.id === me.id) || {}) };
    const isCreate = mode === 'create';
    const isSelf = mode === 'self';
    const adminEditsOther = !isCreate && !isSelf && !!me.is_admin;
    const p = profile || {};
    const loginMode = _pfManageState ? _pfManageState.loginMode : profileLoginMode;
    const librarySupported = _pfManageState ? _pfManageState.librarySupported : true;
    const libraryHint = _pfManageState
        ? _pfManageState.libraryHint.replace('<name>', (p.name || 'profile').toLowerCase().replace(/[^a-z0-9]+/g, '-'))
        : '';

    // everything the form edits, in one place
    const st = {
        name: p.name || '',
        avatar_color: p.avatar_color || PROFILE_COLORS[0][0],
        avatar_url: p.avatar_url || '',
        avatarKind: p.avatar_url ? 'image' : 'initials',
        is_admin: !!p.is_admin,
        sides: isCreate ? 'both' : _pfSidesOf({ ...p, is_admin: false }),
        can_download: isCreate ? true : !pfNoDownloads({ ...p, is_admin: false }),
        allowed_pages: Array.isArray(p.allowed_pages) ? normalizeProfilePageList(p.allowed_pages).slice() : null,
        home_page: p.home_page ? normalizeProfilePageId(p.home_page) : '',
        pinOn: !!p.has_pin,
        pin: '',
        password: '',
        library_mode: p.library_mode === 'own' ? 'own' : 'shared',
        library_root: p.library_root || libraryHint || '',
        preset: isCreate ? 'adult' : null,
    };
    const original = { ...st };

    const title = isCreate ? 'Add a profile' : isSelf ? 'Your profile' : `Edit ${p.name}`;
    const modal = pfModal({ title, size: '', layerClass: 'pf-layer--editor', dismissable: false });
    const stepIds = ['identity', 'access'];
    const stepNames = isSelf ? ['Profile', 'Sign-in'] : ['Who', 'Access'];
    let step = 0;
    let furthest = isCreate ? 0 : 1;

    // step tabs
    const tabs = pfEl('div', { class: 'pf-steps', role: 'tablist' });
    const tabButtons = stepNames.map((name, i) => {
        const tab = pfEl('button', { type: 'button', class: 'pf-step', role: 'tab', text: (isCreate ? `${i + 1}. ` : '') + name });
        tab.addEventListener('click', () => { if (i <= furthest) goTo(i); });
        tabs.append(tab);
        return tab;
    });
    const panes = stepIds.map(() => pfEl('div', { role: 'tabpanel' }));
    const formError = pfEl('p', { class: 'pf-form-error', role: 'alert', 'aria-live': 'polite' });
    modal.body.append(tabs, ...panes, formError);

    // ── step 1: who ──
    const preview = pfEl('div', { class: 'pf-identity-preview' });
    const renderPreview = () => {
        preview.innerHTML = '';
        preview.append(pfAvatar({
            name: st.name.trim() || '?', avatar_color: st.avatar_color,
            avatar_url: st.avatarKind === 'image' ? st.avatar_url.trim() : '',
        }, 'pf-avatar--lg'));
    };
    const nameId = 'pf-name-' + (++_pfUid);
    const nameInput = pfEl('input', { type: 'text', id: nameId, class: 'pf-input', maxlength: '20', autocomplete: 'off', value: st.name, 'aria-describedby': nameId + '-err' });
    const nameError = pfEl('p', { class: 'pf-field-error', id: nameId + '-err', role: 'alert' });
    nameInput.addEventListener('input', () => {
        st.name = nameInput.value;
        nameError.textContent = '';
        nameInput.classList.remove('is-invalid');
        renderPreview();
    });

    const seg = pfEl('div', { class: 'pf-seg', role: 'group', 'aria-label': 'Avatar' });
    const segInitials = pfEl('button', { type: 'button', text: 'Initial' });
    const segImage = pfEl('button', { type: 'button', text: 'Image' });
    seg.append(segInitials, segImage);
    const swatches = pfEl('div', { class: 'pf-swatches', role: 'group', 'aria-label': 'Colour' });
    PROFILE_COLORS.forEach(([color, colorName]) => {
        const sw = pfEl('button', { type: 'button', class: 'pf-swatch', style: `background:${color}`, 'aria-label': colorName, 'data-color': color });
        sw.addEventListener('click', () => { st.avatar_color = color; syncAvatarControls(); renderPreview(); });
        swatches.append(sw);
    });
    const urlInput = pfEl('input', { type: 'url', class: 'pf-input', placeholder: 'https://…/photo.jpg', 'aria-label': 'Image URL', value: st.avatar_url });
    urlInput.addEventListener('input', () => { st.avatar_url = urlInput.value; renderPreview(); });
    const imageHelp = pfEl('p', { class: 'pf-help', text: "A link to a square picture. If it won't load, the initial shows instead." });
    const imageBox = pfEl('div', {}, [urlInput, imageHelp]);
    const syncAvatarControls = () => {
        segInitials.setAttribute('aria-pressed', String(st.avatarKind === 'initials'));
        segImage.setAttribute('aria-pressed', String(st.avatarKind === 'image'));
        swatches.style.display = st.avatarKind === 'initials' ? '' : 'none';
        imageBox.style.display = st.avatarKind === 'image' ? '' : 'none';
        swatches.querySelectorAll('.pf-swatch').forEach(sw => sw.setAttribute('aria-pressed', String(sw.dataset.color === st.avatar_color)));
    };
    segInitials.addEventListener('click', () => { st.avatarKind = 'initials'; syncAvatarControls(); renderPreview(); });
    segImage.addEventListener('click', () => { st.avatarKind = 'image'; syncAvatarControls(); renderPreview(); setTimeout(() => urlInput.focus(), 0); });

    panes[0].append(
        preview,
        pfEl('div', { class: 'pf-field' }, [pfEl('label', { class: 'pf-label', for: nameId, text: 'Name' }), nameInput, nameError]),
        pfEl('div', { class: 'pf-field' }, [pfEl('span', { class: 'pf-label', text: 'Avatar' }), seg, swatches, imageBox]),
    );
    renderPreview();
    syncAvatarControls();

    // ── step 2: access (or sign-in, for yourself) ──
    const pane2 = panes[1];
    let pageBoxes = [];
    const homeSelect = pfEl('select', { class: 'pf-input', 'aria-label': 'Opens on' });
    const renderHomeOptions = () => {
        const sides = (isSelf || st.is_admin) ? (me.is_admin || st.is_admin ? 'both' : _pfSidesOf(me)) : st.sides;
        const pages = (isSelf || st.is_admin) ? (st.is_admin || me.is_admin ? null : me.allowed_pages) : st.allowed_pages;
        const options = profileHomeOptions(sides, pages);
        homeSelect.innerHTML = '';
        homeSelect.append(pfEl('option', { value: '', text: 'Automatic' }));
        options.forEach(o => homeSelect.append(pfEl('option', { value: o.value, text: o.label })));
        if (st.home_page && !options.some(o => o.value === st.home_page)) st.home_page = '';
        homeSelect.value = st.home_page;
    };
    homeSelect.addEventListener('change', () => { st.home_page = homeSelect.value; });
    const homeField = pfEl('div', { class: 'pf-field' }, [
        pfEl('span', { class: 'pf-label', text: 'Opens on' }), homeSelect,
        pfEl('p', { class: 'pf-help', text: 'Automatic is the first page they can use.' }),
    ]);

    // pin: on/off plus the digits
    const pinInput = pfEl('input', {
        type: 'password', class: 'pf-input', inputmode: 'numeric', autocomplete: 'new-password',
        maxlength: String(PROFILE_PIN_MAX), 'aria-label': 'PIN',
        placeholder: p.has_pin ? 'New PIN, or leave empty to keep it' : `${PROFILE_PIN_MIN} to ${PROFILE_PIN_MAX} digits`,
    });
    const pinError = pfEl('p', { class: 'pf-field-error', role: 'alert' });
    pinInput.addEventListener('input', () => { st.pin = pinInput.value; pinError.textContent = ''; });
    const pinExtra = pfEl('div', { class: 'pf-group-extra' }, [pinInput, pinError]);
    const pinDesc = isSelf
        ? (pfIsOwner(me) ? 'Asked when anyone opens your profile. Turning it off also turns off the lock screen.' : 'Asked when anyone opens your profile.')
        : 'Asked when someone picks this profile.';
    const pinSwitch = pfSwitch({
        title: 'PIN to open this profile', desc: pinDesc, checked: st.pinOn,
        onChange: (on) => { st.pinOn = on; pinExtra.style.display = on ? '' : 'none'; if (on) setTimeout(() => pinInput.focus(), 0); },
    });
    pinExtra.style.display = st.pinOn ? '' : 'none';

    // login password: needed to create someone while login mode is on; your own lives in sign-in
    const passwordInput = pfEl('input', {
        type: 'password', class: 'pf-input', autocomplete: 'new-password', maxlength: '200', 'aria-label': 'Login password',
        placeholder: isSelf && p.has_password ? 'New password, or leave empty to keep it' : 'Login password',
    });
    const passwordError = pfEl('p', { class: 'pf-field-error', role: 'alert' });
    passwordInput.addEventListener('input', () => { st.password = passwordInput.value; passwordError.textContent = ''; });

    if (isSelf) {
        pane2.append(
            pfEl('div', { class: 'pf-section-title', text: 'Privacy' }),
            pfEl('div', { class: 'pf-group' }, [pinSwitch.row, pinExtra]),
            pfEl('div', { class: 'pf-section-title', text: 'Login password' }),
            pfEl('div', { class: 'pf-field' }, [passwordInput, passwordError,
                pfEl('p', { class: 'pf-help', text: 'For signing in when login mode is on. Separate from the PIN.' })]),
            pfEl('div', { class: 'pf-section-title', text: 'Home' }),
            homeField,
        );
    } else {
        // admin switch (editing someone else only)
        const accessBox = pfEl('div');
        const adminNote = pfEl('p', { class: 'pf-note', text: 'Admins can use everything, including settings and managing profiles.' });
        if (adminEditsOther) {
            const adminSwitch = pfSwitch({
                title: 'Admin', desc: 'Can manage profiles, settings and every page.', checked: st.is_admin,
                onChange: (on) => { st.is_admin = on; syncAccess(); },
            });
            pane2.append(pfEl('div', { class: 'pf-group', style: 'margin-bottom:6px' }, adminSwitch.row));
        }

        // presets fill the switches below
        const presets = pfEl('div', { class: 'pf-presets', role: 'radiogroup', 'aria-label': 'Start from' });
        const presetButtons = PROFILE_PRESETS.map(preset => {
            const btn = pfEl('button', { type: 'button', class: 'pf-preset', role: 'radio' }, [
                pfEl('span', { class: 'pf-preset-name', text: preset.name }),
                pfEl('span', { class: 'pf-preset-desc', text: preset.desc }),
            ]);
            btn.addEventListener('click', () => {
                st.preset = preset.id;
                st.sides = preset.sides;
                st.can_download = preset.can_download;
                syncAccess();
            });
            presets.append(btn);
            return { btn, preset };
        });

        const musicSwitch = pfSwitch({
            title: 'Music', desc: 'Library, discover, playlists, podcasts and audiobooks.',
            onChange: (on) => setSide('music', on),
        });
        const videoSwitch = pfSwitch({
            title: 'Movies & TV', desc: 'The video side: movies, shows and the calendar.',
            onChange: (on) => setSide('video', on),
        });
        const sideError = pfEl('p', { class: 'pf-field-error', role: 'alert', style: 'padding:0 16px 12px;margin:0' });
        const dlSwitch = pfSwitch({
            title: 'Download without asking',
            desc: 'Covers music, podcasts, audiobooks and video. Off: what they add becomes a request you approve.',
            onChange: (on) => { st.can_download = on; st.preset = _pfMatchPreset(); syncAccess(); },
        });

        function setSide(side, on) {
            const hasMusic = st.sides !== 'video';
            const hasVideo = st.sides !== 'music';
            const nextMusic = side === 'music' ? on : hasMusic;
            const nextVideo = side === 'video' ? on : hasVideo;
            if (!nextMusic && !nextVideo) {
                sideError.textContent = 'Keep at least one side on.';
                syncAccess();
                return;
            }
            sideError.textContent = '';
            st.sides = nextMusic && nextVideo ? 'both' : nextMusic ? 'music' : 'video';
            st.preset = _pfMatchPreset();
            syncAccess();
        }

        // advanced: the page list, the home page, own library
        const pagesBox = pfEl('div');
        const renderPages = () => {
            pagesBox.innerHTML = '';
            pageBoxes = [];
            const allowed = st.allowed_pages ? new Set(st.allowed_pages) : null;
            PROFILE_PAGE_GROUPS.forEach(group => {
                if (!_pfSidesAllow(st.sides, group.side)) return;
                pagesBox.append(pfEl('div', { class: 'pf-pages-group', text: group.label }));
                const grid = pfEl('div', { class: 'pf-pages' });
                group.pages.forEach(pageId => {
                    const cb = pfEl('input', { type: 'checkbox', value: pageId });
                    cb.checked = allowed ? allowed.has(pageId) : true;
                    cb.addEventListener('change', () => { readPages(); renderHomeOptions(); });
                    pageBoxes.push(cb);
                    grid.append(pfEl('label', {}, [cb, _pfPlainPageLabel(pageId)]));
                });
                pagesBox.append(grid);
            });
            pagesBox.append(pfEl('p', { class: 'pf-help', text: 'Help and Issues are always there.' }));
        };
        // every box ticked means "all pages", which also covers pages added later
        const readPages = () => {
            const shown = pageBoxes.filter(cb => cb.checked).map(cb => cb.value);
            if (shown.length === pageBoxes.length) { st.allowed_pages = null; return; }
            // pages of a side that's off keep whatever they were
            const hiddenKept = (st.allowed_pages || []).filter(id => !_pfSidesAllow(st.sides, profilePageSide(id)));
            st.allowed_pages = shown.concat(hiddenKept);
        };

        const details = pfEl('details', { class: 'pf-details' }, [pfEl('summary', { text: 'Choose pages' })]);
        details.append(pagesBox, homeField);
        if (!isCreate) {
            const folderInput = pfEl('input', { type: 'text', class: 'pf-input', spellcheck: 'false', autocomplete: 'off', 'aria-label': 'Output folder', placeholder: libraryHint || '/app/libraries/name', value: st.library_root });
            folderInput.addEventListener('input', () => { st.library_root = folderInput.value; });
            const folderBox = pfEl('div', { class: 'pf-group-extra' }, [
                folderInput,
                pfEl('p', { class: 'pf-help', text: 'Docker: mount this folder in docker-compose.yml. Then add it as a second music library on Plex or Jellyfin and pick that library under their account.' }),
            ]);
            const inactive = !librarySupported;
            const ownSwitch = pfSwitch({
                title: 'Own library',
                desc: inactive ? 'Needs Plex or Jellyfin. With Navidrome or standalone their downloads go to the shared folder.'
                    : 'Their downloads go to their own folder and their own server library.',
                checked: st.library_mode === 'own',
                disabled: inactive && st.library_mode !== 'own',
                onChange: (on) => { st.library_mode = on ? 'own' : 'shared'; folderBox.style.display = on ? '' : 'none'; },
            });
            folderBox.style.display = st.library_mode === 'own' ? '' : 'none';
            details.append(pfEl('div', { class: 'pf-group', style: 'margin-top:4px' }, [ownSwitch.row, folderBox]));
        }

        const accessParts = [
            pfEl('div', { class: 'pf-section-title', text: 'Start from' }), presets,
            pfEl('div', { class: 'pf-section-title', text: 'What they can use' }),
            pfEl('div', { class: 'pf-group' }, [musicSwitch.row, videoSwitch.row, sideError]),
            pfEl('div', { class: 'pf-section-title', text: 'Downloads' }),
            pfEl('div', { class: 'pf-group' }, dlSwitch.row),
        ];
        accessBox.append(...accessParts);
        pane2.append(adminNote, accessBox);
        pane2.append(pfEl('div', { class: 'pf-section-title', text: 'Privacy' }), pfEl('div', { class: 'pf-group' }, [pinSwitch.row, pinExtra]));
        if (isCreate && loginMode) {
            pane2.append(pfEl('div', { class: 'pf-section-title', text: 'Login password' }),
                pfEl('div', { class: 'pf-field' }, [passwordInput, passwordError,
                    pfEl('p', { class: 'pf-help', text: 'Login mode is on, so they need one to sign in.' })]));
        }
        pane2.append(details);

        function _pfMatchPreset() {
            if (st.preset) {
                const current = PROFILE_PRESETS.find(x => x.id === st.preset);
                if (current && current.sides === st.sides && current.can_download === st.can_download) return st.preset;
            }
            const match = PROFILE_PRESETS.find(x => x.sides === st.sides && x.can_download === st.can_download);
            return match ? match.id : null;
        }

        function syncAccess() {
            adminNote.style.display = st.is_admin ? '' : 'none';
            accessBox.style.display = st.is_admin ? 'none' : '';
            details.style.display = st.is_admin ? 'none' : '';
            presetButtons.forEach(({ btn, preset }) => btn.setAttribute('aria-checked', String(st.preset === preset.id)));
            musicSwitch.set(st.sides !== 'video');
            videoSwitch.set(st.sides !== 'music');
            dlSwitch.set(st.can_download);
            renderPages();
            renderHomeOptions();
        }
        if (!isCreate) st.preset = _pfMatchPreset();
        syncAccess();
    }
    renderHomeOptions();

    // ── footer ──
    const back = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--quiet', text: 'Back' });
    const cancel = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--quiet', text: 'Cancel', onclick: () => modal.close() });
    const next = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--primary', text: 'Next' });
    const save = pfEl('button', { type: 'button', class: 'pf-btn pf-btn--primary', text: isCreate ? 'Add profile' : 'Save' });
    modal.foot.append(pfEl('span', { class: 'pf-foot-note' }), back, cancel, next, save);
    back.addEventListener('click', () => goTo(0));
    next.addEventListener('click', () => { if (checkIdentity()) goTo(1); });
    save.addEventListener('click', () => submit());
    nameInput.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        if (isCreate && step === 0) next.click(); else save.click();
    });

    function goTo(i) {
        step = i;
        furthest = Math.max(furthest, i);
        panes.forEach((pane, idx) => { pane.style.display = idx === i ? '' : 'none'; });
        tabButtons.forEach((tab, idx) => {
            tab.setAttribute('aria-selected', String(idx === i));
            tab.disabled = idx > furthest;
        });
        back.style.display = isCreate && i === 1 ? '' : 'none';
        cancel.style.display = isCreate && i === 1 ? 'none' : '';
        next.style.display = isCreate && i === 0 ? '' : 'none';
        save.style.display = isCreate && i === 0 ? 'none' : '';
        modal.body.scrollTop = 0;
    }

    function checkIdentity() {
        if (!st.name.trim()) {
            nameError.textContent = 'Give the profile a name';
            nameInput.classList.add('is-invalid');
            goTo(0);
            nameInput.focus();
            return false;
        }
        return true;
    }

    // everything is checked before anything is sent
    function validate() {
        formError.textContent = '';
        if (!checkIdentity()) return false;
        if (st.pinOn && (st.pin || !p.has_pin || isCreate)) {
            const problem = profilePinError(st.pin);
            if (problem) {
                pinError.textContent = st.pin ? problem : 'Type a PIN, or turn the PIN off';
                goTo(1);
                pinInput.focus();
                return false;
            }
        }
        if (st.password && st.password.length < 4) {
            passwordError.textContent = 'Use at least 4 characters';
            goTo(1);
            passwordInput.focus();
            return false;
        }
        if (isCreate && loginMode && !st.password) {
            passwordError.textContent = 'Login mode is on, so they need a password';
            goTo(1);
            passwordInput.focus();
            return false;
        }
        if (!isSelf && !st.is_admin && st.library_mode === 'own' && !st.library_root.trim()) {
            formError.textContent = 'An own library needs an output folder.';
            goTo(1);
            return false;
        }
        return true;
    }

    function identityPayload() {
        return {
            name: st.name.trim(),
            avatar_color: st.avatar_color,
            avatar_url: st.avatarKind === 'image' ? (st.avatar_url.trim() || null) : null,
            home_page: st.home_page || null,
        };
    }

    function accessPayload() {
        if (st.is_admin) return {};
        return {
            allowed_sides: st.sides,
            can_download: st.can_download,
            allowed_pages: st.allowed_pages,
        };
    }

    async function send(url, method, body) {
        const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const data = await pfReadJson(res);
        if (!res.ok || !data.success) {
            const err = new Error(data.error || 'Something went wrong');
            err.status = res.status;
            throw err;
        }
        return data;
    }

    async function submit() {
        if (!validate()) return;
        save.disabled = true;
        try {
            if (isCreate) await submitCreate();
            else await submitEdit();
        } finally {
            save.disabled = false;
        }
    }

    async function submitCreate() {
        const body = {
            ...identityPayload(), ...accessPayload(),
            pin: st.pinOn ? st.pin : undefined,
            password: st.password || undefined,
        };
        try {
            await send('/api/profiles', 'POST', body);
        } catch (e) {
            if (e.status === 409) {
                nameError.textContent = 'Someone already has that name';
                nameInput.classList.add('is-invalid');
                goTo(0);
                nameInput.focus();
            } else {
                formError.textContent = e.message || 'Connection error';
            }
            return;
        }
        modal.close();
        showToast(`${body.name} is ready`, 'success');
        _pfAfterSave();
    }

    // one PUT for the profile, then the pin and the password on their own.
    // if a later one fails the earlier ones stay saved, and the message says
    // exactly which is which.
    async function submitEdit() {
        const targetId = isSelf ? me.id : p.id;
        const saved = [];
        const failed = [];
        const body = identityPayload();
        if (adminEditsOther) {
            Object.assign(body, accessPayload());
            if (st.is_admin !== original.is_admin) body.is_admin = st.is_admin;
            if (!st.is_admin) {
                body.library_mode = st.library_mode;
                body.library_root = st.library_mode === 'own' ? st.library_root.trim() : '';
            }
        }
        try {
            const result = await send(`/api/profiles/${targetId}`, 'PUT', body);
            saved.push('profile');
            if (isSelf) {
                setCurrentProfile({ ...currentProfile, ...body, ...(result.profile || {}) });
            }
        } catch (e) {
            formError.textContent = e.message || 'Connection error';
            return;
        }

        const pinChanged = st.pinOn ? !!st.pin : !!p.has_pin;
        if (pinChanged) {
            try {
                await send(`/api/profiles/${targetId}/set-pin`, 'POST', { pin: st.pinOn ? st.pin : '' });
                saved.push(st.pinOn ? 'PIN' : 'PIN removal');
                try {
                    if (isSelf && st.pinOn) localStorage.setItem(_pfPinLengthKey(targetId), String(st.pin.length));
                    else localStorage.removeItem(_pfPinLengthKey(targetId));
                } catch (e) { /* ignore */ }
                if (isSelf) currentProfile.has_pin = st.pinOn;
            } catch (e) {
                failed.push(['PIN', e.message]);
            }
        }
        if (isSelf && st.password) {
            try {
                const data = await send(`/api/profiles/${targetId}/set-password`, 'POST', { password: st.password });
                saved.push('login password');
                currentProfile.has_password = !!data.has_password;
            } catch (e) {
                failed.push(['login password', e.message]);
            }
        }

        if (failed.length) {
            formError.textContent = `Saved: ${saved.join(', ')}. ` + failed.map(([what, why]) => `${what} didn't save: ${why}`).join(' ');
            _pfAfterSave();
            return;
        }
        modal.close();
        showToast('Saved', 'success');
        _pfAfterSave();
    }

    goTo(0);
    modal.open(nameInput);
}

function _pfAfterSave() {
    if (document.getElementById('pf-manage-list')) loadProfileManageList();
    const picker = document.getElementById('profile-picker-overlay');
    if (picker && picker.style.display !== 'none') {
        pfFetchProfiles().then(d => showProfilePicker(d.profiles || [], !!currentProfile)).catch(() => {});
    }
}

// Service worker registration. Runs as soon as the JS parses (doesn't
// need to wait for DOMContentLoaded). Cache-first image strategy +
// stale-while-revalidate static shell — see /sw.js for details. Skipped
// when the API isn't available (older browsers, file:// origin) or when
// the page is loaded from a non-secure origin (SW requires HTTPS or
// localhost).
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register(window.SoulSyncURL?.resolve('/sw.js') || '/sw.js', { scope: window.SoulSyncURL?.resolve('/') || '/' })
            .catch((err) => console.warn('[SW] registration failed:', err));
    });
}

document.addEventListener('DOMContentLoaded', async function () {
    console.log('SoulSync WebUI initializing...');

    // Check if first-run setup wizard should be shown
    const params = new URLSearchParams(window.location.search);
    const forceSetup = params.get('setup') === '1';
    let showWizard = forceSetup;

    if (!forceSetup) {
        try {
            const setupResp = await fetch('/api/setup/status');
            // Fail-safe (#842): only launch the wizard when the server DEFINITIVELY
            // says setup isn't done. A non-OK response (e.g. 401 while the launch
            // PIN is locked) must NOT trigger the wizard — otherwise a PIN-gated
            // returning user gets the full setup flow every visit.
            if (setupResp.ok) {
                const setupData = await setupResp.json();
                if (setupData.setup_complete === false) {
                    showWizard = true;
                    localStorage.removeItem('soulsync_setup_complete');
                }
            }
        } catch (e) {
            console.warn('Setup status check failed, continuing normal init:', e);
        }
    }

    if (showWizard && typeof openSetupWizard === 'function') {
        window._onSetupWizardComplete = function () {
            _continueAppInit();
        };
        openSetupWizard();
        return; // Defer init until wizard closes
    }

    _continueAppInit();
});

async function _continueAppInit() {
    // Check profiles first — may show picker instead of app
    const profileReady = await initProfileSystem();
    if (!profileReady) {
        console.log('Waiting for profile selection...');
        return; // App init deferred until profile is selected via picker
    }

    initApp();
}

function initApp() {
    // Unlocked / authenticated — reveal the app (the lock screens hide it via
    // body.app-locked so a bypassed overlay shows nothing). Do this FIRST so
    // component init below measures real layout, not a display:none container.
    document.body.classList.remove('app-locked');
    // Initialize components
    initializeNavigation();
    initializeMobileNavigation();
    initializeMediaPlayer();
    initExpandedPlayer();
    // initializeSyncPage() was here. It ran on EVERY page load, not just sync,
    // and every branch inside it looks up sync markup that no longer exists —
    // the Beatport tab button, #beatport-clear-btn, the tab strip. Its one
    // cross-cutting job was initializeLiveLogViewer(), which targets
    // #sync-log-area; the React sidebar renders that textarea and drives its
    // own /api/logs poller (sync-sidebar.tsx), so the vanilla half is now a
    // no-op that would only race it.
    initializeWatchlist();
    if (typeof initializeSpotifyAuthCompletionListener === 'function') {
        initializeSpotifyAuthCompletionListener();
    }


    // Initialize WebSocket connection (falls back to HTTP polling if unavailable)
    initializeWebSocket();

    // Start global service status polling for sidebar (works on all pages)
    // Initial fetch for immediate data, then setInterval as fallback when WebSocket is disconnected
    fetchAndUpdateServiceStatus();
    setInterval(fetchAndUpdateServiceStatus, 5000); // Every 5 seconds (no-op when WebSocket active)

    // Check for updates on load and every hour
    checkForUpdates();
    setInterval(checkForUpdates, 3600000);

    // Refresh key data immediately when user returns to this tab
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            fetchAndUpdateServiceStatus();
            // No dashboard-specific branch since the flip: the React cards'
            // own pollers are hidden-gated, so the tick that lands after the
            // tab returns refreshes them (the old .page.active check could
            // never match the React page anyway).
        }
    });

    // Start always-on download polling (batched, minimal overhead)
    startGlobalDownloadPolling();

    // Load initial data
    loadInitialData();

    // Handle window resize to re-check track title scrolling
    window.addEventListener('resize', function () {
        if (currentTrack) {
            const trackTitleElement = document.getElementById('track-title');
            const trackTitle = currentTrack.title || 'Unknown Track';
            setTimeout(() => {
                checkAndEnableScrolling(trackTitleElement, trackTitle);
            }, 100); // Small delay to allow layout to settle
        }
    });

    console.log('SoulSync WebUI initialized successfully!');
}

// ===============================
// NAVIGATION SYSTEM
// ===============================

function initializeNavigation() {
    // Sidebar navigation is now driven by native link navigation.
    // Page activation and active-state styling are synchronized from the
    // current URL by the shell bridge and route controllers.
}

const _DEEPLINK_VALID_PAGES = new Set([
    'dashboard', 'sync', 'search', 'discover', 'automations',
    'library', 'import', 'settings', 'help', 'issues', 'stats', 'watchlist',
    'wishlist', 'active-downloads', 'artist-detail', 'playlist-explorer',
    'hydrabase', 'tools', 'chat', 'podcasts', 'audiobooks', 'requests'
]);

function _getPageFromPath() {
    const router = getWebRouter();
    const resolved = router?.resolvePageId?.((window.SoulSyncURL?.strip(window.location.pathname) ?? window.location.pathname));
    if (resolved) return resolved;

    const path = (window.SoulSyncURL?.strip(window.location.pathname) ?? window.location.pathname).replace(/^\/+|\/+$/g, '');
    if (!path) return 'dashboard';
    const segs = path.split('/');
    const basePage = segs[0];
    if (!_DEEPLINK_VALID_PAGES.has(basePage)) return 'dashboard';
    // Context-dependent pages fall back to a sensible parent
    if (basePage === 'playlist-explorer') return 'library';
    return basePage;
}

function _normalizeArtistDetailSource(source) {
    const value = (source || '').toString().trim().toLowerCase();
    return value || 'library';
}

function buildArtistDetailPath(artistId, source = null, name = null) {
    if (!artistId) {
        throw new Error('artistId is required for artist-detail navigation');
    }
    const normalizedSource = _normalizeArtistDetailSource(source);
    let path = '/artist-detail/' + encodeURIComponent(normalizedSource) + '/' + encodeURIComponent(String(artistId));
    // Some sources (Bandcamp) have no numeric-ID lookup API at all — the
    // artist's display name has to travel with the URL, or a page load /
    // browser-back landing on this route has nothing to resolve against.
    if (name) {
        path += '?name=' + encodeURIComponent(name);
    }
    return path;
}

function parseArtistDetailPath(pathname = (window.SoulSyncURL?.strip(window.location.pathname) ?? window.location.pathname)) {
    const segs = String(pathname || '').split('/').filter(Boolean);
    if (segs[0] !== 'artist-detail' || segs.length < 3) return null;

    const source = decodeURIComponent(segs[1] || '');
    const artistId = decodeURIComponent(segs.slice(2).join('/'));
    if (!source || !artistId) return null;

    const name = new URLSearchParams(window.location.search).get('name') || '';

    return {
        artistId,
        source: source.toLowerCase() === 'library' ? null : source,
        name,
    };
}

// ---- Label detail (a record label's catalog, monitored like a watchlist) ----
// A static legacy page; the label MBID rides the query string so a reload /
// browser-back can re-resolve it. Purely additive, parallel to artist-detail
// but far simpler (no dynamic route, no label stack).
let _labelDetailState = { id: null, name: null };
let _labelDetailReturnTo = 'search';   // where the Back button returns to

function buildLabelDetailPath(labelId, name = null) {
    if (!labelId) throw new Error('labelId is required for label-detail navigation');
    // Real path-based route (like artist-detail) so a refresh reloads the page.
    let path = '/label-detail/' + encodeURIComponent(String(labelId));
    if (name) path += '?name=' + encodeURIComponent(name);
    return path;
}


function navigateToLabelDetail(labelId, name = null, options = {}) {
    if (!labelId) return;
    // Remember where we came from so the label-detail Back button returns there
    // (raw history.back() is unreliable through the SPA router).
    if (typeof currentPage === 'string' && currentPage && currentPage !== 'label-detail') {
        _labelDetailReturnTo = currentPage;
    }
    window._labelDetailReturnTo = _labelDetailReturnTo;   // read by label-detail.js
    // The TanStack route component re-fires this on mount; skip the reload if
    // we're already showing this exact label (mirrors navigateToArtistDetail).
    if (String(labelId) === String(_labelDetailState.id) && currentPage === 'label-detail') {
        return;
    }
    _labelDetailState = { id: String(labelId), name: name || '' };
    navigateToPage('label-detail', {
        labelId: String(labelId),
        labelName: name || '',
        skipRouteChange: options.skipRouteChange === true,
    });
}

// ===============================
// MOBILE NAVIGATION
// ===============================

function initializeMobileNavigation() {
    const hamburgerBtn = document.getElementById('hamburger-btn');
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.getElementById('mobile-overlay');

    if (!hamburgerBtn || !sidebar || !overlay) return;

    // One explicit state: the drawer is open only because someone opened it at
    // a mobile width. It is not a width, and it is not carried over from the
    // desktop layout.
    function openMobileNav() {
        sidebar.classList.add('mobile-open');
        hamburgerBtn.classList.add('active');
        hamburgerBtn.setAttribute('aria-expanded', 'true');
        hamburgerBtn.setAttribute('aria-label', 'Close navigation');
        overlay.classList.add('active');
        document.body.classList.add('mobile-nav-open');
        // Focus moves into the drawer so a keyboard isn't left behind the
        // backdrop, and Escape below puts it back on the opener.
        const first = sidebar.querySelector('.nav-button, a[href], button:not([disabled])');
        if (first) first.focus();
    }

    function closeMobileNav(restoreFocus) {
        const wasOpen = sidebar.classList.contains('mobile-open');
        sidebar.classList.remove('mobile-open');
        hamburgerBtn.classList.remove('active');
        hamburgerBtn.setAttribute('aria-expanded', 'false');
        hamburgerBtn.setAttribute('aria-label', 'Open navigation');
        overlay.classList.remove('active');
        document.body.classList.remove('mobile-nav-open');
        if (wasOpen && restoreFocus === true) hamburgerBtn.focus();
    }

    hamburgerBtn.addEventListener('click', () => {
        if (sidebar.classList.contains('mobile-open')) {
            closeMobileNav();
        } else {
            openMobileNav();
        }
    });

    overlay.addEventListener('click', () => closeMobileNav());

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (!sidebar.classList.contains('mobile-open')) return;
        closeMobileNav(true);
    });

    // Crossing the breakpoint. Going desktop -> mobile the drawer defaults
    // CLOSED: nobody asked for it, and the drawer's slide transition made the
    // flip paint a half-open panel over the page. Going mobile -> desktop we
    // just drop the mobile-only classes; the collapse preference lives in
    // html[data-sidebar] and is untouched by any of this.
    const mobileQuery = window.matchMedia ? window.matchMedia('(max-width: 768px)') : null;
    if (mobileQuery) {
        const onBreakpoint = () => {
            // Kill the slide for one frame, so the layout change itself never
            // animates across the viewport.
            sidebar.classList.add('sidebar-no-transition');
            closeMobileNav();
            requestAnimationFrame(() => {
                requestAnimationFrame(() => sidebar.classList.remove('sidebar-no-transition'));
            });
        };
        if (mobileQuery.addEventListener) mobileQuery.addEventListener('change', onBreakpoint);
        else if (mobileQuery.addListener) mobileQuery.addListener(onBreakpoint);
    }

    // Backstop for the overlay click above: the overlay is one element at a
    // fixed z-index, so anything that paints over it swallows the tap and the
    // drawer stays open. Closing on any click that lands outside the drawer
    // doesn't care what's on top. The hamburger is excluded because its own
    // handler already toggles — without this guard the two would fight and
    // re-close the drawer the instant it opened.
    document.addEventListener('click', (event) => {
        if (!sidebar.classList.contains('mobile-open')) return;
        if (sidebar.contains(event.target)) return;
        if (hamburgerBtn.contains(event.target)) return;
        closeMobileNav();
    });

    // A drag inside the drawer must never count as a tap on the link under the
    // finger. Reported as "responds to scrolling as a tap first, making it
    // change pages on each scroll" — every attempt to scroll the nav list
    // navigated instead. Browsers normally cancel the synthetic click once a
    // touch moves far enough, but they don't when the gesture scrolled nothing,
    // which is exactly the case in a drawer whose list is short or already at
    // an edge. Track the movement ourselves and swallow the click.
    //
    // Capture phase so this runs BEFORE the .nav-button handlers below and the
    // anchors' own default navigation.
    let touchStart = null;
    let touchDragged = false;
    const TAP_SLOP_PX = 8;   // a tap wobbles a few px; a drag does not

    sidebar.addEventListener('touchstart', (event) => {
        touchStart = event.touches.length === 1
            ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
            : null;
        touchDragged = false;
    }, { passive: true });

    sidebar.addEventListener('touchmove', (event) => {
        if (!touchStart) return;
        // Distance, not just vertical travel. A drag across the drawer moves
        // mostly sideways and a Y-only check waves it straight through — the
        // click then lands on whatever was under the FINGER AT TOUCHSTART,
        // which is how pressing one entry and dragging away still opened it.
        const dx = event.touches[0].clientX - touchStart.x;
        const dy = event.touches[0].clientY - touchStart.y;
        if (Math.hypot(dx, dy) > TAP_SLOP_PX) {
            touchDragged = true;
        }
    }, { passive: true });

    sidebar.addEventListener('click', (event) => {
        if (!touchDragged) return;
        touchDragged = false;          // one click per gesture; never latch
        event.preventDefault();
        event.stopPropagation();
    }, true);

    // Close sidebar on nav button click (mobile only)
    document.querySelectorAll('.nav-button').forEach(btn => {
        btn.addEventListener('click', () => {
            if (window.innerWidth <= 768) {
                closeMobileNav();
            }
        });
    });

    restoreNavSections();
    initSidebarCollapse();
}

// --- Collapsible sidebar sections (persisted per section in localStorage) ---
function _navSectionItems(label) {
    const items = [];
    let el = label.nextElementSibling;
    while (el && !el.classList.contains('nav-section-label')) {
        if (el.classList.contains('nav-button')) items.push(el);
        el = el.nextElementSibling;
    }
    return items;
}
function _setNavSectionCollapsed(label, collapsed) {
    label.classList.toggle('collapsed', collapsed);
    _navSectionItems(label).forEach(it => it.classList.toggle('nav-item-hidden', collapsed));
}
function toggleNavSection(label) {
    const collapsed = !label.classList.contains('collapsed');
    _setNavSectionCollapsed(label, collapsed);
    try {
        const saved = JSON.parse(localStorage.getItem('navSections') || '{}');
        saved[label.dataset.section] = collapsed;
        localStorage.setItem('navSections', JSON.stringify(saved));
    } catch (e) { /* localStorage unavailable — collapse still works for the session */ }
}
function restoreNavSections() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem('navSections') || '{}'); } catch (e) { saved = {}; }
    const path = (window.SoulSyncURL?.strip(window.location.pathname) ?? window.location.pathname);
    document.querySelectorAll('.nav-section-label').forEach(label => {
        // Expanded by default; collapsed only when the user explicitly collapsed it.
        let collapsed = saved[label.dataset.section] === true;
        // Never collapse the section holding the current page — the active item must stay visible.
        if (_navSectionItems(label).some(it => it.getAttribute('href') === path)) collapsed = false;
        _setNavSectionCollapsed(label, collapsed);
    });
}

// --- Collapsible sidebar (#1155, wishx) ---
// Two states: full width, or icons only. The width itself is CSS
// (--sidebar-w keyed off html[data-sidebar="collapsed"]); this only decides
// which state is on and remembers it. The attribute is applied before paint by
// the inline script in index.html's <head> — this runs later and just keeps the
// button label and the icon tooltips in sync, so the two must agree on the key.
const SIDEBAR_COLLAPSE_KEY = 'sidebarCollapsed';

function isSidebarCollapsed() {
    return document.documentElement.getAttribute('data-sidebar') === 'collapsed';
}

function setSidebarCollapsed(collapsed) {
    const sidebar = document.querySelector('.sidebar');
    const canAnimate = sidebar && window.matchMedia
        && window.matchMedia('(min-width: 769px)').matches
        && !document.body.classList.contains('reduce-effects')
        && !document.body.classList.contains('max-performance');
    const fromWidth = canAnimate ? sidebar.getBoundingClientRect().width : 0;
    const targetWidth = collapsed ? 68 : 240;

    if (canAnimate) {
        sidebar.style.transition = 'none';
        sidebar.style.width = `${fromWidth}px`;
        sidebar.style.minWidth = `${fromWidth}px`;
        sidebar.style.maxWidth = `${fromWidth}px`;
        sidebar.style.flexBasis = `${fromWidth}px`;
        sidebar.offsetWidth; // commit the starting width before the state flips
    }

    // Expanded carries no attribute so the plain :root width applies — one less
    // state for the CSS to special-case.
    if (collapsed) document.documentElement.setAttribute('data-sidebar', 'collapsed');
    else document.documentElement.removeAttribute('data-sidebar');

    if (canAnimate) {
        const transition = 'width 420ms cubic-bezier(0.22, 1, 0.36, 1), min-width 420ms cubic-bezier(0.22, 1, 0.36, 1), max-width 420ms cubic-bezier(0.22, 1, 0.36, 1), flex-basis 420ms cubic-bezier(0.22, 1, 0.36, 1)';
        requestAnimationFrame(() => {
            sidebar.style.transition = transition;
            sidebar.style.width = `${targetWidth}px`;
            sidebar.style.minWidth = `${targetWidth}px`;
            sidebar.style.maxWidth = `${targetWidth}px`;
            sidebar.style.flexBasis = `${targetWidth}px`;
        });
        const clearInlineAnimation = (event) => {
            if (event.target !== sidebar || event.propertyName !== 'width') return;
            sidebar.removeEventListener('transitionend', clearInlineAnimation);
            sidebar.style.transition = '';
            sidebar.style.width = '';
            sidebar.style.minWidth = '';
            sidebar.style.maxWidth = '';
            sidebar.style.flexBasis = '';
        };
        sidebar.addEventListener('transitionend', clearInlineAnimation);
    }

    try {
        if (collapsed) localStorage.setItem(SIDEBAR_COLLAPSE_KEY, '1');
        else localStorage.removeItem(SIDEBAR_COLLAPSE_KEY);
    } catch (e) { /* localStorage blocked — works for the session */ }
    const btn = document.getElementById('sidebar-collapse-toggle');
    if (btn) {
        const label = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
        btn.title = label;
        btn.setAttribute('aria-label', label);
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }
    syncSidebarNavTitles(collapsed);
    return collapsed;
}

function toggleSidebarCollapsed() {
    return setSidebarCollapsed(!isSidebarCollapsed());
}

// Collapsed hides .nav-text, so the icon is all that's left to go on. Native
// title rather than a styled tooltip: .nav-button is overflow:hidden and the
// sidebar's stacking context is already delicate (see the visualiser's z-index
// note in style.css), so a ::after bubble would be clipped or fight for layer.
function syncSidebarNavTitles(collapsed) {
    document.querySelectorAll('.sidebar-nav .nav-button').forEach(btn => {
        if (!collapsed) { btn.removeAttribute('title'); return; }
        const label = (btn.querySelector('.nav-text')?.textContent || '').trim();
        if (label) btn.title = label;
    });
}

function initSidebarCollapse() {
    // Re-apply what the <head> script already read, so the button label and the
    // icon tooltips match the state on first load too.
    setSidebarCollapsed(isSidebarCollapsed());
}

/**
 * The wishlist hero button's behaviour, extracted from initializeWatchlist's
 * click closure to a NAMED top-level function so the React dashboard header
 * can call it too (window.openWishlistFromHero). It stays in init.js because
 * it reads activeDownloadProcesses / WishlistModalState / rehydrateModal —
 * all script-scoped, unreachable from a module. The body is the closure's,
 * verbatim.
 */
async function openWishlistFromHero() {
    // Fast path: check if we already know about an active wishlist process
    const clientProcess = activeDownloadProcesses['wishlist'];
    if (clientProcess && clientProcess.modalElement && document.body.contains(clientProcess.modalElement)) {
        clientProcess.modalElement.style.display = 'flex';
        WishlistModalState.setVisible();
        return;
    }
    // Slow path: ask the server (with timeout to prevent button feeling dead)
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 2000);
        const resp = await fetch('/api/active-processes', { signal: controller.signal });
        clearTimeout(timeout);
        if (resp.ok) {
            const data = await resp.json();
            const serverProcess = (data.active_processes || []).find(p => p.playlist_id === 'wishlist');
            if (serverProcess) {
                try {
                    WishlistModalState.clearUserClosed();
                    await rehydrateModal(serverProcess, true);
                } catch (e) {
                    console.debug('Rehydration failed, navigating to page:', e);
                    navigateToPage('wishlist');
                }
                return;
            }
        }
    } catch (e) {
        // Timeout or network error — just navigate
    }
    navigateToPage('wishlist');
}

function initializeWatchlist() {
    // Watchlist button navigates to watchlist page
    const watchlistButton = document.getElementById('watchlist-button');
    if (watchlistButton) {
        watchlistButton.addEventListener('click', () => navigateToPage('watchlist'));
    }

    // Wishlist button: quick check for active download, otherwise navigate to page
    const wishlistButton = document.getElementById('wishlist-button');
    if (wishlistButton) {
        wishlistButton.addEventListener('click', openWishlistFromHero);
    }

    // Update watchlist count initially
    updateWatchlistButtonCount();

    // Update count every 10 seconds
    setInterval(updateWatchlistButtonCount, 10000);

    // The wishlist SIDEBAR badge's poll. This used to start from
    // loadDashboardData on every dashboard visit (and leak — the interval was
    // never cleared, so in steady state it ran app-wide anyway). The dashboard
    // is React now and loadDashboardData is gone, so the poll lives here with
    // its watchlist twin; updateWishlistCount itself skips ticks while the
    // socket pushes.
    setInterval(updateWishlistCount, 10000);

    console.log('Watchlist system initialized');
}

function navigateToPage(pageId, options = {}) {
    navigationEpoch += 1;

    if (!options.forceReload && pageId === currentPage) return;

    // Permission guard — redirect to home page if not allowed
    if (!isPageAllowed(pageId)) {
        const home = getProfileHomePage();
        if (home !== currentPage && isPageAllowed(home)) {
            navigateToPage(home, options);
        }
        return;
    }

    if (pageId === 'artist-detail' && !options.artistId) {
        return false;
    }
    if (pageId === 'label-detail' && !options.labelId) {
        return false;
    }

    const router = getWebRouter();
    if (router && !options.skipRouteChange) {
        notifyPageWillChange(pageId);
        const route = router.routeManifest?.find((entry) => entry.pageId === pageId);
        if (route?.kind === 'react') {
            showReactHost(pageId);
            setActivePageChrome(pageId);
        } else if (route?.kind === 'legacy' && pageId !== 'artist-detail' && pageId !== 'label-detail') {
            // Show legacy page immediately — don't wait for TanStack Router's async cycle
            showLegacyPage(pageId);
            setActivePageChrome(pageId);
            _optimisticNavPageId = pageId;
            // Defer data loading until the browser is idle, so the page paints AND
            // becomes scrollable before heavy sync init (settings form wiring, etc.)
            // runs. Falls back to a macrotask where requestIdleCallback is missing.
            _scheduleHeavyInit(() => loadPageData(pageId));
        }
        return router.navigateToPage(pageId, {
            replace: options.replace === true,
            artistId: options.artistId,
            artistSource: options.artistSource,
            artistName: options.artistName,
            labelId: options.labelId,
            labelName: options.labelName,
        });
    }

    // Fallback path for initial bootstrap or environments without TanStack routing.
    const route = router?.routeManifest?.find((entry) => entry.pageId === pageId);
    notifyPageWillChange(pageId);
    const legacyPageElement = document.getElementById(`${pageId}-page`);
    if (route?.kind === 'react' || !legacyPageElement) {
        showReactHost(pageId);
        setActivePageChrome(pageId);
    } else {
        activatePage(pageId, { forceReload: options.forceReload === true });
    }

    if (!options.skipPushState) {
        const urlPath = pageId === 'dashboard' ? '/'
            : (pageId === 'artist-detail' && options.artistId) ? buildArtistDetailPath(options.artistId, options.artistSource, options.artistName)
            : (pageId === 'label-detail' && options.labelId) ? buildLabelDetailPath(options.labelId, options.labelName)
            : '/' + pageId;
        if ((window.SoulSyncURL?.strip(window.location.pathname) ?? window.location.pathname) !== urlPath) {
            if (options.replace === true) {
                history.replaceState({ page: pageId }, '', urlPath);
            } else {
                history.pushState({ page: pageId }, '', urlPath);
            }
        }
    }

    return true;
}

async function loadPageData(pageId) {
    try {
        // Stop any active polling when navigating away
        stopDbStatsPolling();
        stopDbUpdatePolling();
        stopWishlistCountPolling();
        stopLogPolling();
        // Stop watchlist/wishlist page timers when navigating away
        if (wishlistCountdownInterval) { clearInterval(wishlistCountdownInterval); wishlistCountdownInterval = null; }
        if (pageId !== 'sync') {
            cleanupBeatportContent();
        }
        switch (pageId) {
            // No 'dashboard' case: React owns /dashboard — the whole bento
            // grid — and loadPageData only runs for legacy-kind pages.
            // loadDashboardData (and its three leaked intervals) is deleted;
            // every card hydrates itself on mount.
            // No 'sync' case: React owns /sync, and loadPageData only runs for
            // legacy-kind pages, so this could never fire again.
            // No 'search' case: React owns /search — BOTH panels, enhanced and
            // basic — and loadPageData only runs for legacy-kind pages, so this
            // could never fire again. search.js, which used to bind the basic
            // panel, is deleted.
            // No 'label-detail' case: React owns /label-detail, and loadPageData
            // only runs for legacy-kind pages. The vanilla renderer it used to
            // call (label-detail.js) is deleted.
            // No 'active-downloads' case: React owns /active-downloads, and
            // loadPageData only runs for legacy-kind pages. The vanilla page
            // it used to call lives in pages-extra.js and is deleted.
            // No 'library' case: React owns /library, and loadPageData only runs
            // for legacy-kind pages. resolvePageId() returns null for a React
            // path and #library-page no longer exists, so neither route into
            // here can reach it — and initializeLibraryPage is deleted.
            case 'artist-detail':
                // Artist detail page is entered through the route handoff and legacy navigator.
                break;
            case 'discover':
                if (!discoverPageInitialized) {
                    if (typeof loadDiscoverPage === 'function') loadDiscoverPage();
                    discoverPageInitialized = true;
                }
                // Already initialized — DOM content persists, no reload needed
                break;
            // No 'playlist-explorer' case: React owns /playlist-explorer, and
            // loadPageData only runs for legacy-kind pages. The vanilla page it
            // used to call lived in pages-extra.js and is deleted, along with
            // #playlist-explorer-page.
            case 'settings':
                // Suppress auto-save while the form is being populated, so opening
                // Settings no longer fires a spurious full save (4 POSTs + backend
                // service re-init) on every visit.
                window._suppressSettingsAutoSave = true;
                try {
                    initializeSettings();
                    switchSettingsTab('connections');
                    await loadSettingsData();
                    await loadQualityProfile();
                    loadApiKeys();
                    loadBlacklistCount();
                } finally {
                    window._suppressSettingsAutoSave = false;
                }
                break;
            case 'hydrabase':
                // Check connection status and pre-fill saved credentials
                try {
                    const hsResp = await fetch('/api/hydrabase/status');
                    const hsData = await hsResp.json();
                    _hydrabaseConnected = hsData.connected;
                    document.getElementById('hydra-connection-status').textContent = hsData.connected ? 'Connected' : 'Disconnected';
                    document.getElementById('hydra-connection-status').style.color = hsData.connected ? 'rgb(var(--accent-light-rgb))' : '#888';
                    document.getElementById('hydra-connect-btn').textContent = hsData.connected ? 'Disconnect' : 'Connect';
                    // Pre-fill saved credentials
                    if (hsData.saved_url) {
                        document.getElementById('hydra-ws-url').value = hsData.saved_url;
                    }
                    if (hsData.saved_api_key) {
                        document.getElementById('hydra-api-key').value = hsData.saved_api_key;
                    }
                    // Update peer count
                    if (hsData.peer_count !== null && hsData.peer_count !== undefined) {
                        document.getElementById('hydra-peer-count').textContent = `Peers: ${hsData.peer_count}`;
                    }
                } catch (e) { }
                // Load comparisons
                loadHydrabaseComparisons();
                break;
            // 'tools' is a React route now (P7). initializeToolsPage() wired the
            // vanilla cards AND called switchRepairTab('jobs') + a 10s
            // fetchAndUpdateDbStats interval — all of which write into ids and
            // classes the React page renders, so leaving this case in would have
            // it stomping React's own DOM on every visit.
            // 'wishlist' is a React route now — navigateToPage shows the React
            // host and never calls loadPageData for it.
            case 'automations':
                await loadAutomations();
                break;
            case 'chat':
                if (window.ChatPage) window.ChatPage.open();
                break;
            case 'help':
                initializeDocsPage();
                break;
        }
    } catch (error) {
        console.error(`Error loading ${pageId} data:`, error);
        showToast(`Failed to load ${pageId} data`, 'error');
    }
}

// ---- Dashboard cursor-following accent blob (two-layer liquid) ----
// Both layers lerp toward a target point: the cursor when it's hovering
// any .dash-card, otherwise the grid center (idle resting position).
// Core layer (--blob-x/y) follows faster, halo (--blob-x-soft/y-soft)
// trails. Each card renders both layers and clips them to its own bounds
// via overflow:hidden, so the blob spans the bento while gaps stay dark.
// Disabled entirely when body.reduce-effects is set.
(function initDashboardCursorBlob() {
    let grid = null;
    let cards = [];
    let cardRects = [];               // cached rects, refreshed each frame
    let targetX = 0, targetY = 0;
    let coreX = 0, coreY = 0;
    let softX = 0, softY = 0;
    let rafId = 0;
    let attached = false;
    let centeredOnce = false;

    const RECENTER_DELAY_MS = 1500;
    let recenterTimer = 0;

    const isReduced = () => document.body.classList.contains('reduce-effects')
        || document.body.classList.contains('max-performance');

    const gridCenter = () => {
        const r = grid.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    };

    // Two-pass per frame: read all rects first (one layout flush), then
    // write all CSS vars (no further reads). Avoids per-card layout thrash.
    const tick = () => {
        if (isReduced()) { rafId = 0; return; }

        coreX += (targetX - coreX) * 0.040;
        coreY += (targetY - coreY) * 0.040;
        softX += (targetX - softX) * 0.022;
        softY += (targetY - softY) * 0.022;

        const n = cards.length;
        if (cardRects.length !== n) cardRects.length = n;
        for (let i = 0; i < n; i++) cardRects[i] = cards[i].getBoundingClientRect();
        for (let i = 0; i < n; i++) {
            const r = cardRects[i];
            const s = cards[i].style;
            s.setProperty('--blob-x',      (coreX - r.left) + 'px');
            s.setProperty('--blob-y',      (coreY - r.top)  + 'px');
            s.setProperty('--blob-x-soft', (softX - r.left) + 'px');
            s.setProperty('--blob-y-soft', (softY - r.top)  + 'px');
        }

        const dx = Math.abs(targetX - softX) + Math.abs(targetX - coreX);
        const dy = Math.abs(targetY - softY) + Math.abs(targetY - coreY);
        if (dx + dy > 0.4) rafId = requestAnimationFrame(tick);
        else rafId = 0;
    };

    const ensureLoop = () => {
        if (!rafId && !isReduced()) rafId = requestAnimationFrame(tick);
    };

    const cancelRecenter = () => {
        if (recenterTimer) { clearTimeout(recenterTimer); recenterTimer = 0; }
    };
    const recenterNow = () => {
        recenterTimer = 0;
        if (!grid) return;
        const c = gridCenter();
        targetX = c.x; targetY = c.y;
        ensureLoop();
    };
    const scheduleRecenter = () => {
        if (recenterTimer) return;
        recenterTimer = setTimeout(recenterNow, RECENTER_DELAY_MS);
    };

    // Snap the blob to grid center the first time the grid becomes
    // measurable (page may not be visible at DOMContentLoaded).
    const snapToCenterIfReady = () => {
        if (!grid || centeredOnce) return;
        const r = grid.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;  // not visible yet
        const c = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        targetX = coreX = softX = c.x;
        targetY = coreY = softY = c.y;
        centeredOnce = true;
        ensureLoop();
    };

    function attach() {
        if (attached) return;
        grid = document.querySelector('.dash-grid');
        if (!grid) return;
        attached = true;
        cards = Array.from(grid.querySelectorAll('.dash-card'));

        snapToCenterIfReady();

        grid.addEventListener('pointermove', (e) => {
            if (isReduced()) return;
            const onCard = e.target && e.target.closest && e.target.closest('.dash-card');
            if (onCard) {
                cancelRecenter();
                targetX = e.clientX;
                targetY = e.clientY;
                ensureLoop();
            } else {
                scheduleRecenter();
            }
        });
        grid.addEventListener('pointerleave', () => {
            if (!isReduced()) scheduleRecenter();
        });
        window.addEventListener('resize', () => {
            if (isReduced()) return;
            // Idle: snap immediately. Active: respect the existing delay.
            if (!recenterTimer) recenterNow();
        });

        // Re-resolve cards when active-downloads card toggles visibility.
        const cardObserver = new MutationObserver(() => {
            cards = Array.from(grid.querySelectorAll('.dash-card'));
            ensureLoop();
        });
        cardObserver.observe(grid, { childList: true, subtree: false, attributes: true, attributeFilter: ['style', 'class'] });

        // If the grid was hidden at attach time, snap once it becomes
        // measurable (page navigation, tab switch).
        if (!centeredOnce && 'IntersectionObserver' in window) {
            const visObserver = new IntersectionObserver((entries) => {
                for (const ent of entries) {
                    if (ent.isIntersecting) { snapToCenterIfReady(); break; }
                }
            });
            visObserver.observe(grid);
        }

        // React to reduce-effects toggle on body class.
        const bodyObserver = new MutationObserver(() => {
            if (isReduced()) {
                cancelRecenter();
                if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
            } else {
                centeredOnce = false;
                snapToCenterIfReady();
            }
        });
        bodyObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attach);
    } else {
        attach();
    }
    // Also retry on full load — covers late-mounted markup.
    window.addEventListener('load', () => {
        attach();
        snapToCenterIfReady();
    });
})();


// ===========================================
// APP BOOT
// ===========================================

/**
 * Hydrate the persisted download bubbles, then navigate to the landing page.
 *
 * Moved here from search.js when basic search was ported to React and that
 * file was deleted. It never had anything to do with search — it is the boot
 * routine, and init.js is where it is called from.
 */
async function loadInitialData() {
    try {
        const initialPath = (window.SoulSyncURL?.strip(window.location.pathname) ?? window.location.pathname);
        const initialNavigationEpoch = navigationEpoch;

        // Snapshot hydration is best-effort chrome — bubbles and the discover
        // download bar. It must never decide whether the app navigates.
        //
        // `hydrateDiscoverDownloadsFromSnapshot` is published by the REACT
        // bundle at module load (see -discover.use-download-bar.ts), unlike the
        // two above it which live in shared-helpers.js. So when that bundle
        // fails to arrive — blocked, 404, offline dev server — the bare call
        // threw a ReferenceError that escaped to the catch below, skipping the
        // navigateToPage() further down. The user got the shell with no page
        // inside it at all. One absent feature must not cost the whole startup.
        try {
            await hydrateArtistBubblesFromSnapshot();
            await hydrateSearchBubblesFromSnapshot();
            // typeof on an undeclared identifier is safe; a bare call is not.
            if (typeof hydrateDiscoverDownloadsFromSnapshot === 'function') {
                await hydrateDiscoverDownloadsFromSnapshot();
            } else {
                console.warn('[init] discover download hydration unavailable — the React bundle did not load');
            }
        } catch (hydrationError) {
            console.warn('[init] snapshot hydration failed; navigating anyway', hydrationError);
        }

        // Navigate to user's home page (or dashboard for admin)
        const homePage = getProfileHomePage();
        const urlPage = _getPageFromPath();
        let targetPage = (urlPage && urlPage !== 'dashboard' && isPageAllowed(urlPage))
            ? urlPage
            : homePage;

        // A real navigation during startup means abandon it — whatever the user
        // asked for wins, and it has already activated its own page.
        if (navigationEpoch !== initialNavigationEpoch) {
            return;
        }

        // The pathname changing is NOT the same thing. React's root route
        // redirects "/" to the profile's home path in beforeLoad (see
        // routes/index.tsx), which rewrites location.pathname while this async
        // function is still mid-flight. Treating that as "the user navigated
        // away" and returning meant showReactHost() below never ran: the URL
        // read /dashboard while the React host was never activated, so the page
        // was blank until you navigated by hand. Desktop wins that race and
        // never sees it; a phone is slow enough to lose it. A redirect only
        // answers the question startup was already asking, so adopt it.
        if ((window.SoulSyncURL?.strip(window.location.pathname) ?? window.location.pathname) !== initialPath) {
            const redirectedPage = _getPageFromPath();
            if (redirectedPage && isPageAllowed(redirectedPage)) {
                targetPage = redirectedPage;
            }
        }

        // a video page as home: opening the bare address lands there. the
        // music boot below still runs underneath, like any side switch.
        const videoHome = profileVideoHomePage();
        if (videoHome && PF_BOOT_PATH === '/' && typeof window._switchAppSide === 'function') {
            const videoNav = document.querySelector(`.video-nav .nav-button[data-video-page="${videoHome}"]`);
            if (videoNav) {
                document.querySelectorAll('.video-nav .nav-button.active').forEach(b => b.classList.remove('active'));
                videoNav.classList.add('active');
                window._switchAppSide('video');
            }
        }

        if (targetPage === 'artist-detail') {
            const artistRoute = typeof parseArtistDetailPath === 'function' ? parseArtistDetailPath() : null;
            if (artistRoute && typeof navigateToArtistDetail === 'function') {
                navigateToArtistDetail(artistRoute.artistId, artistRoute.name || '', artistRoute.source);
            }
            return;
        }

        // Always apply the target page to the legacy shell chrome.
        const router = getWebRouter();
        const route = router?.routeManifest?.find((entry) => entry.pageId === targetPage);

        if (route?.kind === 'react') {
            showReactHost(targetPage);
            setActivePageChrome(targetPage);
            // Keep nested react-tab URLs like /import/auto or /import/singles intact.
            return;
        }

        navigateToPage(targetPage, { forceReload: true });
    } catch (error) {
        console.error('Error loading initial data:', error);
    }
}
