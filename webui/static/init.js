// INITIALIZATION
// ===============================

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
            const activePage = document.querySelector('.page.active');
            if (activePage) {
                window.pageParticles.setPage(activePage.id.replace('-page', ''));
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
            const activePage = document.querySelector('.page.active');
            if (activePage && activePage.id === 'dashboard-page') {
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
}

function applyReduceEffects(enabled) {
    if (enabled) {
        document.body.classList.add('reduce-effects');
    } else {
        document.body.classList.remove('reduce-effects');
    }
    localStorage.setItem('soulsync-reduce-effects', enabled ? '1' : '0');
}

// Bootstrap accent and reduce-effects from localStorage instantly (prevents flash)
(function () {
    if (localStorage.getItem('soulsync-reduce-effects') === '1') {
        document.body.classList.add('reduce-effects');
    }
    const saved = localStorage.getItem('soulsync-accent');
    if (saved) applyAccentColor(saved);
    // Bootstrap particles setting from localStorage
    const particlesSaved = localStorage.getItem('soulsync-particles');
    if (particlesSaved === 'false') {
        window._particlesEnabled = false;
        const canvas = document.getElementById('page-particles-canvas');
        if (canvas) canvas.style.display = 'none';
    }
    // Bootstrap worker orbs setting from localStorage
    const workerOrbsSaved = localStorage.getItem('soulsync-worker-orbs');
    if (workerOrbsSaved === 'false') {
        window._workerOrbsEnabled = false;
    }
})();

// ── Profile System ─────────────────────────────────────────────
let currentProfile = null;

// Normalize legacy allowed_pages entries so the profile edit forms (which are
// built from the new pageLabels map containing 'search') don't drop access
// when saving a profile that was stored before the Search page rename.
// 'downloads' was the old Search id, 'artists' was the retired inline page.
function _normalizeLegacyAllowedPages(pages) {
    if (!Array.isArray(pages)) return pages;
    const mapped = pages.map(id => (id === 'downloads' || id === 'artists') ? 'search' : id);
    return [...new Set(mapped)];
}

function _normalizeLegacyHomePage(homePage) {
    if (homePage === 'downloads' || homePage === 'artists') return 'search';
    return homePage;
}

function getProfileHomePage() {
    if (!currentProfile) return 'dashboard';
    // Legacy profiles stored the Search page as either 'downloads' (the old id
    // before the rename) or 'artists' (the retired inline Artists page).
    // Both fold into the unified Search page now.
    let home = currentProfile.home_page;
    if (home === 'downloads' || home === 'artists') home = 'search';
    if (home) return home;
    return currentProfile.is_admin ? 'dashboard' : 'discover';
}

function isPageAllowed(pageId) {
    if (!currentProfile) return true;
    if (currentProfile.id === 1) return true;
    if (pageId === 'help' || pageId === 'issues') return true;
    if (pageId === 'settings') return currentProfile.is_admin;
    if (pageId === 'artist-detail') {
        // artist-detail is reachable from both Library and Search results, so
        // either grant unlocks it. Without this, a Search-only profile couldn't
        // open a source artist, and a legacy artists-only profile would hit the
        // home-redirect recursion path below.
        const ap = currentProfile.allowed_pages;
        if (!ap) return true;
        return ap.includes('library') || ap.includes('search')
            || ap.includes('downloads') || ap.includes('artists');
    }
    const ap = currentProfile.allowed_pages;
    if (!ap) return true; // null = all pages
    if (ap.includes(pageId)) return true;
    // Legacy compat: 'downloads' (old Search id) and 'artists' (retired inline
    // Artists page) both fold into the unified 'search' page.
    if (pageId === 'search' && (ap.includes('downloads') || ap.includes('artists'))) return true;
    if ((pageId === 'downloads' || pageId === 'artists') && ap.includes('search')) return true;
    return false;
}

function canDownload() {
    if (!currentProfile) return true;
    if (currentProfile.id === 1) return true;
    return currentProfile.can_download !== false && currentProfile.can_download !== 0;
}

function renderProfileAvatar(el, profile) {
    // Renders avatar as image (if avatar_url set) or colored initial fallback
    // Preserves existing classes, ensures 'profile-avatar' is present
    if (!el.classList.contains('profile-avatar') && !el.classList.contains('profile-indicator-avatar') && !el.classList.contains('profile-pin-avatar')) {
        el.className = 'profile-avatar';
    }
    el.style.background = profile.avatar_color || '#6366f1';
    el.textContent = '';
    if (profile.avatar_url) {
        const img = document.createElement('img');
        img.src = profile.avatar_url;
        img.alt = profile.name;
        img.className = 'profile-avatar-img';
        img.onerror = () => {
            img.remove();
            el.textContent = profile.name.charAt(0).toUpperCase();
        };
        el.appendChild(img);
    } else {
        el.textContent = profile.name.charAt(0).toUpperCase();
    }
}

async function initProfileSystem() {
    try {
        // Check if a session already has a profile selected
        const currentRes = await fetch('/api/profiles/current');
        const currentData = await currentRes.json();
        if (currentData.success && currentData.profile) {
            currentProfile = currentData.profile;
            updateProfileIndicator();

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

// ── Launch PIN Lock Screen ─────────────────────────────────────────────

function showLaunchPinScreen() {
    const overlay = document.getElementById('launch-pin-overlay');
    if (!overlay) return;
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

async function saveSecurityPin() {
    const pin = document.getElementById('security-new-pin').value;
    const confirm = document.getElementById('security-confirm-pin').value;
    const msg = document.getElementById('security-pin-msg');

    if (!pin || pin.length < 4) {
        msg.textContent = 'PIN must be at least 4 characters';
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
            sessionStorage.setItem('soulsync_pin_ok', '1');
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

// ── Profile PIN Forgot Recovery ────────────────────────────────────────
function showProfileForgotPin() {
    const dialog = document.getElementById('profile-pin-dialog');
    const content = dialog.querySelector('.profile-pin-content');

    // Store the profile ID we're recovering for
    const profileName = document.getElementById('profile-pin-name').textContent;

    // Replace dialog content with recovery form
    content.dataset.prevHtml = content.innerHTML;
    content.innerHTML = `
        <p style="color:#fff;font-size:14px;font-weight:600;margin-bottom:4px">Reset PIN for ${profileName}</p>
        <p style="color:rgba(255,255,255,0.5);font-size:12px;margin-bottom:12px">Enter any configured API credential<br>(Spotify secret, Plex token, etc.)</p>
        <input type="password" id="profile-recovery-input" class="profile-pin-input" maxlength="200" placeholder="Paste API credential" autocomplete="off">
        <div class="profile-pin-buttons">
            <button id="profile-recovery-cancel" class="profile-pin-cancel">Back</button>
            <button id="profile-recovery-submit" class="profile-pin-submit">Verify & Reset</button>
        </div>
        <p id="profile-recovery-error" class="profile-pin-error" style="display:none"></p>
    `;
    setTimeout(() => document.getElementById('profile-recovery-input').focus(), 100);

    document.getElementById('profile-recovery-cancel').onclick = () => {
        content.innerHTML = content.dataset.prevHtml;
    };

    document.getElementById('profile-recovery-submit').onclick = async () => {
        const input = document.getElementById('profile-recovery-input');
        const error = document.getElementById('profile-recovery-error');
        const credential = input.value.trim();
        if (!credential) return;

        const btn = document.getElementById('profile-recovery-submit');
        btn.disabled = true;
        btn.textContent = 'Verifying...';
        error.style.display = 'none';

        try {
            const res = await fetch('/api/profiles/reset-pin-via-credential', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ credential, profile_id: dialog._profileId || 1 })
            });
            const data = await res.json();
            if (data.success) {
                dialog.style.display = 'none';
                content.innerHTML = content.dataset.prevHtml;
                showToast('PIN cleared. You can set a new one in Settings.', 'success');
                // Re-try selecting the profile (now PIN-free)
                if (dialog._profileId) selectProfile(dialog._profileId);
            } else {
                error.textContent = data.error || 'Credential not recognized';
                error.style.display = 'block';
                input.value = '';
                input.focus();
            }
        } catch (e) {
            error.textContent = 'Connection error';
            error.style.display = 'block';
        }
        btn.disabled = false;
        btn.textContent = 'Verify & Reset';
    };

    document.getElementById('profile-recovery-input').onkeydown = (e) => {
        if (e.key === 'Enter') document.getElementById('profile-recovery-submit').click();
    };
}

function showProfilePicker(profiles, canCancel = false) {
    const overlay = document.getElementById('profile-picker-overlay');
    const grid = document.getElementById('profile-picker-grid');
    const actions = document.getElementById('profile-picker-actions');

    grid.innerHTML = '';
    profiles.forEach(p => {
        const card = document.createElement('div');
        card.className = 'profile-picker-card';
        const avatarEl = document.createElement('div');
        renderProfileAvatar(avatarEl, p);
        card.appendChild(avatarEl);
        const nameEl = document.createElement('span');
        nameEl.className = 'profile-name';
        nameEl.textContent = p.name;
        card.appendChild(nameEl);
        if (p.is_admin) {
            const badge = document.createElement('span');
            badge.className = 'profile-badge';
            badge.textContent = 'Admin';
            card.appendChild(badge);
        }
        card.onclick = () => handleProfileClick(p);
        grid.appendChild(card);
    });

    // Show actions: admin sees "Manage Profiles", non-admin sees "My Profile" (when they have a profile selected)
    const isAdmin = currentProfile ? currentProfile.is_admin : false;
    const manageBtn = document.getElementById('manage-profiles-btn');
    if (isAdmin) {
        actions.style.display = '';
        if (manageBtn) {
            manageBtn.textContent = 'Manage Profiles';
            // Reset onclick to admin handler (initProfileManagement sets this, but re-affirm here)
            manageBtn.onclick = () => {
                document.getElementById('profile-manage-panel').style.display = 'flex';
                loadProfileManageList();
            };
        }
    } else if (currentProfile && canCancel) {
        // Non-admin with an active profile: show "My Profile" to edit own settings
        actions.style.display = '';
        if (manageBtn) {
            manageBtn.textContent = 'My Profile';
            manageBtn.onclick = () => showSelfEditForm();
        }
    } else {
        actions.style.display = 'none';
    }

    // Show/remove cancel button when opened from sidebar indicator
    let cancelBtn = overlay.querySelector('.profile-picker-cancel');
    if (cancelBtn) cancelBtn.remove();
    if (canCancel) {
        cancelBtn = document.createElement('button');
        cancelBtn.className = 'profile-picker-cancel';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.onclick = () => hideProfilePicker();
        actions.parentElement.appendChild(cancelBtn);
    }

    overlay.style.display = 'flex';
    document.querySelector('.main-container').style.display = 'none';
}

async function handleProfileClick(profile) {
    // Fetch profile count — PIN only matters with multiple profiles
    let profileCount = 1;
    try {
        const r = await fetch('/api/profiles');
        const d = await r.json();
        profileCount = (d.profiles || []).length;
    } catch (e) { }

    if (profile.has_pin && profileCount > 1) {
        showPinDialog(profile);
    } else {
        const wasSwitching = !!currentProfile;
        await selectProfile(profile.id);
        if (wasSwitching) {
            window.location.reload();
            return;
        }
        hideProfilePicker();
        initApp();
    }
}

function showPinDialog(profile) {
    const dialog = document.getElementById('profile-pin-dialog');
    const avatar = document.getElementById('profile-pin-avatar');
    const nameEl = document.getElementById('profile-pin-name');
    const input = document.getElementById('profile-pin-input');
    const errorEl = document.getElementById('profile-pin-error');

    renderProfileAvatar(avatar, profile);
    nameEl.textContent = profile.name;
    input.value = '';
    errorEl.style.display = 'none';
    dialog._profileId = profile.id;
    dialog.style.display = 'flex';
    setTimeout(() => input.focus(), 100);

    const submit = document.getElementById('profile-pin-submit');
    const cancel = document.getElementById('profile-pin-cancel');

    const wasSwitching = !!currentProfile;
    const handleSubmit = async () => {
        const pin = input.value;
        if (!pin) return;
        try {
            const res = await fetch('/api/profiles/select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ profile_id: profile.id, pin })
            });
            const data = await res.json();
            if (data.success) {
                cleanup();
                if (wasSwitching) {
                    window.location.reload();
                    return;
                }
                currentProfile = data.profile;
                dialog.style.display = 'none';
                hideProfilePicker();
                updateProfileIndicator();
                initApp();
                return;
            } else {
                errorEl.textContent = data.error || 'Invalid PIN';
                errorEl.style.display = '';
                input.value = '';
                input.focus();
            }
        } catch (e) {
            errorEl.textContent = 'Connection error';
            errorEl.style.display = '';
        }
        cleanup();
    };

    const handleCancel = () => {
        dialog.style.display = 'none';
        cleanup();
    };

    const handleKeydown = (e) => {
        if (e.key === 'Enter') handleSubmit();
        if (e.key === 'Escape') handleCancel();
    };

    const cleanup = () => {
        submit.removeEventListener('click', handleSubmit);
        cancel.removeEventListener('click', handleCancel);
        input.removeEventListener('keydown', handleKeydown);
    };

    submit.addEventListener('click', handleSubmit);
    cancel.addEventListener('click', handleCancel);
    input.addEventListener('keydown', handleKeydown);
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
            currentProfile = data.profile;
            updateProfileIndicator();
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

function hideProfilePicker() {
    document.getElementById('profile-picker-overlay').style.display = 'none';
    document.querySelector('.main-container').style.display = 'flex';
}

function updateProfileIndicator() {
    const indicator = document.getElementById('profile-indicator');
    if (!currentProfile || !indicator) return;

    const avatar = document.getElementById('profile-indicator-avatar');
    const name = document.getElementById('profile-indicator-name');

    renderProfileAvatar(avatar, currentProfile);
    name.textContent = currentProfile.name;
    indicator.style.display = 'flex';

    indicator.onclick = async () => {
        const res = await fetch('/api/profiles');
        const data = await res.json();
        if (data.profiles && data.profiles.length > 0) {
            showProfilePicker(data.profiles, true);
        }
    };

    // Filter sidebar pages based on profile permissions
    document.querySelectorAll('.nav-button[data-page]').forEach(btn => {
        const page = btn.getAttribute('data-page');
        if (page === 'hydrabase') return; // Managed by dev mode toggle
        if (page === 'settings') {
            // Settings always gated by is_admin
            btn.style.display = currentProfile.is_admin ? '' : 'none';
        } else if (page === 'help' || page === 'issues') {
            btn.style.display = ''; // Always visible
        } else if (currentProfile.id === 1) {
            btn.style.display = ''; // Root admin sees all
        } else {
            const ap = currentProfile.allowed_pages;
            btn.style.display = (!ap || ap.includes(page)) ? '' : 'none';
        }
    });

    // Toggle download capability
    if (canDownload()) {
        document.body.classList.remove('downloads-disabled');
    } else {
        document.body.classList.add('downloads-disabled');
    }
}

// =====================
// PERSONAL SETTINGS MODAL
// =====================

async function openPersonalSettings() {
    const overlay = document.getElementById('personal-settings-overlay');
    if (!overlay) return;
    overlay.style.display = 'flex';

    const body = document.getElementById('personal-settings-body');
    body.innerHTML = '<div style="text-align:center;padding:20px;color:rgba(255,255,255,0.4);">Loading...</div>';

    try {
        // Load all per-profile service data in parallel
        const [lbRes, spotifyRes] = await Promise.all([
            fetch('/api/profiles/me/listenbrainz'),
            fetch('/api/profiles/me/spotify'),
        ]);
        const lbData = await lbRes.json();
        const spotifyData = await spotifyRes.json();

        body.innerHTML = '';
        const isNonAdmin = currentProfile && !currentProfile.is_admin;

        if (isNonAdmin) {
            // Tabbed layout for non-admin with multiple sections
            const tabs = [
                { id: 'music', label: 'Music Services' },
                { id: 'server', label: 'Server' },
                { id: 'scrobble', label: 'Scrobbling' },
            ];
            const tabBar = document.createElement('div');
            tabBar.className = 'ps-tabbar';
            tabs.forEach((t, i) => {
                const btn = document.createElement('button');
                btn.className = 'ps-tab' + (i === 0 ? ' active' : '');
                btn.textContent = t.label;
                btn.onclick = () => {
                    tabBar.querySelectorAll('.ps-tab').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    body.querySelectorAll('.ps-tab-content').forEach(c => c.classList.remove('active'));
                    const target = document.getElementById(`ps-tab-${t.id}`);
                    if (target) target.classList.add('active');
                };
                tabBar.appendChild(btn);
            });
            body.appendChild(tabBar);

            // Music Services tab
            const musicTab = document.createElement('div');
            musicTab.id = 'ps-tab-music';
            musicTab.className = 'ps-tab-content active';
            renderPersonalSettingsSpotify(musicTab, spotifyData);
            renderPersonalSettingsTidal(musicTab);
            body.appendChild(musicTab);

            // Server tab
            const serverTab = document.createElement('div');
            serverTab.id = 'ps-tab-server';
            serverTab.className = 'ps-tab-content';
            serverTab.innerHTML = '<div style="text-align:center;padding:20px;color:rgba(255,255,255,0.3);">Loading libraries...</div>';
            body.appendChild(serverTab);
            // Load server libraries async (don't block modal)
            fetch('/api/profiles/me/server-library').then(r => r.json()).then(libData => {
                serverTab.innerHTML = '';
                renderPersonalSettingsServerLibrary(serverTab, libData);
            }).catch(() => {
                serverTab.innerHTML = '';
                renderPersonalSettingsServerLibrary(serverTab, {});
            });

            // Scrobbling tab
            const scrobbleTab = document.createElement('div');
            scrobbleTab.id = 'ps-tab-scrobble';
            scrobbleTab.className = 'ps-tab-content';
            body.appendChild(scrobbleTab);
            // Render LB into the scrobble tab
            const origBody = body;
            renderPersonalSettingsLB(lbData, scrobbleTab);
        } else {
            // Admin: just ListenBrainz, no tabs
            const content = document.createElement('div');
            content.style.padding = '18px 22px 22px';
            body.appendChild(content);
            renderPersonalSettingsLB(lbData, content);
        }
    } catch (e) {
        body.innerHTML = '<div style="color:#ef4444;padding:16px;">Failed to load settings</div>';
    }
}

function closePersonalSettings() {
    const overlay = document.getElementById('personal-settings-overlay');
    if (overlay) overlay.style.display = 'none';
}

function renderPersonalSettingsSpotify(body, data) {
    const hasCreds = data.has_credentials;
    const clientId = data.client_id || '';

    let contentHtml;
    if (hasCreds) {
        contentHtml = `
            <div class="ps-connected-info">
                <div class="ps-connected-icon">🟢</div>
                <div class="ps-connected-details">
                    <div class="ps-connected-username">Credentials configured</div>
                    <div class="ps-connected-server">Client ID: ${escapeHtml(clientId.substring(0, 8))}...</div>
                    <div class="ps-connected-source">Personal Spotify app</div>
                </div>
            </div>
            <div class="ps-actions">
                <button class="ps-btn ps-btn-primary" onclick="authenticatePersonalSpotify()">🔐 Authenticate</button>
                <button class="ps-btn ps-btn-danger" onclick="disconnectPersonalSpotify()">Remove</button>
            </div>
        `;
    } else {
        contentHtml = `
            <div class="ps-form-group">
                <label>Client ID</label>
                <input type="text" id="ps-spotify-client-id" placeholder="Your Spotify Client ID">
            </div>
            <div class="ps-form-group">
                <label>Client Secret</label>
                <input type="password" id="ps-spotify-client-secret" placeholder="Your Spotify Client Secret">
            </div>
            <div class="ps-form-group">
                <label>Redirect URI <span style="font-weight:400;color:rgba(255,255,255,0.3)">(optional)</span></label>
                <input type="text" id="ps-spotify-redirect-uri" placeholder="http://127.0.0.1:8888/callback">
                <div class="ps-help-text">
                    Create an app at <a href="https://developer.spotify.com/dashboard" target="_blank">developer.spotify.com</a> and add the redirect URI
                </div>
            </div>
            <div id="ps-spotify-result"></div>
            <div class="ps-actions">
                <button class="ps-btn ps-btn-primary" onclick="savePersonalSpotify()">Save Credentials</button>
            </div>
        `;
    }

    const section = document.createElement('div');
    section.id = 'ps-spotify-section';
    section.innerHTML = `
        <div class="ps-section">
            <div class="ps-section-header">
                <h4 class="ps-section-title">Spotify</h4>
                <span class="ps-connection-badge ${hasCreds ? 'connected' : 'disconnected'}">
                    <span class="ps-connection-dot"></span>
                    ${hasCreds ? 'Configured' : 'Not configured'}
                </span>
            </div>
            <div class="ps-help-text" style="margin-bottom:12px;">
                Connect your own Spotify account to see your playlists instead of the admin's.
            </div>
            ${contentHtml}
        </div>
    `;

    const existing = document.getElementById('ps-spotify-section');
    if (existing) existing.replaceWith(section);
    else body.appendChild(section);
}

async function savePersonalSpotify() {
    const clientId = document.getElementById('ps-spotify-client-id')?.value?.trim();
    const clientSecret = document.getElementById('ps-spotify-client-secret')?.value?.trim();
    const redirectUri = document.getElementById('ps-spotify-redirect-uri')?.value?.trim();
    const resultEl = document.getElementById('ps-spotify-result');

    if (!clientId || !clientSecret) {
        if (resultEl) resultEl.innerHTML = '<div style="color:#ef4444;font-size:12px;margin-top:8px;">Client ID and Secret are required</div>';
        return;
    }

    try {
        const res = await fetch('/api/profiles/me/spotify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ client_id: clientId, client_secret: clientSecret, redirect_uri: redirectUri })
        });
        const data = await res.json();
        if (data.success) {
            showToast('Spotify credentials saved', 'success');
            openPersonalSettings(); // Reload to show connected state
        } else {
            if (resultEl) resultEl.innerHTML = `<div style="color:#ef4444;font-size:12px;margin-top:8px;">${data.error || 'Failed to save'}</div>`;
        }
    } catch (e) {
        if (resultEl) resultEl.innerHTML = '<div style="color:#ef4444;font-size:12px;margin-top:8px;">Network error</div>';
    }
}

async function authenticatePersonalSpotify() {
    // Trigger OAuth flow with profile_id in state so callback knows which profile
    window.open('/auth/spotify?profile_id=' + (currentProfile?.id || ''), '_blank');
}

function renderPersonalSettingsTidal(body) {
    const section = document.createElement('div');
    section.id = 'ps-tidal-section';
    section.innerHTML = `
        <div class="ps-section">
            <div class="ps-section-header">
                <h4 class="ps-section-title">Tidal</h4>
            </div>
            <div class="ps-help-text" style="margin-bottom:12px;">
                Connect your own Tidal account to see your playlists. Uses the admin's Tidal app credentials.
            </div>
            <div class="ps-actions">
                <button class="ps-btn ps-btn-primary" onclick="authenticatePersonalTidal()">🔐 Authenticate Tidal</button>
            </div>
        </div>
    `;
    const existing = document.getElementById('ps-tidal-section');
    if (existing) existing.replaceWith(section);
    else body.appendChild(section);
}

function authenticatePersonalTidal() {
    window.open('/auth/tidal?profile_id=' + (currentProfile?.id || ''), '_blank');
}

async function renderPersonalSettingsServerLibrary(container, profileData) {
    const section = document.createElement('div');
    section.id = 'ps-server-library-section';

    // Detect which server is active
    let serverType = 'none';
    let libraries = [];
    let users = [];
    const currentLib = profileData || {};

    try {
        // Try each server type to find the active one
        const plexRes = await fetch('/api/plex/music-libraries');
        if (plexRes.ok) {
            const plexData = await plexRes.json();
            if (plexData.libraries && plexData.libraries.length > 0) {
                serverType = 'plex';
                libraries = plexData.libraries;
            }
        }
    } catch (e) { }

    if (serverType === 'none') {
        try {
            const jellyRes = await fetch('/api/jellyfin/music-libraries');
            if (jellyRes.ok) {
                const jellyData = await jellyRes.json();
                if (jellyData.libraries && jellyData.libraries.length > 0) {
                    serverType = 'jellyfin';
                    libraries = jellyData.libraries;
                    users = jellyData.users || [];
                }
            }
        } catch (e) { }
    }

    if (serverType === 'none') {
        section.innerHTML = `
            <div class="ps-section">
                <div class="ps-section-header">
                    <h4 class="ps-section-title">Media Server</h4>
                </div>
                <div class="ps-help-text">No media server connected. Ask your admin to configure Plex, Jellyfin, or Navidrome in Settings.</div>
            </div>
        `;
    } else if (serverType === 'plex') {
        const selectedLib = currentLib.plex_library_id || '';
        const optionsHtml = libraries.map(lib => {
            const name = lib.name || lib.title || lib;
            const val = typeof lib === 'string' ? lib : (lib.name || lib.title);
            return `<option value="${escapeHtml(val)}" ${val === selectedLib ? 'selected' : ''}>${escapeHtml(val)}</option>`;
        }).join('');

        section.innerHTML = `
            <div class="ps-section">
                <div class="ps-section-header">
                    <h4 class="ps-section-title">Plex Library</h4>
                    <span class="ps-connection-badge ${selectedLib ? 'connected' : 'disconnected'}">
                        <span class="ps-connection-dot"></span>
                        ${selectedLib ? 'Custom' : 'Default'}
                    </span>
                </div>
                <div class="ps-help-text" style="margin-bottom:12px;">Choose which Plex music library your playlists sync to.</div>
                <div class="ps-form-group">
                    <label>Music Library</label>
                    <select id="ps-plex-library-select">
                        <option value="">Use admin default</option>
                        ${optionsHtml}
                    </select>
                </div>
                <div class="ps-actions">
                    <button class="ps-btn ps-btn-primary" onclick="savePersonalServerLibrary()">Save</button>
                </div>
            </div>
        `;
    } else if (serverType === 'jellyfin') {
        const selectedUser = currentLib.jellyfin_user_id || '';
        const selectedLib = currentLib.jellyfin_library_id || '';

        const userOpts = users.map(u => {
            const uid = u.id || u.Id;
            const uname = u.name || u.Name;
            return `<option value="${escapeHtml(uid)}" ${uid === selectedUser ? 'selected' : ''}>${escapeHtml(uname)}</option>`;
        }).join('');

        const libOpts = libraries.map(lib => {
            const lid = lib.key || lib.id || lib.Id;
            const lname = lib.name || lib.Name || lib.title;
            return `<option value="${escapeHtml(lid)}" ${lid === selectedLib ? 'selected' : ''}>${escapeHtml(lname)}</option>`;
        }).join('');

        section.innerHTML = `
            <div class="ps-section">
                <div class="ps-section-header">
                    <h4 class="ps-section-title">Jellyfin</h4>
                    <span class="ps-connection-badge ${selectedUser || selectedLib ? 'connected' : 'disconnected'}">
                        <span class="ps-connection-dot"></span>
                        ${selectedUser || selectedLib ? 'Custom' : 'Default'}
                    </span>
                </div>
                <div class="ps-help-text" style="margin-bottom:12px;">Choose which Jellyfin user and library your playlists sync to.</div>
                ${users.length ? `<div class="ps-form-group"><label>User</label><select id="ps-jellyfin-user-select"><option value="">Use admin default</option>${userOpts}</select></div>` : ''}
                <div class="ps-form-group">
                    <label>Music Library</label>
                    <select id="ps-jellyfin-library-select">
                        <option value="">Use admin default</option>
                        ${libOpts}
                    </select>
                </div>
                <div class="ps-actions">
                    <button class="ps-btn ps-btn-primary" onclick="savePersonalServerLibrary()">Save</button>
                </div>
            </div>
        `;
    }

    const existing = document.getElementById('ps-server-library-section');
    if (existing) existing.replaceWith(section);
    else container.appendChild(section);
}

async function savePersonalServerLibrary() {
    try {
        const plexSelect = document.getElementById('ps-plex-library-select');
        const jellyUserSelect = document.getElementById('ps-jellyfin-user-select');
        const jellyLibSelect = document.getElementById('ps-jellyfin-library-select');

        if (plexSelect) {
            await fetch('/api/profiles/me/server-library', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ server_type: 'plex', library_id: plexSelect.value || null })
            });
        }
        if (jellyUserSelect || jellyLibSelect) {
            await fetch('/api/profiles/me/server-library', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    server_type: 'jellyfin',
                    user_id: jellyUserSelect?.value || null,
                    library_id: jellyLibSelect?.value || null
                })
            });
        }

        showToast('Server library settings saved', 'success');
    } catch (e) {
        showToast('Error saving settings', 'error');
    }
}

async function disconnectPersonalSpotify() {
    try {
        const res = await fetch('/api/profiles/me/spotify', { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast('Spotify credentials removed — using shared config', 'info');
            openPersonalSettings(); // Reload
        }
    } catch (e) {
        showToast('Error removing credentials', 'error');
    }
}

function renderPersonalSettingsLB(data, container) {
    const body = container || document.getElementById('personal-settings-body');
    const connected = data.connected;
    const username = data.username || '';
    const baseUrl = data.base_url || '';
    const source = data.source || 'global';

    const tokenFormHtml = `
        <div class="ps-form-group">
            <label>User Token</label>
            <input type="password" id="ps-lb-token" placeholder="Paste your ListenBrainz token">
        </div>
        <div class="ps-form-group">
            <label>Server URL <span style="font-weight:400;color:rgba(255,255,255,0.3)">(optional)</span></label>
            <input type="text" id="ps-lb-base-url" placeholder="Leave empty for official (api.listenbrainz.org)">
            <div class="ps-help-text">
                Get your token from <a href="https://listenbrainz.org/profile/" target="_blank">listenbrainz.org/profile</a>
            </div>
        </div>
        <div id="ps-lb-result"></div>
        <div class="ps-actions">
            <button class="ps-btn ps-btn-secondary" onclick="testPersonalListenBrainz()">Test</button>
            <button class="ps-btn ps-btn-primary" onclick="connectPersonalListenBrainz()">Connect</button>
        </div>
    `;

    let contentHtml;
    if (connected && source === 'profile') {
        // Personal token — show connected state with Disconnect
        const serverDisplay = baseUrl ? baseUrl.replace(/\/1$/, '').replace(/^https?:\/\//, '') : 'api.listenbrainz.org';
        contentHtml = `
            <div class="ps-connected-info">
                <div class="ps-connected-icon">&#129504;</div>
                <div class="ps-connected-details">
                    <div class="ps-connected-username">Connected as ${escapeHtml(username)}</div>
                    <div class="ps-connected-server">${escapeHtml(serverDisplay)}</div>
                    <div class="ps-connected-source">Personal token</div>
                </div>
            </div>
            <div class="ps-actions">
                <button class="ps-btn ps-btn-danger" onclick="disconnectPersonalListenBrainz()">Disconnect</button>
            </div>
        `;
    } else if (connected && source === 'global') {
        // Using admin's shared token — show status + option to set own token
        const serverDisplay = baseUrl ? baseUrl.replace(/\/1$/, '').replace(/^https?:\/\//, '') : 'api.listenbrainz.org';
        contentHtml = `
            <div class="ps-connected-info">
                <div class="ps-connected-icon">&#129504;</div>
                <div class="ps-connected-details">
                    <div class="ps-connected-username">Connected as ${escapeHtml(username)}</div>
                    <div class="ps-connected-server">${escapeHtml(serverDisplay)}</div>
                    <div class="ps-connected-source">Using shared token from Settings</div>
                </div>
            </div>
            <div style="margin-top:14px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.06);">
                <div style="font-size:11px;color:rgba(255,255,255,0.45);margin-bottom:10px;">Set your own token to use a different ListenBrainz account:</div>
                ${tokenFormHtml}
            </div>
        `;
    } else {
        // Not connected at all
        contentHtml = tokenFormHtml;
    }

    const section = document.createElement('div');
    section.id = 'ps-listenbrainz-section';
    section.innerHTML = `
        <div class="ps-section">
            <div class="ps-section-header">
                <h4 class="ps-section-title">ListenBrainz</h4>
                <span class="ps-connection-badge ${connected ? 'connected' : 'disconnected'}">
                    <span class="ps-connection-dot"></span>
                    ${connected ? 'Connected' : 'Not connected'}
                </span>
            </div>
            ${contentHtml}
        </div>
    `;
    // Replace existing or append
    const existing = document.getElementById('ps-listenbrainz-section');
    if (existing) existing.replaceWith(section);
    else body.appendChild(section);
}

async function testPersonalListenBrainz() {
    const token = document.getElementById('ps-lb-token')?.value?.trim();
    const baseUrl = document.getElementById('ps-lb-base-url')?.value?.trim() || '';
    const resultEl = document.getElementById('ps-lb-result');
    if (!token) {
        if (resultEl) resultEl.innerHTML = '<div class="ps-inline-result error">Please enter a token</div>';
        return;
    }
    if (resultEl) resultEl.innerHTML = '<div class="ps-inline-result" style="color:rgba(255,255,255,0.5);">Testing...</div>';
    try {
        const res = await fetch('/api/profiles/me/listenbrainz/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, base_url: baseUrl })
        });
        const data = await res.json();
        if (data.success) {
            resultEl.innerHTML = `<div class="ps-inline-result success">Valid token — ${escapeHtml(data.username)}</div>`;
        } else {
            resultEl.innerHTML = `<div class="ps-inline-result error">${escapeHtml(data.error || 'Invalid token')}</div>`;
        }
    } catch (e) {
        resultEl.innerHTML = '<div class="ps-inline-result error">Connection failed</div>';
    }
}

async function connectPersonalListenBrainz() {
    const token = document.getElementById('ps-lb-token')?.value?.trim();
    const baseUrl = document.getElementById('ps-lb-base-url')?.value?.trim() || '';
    const resultEl = document.getElementById('ps-lb-result');
    if (!token) {
        if (resultEl) resultEl.innerHTML = '<div class="ps-inline-result error">Please enter a token</div>';
        return;
    }
    // Disable buttons during connect
    document.querySelectorAll('.ps-actions .ps-btn').forEach(b => b.disabled = true);
    if (resultEl) resultEl.innerHTML = '<div class="ps-inline-result" style="color:rgba(255,255,255,0.5);">Connecting...</div>';
    try {
        const res = await fetch('/api/profiles/me/listenbrainz', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, base_url: baseUrl })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Connected to ListenBrainz as ${data.username}`, 'success');
            // Re-render as connected
            renderPersonalSettingsLB({ connected: true, username: data.username, base_url: baseUrl, source: 'profile' });
            // Refresh LB playlists on discover page
            _invalidateListenBrainzCache();
            if (typeof initializeListenBrainzTabs === 'function') {
                initializeListenBrainzTabs();
            }
        } else {
            resultEl.innerHTML = `<div class="ps-inline-result error">${escapeHtml(data.error || 'Connection failed')}</div>`;
            document.querySelectorAll('.ps-actions .ps-btn').forEach(b => b.disabled = false);
        }
    } catch (e) {
        resultEl.innerHTML = '<div class="ps-inline-result error">Connection failed</div>';
        document.querySelectorAll('.ps-actions .ps-btn').forEach(b => b.disabled = false);
    }
}

async function disconnectPersonalListenBrainz() {
    try {
        await fetch('/api/profiles/me/listenbrainz', { method: 'DELETE' });
        showToast('ListenBrainz disconnected', 'info');
        // Re-render as disconnected — re-fetch to check if global fallback exists
        const res = await fetch('/api/profiles/me/listenbrainz');
        const data = await res.json();
        renderPersonalSettingsLB(data);
        // Refresh LB playlists on discover page
        _invalidateListenBrainzCache();
        if (typeof initializeListenBrainzTabs === 'function') {
            initializeListenBrainzTabs();
        }
    } catch (e) {
        showToast('Failed to disconnect', 'error');
    }
}

function _invalidateListenBrainzCache() {
    if (typeof listenbrainzPlaylistsLoaded !== 'undefined') listenbrainzPlaylistsLoaded = false;
    if (typeof listenbrainzPlaylistsCache !== 'undefined') {
        try { Object.keys(listenbrainzPlaylistsCache).forEach(k => delete listenbrainzPlaylistsCache[k]); } catch (e) { }
    }
    if (typeof listenbrainzTracksCache !== 'undefined') {
        try { Object.keys(listenbrainzTracksCache).forEach(k => delete listenbrainzTracksCache[k]); } catch (e) { }
    }
}

function initProfileManagement() {
    const manageBtn = document.getElementById('manage-profiles-btn');
    const closeBtn = document.getElementById('profile-manage-close');
    const createBtn = document.getElementById('create-profile-btn');
    const adminPinBtn = document.getElementById('set-admin-pin-btn');

    if (manageBtn) {
        manageBtn.onclick = () => {
            document.getElementById('profile-manage-panel').style.display = 'flex';
            loadProfileManageList();
        };
    }

    if (closeBtn) {
        closeBtn.onclick = () => {
            document.getElementById('profile-manage-panel').style.display = 'none';
            // Refresh picker — keep cancel button if user already has a profile selected
            const hasCancel = !!currentProfile;
            fetch('/api/profiles').then(r => r.json()).then(d => {
                showProfilePicker(d.profiles || [], hasCancel);
            });
        };
    }

    // Color picker
    let selectedColor = '#6366f1';
    document.querySelectorAll('.profile-color-swatch').forEach(swatch => {
        swatch.onclick = () => {
            document.querySelectorAll('.profile-color-swatch').forEach(s => s.classList.remove('selected'));
            swatch.classList.add('selected');
            selectedColor = swatch.dataset.color;
        };
    });
    // Select first by default
    const firstSwatch = document.querySelector('.profile-color-swatch');
    if (firstSwatch) firstSwatch.classList.add('selected');

    if (createBtn) {
        createBtn.onclick = async () => {
            const name = document.getElementById('new-profile-name').value.trim();
            const avatarUrl = document.getElementById('new-profile-avatar-url').value.trim();
            const pin = document.getElementById('new-profile-pin').value;
            if (!name) return;

            // Collect profile settings
            const homePage = document.getElementById('new-profile-home-page').value || null;
            const pageCheckboxes = document.querySelectorAll('#new-profile-allowed-pages input[type="checkbox"]:not(:disabled)');
            const allChecked = Array.from(pageCheckboxes).every(cb => cb.checked);
            const allowedPages = allChecked ? null : Array.from(pageCheckboxes).filter(cb => cb.checked).map(cb => cb.value);
            const canDl = document.getElementById('new-profile-can-download').checked;

            const res = await fetch('/api/profiles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name, avatar_color: selectedColor,
                    avatar_url: avatarUrl || undefined,
                    pin: pin || undefined,
                    home_page: homePage,
                    allowed_pages: allowedPages,
                    can_download: canDl
                })
            });
            const data = await res.json();
            if (data.success) {
                document.getElementById('new-profile-name').value = '';
                document.getElementById('new-profile-avatar-url').value = '';
                document.getElementById('new-profile-pin').value = '';
                document.getElementById('new-profile-home-page').value = '';
                pageCheckboxes.forEach(cb => cb.checked = true);
                document.getElementById('new-profile-can-download').checked = true;
                loadProfileManageList();
                // Show admin PIN section if >1 profiles and admin has no PIN
                checkAdminPinRequired();
            } else {
                alert(data.error || 'Failed to create profile');
            }
        };
    }

    if (adminPinBtn) {
        adminPinBtn.onclick = async () => {
            const pin = document.getElementById('admin-pin-input').value;
            if (!pin || pin.length < 1) return;
            // Find admin profile
            const res = await fetch('/api/profiles');
            const data = await res.json();
            const admin = (data.profiles || []).find(p => p.is_admin);
            if (!admin) return;

            try {
                const pinRes = await fetch(`/api/profiles/${admin.id}/set-pin`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pin })
                });
                const pinData = await pinRes.json();
                if (!pinData.success) {
                    alert(pinData.error || 'Failed to set PIN');
                    return;
                }
            } catch (e) {
                alert('Connection error');
                return;
            }
            document.getElementById('admin-pin-input').value = '';
            document.getElementById('admin-pin-section').style.display = 'none';
            loadProfileManageList();
        };
    }
}

async function loadProfileManageList() {
    const list = document.getElementById('profile-manage-list');
    const res = await fetch('/api/profiles');
    const data = await res.json();
    const profiles = data.profiles || [];

    list.innerHTML = '';
    profiles.forEach(p => {
        const item = document.createElement('div');
        item.className = 'profile-manage-item';

        const av = document.createElement('div');
        renderProfileAvatar(av, p);
        item.appendChild(av);

        const info = document.createElement('div');
        info.className = 'profile-info';
        const nameDiv = document.createElement('div');
        nameDiv.className = 'name';
        nameDiv.textContent = p.name + (p.has_pin ? ' 🔒' : '');
        info.appendChild(nameDiv);
        const roleTags = [];
        if (p.is_admin) roleTags.push('Admin');
        if (p.can_download === false) roleTags.push('No Downloads');
        if (p.allowed_pages) roleTags.push(`${p.allowed_pages.length} pages`);
        if (roleTags.length) {
            const roleDiv = document.createElement('div');
            roleDiv.className = 'role';
            roleDiv.textContent = roleTags.join(' · ');
            info.appendChild(roleDiv);
        }
        item.appendChild(info);

        const actions = document.createElement('div');
        actions.className = 'profile-manage-actions';

        const editBtn = document.createElement('button');
        editBtn.className = 'profile-edit-btn';
        editBtn.dataset.id = p.id;
        editBtn.dataset.name = p.name;
        editBtn.dataset.color = p.avatar_color || '#6366f1';
        editBtn.dataset.avatarUrl = p.avatar_url || '';
        editBtn.dataset.homePage = p.home_page || '';
        editBtn.dataset.allowedPages = p.allowed_pages ? JSON.stringify(p.allowed_pages) : '';
        editBtn.dataset.canDownload = p.can_download !== false ? '1' : '0';
        editBtn.dataset.isAdmin = p.is_admin ? '1' : '0';
        editBtn.title = 'Edit profile';
        editBtn.textContent = '✏️';
        actions.appendChild(editBtn);

        if (!p.is_admin) {
            const delBtn = document.createElement('button');
            delBtn.className = 'profile-delete-btn';
            delBtn.dataset.id = p.id;
            delBtn.title = 'Delete profile';
            delBtn.textContent = '🗑️';
            actions.appendChild(delBtn);
        }

        item.appendChild(actions);
        list.appendChild(item);
    });

    // Bind edit buttons
    list.querySelectorAll('.profile-edit-btn').forEach(btn => {
        btn.onclick = () => {
            showProfileEditForm(btn.dataset.id, btn.dataset.name, btn.dataset.color, btn.dataset.avatarUrl, {
                home_page: btn.dataset.homePage || '',
                allowed_pages: btn.dataset.allowedPages ? JSON.parse(btn.dataset.allowedPages) : null,
                can_download: btn.dataset.canDownload !== '0',
                is_admin: btn.dataset.isAdmin === '1'
            });
        };
    });

    // Bind delete buttons
    list.querySelectorAll('.profile-delete-btn').forEach(btn => {
        btn.onclick = async () => {
            if (!await showConfirmDialog({ title: 'Delete Profile', message: 'Delete this profile and all its data?', confirmText: 'Delete', destructive: true })) return;
            try {
                const res = await fetch(`/api/profiles/${btn.dataset.id}`, { method: 'DELETE' });
                const data = await res.json();
                if (!data.success) {
                    alert(data.error || 'Failed to delete profile');
                }
            } catch (e) {
                alert('Connection error');
            }
            loadProfileManageList();
        };
    });

    checkAdminPinRequired();
}

function showProfileEditForm(profileId, currentName, currentColor, currentAvatarUrl, profileSettings = {}) {
    const list = document.getElementById('profile-manage-list');
    // Remove any existing edit form
    const existing = document.getElementById('profile-edit-form');
    if (existing) existing.remove();

    const isAdmin = currentProfile && currentProfile.is_admin;
    const isEditingAdmin = profileSettings.is_admin;
    const editColors = ['#6366f1', '#ec4899', '#10b981', '#f59e0b', '#3b82f6', '#ef4444', '#8b5cf6', '#14b8a6'];
    // 'search' replaces the legacy 'downloads'/'artists' ids; legacy values are
    // normalized on read below so saving a legacy profile upgrades it.
    const pageLabels = {
        dashboard: 'Dashboard', sync: 'Sync', search: 'Search', discover: 'Discover',
        automations: 'Automations', library: 'Library', stats: 'Listening Stats',
        'playlist-explorer': 'Playlist Explorer', import: 'Import', help: 'Help & Docs'
    };

    const form = document.createElement('div');
    form.id = 'profile-edit-form';
    form.className = 'profile-edit-form';

    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.className = 'profile-input';
    nameInput.value = currentName;
    nameInput.maxLength = 20;
    nameInput.placeholder = 'Profile name';
    form.appendChild(nameInput);

    const urlInput = document.createElement('input');
    urlInput.type = 'url';
    urlInput.className = 'profile-input';
    urlInput.value = currentAvatarUrl || '';
    urlInput.placeholder = 'Avatar image URL (optional)';
    form.appendChild(urlInput);

    const colorRow = document.createElement('div');
    colorRow.className = 'profile-color-picker';
    let editColor = currentColor;
    editColors.forEach(c => {
        const swatch = document.createElement('span');
        swatch.className = 'profile-color-swatch' + (c === currentColor ? ' selected' : '');
        swatch.style.background = c;
        swatch.dataset.color = c;
        swatch.onclick = () => {
            colorRow.querySelectorAll('.profile-color-swatch').forEach(s => s.classList.remove('selected'));
            swatch.classList.add('selected');
            editColor = c;
        };
        colorRow.appendChild(swatch);
    });
    form.appendChild(colorRow);

    // Home page selector — visible to everyone (self-edit or admin editing others)
    const homeLabel = document.createElement('label');
    homeLabel.className = 'profile-settings-label';
    homeLabel.textContent = 'Home Page';
    form.appendChild(homeLabel);

    const homeSelect = document.createElement('select');
    homeSelect.className = 'profile-input';
    const defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = isEditingAdmin ? 'Default (Dashboard)' : 'Default (Discover)';
    homeSelect.appendChild(defaultOpt);
    // Normalize legacy ids so the form shows the right options/selection for
    // profiles saved before the Search page rename.
    const allowedSet = _normalizeLegacyAllowedPages(profileSettings.allowed_pages);
    const normalizedHome = _normalizeLegacyHomePage(profileSettings.home_page);
    Object.entries(pageLabels).forEach(([id, label]) => {
        if (allowedSet && !allowedSet.includes(id)) return; // Skip non-permitted
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = label;
        if (id === normalizedHome) opt.selected = true;
        homeSelect.appendChild(opt);
    });
    form.appendChild(homeSelect);

    // Admin-only settings: allowed pages & can_download
    let pageCheckboxes = [];
    let canDlCheckbox = null;
    if (isAdmin && !isEditingAdmin) {
        const apLabel = document.createElement('label');
        apLabel.className = 'profile-settings-label';
        apLabel.textContent = 'Page Access';
        form.appendChild(apLabel);

        const apContainer = document.createElement('div');
        apContainer.className = 'profile-page-checkboxes';
        Object.entries(pageLabels).forEach(([id, label]) => {
            const lbl = document.createElement('label');
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = id;
            cb.checked = !allowedSet || allowedSet.includes(id);
            lbl.appendChild(cb);
            lbl.appendChild(document.createTextNode(' ' + label));
            apContainer.appendChild(lbl);
            pageCheckboxes.push(cb);
        });
        // Always-on help
        const helpLbl = document.createElement('label');
        const helpCb = document.createElement('input');
        helpCb.type = 'checkbox';
        helpCb.checked = true;
        helpCb.disabled = true;
        helpLbl.appendChild(helpCb);
        helpLbl.appendChild(document.createTextNode(' Help & Docs'));
        apContainer.appendChild(helpLbl);
        form.appendChild(apContainer);

        const dlLabel = document.createElement('label');
        dlLabel.className = 'profile-checkbox-label';
        canDlCheckbox = document.createElement('input');
        canDlCheckbox.type = 'checkbox';
        canDlCheckbox.checked = profileSettings.can_download !== false;
        dlLabel.appendChild(canDlCheckbox);
        dlLabel.appendChild(document.createTextNode(' Can download music'));
        form.appendChild(dlLabel);
    }

    const btnRow = document.createElement('div');
    btnRow.className = 'profile-edit-buttons';

    const saveBtn = document.createElement('button');
    saveBtn.className = 'profile-create-btn';
    saveBtn.textContent = 'Save';
    saveBtn.onclick = async () => {
        const newName = nameInput.value.trim();
        if (!newName) { alert('Name cannot be empty'); return; }
        const newAvatarUrl = urlInput.value.trim() || null;
        const payload = { name: newName, avatar_color: editColor, avatar_url: newAvatarUrl };

        // Home page
        payload.home_page = homeSelect.value || null;

        // Admin-only fields
        if (isAdmin && !isEditingAdmin && pageCheckboxes.length) {
            const allChecked = pageCheckboxes.every(cb => cb.checked);
            payload.allowed_pages = allChecked ? null : pageCheckboxes.filter(cb => cb.checked).map(cb => cb.value);
            payload.can_download = canDlCheckbox ? canDlCheckbox.checked : true;
        }

        try {
            const res = await fetch(`/api/profiles/${profileId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.success) {
                // Update sidebar indicator if editing current profile
                if (currentProfile && currentProfile.id == profileId) {
                    currentProfile.name = newName;
                    currentProfile.avatar_color = editColor;
                    currentProfile.avatar_url = newAvatarUrl;
                    if (payload.home_page !== undefined) currentProfile.home_page = payload.home_page;
                    if (payload.allowed_pages !== undefined) currentProfile.allowed_pages = payload.allowed_pages;
                    if (payload.can_download !== undefined) currentProfile.can_download = payload.can_download;
                    updateProfileIndicator();
                }
                loadProfileManageList();
            } else {
                alert(data.error || 'Failed to update profile');
            }
        } catch (e) {
            alert('Connection error');
        }
    };
    btnRow.appendChild(saveBtn);

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'profile-picker-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = () => form.remove();
    btnRow.appendChild(cancelBtn);

    form.appendChild(btnRow);
    list.appendChild(form);
    nameInput.focus();
    nameInput.select();
}

function showSelfEditForm() {
    if (!currentProfile) return;
    const overlay = document.getElementById('profile-picker-overlay');
    const container = overlay.querySelector('.profile-picker-container');

    // Hide the picker grid and show self-edit form
    const grid = document.getElementById('profile-picker-grid');
    const actions = document.getElementById('profile-picker-actions');
    grid.style.display = 'none';
    actions.style.display = 'none';

    // Remove any existing self-edit form
    const existing = document.getElementById('self-edit-form');
    if (existing) existing.remove();

    // 'search' replaces the legacy 'downloads'/'artists' ids; legacy values are
    // normalized on read below so saving a legacy profile upgrades it.
    const pageLabels = {
        dashboard: 'Dashboard', sync: 'Sync', search: 'Search', discover: 'Discover',
        automations: 'Automations', library: 'Library', stats: 'Listening Stats',
        'playlist-explorer': 'Playlist Explorer', import: 'Import', help: 'Help & Docs'
    };

    const form = document.createElement('div');
    form.id = 'self-edit-form';
    form.className = 'profile-edit-form';
    form.style.marginTop = '16px';

    const title = document.createElement('h3');
    title.textContent = 'My Profile';
    title.style.cssText = 'color: #fff; margin: 0 0 12px; font-size: 18px;';
    form.appendChild(title);

    // Name
    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.className = 'profile-input';
    nameInput.value = currentProfile.name;
    nameInput.maxLength = 20;
    nameInput.placeholder = 'Profile name';
    form.appendChild(nameInput);

    // Home page
    const homeLabel = document.createElement('label');
    homeLabel.className = 'profile-settings-label';
    homeLabel.textContent = 'Home Page';
    form.appendChild(homeLabel);

    const homeSelect = document.createElement('select');
    homeSelect.className = 'profile-input';
    const defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = 'Default (Discover)';
    homeSelect.appendChild(defaultOpt);
    // Normalize legacy ids so the form shows the right options/selection for
    // profiles saved before the Search page rename.
    const ap = _normalizeLegacyAllowedPages(currentProfile.allowed_pages);
    const normalizedHome = _normalizeLegacyHomePage(currentProfile.home_page);
    Object.entries(pageLabels).forEach(([id, label]) => {
        if (ap && !ap.includes(id)) return;
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = label;
        if (id === normalizedHome) opt.selected = true;
        homeSelect.appendChild(opt);
    });
    form.appendChild(homeSelect);

    // Buttons
    const btnRow = document.createElement('div');
    btnRow.className = 'profile-edit-buttons';
    btnRow.style.marginTop = '12px';

    const saveBtn = document.createElement('button');
    saveBtn.className = 'profile-create-btn';
    saveBtn.textContent = 'Save';
    saveBtn.onclick = async () => {
        const newName = nameInput.value.trim();
        if (!newName) { alert('Name cannot be empty'); return; }
        try {
            const res = await fetch(`/api/profiles/${currentProfile.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName, home_page: homeSelect.value || null })
            });
            const data = await res.json();
            if (data.success) {
                currentProfile.name = newName;
                currentProfile.home_page = homeSelect.value || null;
                updateProfileIndicator();
                closeSelfEdit();
                hideProfilePicker();
            } else {
                alert(data.error || 'Failed to update');
            }
        } catch (e) {
            alert('Connection error');
        }
    };
    btnRow.appendChild(saveBtn);

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'profile-picker-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = () => closeSelfEdit();
    btnRow.appendChild(cancelBtn);

    form.appendChild(btnRow);
    container.appendChild(form);

    function closeSelfEdit() {
        form.remove();
        grid.style.display = '';
        actions.style.display = '';
    }
}

async function checkAdminPinRequired() {
    const res = await fetch('/api/profiles');
    const data = await res.json();
    const profiles = data.profiles || [];
    const admin = profiles.find(p => p.is_admin);
    const section = document.getElementById('admin-pin-section');

    if (profiles.length > 1 && admin && !admin.has_pin && section) {
        section.style.display = '';
    } else if (section) {
        section.style.display = 'none';
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
        navigator.serviceWorker.register('/sw.js', { scope: '/' })
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
            const setupData = await setupResp.json();
            if (!setupData.setup_complete) {
                showWizard = true;
                localStorage.removeItem('soulsync_setup_complete');
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
    // Initialize profile management UI handlers
    initProfileManagement();

    // Check profiles first — may show picker instead of app
    const profileReady = await initProfileSystem();
    if (!profileReady) {
        console.log('Waiting for profile selection...');
        return; // App init deferred until profile is selected via picker
    }

    initApp();
}

function initApp() {
    // Initialize components
    initializeNavigation();
    initializeMobileNavigation();
    initializeMediaPlayer();
    initExpandedPlayer();
    initializeSyncPage();
    initializeWatchlist();


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
            // Refresh dashboard-specific data if on dashboard
            const dashboardPage = document.getElementById('dashboard-page');
            if (dashboardPage && dashboardPage.classList.contains('active')) {
                fetchAndUpdateSystemStats();
                fetchAndUpdateActivityFeed();
            }
        }
    });

    // Start always-on download polling (batched, minimal overhead)
    startGlobalDownloadPolling();

    // Load issues badge count
    loadIssuesBadge();

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
    const navButtons = document.querySelectorAll('.nav-button');

    navButtons.forEach(button => {
        button.addEventListener('click', () => {
            const page = button.getAttribute('data-page');
            navigateToPage(page);
        });
    });

    window.addEventListener('popstate', (event) => {
        const page = (event.state && event.state.page) || _getPageFromPath();
        if (page && page !== currentPage) {
            navigateToPage(page, { skipPushState: true });
        }
    });
}

const _DEEPLINK_VALID_PAGES = new Set([
    'dashboard', 'sync', 'search', 'downloads', 'discover', 'artists', 'automations',
    'library', 'import', 'settings', 'help', 'issues', 'stats', 'watchlist',
    'wishlist', 'active-downloads', 'artist-detail', 'playlist-explorer',
    'hydrabase', 'tools'
]);

function _getPageFromPath() {
    const path = window.location.pathname.replace(/^\/+|\/+$/g, '');
    if (!path) return 'dashboard';
    const basePage = path.split('/')[0];
    if (!_DEEPLINK_VALID_PAGES.has(basePage)) return 'dashboard';
    // Context-dependent pages fall back to a sensible parent
    if (basePage === 'artist-detail') return 'library';
    if (basePage === 'playlist-explorer') return 'library';
    return basePage;
}

// ===============================
// MOBILE NAVIGATION
// ===============================

function initializeMobileNavigation() {
    const hamburgerBtn = document.getElementById('hamburger-btn');
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.getElementById('mobile-overlay');

    if (!hamburgerBtn || !sidebar || !overlay) return;

    function openMobileNav() {
        sidebar.classList.add('mobile-open');
        hamburgerBtn.classList.add('active');
        overlay.classList.add('active');
        document.body.classList.add('mobile-nav-open');
    }

    function closeMobileNav() {
        sidebar.classList.remove('mobile-open');
        hamburgerBtn.classList.remove('active');
        overlay.classList.remove('active');
        document.body.classList.remove('mobile-nav-open');
    }

    hamburgerBtn.addEventListener('click', () => {
        if (sidebar.classList.contains('mobile-open')) {
            closeMobileNav();
        } else {
            openMobileNav();
        }
    });

    overlay.addEventListener('click', closeMobileNav);

    // Close sidebar on nav button click (mobile only)
    document.querySelectorAll('.nav-button').forEach(btn => {
        btn.addEventListener('click', () => {
            if (window.innerWidth <= 768) {
                closeMobileNav();
            }
        });
    });
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
        wishlistButton.addEventListener('click', async () => {
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
        });
    }

    // Update watchlist count initially
    updateWatchlistButtonCount();

    // Update count every 10 seconds
    setInterval(updateWatchlistButtonCount, 10000);

    console.log('Watchlist system initialized');
}

function navigateToPage(pageId, options = {}) {
    // Backwards-compat aliases — both legacy ids fold into the unified Search page.
    // 'downloads' was the Search page's old id; 'artists' was the retired inline
    // Artists page, now replaced by clicking artists from the unified Search.
    if (pageId === 'downloads' || pageId === 'artists') pageId = 'search';

    if (pageId === currentPage) {
        // Already on this page — still process pending sync tab actions
        if (pageId === 'sync' && window._pendingSyncTabAction && typeof _applySyncTabAction === 'function') {
            _applySyncTabAction();
        }
        return;
    }

    // Permission guard — redirect to home page if not allowed
    if (!isPageAllowed(pageId)) {
        const home = getProfileHomePage();
        if (home !== currentPage && isPageAllowed(home)) {
            navigateToPage(home);
        }
        return;
    }

    // Update navigation buttons (only if there's a nav button for this page).
    // Pages reachable from many surfaces (artist-detail, playlist-explorer)
    // intentionally have no [data-page] match here — the sidebar shouldn't
    // imply a section the user didn't actually navigate via.
    document.querySelectorAll('.nav-button').forEach(btn => {
        btn.classList.remove('active');
    });
    const navButton = document.querySelector(`[data-page="${pageId}"]`);
    if (navButton) {
        navButton.classList.add('active');
    }

    // Update pages
    document.querySelectorAll('.page').forEach(page => {
        page.classList.remove('active');
    });
    document.getElementById(`${pageId}-page`).classList.add('active');

    currentPage = pageId;

    if (!options.skipPushState) {
        const urlPath = pageId === 'dashboard' ? '/' : '/' + pageId;
        if (window.location.pathname !== urlPath) {
            history.pushState({ page: pageId }, '', urlPath);
        }
    }

    // Show/hide global search bar (hide on search page where the unified search lives)
    if (typeof _gsUpdateVisibility === 'function') _gsUpdateVisibility();

    // Show/hide discover download sidebar based on page
    const downloadSidebar = document.getElementById('discover-download-sidebar');
    if (downloadSidebar) {
        if (pageId === 'discover') {
            // Show sidebar on discover page if there are active downloads
            const activeDownloads = Object.keys(discoverDownloads || {}).length;
            console.log(`📊 [NAVIGATE] Discover page - ${activeDownloads} active downloads`);
            if (activeDownloads > 0) {
                // Update the sidebar UI to render the bubbles
                console.log(`🔄 [NAVIGATE] Updating discover download bar UI`);
                updateDiscoverDownloadBar();
            }
        } else {
            // Always hide sidebar on other pages
            downloadSidebar.classList.add('hidden');
        }
    }

    // Load page-specific data
    loadPageData(pageId);

    // Update page background particles
    if (window.pageParticles && window._particlesEnabled !== false) window.pageParticles.setPage(pageId);

    // Update worker orbs
    if (window.workerOrbs) window.workerOrbs.setPage(pageId);
}

// REPLACE your old loadPageData function with this one:
// REPLACE your old loadPageData function with this corrected one

async function loadPageData(pageId) {
    try {
        // Stop any active polling when navigating away
        stopDbStatsPolling();
        stopDbUpdatePolling();
        stopWishlistCountPolling();
        stopLogPolling();
        // Stop watchlist/wishlist page timers when navigating away
        if (watchlistCountdownInterval) { clearInterval(watchlistCountdownInterval); watchlistCountdownInterval = null; }
        if (wishlistCountdownInterval) { clearInterval(wishlistCountdownInterval); wishlistCountdownInterval = null; }
        if (typeof _stopNebulaLivePolling === 'function') _stopNebulaLivePolling();
        if (pageId !== 'sync') {
            cleanupBeatportContent();
            // Clear any discover sync tab pollers when leaving the sync page
            if (typeof discoverSyncPollers === 'object') {
                for (const key of Object.keys(discoverSyncPollers)) {
                    clearInterval(discoverSyncPollers[key]);
                    delete discoverSyncPollers[key];
                }
            }
            // Reset so discover tab refetches on next visit
            discoverSyncPlaylistsLoaded = false;
        }
        switch (pageId) {
            case 'dashboard':
                await loadDashboardData();
                loadDashboardSyncHistory();
                break;
            case 'sync':
                initializeSyncPage();
                await loadSyncData();
                // Process any pending deep-link tab switch (e.g. from Discover page)
                if (window._pendingSyncTabAction && typeof _applySyncTabAction === 'function') {
                    _applySyncTabAction();
                }
                break;
            case 'search':
                initializeSearch();
                initializeSearchModeToggle();
                initializeFilters();
                break;
            // 'artists' page retired — aliased to 'search' at the top of navigateToPage
            case 'active-downloads':
                loadActiveDownloadsPage();
                break;
            case 'library':
                // Check if we should return to artist detail view instead of list
                if (artistDetailPageState.currentArtistId && artistDetailPageState.currentArtistName) {
                    navigateToPage('artist-detail');
                    if (!artistDetailPageState.isInitialized) {
                        initializeArtistDetailPage();
                        loadArtistDetailData(artistDetailPageState.currentArtistId, artistDetailPageState.currentArtistName);
                    }
                    // Already initialized — DOM content persists, no reload needed
                } else {
                    if (!libraryPageState.isInitialized) {
                        initializeLibraryPage();
                    }
                    // Already initialized — DOM content persists, no reload needed
                }
                break;
            case 'artist-detail':
                // Artist detail page is handled separately by navigateToArtistDetail()
                break;
            case 'discover':
                if (!discoverPageInitialized) {
                    await loadDiscoverPage();
                    discoverPageInitialized = true;
                }
                // Already initialized — DOM content persists, no reload needed
                break;
            case 'playlist-explorer':
                initExplorer();
                break;
            case 'settings':
                initializeSettings();
                switchSettingsTab('connections');
                await loadSettingsData();
                await loadQualityProfile();
                loadApiKeys();
                loadBlacklistCount();
                break;
            case 'stats':
                initializeStatsPage();
                break;
            case 'import':
                initializeImportPage();
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
            case 'tools':
                await initializeToolsPage();
                break;
            case 'watchlist':
                await initializeWatchlistPage();
                break;
            case 'wishlist':
                await initializeWishlistPage();
                break;
            case 'automations':
                await loadAutomations();
                break;
            case 'issues':
                await loadIssuesPage();
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

// ===============================
// SERVICE STATUS MONITORING
// ===============================

// Legacy function - now handled by fetchAndUpdateServiceStatus
// Keeping this for compatibility but it's no longer actively used

// Old updateStatusIndicator function removed - replaced by updateSidebarServiceStatus

// ===============================

