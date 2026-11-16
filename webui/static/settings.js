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

// syncOidcProviderBlock: Security & Access is React (system-security.tsx)

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
    // a React service card lifted into the Services modal is out of its slot
    if (_t?.closest?.('.api-service-frame[data-react-panel]')) return;
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

// the metadata source picker and its rules, the Experimental switches and
// their confirm modal: React (connections-metadata.tsx, connections.ts,
// SETTINGS_PORT.md step 10a)


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

    // the metadata source picker follows /status and the Discogs token itself
    // (connections-metadata.tsx)

    // the server toggles and Auto-detect buttons are React (connections-servers.tsx)

    // Test connection buttons
    // Test button event listeners removed - they use onclick attributes in HTML to avoid double firing

}

// resetFileOrganizationTemplates and validateFileOrganizationTemplates: the
// Library tab's Organization card is React (library-organization.tsx,
// organization.ts)

// ── Connections: services as tiles ─────────────────────────────────────────
// The Music / Video tabs, the tiles and the modal a tile opens are React
// (src/features/settings/tabs/connections-services.tsx, SETTINGS_PORT.md step
// 10a). window.buildServiceTiles, switchServiceKind, openServiceModal and
// closeServiceModal reach them from here and other pages.

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
    // the artwork cache status loads itself on arriving at System (system-tab.tsx)
    // #1822: MetaSync lives on Connections. (sidebar weather, on Appearance,
    // reloads itself each time its React tab is opened)
    if (tab === 'connections' && typeof loadMetaSyncSettings === 'function') {
        try { loadMetaSyncSettings(); } catch (e) { }
    }
    // (the download chain and the Sources tiles re-read the video chain and
    // fill their dots themselves when their tab opens: sources-tab.tsx)
    // (the live log viewer starts and stops itself: its React tab is told
    // whether it is the open tab, src/features/settings/host.tsx)
    // (the service cards' header gradient went with step 10b: nothing showed
    // those headers, the group is hidden and the Services modal hides them)

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


// ── Settings → Connections: the service cards ──
// The music cards are React (connections-metadata.tsx, connections-extras.tsx,
// SETTINGS_PORT.md steps 10a and 10b), and their header gradient and verify
// calls are gone: nothing showed those headers. The video side's cards are
// still vanilla, and their hidden headers still call this.
function toggleStgService(el) {
    el.closest('.stg-service')?.classList.toggle('expanded');
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

        // Spotify, Tidal and Deezer (and their redirect lines): React
        // (connections-metadata.tsx, SETTINGS_PORT.md step 10a)

        // Plex, Jellyfin, Navidrome (its playlist account too), Music Assistant,
        // the active server and each server's library picker: React
        // (connections-servers.tsx, SETTINGS_PORT.md step 10c)

        // Populate Soulseek settings
        // the rest of soulseek.*: its Sources panel is React (source-panels.tsx)

        // ListenBrainz, AcoustID, Concerts, Last.fm (its authorized line too),
        // Genius, Pollinations and fanart.tv: React (connections-extras.tsx, step 10b)

        // iTunes, Discogs, the metadata source, the Spotify (no auth)
        // enrichment switch, Experimental and Hydrabase (its connection
        // included): React (connections-metadata.tsx, step 10a)

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
        // the Connections tab's Deezer card and the Deezer source panel hold
        // the same ARL (deezer_download.arl), both React

        // youtube.* and download_source.youtube_album_bundle: the YouTube source
        // panel is React (source-panels-more.tsx)

        // Populate Database settings
        // max_workers: the System tab is React (system-tab.tsx)

        // Post-Processing: React (library-processing.tsx)
        // MusicBrainz server: React (connections-metadata.tsx)
        // File Organization and every template: the Organization card is React
        // (library-organization.tsx, library-audiobooks.tsx)
        // the audiobook settings: the Organization card is React (library-audiobooks.tsx)
        // the naming options, tag options and wishlist rules: the Organization card is React
        // MCP and the Lidarr compatible API: the System tab is React (system-tab.tsx)

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

        // Security & Access: the System tab is React (system-security.tsx)

        // Check dev mode status
        try {
            const devResponse = await fetch('/api/dev-mode');
            const devData = await devResponse.json();
            if (devData.enabled) {
                // Re-broadcast for the React dashboard's Hydrabase orb.
                // (the System tab's dev mode status follows it too, system-tab.tsx)
                window.dispatchEvent(new CustomEvent('ss:dev-mode', { detail: { enabled: true } }));
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
        // the service tiles read the cards again after each load themselves
        // (connections-services.tsx)

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
        if (el.closest('.api-service-frame[data-react-panel]')) return;
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

// the Plex setup and PIN link, the server toggles, the media server names:
// React (connections-servers.tsx, SETTINGS_PORT.md step 10c)

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


// ── Music Library Paths ──
// the extra audiobook folders: React (library-audiobooks.tsx)

// ── Sample Studio folders ──
// Mirrors the additional-music-folders UI above: a dynamic list of output
// folders for saved chops. The first entry is the default destination.

// the spoken-word artists list: React (library-audiobooks.tsx)

// The genre whitelist is React now: src/features/settings/tabs/downloads-tab.tsx

// The live log viewer is React now: src/features/settings/tabs/logs-tab.tsx

// Database Maintenance: the System tab is React (system-tab.tsx)

// activateDevMode: the System tab is React (system-tab.tsx)

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
    // Orb visibility rides the dispatch (React-owned since the flip), and so
    // does the System tab's dev mode status (system-tab.tsx)
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

    // the MusicBrainz server fields refuse the save while they would not
    // work. they are React (connections-metadata.tsx), which holds its own
    // saves back too
    const _mbErr = window.SoulSyncSettings?.musicbrainzError?.();
    if (_mbErr) {
        if (!quiet) showToast(_mbErr, 'error');
        return;
    }

    // a naming template that would not work refuses the save. the templates
    // are the Organization card's (React), which holds its own saves back too
    const validationErrors = window.SoulSyncSettings?.templateErrors?.() || [];
    if (validationErrors.length > 0) {
        if (!quiet) showToast('Template validation failed: ' + validationErrors.join(', '), 'error');
        return;
    }

    // the active server is React and saves itself (connections-servers.tsx);
    // the video side opens a server's panel there without changing it

    // the metadata source: React (connections-metadata.tsx). A source that
    // cannot be picked switches as soon as it cannot, so a save never sends one

    // the optional "Playlist File Naming" template: a filename (no path
    // separator) that includes $title, as the server requires. The field is
    // the Organization card's (React), which holds its own saves back too
    const _plItemErr = window.SoulSyncSettings?.playlistItemError?.();
    if (_plItemErr) {
        showToast(_plItemErr, 'error');
        return;
    }

    const settings = {
        // active_media_server, plex, jellyfin, music_assistant and navidrome:
        // React (connections-servers.tsx, step 10c)
        // spotify, tidal (client_id, client_secret, redirect_uri): React
        // (connections-metadata.tsx); embed_tags and tags: the Post-Processing card
        soulseek: {
            // slskd_url, api_key and the search, peer, timeout and cleanup
            // keys: the Soulseek source panel is React (source-panels.tsx)
            // download_path, transfer_path, min_free_disk_gb: the Folders card is React
            // preferred_version: the Downloads tab (Filtering) is React
        },
        // listenbrainz, acoustid, concerts, lastfm (username, api_key, api_secret,
        // scrobble_enabled), fanart, pollinations and genius: React
        // (connections-extras.tsx, step 10b); the tags of each: the Post-Processing card
        // itunes, discogs, metadata, experimental and hydrabase: React
        // (connections-metadata.tsx)
        // image_cache: the System tab is React (system-tab.tsx)
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
        // musicbrainz (base_url, request_interval): React (connections-metadata.tsx);
        // embed_tags, use_original_date_for_date and tags: the Post-Processing card
        // deezer (app_id, app_secret, redirect_uri): React (connections-metadata.tsx)
        // audiodb (embed_tags, tags): the Post-Processing card is React
        // file_organization: the Organization card is React
        // mcp and lidarr_compat: the System tab is React (system-tab.tsx)
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
        // security: Security & Access is React (system-security.tsx), with
        // the cors_origins warning
    };

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


// the deezer card holds two logins: the oauth app (favorites, playlists) and an
// ARL (playlists + downloads). testConnection('deezer') covers the public api and
// the oauth token; the ARL has its own check, so run both and say what each found.


// ======================== API Key Management ========================

// the Lidarr profile list and the API keys: the System tab is React (system-tab.tsx)

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

// the media server Auto-detect buttons: React (connections-servers.tsx)


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


// ===== Tidal Download Auth (Device Flow) =====

// ===============================
// QOBUZ AUTH FUNCTIONS
// ===============================


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

// the media server library pickers (Plex, Jellyfin, Navidrome) and the
// Navidrome playlist account: React (connections-servers.tsx, step 10c)

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

// the artwork cache: the System tab is React (system-tab.tsx)

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

// the MusicBrainz server settings: React (connections-metadata.tsx, connections.ts)
