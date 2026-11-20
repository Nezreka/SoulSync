/**
 * Connections > Server Connections (SETTINGS_PORT.md, step 10c): the music
 * media server. The five toggles, Plex (its setup, Link to Plex by PIN,
 * re-link, manual setup, clear), Jellyfin, Navidrome (and the admin's own
 * playlist account), Music Assistant and Standalone, each server's library
 * picker, and Test Server. Also settings.js testConnection, which every
 * card's Test button uses: a good Plex, Jellyfin or Navidrome test reloads
 * that server's library picker.
 *
 * The libraries, the Jellyfin user, the Navidrome folder, the Music
 * Assistant source and the playlist account save through their own
 * endpoints, as they did. The connection fields are settings keys. Which
 * server is active is the top-level active_media_server, saved on its own.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

import type { SettingsStore } from '../settings-store';

import { settingsStore } from '../settings-store';
import { useLoadedDraft } from './library-card';
import { SecretInput } from './parts';
import { text, useSettings } from './source-panels';

type Section_ = Record<string, unknown>;
type Shell = Window & {
  showToast?: (message: string, type?: string, context?: string) => void;
  showLoadingOverlay?: (message?: string) => void;
  hideLoadingOverlay?: () => void;
  landPendingSettingsSave?: () => void;
  _settingsAutosaveOff?: boolean;
};
const shell = () => window as Shell;
const toast = (message: string, type: string, context?: string) =>
  shell().showToast?.(message, type, context);

async function json<T = Record<string, unknown>>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  return (await response.json()) as T;
}
const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});

function useLoads(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().loads);
}

// ── the library pickers hear a good test ──

const reloads = new Set<(service: string) => void>();
function onReload(listener: (service: string) => void) {
  reloads.add(listener);
  return () => {
    reloads.delete(listener);
  };
}

/**
 * settings.js testConnection: what is waiting to save goes first, so the
 * server tests what is on screen; the answer is a toast (a pass carrying a
 * warning reads as one). A good media server test reloads its library picker.
 */
export async function testConnection(service: string, store: SettingsStore = settingsStore) {
  const w = shell();
  try {
    w.landPendingSettingsSave?.();
    if (w._settingsAutosaveOff !== true && store.hasPending()) await store.flush();
    w.showLoadingOverlay?.(`Testing ${service} connection...`);
    const result = await json<{ success?: boolean; message?: string; error?: string }>(
      '/api/test-connection',
      post({ service }),
    );
    if (result.success) {
      const warned = String(result.message || '')
        .trim()
        .startsWith('⚠');
      toast(result.message || `${service} connection successful`, warned ? 'warning' : 'success');
      for (const reload of reloads) reload(service);
    } else {
      toast(`${service} connection failed: ${result.error}`, 'error', 'gs-connecting');
    }
  } catch (error) {
    console.error(`Error testing ${service} connection:`, error);
    toast(`Failed to test ${service} connection`, 'error', 'gs-connecting');
  } finally {
    w.hideLoadingOverlay?.();
  }
}

// ── the servers ──

const SERVERS = ['plex', 'jellyfin', 'navidrome', 'music_assistant', 'soulsync'] as const;
type Server = (typeof SERVERS)[number];

/** a picker filled from its server: hidden until it has something */
interface Picker {
  shown: boolean;
  options: Array<{ value: string; label: string }>;
  value: string;
}
const NO_PICKER: Picker = { shown: false, options: [], value: '' };

/** a list answer to a picker: settings.js's load*Libraries functions */
function pickerFrom(
  items: Array<Record<string, unknown>> | undefined,
  data: Record<string, unknown>,
  value: (item: Record<string, unknown>) => string,
  label: (item: Record<string, unknown>) => string,
  head: Array<{ value: string; label: string }> = [],
  matches: (item: Record<string, unknown>) => boolean = (item) =>
    label(item) === data.current || label(item) === data.selected,
): Picker {
  if (!data.success || !items?.length) return NO_PICKER;
  const options = [...head, ...items.map((i) => ({ value: value(i), label: label(i) }))];
  // the last match wins, as a run of option.selected = true did
  let picked = head.length ? head[0].value : options[0].value;
  for (const item of items) if (matches(item)) picked = value(item);
  return { shown: true, options, value: picked };
}

function usePicker(load: () => Promise<Picker>) {
  const [picker, setPicker] = useState<Picker>(NO_PICKER);
  const reload = async () => {
    try {
      setPicker(await load());
    } catch (error) {
      console.error('Error loading a media server picker:', error);
      setPicker(NO_PICKER);
    }
  };
  return { picker, setPicker, reload };
}

const loadPlexLibraries = async (): Promise<Picker> => {
  const data = await json<Record<string, unknown>>('/api/plex/music-libraries');
  const items = data.libraries as Array<Record<string, unknown>> | undefined;
  // value: what the backend expects (a title, or the All Libraries sentinel)
  const value = (l: Record<string, unknown>) => text(l.value) || text(l.title);
  return pickerFrom(
    items,
    data,
    value,
    (l) => text(l.title),
    [],
    (l) =>
      [value(l) === data.selected, l.title === data.current, l.title === data.selected].some(
        Boolean,
      ),
  );
};
const loadJellyfinUsers = async (): Promise<Picker> => {
  const data = await json<Record<string, unknown>>('/api/jellyfin/users');
  const items = data.users as Array<Record<string, unknown>> | undefined;
  return pickerFrom(
    items,
    data,
    (u) => text(u.name),
    (u) => text(u.name),
  );
};
const loadJellyfinLibraries = async (): Promise<Picker> => {
  const data = await json<Record<string, unknown>>('/api/jellyfin/music-libraries');
  const items = data.libraries as Array<Record<string, unknown>> | undefined;
  return pickerFrom(
    items,
    data,
    (l) => text(l.title),
    (l) => text(l.title),
  );
};
const loadNavidromeFolders = async (): Promise<Picker> => {
  const data = await json<Record<string, unknown>>('/api/navidrome/music-folders');
  const items = data.folders as Array<Record<string, unknown>> | undefined;
  return pickerFrom(
    items,
    data,
    (f) => text(f.title),
    (f) => text(f.title),
    [{ value: '', label: 'All Libraries' }],
  );
};

/** a picker's choice, sent to its endpoint (settings.js select* functions) */
async function choose(url: string, body: Record<string, string>, what: string, quietOk = true) {
  try {
    const data = await json<{ success?: boolean; error?: string; message?: string }>(
      url,
      post(body),
    );
    if (data.success) {
      if (!quietOk) toast(text(data.message), 'success');
      return true;
    }
    console.error(`Failed to switch ${what}:`, data.error);
    toast(
      `Failed to ${what === 'music folder' ? 'set' : 'switch'} ${what}: ${data.error}`,
      'error',
      'set-media',
    );
  } catch (error) {
    console.error(`Error selecting ${what}:`, error);
    toast(`Error selecting ${what}. Please try again.`, 'error', 'set-media');
  }
  return false;
}

/** a text field for one key, as settings.js loaded it */
function useField(store: SettingsStore, section: string, key: string, trim = false) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const saved = text(((settings[section] || {}) as Section_)[key] || '');
  const draft = useLoadedDraft(store, saved, (raw) =>
    store.set(section, key, trim ? raw.trim() : raw),
  );
  return { value: loads ? (trim ? draft.value : saved) : '', onChange: draft.onChange };
}

/** settings.js autoDetect*: a found address goes in the field */
async function detect(server: string, name: string, set: (url: string) => void, overlay = true) {
  const w = shell();
  try {
    if (overlay) w.showLoadingOverlay?.(`Auto-detecting ${name} server...`);
    const result = await json<{ success?: boolean; found_url?: string; error?: string }>(
      '/api/detect-media-server',
      post({ server_type: server }),
    );
    if (result.success && (overlay || result.found_url)) {
      set(text(result.found_url));
      toast(
        overlay
          ? `${name} server detected: ${text(result.found_url)}`
          : `${name} found at ${text(result.found_url)}`,
        'success',
      );
    } else {
      toast(overlay ? text(result.error) : result.error || `No ${name} server found`, 'error');
    }
  } catch (error) {
    if (overlay) {
      console.error(`Error auto-detecting ${name}:`, error);
      toast(`Failed to auto-detect ${name} server`, 'error');
    } else {
      toast(
        `Auto-detect failed: ${error instanceof Error ? error.message : String(error)}`,
        'error',
      );
    }
  } finally {
    if (overlay) w.hideLoadingOverlay?.();
  }
}

// ── Plex ──

const PIN_POLL_MS = 5000;
const SHOW = undefined;
const HIDE = { display: 'none' } as const;

interface PlexView {
  /** #plex-configuration and #plex-setup shown */
  config: boolean;
  setup: boolean;
  /** the setup's buttons, and the PIN box in their place */
  buttons: boolean;
  pin: boolean;
  /** the fields locked (a configuration just linked) */
  locked: boolean;
  /** manual setup: the action button cancels instead of clearing */
  manual: boolean;
  /** the action button has been set up once (it has a title from then on) */
  titled: boolean;
}
const PLEX_START: PlexView = {
  config: true,
  setup: true,
  buttons: true,
  pin: false,
  locked: false,
  manual: false,
  titled: false,
};

function PlexServer({
  hidden,
  store,
  view,
  setView,
  libraries,
  setLibraries,
}: {
  hidden: string;
  store: SettingsStore;
  view: PlexView;
  setView: (update: (v: PlexView) => PlexView) => void;
  libraries: Picker;
  setLibraries: (p: Picker) => void;
}) {
  const url = useField(store, 'plex', 'base_url');
  const token = useField(store, 'plex', 'token');
  const configured = !!(url.value.trim() || token.value.trim());
  const [pin, setPin] = useState({ code: '', status: '' });
  const request = useRef<string | null>(null);
  const relink = useRef(false);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (poll.current) clearInterval(poll.current);
    poll.current = null;
  };
  useEffect(() => stopPolling, []);

  /** settings.js updatePlexConfigurationButtons: an edit closes the PIN box */
  const edited = () => setView((v) => ({ ...v, pin: false }));

  function showConfiguration(locked = false, manual = false) {
    stopPolling();
    setView((v) => ({
      ...v,
      config: true,
      setup: false,
      pin: false,
      locked,
      manual,
      titled: true,
    }));
    if (manual) setLibraries({ ...libraries, shown: false });
  }
  function showSetup() {
    setView((v) => ({ ...v, config: false, setup: true, pin: false, manual: false, titled: true }));
    setLibraries({ ...libraries, shown: false });
  }

  function cancelPin() {
    stopPolling();
    request.current = null;
    setView((v) => ({
      ...v,
      buttons: true,
      pin: false,
      // a re-link goes back to the configured view, nothing changed
      setup: relink.current ? false : v.setup,
    }));
    relink.current = false;
  }

  async function pollPin() {
    const id = request.current;
    if (!id) return;
    const say = (status: string) => setPin((p) => ({ ...p, status }));
    try {
      const result = await json<Record<string, unknown>>(
        `/api/plex/pin/status?request_id=${encodeURIComponent(id)}`,
      );
      if (!result.success && result.expired) {
        say('PIN code expired. Generate a new code to continue.');
        stopPolling();
        return;
      }
      if (result.success) {
        stopPolling();
        // the address as it is now, not when the polling began
        const plex = (store.getState().settings.plex || {}) as Section_;
        const current = text(plex.base_url).trim();
        const fresh = text(result.token);
        if (relink.current && current) {
          // keep the address that works (a docker host, a reverse proxy Plex
          // cannot discover); save the token only once it reaches that server
          say('Authorized. Checking the new token against your server...');
          const check = await json<{ success?: boolean; error?: string }>(
            '/api/plex/verify-token',
            post({ url: current, token: fresh }),
          ).catch(() => ({ success: false, error: undefined }));
          if (!check.success) {
            say(
              `${check.error || "The new token couldn't reach your server"}. Nothing was changed.`,
            );
            toast(
              "Re-link didn't change anything: that Plex account can't reach your server",
              'error',
            );
            return;
          }
          store.set('plex', 'token', fresh);
        } else {
          say('Authorization complete! Saving Plex configuration...');
          store.patch('plex', { base_url: text(result.found_url), token: fresh });
        }
        relink.current = false;
        await store.flush();
        toast('Plex successfully linked', 'success');
        showConfiguration(true);
        await testConnection('plex', store);
        return;
      }
      if (result.status) say(text(result.status));
      else if (result.error) say(text(result.error));
    } catch (error) {
      console.error('Error polling Plex PIN status:', error);
      say('Unable to contact Plex auth status. Retrying...');
    }
  }

  async function startPin() {
    setView((v) => ({ ...v, buttons: false, pin: true }));
    setPin({ code: '', status: 'Starting Plex authorization...' });
    const w = shell();
    try {
      w.showLoadingOverlay?.('Starting Plex authorization...');
      const result = await json<Record<string, unknown>>('/api/plex/pin/start', post(undefined));
      if (!result.success) throw new Error(text(result.error) || 'Failed to start Plex PIN flow');
      request.current = text(result.request_id);
      setPin({
        code: text(result.code),
        status: result.expires_in
          ? `Enter this code at plex.tv/link. Code expires in ${text(result.expires_in)} seconds.`
          : 'Enter this code at plex.tv/link. Waiting for authorization...',
      });
      stopPolling();
      if (request.current) {
        poll.current = setInterval(() => void pollPin(), PIN_POLL_MS);
        void pollPin();
      }
    } catch (error) {
      console.error('Plex PIN auth start failed:', error);
      toast(error instanceof Error ? error.message : 'Failed to start Plex authorization', 'error');
      cancelPin();
    } finally {
      w.hideLoadingOverlay?.();
    }
  }

  function startRelink() {
    relink.current = true;
    setView((v) => ({ ...v, setup: true }));
    void startPin();
  }

  function restartPin() {
    const was = relink.current;
    cancelPin();
    if (was) startRelink();
    else void startPin();
  }

  async function clear() {
    cancelPin();
    store.patch('plex', { base_url: '', token: '' });
    setView((v) => ({ ...v, config: false, setup: true, buttons: true, pin: false }));
    setLibraries({
      shown: false,
      options: [{ value: '', label: 'Select a music library' }],
      value: '',
    });
    try {
      await fetch('/api/plex/clear-library', { method: 'POST' });
    } catch (error) {
      console.warn('Failed to clear Plex library preference:', error);
    }
    void store.flush();
    toast('Plex configuration cleared', 'success');
  }

  const actionsStyle = {
    justifyContent: 'flex-end',
    width: '100%',
    flexBasis: '100%',
    marginTop: '8px',
    display: configured ? 'none' : 'flex',
  };

  return (
    <div className={hidden} id="plex-container">
      <div id="plex-configuration" style={view.config ? SHOW : HIDE}>
        <div className="form-group">
          <label>Plex Server URL:</label>
          <input
            type="url"
            id="plex-url"
            placeholder="http://localhost:32400"
            disabled={view.locked}
            value={url.value}
            onChange={(event) => {
              url.onChange(event.target.value);
              edited();
            }}
          />
          <div className="form-actions" id="plex-url-actions" style={actionsStyle}>
            <button
              id="plex-autodetect-button"
              className="detect-button"
              onClick={() =>
                void detect('plex', 'Plex', (found) => store.set('plex', 'base_url', found))
              }
            >
              Auto-detect
            </button>
          </div>
        </div>
        <div className="form-group">
          <label>Plex Token:</label>
          <SecretInput
            id="plex-token"
            placeholder="X-Plex-Token"
            disabled={view.locked}
            value={token.value}
            onChange={(value) => {
              token.onChange(value);
              edited();
            }}
          />
          <div className="form-actions" id="plex-token-actions" style={actionsStyle}>
            <button
              id="plex-connect-button"
              className="test-button"
              onClick={() => void testConnection('plex', store)}
            >
              Connect
            </button>
          </div>
        </div>
        <div
          className="form-group"
          id="plex-library-selector-container"
          style={{ display: libraries.shown ? 'block' : 'none' }}
        >
          <label>Music Library:</label>
          <select
            id="plex-music-library"
            value={libraries.value}
            onChange={(event) => {
              const value = event.target.value;
              setLibraries({ ...libraries, value });
              if (value)
                void choose('/api/plex/select-music-library', { library_name: value }, 'library');
            }}
          >
            {(libraries.options.length
              ? libraries.options
              : [{ value: '', label: 'Loading...' }]
            ).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <small style={{ color: '#999', fontSize: '0.9em', display: 'block', marginTop: '5px' }}>
            Select which music library to use (doesn't affect config file)
          </small>
        </div>
        <div className="form-actions">
          <button
            id="plex-config-action-button"
            className="detect-button"
            title={
              view.manual
                ? 'Cancel manual Plex configuration'
                : view.titled
                  ? 'Clear saved Plex configuration'
                  : undefined
            }
            onClick={() => (view.manual ? showSetup() : void clear())}
          >
            {view.manual ? 'Cancel' : 'Clear Configuration'}
          </button>{' '}
          <button
            id="plex-relink-button"
            className="detect-button"
            onClick={startRelink}
            title="Get a fresh token through plex.tv/link. Your server URL stays as it is."
          >
            Re-link with Plex
          </button>
        </div>
      </div>
      <div id="plex-setup" style={view.setup ? SHOW : HIDE}>
        <div id="plex-setup-buttons" className="form-actions" style={view.buttons ? SHOW : HIDE}>
          <button
            id="plex-link-to-plex-button"
            className="detect-button"
            style={configured ? HIDE : SHOW}
            onClick={() => void startPin()}
          >
            Link to Plex (OAuth)
          </button>{' '}
          <button
            id="plex-manual-config-button"
            className="detect-button"
            style={configured ? HIDE : SHOW}
            onClick={() => showConfiguration(false, true)}
          >
            Manually Configure Plex
          </button>{' '}
          <button
            id="plex-view-config-button"
            className="detect-button view-config-button"
            style={configured ? SHOW : HIDE}
            onClick={() => showConfiguration()}
          >
            View Configuration
          </button>
        </div>
        <div
          id="plex-pin-auth-flow"
          style={{
            display: view.pin ? undefined : 'none',
            padding: '16px',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: '12px',
            marginTop: '12px',
          }}
        >
          <div className="form-group">
            <label>Plex PIN Code</label>
            <div
              id="plex-pin-code"
              style={{ fontSize: '1.4em', fontWeight: 700, marginTop: '8px' }}
            >
              {pin.code}
            </div>
          </div>
          <div className="form-group">
            <div
              id="plex-pin-instructions"
              className="setting-help-text"
              style={{ marginBottom: '8px' }}
            >
              Go to <strong>https://plex.tv/link</strong> and enter the code shown above.
            </div>
            <div id="plex-pin-status" className="setting-help-text" style={{ color: '#ccc' }}>
              {pin.status}
            </div>
          </div>
          <div className="form-actions" style={{ gap: '10px' }}>
            <button className="detect-button" id="plex-pin-refresh-button" onClick={restartPin}>
              Generate New Code
            </button>{' '}
            <button className="test-button" onClick={cancelPin}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Navidrome's playlist account ──

const PLAYLIST_HELP =
  'Your synced playlists go to this Navidrome user instead of the account above. Leave it empty to use the account above. Other profiles set their own in My Account.';

function PlaylistLogin({ store }: { store: SettingsStore }) {
  const [saved, setSaved] = useState('');
  const [user, setUser] = useState('');
  const [pass, setPass] = useState('');
  const loads = useLoads(store);

  // settings.js _paintNavidromePlaylistLogin
  const paint = (username: string) => {
    setSaved(username);
    setUser(username);
    setPass('');
  };

  useEffect(() => {
    if (!loads) return;
    json<{ success?: boolean; navidrome_username?: string }>('/api/profiles/me/server-library')
      .then((data) => paint(data?.success ? text(data.navidrome_username) : ''))
      .catch((error: unknown) => console.debug('navidrome playlist login load failed:', error));
  }, [loads]);

  async function save() {
    const username = user.trim();
    if (!username || !pass) {
      toast('Enter the playlist account username and password', 'warning');
      return;
    }
    try {
      const response = await fetch(
        '/api/profiles/me/navidrome-login',
        post({ username, password: pass }),
      );
      const data = (await response.json()) as {
        success?: boolean;
        error?: string;
        username?: string;
      };
      if (!response.ok || !data.success) {
        toast(data.error || 'Could not save the playlist account', 'error');
        return;
      }
      paint(data.username || username);
      toast(`Playlists will sync to ${data.username || username}`, 'success');
    } catch {
      toast('Could not save the playlist account', 'error');
    }
  }

  async function clear() {
    try {
      const response = await fetch('/api/profiles/me/navidrome-login', { method: 'DELETE' });
      const data = (await response.json()) as { success?: boolean; error?: string };
      if (!response.ok || !data.success) {
        toast(data.error || 'Could not clear the playlist account', 'error');
        return;
      }
      paint('');
      toast('Playlists will sync to the account above', 'success');
    } catch {
      toast('Could not clear the playlist account', 'error');
    }
  }

  return (
    <div className="form-group" id="navidrome-playlist-login">
      <label>Playlist account (optional):</label>
      <input
        type="text"
        id="navidrome-playlist-username"
        placeholder="Same as above"
        autoComplete="off"
        value={user}
        onChange={(event) => setUser(event.target.value)}
      />
      <input
        type="password"
        id="navidrome-playlist-password"
        placeholder="Password"
        autoComplete="new-password"
        style={{ marginTop: '6px' }}
        value={pass}
        onChange={(event) => setPass(event.target.value)}
      />
      <div className="setting-help-text" id="navidrome-playlist-status">
        {saved ? `Your synced playlists go to ${saved}.` : PLAYLIST_HELP}
      </div>
      <div className="form-actions">
        <button className="test-button" type="button" onClick={() => void save()}>
          Save playlist account
        </button>{' '}
        <button
          className="detect-button"
          type="button"
          id="navidrome-playlist-clear"
          style={saved ? SHOW : HIDE}
          onClick={() => void clear()}
        >
          Use the account above
        </button>
      </div>
    </div>
  );
}

// ── Music Assistant's source ──

async function loadMaSources(quiet: boolean): Promise<Picker> {
  try {
    const result = await json<{
      success?: boolean;
      error?: string;
      selected?: string;
      sources?: Array<{ key: string; title: string; domain: string }>;
    }>('/api/music-assistant/sources');
    if (!result.success) {
      if (!quiet) toast(result.error || 'Could not load Music Assistant sources', 'error');
      return NO_PICKER;
    }
    const sources = result.sources || [];
    if (!quiet && !sources.length)
      toast('Music Assistant has no Local files or SMB source', 'warning');
    const options = sources.map((s) => ({ value: s.key, label: `${s.title} (${s.domain})` }));
    const hit = sources.find((s) => s.key === result.selected);
    return { shown: sources.length > 0, options, value: hit ? hit.key : options[0]?.value || '' };
  } catch (error) {
    if (!quiet)
      toast(
        `Could not load Music Assistant sources: ${error instanceof Error ? error.message : String(error)}`,
        'error',
      );
    return NO_PICKER;
  }
}

// ── the section ──

export function ServerConnections({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const saved = text((settings as Section_).active_media_server) || 'plex';
  const [active, setActive] = useState<Server>('plex');
  const [view, setView] = useState<PlexView>(PLEX_START);
  const plexLibs = usePicker(loadPlexLibraries);
  const jfUsers = usePicker(loadJellyfinUsers);
  const jfLibs = usePicker(loadJellyfinLibraries);
  const ndFolders = usePicker(loadNavidromeFolders);
  const [maSources, setMaSources] = useState<Picker>(NO_PICKER);

  const jfUrl = useField(store, 'jellyfin', 'base_url');
  const jfKey = useField(store, 'jellyfin', 'api_key');
  const jfSaved = text(((settings.jellyfin || {}) as Section_).api_timeout || 120);
  const jfTimeout = useLoadedDraft(store, jfSaved, (raw) =>
    store.set('jellyfin', 'api_timeout', parseInt(raw, 10) || 30),
  );
  const ndUrl = useField(store, 'navidrome', 'base_url');
  const ndUser = useField(store, 'navidrome', 'username');
  const ndPass = useField(store, 'navidrome', 'password');
  const maUrl = useField(store, 'music_assistant', 'base_url', true);
  const maToken = useField(store, 'music_assistant', 'token', true);
  const maFolder = useField(store, 'music_assistant', 'source_folder', true);

  /** settings.js toggleServer: the server's panel, its pickers loaded */
  function show(server: Server) {
    setActive(server);
    // Plex opens on its setup (Link, Manual, View Configuration)
    setView((v) => ({ ...v, config: server !== 'plex', setup: server === 'plex' }));
    if (server === 'plex') void plexLibs.reload();
    if (server === 'jellyfin') void jfUsers.reload().then(() => jfLibs.reload());
    if (server === 'navidrome') void ndFolders.reload();
  }

  // each load: the saved server's panel, the playlist account, MA's sources
  useEffect(() => {
    if (!loads) return;
    const server = (SERVERS as readonly string[]).includes(saved) ? (saved as Server) : 'plex';
    show(server);
    setView((v) => ({ ...v, pin: false }));
    const ma = (store.getState().settings.music_assistant || {}) as Section_;
    if (ma.base_url && ma.token) void loadMaSources(true).then(setMaSources);
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  // a good test reloads the tested server's picker
  useEffect(
    () =>
      onReload((service) => {
        if (service === 'plex') void plexLibs.reload();
        else if (service === 'jellyfin') void jfUsers.reload().then(() => jfLibs.reload());
        else if (service === 'navidrome') void ndFolders.reload();
      }),
    [], // eslint-disable-line react-hooks/exhaustive-deps
  );

  function toggle(server: Server) {
    show(server);
    // the video side reuses this page, but only opens a panel here: it never
    // changes the music server
    if (document.body.getAttribute('data-side') === 'video') return;
    store.setExtra('active_media_server', server, async (value) => {
      const result = await json<{ success?: boolean }>(
        '/api/settings',
        post({ active_media_server: value }),
      );
      return result.success === true;
    });
  }

  const hidden = (server: Server) =>
    server === active ? 'server-config-container' : 'server-config-container hidden';
  const toggleClass = (server: Server) =>
    server === active ? 'server-toggle-btn active' : 'server-toggle-btn';

  return (
    <div className="settings-group" data-stg="connections" data-svc-side="music">
      <h3>Server Connections</h3>

      <div className="server-toggle-container">
        <button className={toggleClass('plex')} id="plex-toggle" onClick={() => toggle('plex')}>
          <img src="/static/img/brands/plex.png" alt="Plex" className="server-logo" />
        </button>{' '}
        <button
          className={toggleClass('jellyfin')}
          id="jellyfin-toggle"
          onClick={() => toggle('jellyfin')}
        >
          <img src="/static/img/brands/jellyfin.png" alt="Jellyfin" className="server-logo" />
        </button>{' '}
        <button
          className={toggleClass('navidrome')}
          id="navidrome-toggle"
          onClick={() => toggle('navidrome')}
        >
          <img src="/static/img/brands/navidrome.png" alt="Navidrome" className="server-logo" />{' '}
          Navidrome
        </button>{' '}
        <button
          className={toggleClass('music_assistant')}
          id="music_assistant-toggle"
          onClick={() => toggle('music_assistant')}
        >
          <img
            src="/static/img/brands/music-assistant.png"
            alt="Music Assistant"
            className="server-logo"
          />{' '}
          Music Assistant
        </button>{' '}
        <button
          className={toggleClass('soulsync')}
          id="soulsync-toggle"
          onClick={() => toggle('soulsync')}
        >
          <img src="/static/trans2.png" alt="SoulSync" className="server-logo" /> Standalone
        </button>
      </div>

      <PlexServer
        hidden={hidden('plex')}
        store={store}
        view={view}
        setView={setView}
        libraries={plexLibs.picker}
        setLibraries={plexLibs.setPicker}
      />
      <JellyfinServer
        hidden={hidden('jellyfin')}
        store={store}
        url={jfUrl}
        apiKey={jfKey}
        timeout={jfTimeout}
        loaded={loads > 0}
        users={jfUsers}
        libraries={jfLibs}
      />
      <div className={hidden('navidrome')} id="navidrome-container">
        <div className="setting-help-text">
          Navidrome users should enable <strong>Real Path</strong> in the Navidrome player/settings
          so SoulSync can resolve imported and synced files correctly.
        </div>
        <div className="form-group">
          <label>Navidrome Server URL:</label>
          <input
            type="url"
            id="navidrome-url"
            placeholder="http://localhost:4533"
            value={ndUrl.value}
            onChange={(event) => ndUrl.onChange(event.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Username:</label>
          <input
            type="text"
            id="navidrome-username"
            placeholder="Username"
            value={ndUser.value}
            onChange={(event) => ndUser.onChange(event.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Password:</label>
          <SecretInput id="navidrome-password" placeholder="Password" {...ndPass} />
        </div>
        <PlaylistLogin store={store} />
        <div
          className="form-group"
          id="navidrome-folder-selector-container"
          style={{ display: ndFolders.picker.shown ? 'block' : 'none' }}
        >
          <label>Music Library</label>
          <select
            id="navidrome-music-folder"
            value={ndFolders.picker.value}
            onChange={(event) => {
              const value = event.target.value;
              ndFolders.setPicker({ ...ndFolders.picker, value });
              void choose(
                '/api/navidrome/select-music-folder',
                { folder_name: value },
                'music folder',
                false,
              );
            }}
          >
            {(ndFolders.picker.options.length
              ? ndFolders.picker.options
              : [{ value: '', label: 'All Libraries' }]
            ).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <small>Select which music library to import from</small>
        </div>
        <div className="form-actions">
          <button
            className="detect-button"
            onClick={() =>
              void detect('navidrome', 'Navidrome', (found) =>
                store.set('navidrome', 'base_url', found),
              )
            }
          >
            Auto-detect
          </button>{' '}
          <button className="test-button" onClick={() => void testConnection('navidrome', store)}>
            Test
          </button>
        </div>
      </div>

      <div className={hidden('music_assistant')} id="music_assistant-container">
        <div className="setting-help-text">
          SoulSync reads one <strong>Local files</strong> (or SMB) music source of your Music
          Assistant library, so every track has a file path. Create a long-lived token in Music
          Assistant under Settings, Users.
        </div>
        <div className="form-group">
          <label>Music Assistant URL:</label>
          <input
            type="url"
            id="music_assistant-url"
            placeholder="http://localhost:8095"
            value={maUrl.value}
            onChange={(event) => maUrl.onChange(event.target.value)}
          />
          <div className="form-actions">
            <button
              className="detect-button"
              type="button"
              onClick={() =>
                void detect(
                  'music_assistant',
                  'Music Assistant',
                  (found) => store.set('music_assistant', 'base_url', found),
                  false,
                )
              }
            >
              Auto-detect
            </button>
          </div>
        </div>
        <div className="form-group">
          <label>Long-lived token:</label>
          <SecretInput id="music_assistant-token" placeholder="Token" {...maToken} />
        </div>
        <div
          className="form-group"
          id="music_assistant-source-selector-container"
          style={{ display: maSources.shown ? undefined : 'none' }}
        >
          <label>Music source</label>
          <select
            id="music_assistant-source"
            value={maSources.value}
            onChange={(event) => {
              const value = event.target.value;
              setMaSources({ ...maSources, value });
              if (!value) return;
              // the page's save posted the picked source too
              store.set('music_assistant', 'source_instance', value);
              const label = maSources.options.find((o) => o.value === value)?.label || value;
              void json<{ success?: boolean; error?: string }>(
                '/api/music-assistant/select-source',
                post({ instance_id: value }),
              )
                .then((result) =>
                  toast(
                    result.success
                      ? `Music source set to ${label}`
                      : result.error || 'Could not set the source',
                    result.success ? 'success' : 'error',
                  ),
                )
                .catch((error: unknown) =>
                  toast(
                    `Could not set the source: ${error instanceof Error ? error.message : String(error)}`,
                    'error',
                  ),
                );
            }}
          >
            {maSources.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <div className="setting-help-text">
            The Local files source whose tracks SoulSync scans. Save the URL and token first, then
            pick one.
          </div>
        </div>
        <div className="form-group">
          <label>Source folder:</label>
          <input
            type="text"
            id="music_assistant-source-folder"
            placeholder="/media/music"
            value={maFolder.value}
            onChange={(event) => maFolder.onChange(event.target.value)}
          />
          <div className="setting-help-text">
            Only needed when Music Assistant doesn't share it (2.10.9 doesn't). The folder your
            source reads, as Music Assistant sees it.
          </div>
        </div>
        <div className="form-actions">
          <button
            className="test-button"
            type="button"
            onClick={() => void loadMaSources(false).then(setMaSources)}
          >
            Load sources
          </button>
        </div>
      </div>

      <div className={hidden('soulsync')} id="soulsync-container">
        <div className="soulsync-standalone-info">
          <img src="/static/trans2.png" alt="SoulSync" className="soulsync-standalone-logo" />
          <div className="soulsync-standalone-text">
            <h4>Standalone Mode</h4>
            <p>
              SoulSync manages your library directly without an external media server. Import your
              existing music first: move files into the import folder, then use a separate music
              library folder for the processed library SoulSync creates.
            </p>
          </div>
        </div>
        <div className="form-actions">
          <button className="test-button" onClick={() => void testConnection('soulsync', store)}>
            Verify Music Library Folder
          </button>
        </div>
      </div>

      <div className="server-test-section">
        <button
          className="test-button server-test-btn"
          onClick={() => void testConnection('server', store)}
        >
          Test Server
        </button>
      </div>
    </div>
  );
}

function JellyfinServer({
  hidden,
  store,
  url,
  apiKey,
  timeout,
  loaded,
  users,
  libraries,
}: {
  hidden: string;
  store: SettingsStore;
  url: { value: string; onChange: (raw: string) => void };
  apiKey: { value: string; onChange: (raw: string) => void };
  timeout: { value: string; onChange: (raw: string) => void };
  loaded: boolean;
  users: ReturnType<typeof usePicker>;
  libraries: ReturnType<typeof usePicker>;
}) {
  return (
    <div className={hidden} id="jellyfin-container">
      <div className="form-group">
        <label>Jellyfin Server URL:</label>
        <input
          type="url"
          id="jellyfin-url"
          placeholder="http://localhost:8096"
          value={url.value}
          onChange={(event) => url.onChange(event.target.value)}
        />
      </div>
      <div className="form-group">
        <label>API Key:</label>
        <SecretInput id="jellyfin-api-key" placeholder="Jellyfin API Key" {...apiKey} />
      </div>
      <div className="form-actions">
        <button
          className="detect-button"
          onClick={() =>
            void detect('jellyfin', 'Jellyfin', (found) => store.set('jellyfin', 'base_url', found))
          }
        >
          Auto-detect
        </button>{' '}
        <button className="test-button" onClick={() => void testConnection('jellyfin', store)}>
          Test
        </button>
      </div>

      <div className="form-group">
        <label>API Timeout (seconds):</label>
        <input
          type="number"
          id="jellyfin-timeout"
          placeholder="120"
          min="15"
          max="300"
          value={loaded ? timeout.value : '120'}
          onChange={(event) => timeout.onChange(event.target.value)}
        />
        <small className="settings-hint">
          Timeout for bulk API requests during database sync (15-300s). Increase if your Jellyfin
          server is slow to respond.
        </small>
      </div>

      <div
        className="form-group"
        id="jellyfin-user-selector-container"
        style={{ display: users.picker.shown ? 'block' : 'none' }}
      >
        <label>Jellyfin User</label>
        <select
          id="jellyfin-user"
          value={users.picker.value}
          onChange={(event) => {
            const value = event.target.value;
            users.setPicker({ ...users.picker, value });
            if (!value) return;
            void choose('/api/jellyfin/select-user', { username: value }, 'user').then((ok) => {
              // the new user's libraries
              if (ok) void libraries.reload();
            });
          }}
        >
          {(users.picker.options.length
            ? users.picker.options
            : [{ value: '', label: 'Select User' }]
          ).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <small>Select which user's music library to scan</small>
      </div>

      <div
        className="form-group"
        id="jellyfin-library-selector-container"
        style={{ display: libraries.picker.shown ? 'block' : 'none' }}
      >
        <label>Music Library</label>
        <select
          id="jellyfin-music-library"
          value={libraries.picker.value}
          onChange={(event) => {
            const value = event.target.value;
            libraries.setPicker({ ...libraries.picker, value });
            if (value)
              void choose('/api/jellyfin/select-music-library', { library_name: value }, 'library');
          }}
        >
          {(libraries.picker.options.length
            ? libraries.picker.options
            : [{ value: '', label: 'Select Library' }]
          ).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <small>Select which music library to use (doesn't affect config file)</small>
      </div>
    </div>
  );
}
