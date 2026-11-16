/**
 * The Settings page's own behaviour (SETTINGS_PORT.md step V4b): what was left
 * of settings.js once every tab was React. Opening the page (load, the first
 * tab), the tab switch, the search, the save status button and its save, the
 * keyboard on the section headers, Ctrl/Cmd+K, the password managers kept off
 * the credential fields, the masked secrets, and the Sources helpers other
 * pages call.
 *
 * The markup is still index.html's until the route is React (step V4c), so
 * this works on it exactly as settings.js did. It is in the shell bundle
 * (src/shell/index.ts SHELL_WINDOW_EXPORTS), a classic script that loads
 * before init.js, so its functions are on window as settings.js's were, for
 * init.js, the inline handlers and the pages that call them. It imports
 * nothing from the tabs (they are the deferred main bundle's): it reaches
 * them through window.SoulSyncSettings, as settings.js did.
 */

type SaveState = 'idle' | 'pending' | 'saving' | 'saved' | 'error';

interface Shell {
  _settingsPayload?: unknown;
  _settingsLoadFailed?: boolean;
  _suppressSettingsAutoSave?: boolean;
  _settingsAutosaveOff?: boolean;
  SoulSyncSettings?: {
    hydrate: (settings: unknown) => void;
    show: (tab: string) => void;
    flush: () => Promise<boolean>;
    templateErrors: () => string[];
    playlistItemError: () => string | null;
    musicbrainzError: () => string | null;
  };
  openSourceModal?: (id: string) => void;
  checkQobuzAuthStatus?: () => unknown;
  showToast?: (message: string, type: string, tag?: string) => void;
  showLoadingOverlay?: (message: string) => void;
  hideLoadingOverlay?: () => void;
  _forceServiceStatusRefresh?: () => void;
}
/** core.js's API.settings (a script const, not on window) */
const SETTINGS_URL = '/api/settings';
const shell = () => window as unknown as Shell & Record<string, unknown>;
/** showToast, with the tag only where the page passed one */
const toast = (message: string, type: string, tag?: string) =>
  tag === undefined ? shell().showToast?.(message, type) : shell().showToast?.(message, type, tag);

// ── the save status button (#1909) ──

// settings save themselves. the old Save Settings button and an "auto-saved"
// toast after every keystroke told two stories; the button is now the one
// quiet status, and still saves right away when clicked (the video side
// hangs its own saves on that click)
export const SETTINGS_SAVE_LABELS: Record<SaveState, string> = {
  idle: 'All changes saved',
  pending: 'Save now',
  saving: 'Saving…',
  saved: 'Saved',
  error: 'Retry save',
};
let savedTimer: ReturnType<typeof setTimeout> | null = null;

export function setSettingsSaveState(state: SaveState) {
  const btn = document.getElementById('save-settings');
  if (!btn || !SETTINGS_SAVE_LABELS[state]) return;
  if (savedTimer) {
    clearTimeout(savedTimer);
    savedTimer = null;
  }
  btn.dataset.state = state;
  const label = btn.querySelector('span');
  const manual = shell()._settingsAutosaveOff === true;
  if (label)
    label.textContent =
      manual && state === 'pending' ? 'Save changes' : SETTINGS_SAVE_LABELS[state];
  if (manual) btn.title = state === 'pending' ? 'Autosave is off. Click to save your changes.' : '';
  else
    btn.title =
      state === 'idle' || state === 'saved'
        ? 'Settings save as you go. Click to save again now.'
        : '';
  if (state === 'saved') savedTimer = setTimeout(() => setSettingsSaveState('idle'), 2000);
}

// ── the save ──

/**
 * The save button's save, and the one the pages' own code asks for. Every
 * control is React and saves through the store; this still refuses while
 * the settings never loaded or a rule says no, still posts the page's
 * sections that have nothing left in them (the server re-reads its services
 * on a settings save), and flushes the store.
 */
export async function saveSettings(quiet = false): Promise<void> {
  // #879: the form is showing defaults, not the user's config
  if (shell()._settingsLoadFailed) {
    if (!quiet)
      toast(
        "Settings didn't load — reload the page before saving (your config is untouched)",
        'error',
      );
    return;
  }
  const host = shell().SoulSyncSettings;
  // the MusicBrainz server fields (Connections) refuse the save while they
  // would not work
  const mbErr = host?.musicbrainzError?.();
  if (mbErr) {
    if (!quiet) toast(mbErr, 'error');
    return;
  }
  // a naming template that would not work (the Organization card)
  const validationErrors = host?.templateErrors?.() || [];
  if (validationErrors.length > 0) {
    if (!quiet) toast('Template validation failed: ' + validationErrors.join(', '), 'error');
    return;
  }
  // the optional Playlist File Naming template: toasted even when quiet
  const plItemErr = host?.playlistItemError?.();
  if (plItemErr) {
    toast(plItemErr, 'error');
    return;
  }

  // what saveSettings still posted once every control was React: the
  // sections with nothing left in them
  const settings = {
    soulseek: {},
    tidal_download: {},
    hifi_download: {},
    soundcloud_download: {},
    library: {},
    playlists: {},
    m3u_export: {},
  };

  try {
    if (!quiet) shell().showLoadingOverlay?.('Saving settings...');
    setSettingsSaveState('saving');
    const response = await fetch(SETTINGS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    const result = (await response.json()) as { success?: boolean; error?: string };
    // the React tabs hold their own values and save only what changed: the
    // save button reaches them here, and so do their changes with autosave
    // switched off
    let reactSaved = true;
    try {
      if (host) reactSaved = await host.flush();
    } catch (error) {
      console.error('Error saving React settings tabs:', error);
      reactSaved = false;
    }
    if (result.success && !reactSaved) {
      setSettingsSaveState('error');
      toast('Some settings failed to save', 'error', 'set-services');
    } else if (result.success) {
      setSettingsSaveState('saved');
      shell()._forceServiceStatusRefresh?.();
    } else {
      setSettingsSaveState('error');
      toast(`Failed to save settings: ${result.error}`, 'error', 'set-services');
    }
  } catch (error) {
    console.error('Error saving settings:', error);
    setSettingsSaveState('error');
    toast('Failed to save settings', 'error', 'set-services');
  } finally {
    if (!quiet) shell().hideLoadingOverlay?.();
  }
}

/** a modal closing lands an edit still waiting on the autosave: every
 *  control is React and its store lands its own, so nothing waits here */
export function landPendingSettingsSave() {
  // (settings.js held a page-wide autosave timer; no field starts it now)
}

// ── the load ──

// Mirrors ConfigManager.REDACTED_SENTINEL: configured secrets come back as
// this placeholder (rendered as dots in a password input)
export const REDACTED_SECRET_SENTINEL = '__redacted_unchanged__';

/** a password field outside the React tabs (another page's) clears the mask
 *  on focus and puts it back when left untouched, so a save round-trips it
 *  and the real secret is kept. a React field does this itself (SecretInput) */
export function wireRedactedSecrets(root: ParentNode = document) {
  for (const el of root.querySelectorAll<HTMLInputElement>('input[type="password"]')) {
    if (el.dataset.redactWired === '1') continue;
    if (el.closest('[data-stg-react]') && !el.closest('.src-config-panel:not([data-react-panel])'))
      continue;
    if (el.closest('.api-service-frame[data-react-panel]')) continue;
    el.dataset.redactWired = '1';
    el.addEventListener('focus', () => {
      if (el.value === REDACTED_SECRET_SENTINEL) {
        el.value = '';
        el.dataset.wasRedacted = '1';
      }
    });
    el.addEventListener('blur', () => {
      if (el.dataset.wasRedacted === '1' && el.value === '') {
        el.value = REDACTED_SECRET_SENTINEL;
        el.dataset.wasRedacted = '';
      }
    });
    // real typing is a real change or clear: blur won't mask it again
    el.addEventListener('input', () => {
      el.dataset.wasRedacted = '';
    });
  }
}

export async function loadSettingsData(): Promise<void> {
  try {
    const response = await fetch(SETTINGS_URL);
    const settings = (await response.json()) as Record<string, unknown> | null;
    // #879: a failed GET returns an error body, not settings. Saving from a
    // form filled with it would wipe the config: block saves until a good load
    if (!response.ok || !settings || typeof settings !== 'object' || settings.error) {
      shell()._settingsLoadFailed = true;
      throw new Error(
        `settings load failed (HTTP ${response.status}): ${
          typeof settings?.error === 'string' && settings.error
            ? settings.error
            : 'unexpected response'
        }`,
      );
    }
    shell()._settingsLoadFailed = false;
    shell()._settingsPayload = settings;
    shell().SoulSyncSettings?.hydrate(settings);

    // autosave, on unless switched off. the form was just reloaded, so an
    // unsaved mark left over from a manual-save visit is stale
    const appearance = settings.ui_appearance as { settings_autosave?: boolean } | undefined;
    shell()._settingsAutosaveOff = !(appearance?.settings_autosave !== false);
    if (document.getElementById('save-settings')?.dataset.state === 'pending')
      setSettingsSaveState('idle');

    // dev mode: the Hydrabase page's link, and the dashboard's orb and the
    // System tab follow the event
    try {
      const devResponse = await fetch('/api/dev-mode');
      const devData = (await devResponse.json()) as { enabled?: boolean };
      if (devData.enabled) {
        window.dispatchEvent(new CustomEvent('ss:dev-mode', { detail: { enabled: true } }));
        const nav = document.getElementById('hydrabase-nav');
        if (nav) nav.style.display = '';
      }
    } catch (error) {
      console.error('Error checking dev mode:', error);
    }

    // secrets arrive masked (#832 follow-up)
    wireRedactedSecrets();
  } catch (error) {
    console.error('Error loading settings:', error);
    // #879: a failed load blocks saves until the next good one
    shell()._settingsLoadFailed = true;
    toast(
      'Failed to load settings — reload the page before saving (your saved config is untouched)',
      'error',
    );
  }
}

// ── opening the page ──

/** password managers re-scan the whole (large) page on every change when
 *  they take its API key fields for logins: tell them to skip them */
export function markCredentialFieldsNoAutofill(root: ParentNode = document) {
  for (const el of root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(
    'input, textarea',
  )) {
    if (el.dataset.bwignore !== undefined) continue; // already tagged
    el.setAttribute('data-bwignore', ''); // Bitwarden
    el.setAttribute('data-1p-ignore', ''); // 1Password
    el.setAttribute('data-lpignore', 'true'); // LastPass
    if (!el.getAttribute('autocomplete')) el.setAttribute('autocomplete', 'off');
  }
}

/** the page, each time it is opened: the password managers kept off its
 *  fields. (the save button is the page's own, settings-page.tsx; every
 *  control is React and saves through its store: the page's own autosave
 *  listened to no field any more but the search box, where each letter typed
 *  queued a whole save. it is gone, SETTINGS_PORT.md step V4b) */
export function initializeSettings() {
  const page = document.getElementById('settings-page');
  if (page) markCredentialFieldsNoAutofill(page);
}

/** a tab asked for while the page is not on screen (a caller navigates to
 *  Settings, then switches tab after a moment): the page opens on it */
let pendingTab: string | null = null;

/** what opening Settings does each visit, as init.js did for the vanilla
 *  page: the autosave held off while the form fills, the first tab, the load */
export async function openSettingsPage() {
  const w = shell();
  w._suppressSettingsAutoSave = true;
  try {
    initializeSettings();
    const tab = pendingTab ?? 'connections';
    pendingTab = null;
    switchSettingsTab(tab);
    await loadSettingsData();
    (w.loadBlacklistCount as (() => void) | undefined)?.();
  } finally {
    w._suppressSettingsAutoSave = false;
  }
}

// ── the tabs ──

export const TAB_TITLES: Record<string, { title: string; sub: string }> = {
  connections: {
    title: 'Connections',
    sub: 'Media servers, accounts, Soulseek, MetaSync and notifications',
  },
  sources: { title: 'Sources', sub: 'Configure and prioritize acquisition sources and indexers' },
  downloads: {
    title: 'Downloads',
    sub: 'Queue behavior, retries, publishing, and what gets filtered out',
  },
  quality: { title: 'Quality', sub: 'Audio and video formats, release profiles, and ladders' },
  library: { title: 'Library', sub: 'Folders, file organization, tagging, and import' },
  discovery: {
    title: 'Discovery',
    sub: 'Playlists, listening stats, recommendations and import lists',
  },
  appearance: {
    title: 'Appearance',
    sub: 'Visual themes, accents, animations, and the sidebar weather',
  },
  advanced: {
    title: 'System',
    sub: 'Security, database tools, caches, logging, the API and experimental features',
  },
  logs: { title: 'Logs', sub: 'Live streaming logs and real-time operational diagnostics' },
};

export function switchSettingsTab(tab: string) {
  if (!document.getElementById('settings-page')) {
    pendingTab = tab;
    return;
  }
  const search = document.getElementById('stg-search-input') as HTMLInputElement | null;
  const results = document.getElementById('stg-search-results');
  if (search) search.value = '';
  if (results) results.hidden = true;
  if (tab === 'library') document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
  for (const t of document.querySelectorAll<HTMLElement>('#settings-page .stg-tab')) {
    const active = t.dataset.tab === tab;
    t.classList.toggle('active', active);
    if (active) t.setAttribute('aria-current', 'page');
    else t.removeAttribute('aria-current');
  }
  // the tabs render their [data-stg] blocks into their slots before the
  // blocks are shown and hidden by tab just below (host.tsx)
  try {
    shell().SoulSyncSettings?.show(tab);
  } catch (error) {
    console.error('settings tab mount failed', error);
  }
  for (const g of document.querySelectorAll<HTMLElement>('#settings-page [data-stg]'))
    g.style.display = g.dataset.stg === tab ? '' : 'none';
  // a tab switch resets inline display: collapsed bodies stay collapsed
  for (const b of document.querySelectorAll<HTMLElement>(
    '#settings-page .settings-section-body.collapsed',
  ))
    b.style.display = 'none';
  // a column with nothing to show on this tab goes too
  for (const col of document.querySelectorAll<HTMLElement>(
    '#settings-page .settings-left-column, #settings-page .settings-right-column, #settings-page .settings-third-column',
  )) {
    const visible = [...col.querySelectorAll<HTMLElement>('.settings-group[data-stg]')].some(
      (g) => g.style.display !== 'none',
    );
    col.style.display = visible ? '' : 'none';
  }
  const titleEl = document.getElementById('stg-active-title');
  const subEl = document.getElementById('stg-active-sub');
  if (titleEl && TAB_TITLES[tab]) titleEl.textContent = TAB_TITLES[tab].title;
  if (subEl && TAB_TITLES[tab]) subEl.textContent = TAB_TITLES[tab].sub;
}

// ── the search ──

/** labels and help only, never saved values or credentials. a result opens
 *  its tab rather than showing hidden, unloaded forms */
export function filterSettings(query: string) {
  const results = document.getElementById('stg-search-results');
  if (!results) return;
  const q = (query || '').trim().toLowerCase();
  results.replaceChildren();
  results.hidden = !q;
  if (!q) return;
  const matches: HTMLElement[] = [];
  for (const section of document.querySelectorAll<HTMLElement>(
    '#settings-page .settings-group[data-stg], #settings-page .settings-section-body[data-stg]',
  )) {
    if (!(section.textContent || '').toLowerCase().includes(q)) continue;
    if (matches.some((item) => item.contains(section))) continue;
    matches.push(section);
  }
  if (!matches.length) {
    const empty = document.createElement('p');
    empty.setAttribute('role', 'status');
    empty.textContent = 'No settings found. Try a service name, folder, or feature.';
    results.append(empty);
  }
  for (const section of matches.slice(0, 10)) {
    const category = section.dataset.stg || '';
    const tab = document.querySelector(`#settings-page .stg-tab[data-tab="${category}"]`);
    const heading =
      section.querySelector('h3') || section.previousElementSibling?.querySelector('h3');
    const title = heading?.textContent?.trim() || category;
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `${tab?.textContent?.trim() || category} — ${title}`;
    button.addEventListener('click', () => {
      switchSettingsTab(category);
      if (section.classList.contains('settings-section-body')) {
        section.classList.remove('collapsed');
        section.style.display = '';
        const header = section.previousElementSibling;
        header?.classList.remove('collapsed');
        header?.setAttribute('aria-expanded', 'true');
      }
      const label = [...section.querySelectorAll('label, h4')].find((l) =>
        (l.textContent || '').toLowerCase().includes(q),
      );
      const panel = label?.closest('[role="tabpanel"]');
      if (panel) {
        // the Library media cards are React: a click moves their own state
        document.querySelector<HTMLElement>(`[aria-controls="${panel.id}"]`)?.click();
      }
      section.tabIndex = -1;
      section.focus({ preventScroll: true });
      section.scrollIntoView({ block: 'start', behavior: 'auto' });
      section.classList.add('stg-search-target');
      window.setTimeout(() => section.classList.remove('stg-search-target'), 2000);
    });
    results.append(button);
  }
}

// ── the Sources helpers other pages and the chain call ──

/** the src id → the config panel that belongs to it */
export const SOURCE_CONFIG_ID_BY_SRC: Record<string, string> = {
  soulseek: 'soulseek-settings-container',
  youtube: 'youtube-settings-container',
  tidal: 'tidal-download-settings-container',
  qobuz: 'qobuz-settings-container',
  hifi: 'hifi-download-settings-container',
  deezer_dl: 'deezer-download-settings-container',
  lidarr: 'lidarr-download-settings-container',
  soundcloud: 'soundcloud-download-settings-container',
  torrent: 'torrent-client-settings-container',
  usenet: 'usenet-client-settings-container',
  prowlarr: 'prowlarr-settings-container',
};

/** a source's live check, run when its modal opens (best-effort) */
const SOURCE_OPEN_PROBE: Record<string, () => unknown> = {
  // the Connections tab's (connections.ts), on window from the settings host
  qobuz: () => shell().checkQobuzAuthStatus?.(),
};
export function probeSourceOnOpen(srcId: string) {
  const probe = SOURCE_OPEN_PROBE[srcId];
  if (!probe) return;
  try {
    void probe();
  } catch {
    // the modal still opens
  }
}

/** a source's panel, by its container id: its tile's modal */
export function openSourceConfig(containerId: string) {
  const srcId = Object.keys(SOURCE_CONFIG_ID_BY_SRC).find(
    (k) => SOURCE_CONFIG_ID_BY_SRC[k] === containerId,
  );
  if (srcId) shell().openSourceModal?.(srcId);
}

/** kept so an older cached page cannot throw: tiles replaced the row cards */
export function toggleSourceCard(header: HTMLElement | null) {
  if (header && header.dataset) shell().openSourceModal?.(header.dataset.sourceId || '');
}

// ── keyboard ──

/** the section headers are role="button": Enter and Space toggle them, and
 *  aria-expanded follows; Ctrl/Cmd+K goes to the search */
function wireKeyboard() {
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    const header = (e.target as Element | null)?.closest?.<HTMLElement>(
      '#settings-page .settings-section-header[role="button"]',
    );
    if (!header) return;
    e.preventDefault(); // Space would scroll the page
    header.click(); // its own toggle: one behaviour, not two
  });
  document.addEventListener('click', (e) => {
    const header = (e.target as Element | null)?.closest?.<HTMLElement>(
      '#settings-page .settings-section-header[role="button"]',
    );
    if (!header) return;
    // after the header's own handler, so it reports the state it ended in
    requestAnimationFrame(() =>
      header.setAttribute(
        'aria-expanded',
        header.classList.contains('collapsed') ? 'false' : 'true',
      ),
    );
  });
  document.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || (e.key || '').toLowerCase() !== 'k') return;
    const page = document.getElementById('settings-page');
    const input = document.getElementById('stg-search-input') as HTMLInputElement | null;
    if (!page || !input || !page.offsetParent) return;
    e.preventDefault();
    input.focus();
    input.select();
  });
}

// ── at load ──

/** what settings.js did as it loaded: the keyboard, and the password
 *  managers kept off every page's fields */
export function startSettingsShell() {
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', () => markCredentialFieldsNoAutofill());
  else markCredentialFieldsNoAutofill();
  wireKeyboard();
}
