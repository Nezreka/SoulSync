// SUPPORT MODAL
// ===============================

function showSupportModal() {
    const overlay = document.getElementById('support-modal-overlay');
    if (overlay) overlay.classList.remove('hidden');
}

function closeSupportModal() {
    const overlay = document.getElementById('support-modal-overlay');
    if (overlay) overlay.classList.add('hidden');
}

async function copyAddress(address, cryptoName) {
    try {
        // navigator.clipboard requires HTTPS — use fallback for HTTP (Docker)
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(address);
        } else {
            const textarea = document.createElement('textarea');
            textarea.value = address;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
        }
        showToast(`${cryptoName} address copied to clipboard`, 'success');
    } catch (error) {
        console.error('Failed to copy address:', error);
        // Show the address so user can copy manually
        showToast(`${cryptoName}: ${address}`, 'info');
    }
}

// ===============================
// SETTINGS FUNCTIONALITY
// ===============================

let settingsAutoSaveTimer = null;

// (syncPreferExplicitState and syncRetryConditionalRows lived here: Filtering
// and Retry Logic are React now, src/features/settings/tabs/downloads-tab.tsx)

// Sign in with OIDC: the provider's settings (and the admin's connect row
// inside them) only mean something while the toggle is on, so they hide when
// it is off. Hidden fields keep their values and still save, so switching
// OIDC off and on again loses nothing.
function syncOidcProviderBlock() {
    const toggle = document.getElementById('security-oidc-signin');
    const block = document.getElementById('security-oidc-provider');
    if (block) block.hidden = !(toggle && toggle.checked);
}
window.syncOidcProviderBlock = syncOidcProviderBlock;

// (the Quality tab's controls are React and save to their profile themselves:
// src/features/settings/tabs/quality-tab.tsx)

// A field the video side saves through its own /api/video/* endpoints: inside a
// video-only block, or wired by a data-video-* hook.
function _isVideoOwnedField(el) {
    if (!el || typeof el.closest !== 'function') return false;
    if (el.closest('[data-video-only]')) return true;
    return Array.from(el.attributes || []).some(a => a.name.startsWith('data-video-'));
}

function debouncedAutoSaveSettings(event) {
    // Ignore changes made while the page is programmatically populating its
    // fields on load — those aren't user edits and must not trigger a full
    // save (which re-initializes every backend service client).
    if (window._suppressSettingsAutoSave) return;
    // A tab ported to React saves its own controls, and only the keys that
    // changed (src/features/settings/settings-store.ts). Its controls are in
    // the page when initializeSettings wires every input, so without this one
    // change sent two saves: the tab's own and a full-page one (SETTINGS_PORT.md)
    // A vanilla source panel opened in the React source modal sits inside a
    // React slot but is still vanilla: it saves here, as it always did. A
    // panel ported to React carries data-react-panel (step 7b).
    const _t = event?.target;
    if (_t?.closest?.('[data-stg-react]')
        && !_t.closest('.src-config-panel:not([data-react-panel])')) return;
    // On the video side, a VIDEO field (TMDB key, region, autoplay...) saves
    // itself via /api/video/*, so it must not also fire this full save. Every
    // other field on the page - the shared tabs, System, Security - has no
    // saver but this one, so it auto-saves here as it does on the music side.
    // saveSettings keeps the stored active_media_server on the video side, so
    // this can't change the music server. A call with no event is a music write
    // made from code (the download chain's music and audiobook tabs), so it saves.
    if (document.body.getAttribute('data-side') === 'video' && _isVideoOwnedField(event?.target)) return;
    // #879: never auto-save while the last settings load failed — the form is
    // showing defaults, not the real config, so saving would wipe it.
    if (window._settingsLoadFailed) return;
    // #827: the Logs tab has no savable settings — its live-viewer controls
    // (source picker, filters, auto-scroll) were tripping the auto-save and
    // flooding app.log with "Settings saved" lines, drowning out the logs the
    // user is trying to read. Never auto-save while the Logs tab is active.
    if (document.querySelector('.stg-tab.active')?.dataset.tab === 'logs') return;
    if (settingsAutoSaveTimer) clearTimeout(settingsAutoSaveTimer);
    // autosave switched off in System: the change waits for the save button.
    // (the switch itself is React and saves itself, system-tab.tsx)
    if (window._settingsAutosaveOff === true) {
        settingsAutoSaveTimer = null;
        setSettingsSaveState('pending');
        return;
    }
    settingsAutoSaveTimer = setTimeout(() => saveSettings(true), 2000);
    setSettingsSaveState('pending');
}

// the autosave switch is React (system-tab.tsx): it flips
// window._settingsAutosaveOff itself

function handleManualSaveClick() {
    if (settingsAutoSaveTimer) clearTimeout(settingsAutoSaveTimer);
    // a React tab's queued save (the quality profile's too) goes out in this
    // save, through SoulSyncSettings.flush
    saveSettings(false);
}

// settings save themselves (#1909). the old Save Settings button and an
// "auto-saved" toast after every keystroke told two stories, and the tour
// still said nothing applied until you clicked. the button is now the one
// quiet status, and still saves right away when clicked (the video side
// hangs its own saves on that click).
const SETTINGS_SAVE_LABELS = {
    idle: 'All changes saved',
    pending: 'Save now',
    saving: 'Saving…',
    saved: 'Saved',
    error: 'Retry save',
};
let _settingsSavedTimer = null;

function setSettingsSaveState(state) {
    const btn = document.getElementById('save-settings');
    if (!btn || !SETTINGS_SAVE_LABELS[state]) return;
    if (_settingsSavedTimer) { clearTimeout(_settingsSavedTimer); _settingsSavedTimer = null; }
    btn.dataset.state = state;
    const label = btn.querySelector('span');
    const manual = window._settingsAutosaveOff === true;
    if (label) {
        label.textContent = manual && state === 'pending'
            ? 'Save changes' : SETTINGS_SAVE_LABELS[state];
    }
    if (manual) {
        btn.title = state === 'pending' ? 'Autosave is off. Click to save your changes.' : '';
    } else {
        btn.title = state === 'idle' || state === 'saved'
            ? 'Settings save as you go. Click to save again now.'
            : '';
    }
    if (state === 'saved') {
        _settingsSavedTimer = setTimeout(() => setSettingsSaveState('idle'), 2000);
    }
}

function syncMetadataSourceSelection(source) {
    const select = document.getElementById('metadata-fallback-source');
    if (!select || !source) return;
    const option = select.querySelector(`option[value="${source}"]`);
    if (option) select.value = source;
    select.dataset.lastValidSource = source;
}

function _isMetadataSourceSelectable(source) {
    if (source === 'spotify') {
        // Official Spotify needs a connected session.
        return _lastStatusPayload?.spotify?.authenticated === true;
    }
    if (source === 'spotify_free') {
        // No-creds Spotify only needs the SpotipyFree package installed —
        // selecting it IS the opt-in, so it must NOT depend on having selected it.
        return _lastStatusPayload?.spotify?.free_installed === true;
    }
    if (source === 'discogs') {
        const token = document.getElementById('discogs-token');
        return !!token?.value?.trim();
    }
    if (source === 'jiosaavn') {
        return document.getElementById('experimental-jiosaavn-enabled')?.checked === true;
    }
    return true;
}

function syncJiosaavnMetadataSourceOption(enabled) {
    const select = document.getElementById('metadata-fallback-source');
    if (!select) return;

    let option = select.querySelector('option[value="jiosaavn"]');
    if (enabled) {
        if (!option) {
            option = document.createElement('option');
            option.value = 'jiosaavn';
            option.textContent = 'JioSaavn';
            const anchor = select.querySelector('option[value="musicbrainz"]');
            if (anchor?.nextSibling) {
                select.insertBefore(option, anchor.nextSibling);
            } else {
                select.appendChild(option);
            }
        }
        return;
    }

    if (select.value === 'jiosaavn') {
        select.value = 'deezer';
        select.dataset.lastValidSource = 'deezer';
    }
    option?.remove();
}

let _experimentalEnableResolver = null;

function showExperimentalEnableDialog({ title, message }) {
    if (_experimentalEnableResolver) {
        _experimentalEnableResolver(false);
        _experimentalEnableResolver = null;
    }

    const overlay = document.getElementById('experimental-enable-modal-overlay');
    const titleEl = document.getElementById('experimental-enable-modal-title');
    const messageEl = document.getElementById('experimental-enable-modal-message');
    const ack = document.getElementById('experimental-enable-ack-checkbox');
    const confirmBtn = document.getElementById('experimental-enable-confirm-btn');
    if (!overlay || !titleEl || !messageEl || !ack || !confirmBtn) {
        return Promise.resolve(false);
    }

    titleEl.textContent = title || 'Enable experimental feature';
    messageEl.textContent = message || '';
    ack.checked = false;
    confirmBtn.disabled = true;

    const onAckChange = () => {
        confirmBtn.disabled = !ack.checked;
    };
    ack.onchange = onAckChange;

    overlay.classList.remove('hidden');

    return new Promise((resolve) => {
        _experimentalEnableResolver = (result) => {
            ack.onchange = null;
            resolve(result);
        };
    });
}

function resolveExperimentalEnableDialog(confirmed) {
    const overlay = document.getElementById('experimental-enable-modal-overlay');
    const ack = document.getElementById('experimental-enable-ack-checkbox');
    const confirmBtn = document.getElementById('experimental-enable-confirm-btn');
    const ok = confirmed && ack?.checked;

    if (overlay) overlay.classList.add('hidden');
    if (ack) ack.checked = false;
    if (confirmBtn) confirmBtn.disabled = true;

    if (_experimentalEnableResolver) {
        _experimentalEnableResolver(ok);
        _experimentalEnableResolver = null;
    }
}

function syncJiosaavnEnrichmentBubble(enabled) {
    // Re-broadcast for the React dashboard (tools-seam rule: in the handler) —
    // its JioSaavn orb shows/hides on this, since the container write below
    // only reaches the vanilla markup.
    window.dispatchEvent(new CustomEvent('ss:jiosaavn-experimental', { detail: { enabled: !!enabled } }));
    // No container write since the dashboard flip — the JioSaavn orb is
    // React-rendered and shows/hides on the dispatch above.
    if (typeof refreshRateMonitorExperimentalVisibility === 'function') {
        refreshRateMonitorExperimentalVisibility();
    }
    if (enabled && typeof renderEnrichmentRail === 'function') {
        renderEnrichmentRail();
    }
    // The chips replay is React's now: service-cards.tsx re-renders its
    // cached enrichment payload on the ss:jiosaavn-experimental dispatch.
}

async function onExperimentalJiosaavnToggle(checkbox) {
    if (!checkbox) return;

    if (!checkbox.checked) {
        syncJiosaavnMetadataSourceOption(false);
        syncJiosaavnEnrichmentBubble(false);
        debouncedAutoSaveSettings();
        return;
    }

    checkbox.checked = false;

    const acknowledged = await showExperimentalEnableDialog({
        title: 'Enable JioSaavn',
        message: 'JioSaavn uses a third-party public API proxy. Coverage and availability may change without notice. Once enabled, JioSaavn will appear as a metadata source on the Connections tab and in search.',
    });

    if (!acknowledged) return;

    checkbox.checked = true;
    syncJiosaavnMetadataSourceOption(true);
    syncJiosaavnEnrichmentBubble(true);
    debouncedAutoSaveSettings();
}

async function onExperimentalBandcampToggle(checkbox) {
    if (!checkbox) return;

    if (!checkbox.checked) {
        debouncedAutoSaveSettings();
        return;
    }

    checkbox.checked = false;

    const acknowledged = await showExperimentalEnableDialog({
        title: 'Enable Bandcamp',
        message: 'Bandcamp has no official public search/metadata API — this uses Bandcamp\'s own public search and release-page endpoints. Coverage and availability may change without notice. Once enabled, Bandcamp will appear as a search source in Discover and as a metadata enrichment source for downloaded tracks.',
    });

    if (!acknowledged) return;

    checkbox.checked = true;
    debouncedAutoSaveSettings();
}

function _metadataSourceFallback(source) {
    if (source === 'spotify') return 'deezer';
    return 'deezer';
}

function focusServiceSettingsSection(service, message) {
    const card = document.querySelector(`#settings-page .stg-service[data-service="${service}"]`);
    if (!card) return;

    const header = card.querySelector('.stg-service-header');
    if (!card.classList.contains('expanded') && header) {
        toggleStgService(header);
    }

    card.scrollIntoView({ behavior: 'smooth', block: 'center' });

    const firstControl = card.querySelector('input, button');
    if (firstControl) {
        firstControl.focus({ preventScroll: true });
    }

    if (message) {
        showToast(message, 'warning');
    }
}

function sanitizeMetadataSourceSelection({ quiet = true } = {}) {
    const select = document.getElementById('metadata-fallback-source');
    if (!select) return false;

    const selectedSource = select.value || 'deezer';
    if (_isMetadataSourceSelectable(selectedSource)) {
        select.dataset.lastValidSource = selectedSource;
        return false;
    }

    const lastValid = select.dataset.lastValidSource;
    const fallbackSource = lastValid && lastValid !== selectedSource && _isMetadataSourceSelectable(lastValid)
        ? lastValid
        : _metadataSourceFallback(selectedSource);

    if (fallbackSource && fallbackSource !== selectedSource) {
        select.value = fallbackSource;
    }
    select.dataset.lastValidSource = fallbackSource;

    if (!quiet) {
        const message = selectedSource === 'discogs'
            ? 'Discogs requires a personal access token before it can be selected as the primary metadata source.'
            : 'Spotify must be authenticated before it can be selected as the primary metadata source.';
        focusServiceSettingsSection(selectedSource, message);
    }

    return true;
}

function handleMetadataSourceChange(event) {
    const select = event.target;
    if (!select || select.id !== 'metadata-fallback-source') return;

    const selectedSource = select.value;
    if (_isMetadataSourceSelectable(selectedSource)) {
        select.dataset.lastValidSource = selectedSource;
        return;
    }

    sanitizeMetadataSourceSelection({ quiet: false });
}

let _settingsInitialized = false;
// Tell password-manager extensions (Bitwarden / 1Password / LastPass) to ignore
// this app's credential inputs. The settings page is full of API-key / token /
// secret fields; password managers treat them as login forms and re-scan the
// whole (large, constantly-mutating) DOM on every change, which can peg the main
// thread for seconds. These attributes make them skip the fields entirely.
function _markCredentialFieldsNoAutofill(root) {
    const scope = root || document;
    scope.querySelectorAll('input, textarea').forEach((el) => {
        if (el.dataset.bwignore !== undefined) return; // already tagged
        el.setAttribute('data-bwignore', '');          // Bitwarden
        el.setAttribute('data-1p-ignore', '');         // 1Password
        el.setAttribute('data-lpignore', 'true');      // LastPass
        if (!el.getAttribute('autocomplete')) el.setAttribute('autocomplete', 'off');
    });
}
// Run once on load (inputs exist from page load — all pages are mounted).
if (document.readyState !== 'loading') _markCredentialFieldsNoAutofill();
else document.addEventListener('DOMContentLoaded', () => _markCredentialFieldsNoAutofill());

function initializeSettings() {
    // This function is called when the settings page is loaded.
    // It attaches event listeners to all interactive elements on the page.
    // Listeners are stable for the page lifetime, so wiring them once avoids
    // re-scanning the ~960-node settings subtree on every revisit (scroll jank).
    if (_settingsInitialized) return;
    _settingsInitialized = true;

    // Re-tag in case any inputs were added dynamically since page load.
    _markCredentialFieldsNoAutofill(document.getElementById('settings-page'));

    // Main save button (manual save, non-quiet)
    // Uses named function reference so addEventListener deduplicates across repeated calls
    const saveButton = document.getElementById('save-settings');
    if (saveButton) {
        saveButton.addEventListener('click', handleManualSaveClick);
    }

    // Debounced auto-save on all settings inputs
    // Uses named function reference (debouncedAutoSaveSettings) so addEventListener deduplicates
    const settingsPage = document.getElementById('settings-page');
    if (settingsPage) {
        settingsPage.querySelectorAll('input[type="text"], input[type="url"], input[type="password"], input[type="number"], input[type="range"]').forEach(input => {
            input.addEventListener('input', debouncedAutoSaveSettings);
        });
        // Textareas were in NEITHER list, so nothing on the page auto-saved
        // them. Paste a cookies.txt, click Test, and the test ran against the
        // config as it was before the paste — the value only reached the server
        // if something else happened to trigger a save. 'input' rather than
        // 'change' because 'change' on a textarea waits for blur, and pasting
        // then clicking a button inside the same panel never blurs it.
        // The Hydrabase payload boxes are a dev console, not settings, and must
        // not fire a full settings save on every keystroke.
        settingsPage.querySelectorAll('textarea:not(.hydra-payload)').forEach(input => {
            input.addEventListener('input', debouncedAutoSaveSettings);
        });
        settingsPage.querySelectorAll('input[type="checkbox"], select').forEach(input => {
            input.addEventListener('change', debouncedAutoSaveSettings);
        });
    }

    const metadataSourceSelect = document.getElementById('metadata-fallback-source');
    if (metadataSourceSelect) {
        metadataSourceSelect.addEventListener('change', handleMetadataSourceChange);
    }
    const discogsTokenInput = document.getElementById('discogs-token');
    if (discogsTokenInput) {
        discogsTokenInput.addEventListener('input', () => {
            if (typeof syncPrimaryMetadataSourceAvailability === 'function') {
                syncPrimaryMetadataSourceAvailability(_lastStatusPayload?.spotify || null);
            }
            sanitizeMetadataSourceSelection({ quiet: true });
        });
    }

    // Server toggle buttons
    const plexToggle = document.getElementById('plex-toggle');
    if (plexToggle) {
        plexToggle.addEventListener('click', () => toggleServer('plex'));
    }
    const jellyfinToggle = document.getElementById('jellyfin-toggle');
    if (jellyfinToggle) {
        jellyfinToggle.addEventListener('click', () => toggleServer('jellyfin'));
    }

    // Auto-detect buttons
    // (slskd's Auto-detect is in the React Soulseek panel. this listener and
    // the button's own onclick both ran it, twice per click)
    const detectPlexBtn = document.querySelector('#plex-container .detect-button');
    if (detectPlexBtn) {
        detectPlexBtn.addEventListener('click', autoDetectPlex);
    }
    const detectJellyfinBtn = document.querySelector('#jellyfin-container .detect-button');
    if (detectJellyfinBtn) {
        detectJellyfinBtn.addEventListener('click', autoDetectJellyfin);
    }

    // Test connection buttons
    // Test button event listeners removed - they use onclick attributes in HTML to avoid double firing

    if (typeof syncPrimaryMetadataSourceAvailability === 'function') {
        syncPrimaryMetadataSourceAvailability(_lastStatusPayload?.spotify || null);
    }
    syncSpotifySettingsAuthState(_lastStatusPayload?.spotify || null);
    syncMetadataSourceSelection(_lastStatusPayload?.metadata_source?.source);
    sanitizeMetadataSourceSelection({ quiet: true });
    if (metadataSourceSelect) {
        metadataSourceSelect.dataset.lastValidSource = metadataSourceSelect.value;
    }
}

// resetFileOrganizationTemplates and validateFileOrganizationTemplates: the
// Library tab's Organization card is React (library-organization.tsx,
// organization.ts)

// ── Connections: services as tiles ─────────────────────────────────────────
//
// 22 services lived in two "API Configuration" groups as nested accordions, so
// finding out whether Last.fm was even configured meant scrolling a wall of
// chevrons and opening them one at a time. The tiles answer that without
// opening anything, and the media tabs stop the music side and the video side
// being one undifferentiated list.
//
// The forms themselves are NOT rebuilt. A tile opens the real panel in a modal
// by MOVING the node - never cloning it - so every id stays unique and all the
// existing save/load/test code keeps working with no idea this happened. Same
// contract the Sources tab runs on, and the reason it is worth restating: a
// clone would duplicate ~200 field ids, and on this page a duplicated id is how
// a save writes the wrong value into a real credential.
//
// Where a panel goes home is remembered as (parent, nextSibling) rather than a
// shared holding container. It restores to the exact slot it came from, so the
// accordion groups below keep their order even after a service has been opened.
const SERVICE_KINDS = {
    music: { label: 'Music', attr: 'data-service' },
    video: { label: 'Video', attr: 'data-video-service' },
};

// Real brand art wherever the repo already has it. A service with no logo gets
// a monogram on its own colour rather than a grey blob - the point of the grid
// is to be recognisable at a glance, and a row of identical blobs is a list you
// have to read anyway.
const SERVICE_LOGOS = {
    spotify: '/static/img/brands/spotify.png',
    itunes: '/static/img/brands/itunes.png',
    deezer: '/static/img/brands/deezer.png',
    discogs: '/static/img/brands/discogs.svg',
    tidal: '/static/img/brands/tidal.svg',
    qobuz: '/static/img/brands/qobuz.svg',
    lastfm: '/static/img/brands/lastfm.png',
    genius: '/static/img/brands/genius.png',
    musicbrainz: '/static/img/brands/musicbrainz.png',
    listenbrainz: '/static/img/brands/listenbrainz.png',
    tmdb: '/static/img/brands/tmdb.svg',
    tvdb: '/static/img/brands/tvdb.svg',
    ytextras: '/static/img/brands/youtube.svg',
};

// Marks that are dark artwork and vanish on a dark tile, same list the download
// chain keeps for the same reason.
const INVERT_SERVICE_MARKS = new Set(['tidal', 'qobuz', 'discogs', 'tvdb']);

// Services with no brand art in the repo. Rather than invent icons, these reuse
// the glyphs the VIDEO DASHBOARD already shows for the same sources - so OMDb is
// the same star in both places and the app keeps ONE vocabulary. A service with
// neither a logo nor an established glyph falls back to its monogram; inventing
// one here would be the start of a second, competing icon set.
const SERVICE_GLYPHS = {
    omdb: '\u2605',             // star - ratings, as on the dashboard
    fanart: '\u{1F3A8}',        // palette - artwork
    'artist-fanart': '\u{1F3A8}', // the same source, for artist artwork
    opensubtitles: '\u{1F4AC}', // speech bubble - subtitles
    trakt: '\u2605',            // star - audience rating
    nokey: '\u{1F4FA}',         // tv - community data (TVmaze)
};

// Grouped by what each service does for you, same headings as the download
// chain. Order inside a group is the order here, not page order. A service not
// listed lands in "Other" so a new one can never vanish from the grid.
const SERVICE_GROUPS = {
    music: [
        { label: 'Metadata', hint: 'artists, albums and tracks',
          ids: ['musicbrainz', 'spotify', 'itunes', 'deezer', 'discogs', 'tidal', 'qobuz', 'hydrabase'] },
        { label: 'Scrobbling', hint: 'listening history and recommendations',
          ids: ['lastfm', 'listenbrainz'] },
        { label: 'Extras', hint: 'lyrics, concerts, fingerprint checks, cover art and artist artwork',
          ids: ['genius', 'concerts', 'acoustid', 'pollinations', 'artist-fanart'] },
    ],
    video: [
        { label: 'Metadata', hint: 'movies, shows and episodes',
          ids: ['tmdb', 'tvdb', 'nokey'] },
        { label: 'Ratings & lists', hint: 'scores and list collections',
          ids: ['omdb', 'trakt', 'mdblist'] },
        { label: 'Subtitles', hint: 'found and downloaded for your library',
          ids: ['opensubtitles', 'subdl', 'subsource'] },
        { label: 'Artwork & extras', hint: 'posters, fanart and trailers',
          ids: ['fanart', 'ytextras'] },
    ],
};

// Follow the side you are standing on, exactly like the download chain does.
// Defaulting to music meant a video user landed on a grid of Spotify and Tidal
// tiles with their own TMDB key nowhere in sight.
let _svcKind = 'music';
let _svcKindChosen = false;

function _svcSyncKindToSide() {
    if (_svcKindChosen) return;
    _svcKind = document.body.getAttribute('data-side') === 'video' ? 'video' : 'music';
}
let _svcOpen = null;          // { id, parent, next } for the panel currently lifted

function _svcFrames(kind) {
    const attr = SERVICE_KINDS[kind]?.attr;
    if (!attr) return [];
    // Scoped to the CONNECTIONS tab. Unscoped this also picked up "Detail Pages",
    // a video-preferences panel that lives on the Library tab and is not a
    // service at all - it appeared in the grid as a connection you could
    // configure, which it is not.
    return Array.from(document.querySelectorAll(`#settings-page .api-service-frame[${attr}]`))
        .filter(f => f.closest('[data-stg="connections"]'));
}

function _svcMeta(frame) {
    const title = frame.querySelector('.service-title')?.textContent?.trim() || '';
    const id = frame.getAttribute('data-service') || frame.getAttribute('data-video-service') || '';
    const logo = SERVICE_LOGOS[id]
        || frame.querySelector('.video-svc-logo')?.getAttribute('src')
        || null;
    const dot = frame.querySelector('.stg-service-dot');
    const colour = dot?.style?.color || '';
    const required = !!frame.querySelector('.stg-req-pill');
    // "Deezer (Favorites & Playlists)" is a name plus an explanation; the tile
    // shows the name and the modal shows the rest.
    const paren = title.indexOf('(');
    const name = (paren > 0 ? title.slice(0, paren) : title).trim();
    return {
        id, name,
        detail: paren > 0 ? title.slice(paren + 1).replace(/\)\s*$/, '').trim() : '',
        logo, colour, required,
        inverted: INVERT_SERVICE_MARKS.has(id),
        glyph: SERVICE_GLYPHS[id] || null,
        monogram: (name.replace(/[^A-Za-z0-9]/g, '')[0] || '?').toUpperCase(),
    };
}

// A service counts as configured when it holds a non-empty credential. Read
// from the live inputs, never from a cached settings object - the same rule the
// library summaries follow, and for the same reason.
function _svcConfigured(frame) {
    const fields = frame.querySelectorAll('input[type="text"], input[type="password"], input[type="email"]');
    if (fields.length) {
        for (const f of fields) {
            if (String(f.value || '').trim()) return true;
        }
        return false;
    }
    // keyless services (Community Data, YouTube Extras) are on/off instead
    const toggles = frame.querySelectorAll('input[type="checkbox"]');
    for (const t of toggles) {
        if (t.checked) return true;
    }
    return null;          // nothing to judge by - say nothing rather than "off"
}

function buildServiceTiles() {
    _svcSyncKindToSide();
    const grid = document.getElementById('service-tile-grid');
    const tabs = document.getElementById('svc-tabs');
    if (!grid || !tabs) return;

    tabs.innerHTML = Object.entries(SERVICE_KINDS).map(([k, spec]) =>
        `<button type="button" role="tab" class="dlchain-tab${k === _svcKind ? ' active' : ''}" `
        + `onclick="switchServiceKind('${k}')">${spec.label}</button>`).join('');

    // the tab owns the whole tab, not just the grid: the matching side's server
    // section comes with it, so what is on screen agrees with what the tab says
    document.querySelectorAll('#settings-page .settings-group[data-svc-side]').forEach(g => {
        g.classList.toggle('svc-side-active', g.getAttribute('data-svc-side') === _svcKind);
    });

    const frames = _svcFrames(_svcKind);
    if (!frames.length) {
        grid.innerHTML = '<div class="dlchain-empty">No services on this side yet.</div>';
        return;
    }
    const attr = SERVICE_KINDS[_svcKind].attr;
    const tile = (frame) => {
        const id = frame.getAttribute(attr);
        const m = _svcMeta(frame);
        const set = _svcConfigured(frame);
        const state = set === null ? 'na' : (set ? 'ok' : 'warn');
        // real artwork first, then the app's own glyph for that source, then a
        // monogram. each step is a real fallback, not a placeholder.
        const fallback = m.glyph
            ? `<span class="svc-glyph">${m.glyph}</span>`
            : `<span class="svc-monogram">${escapeHtml(m.monogram)}</span>`;
        const art = m.logo
            ? `<img class="svc-mark${m.inverted ? ' is-inverted' : ''}" src="${escapeHtml(m.logo)}" alt="" `
              + `onerror="this.replaceWith(document.createRange().createContextualFragment(this.dataset.fb))" `
              + `data-fb="${escapeHtml(fallback)}">`
            : fallback;
        const chip = m.required && !set
            ? '<span class="src-tile-chip src-tile-chip--warn">required</span>'
            : (set === null ? '<span class="src-tile-chip">no key needed</span>'
               : set ? '<span class="src-tile-chip src-tile-chip--on">configured</span>'
                     : '<span class="src-tile-chip">not set up</span>');
        const brand = (m.colour || '').replace('#', '');
        return `<button type="button" class="svc-tile${set ? ' is-active' : ''}" `
             + `data-service-tile="${escapeHtml(id)}" onclick="openServiceModal('${escapeHtml(id)}')" `
             + `style="--svc-brand: ${escapeHtml(m.colour || '#8a8a8a')}" `
             + `title="Configure ${escapeHtml(m.name)}">`
             + `<span class="src-tile-dot hss-${state}"></span>`
             + `<span class="svc-tile-art">${art}</span>`
             + `<span class="svc-tile-name">${escapeHtml(m.name)}</span>`
             + chip + '</button>';
    };

    const byId = new Map(frames.map(f => [f.getAttribute(attr), f]));
    const groups = (SERVICE_GROUPS[_svcKind] || []).map(g => ({
        ...g, frames: g.ids.map(id => byId.get(id)).filter(Boolean),
    }));
    const listed = new Set(groups.flatMap(g => g.ids));
    groups.push({ label: 'Other', hint: '', frames: frames.filter(f => !listed.has(f.getAttribute(attr))) });

    grid.innerHTML = groups.filter(g => g.frames.length).map(g =>
        `<div class="src-group"><div class="src-group-head">`
        + `<span class="src-group-label">${g.label}</span>`
        + `<span class="src-group-count">${g.frames.length}</span>`
        + (g.hint ? `<span class="src-group-hint">${g.hint}</span>` : '')
        + `</div><div class="svc-tile-row">${g.frames.map(tile).join('')}</div></div>`).join('');
}
window.buildServiceTiles = buildServiceTiles;

function switchServiceKind(kind) {
    if (!SERVICE_KINDS[kind]) return;
    _svcKindChosen = true;      // an explicit pick outranks the side default
    _svcKind = kind;
    buildServiceTiles();
}
window.switchServiceKind = switchServiceKind;

function openServiceModal(id) {
    const attr = SERVICE_KINDS[_svcKind].attr;
    const frame = document.querySelector(`#settings-page .api-service-frame[${attr}="${CSS.escape(id)}"]`);
    const overlay = document.getElementById('service-config-modal');
    const body = document.getElementById('svc-modal-body');
    if (!frame || !overlay || !body) return;
    if (_svcOpen) closeServiceModal();          // never lift two panels at once

    const m = _svcMeta(frame);
    document.getElementById('svc-modal-title').textContent = m.name;
    document.getElementById('svc-modal-sub').textContent = m.detail;
    document.getElementById('svc-modal-icon').innerHTML = m.logo
        ? `<img class="svc-mark${m.inverted ? ' is-inverted' : ''}" src="${escapeHtml(m.logo)}" alt="">`
        : (m.glyph ? `<span class="svc-glyph">${m.glyph}</span>`
                   : `<span class="svc-monogram">${escapeHtml(m.monogram)}</span>`);

    // remember the exact slot so it goes back where it came from
    _svcOpen = { id, attr, parent: frame.parentNode, next: frame.nextSibling };
    body.appendChild(frame);
    // the accordion inside is collapsed by default; in a modal it IS the content
    frame.querySelector('.stg-service-body')?.style.setProperty('display', 'block');
    overlay.hidden = false;
    document.body.classList.add('src-modal-open');
}
window.openServiceModal = openServiceModal;

function closeServiceModal() {
    const overlay = document.getElementById('service-config-modal');
    if (!overlay) return;
    // home BEFORE hiding, so the panel's ids never sit inside a hidden modal
    if (_svcOpen) {
        const frame = document.querySelector(
            `#settings-page .api-service-frame[${_svcOpen.attr}="${CSS.escape(_svcOpen.id)}"]`);
        if (frame && _svcOpen.parent) {
            frame.querySelector('.stg-service-body')?.style.removeProperty('display');
            _svcOpen.parent.insertBefore(frame, _svcOpen.next);
        }
    }
    _svcOpen = null;
    overlay.hidden = true;
    document.body.classList.remove('src-modal-open');
    // an api key typed seconds before closing would otherwise sit on the 2s
    // autosave timer with the modal gone and nothing on screen saying so
    if (typeof settingsAutoSaveTimer !== 'undefined' && settingsAutoSaveTimer) {
        clearTimeout(settingsAutoSaveTimer);
        settingsAutoSaveTimer = null;
        saveSettings(true);
    }
    buildServiceTiles();
}
window.closeServiceModal = closeServiceModal;

document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const overlay = document.getElementById('service-config-modal');
    if (overlay && !overlay.hidden) closeServiceModal();
});

// ── Library tab: what each section currently SAYS ──────────────────────────
//
// Ten collapsed cards whose hints all read like table-of-contents entries
// ("Metadata, tags, conversion, lyrics") tell you what is inside a section but
// nothing about your install. You had to open all ten to learn anything, which
// is the same problem the Sources tab had before its tiles started showing
// state - and showing state is what made that page work.
//
// So each header reports its own current value instead. The card still says
// what it is; the hint now says what it is SET TO.
//
// Rules that keep this honest:
//   * only report what is actually on the page. a field that has not rendered
//     yet reports nothing rather than "off" - claiming a setting is disabled
//     when you simply cannot see it is worse than staying quiet.
//   * derive from the live controls, never from a second copy of the state.
//     a summary that can disagree with the field below it is a bug generator.
const LIBRARY_SUMMARIES = {
    // 'library-paths': the Folders card is React (library-folders.tsx)
    // 'library-music-org': the Organization card is React (library-organization.tsx)
    'library-video-folders': () => {
        const set = ['video-movies-path', 'video-tv-path', 'video-youtube-path']
            .filter(id => (document.getElementById(id)?.value || '').trim()).length;
        const extra = [...document.querySelectorAll('[data-video-extra-kind] input')]
            .filter(input => input.value.trim()).length;
        const primary = set ? `${set} of 3 libraries set` : 'no primary libraries set';
        return extra ? `${primary} · ${extra} additional` : (set ? primary : 'no libraries set yet');
    },
    // 'library-post-processing': React (library-processing.tsx)
    // library-filtering: the Downloads tab is React (tabs/downloads.ts)
    // library-playlists, library-stats and library-discovery: the Discovery
    // tab is React and renders its own summaries (tabs/discovery.ts)
    'library-video-prefs': () => {
        const region = document.getElementById('video-watch-region');
        const label = region?.selectedOptions?.[0]?.textContent?.trim();
        return label ? `where to watch: ${label}` : '';
    },
};

function refreshLibrarySummaries() {
    for (const [key, compute] of Object.entries(LIBRARY_SUMMARIES)) {
        const slot = document.querySelector(`[data-stg-summary="${key}"]`);
        if (!slot) continue;
        let text = '';
        try { text = compute() || ''; } catch (e) { text = ''; }
        // an empty summary falls back to the static description rather than
        // leaving a blank gap where a hint used to be
        if (text) {
            slot.textContent = text;
            slot.classList.add('is-live');
        } else {
            slot.textContent = slot.dataset.stgFallback || '';
            slot.classList.remove('is-live');
        }
    }
}
window.refreshLibrarySummaries = refreshLibrarySummaries;

// recompute whenever anything on the page changes, not on a timer
document.addEventListener('change', (e) => {
    if (e.target.closest?.('#settings-page')) refreshLibrarySummaries();
});
document.addEventListener('input', (e) => {
    // the summarised sections now live on more than the Library tab (#1822)
    if (e.target.closest?.('#settings-page [data-stg]')) refreshLibrarySummaries();
});

// ── Collapsible section headers: keyboard + screen reader ──────────────────
//
// The 31 section toggles are <div onclick>. That works for a mouse and for
// nothing else: Tab skipped every one of them, Enter and Space did nothing, and
// the :focus-visible styling written for them could never fire. They carry
// role="button" and tabindex="0" now, and this supplies the half a real button
// would have given for free.
//
// Delegated rather than 31 listeners: the markup is static, but a delegated
// handler cannot go stale if a section is ever rendered late.
//
// aria-expanded is kept in sync here too. Setting it once in the markup and
// never updating it is worse than omitting it - a screen reader would announce
// "collapsed" for a section the user just opened.
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    const header = e.target.closest?.('#settings-page .settings-section-header[role="button"]');
    if (!header) return;
    e.preventDefault();          // Space would scroll the page
    header.click();              // reuse the inline toggle - one behaviour, not two
});

document.addEventListener('click', (e) => {
    const header = e.target.closest?.('#settings-page .settings-section-header[role="button"]');
    if (!header) return;
    // after the inline handler has run, so it reports the state we ended in
    requestAnimationFrame(() => {
        header.setAttribute('aria-expanded', header.classList.contains('collapsed') ? 'false' : 'true');
    });
});

// switchLibraryMediaTab and handleLibraryMediaTabKey: both Library media cards
// are React (library-card.tsx)

// Settings redesign — tab switching + service accordions
function switchSettingsTab(tab) {
    const search = document.getElementById('stg-search-input');
    const results = document.getElementById('stg-search-results');
    if (search) search.value = '';
    if (results) results.hidden = true;
    if (tab === 'library') document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
    // Update tab bar
    document.querySelectorAll('#settings-page .stg-tab').forEach(t => {
        const active = t.dataset.tab === tab;
        t.classList.toggle('active', active);
        if (active) t.setAttribute('aria-current', 'page');
        else t.removeAttribute('aria-current');
    });
    // The tabs ported to React render their [data-stg] blocks into their slots
    // ([data-stg-react]) here, before the blocks are shown and hidden by tab
    // just below (src/features/settings/host.tsx, SETTINGS_PORT.md)
    if (window.SoulSyncSettings) {
        try { window.SoulSyncSettings.show(tab); } catch (e) { console.error('settings tab mount failed', e); }
    }
    // Show/hide settings groups and section headers by data-stg attribute
    document.querySelectorAll('#settings-page [data-stg]').forEach(g => {
        g.style.display = g.dataset.stg === tab ? '' : 'none';
    });
    // Re-apply collapsed state on section bodies (tab switch resets inline display)
    document.querySelectorAll('#settings-page .settings-section-body.collapsed').forEach(b => {
        b.style.display = 'none';
    });
    // Also hide/show the column wrappers if they're empty in this tab
    document.querySelectorAll('#settings-page .settings-left-column, #settings-page .settings-right-column, #settings-page .settings-third-column').forEach(col => {
        const hasVisible = Array.from(col.querySelectorAll('.settings-group[data-stg]')).some(g => g.style.display !== 'none');
        col.style.display = hasVisible ? '' : 'none';
    });
    // DB maintenance info loads itself on arriving at System (system-tab.tsx)
    // (the yt-dlp tile loads itself in the React YouTube panel: on arriving at
    // Sources and on opening YouTube)
    if (tab === 'advanced' && typeof loadImageCacheStatus === 'function') {
        try { loadImageCacheStatus(); } catch (e) { }
    }
    // #1822: MetaSync lives on Connections. (sidebar weather, on Appearance,
    // reloads itself each time its React tab is opened)
    if (tab === 'connections' && typeof loadMetaSyncSettings === 'function') {
        try { loadMetaSyncSettings(); } catch (e) { }
    }
    // (the download chain and the Sources tiles re-read the video chain and
    // fill their dots themselves when their tab opens: sources-tab.tsx)
    // (the live log viewer starts and stops itself: its React tab is told
    // whether it is the open tab, src/features/settings/host.tsx)
    // Refresh the green/yellow header gradient when arriving on Connections
    if (tab === 'connections') {
        try { applyServiceStatusGradients(); } catch (e) { }
    }

    // Update active section title & subtitle in the detail header
    const titles = {
        connections: { title: 'Connections', sub: 'Media servers, accounts, Soulseek, MetaSync and notifications' },
        sources: { title: 'Sources', sub: 'Configure and prioritize acquisition sources and indexers' },
        downloads: { title: 'Downloads', sub: 'Queue behavior, retries, publishing, and what gets filtered out' },
        quality: { title: 'Quality', sub: 'Audio and video formats, release profiles, and ladders' },
        library: { title: 'Library', sub: 'Folders, file organization, tagging, and import' },
        discovery: { title: 'Discovery', sub: 'Playlists, listening stats, recommendations and import lists' },
        appearance: { title: 'Appearance', sub: 'Visual themes, accents, animations, and the sidebar weather' },
        advanced: { title: 'System', sub: 'Security, database tools, caches, logging, the API and experimental features' },
        logs: { title: 'Logs', sub: 'Live streaming logs and real-time operational diagnostics' }
    };
    const titleEl = document.getElementById('stg-active-title');
    const subEl = document.getElementById('stg-active-sub');
    if (titleEl && titles[tab]) titleEl.textContent = titles[tab].title;
    if (subEl && titles[tab]) subEl.textContent = titles[tab].sub;
}

function filterSettings(query) {
    const results = document.getElementById('stg-search-results');
    if (!results) return;
    const q = (query || '').trim().toLowerCase();
    results.replaceChildren();
    results.hidden = !q;
    if (!q) return;
    // Search only labels/help, never saved values or credentials. Navigation
    // initializes the chosen category instead of exposing hidden, unloaded forms.
    const matches = [];
    document.querySelectorAll('#settings-page .settings-group[data-stg], #settings-page .settings-section-body[data-stg]').forEach(section => {
        if (!(section.textContent || '').toLowerCase().includes(q)) return;
        if (matches.some(item => item.contains(section))) return;
        matches.push(section);
    });
    if (!matches.length) {
        const empty = document.createElement('p');
        empty.setAttribute('role', 'status');
        empty.textContent = 'No settings found. Try a service name, folder, or feature.';
        results.append(empty);
    }
    matches.slice(0, 10).forEach(section => {
        const category = section.dataset.stg;
        const tab = document.querySelector('#settings-page .stg-tab[data-tab="' + category + '"]');
        const heading = section.querySelector('h3') || section.previousElementSibling?.querySelector('h3');
        const title = heading?.textContent.trim() || category;
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = (tab?.textContent.trim() || category) + ' — ' + title;
        button.addEventListener('click', () => {
            switchSettingsTab(category);
            if (section.classList.contains('settings-section-body')) {
                section.classList.remove('collapsed');
                section.style.display = '';
                const header = section.previousElementSibling;
                header?.classList.remove('collapsed');
                header?.setAttribute('aria-expanded', 'true');
            }
            const matchingLabel = Array.from(section.querySelectorAll('label, h4')).find(label => label.textContent.toLowerCase().includes(q));
            const mediaPanel = matchingLabel?.closest('[role="tabpanel"]');
            if (mediaPanel) {
                const mediaTab = document.querySelector('[aria-controls="' + mediaPanel.id + '"]');
                // the Library media cards are React: a click moves their own state
                if (mediaTab) mediaTab.click();
            }
            section.tabIndex = -1;
            section.focus({ preventScroll: true });
            section.scrollIntoView({ block: 'start', behavior: 'auto' });
            section.classList.add('stg-search-target');
            window.setTimeout(() => section.classList.remove('stg-search-target'), 2000);
        });
        results.append(button);
    });
}
window.filterSettings = filterSettings;

// #1822: what a setting does, said under it. 204 settings hid that behind an
// "i" you had to click. the first sentence of each help shows as a quiet line;
// the icon stays only where there is more to read.
function settingHelpFirstLine(text) {
    const clean = (text || '').replace(/\s+/g, ' ').trim();
    if (!clean) return { line: '', rest: false };
    const match = clean.match(/^(.+?[.!?])(\s|$)/);
    let line = match ? match[1] : clean;
    if (line.length > 140) {
        const cut = line.slice(0, 140);
        line = cut.slice(0, Math.max(cut.lastIndexOf(' '), 80)).replace(/[,;:\s]+$/, '') + '…';
    }
    return { line, rest: line.length < clean.length };
}

function initSettingHelpLines(root) {
    const scope = root || document.getElementById('settings-page');
    if (!scope) return;
    scope.querySelectorAll('.info-icon[onclick*="toggleSettingHelp"]').forEach(icon => {
        if (icon.dataset.helpLine) return;
        icon.dataset.helpLine = '1';
        // only settings rows; a service card title keeps its icon as it is
        const group = icon.closest('.form-group');
        const body = group ? _settingHelpBody(icon) : null;
        if (!body) return;
        const { line, rest } = settingHelpFirstLine(body.textContent);
        if (!line) return;
        // a full-width last row of the setting itself, so it lines up with the
        // label whatever the row's layout
        const small = document.createElement('small');
        small.className = 'stg-help-line';
        const text = document.createElement('span');
        text.className = 'stg-help-text';
        text.textContent = line;
        small.append(text);
        group.append(small);
        if (rest) {
            // the "i" sits at the end of the line and still opens the rest
            icon.__helpBody = body;
            small.append(icon);
        } else {
            icon.hidden = true;
        }
    });
}
window.initSettingHelpLines = initSettingHelpLines;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initSettingHelpLines());
} else {
    initSettingHelpLines();
}

// #1868, the naming templates' live preview: the Organization card is React
// (library-organization.tsx)

// Ctrl/Cmd+K jumps to settings search while the settings page is open
document.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || (e.key || '').toLowerCase() !== 'k') return;
    const page = document.getElementById('settings-page');
    const input = document.getElementById('stg-search-input');
    if (!page || !input || !page.offsetParent) return;
    e.preventDefault();
    input.focus();
    input.select();
});



// ── Settings → Connections: per-service status gradient + verify wiring ──
// Gradient shows green when the user has filled in credentials, yellow when empty.
// It's based purely on config presence (cheap, no API calls). The verify layer —
// which runs on expand / Expand All — surfaces whether those credentials actually
// work, via an inline warning bar inside the expanded panel.

let _stgServiceStatusState = {};  // service -> {configured: bool}
let _stgServiceVerifyInFlight = {};  // service -> true while a verify call is running

async function applyServiceStatusGradients() {
    try {
        const resp = await fetch('/api/settings/config-status');
        if (!resp.ok) return;
        const data = await resp.json();
        _stgServiceStatusState = data || {};
        document.querySelectorAll('#settings-page .stg-service[data-service]').forEach(card => {
            const service = card.getAttribute('data-service');
            const header = card.querySelector('.stg-service-header');
            if (!service || !header) return;
            const configured = !!(data[service] && data[service].configured);
            header.classList.toggle('status-configured', configured);
            header.classList.toggle('status-missing', !configured);
            // Ensure the header has a spinner placeholder for the verify-checking state
            if (!header.querySelector('.stg-service-verify-spinner')) {
                const spinner = document.createElement('span');
                spinner.className = 'stg-service-verify-spinner';
                // Insert before the chevron on the right
                const chevron = header.querySelector('.stg-service-chevron');
                if (chevron) header.insertBefore(spinner, chevron);
                else header.appendChild(spinner);
            }
        });
        syncSpotifySettingsAuthState(_lastStatusPayload?.spotify || null);
    } catch (e) {
        console.warn('[Settings Status] Failed to apply gradients:', e);
    }
}

function syncSpotifySettingsAuthState(statusData) {
    if (!statusData) return;

    const card = document.querySelector('#settings-page .stg-service[data-service="spotify"]');
    if (!card) return;

    const header = card.querySelector('.stg-service-header');
    const dot = card.querySelector('.stg-service-dot');
    if (!header && !dot) return;

    const authenticated = statusData?.authenticated === true;
    const rateLimited = !!(statusData?.rate_limited && statusData?.rate_limit);
    const cooldown = !!(statusData?.post_ban_cooldown > 0);
    const needsAttention = !authenticated || rateLimited || cooldown;

    if (header) {
        header.classList.toggle('status-configured', !needsAttention);
        header.classList.toggle('status-missing', needsAttention);
    }

    if (dot) {
        dot.style.color = needsAttention ? '#f1c40f' : '#1DB954';
    }
}

function _stgSetCheckingState(service, isChecking) {
    const card = document.querySelector(`#settings-page .stg-service[data-service="${service}"]`);
    if (!card) return;
    const header = card.querySelector('.stg-service-header');
    const body = card.querySelector('.stg-service-body');
    if (header) {
        header.classList.toggle('status-checking', !!isChecking);
        // Lazy-create the spinner element so it's there even if
        // applyServiceStatusGradients() hasn't run yet.
        if (!header.querySelector('.stg-service-verify-spinner')) {
            const spinner = document.createElement('span');
            spinner.className = 'stg-service-verify-spinner';
            const chevron = header.querySelector('.stg-service-chevron');
            if (chevron) header.insertBefore(spinner, chevron);
            else header.appendChild(spinner);
        }
    }
    if (!body) return;
    const existing = body.querySelector('.stg-service-verify-status');
    if (isChecking) {
        if (!existing) {
            const status = document.createElement('div');
            status.className = 'stg-service-verify-status';
            status.textContent = 'Testing connection…';
            body.insertBefore(status, body.firstChild);
        }
    } else if (existing) {
        existing.remove();
    }
}

function _stgShowVerifyWarning(service, message) {
    const card = document.querySelector(`#settings-page .stg-service[data-service="${service}"]`);
    if (!card) return;
    const body = card.querySelector('.stg-service-body');
    if (!body) return;
    const existing = body.querySelector('.stg-service-warning');
    if (existing) existing.remove();
    const warning = document.createElement('div');
    warning.className = 'stg-service-warning';
    warning.innerHTML = `
        <span class="stg-service-warning-icon">&#9888;</span>
        <span class="stg-service-warning-text"></span>
    `;
    warning.querySelector('.stg-service-warning-text').textContent =
        message || 'Connection test failed.';
    body.insertBefore(warning, body.firstChild);
}

function _stgClearVerifyWarning(service) {
    const card = document.querySelector(`#settings-page .stg-service[data-service="${service}"]`);
    if (!card) return;
    const existing = card.querySelector('.stg-service-warning');
    if (existing) existing.remove();
}

async function _stgRefreshAfterSave() {
    // Called after a successful settings save. Cheap gradient refresh always,
    // plus re-verify any cards the user currently has expanded (so they see
    // immediate feedback on credentials they just edited). Collapsed cards
    // keep their cached verify result until the user expands them.
    try {
        await applyServiceStatusGradients();
        const expandedServices = Array.from(
            document.querySelectorAll('#settings-page .stg-service.expanded[data-service]')
        )
            .map(card => card.getAttribute('data-service'))
            .filter(Boolean);
        if (expandedServices.length > 0) {
            _stgVerifyServices(expandedServices, { force: true });
        }
    } catch (e) {
        console.warn('[Settings Status] Post-save refresh failed:', e);
    }
}

async function _stgVerifyServices(services, { force = false } = {}) {
    if (!services || !services.length) return {};
    // Mark all as checking immediately so the user sees spinners/status lines
    services.forEach(svc => {
        _stgServiceVerifyInFlight[svc] = true;
        _stgSetCheckingState(svc, true);
        _stgClearVerifyWarning(svc);
    });
    try {
        const url = '/api/settings/verify' + (force ? '?force=true' : '');
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ services })
        });
        const data = await resp.json();
        services.forEach(svc => {
            _stgServiceVerifyInFlight[svc] = false;
            _stgSetCheckingState(svc, false);
            const result = data[svc];
            if (result && result.success === false) {
                _stgShowVerifyWarning(svc, result.error || result.message || '');
            }
        });
        return data;
    } catch (e) {
        console.warn('[Settings Verify] Network error:', e);
        services.forEach(svc => {
            _stgServiceVerifyInFlight[svc] = false;
            _stgSetCheckingState(svc, false);
            _stgShowVerifyWarning(svc, 'Unable to reach the verification endpoint.');
        });
        return {};
    }
}

function toggleStgService(el) {
    const service = el.closest('.stg-service');
    if (service) {
        const wasExpanded = service.classList.contains('expanded');
        service.classList.toggle('expanded');
        // Fire verify when expanding a single card (not on collapse). The backend
        // caches per service for 5 min, so rapid expand/collapse won't re-ping.
        if (!wasExpanded) {
            const serviceName = service.getAttribute('data-service');
            if (serviceName && !_stgServiceVerifyInFlight[serviceName]) {
                _stgVerifyServices([serviceName]);
            }
        }
    }
}
function toggleAllServiceAccordions(btn) {
    const services = document.querySelectorAll('#settings-page .stg-service');
    const allExpanded = Array.from(services).every(s => s.classList.contains('expanded'));
    const willExpand = !allExpanded;
    services.forEach(s => s.classList.toggle('expanded', willExpand));
    btn.textContent = allExpanded ? 'Expand All' : 'Collapse All';

    // On Expand All, fire a single batched verify for every service that has a
    // data-service attribute. Backend caps concurrency at 3 to avoid rate limits.
    // Skipped on Collapse All.
    if (willExpand) {
        const serviceNames = Array.from(services)
            .map(s => s.getAttribute('data-service'))
            .filter(Boolean)
            .filter(name => !_stgServiceVerifyInFlight[name]);
        if (serviceNames.length > 0) {
            _stgVerifyServices(serviceNames);
        }
    }
}


// The download chains (music, video, audiobooks), the source registry the
// chain and the Sources tiles read, each source's connection state and Test
// all sources are React: src/features/settings/tabs/sources-model.ts and
// sources-tab.tsx (SETTINGS_PORT.md step 7a). The audiobook chain is a
// settings key the chain reads and writes through the store (step 8c).

// ── Sources tab ─────────────────────────────────────────────────────────────
// The source registry, the tiles and the modal they open are React
// (src/features/settings/tabs/sources-model.ts and sources-tab.tsx). What stays
// here: the entry points older markup and other tabs call, and the panels'
// own code, until each panel is ported (SETTINGS_PORT.md step 7).

function toggleSourceCard(header) {
    // kept so an older cached page cannot throw; tiles replaced the row cards.
    if (header && header.dataset) window.openSourceModal?.(header.dataset.sourceId);
}
window.toggleSourceCard = toggleSourceCard;

// Live auth/connection checks, run when a source is opened rather than on every
// settings redraw. Best-effort: a probe that throws must not stop the modal.
const SOURCE_OPEN_PROBE = {
    // tidal checks itself when its modal opens (source-panels.tsx)
    qobuz: () => checkQobuzAuthStatus(),
    // hifi checks itself when its modal opens (source-panels.tsx)
    // soundcloud checks itself when its modal opens (source-panels.tsx)
};

function probeSourceOnOpen(srcId) {
    const probe = SOURCE_OPEN_PROBE[srcId];
    if (!probe) return;
    try { probe(); } catch (e) { /* the modal still opens */ }
}

// The modal a tile opens is React (src/features/settings/tabs/sources-tab.tsx,
// SETTINGS_PORT.md step 7b): window.openSourceModal and closeSourceModal are
// set by its host. It still MOVES the vanilla panel parked in
// #source-config-home into its body and back, never copies it, so every id
// here stays one element. probeSourceOnOpen above is what it calls on open.

// a field edited seconds before the modal closed would sit on the autosave
// timer with nothing on screen saying so: the modal lands it through this
window.landPendingSettingsSave = () => {
    if (settingsAutoSaveTimer) {
        clearTimeout(settingsAutoSaveTimer);
        settingsAutoSaveTimer = null;
        saveSettings(true);
    }
};

// src id -> the config panel that belongs to it. Only sources with a panel get
// a tile; torrent and usenet share one, and it is filed under torrent.
const SOURCE_CONFIG_ID_BY_SRC = {
    soulseek: 'soulseek-settings-container', youtube: 'youtube-settings-container',
    tidal: 'tidal-download-settings-container', qobuz: 'qobuz-settings-container',
    hifi: 'hifi-download-settings-container', deezer_dl: 'deezer-download-settings-container',
    lidarr: 'lidarr-download-settings-container',
    soundcloud: 'soundcloud-download-settings-container',
    // Usenet is its own link in the chain and shares the Prowlarr panel. It had
    // no tile at all, so a usenet user saw nothing to click.
    torrent: 'torrent-client-settings-container',
    usenet: 'usenet-client-settings-container',
    prowlarr: 'prowlarr-settings-container',
};

// The cog on a chain row. The config is on the Sources tab now, so this crosses
// to it and opens that one source rather than expanding a panel in place.
function toggleHybridSourceConfig(srcId) {
    if (typeof switchSettingsTab === 'function') switchSettingsTab('sources');
    // torrent and usenet share one panel
    window.openSourceModal?.(srcId === 'usenet' ? 'torrent' : srcId);
}

// Open one source's accordion on the Sources tab and scroll it into view.
// Collapsed state is carried on BOTH the header and the body (that is what the
// shared accordion onclick toggles), so opening one by hand has to set both or
// the next click reads as "already open" and closes nothing.
function openSourceConfig(containerId) {
    const srcId = Object.keys(SOURCE_CONFIG_ID_BY_SRC)
        .find(k => SOURCE_CONFIG_ID_BY_SRC[k] === containerId);
    if (srcId) window.openSourceModal?.(srcId);
}
window.openSourceConfig = openSourceConfig;

// Chromium-family browsers seal their cookie store with App-Bound Encryption on
// Windows, and yt-dlp cannot read it (yt-dlp issue 10927). Firefox is unaffected
// — different storage, no DPAPI.

// Mark them rather than disable them, for a reason worth writing down: a
// <select> whose SELECTED option is disabled reports value === '', so the next
// auto-save would quietly write an empty cookie source over the user's setting.
// Labelling warns everybody and destroys nobody's config.

// Say it at the moment of choosing, not when a download fails days later.

// the preferred album-art sources: React (library-processing.tsx)

function updatePlexConfigurationButtons() {
    const plexUrl = document.getElementById('plex-url');
    const plexToken = document.getElementById('plex-token');
    const hasPlexConfig = Boolean((plexUrl?.value || '').trim() || (plexToken?.value || '').trim());
    const plexViewConfigButton = document.getElementById('plex-view-config-button');
    const plexLinkToPlexButton = document.getElementById('plex-link-to-plex-button');
    const plexManualConfigButton = document.getElementById('plex-manual-config-button');
    const plexUrlActions = document.getElementById('plex-url-actions');
    const plexTokenActions = document.getElementById('plex-token-actions');
    const plexPinAuthFlow = document.getElementById('plex-pin-auth-flow');

    if (plexViewConfigButton) plexViewConfigButton.style.display = hasPlexConfig ? '' : 'none';
    if (plexLinkToPlexButton) plexLinkToPlexButton.style.display = hasPlexConfig ? 'none' : '';
    if (plexManualConfigButton) plexManualConfigButton.style.display = hasPlexConfig ? 'none' : '';
    if (plexUrlActions) plexUrlActions.style.display = hasPlexConfig ? 'none' : 'flex';
    if (plexTokenActions) plexTokenActions.style.display = hasPlexConfig ? 'none' : 'flex';
    if (plexPinAuthFlow) plexPinAuthFlow.style.display = 'none';
}

async function loadSettingsData() {
    try {
        const response = await fetch(API.settings);
        const settings = await response.json();

        // #879: a failed GET /api/settings returns an error body (e.g. {"error":
        // "..."} on a 500), NOT real settings. Populating from it blanks every
        // field to its default ('settings.spotify?.x || ""'), and the next
        // (auto)save then overwrites the user's real config. Abort BEFORE
        // touching any field and flag it so saves stay blocked until a good load.
        if (!response.ok || !settings || typeof settings !== 'object' || settings.error) {
            window._settingsLoadFailed = true;
            throw new Error('settings load failed (HTTP ' + response.status + '): ' +
                ((settings && settings.error) || 'unexpected response'));
        }
        window._settingsLoadFailed = false;  // good load → saving is safe again
        window._settingsPayload = settings;
        // the tabs ported to React read the same payload from their own store
        if (window.SoulSyncSettings) window.SoulSyncSettings.hydrate(settings);

        // Populate Spotify settings
        document.getElementById('spotify-client-id').value = settings.spotify?.client_id || '';
        document.getElementById('spotify-client-secret').value = settings.spotify?.client_secret || '';
        document.getElementById('spotify-redirect-uri').value = settings.spotify?.redirect_uri || 'http://127.0.0.1:8888/callback';
        document.getElementById('spotify-callback-display').textContent = settings.spotify?.redirect_uri || 'http://127.0.0.1:8888/callback';

        // Populate Tidal settings
        document.getElementById('tidal-client-id').value = settings.tidal?.client_id || '';
        document.getElementById('tidal-client-secret').value = settings.tidal?.client_secret || '';
        document.getElementById('tidal-redirect-uri').value = settings.tidal?.redirect_uri || 'http://127.0.0.1:8889/tidal/callback';
        document.getElementById('tidal-callback-display').textContent = settings.tidal?.redirect_uri || 'http://127.0.0.1:8889/tidal/callback';

        // Populate Deezer OAuth settings
        document.getElementById('deezer-app-id').value = settings.deezer?.app_id || '';
        document.getElementById('deezer-app-secret').value = settings.deezer?.app_secret || '';
        document.getElementById('deezer-redirect-uri').value = settings.deezer?.redirect_uri || 'http://127.0.0.1:8008/deezer/callback';
        document.getElementById('deezer-callback-display').textContent = settings.deezer?.redirect_uri || 'http://127.0.0.1:8008/deezer/callback';

        // Add event listeners to update display URLs when input changes
        document.getElementById('spotify-redirect-uri').addEventListener('input', function () {
            document.getElementById('spotify-callback-display').textContent = this.value || 'http://127.0.0.1:8888/callback';
        });

        document.getElementById('tidal-redirect-uri').addEventListener('input', function () {
            document.getElementById('tidal-callback-display').textContent = this.value || 'http://127.0.0.1:8889/tidal/callback';
        });

        document.getElementById('deezer-redirect-uri').addEventListener('input', function () {
            document.getElementById('deezer-callback-display').textContent = this.value || 'http://127.0.0.1:8008/deezer/callback';
        });

        // Populate Plex settings
        const plexUrlInput = document.getElementById('plex-url');
        const plexTokenInput = document.getElementById('plex-token');
        if (plexUrlInput) plexUrlInput.value = settings.plex?.base_url || '';
        if (plexTokenInput) plexTokenInput.value = settings.plex?.token || '';
        if (plexUrlInput) plexUrlInput.addEventListener('input', updatePlexConfigurationButtons);
        if (plexTokenInput) plexTokenInput.addEventListener('input', updatePlexConfigurationButtons);
        updatePlexConfigurationButtons();

        // Populate Jellyfin settings
        document.getElementById('jellyfin-url').value = settings.jellyfin?.base_url || '';
        document.getElementById('jellyfin-api-key').value = settings.jellyfin?.api_key || '';
        document.getElementById('jellyfin-timeout').value = settings.jellyfin?.api_timeout || 120;

        // Populate Navidrome settings
        document.getElementById('navidrome-url').value = settings.navidrome?.base_url || '';
        document.getElementById('navidrome-username').value = settings.navidrome?.username || '';
        document.getElementById('navidrome-password').value = settings.navidrome?.password || '';
        loadNavidromePlaylistLogin();
        loadMediaServerNames();

        // Populate Music Assistant settings
        const maUrl = document.getElementById('music_assistant-url');
        const maToken = document.getElementById('music_assistant-token');
        if (maUrl) maUrl.value = settings.music_assistant?.base_url || '';
        if (maToken) maToken.value = settings.music_assistant?.token || '';
        const maFolder = document.getElementById('music_assistant-source-folder');
        if (maFolder) maFolder.value = settings.music_assistant?.source_folder || '';
        if (maUrl && maUrl.value && maToken && maToken.value) loadMusicAssistantSources(true);

        // Set active server and toggle visibility
        const activeServer = settings.active_media_server || 'plex';
        // Remember the persisted music server so a save from the VIDEO side keeps
        // it unchanged (the toggle there is for opening a config panel, not picking
        // the music server).
        window._persistedActiveServer = activeServer;
        toggleServer(activeServer);

        // Load Plex music libraries if Plex is the active server
        if (activeServer === 'plex') {
            loadPlexMusicLibraries();
        }

        // Load Jellyfin users and music libraries if Jellyfin is the active server
        if (activeServer === 'jellyfin') {
            loadJellyfinUsers().then(() => loadJellyfinMusicLibraries());
        }

        // Load Navidrome music folders if Navidrome is the active server
        if (activeServer === 'navidrome') {
            loadNavidromeMusicFolders();
        }

        // Populate Soulseek settings
        // the rest of soulseek.*: its Sources panel is React (source-panels.tsx)

        // Populate ListenBrainz settings
        document.getElementById('listenbrainz-base-url').value = settings.listenbrainz?.base_url || '';
        document.getElementById('listenbrainz-token').value = settings.listenbrainz?.token || '';
        const _lbUser = document.getElementById('listenbrainz-username');
        if (_lbUser) _lbUser.value = settings.listenbrainz?.username || '';

        // Populate AcoustID settings
        document.getElementById('acoustid-api-key').value = settings.acoustid?.api_key || '';
        document.getElementById('acoustid-enabled').checked = settings.acoustid?.enabled || false;
        // require_verified: the Quality tab is React (it is a quality profile setting)

        // Populate Last.fm settings
        const _tmKey = document.getElementById('concerts-ticketmaster-api-key');
        if (_tmKey) _tmKey.value = settings.concerts?.ticketmaster_api_key || '';
        const _slfmKey = document.getElementById('concerts-setlistfm-api-key');
        if (_slfmKey) _slfmKey.value = settings.concerts?.setlistfm_api_key || '';
        const _concertCountry = document.getElementById('concerts-country');
        if (_concertCountry) _concertCountry.value = settings.concerts?.country || '';
        document.getElementById('lastfm-api-key').value = settings.lastfm?.api_key || '';
        document.getElementById('lastfm-api-secret').value = settings.lastfm?.api_secret || '';
        const _lfmUser = document.getElementById('lastfm-username');
        if (_lfmUser) _lfmUser.value = settings.lastfm?.username || '';
        document.getElementById('lastfm-scrobble-enabled').checked = settings.lastfm?.scrobble_enabled === true;
        const lfmStatus = document.getElementById('lastfm-scrobble-status');
        if (lfmStatus) {
            lfmStatus.textContent = settings.lastfm?.session_key ? 'Authorized' : 'Not authorized';
        }

        // Populate ListenBrainz scrobble toggle
        document.getElementById('listenbrainz-scrobble-enabled').checked = settings.listenbrainz?.scrobble_enabled === true;

        // Populate Genius settings
        document.getElementById('genius-access-token').value = settings.genius?.access_token || '';
        const _pollinationsKey = document.getElementById('pollinations-api-key');
        if (_pollinationsKey) _pollinationsKey.value = settings.pollinations?.api_key || '';
        // the music card's own id: the video side's fanart.tv card owns 'fanart-api-key'
        const _fanartKey = document.getElementById('artist-fanart-api-key');
        if (_fanartKey) _fanartKey.value = settings.fanart?.api_key || '';

        // Populate iTunes settings
        document.getElementById('itunes-country').value = settings.itunes?.country || 'US';

        // Populate Discogs settings
        document.getElementById('discogs-token').value = settings.discogs?.token || '';

        // Populate Metadata source setting. 'Spotify Free' is stored as
        // fallback_source='spotify' + spotify_free=true (so all downstream
        // 'spotify' routing is unchanged) — map it back to the dropdown value.
        const _fbSrc = settings.metadata?.fallback_source || 'deezer';
        const _metaSel = (_fbSrc === 'spotify' && settings.metadata?.spotify_free === true)
            ? 'spotify_free' : _fbSrc;
        document.getElementById('metadata-fallback-source').value = _metaSel;
        const _efEl = document.getElementById('metadata-spotify-free-enrichment');
        // Default ON: unset (undefined) reads as enabled, matching the worker's
        // config default (metadata.spotify_free_enrichment defaults True).
        if (_efEl) _efEl.checked = settings.metadata?.spotify_free_enrichment !== false;

        const _jiosaavnExp = document.getElementById('experimental-jiosaavn-enabled');
        const jiosaavnEnabled = settings.experimental?.jiosaavn_enabled === true;
        if (_jiosaavnExp) _jiosaavnExp.checked = jiosaavnEnabled;
        syncJiosaavnMetadataSourceOption(jiosaavnEnabled);
        syncJiosaavnEnrichmentBubble(jiosaavnEnabled);
        if (jiosaavnEnabled && _metaSel === 'jiosaavn') {
            document.getElementById('metadata-fallback-source').value = 'jiosaavn';
        }

        const _bandcampExp = document.getElementById('experimental-bandcamp-enabled');
        if (_bandcampExp) _bandcampExp.checked = settings.experimental?.bandcamp_enabled === true;

        // Populate Hydrabase settings
        const hbConfig = settings.hydrabase || {};
        document.getElementById('hydrabase-url').value = hbConfig.url || '';
        document.getElementById('hydrabase-api-key').value = hbConfig.api_key || '';
        document.getElementById('hydrabase-auto-connect').checked = hbConfig.auto_connect || false;
        // Check live connection status + add Hydrabase to fallback dropdown if connected
        fetch('/api/hydrabase/status').then(r => r.json()).then(s => {
            const btn = document.getElementById('hydrabase-connect-btn');
            const statusEl = document.getElementById('hydrabase-settings-status');
            if (s.connected) {
                if (btn) btn.textContent = 'Disconnect';
                if (statusEl) { statusEl.textContent = 'Connected'; statusEl.style.color = '#4caf50'; }
                // Add Hydrabase to fallback source dropdown
                const fbSelect = document.getElementById('metadata-fallback-source');
                if (fbSelect && !fbSelect.querySelector('option[value="hydrabase"]')) {
                    const opt = document.createElement('option');
                    opt.value = 'hydrabase';
                    opt.textContent = 'Hydrabase (P2P)';
                    fbSelect.appendChild(opt);
                }
                // Restore selection if it was hydrabase
                if ((settings.metadata?.fallback_source) === 'hydrabase') {
                    fbSelect.value = 'hydrabase';
                }
            }
        }).catch(() => { });

        // every Library folder, the spoken-word artists included: the Folders
        // card is React (library-folders.tsx, library-audiobooks.tsx)

        // the download chain (mode and order): React, loaded in
        // SoulSyncSettings.hydrate above (sources-model.ts)
        // Per-source download quality is now derived from the global Quality
        // Profile (ranked targets) — the per-source quality selects were removed.
        // the HiFi instance list: its React panel reads it with the settings
        // deezer_download and lidarr_download: their Sources panels are React
        // prowlarr, torrent_client, usenet_client and their download_source
        // keys: their Sources panels are React (source-panels-more.tsx)
        // The Connections tab's Deezer card holds the same ARL as the Deezer
        // source panel (React, source-panels.tsx). Typing here writes the
        // panel's key; the panel writes this field back.
        const _connArl = document.getElementById('deezer-connection-arl');
        if (_connArl) _connArl.value = settings.deezer_download?.arl || '';
        if (_connArl && _connArl.dataset.arlMirror !== '1') {
            _connArl.dataset.arlMirror = '1';
            _connArl.addEventListener('input', () => {
                window.SoulSyncSettings?.set('deezer_download', 'arl', _connArl.value);
            });
        }

        // youtube.* and download_source.youtube_album_bundle: the YouTube source
        // panel is React (source-panels-more.tsx)

        // Populate Database settings
        // max_workers: the System tab is React (system-tab.tsx)

        // Post-Processing: React (library-processing.tsx)
        loadMusicBrainzServerSettings(settings);
        // File Organization and every template: the Organization card is React
        // (library-organization.tsx, library-audiobooks.tsx)
        // the audiobook settings: the Organization card is React (library-audiobooks.tsx)
        // the naming options, tag options and wishlist rules: the Organization card is React
        const _lcOn = document.getElementById('lidarr-compat-enabled');
        if (_lcOn) _lcOn.checked = settings.lidarr_compat?.enabled === true;
        loadLidarrCompatProfiles(settings.lidarr_compat?.profile_id ?? 1);
        const _mcpOn = document.getElementById('mcp-enabled');
        if (_mcpOn) _mcpOn.checked = settings.mcp?.enabled === true;

        // Playlist Sync is on the Discovery tab, which is React (discovery-tab.tsx)

        // Post-Download Conversion: the Quality tab is React

        // Album Publishing (#999): the Downloads tab is React

        // Listening Stats and Discovery are on the Discovery tab, which is React

        // the extra music libraries and Sample Studio folders: React (library-folders.tsx)


        // Library Organize and re-download deleted files: the Organization card is React

        // Filtering (explicit content, preferred version, size cap, genre
        // whitelist): the Downloads tab is React

        // Import: React (library-processing.tsx)

        // Populate M3U Export settings (the export and whole-library switches
        // and folder are on the Discovery tab, which is React)
        // relative paths, base path and prefix rewrite: the Folders card is React

        // UI Appearance is React now: opening Settings applies the saved accent,
        // visualizer and effect switches from src/features/settings/tabs/appearance.ts
        // (applyAppearanceOnLoad, called by SoulSyncSettings.hydrate above)

        // autosave switch, on unless switched off. the form was just reloaded,
        // so an unsaved mark left over from a manual-save visit is stale
        // (the switch itself is React, system-tab.tsx; the flag is the page's)
        const autosaveOn = settings.ui_appearance?.settings_autosave !== false;
        window._settingsAutosaveOff = !autosaveOn;
        if (document.getElementById('save-settings')?.dataset.state === 'pending' && !settingsAutoSaveTimer) {
            setSettingsSaveState('idle');
        }

        // Logging: the System tab is React (system-tab.tsx)

        // Load security settings
        try {
            const requirePin = settings.security?.require_pin_on_launch || false;
            document.getElementById('security-require-pin').checked = requirePin;

            // CORS origins — stored verbatim as the user typed (string).
            const corsOrigins = settings.security?.cors_origins || '';
            const corsField = document.getElementById('security-cors-origins');
            if (corsField) corsField.value = corsOrigins;

            // Reverse-proxy mode + auth-proxy header (default off / empty).
            const trustProxy = document.getElementById('security-trust-proxy');
            if (trustProxy) trustProxy.checked = settings.security?.trust_reverse_proxy || false;
            const authHeader = document.getElementById('security-auth-proxy-header');
            if (authHeader) authHeader.value = settings.security?.auth_proxy_header || '';
            const reqLogin = document.getElementById('security-require-login');
            if (reqLogin) reqLogin.checked = settings.security?.require_login || false;
            const plexSignin = document.getElementById('security-plex-signin');
            if (plexSignin) plexSignin.checked = settings.security?.plex_signin || false;
            const plexCreate = document.getElementById('security-plex-signin-auto-create');
            if (plexCreate) plexCreate.checked = settings.security?.plex_signin_auto_create !== false;
            const plexDl = document.getElementById('security-plex-signin-can-download');
            if (plexDl) plexDl.checked = settings.security?.plex_signin_default_can_download || false;
            const oidcOn = document.getElementById('security-oidc-signin');
            if (oidcOn) oidcOn.checked = settings.security?.oidc_signin || false;
            const oidcOnly = document.getElementById('security-oidc-only');
            if (oidcOnly) oidcOnly.checked = settings.security?.oidc_only || false;
            syncOidcProviderBlock();
            for (const [id, key] of [
                ['security-oidc-issuer', 'oidc_issuer'],
                ['security-oidc-client-id', 'oidc_client_id'],
                ['security-oidc-client-secret', 'oidc_client_secret'],
                ['security-oidc-button-label', 'oidc_button_label'],
                ['security-oidc-icon-url', 'oidc_icon_url'],
                ['security-oidc-required-group', 'oidc_required_group'],
                ['security-oidc-groups-claim', 'oidc_groups_claim'],
                ['security-oidc-scopes', 'oidc_scopes'],
                ['security-oidc-redirect-uri', 'oidc_redirect_uri'],
            ]) {
                const el = document.getElementById(id);
                if (el) el.value = settings.security?.[key] || '';
            }
            const oidcCreate = document.getElementById('security-oidc-signin-auto-create');
            if (oidcCreate) oidcCreate.checked = settings.security?.oidc_signin_auto_create !== false;
            const oidcDl = document.getElementById('security-oidc-signin-can-download');
            if (oidcDl) oidcDl.checked = settings.security?.oidc_signin_default_can_download || false;
            // the address to register at the provider, as this browser sees it
            const oidcCb = document.getElementById('security-oidc-callback-url');
            if (oidcCb) {
                // the browser's own address first, then what the server will
                // really send the provider. behind a proxy that hides https
                // they differ, and only the server's one matters (RileyRabbit).
                oidcCb.textContent = window.location.origin + window.SoulSyncURL.resolve('/api/auth/oidc/callback');
                const oidcWarn = document.getElementById('security-oidc-callback-warn');
                fetch('/api/auth/oidc/available').then(r => r.ok ? r.json() : null).then(d => {
                    if (!d || !d.callback_url) return;
                    oidcCb.textContent = d.callback_url;
                    if (!oidcWarn) return;
                    const sent = d.callback_url.split(':')[0];
                    const page = window.location.protocol.replace(':', '');
                    oidcWarn.hidden = sent === page;
                }).catch(() => { /* keep the browser's guess */ });
            }
            const signupSides = document.getElementById('security-signup-sides');
            if (signupSides) {
                const sides = settings.security?.signup_sides;
                signupSides.value = ['music', 'video', 'both'].includes(sides) ? sides : 'music';
            }
            const signupLimit = document.getElementById('security-signup-request-limit');
            if (signupLimit) signupLimit.value = settings.security?.signup_request_limit ?? 0;
            const signupDays = document.getElementById('security-signup-request-limit-days');
            if (signupDays) signupDays.value = settings.security?.signup_request_limit_days ?? 7;

            // Check if admin has a PIN set
            const profilesRes = await fetch('/api/profiles');
            const profilesData = await profilesRes.json();
            const adminProfile = (profilesData.profiles || []).find(p => p.is_admin);
            const adminHasPin = adminProfile?.has_pin || false;

            // Show/hide PIN setup vs change sections
            document.getElementById('security-pin-setup').style.display = adminHasPin ? 'none' : 'block';
            document.getElementById('security-change-pin-section').style.display = adminHasPin ? 'block' : 'none';

            // If no PIN, disable the toggle
            if (!adminHasPin) {
                document.getElementById('security-require-pin').checked = false;
                document.getElementById('security-require-pin').disabled = true;
            }

            // Login: the "Require login" toggle is gated on an admin password —
            // visually locked until Step 1 is done (anti-lockout, made obvious).
            updateRequireLoginGate(adminProfile?.has_password || false);
            // Show already-saved password/recovery state (vs looking unset).
            applyLoginSavedState(adminProfile);
        } catch (error) {
            console.error('Error loading security settings:', error);
        }

        // Check dev mode status
        try {
            const devResponse = await fetch('/api/dev-mode');
            const devData = await devResponse.json();
            if (devData.enabled) {
                // Re-broadcast for the React dashboard's Hydrabase orb.
                window.dispatchEvent(new CustomEvent('ss:dev-mode', { detail: { enabled: true } }));
                document.getElementById('dev-mode-status').textContent = 'Active';
                document.getElementById('dev-mode-status').style.color = 'rgb(var(--accent-light-rgb))';
                document.getElementById('hydrabase-nav').style.display = '';
                // The Hydrabase ORB is React-rendered since the dashboard flip
                // and shows on the dispatch above; the old container write
                // would throw here (the node only exists while the dashboard
                // is mounted).
            }
        } catch (error) {
            console.error('Error checking dev mode:', error);
        }

        // Secret fields now arrive masked as REDACTED_SECRET_SENTINEL (#832
        // follow-up) — wire them so editing replaces the mask instead of typing
        // on top of it, and an untouched field re-masks on blur (round-trips the
        // sentinel, which the server treats as "keep existing").
        _wireRedactedSecrets();

        // the library headers report live values, so they are only meaningful
        // once the form actually holds them. deliberately inside the try: if the
        // load failed there is nothing true to summarise, and the headers keep
        // their static descriptions rather than reporting an empty form as
        // "nothing enabled".
        if (typeof refreshLibrarySummaries === 'function') refreshLibrarySummaries();
        // the service tiles report which credentials are actually filled in, so
        // they are only truthful once the form holds them. same reason as above:
        // summarising an empty form would report every service as "not set up".
        if (typeof buildServiceTiles === 'function') buildServiceTiles();

    } catch (error) {
        console.error('Error loading settings:', error);
        // #879: any load failure → block saves so a blank/partial form can't be
        // written over the real config. Cleared on the next successful load.
        window._settingsLoadFailed = true;
        showToast('Failed to load settings — reload the page before saving (your saved config is untouched)', 'error');
    }
}

// Mirrors ConfigManager.REDACTED_SENTINEL — secrets are never sent to the
// browser; configured ones come back as this placeholder (rendered as dots in
// the password inputs).
const REDACTED_SECRET_SENTINEL = '__redacted_unchanged__';

function _wireRedactedSecrets() {
    document.querySelectorAll('input[type="password"]').forEach(el => {
        if (el.dataset.redactWired === '1') return;
        // a React field does this itself (SecretInput in tabs/parts.tsx):
        // writing its value from here would fight React for it
        if (el.closest('[data-stg-react]')
            && !el.closest('.src-config-panel:not([data-react-panel])')) return;
        el.dataset.redactWired = '1';
        // Clear the mask on focus so the user types a fresh value, not on top
        // of the sentinel.
        el.addEventListener('focus', () => {
            if (el.value === REDACTED_SECRET_SENTINEL) {
                el.value = '';
                el.dataset.wasRedacted = '1';
            }
        });
        // Untouched (focused but not edited, left empty) → restore the mask so
        // save round-trips the sentinel and the real secret is kept.
        el.addEventListener('blur', () => {
            if (el.dataset.wasRedacted === '1' && el.value === '') {
                el.value = REDACTED_SECRET_SENTINEL;
                el.dataset.wasRedacted = '';
            }
        });
        // Real typing means a genuine change/clear — drop the redacted mark so
        // blur won't re-mask (an emptied field then saves as a real clear).
        el.addEventListener('input', () => { el.dataset.wasRedacted = ''; });
    });
}

// changeLogLevel: the System tab is React (system-tab.tsx)

function updateMediaServerFields() {
    const serverType = document.getElementById('media-server-type').value;
    const urlInput = document.getElementById('media-server-url');
    const tokenInput = document.getElementById('media-server-token');

    if (serverType === 'plex') {
        urlInput.placeholder = 'http://localhost:32400';
        tokenInput.placeholder = 'Plex Token';
    } else {
        urlInput.placeholder = 'http://localhost:8096';
        tokenInput.placeholder = 'Jellyfin API Key';
    }
}

let _plexPinAuthRequestId = null;
let _plexPinAuthPollInterval = null;

function showPlexConfiguration(disableFields = false, isManualConfig = false) {
    stopPlexPinAuthPolling();
    const plexConfig = document.getElementById('plex-configuration');
    const plexSetup = document.getElementById('plex-setup');
    const plexPinAuthFlow = document.getElementById('plex-pin-auth-flow');
    const plexUrl = document.getElementById('plex-url');
    const plexToken = document.getElementById('plex-token');
    const plexLibraryContainer = document.getElementById('plex-library-selector-container');

    if (plexConfig) plexConfig.style.display = '';
    if (plexSetup) plexSetup.style.display = 'none';
    if (plexPinAuthFlow) plexPinAuthFlow.style.display = 'none';
    if (plexUrl) plexUrl.disabled = disableFields;
    if (plexToken) plexToken.disabled = disableFields;
    if (plexLibraryContainer && isManualConfig) {
        plexLibraryContainer.style.display = 'none';
    }
    setPlexConfigActionButton(isManualConfig);
    updatePlexConfigurationButtons();
}

function showPlexSetup() {
    const plexConfig = document.getElementById('plex-configuration');
    const plexSetup = document.getElementById('plex-setup');
    const plexPinAuthFlow = document.getElementById('plex-pin-auth-flow');
    const plexLibraryContainer = document.getElementById('plex-library-selector-container');

    if (plexConfig) plexConfig.style.display = 'none';
    if (plexSetup) plexSetup.style.display = '';
    if (plexPinAuthFlow) plexPinAuthFlow.style.display = 'none';
    if (plexLibraryContainer) plexLibraryContainer.style.display = 'none';
    setPlexConfigActionButton(false);
}

function setPlexConfigActionButton(isManualConfig) {
    const actionButton = document.getElementById('plex-config-action-button');
    if (!actionButton) return;

    if (isManualConfig) {
        actionButton.textContent = 'Cancel';
        actionButton.onclick = showPlexSetup;
        actionButton.title = 'Cancel manual Plex configuration';
    } else {
        actionButton.textContent = 'Clear Configuration';
        actionButton.onclick = clearPlexConfiguration;
        actionButton.title = 'Clear saved Plex configuration';
    }
}

// Re-link: Plex is already configured and the user wants a fresh token
// through plex.tv/link without clearing anything. The PIN box lives in the
// setup area (hidden once configured), so re-link shows that area on its own.
let _plexPinRelink = false;

function startPlexRelink() {
    _plexPinRelink = true;
    const plexSetup = document.getElementById('plex-setup');
    if (plexSetup) plexSetup.style.display = '';
    startPlexPinAuth();
}

async function startPlexPinAuth() {
    const setupButtons = document.getElementById('plex-setup-buttons');
    const authFlow = document.getElementById('plex-pin-auth-flow');
    const statusEl = document.getElementById('plex-pin-status');
    if (setupButtons) setupButtons.style.display = 'none';
    if (authFlow) authFlow.style.display = '';
    if (statusEl) statusEl.textContent = 'Starting Plex authorization...';

    try {
        showLoadingOverlay('Starting Plex authorization...');
        const response = await fetch('/api/plex/pin/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const result = await response.json();
        if (!result.success) {
            throw new Error(result.error || 'Failed to start Plex PIN flow');
        }

        _plexPinAuthRequestId = result.request_id;
        const pinCodeEl = document.getElementById('plex-pin-code');
        if (pinCodeEl) pinCodeEl.textContent = result.code || '';
        if (statusEl) {
            statusEl.textContent = result.expires_in
                ? `Enter this code at plex.tv/link. Code expires in ${result.expires_in} seconds.`
                : 'Enter this code at plex.tv/link. Waiting for authorization...';
        }

        startPlexPinAuthPolling();
    } catch (error) {
        console.error('Plex PIN auth start failed:', error);
        showToast(error.message || 'Failed to start Plex authorization', 'error');
        cancelPlexPinAuth();
    } finally {
        hideLoadingOverlay();
    }
}

function startPlexPinAuthPolling() {
    stopPlexPinAuthPolling();
    if (!_plexPinAuthRequestId) return;
    _plexPinAuthPollInterval = setInterval(pollPlexPinAuthStatus, 5000);
    pollPlexPinAuthStatus();
}

function stopPlexPinAuthPolling() {
    if (_plexPinAuthPollInterval) {
        clearInterval(_plexPinAuthPollInterval);
        _plexPinAuthPollInterval = null;
    }
}

async function pollPlexPinAuthStatus() {
    if (!_plexPinAuthRequestId) return;
    try {
        const response = await fetch(`/api/plex/pin/status?request_id=${encodeURIComponent(_plexPinAuthRequestId)}`);
        const result = await response.json();
        const statusEl = document.getElementById('plex-pin-status');

        if (!result.success && result.expired) {
            if (statusEl) statusEl.textContent = 'PIN code expired. Generate a new code to continue.';
            stopPlexPinAuthPolling();
            return;
        }

        if (result.success) {
            stopPlexPinAuthPolling();
            const urlEl = document.getElementById('plex-url');
            const currentUrl = (urlEl?.value || '').trim();
            if (_plexPinRelink && currentUrl) {
                // Re-link keeps the URL that already works (a docker host or a
                // reverse proxy Plex can't discover) and only saves the new
                // token once it's proven to reach that server.
                if (statusEl) statusEl.textContent = 'Authorized. Checking the new token against your server...';
                const check = await fetch('/api/plex/verify-token', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: currentUrl, token: result.token || '' })
                }).then((r) => r.json()).catch(() => ({ success: false }));
                if (!check.success) {
                    if (statusEl) statusEl.textContent = (check.error || "The new token couldn't reach your server")
                        + '. Nothing was changed.';
                    showToast("Re-link didn't change anything: that Plex account can't reach your server", 'error');
                    return;
                }
                document.getElementById('plex-token').value = result.token || '';
            } else {
                if (statusEl) statusEl.textContent = 'Authorization complete! Saving Plex configuration...';
                if (urlEl) urlEl.value = result.found_url || '';
                document.getElementById('plex-token').value = result.token || '';
            }
            _plexPinRelink = false;
            if (typeof saveSettings === 'function') {
                await saveSettings(true);
            }
            showToast('Plex successfully linked', 'success');
            showPlexConfiguration(true);
            await testConnection('plex');
            return;
        }

        if (result.status) {
            if (statusEl) statusEl.textContent = result.status;
            return;
        }

        if (result.error) {
            if (statusEl) statusEl.textContent = result.error;
            return;
        }
    } catch (error) {
        console.error('Error polling Plex PIN status:', error);
        const statusEl = document.getElementById('plex-pin-status');
        if (statusEl) statusEl.textContent = 'Unable to contact Plex auth status. Retrying...';
    }
}

function cancelPlexPinAuth() {
    stopPlexPinAuthPolling();
    _plexPinAuthRequestId = null;
    const setupButtons = document.getElementById('plex-setup-buttons');
    const authFlow = document.getElementById('plex-pin-auth-flow');
    if (setupButtons) setupButtons.style.display = '';
    if (authFlow) authFlow.style.display = 'none';
    if (_plexPinRelink) {
        // back to the configured view, nothing changed
        _plexPinRelink = false;
        const plexSetup = document.getElementById('plex-setup');
        if (plexSetup) plexSetup.style.display = 'none';
    }
}

function restartPlexPinAuth() {
    const relink = _plexPinRelink;
    cancelPlexPinAuth();
    if (relink) startPlexRelink();
    else startPlexPinAuth();
}

async function clearPlexConfiguration() {
    cancelPlexPinAuth();
    const plexUrl = document.getElementById('plex-url');
    const plexToken = document.getElementById('plex-token');
    const plexConfig = document.getElementById('plex-configuration');
    const plexSetup = document.getElementById('plex-setup');
    const plexSetupButtons = document.getElementById('plex-setup-buttons');
    const plexViewConfigButton = document.getElementById('plex-view-config-button');
    const plexLinkToPlexButton = document.getElementById('plex-link-to-plex-button');
    const plexManualConfigButton = document.getElementById('plex-manual-config-button');

    if (plexUrl) plexUrl.value = '';
    if (plexToken) plexToken.value = '';
    if (plexConfig) plexConfig.style.display = 'none';
    if (plexSetup) plexSetup.style.display = '';
    if (plexSetupButtons) plexSetupButtons.style.display = '';
    if (plexViewConfigButton) plexViewConfigButton.style.display = 'none';
    if (plexLinkToPlexButton) plexLinkToPlexButton.style.display = '';
    if (plexManualConfigButton) plexManualConfigButton.style.display = '';

    const plexLibraryContainer = document.getElementById('plex-library-selector-container');
    const plexLibrarySelect = document.getElementById('plex-music-library');
    if (plexLibrarySelect) {
        plexLibrarySelect.innerHTML = '<option value="">Select a music library</option>';
    }
    if (plexLibraryContainer) {
        plexLibraryContainer.style.display = 'none';
    }

    updatePlexConfigurationButtons();

    try {
        await fetch('/api/plex/clear-library', { method: 'POST' });
    } catch (e) {
        console.warn('Failed to clear Plex library preference:', e);
    }

    if (typeof saveSettings === 'function') {
        saveSettings(true);
    }
    if (typeof showToast === 'function') {
        showToast('Plex configuration cleared', 'success');
    }
}

// The media server names, in UI order. Refreshed from GET /api/media-servers
// (the per-server descriptor) when the settings page loads; this list is
// what the page knew before the endpoint existed and stays the fallback.
let _mediaServerNames = ['plex', 'jellyfin', 'navidrome', 'music_assistant', 'soulsync'];

async function loadMediaServerNames() {
    try {
        const res = await fetch('/api/media-servers');
        if (!res.ok) return;
        const names = ((await res.json())?.servers || []).map(s => s?.name).filter(Boolean);
        if (names.length) _mediaServerNames = names;
    } catch (e) {
        // fallback list stays
    }
}

// The server whose toggle is active, else the first one.
function activeServerFromToggles() {
    const hit = _mediaServerNames.find(name => document.getElementById(`${name}-toggle`)?.classList.contains('active'));
    return hit || _mediaServerNames[0];
}

function toggleServer(serverType) {
    // Update toggle buttons and show the matching config container. A
    // server without a toggle or a container on this page (the standalone
    // library on the video side) is simply skipped.
    for (const name of _mediaServerNames) {
        document.getElementById(`${name}-toggle`)?.classList.toggle('active', name === serverType);
        document.getElementById(`${name}-container`)?.classList.toggle('hidden', name !== serverType);
    }

    // Show Plex setup when Plex is selected; otherwise hide both Plex panels
    const plexConfig = document.getElementById('plex-configuration');
    const plexSetup = document.getElementById('plex-setup');
    if (plexConfig) plexConfig.style.display = serverType === 'plex' ? 'none' : '';
    if (plexSetup) plexSetup.style.display = serverType === 'plex' ? '' : 'none';

    // Load Plex music libraries when switching to Plex
    if (serverType === 'plex') {
        loadPlexMusicLibraries();
    }

    // Load Jellyfin users and music libraries when switching to Jellyfin
    if (serverType === 'jellyfin') {
        loadJellyfinUsers().then(() => loadJellyfinMusicLibraries());
    }

    // Load Navidrome music folders when switching to Navidrome
    if (serverType === 'navidrome') {
        loadNavidromeMusicFolders();
    }

    // Auto-save after server toggle change
    debouncedAutoSaveSettings();
}

// The quality profile functions (the Music Quality tile, the ranked target
// list, the presets, the Quality Profiles panel and its Manage overview) are
// React now: src/features/settings/tabs/quality-tab.tsx (SETTINGS_PORT.md, step 6).
// The help toggle below stayed: every vanilla tab still uses it.

// Toggle the collapsible help text below a setting's ⓘ icon. Walks forward from
// the icon's row to the next .setting-help-body sibling, so it works whether the
// body is the immediate next element or sits after a control (e.g. a <select>),
// and regardless of any wrapping container.
function _settingHelpBody(iconEl) {
    // Locate the help body an info icon belongs to. Search order:
    //  1) the next .setting-help-body sibling after the icon's row (icon + body
    //     both inside the same .form-group / .setting-row),
    //  2) the element right after the enclosing .form-group (help wall that sits
    //     as a sibling just below the group — the common always-visible case).
    // An icon moved into its one-line summary remembers its body (#1822).
    if (iconEl.__helpBody) return iconEl.__helpBody;
    const row = iconEl.closest('.setting-row') || iconEl;
    let el = row.nextElementSibling;
    while (el && !el.classList.contains('setting-help-body')) {
        el = el.nextElementSibling;
    }
    if (!el) {
        const fg = iconEl.closest('.form-group');
        let sib = fg ? fg.nextElementSibling : null;
        // Only accept an immediately-following help body — never reach across
        // into the next setting/group.
        if (sib && sib.classList.contains('setting-help-body')) el = sib;
    }
    return el || null;
}

function toggleSettingHelp(iconEl) {
    const row = iconEl.closest('.setting-row') || iconEl;
    const el = _settingHelpBody(iconEl);
    if (el) {
        el.hidden = !el.hidden;
        // Reflect open state on the icon itself (filled badge) so it's clear
        // which help panel is currently revealed.
        const icon = iconEl.classList.contains('info-icon') ? iconEl : row.querySelector('.info-icon');
        if (icon) icon.classList.toggle('open', !el.hidden);
    }
}

async function toggleHydrabaseFromSettings() {
    const statusEl = document.getElementById('hydrabase-settings-status');
    const btn = document.getElementById('hydrabase-connect-btn');
    const url = document.getElementById('hydrabase-url').value.trim();
    const apiKey = document.getElementById('hydrabase-api-key').value.trim();

    if (!url || !apiKey) {
        if (statusEl) statusEl.textContent = 'URL and API Key required';
        return;
    }

    // Save settings first
    await saveSettings(true);

    try {
        // Check current status
        const statusRes = await fetch('/api/hydrabase/status');
        const statusData = await statusRes.json();

        if (statusData.connected) {
            // Disconnect
            await fetch('/api/hydrabase/disconnect', { method: 'POST' });
            if (btn) btn.textContent = 'Connect';
            if (statusEl) { statusEl.textContent = 'Disconnected'; statusEl.style.color = 'rgba(255,255,255,0.4)'; }
            // Remove from fallback dropdown + reset to iTunes if was selected
            const fbSel2 = document.getElementById('metadata-fallback-source');
            if (fbSel2) {
                const hbOpt = fbSel2.querySelector('option[value="hydrabase"]');
                if (hbOpt) {
                    if (fbSel2.value === 'hydrabase') fbSel2.value = 'itunes';
                    hbOpt.remove();
                }
            }
            showToast('Hydrabase disconnected', 'info');
        } else {
            // Connect
            const res = await fetch('/api/hydrabase/connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, api_key: apiKey })
            });
            const data = await res.json();
            if (data.success) {
                if (btn) btn.textContent = 'Disconnect';
                if (statusEl) { statusEl.textContent = 'Connected'; statusEl.style.color = '#4caf50'; }
                // Add to fallback dropdown
                const fbSel = document.getElementById('metadata-fallback-source');
                if (fbSel && !fbSel.querySelector('option[value="hydrabase"]')) {
                    const opt = document.createElement('option');
                    opt.value = 'hydrabase';
                    opt.textContent = 'Hydrabase (P2P)';
                    fbSel.appendChild(opt);
                }
                showToast('Hydrabase connected', 'success');
            } else {
                if (statusEl) statusEl.textContent = data.error || 'Connection failed';
                showToast('Hydrabase connection failed', 'error');
            }
        }
    } catch (e) {
        if (statusEl) statusEl.textContent = 'Error';
        showToast('Hydrabase connection error', 'error');
    }
}

// ── Music Library Paths ──
// the extra audiobook folders: React (library-audiobooks.tsx)

// ── Sample Studio folders ──
// Mirrors the additional-music-folders UI above: a dynamic list of output
// folders for saved chops. The first entry is the default destination.

// the spoken-word artists list: React (library-audiobooks.tsx)

// The genre whitelist is React now: src/features/settings/tabs/downloads-tab.tsx

// The live log viewer is React now: src/features/settings/tabs/logs-tab.tsx

// Database Maintenance: the System tab is React (system-tab.tsx)

async function activateDevMode() {
    const password = document.getElementById('dev-mode-password').value;
    try {
        const response = await fetch('/api/dev-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });
        const data = await response.json();
        if (data.success) {
            // Re-broadcast for the React dashboard's Hydrabase orb.
            window.dispatchEvent(new CustomEvent('ss:dev-mode', { detail: { enabled: true } }));
            document.getElementById('dev-mode-status').textContent = 'Active';
            document.getElementById('dev-mode-status').style.color = 'rgb(var(--accent-light-rgb))';
            document.getElementById('hydrabase-nav').style.display = '';
            // Orb visibility rides the dispatch (React-owned since the flip).
            document.getElementById('dev-mode-password').value = '';
            showToast('Dev mode activated', 'success');
        } else {
            showToast('Invalid password', 'error');
        }
    } catch (e) {
        showToast('Failed to activate dev mode', 'error');
    }
}

// ── Hydrabase Functions ──

let _hydrabaseConnected = false;

async function hydrabaseToggleConnection() {
    if (_hydrabaseConnected) {
        await hydrabaseDisconnect();
    } else {
        await hydrabaseConnect();
    }
}

async function hydrabaseConnect() {
    const url = document.getElementById('hydra-ws-url').value.trim();
    const apiKey = document.getElementById('hydra-api-key').value.trim();
    if (!url || !apiKey) {
        showToast('URL and API key required', 'error');
        return;
    }
    const statusEl = document.getElementById('hydra-connection-status');
    const btn = document.getElementById('hydra-connect-btn');
    statusEl.textContent = 'Connecting...';
    statusEl.style.color = '#f0ad4e';
    try {
        const response = await fetch('/api/hydrabase/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, api_key: apiKey })
        });
        const data = await response.json();
        if (data.success) {
            _hydrabaseConnected = true;
            statusEl.textContent = 'Connected';
            statusEl.style.color = 'rgb(var(--accent-light-rgb))';
            btn.textContent = 'Disconnect';
            showToast('Connected to Hydrabase', 'success');
        } else {
            statusEl.textContent = 'Failed';
            statusEl.style.color = '#f44336';
            showToast(data.error || 'Connection failed', 'error');
        }
    } catch (e) {
        statusEl.textContent = 'Error';
        statusEl.style.color = '#f44336';
        showToast('Connection error', 'error');
    }
}

async function hydrabaseDisconnect() {
    try {
        await fetch('/api/hydrabase/disconnect', { method: 'POST' });
    } catch (e) { }
    _hydrabaseConnected = false;
    document.getElementById('hydra-connection-status').textContent = 'Disconnected';
    document.getElementById('hydra-connection-status').style.color = '#888';
    document.getElementById('hydra-connect-btn').textContent = 'Connect';
    // Dev mode is disabled on disconnect — hide Hydrabase nav and update settings status
    window.dispatchEvent(new CustomEvent('ss:dev-mode', { detail: { enabled: false } }));
    document.getElementById('hydrabase-nav').style.display = 'none';
    // Orb visibility rides the dispatch (React-owned since the flip).
    const devStatus = document.getElementById('dev-mode-status');
    if (devStatus) {
        devStatus.textContent = 'Inactive';
        devStatus.style.color = '#888';
    }
    showToast('Disconnected — dev mode disabled', 'success');
    navigateToPage('settings');
}

async function loadHydrabaseComparisons() {
    const container = document.getElementById('hydra-comparisons-container');
    if (!container) return;
    try {
        const response = await fetch('/api/hydrabase/comparisons');
        const data = await response.json();
        if (!data.success || !data.comparisons?.length) {
            container.innerHTML = '<p style="color: #666; font-size: 13px;">No comparisons yet. Search with Hydrabase active to generate comparisons.</p>';
            return;
        }
        let html = '';
        for (const comp of data.comparisons) {
            const time = new Date(comp.timestamp * 1000).toLocaleTimeString();
            html += `<div style="background: rgba(30, 30, 30, 0.6); border-radius: 8px; padding: 10px; margin-bottom: 8px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <strong style="color: #fff;">"${comp.query}"</strong>
                    <span style="color: #666; font-size: 11px;">${time}</span>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; font-size: 12px;">
                    <div style="padding: 6px 8px; border-radius: 6px; background: rgba(99, 102, 241, 0.15); border: 1px solid rgba(99, 102, 241, 0.3);">
                        <div style="color: rgba(139, 92, 246, 1); font-weight: 600; margin-bottom: 2px;">Hydrabase</div>
                        <div style="color: #aaa;">${comp.hydrabase?.tracks || 0}T / ${comp.hydrabase?.artists || 0}A / ${comp.hydrabase?.albums || 0}Al</div>
                    </div>
                    <div style="padding: 6px 8px; border-radius: 6px; background: rgba(29, 185, 84, 0.15); border: 1px solid rgba(29, 185, 84, 0.3);">
                        <div style="color: rgb(var(--accent-light-rgb)); font-weight: 600; margin-bottom: 2px;">Spotify</div>
                        <div style="color: #aaa;">${comp.spotify?.tracks || 0}T / ${comp.spotify?.artists || 0}A / ${comp.spotify?.albums || 0}Al</div>
                    </div>
                    <div style="padding: 6px 8px; border-radius: 6px; background: rgba(251, 93, 93, 0.15); border: 1px solid rgba(251, 93, 93, 0.3);">
                        <div style="color: #fb5d5d; font-weight: 600; margin-bottom: 2px;">${comp.fallback_source === 'deezer' ? 'Deezer' : 'iTunes'}</div>
                        <div style="color: #aaa;">${(comp.fallback || comp.itunes)?.tracks || 0}T / ${(comp.fallback || comp.itunes)?.artists || 0}A / ${(comp.fallback || comp.itunes)?.albums || 0}Al</div>
                    </div>
                </div>
            </div>`;
        }
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = '<p style="color: #f44336; font-size: 13px;">Failed to load comparisons.</p>';
    }
}

async function hydrabaseSendRaw(textareaId) {
    const textarea = document.getElementById(textareaId);
    const raw = textarea.value.trim();
    if (!raw) {
        showToast('Payload is empty', 'error');
        return;
    }
    if (!_hydrabaseConnected) {
        showToast('Not connected to Hydrabase', 'error');
        return;
    }
    let payload;
    try {
        payload = JSON.parse(raw);
    } catch (e) {
        showToast('Invalid JSON payload', 'error');
        return;
    }
    // Auto-inject a fresh nonce if not set or zero
    if (!payload.nonce) {
        payload.nonce = Date.now();
    }
    const responseArea = document.getElementById('hydra-response');
    responseArea.textContent = 'Sending...';
    try {
        const response = await fetch('/api/hydrabase/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ payload })
        });
        const data = await response.json();
        if (data.success) {
            responseArea.textContent = JSON.stringify(data.data, null, 2);
        } else {
            responseArea.textContent = 'Error: ' + (data.error || 'Unknown error');
            if (data.error && data.error.includes('Not connected')) {
                _hydrabaseConnected = false;
                document.getElementById('hydra-connection-status').textContent = 'Disconnected';
                document.getElementById('hydra-connection-status').style.color = '#888';
                document.getElementById('hydra-connect-btn').textContent = 'Connect';
            }
        }
    } catch (e) {
        responseArea.textContent = 'Error: ' + e.message;
    }
}

// the tag embedding groups: React (library-processing.tsx)

// ── reading the form without destroying what is not on it ──────────────────
// `getElementById('x')?.value || ''` looks defensive and is the opposite: when
// the element is absent it writes an EMPTY STRING over the stored setting. The
// server sets keys one at a time with no deep merge, so an empty string IS the
// new value.
//
// That is not hypothetical. A broken edit removed the Indexers / Torrent /
// Usenet block from index.html for a few minutes; the running server serves
// that file per request, so the settings page rendered without those fields and
// the next auto-save wiped three URLs out of the database. The API key survived
// only because the server refuses to overwrite a stored secret with a blank.
//
// These return undefined for a missing element, and JSON.stringify drops
// undefined-valued keys — so the key never reaches the server and the stored
// value is left exactly as it was. A missing field now means "I have nothing to
// say about this", which is the truth, instead of "set it to nothing".
function _cfgStr(id, { trim = false, fallback } = {}) {
    const el = document.getElementById(id);
    if (!el) return undefined;              // absent -> say nothing about it
    let v = el.value ?? '';
    if (trim) v = String(v).trim();
    return (v === '' && fallback !== undefined) ? fallback : v;
}

// `parseFloat(el?.value) || 0` has the same fault as the int version.
function _cfgFloat(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return undefined;
    const n = parseFloat(el.value);
    return Number.isFinite(n) ? n : fallback;
}

// Same rule for numbers. `parseInt(el?.value) || 0` writes a ZERO over the
// stored setting when the element is missing — which for a timeout or a bitrate
// floor is not a blank, it is a different and usually harmful value.
function _cfgInt(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return undefined;
    const n = parseInt(el.value, 10);
    return Number.isFinite(n) ? n : fallback;
}

function _cfgBool(id) {
    const el = document.getElementById(id);
    return el ? !!el.checked : undefined;
}

async function saveSettings(quiet = false) {
    // #879: refuse to save if the settings never loaded successfully — the form
    // is showing defaults, not the user's real config, so saving would wipe it.
    // Cleared automatically on the next successful load (reload the page).
    if (window._settingsLoadFailed) {
        if (!quiet && typeof showToast === 'function') {
            showToast("Settings didn't load — reload the page before saving (your config is untouched)", 'error');
        }
        return;
    }

    // Comma-separated text fields, tolerant of stray spaces and empties.
    const splitList = (raw) => String(raw || '').split(',')
        .map(part => part.trim())
        .filter(Boolean);

    let musicBrainzServerSettings;
    try {
        musicBrainzServerSettings = collectMusicBrainzServerSettings();
    } catch (error) {
        if (!quiet) showToast(error.message, 'error');
        return;
    }

    // a naming template that would not work refuses the save. the templates
    // are the Organization card's (React), which holds its own saves back too
    const validationErrors = window.SoulSyncSettings?.templateErrors?.() || [];
    if (validationErrors.length > 0) {
        if (!quiet) showToast('Template validation failed: ' + validationErrors.join(', '), 'error');
        return;
    }

    // Determine active server from toggle buttons
    let activeServer = activeServerFromToggles();
    // ISOLATION: this page is reused on the video side. Connection details (Plex/
    // Jellyfin creds) ARE shared and save fine — but the video side must NEVER
    // change the MUSIC active server. So when saving from the video side, keep
    // active_media_server exactly as it was persisted (the toggle there only opens
    // a config panel; it does not pick the music server).
    if (document.body.getAttribute('data-side') === 'video' && window._persistedActiveServer) {
        activeServer = window._persistedActiveServer;
    }

    const metadataSourceSelect = document.getElementById('metadata-fallback-source');
    const discogsTokenInput = document.getElementById('discogs-token');
    const discogsTokenPresent = !!discogsTokenInput?.value?.trim();
    let metadataSource = metadataSourceSelect?.value || 'deezer';
    const spotifySessionActive = _lastStatusPayload?.spotify?.authenticated === true;
    const spotifyFreeInstalled = _lastStatusPayload?.spotify?.free_installed === true;
    if (metadataSource === 'spotify' && !spotifySessionActive) {
        metadataSource = _metadataSourceFallback('spotify');
        if (metadataSourceSelect) metadataSourceSelect.value = metadataSource;
        if (!quiet) {
            showToast('Spotify is disconnected, so the primary metadata source was switched.', 'warning');
        }
    } else if (metadataSource === 'spotify_free' && !spotifyFreeInstalled) {
        metadataSource = _metadataSourceFallback('spotify_free');
        if (metadataSourceSelect) metadataSourceSelect.value = metadataSource;
        if (!quiet) {
            showToast('Spotify Free needs the SpotipyFree package installed.', 'warning');
        }
    } else if (metadataSource === 'discogs' && !discogsTokenPresent) {
        metadataSource = _metadataSourceFallback('discogs');
        if (metadataSourceSelect) metadataSourceSelect.value = metadataSource;
        if (!quiet) {
            showToast('Discogs requires a personal access token before it can be selected as the primary metadata source.', 'warning');
        }
    } else if (metadataSource === 'jiosaavn' && !document.getElementById('experimental-jiosaavn-enabled')?.checked) {
        metadataSource = 'deezer';
        if (metadataSourceSelect) metadataSourceSelect.value = metadataSource;
        if (!quiet) {
            showToast('JioSaavn is not enabled — turn it on under Advanced → Experimental.', 'warning');
        }
    }

    // the optional "Playlist File Naming" template: a filename (no path
    // separator) that includes $title, as the server requires. The field is
    // the Organization card's (React), which holds its own saves back too
    const _plItemErr = window.SoulSyncSettings?.playlistItemError?.();
    if (_plItemErr) {
        showToast(_plItemErr, 'error');
        return;
    }

    const settings = {
        active_media_server: activeServer,
        spotify: {
            client_id: document.getElementById('spotify-client-id').value,
            client_secret: document.getElementById('spotify-client-secret').value,
            redirect_uri: document.getElementById('spotify-redirect-uri').value,
            // embed_tags and tags: the Post-Processing card is React (library-processing.tsx)
        },
        tidal: {
            client_id: document.getElementById('tidal-client-id').value,
            client_secret: document.getElementById('tidal-client-secret').value,
            redirect_uri: document.getElementById('tidal-redirect-uri').value,
            // embed_tags and tags: the Post-Processing card is React (library-processing.tsx)
        },
        plex: {
            base_url: document.getElementById('plex-url').value,
            token: document.getElementById('plex-token').value
        },
        jellyfin: {
            base_url: document.getElementById('jellyfin-url').value,
            api_key: document.getElementById('jellyfin-api-key').value,
            api_timeout: parseInt(document.getElementById('jellyfin-timeout').value) || 30
        },
        music_assistant: {
            base_url: _cfgStr('music_assistant-url', { trim: true }),
            token: _cfgStr('music_assistant-token', { trim: true }),
            source_instance: _cfgStr('music_assistant-source'),
            source_folder: _cfgStr('music_assistant-source-folder', { trim: true })
        },
        navidrome: {
            base_url: document.getElementById('navidrome-url').value,
            username: document.getElementById('navidrome-username').value,
            password: document.getElementById('navidrome-password').value
        },
        soulseek: {
            // slskd_url, api_key and the search, peer, timeout and cleanup
            // keys: the Soulseek source panel is React (source-panels.tsx)
            // download_path, transfer_path, min_free_disk_gb: the Folders card is React
            // preferred_version: the Downloads tab (Filtering) is React
        },
        listenbrainz: {
            base_url: document.getElementById('listenbrainz-base-url').value,
            token: document.getElementById('listenbrainz-token').value,
            username: _cfgStr('listenbrainz-username', { trim: true }),
            scrobble_enabled: document.getElementById('listenbrainz-scrobble-enabled').checked,
        },
        acoustid: {
            api_key: document.getElementById('acoustid-api-key').value,
            enabled: document.getElementById('acoustid-enabled').checked,
            // require_verified: the Quality tab is React, and saves it with
            // its profile (the server copies the default profile into config)
        },
        concerts: {
            ticketmaster_api_key: _cfgStr('concerts-ticketmaster-api-key', { trim: true }),
            setlistfm_api_key: _cfgStr('concerts-setlistfm-api-key', { trim: true }),
            // ?. not || '': an absent field must stay undefined, or a save wipes it
            country: _cfgStr('concerts-country', { trim: true })?.toUpperCase()
        },
        lastfm: {
            // _cfgStr rather than .value: this input is absent on the video
            // side, and reading .value off a missing element is how a save
            // wipes a stored setting.
            username: _cfgStr('lastfm-username', { trim: true }),
            api_key: document.getElementById('lastfm-api-key').value,
            api_secret: document.getElementById('lastfm-api-secret').value,
            scrobble_enabled: document.getElementById('lastfm-scrobble-enabled').checked,
            // embed_tags and tags: the Post-Processing card is React (library-processing.tsx)
        },
        fanart: {
            // _cfgStr: undefined when the field is absent, so a save there
            // doesn't wipe the stored key
            api_key: _cfgStr('artist-fanart-api-key', { trim: true }),
        },
        pollinations: {
            // _cfgStr: undefined when the field is absent (video side), so a
            // save there doesn't wipe the stored key
            api_key: _cfgStr('pollinations-api-key', { trim: true }),
        },
        genius: {
            access_token: document.getElementById('genius-access-token').value,
            // embed_tags and tags: the Post-Processing card is React (library-processing.tsx)
        },
        itunes: {
            country: document.getElementById('itunes-country').value || 'US',
            // embed_tags and tags: the Post-Processing card is React (library-processing.tsx)
        },
        discogs: {
            token: document.getElementById('discogs-token').value,
        },
        metadata: {
            // 'Spotify Free' is stored as the spotify source + a flag, so all
            // downstream 'spotify' routing is unchanged.
            fallback_source: metadataSource === 'spotify_free' ? 'spotify' : metadataSource,
            spotify_free: metadataSource === 'spotify_free',
            // Independent opt-in: run the enrichment worker on Spotify Free even
            // when an official account is connected (spares the official quota).
            spotify_free_enrichment: _cfgBool('metadata-spotify-free-enrichment')
        },
        experimental: {
            jiosaavn_enabled: _cfgBool('experimental-jiosaavn-enabled'),
            bandcamp_enabled: _cfgBool('experimental-bandcamp-enabled'),
        },
        image_cache: {
            // Server-side resizing is opt-in; the cache itself keeps whatever
            // it was already set to (on, for every install since it shipped).
            thumbnails: _cfgBool('imgcache-thumbnails'),
            max_cache_mb: _cfgInt('imgcache-max-mb', 0),
        },
        hydrabase: {
            url: document.getElementById('hydrabase-url').value,
            api_key: document.getElementById('hydrabase-api-key').value,
            auto_connect: document.getElementById('hydrabase-auto-connect').checked
        },
        // download_source: every key is React now (the chain, the Downloads
        // tab, and the YouTube, torrent and usenet source panels)
        tidal_download: {
            // quality derived from the global Quality Profile (ranked targets); allow_fallback always true
        },
        hifi_download: {
            // quality derived from the global Quality Profile (ranked targets); allow_fallback always true
        },
        // hifi (embed_tags, tags): the Post-Processing card is React
        // beatport (embed_tags, search_all_tracks, tags): the Post-Processing card is React
        // deezer_download and lidarr_download: their Sources panels are React
        // prowlarr, torrent_client and usenet_client: their Sources panels are React
        soundcloud_download: {
            // No knobs yet — anonymous-only. Keeping the key present so
            // future tier-2 OAuth wiring (Go+ session token) doesn't have
            // to migrate existing configs.
        },
        // qobuz (embed_tags, tags): the Post-Processing card is React
        // database: the System tab is React (system-tab.tsx)
        // metadata_enhancement: the Post-Processing card is React
        musicbrainz: {
            ...musicBrainzServerSettings,
            // embed_tags, use_original_date_for_date and tags: the
            // Post-Processing card is React (library-processing.tsx)
        },
        deezer: {
            app_id: document.getElementById('deezer-app-id').value,
            app_secret: document.getElementById('deezer-app-secret').value,
            redirect_uri: document.getElementById('deezer-redirect-uri').value,
            // embed_tags and tags: the Post-Processing card is React (library-processing.tsx)
        },
        // audiodb (embed_tags, tags): the Post-Processing card is React
        // file_organization: the Organization card is React
        mcp: {
            enabled: _cfgBool('mcp-enabled'),
        },
        lidarr_compat: {
            enabled: _cfgBool('lidarr-compat-enabled'),
            profile_id: _cfgInt('lidarr-compat-profile', _lidarrCompatProfileId),
        },
        // wishlist: the Organization card is React
        // playlist_sync, listening_stats and discover: the Discovery tab is
        // React and saves its own keys
        // content_filter and genre_whitelist: the Downloads tab is React
        // post_processing: the Post-Processing card (replaygain, duration),
        // Quality and Downloads tabs are React
        library: {
            // music_paths, sample_paths, music_videos_path, podcasts_path: the
            // Folders card is React (library-folders.tsx)
            // audiobooks_path, audiobooks_additional_paths: React (library-audiobooks.tsx)
            // reorganize_preserve_casing, write_artist_nfo,
            // redownload_externally_deleted: the Organization card is React
        },
        // podcasts (download_path, media_format): the Folders and Organization
        // cards are React
        // audiobooks: React (library-audiobooks.tsx)
        // import: the Import card and the Quality tab are React
        playlists: {
            // materialize_path and materialize_mode: the Folders card is React
        },
        // lossy_copy: the Quality tab is React, and saves it with its profile
        // album_downloads: the Downloads tab (Album Publishing) is React
        m3u_export: {
            // enabled, library_enabled and library_path: the Discovery tab
            // relative_paths, entry_base_path, rewrite_from, rewrite_to: the
            // Folders card is React
        },
        // ui_appearance: the Appearance tab and the System tab's autosave
        // switch are React and save their own keys of it
        // youtube: its Sources panel is React (source-panels-more.tsx)
        security: {
            require_pin_on_launch: _cfgBool('security-require-pin'),
            cors_origins: _cfgStr('security-cors-origins', { trim: true }),
            trust_reverse_proxy: _cfgBool('security-trust-proxy'),
            auth_proxy_header: _cfgStr('security-auth-proxy-header', { trim: true }),
            require_login: _cfgBool('security-require-login'),
            plex_signin: _cfgBool('security-plex-signin'),
            plex_signin_auto_create: _cfgBool('security-plex-signin-auto-create'),
            plex_signin_default_can_download: _cfgBool('security-plex-signin-can-download'),
            oidc_signin: _cfgBool('security-oidc-signin'),
            oidc_only: _cfgBool('security-oidc-only'),
            oidc_issuer: _cfgStr('security-oidc-issuer', { trim: true }),
            oidc_client_id: _cfgStr('security-oidc-client-id', { trim: true }),
            oidc_client_secret: _cfgStr('security-oidc-client-secret'),
            oidc_button_label: _cfgStr('security-oidc-button-label', { trim: true }),
            oidc_icon_url: _cfgStr('security-oidc-icon-url', { trim: true }),
            oidc_required_group: _cfgStr('security-oidc-required-group', { trim: true }),
            oidc_groups_claim: _cfgStr('security-oidc-groups-claim', { trim: true }),
            oidc_scopes: _cfgStr('security-oidc-scopes', { trim: true }),
            oidc_redirect_uri: _cfgStr('security-oidc-redirect-uri', { trim: true }),
            oidc_signin_auto_create: _cfgBool('security-oidc-signin-auto-create'),
            oidc_signin_default_can_download: _cfgBool('security-oidc-signin-can-download'),
            signup_sides: _cfgStr('security-signup-sides', { fallback: 'music' }),
            signup_request_limit: _cfgInt('security-signup-request-limit', 0),
            signup_request_limit_days: _cfgInt('security-signup-request-limit-days', 7),
        }
    };

    // Validate cors_origins entries — backend silently filters malformed
    // values, so warn the user up-front if any line doesn't look like a
    // URL (or the special '*' token). One-shot toast; doesn't block save.
    const corsRaw = settings.security.cors_origins;
    if (corsRaw) {
        const entries = corsRaw.replace(/\n/g, ',').split(',')
            .map(s => s.trim())
            .filter(s => s);
        const invalid = entries.filter(e => {
            if (e === '*') return false;
            // Accept scheme://host[:port] only — no path, query, or fragment.
            // Engineio compares Origin against {scheme}://{host} exactly.
            return !/^https?:\/\/[^\s/?#]+$/i.test(e);
        });
        if (invalid.length) {
            showToast(
                `Allowed Origins: ${invalid.length} entr${invalid.length === 1 ? 'y looks' : 'ies look'} malformed (need full URL like https://soulsync.example.com, no trailing slash). Saving anyway — they\'ll be ignored.`,
                'warning'
            );
        }
    }

    // (this save used to put the live default profile's values back into the
    // quality keys while another profile was open, so it could not overwrite
    // the default with them. It no longer sends those keys at all: the Quality
    // tab is React and saves them with their profile, SETTINGS_PORT.md step 6)

    try {
        if (!quiet) showLoadingOverlay('Saving settings...');
        setSettingsSaveState('saving');

        // Save main settings
        const response = await fetch(API.settings, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });

        const result = await response.json();

        // The tabs ported to React hold their own values and save only what
        // changed. This is how the save button reaches them, and how their
        // changes go out with autosave switched off (SETTINGS_PORT.md). The
        // quality profile on screen is one of them (the Quality tab).
        let reactSaved = true;
        try {
            if (window.SoulSyncSettings) reactSaved = await window.SoulSyncSettings.flush();
        } catch (error) {
            console.error('Error saving React settings tabs:', error);
            reactSaved = false;
        }

        // (the discovery lookback and hemisphere are saved by their React tab,
        // when they change, through SoulSyncSettings.flush above)

        if (result.success && !reactSaved) {
            setSettingsSaveState('error');
            showToast('Some settings failed to save', 'error', 'set-services');
        } else if (result.success) {
            // the button says Saved, a toast on every keystroke was noise
            setSettingsSaveState('saved');
            _forceServiceStatusRefresh();
            _stgRefreshAfterSave();
        } else {
            setSettingsSaveState('error');
            showToast(`Failed to save settings: ${result.error}`, 'error', 'set-services');
        }
    } catch (error) {
        console.error('Error saving settings:', error);
        setSettingsSaveState('error');
        showToast('Failed to save settings', 'error', 'set-services');
    } finally {
        if (!quiet) hideLoadingOverlay();
    }
}

async function authorizeLastfmScrobbling() {
    try {
        // Save settings first so API secret is stored
        await saveSettings();
        const resp = await fetch('/api/lastfm/auth-url');
        const data = await resp.json();
        if (data.success && data.url) {
            window.open(data.url, '_blank', 'width=600,height=500');
            showToast('Authorize SoulSync in the Last.fm window that opened', 'info');
        } else {
            showToast(data.error || 'Could not generate auth URL', 'error');
        }
    } catch (e) {
        showToast('Failed to start Last.fm authorization', 'error');
    }
}

async function testConnection(service) {
    try {
        if (typeof settingsAutoSaveTimer !== 'undefined' && settingsAutoSaveTimer) {
            clearTimeout(settingsAutoSaveTimer);
            settingsAutoSaveTimer = null;
            if (typeof saveSettings === 'function') await saveSettings(true);
        }
        showLoadingOverlay(`Testing ${service} connection...`);

        const response = await fetch(API.testConnection, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service })
        });

        const result = await response.json();

        if (result.success) {
            // Use backend's message which contains dynamic source name. a pass
            // can still carry a warning (leading \u26a0), which shouldn't read as green
            const warned = String(result.message || '').trim().startsWith('\u26a0');
            showToast(result.message || `${service} connection successful`, warned ? 'warning' : 'success');

            // Load music libraries after successful connection
            if (service === 'plex') {
                loadPlexMusicLibraries();
            } else if (service === 'jellyfin') {
                loadJellyfinUsers().then(() => loadJellyfinMusicLibraries());
            } else if (service === 'navidrome') {
                loadNavidromeMusicFolders();
            }
        } else {
            showToast(`${service} connection failed: ${result.error}`, 'error', 'gs-connecting');
        }
    } catch (error) {
        console.error(`Error testing ${service} connection:`, error);
        showToast(`Failed to test ${service} connection`, 'error', 'gs-connecting');
    } finally {
        hideLoadingOverlay();
    }
}

// the deezer card holds two logins: the oauth app (favorites, playlists) and an
// ARL (playlists + downloads). testConnection('deezer') covers the public api and
// the oauth token; the ARL has its own check, so run both and say what each found.
async function testDeezerConnection() {
    let arl = document.getElementById('deezer-connection-arl')?.value || '';
    if (!arl.trim()) return testConnection('deezer');
    // an untouched field holds the redaction mask; empty means "test the saved one" (#870)
    if (arl === REDACTED_SECRET_SENTINEL) arl = '';
    try {
        if (typeof settingsAutoSaveTimer !== 'undefined' && settingsAutoSaveTimer) {
            clearTimeout(settingsAutoSaveTimer);
            settingsAutoSaveTimer = null;
            if (typeof saveSettings === 'function') await saveSettings(true);
        }
        showLoadingOverlay('Testing Deezer connection...');
        const post = (url, body) => fetch(url, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        }).then(r => r.json()).catch(() => ({ success: false, error: 'request failed' }));
        const [api, arlRes] = await Promise.all([
            post(API.testConnection, { service: 'deezer' }),
            post('/api/deezer-download/test', { arl }),
        ]);
        const apiMsg = api.success ? (api.message || 'Deezer reachable') : `Deezer failed: ${api.error}`;
        const arlMsg = arlRes.success
            ? `ARL: ${arlRes.user || 'Unknown'} (${arlRes.tier || 'Free'})`
            : `ARL failed: ${arlRes.error || 'rejected'}`;
        const ok = api.success && arlRes.success;
        const warned = String(api.message || '').trim().startsWith('\u26a0');
        showToast(`${apiMsg} · ${arlMsg}`, !ok ? 'error' : (warned ? 'warning' : 'success'), ok ? undefined : 'gs-connecting');
    } finally {
        hideLoadingOverlay();
    }
}

async function clearQuarantine() {
    if (!await showConfirmDialog({ title: 'Clear Quarantine', message: 'Delete all files in the quarantine folder? This cannot be undone.', confirmText: 'Delete', destructive: true })) return;
    try {
        showLoadingOverlay('Clearing quarantine folder...');
        const response = await fetch('/api/quarantine/clear', { method: 'POST' });
        const result = await response.json();
        if (result.success) {
            showToast(result.message || 'Quarantine cleared', 'success');
        } else {
            showToast(`Failed to clear quarantine: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('Error clearing quarantine:', error);
        showToast('Failed to clear quarantine', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

// ======================== API Key Management ========================

// the saved profile, so a save before the list loads keeps it
let _lidarrCompatProfileId = 1;

async function loadLidarrCompatProfiles(selectedId) {
    _lidarrCompatProfileId = parseInt(selectedId, 10) || 1;
    const select = document.getElementById('lidarr-compat-profile');
    if (!select) return;
    try {
        const response = await fetch('/api/profiles');
        const data = await response.json();
        const profiles = data.profiles || [];
        if (!profiles.length) return;
        select.innerHTML = profiles.map(p => {
            const note = p.can_download === false ? ' (needs approval)' : '';
            return `<option value="${escapeHtml(String(p.id))}">${escapeHtml(p.name || 'Profile ' + p.id)}${note}</option>`;
        }).join('');
        select.value = String(_lidarrCompatProfileId);
        if (select.value !== String(_lidarrCompatProfileId)) select.value = '1';
    } catch (e) {
        console.debug('lidarr profile list failed', e);
    }
}

async function loadApiKeys() {
    const container = document.getElementById('api-keys-list');
    if (!container) return;

    try {
        const response = await fetch('/api/v1/api-keys-internal');
        if (response.ok) {
            const data = await response.json();
            renderApiKeys(data.data?.keys || []);
        } else {
            container.innerHTML = '<div style="color: #666; font-size: 13px;">No API keys configured.</div>';
        }
    } catch (e) {
        container.innerHTML = '<div style="color: #666; font-size: 13px;">No API keys configured.</div>';
    }
}

function renderApiKeys(keys) {
    const container = document.getElementById('api-keys-list');
    if (!container) return;

    if (!keys || keys.length === 0) {
        container.innerHTML = '<div style="color: #666; font-size: 13px; padding: 4px 0;">No API keys yet. Generate one below.</div>';
        return;
    }

    container.innerHTML = keys.map(k => `
        <div style="display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; margin-bottom: 4px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); border-radius: 6px;">
            <div style="flex: 1; min-width: 0;">
                <div style="font-size: 13px; color: #e0e0e0; font-weight: 500;">${escapeHtml(k.label || 'Unnamed')}
                    <span style="margin-left: 6px; padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; ${k.scope === 'read' ? 'background: rgba(100,255,218,0.12); color: #64ffda;' : 'background: rgba(255,171,64,0.12); color: #ffab40;'}">${k.scope === 'read' ? 'Read-only' : 'Admin'}</span>
                </div>
                <div style="font-size: 11px; color: #666; margin-top: 2px;">
                    <code>${escapeHtml(k.key_prefix || 'sk_...')}...</code>
                    &middot; Created ${k.created_at ? new Date(k.created_at).toLocaleDateString() : 'unknown'}
                    ${k.last_used_at ? '&middot; Last used ' + new Date(k.last_used_at).toLocaleDateString() : ''}
                </div>
            </div>
            <button class="revoke-api-key-btn" data-key-id="${escapeHtml(k.id)}" data-key-label="${escapeHtml(k.label || 'this key')}"
                style="padding: 4px 10px; background: rgba(255,82,82,0.1); border: 1px solid rgba(255,82,82,0.2); color: #ff5252; border-radius: 4px; cursor: pointer; font-size: 11px; white-space: nowrap;">
                Revoke
            </button>
        </div>
    `).join('');
    _initApiKeyClickHandler();
}

function _initApiKeyClickHandler() {
    const container = document.getElementById('api-keys-list');
    if (!container) return;
    container.onclick = (e) => {
        const btn = e.target.closest('.revoke-api-key-btn');
        if (btn) {
            e.preventDefault();
            revokeApiKey(btn.dataset.keyId, btn.dataset.keyLabel);
        }
    };
}

async function generateApiKey() {
    const labelInput = document.getElementById('api-key-label');
    const label = labelInput ? labelInput.value.trim() : '';
    const scopeInput = document.getElementById('api-key-scope');
    const scope = scopeInput && scopeInput.value === 'admin' ? 'admin' : 'read';

    try {
        const response = await fetch('/api/v1/api-keys-internal/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label: label || 'Default', scope })
        });
        const data = await response.json();

        if (data.success && data.data?.key) {
            const keyDisplay = document.getElementById('api-key-generated');
            const keyValue = document.getElementById('api-key-value');
            if (keyDisplay && keyValue) {
                keyValue.textContent = data.data.key;
                keyDisplay.style.display = 'block';
            }
            if (labelInput) labelInput.value = '';
            showToast('API key generated! Copy it now.', 'success');
            loadApiKeys();
        } else {
            showToast(data.error?.message || 'Failed to generate API key', 'error');
        }
    } catch (error) {
        console.error('Error generating API key:', error);
        showToast('Failed to generate API key', 'error');
    }
}

function copyApiKey() {
    const keyValue = document.getElementById('api-key-value');
    if (keyValue) {
        navigator.clipboard.writeText(keyValue.textContent).then(() => {
            showToast('API key copied to clipboard', 'success');
        }).catch(() => {
            // Fallback for older browsers
            const range = document.createRange();
            range.selectNode(keyValue);
            window.getSelection().removeAllRanges();
            window.getSelection().addRange(range);
            document.execCommand('copy');
            showToast('API key copied', 'success');
        });
    }
}

async function revokeApiKey(keyId, label) {
    if (!await showConfirmDialog({ title: 'Revoke API Key', message: `Revoke API key "${label}"? Any apps using this key will stop working.`, confirmText: 'Revoke', destructive: true })) return;

    try {
        const response = await fetch(`/api/v1/api-keys-internal/revoke/${keyId}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            showToast('API key revoked', 'success');
            loadApiKeys();
        } else {
            showToast(data.error?.message || 'Failed to revoke key', 'error');
        }
    } catch (error) {
        console.error('Error revoking API key:', error);
        showToast('Failed to revoke key', 'error');
    }
}

// Dashboard-specific test functions that create activity items
async function testDashboardConnection(service) {
    // 'spotify_free' is a display-only label for the no-auth composite; the real
    // service to test is 'spotify'.
    if (service === 'spotify_free') service = 'spotify';
    try {
        showLoadingOverlay(`Testing ${service} service...`);

        const response = await fetch(API.testDashboardConnection, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service })
        });

        const result = await response.json();

        if (result.success) {
            // Use backend's message which contains dynamic source name
            showToast(result.message || `${service} service verified`, 'success');
            // Refresh status indicators immediately so UI reflects the new state
            fetchAndUpdateServiceStatus();
        } else {
            showToast(`${service} service check failed: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error(`Error testing ${service} service:`, error);
        showToast(`Failed to test ${service} service`, 'error');
    } finally {
        hideLoadingOverlay();
    }
}

// Individual Auto-detect functions - same as GUI
async function autoDetectPlex() {
    try {
        showLoadingOverlay('Auto-detecting Plex server...');

        const response = await fetch('/api/detect-media-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_type: 'plex' })
        });

        const result = await response.json();

        if (result.success) {
            document.getElementById('plex-url').value = result.found_url;
            showToast(`Plex server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }

    } catch (error) {
        console.error('Error auto-detecting Plex:', error);
        showToast('Failed to auto-detect Plex server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function autoDetectJellyfin() {
    try {
        showLoadingOverlay('Auto-detecting Jellyfin server...');

        const response = await fetch('/api/detect-media-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_type: 'jellyfin' })
        });

        const result = await response.json();

        if (result.success) {
            document.getElementById('jellyfin-url').value = result.found_url;
            showToast(`Jellyfin server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }

    } catch (error) {
        console.error('Error auto-detecting Jellyfin:', error);
        showToast('Failed to auto-detect Jellyfin server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function autoDetectMusicAssistant() {
    try {
        const response = await fetch('/api/detect-media-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_type: 'music_assistant' })
        });
        const result = await response.json();
        if (result.success && result.found_url) {
            document.getElementById('music_assistant-url').value = result.found_url;
            showToast(`Music Assistant found at ${result.found_url}`, 'success');
        } else {
            showToast(result.error || 'No Music Assistant server found', 'error');
        }
    } catch (e) {
        showToast('Auto-detect failed: ' + e.message, 'error');
    }
}

async function loadMusicAssistantSources(quiet = false) {
    const container = document.getElementById('music_assistant-source-selector-container');
    const selector = document.getElementById('music_assistant-source');
    if (!container || !selector) return;
    try {
        const response = await fetch('/api/music-assistant/sources');
        const result = await response.json();
        if (!result.success) {
            container.style.display = 'none';
            if (!quiet) showToast(result.error || 'Could not load Music Assistant sources', 'error');
            return;
        }
        selector.innerHTML = '';
        for (const s of result.sources || []) {
            const opt = document.createElement('option');
            opt.value = s.key;
            opt.textContent = `${s.title} (${s.domain})`;
            if (s.key === result.selected) opt.selected = true;
            selector.appendChild(opt);
        }
        container.style.display = (result.sources || []).length ? '' : 'none';
        if (!quiet && !(result.sources || []).length) showToast('Music Assistant has no Local files or SMB source', 'warning');
    } catch (e) {
        container.style.display = 'none';
        if (!quiet) showToast('Could not load Music Assistant sources: ' + e.message, 'error');
    }
}

async function selectMusicAssistantSource() {
    const selector = document.getElementById('music_assistant-source');
    if (!selector || !selector.value) return;
    try {
        const response = await fetch('/api/music-assistant/select-source', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ instance_id: selector.value })
        });
        const result = await response.json();
        showToast(result.success ? `Music source set to ${selector.selectedOptions[0]?.textContent || selector.value}` : (result.error || 'Could not set the source'), result.success ? 'success' : 'error');
    } catch (e) {
        showToast('Could not set the source: ' + e.message, 'error');
    }
}

async function autoDetectNavidrome() {
    try {
        showLoadingOverlay('Auto-detecting Navidrome server...');

        const response = await fetch('/api/detect-media-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_type: 'navidrome' })
        });

        const result = await response.json();

        if (result.success) {
            document.getElementById('navidrome-url').value = result.found_url;
            showToast(`Navidrome server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }

    } catch (error) {
        console.error('Error auto-detecting Navidrome:', error);
        showToast('Failed to auto-detect Navidrome server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

function cancelDetection(service) {
    const progressDiv = document.getElementById(`${service}-detection-progress`);
    progressDiv.classList.add('hidden');
    showToast(`${service} detection cancelled`, 'error');
}

function updateStatusDisplays() {
    // Update status displays based on current service status
    // This would be called after status updates
    const services = ['spotify', 'media-server', 'soulseek'];
    services.forEach(service => {
        const display = document.getElementById(`${service}-status-display`);
        if (display) {
            // Status will be updated by the regular status monitoring
        }
    });
}

async function authenticateSpotify() {
    try {
        showLoadingOverlay('Saving credentials and starting Spotify authentication...');
        // Save settings first to ensure client_id/client_secret are persisted
        await saveSettings();
        showToast('Spotify authentication started', 'success');
        window._spotifyAuthWindow = window.open('/auth/spotify', '_blank');
    } catch (error) {
        console.error('Error authenticating Spotify:', error);
        showToast('Failed to start Spotify authentication', 'error', 'gs-connecting');
    } finally {
        hideLoadingOverlay();
    }
}

async function disconnectSpotify() {
    if (!await showConfirmDialog({
        title: 'Disconnect Spotify',
        message: 'Disconnect Spotify? Spotify-specific actions will stop until you reauthenticate.'
    })) {
        return;
    }
    try {
        showLoadingOverlay('Disconnecting Spotify...');
        const response = await fetch('/api/spotify/disconnect', { method: 'POST' });
        const data = await response.json();
        if (data.success) {
            showToast(data.message || 'Spotify disconnected.', 'success');
            syncMetadataSourceSelection(data.source || 'deezer');
            // Immediately refresh status to update UI
            await fetchAndUpdateServiceStatus();
        } else {
            showToast(`Failed to disconnect: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error disconnecting Spotify:', error);
        showToast('Failed to disconnect Spotify', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

// ── Spotify Rate Limit Handling ───────────────────────────────────────────
let _spotifyRateLimitShown = false;
let _spotifyInCooldown = false;
let _rateLimitModalOpen = false;
let _rateLimitCountdownInterval = null;
let _rateLimitExpiresAt = 0;

function handleSpotifyRateLimit(rateLimitInfo) {
    if (!rateLimitInfo || !rateLimitInfo.active) {
        if (_spotifyRateLimitShown) {
            _spotifyRateLimitShown = false;
            closeRateLimitModal();
            showToast('Spotify access restored', 'success');
            // Refresh discover page if user is on it — data source switched back to Spotify
            if (currentPage === 'discover') {
                console.log('Spotify restored — refreshing discover page data');
                if (typeof loadDiscoverPage === 'function') loadDiscoverPage();
            }
        }
        return;
    }
    // Update countdown if modal is open (status pushes every 10s keep it accurate)
    if (_rateLimitModalOpen && rateLimitInfo.remaining_seconds) {
        _rateLimitExpiresAt = Date.now() + (rateLimitInfo.remaining_seconds * 1000);
    }
    if (!_spotifyRateLimitShown) {
        _spotifyRateLimitShown = true;
        _spotifyInCooldown = false;
        showRateLimitModal(rateLimitInfo);
        // Refresh discover page if user is on it — data source switched to iTunes
        if (currentPage === 'discover') {
            console.log('Spotify rate limited — refreshing discover page with iTunes data');
            if (typeof loadDiscoverPage === 'function') loadDiscoverPage();
        }
    }
}

function showRateLimitModal(rateLimitInfo) {
    const overlay = document.getElementById('rate-limit-modal-overlay');
    if (!overlay) return;

    // Populate details
    const banDuration = document.getElementById('rate-limit-ban-duration');
    const endpoint = document.getElementById('rate-limit-endpoint');
    const countdown = document.getElementById('rate-limit-countdown');

    banDuration.textContent = formatRateLimitDuration(rateLimitInfo.retry_after || rateLimitInfo.remaining_seconds);
    endpoint.textContent = rateLimitInfo.endpoint || 'unknown';
    countdown.textContent = formatRateLimitDuration(rateLimitInfo.remaining_seconds);

    // Set expiry for live countdown
    _rateLimitExpiresAt = Date.now() + (rateLimitInfo.remaining_seconds * 1000);

    // Start live countdown timer
    if (_rateLimitCountdownInterval) clearInterval(_rateLimitCountdownInterval);
    _rateLimitCountdownInterval = setInterval(() => {
        const remaining = Math.max(0, Math.round((_rateLimitExpiresAt - Date.now()) / 1000));
        countdown.textContent = formatRateLimitDuration(remaining);
        if (remaining <= 0) {
            clearInterval(_rateLimitCountdownInterval);
            _rateLimitCountdownInterval = null;
        }
    }, 1000);

    overlay.classList.remove('hidden');
    _rateLimitModalOpen = true;
}

function closeRateLimitModal() {
    const overlay = document.getElementById('rate-limit-modal-overlay');
    if (overlay) overlay.classList.add('hidden');
    if (_rateLimitCountdownInterval) {
        clearInterval(_rateLimitCountdownInterval);
        _rateLimitCountdownInterval = null;
    }
    _rateLimitModalOpen = false;
}

async function disconnectSpotifyFromRateLimit() {
    closeRateLimitModal();
    try {
        showLoadingOverlay('Disconnecting Spotify...');
        const response = await fetch('/api/spotify/disconnect', { method: 'POST' });
        const data = await response.json();
        if (data.success) {
            _spotifyRateLimitShown = false;
            showToast(data.message || 'Spotify disconnected.', 'success');
            syncMetadataSourceSelection(data.source || 'deezer');
            await fetchAndUpdateServiceStatus();
            if (currentPage === 'discover') {
                if (typeof loadDiscoverPage === 'function') loadDiscoverPage();
            }
        } else {
            showToast(`Failed to disconnect: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error disconnecting Spotify:', error);
        showToast('Failed to disconnect Spotify', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

function formatRateLimitDuration(seconds) {
    if (!seconds || seconds <= 0) return '0s';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

async function authenticateTidal() {
    try {
        showLoadingOverlay('Saving credentials and starting Tidal authentication...');
        // Save settings first to ensure credentials are persisted
        await saveSettings();
        showToast('Tidal authentication started', 'success');
        window.open('/auth/tidal', '_blank');
    } catch (error) {
        console.error('Error authenticating Tidal:', error);
        showToast('Failed to start Tidal authentication', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function disconnectTidal() {
    // Clear saved Tidal token. Use when re-authentication doesn't pick
    // up newly-added scopes (existing token predates a scope expansion
    // and `prompt=consent` alone isn't forcing fresh consent on this
    // user's auth flow). After disconnect, click Authenticate again
    // for a clean grant.
    if (!confirm('Disconnect Tidal? Saved token will be cleared and you\'ll need to re-authenticate.')) {
        return;
    }
    try {
        showLoadingOverlay('Disconnecting Tidal...');
        const resp = await fetch('/api/tidal/disconnect', { method: 'POST' });
        const data = await resp.json();
        if (resp.ok && data.success) {
            showToast('Tidal disconnected. Click Authenticate to reconnect with current scopes.', 'success');
        } else {
            showToast(`Disconnect failed: ${data.error || 'unknown error'}`, 'error');
        }
    } catch (error) {
        console.error('Error disconnecting Tidal:', error);
        showToast('Failed to disconnect Tidal', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function authenticateDeezer() {
    try {
        showLoadingOverlay('Saving credentials and starting Deezer authentication...');
        await saveSettings();
        showToast('Deezer authentication started', 'success');
        window.open('/auth/deezer', '_blank');
    } catch (error) {
        console.error('Error authenticating Deezer:', error);
        showToast('Failed to start Deezer authentication', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

// ===== Tidal Download Auth (Device Flow) =====

// ===============================
// QOBUZ AUTH FUNCTIONS
// ===============================

async function checkQobuzAuthStatus() {
    try {
        const resp = await fetch('/api/qobuz/auth/status');
        const data = await resp.json();

        // the Qobuz source panel is React (source-panels.tsx): it hears this
        window.dispatchEvent(new CustomEvent('soulsync:qobuz-auth', { detail: data }));

        // Update connections tab section
        const connFormEl = document.getElementById('qobuz-connection-form');
        const connLoggedInEl = document.getElementById('qobuz-connection-logged-in');
        const connUserInfoEl = document.getElementById('qobuz-connection-user-info');

        if (data.authenticated) {
            const user = data.user || {};
            const label = `Connected: ${user.display_name || 'Qobuz User'} (${user.subscription || 'Active'})`;

            if (connUserInfoEl) { connUserInfoEl.textContent = label; }
            if (connLoggedInEl) connLoggedInEl.style.display = 'flex';
            if (connFormEl) connFormEl.style.display = 'none';
        } else {
            if (connLoggedInEl) connLoggedInEl.style.display = 'none';
            if (connFormEl) connFormEl.style.display = 'block';
        }
    } catch (e) {
        console.error('Qobuz auth status check failed:', e);
    }
}

async function loginQobuzFromConnections() {
    const btn = document.getElementById('qobuz-connection-login-btn');
    const statusEl = document.getElementById('qobuz-connection-status');
    const email = document.getElementById('qobuz-connection-email').value.trim();
    const password = document.getElementById('qobuz-connection-password').value;

    if (!email || !password) {
        showToast('Please enter your Qobuz email and password', 'warning');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Connecting...';
    statusEl.textContent = '';

    try {
        const resp = await fetch('/api/qobuz/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });
        const data = await resp.json();

        if (data.success) {
            showToast('Qobuz connected successfully!', 'success');
            document.getElementById('qobuz-connection-password').value = '';
            checkQobuzAuthStatus();
        } else {
            statusEl.textContent = data.error || 'Login failed';
            statusEl.style.color = '#ff5555';
            showToast(data.error || 'Qobuz login failed', 'error');
        }
    } catch (error) {
        console.error('Qobuz login error:', error);
        statusEl.textContent = 'Connection error';
        statusEl.style.color = '#ff5555';
        showToast('Failed to connect to Qobuz', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Connect Qobuz';
    }
}

async function loginQobuzWithToken() {
    const btn = document.getElementById('qobuz-token-login-btn');
    const statusEl = document.getElementById('qobuz-token-status');
    const token = document.getElementById('qobuz-connection-token').value.trim();

    if (!token) {
        showToast('Please paste your Qobuz auth token', 'warning');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Connecting...';
    if (statusEl) statusEl.textContent = '';

    try {
        const resp = await fetch('/api/qobuz/auth/token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });
        const data = await resp.json();

        if (data.success) {
            showToast('Qobuz connected via token!', 'success');
            document.getElementById('qobuz-connection-token').value = '';
            checkQobuzAuthStatus();
        } else {
            if (statusEl) { statusEl.textContent = data.error || 'Token login failed'; statusEl.style.color = '#ff5555'; }
            showToast(data.error || 'Qobuz token login failed', 'error');
        }
    } catch (error) {
        console.error('Qobuz token login error:', error);
        if (statusEl) { statusEl.textContent = 'Connection error'; statusEl.style.color = '#ff5555'; }
        showToast('Failed to connect to Qobuz', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Connect with Token';
    }
}

async function logoutQobuz() {
    try {
        await fetch('/api/qobuz/auth/logout', { method: 'POST' });
        showToast('Qobuz disconnected', 'success');
        checkQobuzAuthStatus();
    } catch (e) {
        console.error('Qobuz logout error:', e);
    }
}

const PATH_INPUT_IDS = {
    // the music and podcast folders lock themselves in the React Folders card
    'video-download': 'video-download-path',   // the same setting, from the Video folders tab
};

// Deployment-aware folder-paths guidance (+ the fresh-install disk landmine).
// Docker installs must NOT touch the container paths; bare-metal/LXC installs
// MUST edit them. A non-Docker install still on the ./Transfer default is
// silently filling the install disk (a Proxmox LXC root is typically 8GB and
// hangs when full — reported live on Discord), so that state warns loudly.
function togglePathLock(pathType, btn) {
    const inputId = PATH_INPUT_IDS[pathType] || (pathType.endsWith('-path') ? pathType : pathType + '-path');
    const input = document.getElementById(inputId);
    if (!input) return;
    const isLocked = input.hasAttribute('readonly') || input.readOnly;
    if (isLocked) {
        input.removeAttribute('readonly');
        input.readOnly = false;
        input.focus();
        btn.textContent = 'Lock';
        btn.classList.remove('locked');
    } else {
        input.setAttribute('readonly', '');
        input.readOnly = true;
        btn.textContent = 'Unlock';
        btn.classList.add('locked');
    }
}

// Manually (re)build every "organize by playlist" folder from current library
// ownership — mirrors the automatic rebuild that runs after a playlist download.
// ===============================

// ============================================================================
// MEDIA-SERVER LIBRARY PICKERS
//
// Moved here verbatim from beatport-ui.js 3648-3931. They were never Beatport
// code — they drive this page's Plex / Jellyfin / Navidrome selects (the
// `onchange` handlers at index.html 4392, 4456, 4465, 4492) and are called from
// three places in this file. They only lived in beatport-ui.js because that is
// where somebody appended them.
//
// The move is a PRECONDITION for the sync port: that file is deleted when the
// sync page flips to React, and deleting it with these inside would break
// media-server setup for every user, on a page that has nothing to do with sync.
//
// Moved unchanged first, then the six `alert()` calls it carried were
// converted to showToast in a follow-up — matching the Navidrome path in this
// same block, which already used
// `showToast(msg, 'error', 'set-media')`. The move and the edit were kept
// apart on purpose: a move that also edits is a move you cannot verify.
// ============================================================================

// ============ Plex Music Library Selection ============

async function loadPlexMusicLibraries() {
    try {
        const response = await fetch('/api/plex/music-libraries');
        const data = await response.json();

        if (data.success && data.libraries && data.libraries.length > 0) {
            const selector = document.getElementById('plex-music-library');
            const container = document.getElementById('plex-library-selector-container');

            // Clear existing options
            selector.innerHTML = '';

            // Add options for each library. ``value`` is the canonical
            // identifier the backend expects (real libraries: title;
            // synthetic "All Libraries" entry: the sentinel string).
            // ``title`` stays the human-readable label.
            data.libraries.forEach(library => {
                const option = document.createElement('option');
                const optionValue = library.value || library.title;
                option.value = optionValue;
                option.textContent = library.title;

                // Pre-select match: compare ``value`` against the saved
                // DB pref (``data.selected``) AND ``title`` against the
                // live-active library name (``data.current``). Covers
                // both the sentinel case and the legacy single-library
                // case.
                if (optionValue === data.selected
                        || library.title === data.current
                        || library.title === data.selected) {
                    option.selected = true;
                }

                selector.appendChild(option);
            });

            // Show the container
            container.style.display = 'block';
        } else {
            // Hide if no libraries found or not connected
            document.getElementById('plex-library-selector-container').style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading Plex music libraries:', error);
        document.getElementById('plex-library-selector-container').style.display = 'none';
    }
}

async function selectPlexLibrary() {
    const selector = document.getElementById('plex-music-library');
    const selectedLibrary = selector.value;

    if (!selectedLibrary) return;

    try {
        const response = await fetch('/api/plex/select-music-library', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                library_name: selectedLibrary
            })
        });

        const data = await response.json();

        if (data.success) {
            console.log(`Plex music library switched to: ${selectedLibrary}`);
        } else {
            console.error('Failed to switch library:', data.error);
            showToast(`Failed to switch library: ${data.error}`, 'error', 'set-media');
        }
    } catch (error) {
        console.error('Error selecting Plex library:', error);
        showToast('Error selecting library. Please try again.', 'error', 'set-media');
    }
}

// ============ Jellyfin User Selection ============

async function loadJellyfinUsers() {
    try {
        const response = await fetch('/api/jellyfin/users');
        const data = await response.json();

        if (data.success && data.users && data.users.length > 0) {
            const selector = document.getElementById('jellyfin-user');
            const container = document.getElementById('jellyfin-user-selector-container');

            // Clear existing options
            selector.innerHTML = '';

            // Add options for each user
            data.users.forEach(user => {
                const option = document.createElement('option');
                option.value = user.name;
                option.textContent = user.name;

                // Mark the currently selected user
                if (user.name === data.current || user.name === data.selected) {
                    option.selected = true;
                }

                selector.appendChild(option);
            });

            // Show the container
            container.style.display = 'block';
        } else {
            // Hide if no users found or not connected
            document.getElementById('jellyfin-user-selector-container').style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading Jellyfin users:', error);
        document.getElementById('jellyfin-user-selector-container').style.display = 'none';
    }
}

async function selectJellyfinUser() {
    const selector = document.getElementById('jellyfin-user');
    const selectedUser = selector.value;

    if (!selectedUser) return;

    try {
        const response = await fetch('/api/jellyfin/select-user', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                username: selectedUser
            })
        });

        const data = await response.json();

        if (data.success) {
            console.log(`Jellyfin user switched to: ${selectedUser}`);
            // Refresh library dropdown for the new user
            loadJellyfinMusicLibraries();
        } else {
            console.error('Failed to switch user:', data.error);
            showToast(`Failed to switch user: ${data.error}`, 'error', 'set-media');
        }
    } catch (error) {
        console.error('Error selecting Jellyfin user:', error);
        showToast('Error selecting user. Please try again.', 'error', 'set-media');
    }
}

// ============ Jellyfin Music Library Selection ============

async function loadJellyfinMusicLibraries() {
    try {
        const response = await fetch('/api/jellyfin/music-libraries');
        const data = await response.json();

        if (data.success && data.libraries && data.libraries.length > 0) {
            const selector = document.getElementById('jellyfin-music-library');
            const container = document.getElementById('jellyfin-library-selector-container');

            // Clear existing options
            selector.innerHTML = '';

            // Add options for each library
            data.libraries.forEach(library => {
                const option = document.createElement('option');
                option.value = library.title;
                option.textContent = library.title;

                // Mark the currently selected library
                if (library.title === data.current || library.title === data.selected) {
                    option.selected = true;
                }

                selector.appendChild(option);
            });

            // Show the container
            container.style.display = 'block';
        } else {
            // Hide if no libraries found or not connected
            document.getElementById('jellyfin-library-selector-container').style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading Jellyfin music libraries:', error);
        document.getElementById('jellyfin-library-selector-container').style.display = 'none';
    }
}

async function selectJellyfinLibrary() {
    const selector = document.getElementById('jellyfin-music-library');
    const selectedLibrary = selector.value;

    if (!selectedLibrary) return;

    try {
        const response = await fetch('/api/jellyfin/select-music-library', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                library_name: selectedLibrary
            })
        });

        const data = await response.json();

        if (data.success) {
            console.log(`Jellyfin music library switched to: ${selectedLibrary}`);
        } else {
            console.error('Failed to switch library:', data.error);
            showToast(`Failed to switch library: ${data.error}`, 'error', 'set-media');
        }
    } catch (error) {
        console.error('Error selecting Jellyfin library:', error);
        showToast('Error selecting library. Please try again.', 'error', 'set-media');
    }
}

// ============ Navidrome Music Folder Selection ============

async function loadNavidromeMusicFolders() {
    try {
        const response = await fetch('/api/navidrome/music-folders');
        const data = await response.json();

        if (data.success && data.folders && data.folders.length > 0) {
            const selector = document.getElementById('navidrome-music-folder');
            const container = document.getElementById('navidrome-folder-selector-container');

            selector.innerHTML = '<option value="">All Libraries</option>';

            data.folders.forEach(folder => {
                const option = document.createElement('option');
                option.value = folder.title;
                option.textContent = folder.title;

                if (folder.title === data.current || folder.title === data.selected) {
                    option.selected = true;
                }

                selector.appendChild(option);
            });

            container.style.display = 'block';
        } else {
            document.getElementById('navidrome-folder-selector-container').style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading Navidrome music folders:', error);
        document.getElementById('navidrome-folder-selector-container').style.display = 'none';
    }
}

// the admin's own navidrome playlist user (Cremonies). it's the same
// per-profile login My Account saves for everyone else, so sync already writes
// as it. kept out of the main settings save on purpose, it's not app config.
function _paintNavidromePlaylistLogin(username) {
    const user = document.getElementById('navidrome-playlist-username');
    const pass = document.getElementById('navidrome-playlist-password');
    const status = document.getElementById('navidrome-playlist-status');
    const clear = document.getElementById('navidrome-playlist-clear');
    if (!user || !status) return;
    user.value = username || '';
    if (pass) pass.value = '';
    if (clear) clear.style.display = username ? '' : 'none';
    status.textContent = username
        ? `Your synced playlists go to ${username}.`
        : 'Your synced playlists go to this Navidrome user instead of the account above. '
          + 'Leave it empty to use the account above. Other profiles set their own in My Account.';
}

async function loadNavidromePlaylistLogin() {
    try {
        const response = await fetch('/api/profiles/me/server-library');
        const data = await response.json();
        _paintNavidromePlaylistLogin(data && data.success ? data.navidrome_username : '');
    } catch (error) {
        console.debug('navidrome playlist login load failed:', error);
    }
}

async function saveNavidromePlaylistLogin() {
    const username = (document.getElementById('navidrome-playlist-username')?.value || '').trim();
    const password = document.getElementById('navidrome-playlist-password')?.value || '';
    if (!username || !password) {
        showToast('Enter the playlist account username and password', 'warning');
        return;
    }
    try {
        const response = await fetch('/api/profiles/me/navidrome-login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            showToast(data.error || 'Could not save the playlist account', 'error');
            return;
        }
        _paintNavidromePlaylistLogin(data.username || username);
        showToast(`Playlists will sync to ${data.username || username}`, 'success');
    } catch (error) {
        showToast('Could not save the playlist account', 'error');
    }
}

async function clearNavidromePlaylistLogin() {
    try {
        const response = await fetch('/api/profiles/me/navidrome-login', { method: 'DELETE' });
        const data = await response.json();
        if (!response.ok || !data.success) {
            showToast(data.error || 'Could not clear the playlist account', 'error');
            return;
        }
        _paintNavidromePlaylistLogin('');
        showToast('Playlists will sync to the account above', 'success');
    } catch (error) {
        showToast('Could not clear the playlist account', 'error');
    }
}

async function selectNavidromeMusicFolder() {
    const selector = document.getElementById('navidrome-music-folder');
    const selectedFolder = selector.value;

    try {
        const response = await fetch('/api/navidrome/select-music-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_name: selectedFolder })
        });

        const data = await response.json();

        if (data.success) {
            showToast(data.message, 'success');
        } else {
            console.error('Failed to set music folder:', data.error);
            showToast(`Failed to set music folder: ${data.error}`, 'error', 'set-media');
        }
    } catch (error) {
        console.error('Error selecting Navidrome music folder:', error);
        showToast('Error selecting music folder. Please try again.', 'error', 'set-media');
    }
}


// ============================================================
// == YT-DLP UPDATER (Advanced tab, both sides)              ==
// ============================================================
// YouTube changes how it serves video far faster than yt-dlp cuts a stable
// release, so a copy a few weeks old starts answering "403 Forbidden" on videos
// that worked yesterday. Without an in-app update, those downloads quietly burn
// their retry budget and get abandoned for an entirely fixable reason.

// == ARTWORK CACHE (Advanced tab, both sides)               ==
// One cache serves album covers on the music side and posters on the video
// side, so this panel is deliberately not marked video-only.

function _imgCacheBytes(n) {
    if (!n) return '0 MB';
    const mb = n / (1024 * 1024);
    return mb >= 1024 ? (mb / 1024).toFixed(2) + ' GB' : mb.toFixed(1) + ' MB';
}

function _imgCacheStatus(message, tone) {
    const el = document.getElementById('imgcache-status');
    if (!el) return;
    el.textContent = message;
    el.style.display = 'block';
    el.style.background = tone === 'error' ? 'rgba(255,80,80,.12)' : 'rgba(80,255,150,.10)';
    el.style.color = tone === 'error' ? '#ff9a9a' : '#8ee7b0';
}

function onImageCacheSettingChanged() {
    if (typeof debouncedAutoSaveSettings === 'function') debouncedAutoSaveSettings();
}

async function loadImageCacheStatus() {
    try {
        const resp = await fetch('/api/image-cache/status');
        const data = await resp.json();
        if (!data.success) return;
        const entries = document.getElementById('imgcache-entries');
        const size = document.getElementById('imgcache-size');
        if (entries) {
            // A "pending" row is a registered URL nothing ever loaded. When it
            // dominates the count, the URLs being handed out are changing
            // between renders (a media-server auth salt used to rotate on every
            // call), and the entry count alone hides that completely.
            const pending = data.pending || 0;
            entries.textContent = pending
                ? `${(data.entries || 0).toLocaleString()} (${pending.toLocaleString()} pending)`
                : (data.entries || 0).toLocaleString();
        }
        if (size) {
            size.textContent = data.max_bytes
                ? `${_imgCacheBytes(data.bytes)} of ${_imgCacheBytes(data.max_bytes)}`
                : `${_imgCacheBytes(data.bytes)} (no limit)`;
        }
        // Reflect saved config without clobbering something the user is editing.
        const maxEl = document.getElementById('imgcache-max-mb');
        if (maxEl && document.activeElement !== maxEl) {
            maxEl.value = Math.round((data.max_bytes || 0) / (1024 * 1024));
        }
        const thumbEl = document.getElementById('imgcache-thumbnails');
        if (thumbEl) thumbEl.checked = data.thumbnails === true;
    } catch (e) {
        console.error('image cache status failed', e);
    }
}

async function runImageCachePrune() {
    const btn = document.getElementById('imgcache-prune-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Reclaiming...'; }
    try {
        const resp = await fetch('/api/image-cache/prune', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            const freed = (data.expired || 0) + (data.evicted || 0);
            _imgCacheStatus(freed
                ? `Removed ${freed} cached image${freed === 1 ? '' : 's'}.`
                : 'Nothing to reclaim — the cache is already within its limits.', 'ok');
            loadImageCacheStatus();
        } else {
            _imgCacheStatus(data.error || 'Could not reclaim space.', 'error');
        }
    } catch (e) {
        _imgCacheStatus('Could not reclaim space: ' + e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Reclaim space now'; }
    }
}

async function runImageCacheClear() {
    const ok = await showConfirmDialog({
        title: 'Clear the artwork cache?',
        message: 'Every cached cover and poster is removed. Nothing is lost permanently — '
               + 'images are downloaded again as pages need them, so the only cost is a '
               + 'slower first load.',
        confirmText: 'Clear cache',
        cancelText: 'Cancel'
    });
    if (!ok) return;

    const btn = document.getElementById('imgcache-clear-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Clearing...'; }
    try {
        const resp = await fetch('/api/image-cache/clear', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            _imgCacheStatus(`Cleared ${data.removed || 0} cached image${data.removed === 1 ? '' : 's'}.`, 'ok');
            loadImageCacheStatus();
        } else {
            _imgCacheStatus(data.error || 'Could not clear the cache.', 'error');
        }
    } catch (e) {
        _imgCacheStatus('Could not clear the cache: ' + e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Clear cache'; }
    }
}

// Sidebar Weather is React now: src/features/settings/tabs/appearance-tab.tsx

// == METASYNC (Advanced tab)               ==
// Connection to the local MetaSync node: pairing (or a pasted local API
// key), the pool key SoulSync holds and hands over, live status, and
// disconnect. All calls go to SoulSync's own /api/metasync endpoints — the
// server talks to the node, so the browser never has to reach it. Stored
// secrets are never sent back here: the key fields only ever carry what
// the user just typed, and an empty field on save means "unchanged".

async function _msApi(method, path, body) {
    const resp = await fetch('/api/metasync' + path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    return resp.json();
}

function _msStatus(text) {
    const el = document.getElementById('metasync-status');
    if (el) el.textContent = text;
}

function _msDetail(html) {
    const el = document.getElementById('metasync-status-detail');
    if (el) el.innerHTML = html;
}

function _msEscape(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
}

let _msAutoDetectTried = false;

// Ask SoulSync to probe the usual places for the MetaSync node. On a find,
// fill the URL field and return the URL; on no find, return '' and stay
// quiet — the manual field remains the fallback either way.
async function metasyncDetect() {
    try {
        const data = await _msApi('POST', '/detect');
        if (data && data.success && data.found && data.node_url) {
            const field = document.getElementById('metasync-node-url');
            if (field) field.value = data.node_url;
            return data.node_url;
        }
    } catch (e) {
        console.error('metasync detect failed', e);
    }
    return '';
}

// Auto-detect runs once per page load, and only while there is nothing to
// lose: no connection stored yet and no URL typed or saved. A find fills
// the field and says so; finding nothing changes nothing on screen.
async function _msMaybeAutoDetect(view) {
    if (_msAutoDetectTried || view.local_key_set) return;
    const field = document.getElementById('metasync-node-url');
    if (!field || field.value.trim()) return;
    _msAutoDetectTried = true;
    const found = await metasyncDetect();
    if (found) _msStatus('Found MetaSync on this machine at ' + found + ' — click Connect to pair with it.');
}

async function loadMetaSyncSettings() {
    try {
        const data = await _msApi('GET', '');
        if (!data || data.success !== true) {
            _msStatus('Could not load MetaSync settings');
            return;
        }
        const nodeUrl = document.getElementById('metasync-node-url');
        if (nodeUrl && document.activeElement !== nodeUrl) nodeUrl.value = data.node_url || '';
        const poolUrl = document.getElementById('metasync-pool-url');
        if (poolUrl && document.activeElement !== poolUrl) poolUrl.value = data.pool_url || '';
        const poolKey = document.getElementById('metasync-pool-key');
        if (poolKey) {
            poolKey.placeholder = data.pool_key_set
                ? 'Saved — type a new key only to replace it'
                : 'Your pool account key';
        }
        const state = document.getElementById('metasync-connect-state');
        if (state) {
            state.textContent = data.local_key_set
                ? 'Connected' + (data.node_fingerprint ? ' — MetaSync ID ' + data.node_fingerprint : '')
                : 'Not connected';
        }
        await refreshMetaSyncStatus();
        await _msMaybeAutoDetect(data);
    } catch (e) {
        console.error('metasync settings load failed', e);
        _msStatus('Could not load MetaSync settings');
    }
}

async function saveMetaSyncSettings() {
    const body = {
        node_url: document.getElementById('metasync-node-url')?.value.trim() || '',
        pool_url: document.getElementById('metasync-pool-url')?.value.trim() || '',
    };
    const poolKey = document.getElementById('metasync-pool-key')?.value.trim();
    if (poolKey) body.pool_key = poolKey;
    const localKey = document.getElementById('metasync-local-key')?.value.trim();
    if (localKey) body.local_api_key = localKey;
    try {
        const data = await _msApi('PUT', '/settings', body);
        if (data.success) {
            if (typeof showToast === 'function') showToast('MetaSync settings saved', 'success');
            const pk = document.getElementById('metasync-pool-key');
            if (pk) pk.value = '';
            const lk = document.getElementById('metasync-local-key');
            if (lk) lk.value = '';
            loadMetaSyncSettings();
        } else if (typeof showToast === 'function') {
            showToast(data.error || 'Could not save MetaSync settings', 'error');
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast('Could not save MetaSync settings: ' + e.message, 'error');
    }
}

async function metasyncPairRequest() {
    // persist the node URL with the request so claim/handoff use the same one
    let nodeUrl = document.getElementById('metasync-node-url')?.value.trim() || '';
    if (!nodeUrl) {
        // Nothing typed — try to find the node before asking the user for it.
        nodeUrl = await metasyncDetect();
    }
    if (!nodeUrl) {
        if (typeof showToast === 'function') showToast('Enter the MetaSync URL first', 'error');
        return;
    }
    try {
        const data = await _msApi('POST', '/pair/request', { node_url: nodeUrl });
        if (!data.success) {
            if (typeof showToast === 'function') showToast(data.error || 'Pairing request failed', 'error');
            return;
        }
        const box = document.getElementById('metasync-pair-box');
        if (box) box.style.display = '';
        const code = document.getElementById('metasync-pair-code');
        if (code) code.textContent = data.pairing_code || '';
        const fp = document.getElementById('metasync-pair-fingerprint');
        if (fp) fp.textContent = data.node_fingerprint || '';
        if (typeof showToast === 'function') showToast('Approve the pairing in the MetaSync dashboard', 'success');
    } catch (e) {
        if (typeof showToast === 'function') showToast('Pairing request failed: ' + e.message, 'error');
    }
}

async function metasyncPairClaim() {
    try {
        const data = await _msApi('POST', '/pair/claim');
        if (data.success) {
            if (typeof showToast === 'function') showToast('MetaSync connected', 'success');
            const box = document.getElementById('metasync-pair-box');
            if (box) box.style.display = 'none';
            loadMetaSyncSettings();
        } else if (typeof showToast === 'function') {
            showToast(data.error || 'Could not finish connecting', 'error');
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast('Could not finish connecting: ' + e.message, 'error');
    }
}

async function metasyncHandoff() {
    // unsaved field values ride along; the server persists what it used
    const body = {
        pool_url: document.getElementById('metasync-pool-url')?.value.trim() || '',
    };
    const poolKey = document.getElementById('metasync-pool-key')?.value.trim();
    if (poolKey) body.pool_key = poolKey;
    try {
        const data = await _msApi('POST', '/handoff', body);
        if (!data.success) {
            if (typeof showToast === 'function') showToast(data.error || 'Handoff failed', 'error');
            return;
        }
        if (data.exchange_status === 'bound') {
            if (typeof showToast === 'function') showToast('Pool key handed over — MetaSync is bound to the pool', 'success');
        } else if (data.exchange_status === 'binding_pending') {
            if (typeof showToast === 'function') showToast('Pool key handed over — waiting for approval in the portal', 'warning');
        } else if (data.exchange_status === 'error') {
            if (typeof showToast === 'function') showToast('Pool key handed over, but the pool exchange failed: ' + (data.exchange_error || 'unknown error'), 'error');
        } else {
            if (typeof showToast === 'function') showToast('Pool key handed over to MetaSync', 'success');
        }
        loadMetaSyncSettings();
    } catch (e) {
        if (typeof showToast === 'function') showToast('Handoff failed: ' + e.message, 'error');
    }
}

// The node's binding words, in plain language for the status readout.
const _MS_BINDING_TEXT = {
    bound: 'Bound to the pool',
    pending_approval: 'Waiting for approval in the portal',
    auth_failed: 'Pool authentication failed',
    unbound: 'Not bound yet',
    not_checked: 'Not checked yet',
    not_connected: 'No pool key on MetaSync',
    not_applicable: 'Direct key (no binding)',
};

async function refreshMetaSyncStatus() {
    try {
        const data = await _msApi('GET', '/status');
        if (!data || data.success !== true) {
            _msStatus((data && data.error) || 'MetaSync status unavailable');
            _msDetail('');
            return;
        }
        const s = data.status || {};
        _msStatus('Step 1: connected — MetaSync ' + (s.version || '') +
            (s.soulsync_connected ? ' is reading this SoulSync' : ' is not reading this SoulSync yet'));
        const lines = [];
        if (s.node_fingerprint) lines.push('MetaSync ID: ' + _msEscape(s.node_fingerprint) +
            ' (the same ID MetaSync shows in its dashboard)');
        const pools = Array.isArray(s.pools) ? s.pools : [];
        if (!pools.length) {
            lines.push('Step 2: no pool joined yet — send your pool key above to join one.');
        }
        pools.forEach(p => {
            const binding = _MS_BINDING_TEXT[p.binding_status] || p.binding_status || 'Unknown';
            let line = 'Pool — ' + _msEscape(p.name || p.id || 'Pool') + ' (' + _msEscape(p.url || '') + '): ' +
                _msEscape(binding);
            if (p.last_exchange_error) line += ' — last error: ' + _msEscape(p.last_exchange_error);
            lines.push(line);
        });
        // Step 3: is this install actually counted? A node starts in dry
        // run and sends nothing; an older node doesn't report dry_run at all
        const nodeUrl = document.getElementById('metasync-node-url')?.value.trim() || '';
        const openNode = /^https?:\/\//i.test(nodeUrl)
            ? ' <a href="' + _msEscape(nodeUrl) + '" target="_blank" rel="noopener noreferrer">Open MetaSync</a>'
            : '';
        if (s.dry_run === true && pools.length) {
            const u = s.unsent_report;
            lines.push(u && u.week
                ? 'Step 3: dry run is on. Your report for ' + _msEscape(u.week) + ' is ready (' +
                  _msEscape(u.signal_count) + ' anonymous album signals) and nothing was sent, so this install ' +
                  'isn\'t counted yet. Check it and press Start sending in MetaSync.' + openNode
                : 'Step 3: dry run is on, so MetaSync builds your weekly report but sends nothing yet.' + openNode);
        } else if (s.dry_run === false && pools.length) {
            lines.push('Step 3: sending. One anonymous report a week, nothing else.');
        }
        const net = s.network;
        if (net && Number.isInteger(net.installs) && Number.isInteger(net.publish_threshold)) {
            lines.push('The network: ' + net.installs + ' install' + (net.installs === 1 ? '' : 's') +
                ' reported ' + _msEscape(net.week || 'last week') + '. ' +
                (net.installs < net.publish_threshold
                    ? 'Charts from the network start at ' + net.publish_threshold +
                      ' installs per album; until then they fill from ListenBrainz.'
                    : 'An album charts once ' + net.publish_threshold + ' installs report it in a week.'));
        }
        if (s.pairing_status === 'pending') lines.push('A pairing request is waiting for approval in the MetaSync dashboard.');
        _msDetail(lines.join('<br>'));
    } catch (e) {
        _msStatus('MetaSync status unavailable');
        _msDetail('');
    }
}

async function metasyncDisconnect() {
    const ok = await showConfirmDialog({
        title: 'Disconnect MetaSync?',
        message: 'The pool key on MetaSync is cleared and the SoulSync key it holds is revoked. ' +
            'Your pool key stays saved here.',
        confirmText: 'Disconnect',
        cancelText: 'Cancel',
        destructive: true
    });
    if (!ok) return;
    try {
        const data = await _msApi('POST', '/disconnect');
        if (data.success) {
            if (typeof showToast === 'function') {
                showToast(data.node_notified
                    ? 'MetaSync disconnected'
                    : 'Disconnected here — MetaSync could not be reached, so clear its pool key from its dashboard too', 'success');
            }
            const box = document.getElementById('metasync-pair-box');
            if (box) box.style.display = 'none';
            loadMetaSyncSettings();
        } else if (typeof showToast === 'function') {
            showToast(data.error || 'Disconnect failed', 'error');
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast('Disconnect failed: ' + e.message, 'error');
    }
}

// MUSICBRAINZ SERVER SETTINGS
function loadMusicBrainzServerSettings(settings) {
    document.getElementById('musicbrainz-base-url').value = settings.musicbrainz?.base_url || '';
    const interval = Number(settings.musicbrainz?.request_interval ?? 1.05);
    document.getElementById('musicbrainz-rate-mode').value = interval === 0 ? 'adaptive' : 'fixed';
    document.getElementById('musicbrainz-request-rate').value = interval > 0
        ? Number((1 / interval).toPrecision(6)) : 10;
    updateMusicBrainzRateControls();
}

function updateMusicBrainzRateControls() {
    document.getElementById('musicbrainz-request-rate').disabled =
        document.getElementById('musicbrainz-rate-mode').value === 'adaptive';
}

function collectMusicBrainzServerSettings() {
    const base_url = document.getElementById('musicbrainz-base-url').value.trim();
    const adaptive = document.getElementById('musicbrainz-rate-mode').value === 'adaptive';
    const rawRate = document.getElementById('musicbrainz-request-rate').value.trim();
    const request_rate = Number(rawRate);
    let serverHost = 'musicbrainz.org';
    if (base_url) {
        let url;
        try { url = new URL(base_url); } catch (_) {
            throw new Error('MusicBrainz server URL must start with http:// or https://.');
        }
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
            throw new Error('Use a MusicBrainz HTTP(S) URL without credentials, query strings or fragments.');
        }
        serverHost = url.hostname.toLowerCase().replace(/\.$/, '');
    }
    if (adaptive && (serverHost === 'musicbrainz.org' || serverHost.endsWith('.musicbrainz.org'))) {
        throw new Error('No limit (adaptive) is only for a self-hosted MusicBrainz server.');
    }
    if (!adaptive && (!rawRate || !Number.isFinite(request_rate) || request_rate <= 0)) {
        throw new Error('MusicBrainz requests/second must be a positive number.');
    }
    const request_interval = adaptive ? 0 : 1 / request_rate;
    return { base_url, request_interval };
}
// END MUSICBRAINZ SERVER SETTINGS
