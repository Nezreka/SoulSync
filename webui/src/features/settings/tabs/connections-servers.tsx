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

import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';

import type { SettingsStore } from '../settings-store';

import { settingsStore } from '../settings-store';
import {
  ActionRow,
  Btn,
  Card,
  FieldLine,
  More,
  Note,
  NumberField,
  Row,
  Say,
  SecretField,
  Select,
  SheetField,
  SheetGroup,
  StatusWords,
  TextField,
  type Tone,
} from '../ui';
import s from '../ui/settings.module.css';
import { statusFrames } from './connections';
import { useLoadedDraft } from './library-card';
import { secretNote, text, useSettings } from './source-panels';

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

// ── what each service's last Test said (the tiles and the sheets read it) ──

export interface TestResult {
  tone: Tone;
  text: string;
  at: number;
}
let results: Record<string, TestResult> = {};
const resultListeners = new Set<() => void>();
export const testResults = {
  subscribe: (listener: () => void) => {
    resultListeners.add(listener);
    return () => {
      resultListeners.delete(listener);
    };
  },
  get: () => results,
  set: (service: string, result: Omit<TestResult, 'at'>) => {
    results = { ...results, [service]: { ...result, at: Date.now() } };
    for (const listener of resultListeners) listener();
  },
  /** for tests */
  reset: () => {
    results = {};
    for (const listener of resultListeners) listener();
  },
};
export const useTestResults = () => useSyncExternalStore(testResults.subscribe, testResults.get);

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
      testResults.set(service, {
        tone: warned ? 'warn' : 'ok',
        text: String(result.message || 'Connected').replace(/^⚠\s*/, ''),
      });
      for (const reload of reloads) reload(service);
    } else {
      toast(`${service} connection failed: ${result.error}`, 'error', 'gs-connecting');
      testResults.set(service, { tone: 'bad', text: result.error || 'Connection failed' });
    }
  } catch (error) {
    console.error(`Error testing ${service} connection:`, error);
    testResults.set(service, { tone: 'bad', text: 'Could not reach SoulSync to test it' });
    toast(`Failed to test ${service} connection`, 'error', 'gs-connecting');
  } finally {
    w.hideLoadingOverlay?.();
  }
}

// ── the servers ──

const SERVERS = ['plex', 'jellyfin', 'navidrome', 'music_assistant', 'soulsync'] as const;
export type Server = (typeof SERVERS)[number];

/** the five servers as the pick-one row shows them */
export const SERVER_LOOK: Record<Server, { name: string; logo: string }> = {
  plex: { name: 'Plex', logo: '/static/img/brands/plex.png' },
  jellyfin: { name: 'Jellyfin', logo: '/static/img/brands/jellyfin.png' },
  navidrome: { name: 'Navidrome', logo: '/static/img/brands/navidrome.png' },
  music_assistant: { name: 'Music Assistant', logo: '/static/img/brands/music-assistant.png' },
  soulsync: { name: 'Standalone', logo: '/static/trans2.png' },
};

export const serverName = (server: string) =>
  (SERVER_LOOK as Record<string, { name: string }>)[server]?.name || server;

/** what SoulSync knows about the active server, in words: the last Test
 *  Server, else the server poll (/status media_server), else nothing yet */
export function serverState(
  server: string,
  frame: { media_server?: { connected?: boolean; type?: string | null } | null } | null,
  tested: TestResult | undefined,
): { tone: Tone; text: string } {
  if (server === 'soulsync') return { tone: 'ok', text: 'Standalone: no media server' };
  if (tested) return { tone: tested.tone, text: tested.text };
  const polled = frame?.media_server;
  if (polled && polled.type === server)
    return polled.connected
      ? { tone: 'ok', text: 'Connected' }
      : { tone: 'bad', text: 'Not connected' };
  return { tone: 'idle', text: 'Not tested yet' };
}

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

  return (
    <div className={hidden} id="plex-container" data-ui="">
      <div id="plex-configuration" hidden={!view.config}>
        <SheetGroup title="Connection" />
        <SheetField
          label="Plex server URL"
          htmlFor="plex-url"
          help={view.locked ? 'Linked through plex.tv. Clear it to type another one.' : undefined}
        >
          <FieldLine>
            <TextField
              full
              mono
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
            <span id="plex-url-actions" hidden={configured}>
              <Btn
                small
                id="plex-autodetect-button"
                onClick={() =>
                  void detect('plex', 'Plex', (found) => store.set('plex', 'base_url', found))
                }
              >
                Auto-detect
              </Btn>
            </span>
          </FieldLine>
        </SheetField>
        <SheetField label="Plex token" htmlFor="plex-token" help={secretNote(token.value)}>
          <SecretField
            id="plex-token"
            placeholder="X-Plex-Token"
            disabled={view.locked}
            value={token.value}
            onChange={(value) => {
              token.onChange(value);
              edited();
            }}
          />
        </SheetField>
        <Row
          id="plex-library-selector-container"
          hidden={!libraries.shown}
          label="Music library"
          htmlFor="plex-music-library"
          field
          help="Which Plex library SoulSync reads. Saved on its own, not in the settings file."
        >
          <Select
            id="plex-music-library"
            value={libraries.value}
            onChange={(event) => {
              const value = event.target.value;
              setLibraries({ ...libraries, value });
              if (value)
                void choose('/api/plex/select-music-library', { library_name: value }, 'library');
            }}
            options={
              libraries.options.length ? libraries.options : [{ value: '', label: 'Loading...' }]
            }
          />
        </Row>
        <ActionRow
          label="Sign in again"
          help="A fresh token through plex.tv/link. Your server URL stays as it is."
        >
          <Btn small id="plex-relink-button" onClick={startRelink}>
            Re-link with Plex
          </Btn>
        </ActionRow>
        <ActionRow
          label={view.manual ? 'Stop setting it up by hand' : 'Forget this server'}
          help={
            view.manual
              ? 'Back to linking through plex.tv.'
              : 'Forgets this Plex server and its token.'
          }
        >
          <Btn
            small
            kind={view.manual ? undefined : 'danger'}
            id="plex-config-action-button"
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
          </Btn>
        </ActionRow>
      </div>
      <div id="plex-setup" hidden={!view.setup}>
        <div id="plex-setup-buttons" hidden={!view.buttons}>
          <ActionRow
            label="Link to Plex"
            help="Sign in at plex.tv/link with a code. SoulSync finds your server and saves its token."
            hidden={configured}
          >
            <Btn
              small
              kind="primary"
              id="plex-link-to-plex-button"
              hidden={configured}
              onClick={() => void startPin()}
            >
              Link to Plex
            </Btn>
          </ActionRow>
          <ActionRow
            label="Set it up by hand"
            help="Type the server URL and a token yourself."
            hidden={configured}
          >
            <Btn
              small
              id="plex-manual-config-button"
              hidden={configured}
              onClick={() => showConfiguration(false, true)}
            >
              Manually Configure Plex
            </Btn>
          </ActionRow>
          <ActionRow
            label="Your Plex server"
            help="The address and token SoulSync uses."
            hidden={!configured}
          >
            <Btn
              small
              id="plex-view-config-button"
              hidden={!configured}
              onClick={() => showConfiguration()}
            >
              View Configuration
            </Btn>
          </ActionRow>
        </div>
        <div id="plex-pin-auth-flow" hidden={!view.pin}>
          <SheetGroup title="Link to Plex" />
          <Row
            label="Plex PIN code"
            help={
              <span id="plex-pin-instructions">
                Go to <strong>https://plex.tv/link</strong> and enter this code.
              </span>
            }
          >
            <b id="plex-pin-code" className={s.pinCode}>
              {pin.code}
            </b>
          </Row>
          <Say id="plex-pin-status">{pin.status}</Say>
          <ActionRow label="Not working?" help="A new code, or stop linking.">
            <Btn small id="plex-pin-refresh-button" onClick={restartPin}>
              Generate New Code
            </Btn>
            <Btn small kind="ghost" onClick={cancelPin}>
              Cancel
            </Btn>
          </ActionRow>
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
    <div id="navidrome-playlist-login">
      <SheetGroup title="Playlist account (optional)" />
      <SheetField
        label="Username"
        htmlFor="navidrome-playlist-username"
        help={
          <span id="navidrome-playlist-status">
            {saved ? `Your synced playlists go to ${saved}.` : PLAYLIST_HELP}
          </span>
        }
      >
        <TextField
          full
          id="navidrome-playlist-username"
          placeholder="Same as above"
          autoComplete="off"
          value={user}
          onChange={(event) => setUser(event.target.value)}
        />
      </SheetField>
      <SheetField label="Password" htmlFor="navidrome-playlist-password">
        <TextField
          full
          type="password"
          id="navidrome-playlist-password"
          placeholder="Password"
          autoComplete="new-password"
          value={pass}
          onChange={(event) => setPass(event.target.value)}
        />
      </SheetField>
      <ActionRow label="Playlist account" help="Saved for your profile, on its own.">
        <Btn
          small
          kind="ghost"
          id="navidrome-playlist-clear"
          hidden={!saved}
          onClick={() => void clear()}
        >
          Use the account above
        </Btn>
        <Btn small onClick={() => void save()}>
          Save playlist account
        </Btn>
      </ActionRow>
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

export function ServerConnections({
  store,
  host,
  onView,
}: {
  store: SettingsStore;
  /** the sheet body the open server's panel shows in; hidden home otherwise */
  host?: HTMLElement | null;
  /** View Configuration: open the sheet */
  onView?: (server: Server) => void;
}) {
  const frame = useSyncExternalStore(statusFrames.subscribe, statusFrames.get);
  const tests = useTestResults();
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
  // the one in use: on the music side the one last picked (its panel is the
  // one shown, and it saves through active_media_server); the video side
  // only ever looks, so there it is the saved one
  const savedServer = (SERVERS as readonly string[]).includes(saved) ? (saved as Server) : 'plex';
  const chosen = document.body.getAttribute('data-side') === 'video' ? savedServer : active;
  const state = serverState(chosen, frame as never, tests.server);

  const panels: ReactNode = (
    <div className="server-panels" data-server-panels="" data-ui="">
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
        <Note info>
          Turn on <strong>Real Path</strong> in Navidrome's player settings so SoulSync can find
          imported and synced files.
        </Note>
        <SheetGroup title="Connection" />
        <SheetField label="Navidrome server URL" htmlFor="navidrome-url">
          <FieldLine>
            <TextField
              full
              mono
              type="url"
              id="navidrome-url"
              placeholder="http://localhost:4533"
              value={ndUrl.value}
              onChange={(event) => ndUrl.onChange(event.target.value)}
            />
            <Btn
              small
              onClick={() =>
                void detect('navidrome', 'Navidrome', (found) =>
                  store.set('navidrome', 'base_url', found),
                )
              }
            >
              Auto-detect
            </Btn>
          </FieldLine>
        </SheetField>
        <SheetField label="Username" htmlFor="navidrome-username">
          <TextField
            full
            id="navidrome-username"
            placeholder="Username"
            value={ndUser.value}
            onChange={(event) => ndUser.onChange(event.target.value)}
          />
        </SheetField>
        <SheetField label="Password" htmlFor="navidrome-password" help={secretNote(ndPass.value)}>
          <SecretField id="navidrome-password" placeholder="Password" {...ndPass} />
        </SheetField>
        <Row
          id="navidrome-folder-selector-container"
          hidden={!ndFolders.picker.shown}
          label="Music library"
          htmlFor="navidrome-music-folder"
          field
          help="Which music library to import from."
        >
          <Select
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
            options={
              ndFolders.picker.options.length
                ? ndFolders.picker.options
                : [{ value: '', label: 'All Libraries' }]
            }
          />
        </Row>
        <PlaylistLogin store={store} />
      </div>

      <div className={hidden('music_assistant')} id="music_assistant-container">
        <Note info>
          SoulSync reads one <strong>Local files</strong> (or SMB) music source of your Music
          Assistant library, so every track has a file path. Make a long-lived token in Music
          Assistant under Settings, Users.
        </Note>
        <SheetGroup title="Connection" />
        <SheetField label="Music Assistant URL" htmlFor="music_assistant-url">
          <FieldLine>
            <TextField
              full
              mono
              type="url"
              id="music_assistant-url"
              placeholder="http://localhost:8095"
              value={maUrl.value}
              onChange={(event) => maUrl.onChange(event.target.value)}
            />
            <Btn
              small
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
            </Btn>
          </FieldLine>
        </SheetField>
        <SheetField
          label="Long-lived token"
          htmlFor="music_assistant-token"
          help={secretNote(maToken.value)}
        >
          <SecretField id="music_assistant-token" placeholder="Token" {...maToken} />
        </SheetField>
        <SheetGroup title="Music source" />
        <ActionRow
          label="Sources"
          help="Save the URL and token first, then load the sources to pick one."
        >
          <Btn small onClick={() => void loadMaSources(false).then(setMaSources)}>
            Load sources
          </Btn>
        </ActionRow>
        <Row
          id="music_assistant-source-selector-container"
          hidden={!maSources.shown}
          label="Music source"
          htmlFor="music_assistant-source"
          field
          help="The Local files source whose tracks SoulSync scans."
        >
          <Select
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
            options={maSources.options}
          />
        </Row>
        <More label="More options" count={1} names="source folder">
          <SheetField
            label="Source folder"
            htmlFor="music_assistant-source-folder"
            help="Only needed when Music Assistant does not share it (2.10.9 does not). The folder your source reads, as Music Assistant sees it."
          >
            <TextField
              full
              mono
              id="music_assistant-source-folder"
              placeholder="/media/music"
              value={maFolder.value}
              onChange={(event) => maFolder.onChange(event.target.value)}
            />
          </SheetField>
        </More>
      </div>

      <div className={hidden('soulsync')} id="soulsync-container">
        <Note info>
          <strong>Standalone.</strong> SoulSync manages your library itself, with no media server.
          Import your existing music first: move files into the import folder, and keep a separate
          music library folder for the library SoulSync builds.
        </Note>
      </div>
    </div>
  );

  return (
    <>
      <Card
        title="Server Connections"
        hint="Pick one. Picking a server makes it the active one."
        id="stg-server-card"
        data-svc-side="music"
        actions={
          <>
            <StatusWords tone={state.tone}>{`${serverName(chosen)}: ${state.text}`}</StatusWords>
            <Btn small onClick={() => void testConnection('server', store)}>
              Test Server
            </Btn>
            <Btn
              small
              icon="gear"
              onClick={() => {
                onView?.(active);
                // a configured Plex opens on its fields, not its setup buttons
                const plex = document.getElementById('plex-view-config-button');
                if (active === 'plex' && plex && plex.style.display !== 'none') plex.click();
              }}
            >
              View Configuration
            </Btn>
          </>
        }
      >
        <div className={s.servers} role="radiogroup" aria-label="Media server">
          {SERVERS.map((server) => (
            <button
              key={server}
              type="button"
              role="radio"
              aria-checked={server === chosen}
              id={`${server}-toggle`}
              className={server === chosen ? `${s.server} ${s.serverOn}` : s.server}
              onClick={() => toggle(server)}
            >
              <img src={SERVER_LOOK[server].logo} alt="" className={s.serverLogo} />
              <span className={s.serverName}>{SERVER_LOOK[server].name}</span>
            </button>
          ))}
        </div>
      </Card>
      {host ? createPortal(panels, host) : <div hidden>{panels}</div>}
    </>
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
      <SheetGroup title="Connection" />
      <SheetField label="Jellyfin server URL" htmlFor="jellyfin-url">
        <FieldLine>
          <TextField
            full
            mono
            type="url"
            id="jellyfin-url"
            placeholder="http://localhost:8096"
            value={url.value}
            onChange={(event) => url.onChange(event.target.value)}
          />
          <Btn
            small
            onClick={() =>
              void detect('jellyfin', 'Jellyfin', (found) =>
                store.set('jellyfin', 'base_url', found),
              )
            }
          >
            Auto-detect
          </Btn>
        </FieldLine>
      </SheetField>
      <SheetField label="API key" htmlFor="jellyfin-api-key" help={secretNote(apiKey.value)}>
        <SecretField id="jellyfin-api-key" placeholder="Jellyfin API Key" {...apiKey} />
      </SheetField>
      <Row
        id="jellyfin-user-selector-container"
        hidden={!users.picker.shown}
        label="Jellyfin user"
        htmlFor="jellyfin-user"
        field
        help="Whose music library is scanned."
      >
        <Select
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
          options={
            users.picker.options.length
              ? users.picker.options
              : [{ value: '', label: 'Select User' }]
          }
        />
      </Row>
      <Row
        id="jellyfin-library-selector-container"
        hidden={!libraries.picker.shown}
        label="Music library"
        htmlFor="jellyfin-music-library"
        field
        help="Which music library SoulSync reads. Saved on its own, not in the settings file."
      >
        <Select
          id="jellyfin-music-library"
          value={libraries.picker.value}
          onChange={(event) => {
            const value = event.target.value;
            libraries.setPicker({ ...libraries.picker, value });
            if (value)
              void choose('/api/jellyfin/select-music-library', { library_name: value }, 'library');
          }}
          options={
            libraries.picker.options.length
              ? libraries.picker.options
              : [{ value: '', label: 'Select Library' }]
          }
        />
      </Row>
      <More label="More options" count={1} names="API timeout">
        <Row
          label="API timeout"
          htmlFor="jellyfin-timeout"
          field
          help="For bulk requests during a database sync (15 to 300). Raise it if your Jellyfin server is slow to answer."
        >
          <NumberField
            id="jellyfin-timeout"
            placeholder="120"
            min="15"
            max="300"
            unit="seconds"
            value={loaded ? timeout.value : '120'}
            onChange={(event) => timeout.onChange(event.target.value)}
          />
        </Row>
      </More>
    </div>
  );
}
