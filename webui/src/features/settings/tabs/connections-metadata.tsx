/**
 * Connections, step 10a (SETTINGS_PORT.md): the primary metadata source
 * picker, the Library discography source under it, every Metadata service
 * card (Spotify, iTunes, Deezer, Discogs, Tidal, Qobuz, MusicBrainz,
 * Hydrabase) and System's Experimental switches, which the picker reads.
 *
 * The service cards keep their markup: they sit in the hidden API
 * Configuration group, and the Services tiles (still settings.js) lift one
 * into their modal by moving the node. data-react-panel tells settings.js's
 * page-wide autosave and secret wiring a card is React's wherever it sits.
 */

import {
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { createPortal } from 'react-dom';

import type { SettingsPayload, SettingsStore } from '../settings-store';

import {
  LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS,
  normalizeLibraryDiscographySource,
} from '../library-discography-source';
import { Card, Row, SectionLabel, Select } from '../ui';
import {
  checkQobuzAuthStatus,
  hydrabaseLink,
  onQobuzAuth,
  type QobuzAuthState,
  jiosaavnOn,
  lockedMessage,
  musicbrainzKeys,
  musicbrainzRate,
  REDIRECT_DEFAULTS,
  savedSource,
  selectable,
  sourceKeys,
  sourceOptions,
  type StatusFrame,
  statusFrames,
} from './connections';
import { useServicePlace } from './connections-services';
import {
  helpBits,
  REDACTED_SECRET_SENTINEL,
  SecretInput,
  Section,
  useDraft,
  useHelp,
} from './parts';
import { text, useSettings } from './source-panels';

type Section_ = Record<string, unknown>;

type Shell = Window & {
  showToast?: (message: string, type?: string, context?: string) => void;
  showLoadingOverlay?: (message?: string) => void;
  hideLoadingOverlay?: () => void;
  showConfirmDialog?: (options: { title: string; message: string }) => Promise<boolean>;
  saveSettings?: (quiet?: boolean) => Promise<void>;
  testConnection?: (service: string) => Promise<void>;
  refreshRateMonitorExperimentalVisibility?: () => void;
  renderEnrichmentRail?: () => void;
  _settingsAutosaveOff?: boolean;
};
const shell = () => window as Shell;
const toast = (message: string, type: string, context?: string) =>
  shell().showToast?.(message, type, context);

function useLoads(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().loads);
}
export function useStatus(): StatusFrame | null {
  return useSyncExternalStore(statusFrames.subscribe, statusFrames.get);
}
function useHydrabase() {
  return useSyncExternalStore(hydrabaseLink.subscribe, hydrabaseLink.get);
}

const sec = (settings: SettingsPayload, name: string) => (settings[name] || {}) as Section_;

/** a change still waiting on autosave goes out first, so the server tests
 *  what is on screen (settings.js testConnection did this for its own save) */
async function flushFirst(store: SettingsStore) {
  if (shell()._settingsAutosaveOff !== true && store.hasPending()) await store.flush();
}

export async function testService(store: SettingsStore, service: string) {
  await flushFirst(store);
  await shell().testConnection?.(service);
}

/** settings.js syncMetadataSourceSelection: the server has already switched
 *  the source (a Spotify disconnect answers with its pick), so it is shown,
 *  not saved again */
export function syncMetadataSourceSelection(store: SettingsStore, source: string) {
  if (!source) return;
  store.assume('metadata', sourceKeys(source));
}

// ── the metadata source picker ──

function MetadataPicker({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const status = useStatus();
  const hydrabase = useHydrabase();
  const options = sourceOptions(settings, status, hydrabase);
  const shown = savedSource(settings);
  const lastValid = useRef(shown);
  const select = useRef<HTMLSelectElement>(null);

  /** settings.js sanitizeMetadataSourceSelection: a source that cannot be
   *  picked goes back to the last one that could, else Deezer */
  const fallbackFor = (source: string) => {
    const last = lastValid.current;
    return last && last !== source && selectable(last, settings, status) ? last : 'deezer';
  };

  // quietly, on each /status frame and each Discogs token edit. the page
  // saved the switch with its next save of anything; this saves it now
  useEffect(() => {
    if (!status) return;
    if (selectable(shown, settings, status)) {
      lastValid.current = shown;
      return;
    }
    const fallback = fallbackFor(shown);
    lastValid.current = fallback;
    store.patch('metadata', sourceKeys(fallback));
  });

  // a saved source whose option is not there (Hydrabase before its status
  // answers) shows nothing picked, as the page's select did
  useLayoutEffect(() => {
    const el = select.current;
    if (el && !options.some((o) => o.value === shown)) el.selectedIndex = -1;
  });

  function change(value: string) {
    if (selectable(value, settings, status)) {
      lastValid.current = value;
      store.patch('metadata', sourceKeys(value));
      return;
    }
    const fallback = fallbackFor(value);
    lastValid.current = fallback;
    if (fallback !== shown) store.patch('metadata', sourceKeys(fallback));
    toast(lockedMessage(value), 'warning');
  }

  return (
    <Row
      field
      label="Primary metadata source"
      htmlFor="metadata-fallback-source"
      help="Spotify and Discogs need their account connected below before they can be picked."
      more={HELP_BODY}
    >
      <Select
        id="metadata-fallback-source"
        ref={select}
        value={shown}
        onChange={(event) => change(event.target.value)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} title={o.title} data-unavailable={o.unavailable}>
            {o.label}
          </option>
        ))}
      </Select>
    </Row>
  );
}

const HELP_BODY = (
  <>
    Where artist, album and track metadata comes from:
    <ul>
      <li>
        <strong>Spotify</strong>: official Spotify; connect your account with the Spotify tile under
        Services.
      </li>
      <li>
        <strong>Spotify (no auth)</strong>: the same Spotify catalog with no account needed. It is
        unofficial and best-effort (it may break if Spotify changes its site) and cannot read your
        own library or playlists.
      </li>
      <li>
        <strong>Deezer</strong> and <strong>iTunes</strong>: free, no account.
      </li>
      <li>
        <strong>MusicBrainz</strong>: free; the public server allows 1 request a second. Set up a
        self-hosted server in the MusicBrainz tile under Services.
      </li>
      <li>
        <strong>Discogs</strong>: needs a personal access token.
      </li>
      <li>
        <strong>JioSaavn</strong>: opt in under System, Experimental. Strong for Indian and
        Bollywood catalogs.
      </li>
    </ul>
    Tip: pick <strong>Spotify (no auth)</strong> and connect a real Spotify account later, and the
    official account is used normally; no-auth is only used while Spotify is rate-limited, then it
    switches back by itself.
  </>
);

/** the Library discography source, which library-discography-source.ts
 *  used to slip in under the picker. it saves at once, on its own */
function DiscographyPicker({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const value = normalizeLibraryDiscographySource(
    sec(settings, 'metadata').library_discography_source,
  );
  const [busy, setBusy] = useState(false);

  async function change(raw: string) {
    const source = normalizeLibraryDiscographySource(raw);
    setBusy(true);
    try {
      const response = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ metadata: { library_discography_source: source } }),
      });
      const result = (await response.json()) as { success?: boolean; error?: string };
      if (!response.ok || result.success !== true)
        throw new Error(result.error || `Settings save failed with HTTP ${response.status}`);
      store.assume('metadata', { library_discography_source: source });
      toast('Library discography source saved', 'success');
    } catch (error) {
      console.error('Failed to save Library discography source:', error);
      toast(
        error instanceof Error ? error.message : 'Failed to save Library discography source',
        'error',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Row
      field
      id="library-discography-source-group"
      label="Library discography source"
      htmlFor="library-discography-source"
      help="Controls album, EP and single catalogues shown in Library artist views. Automatic tries iTunes, then Deezer."
    >
      <Select
        id="library-discography-source"
        value={value}
        disabled={busy}
        onChange={(event) => void change(event.target.value)}
        options={LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS.map((o) => ({
          value: o.value,
          label: o.label,
        }))}
      />
    </Row>
  );
}

export function MetadataSourceGroup({ store }: { store: SettingsStore }) {
  return (
    <div data-ui="" data-music-only="">
      <SectionLabel
        title="Metadata source"
        hint="Where artist, album and track details come from"
      />
      <Card id="stg-metadata-source">
        <MetadataPicker store={store} />
        <DiscographyPicker store={store} />
      </Card>
    </div>
  );
}

// ── the service cards ──

/** one service's card as the API Configuration group had it. the header
 *  is hidden wherever the card shows (the group is hidden, the tile modal
 *  hides headers); the tiles read its dot's colour */
export function ServiceCard({
  service,
  colour,
  title,
  titleClass,
  id,
  extra,
  children,
}: {
  service: string;
  colour: string;
  title: string;
  titleClass?: string;
  id?: string;
  extra?: Record<string, string>;
  children: ReactNode;
}) {
  const { wrap, open } = useServicePlace(service, CARD_ORDER.indexOf(service));
  return createPortal(
    <div
      className="api-service-frame stg-service"
      id={id}
      data-service={service}
      data-react-panel=""
      {...extra}
    >
      <div
        className="stg-service-header"
        onClick={(event) => event.currentTarget.parentElement?.classList.toggle('expanded')}
      >
        <span className="stg-service-dot" style={{ color: colour }}></span>
        <h4 className={titleClass ? `service-title ${titleClass}` : 'service-title'}>{title}</h4>
        <span className="stg-service-chevron">›</span>
      </div>
      {/* collapsed by default as an accordion; in the modal it is the content */}
      <div className="stg-service-body" style={open ? { display: 'block' } : undefined}>
        {children}
      </div>
    </div>,
    wrap,
  );
}

/** the music cards in page order (each part's cards keep it at home) */
const CARD_ORDER = [
  'spotify',
  'itunes',
  'deezer',
  'discogs',
  'tidal',
  'qobuz',
  'concerts',
  'lastfm',
  'genius',
  'pollinations',
  'artist-fanart',
  'musicbrainz',
  'acoustid',
  'listenbrainz',
  'hydrabase',
];

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="form-group">
      {/* a label never ends in a colon (the cards still pass one) */}
      <label>{label.replace(/:\s*$/, '')}</label>
      {children}
    </div>
  );
}

/** a card's Test: the sheet's foot presses it (one Test per sheet), so it
 *  stays in the card, out of sight, and sends what it always sent */
export function TestButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="test-button" type="button" data-svc-test="" hidden onClick={onClick}>
      Test Connection
    </button>
  );
}

/** a text field for one key, as settings.js loaded it (`|| ''`) */
export function useText(store: SettingsStore, section: string, key: string) {
  const settings = useSettings(store);
  return {
    value: text(sec(settings, section)[key] || ''),
    onChange: (value: string) => store.set(section, key, value),
  };
}

/** a redirect uri: the saved one, else the default, once loaded; the
 *  Current Redirect URI line under it follows what is typed */
function useRedirect({
  store,
  section,
  id,
}: {
  store: SettingsStore;
  section: keyof typeof REDIRECT_DEFAULTS;
  id: string;
}) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const fallback = REDIRECT_DEFAULTS[section];
  const value = loads ? text(sec(settings, section).redirect_uri || fallback) : '';
  // what was typed stays on screen, an emptied field too: the page filled the
  // default once, on load, and showed what was typed after it
  const draft = useDraft(value, (raw) => store.set(section, 'redirect_uri', raw));
  useEffect(() => draft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return {
    input: (
      <input
        type="text"
        id={`${id}-redirect-uri`}
        placeholder={fallback}
        value={draft.value}
        onChange={(event) => draft.onChange(event.target.value)}
      />
    ),
    shown: draft.value || fallback,
  };
}

/** settings.js authenticateSpotify / Tidal / Deezer: save everything, then
 *  open the service's sign-in */
async function authenticate(name: string, path: string, errorContext?: string) {
  const w = shell();
  try {
    w.showLoadingOverlay?.(`Saving credentials and starting ${name} authentication...`);
    await w.saveSettings?.();
    toast(`${name} authentication started`, 'success');
    const opened = window.open(path, '_blank');
    if (name === 'Spotify')
      (window as Window & { _spotifyAuthWindow?: unknown })._spotifyAuthWindow = opened;
  } catch (error) {
    console.error(`Error authenticating ${name}:`, error);
    toast(`Failed to start ${name} authentication`, 'error', errorContext);
  } finally {
    w.hideLoadingOverlay?.();
  }
}

function SpotifyCard({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const status = useStatus();
  const help = useHelp();
  const id = useText(store, 'spotify', 'client_id');
  const secret = useText(store, 'spotify', 'client_secret');
  const redirect = useRedirect({ store, section: 'spotify', id: 'spotify' });
  const meta = sec(settings, 'metadata');
  const enrich = loads ? meta.spotify_free_enrichment !== false : false;

  // settings.js syncSpotifySettingsAuthState, once /status has spoken
  const spotify = status?.spotify;
  const attention = spotify
    ? spotify.authenticated !== true ||
      !!(spotify.rate_limited && spotify.rate_limit) ||
      (spotify.post_ban_cooldown ?? 0) > 0
    : null;

  async function disconnect() {
    const w = shell();
    if (
      !(await w.showConfirmDialog?.({
        title: 'Disconnect Spotify',
        message: 'Disconnect Spotify? Spotify-specific actions will stop until you reauthenticate.',
      }))
    )
      return;
    try {
      w.showLoadingOverlay?.('Disconnecting Spotify...');
      const response = await fetch('/api/spotify/disconnect', { method: 'POST' });
      const data = (await response.json()) as {
        success?: boolean;
        message?: string;
        error?: string;
        source?: string;
      };
      if (data.success) {
        toast(data.message || 'Spotify disconnected.', 'success');
        syncMetadataSourceSelection(store, data.source || 'deezer');
        await Promise.resolve(w.fetchAndUpdateServiceStatus?.());
      } else {
        toast(`Failed to disconnect: ${data.error}`, 'error');
      }
    } catch (error) {
      console.error('Error disconnecting Spotify:', error);
      toast('Failed to disconnect Spotify', 'error');
    } finally {
      w.hideLoadingOverlay?.();
    }
  }

  const bits = helpBits(ENRICH_HELP, help);
  return (
    <ServiceCard
      service="spotify"
      colour={attention ? '#f1c40f' : '#1DB954'}
      title="Spotify"
      titleClass="spotify-title"
    >
      <Field label="Client ID">
        <input
          type="text"
          id="spotify-client-id"
          placeholder="Spotify Client ID"
          value={id.value}
          onChange={(event) => id.onChange(event.target.value)}
        />
      </Field>
      <Field label="Client Secret">
        <SecretInput
          id="spotify-client-secret"
          placeholder="Spotify Client Secret"
          value={secret.value}
          onChange={secret.onChange}
        />
      </Field>
      <Field label="Redirect URI">{redirect.input}</Field>
      <div className="callback-info">
        <div className="callback-label">Current Redirect URI:</div>
        <div className="callback-url" id="spotify-callback-display">
          {redirect.shown}
        </div>
        <div className="callback-help">
          Add this URL to your Spotify app's 'Redirect URIs' in the Spotify Developer Dashboard.
          Spotify requires HTTPS for any URI that isn't loopback — on a NAS or remote server, enter
          an HTTPS proxy URL in the Redirect URI field above instead (see Help &amp; Docs → Getting
          Started).
        </div>
      </div>
      <div className="form-actions">
        <button
          className="auth-button"
          onClick={() => void authenticate('Spotify', '/auth/spotify', 'gs-connecting')}
        >
          Authenticate
        </button>{' '}
        <button
          className="auth-button disconnect-button"
          id="spotify-disconnect-btn"
          // shown while /status says Spotify is signed in: the old page's
          // syncSpotifySettingsAuthState did this, and the port lost it
          hidden={spotify?.authenticated !== true}
          onClick={() => void disconnect()}
        >
          Disconnect
        </button>{' '}
        <button
          className="test-button"
          type="button"
          data-svc-test=""
          hidden
          onClick={() => void testService(store, 'spotify')}
        >
          Test Connection
        </button>
      </div>
      <div
        className="form-group"
        style={{
          marginTop: '14px',
          borderTop: '1px solid rgba(255,255,255,0.08)',
          paddingTop: '14px',
        }}
      >
        <div className="setting-row" style={{ flex: 1, minWidth: 0 }}>
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              cursor: 'pointer',
              fontWeight: 'normal',
              margin: 0,
            }}
          >
            <input
              type="checkbox"
              id="metadata-spotify-free-enrichment"
              checked={enrich}
              onChange={(event) =>
                store.set('metadata', 'spotify_free_enrichment', event.target.checked)
              }
            />{' '}
            <span>Use Spotify (no auth) for background enrichment</span>
          </label>
          {bits.icon}
        </div>
        <div className="setting-help-text setting-help-body" hidden={!help.open}>
          {ENRICH_HELP}
        </div>
        {bits.line}
      </div>
    </ServiceCard>
  );
}

const ENRICH_HELP = (
  <>
    Runs the background metadata enrichment worker on the no-auth Spotify source instead of this
    connected account — keeping bulk enrichment off your official API quota (and dodging rate-limit
    bans), so your account is reserved for interactive search and playlist sync.{' '}
    <strong>On by default.</strong> Turn off to enrich through your connected account instead. Works
    even with no account connected. Note: the no-auth source can't supply artist genres.
  </>
);

const ITUNES_COUNTRIES: Array<[string, string]> = [
  ['US', 'United States (US)'],
  ['GB', 'United Kingdom (GB)'],
  ['CA', 'Canada (CA)'],
  ['AU', 'Australia (AU)'],
  ['DE', 'Germany (DE)'],
  ['FR', 'France (FR)'],
  ['JP', 'Japan (JP)'],
  ['KR', 'South Korea (KR)'],
  ['BR', 'Brazil (BR)'],
  ['SE', 'Sweden (SE)'],
  ['NL', 'Netherlands (NL)'],
  ['IT', 'Italy (IT)'],
  ['ES', 'Spain (ES)'],
  ['MX', 'Mexico (MX)'],
  ['IN', 'India (IN)'],
  ['RU', 'Russia (RU)'],
];

function ItunesCard({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const country = text(sec(settings, 'itunes').country || 'US');
  return (
    <ServiceCard
      service="itunes"
      colour="#fc3c44"
      title="iTunes / Apple Music"
      titleClass="itunes-title"
    >
      <Field label="Storefront Country">
        <select
          id="itunes-country"
          value={country}
          onChange={(event) => store.set('itunes', 'country', event.target.value || 'US')}
        >
          {ITUNES_COUNTRIES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </Field>
      <div className="callback-info">
        <div className="callback-help">
          Sets the primary Apple Music storefront. Region-specific albums are auto-searched across
          other storefronts as fallback.
        </div>
      </div>
      <div className="form-actions">
        <TestButton onClick={() => void testService(store, 'itunes')} />
      </div>
    </ServiceCard>
  );
}

function DeezerCard({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const appId = useText(store, 'deezer', 'app_id');
  const appSecret = useText(store, 'deezer', 'app_secret');
  const redirect = useRedirect({ store, section: 'deezer', id: 'deezer' });
  // the same token as the Deezer source panel (deezer_download.arl)
  const arl = text(sec(settings, 'deezer_download').arl || '');

  // the card holds two logins: the oauth app (favorites, playlists) and an
  // ARL (playlists + downloads). each has its own check: run both, say both
  async function test() {
    if (!arl.trim()) return testService(store, 'deezer');
    // an untouched field holds the mask: empty tests the saved one (#870)
    const sent = arl === REDACTED_SECRET_SENTINEL ? '' : arl;
    const w = shell();
    try {
      await flushFirst(store);
      w.showLoadingOverlay?.('Testing Deezer connection...');
      const post = (url: string, body: unknown) =>
        fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
          .then((r) => r.json() as Promise<Section_>)
          .catch(() => ({ success: false, error: 'request failed' }) as Section_);
      const [api, arlRes] = await Promise.all([
        post('/api/test-connection', { service: 'deezer' }),
        post('/api/deezer-download/test', { arl: sent }),
      ]);
      const apiMsg = api.success
        ? text(api.message) || 'Deezer reachable'
        : `Deezer failed: ${text(api.error)}`;
      const arlMsg = arlRes.success
        ? `ARL: ${text(arlRes.user) || 'Unknown'} (${text(arlRes.tier) || 'Free'})`
        : `ARL failed: ${text(arlRes.error) || 'rejected'}`;
      const ok = !!api.success && !!arlRes.success;
      const warned = text(api.message).trim().startsWith('⚠');
      toast(
        `${apiMsg} · ${arlMsg}`,
        !ok ? 'error' : warned ? 'warning' : 'success',
        ok ? undefined : 'gs-connecting',
      );
    } finally {
      w.hideLoadingOverlay?.();
    }
  }

  return (
    <ServiceCard
      service="deezer"
      colour="#A238FF"
      title="Deezer (Favorites & Playlists)"
      titleClass="deezer-title"
    >
      <Field label="App ID">
        <input
          type="text"
          id="deezer-app-id"
          placeholder="Deezer App ID"
          value={appId.value}
          onChange={(event) => appId.onChange(event.target.value)}
        />
      </Field>
      <Field label="App Secret">
        <SecretInput
          id="deezer-app-secret"
          placeholder="Deezer App Secret"
          value={appSecret.value}
          onChange={appSecret.onChange}
        />
      </Field>
      <Field label="Redirect URI">{redirect.input}</Field>
      <div className="callback-info">
        <div className="callback-label">Current Redirect URI:</div>
        <div className="callback-url" id="deezer-callback-display">
          {redirect.shown}
        </div>
        <div className="callback-help">
          Add this URL to your Deezer app at developers.deezer.com
        </div>
      </div>
      <div className="form-actions">
        <button className="auth-button" onClick={() => void authenticate('Deezer', '/auth/deezer')}>
          Authenticate
        </button>
      </div>
      <div
        className="callback-info"
        style={{
          marginTop: '10px',
          borderTop: '1px solid rgba(255,255,255,0.05)',
          paddingTop: '10px',
        }}
      >
        <div className="callback-help" style={{ marginBottom: '8px' }}>
          Or use an <strong>ARL token</strong> for playlist access and downloads (no OAuth app
          needed):
        </div>
      </div>
      <Field label="ARL Token">
        <SecretInput
          id="deezer-connection-arl"
          placeholder="Paste your ARL cookie token here"
          value={arl}
          onChange={(value) => store.set('deezer_download', 'arl', value)}
        />
      </Field>
      <div className="callback-info">
        <div className="callback-help">
          Log into{' '}
          <a href="https://www.deezer.com" target="_blank" style={{ color: '#A238FF' }}>
            deezer.com
          </a>{' '}
          → DevTools (F12) → Application → Cookies → copy the <code>arl</code> value. Provides
          playlist access + downloads.
        </div>
      </div>
      <div className="form-actions">
        <TestButton onClick={() => void test()} />
      </div>
    </ServiceCard>
  );
}

function DiscogsCard({ store }: { store: SettingsStore }) {
  const token = useText(store, 'discogs', 'token');
  return (
    <ServiceCard service="discogs" colour="#e0d4b8" title="Discogs">
      <Field label="Personal Access Token">
        <SecretInput
          id="discogs-token"
          placeholder="Discogs Personal Access Token"
          value={token.value}
          onChange={token.onChange}
        />
      </Field>
      <div className="callback-info">
        <div className="callback-help">
          Get your free token from{' '}
          <a
            href="https://www.discogs.com/settings/developers"
            target="_blank"
            style={{ color: '#ffff64' }}
          >
            Discogs Developer Settings
          </a>
        </div>
        <div className="callback-help">
          Click "Generate new token" — no app registration needed. Provides 60 req/min and cover art
          in search results.
        </div>
      </div>
      <div className="form-actions">
        <TestButton onClick={() => void testService(store, 'discogs')} />
      </div>
    </ServiceCard>
  );
}

function TidalCard({ store }: { store: SettingsStore }) {
  const id = useText(store, 'tidal', 'client_id');
  const secret = useText(store, 'tidal', 'client_secret');
  const redirect = useRedirect({ store, section: 'tidal', id: 'tidal' });

  // clears the saved token: for when re-authenticating does not pick up
  // newly added scopes. Authenticate again afterwards for a clean grant
  async function disconnect() {
    if (
      !window.confirm(
        "Disconnect Tidal? Saved token will be cleared and you'll need to re-authenticate.",
      )
    )
      return;
    const w = shell();
    try {
      w.showLoadingOverlay?.('Disconnecting Tidal...');
      const resp = await fetch('/api/tidal/disconnect', { method: 'POST' });
      const data = (await resp.json()) as { success?: boolean; error?: string };
      if (resp.ok && data.success) {
        toast(
          'Tidal disconnected. Click Authenticate to reconnect with current scopes.',
          'success',
        );
      } else {
        toast(`Disconnect failed: ${data.error || 'unknown error'}`, 'error');
      }
    } catch (error) {
      console.error('Error disconnecting Tidal:', error);
      toast('Failed to disconnect Tidal', 'error');
    } finally {
      w.hideLoadingOverlay?.();
    }
  }

  return (
    <ServiceCard
      service="tidal"
      colour="#ff6600"
      title="Tidal (Playlists & Metadata)"
      titleClass="tidal-title"
    >
      <Field label="Client ID">
        <input
          type="text"
          id="tidal-client-id"
          placeholder="Tidal Client ID"
          value={id.value}
          onChange={(event) => id.onChange(event.target.value)}
        />
      </Field>
      <Field label="Client Secret">
        <SecretInput
          id="tidal-client-secret"
          placeholder="Tidal Client Secret"
          value={secret.value}
          onChange={secret.onChange}
        />
      </Field>
      <Field label="Redirect URI">{redirect.input}</Field>
      <div className="callback-info">
        <div className="callback-label">Current Redirect URI:</div>
        <div className="callback-url" id="tidal-callback-display">
          {redirect.shown}
        </div>
        <div className="callback-help">Add this URL to your Tidal app configuration</div>
      </div>
      <div className="form-actions">
        <button className="auth-button" onClick={() => void authenticate('Tidal', '/auth/tidal')}>
          Authenticate
        </button>{' '}
        <button
          className="auth-button"
          style={{ marginLeft: '8px' }}
          onClick={() => void disconnect()}
        >
          Disconnect
        </button>{' '}
        <TestButton onClick={() => void testService(store, 'tidal')} />
      </div>
    </ServiceCard>
  );
}

// ── Qobuz ──

type QobuzAuth = QobuzAuthState;

type Said = { text: string; color?: string };
const QOBUZ_ERR = '#ff5555';

function QobuzCard({ store }: { store: SettingsStore }) {
  const [auth, setAuth] = useState<QobuzAuth | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState('');
  const [login, setLogin] = useState<{ busy: boolean; said: Said }>({
    busy: false,
    said: { text: '' },
  });
  const [byToken, setByToken] = useState<{ busy: boolean; said: Said }>({
    busy: false,
    said: { text: '' },
  });

  useEffect(() => onQobuzAuth(setAuth), []);

  async function connect() {
    const address = email.trim();
    if (!address || !password) {
      toast('Please enter your Qobuz email and password', 'warning');
      return;
    }
    setLogin({ busy: true, said: { text: '' } });
    let said: Said = { text: '' };
    try {
      const resp = await fetch('/api/qobuz/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: address, password }),
      });
      const data = (await resp.json()) as { success?: boolean; error?: string };
      if (data.success) {
        toast('Qobuz connected successfully!', 'success');
        setPassword('');
        void checkQobuzAuthStatus();
      } else {
        said = { text: data.error || 'Login failed', color: QOBUZ_ERR };
        toast(data.error || 'Qobuz login failed', 'error');
      }
    } catch (error) {
      console.error('Qobuz login error:', error);
      said = { text: 'Connection error', color: QOBUZ_ERR };
      toast('Failed to connect to Qobuz', 'error');
    } finally {
      setLogin({ busy: false, said });
    }
  }

  async function connectToken() {
    const value = token.trim();
    if (!value) {
      toast('Please paste your Qobuz auth token', 'warning');
      return;
    }
    setByToken({ busy: true, said: { text: '' } });
    let said: Said = { text: '' };
    try {
      const resp = await fetch('/api/qobuz/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: value }),
      });
      const data = (await resp.json()) as { success?: boolean; error?: string };
      if (data.success) {
        toast('Qobuz connected via token!', 'success');
        setToken('');
        void checkQobuzAuthStatus();
      } else {
        said = { text: data.error || 'Token login failed', color: QOBUZ_ERR };
        toast(data.error || 'Qobuz token login failed', 'error');
      }
    } catch (error) {
      console.error('Qobuz token login error:', error);
      said = { text: 'Connection error', color: QOBUZ_ERR };
      toast('Failed to connect to Qobuz', 'error');
    } finally {
      setByToken({ busy: false, said });
    }
  }

  async function logout() {
    try {
      await fetch('/api/qobuz/auth/logout', { method: 'POST' });
      toast('Qobuz disconnected', 'success');
      void checkQobuzAuthStatus();
    } catch (error) {
      console.error('Qobuz logout error:', error);
    }
  }

  const user = auth?.user || {};
  const loggedIn = !!auth?.authenticated;
  return (
    <ServiceCard
      service="qobuz"
      colour="#4285f4"
      title="Qobuz (Metadata & Enrichment)"
      titleClass="qobuz-title"
    >
      <div
        id="qobuz-connection-logged-in"
        style={{ display: loggedIn ? 'flex' : 'none', marginBottom: '8px' }}
      >
        <span
          id="qobuz-connection-user-info"
          className="setting-help-text"
          style={{ color: '#4caf50' }}
        >
          {loggedIn
            ? `Connected: ${user.display_name || 'Qobuz User'} (${user.subscription || 'Active'})`
            : ''}
        </span>{' '}
        <button className="auth-button" style={{ marginLeft: '8px' }} onClick={() => void logout()}>
          Disconnect
        </button>
      </div>
      <div
        id="qobuz-connection-form"
        style={auth ? { display: loggedIn ? 'none' : 'block' } : undefined}
      >
        <Field label="Email">
          <input
            type="email"
            id="qobuz-connection-email"
            className="form-input"
            placeholder="Qobuz email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>
        <Field label="Password">
          <input
            type="password"
            id="qobuz-connection-password"
            className="form-input"
            placeholder="Qobuz password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <div className="form-actions">
          <button
            className="auth-button"
            id="qobuz-connection-login-btn"
            disabled={login.busy}
            onClick={() => void connect()}
          >
            {login.busy ? 'Connecting...' : 'Connect Qobuz'}
          </button>{' '}
          <span
            id="qobuz-connection-status"
            className="setting-help-text"
            style={{ marginLeft: '8px', color: login.said.color }}
          >
            {login.said.text}
          </span>
        </div>
      </div>
      <div className="setting-help-text" style={{ marginTop: '6px' }}>
        Connects Qobuz for metadata enrichment (ISRC, labels, copyright). Also used for downloads if
        Qobuz is your download source.
      </div>
      <div
        className="callback-info"
        style={{
          marginTop: '10px',
          borderTop: '1px solid rgba(255,255,255,0.05)',
          paddingTop: '10px',
        }}
      >
        <div className="callback-help" style={{ marginBottom: '8px' }}>
          <strong>Alternative: Auth Token</strong> — If email/password login fails (CAPTCHA), paste
          your token directly:
        </div>
      </div>
      <Field label="Auth Token">
        <input
          type="password"
          id="qobuz-connection-token"
          className="form-input"
          placeholder="Paste your Qobuz auth token here"
          value={token}
          onChange={(event) => setToken(event.target.value)}
        />
      </Field>
      <div className="form-actions">
        <button
          className="auth-button"
          id="qobuz-token-login-btn"
          disabled={byToken.busy}
          onClick={() => void connectToken()}
        >
          {byToken.busy ? 'Connecting...' : 'Connect with Token'}
        </button>{' '}
        <span
          id="qobuz-token-status"
          className="setting-help-text"
          style={{ marginLeft: '8px', color: byToken.said.color }}
        >
          {byToken.said.text}
        </span>
      </div>
      <div className="callback-info">
        <div className="callback-help">
          To get your token: log into{' '}
          <a href="https://play.qobuz.com" target="_blank" style={{ color: '#4285f4' }}>
            play.qobuz.com
          </a>{' '}
          → DevTools (F12) → Network tab → find any request to <code>www.qobuz.com/api.json</code> →
          look in the request headers for <code>X-User-Auth-Token</code> → copy that value.
        </div>
      </div>
      <div className="form-actions">
        <TestButton onClick={() => void testService(store, 'qobuz')} />
      </div>
    </ServiceCard>
  );
}

export function MetadataServiceCards({ store }: { store: SettingsStore }) {
  return (
    <>
      <SpotifyCard store={store} />
      <ItunesCard store={store} />
      <DeezerCard store={store} />
      <DiscogsCard store={store} />
      <TidalCard store={store} />
      <QobuzCard store={store} />
    </>
  );
}

// ── MusicBrainz ──

let mbError: string | null = null;
/** what is wrong with the MusicBrainz server fields: every save waits while
 *  it is set, as the page refused its whole save */
export const musicbrainzError = () => mbError;

export function MusicbrainzCard({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const saved = sec(settings, 'musicbrainz');
  const [draft, setDraft] = useState<{ url: string; mode: string; rate: string } | null>(null);

  // each load shows what is saved (settings.js loadMusicBrainzServerSettings)
  useEffect(() => {
    setDraft(null);
    mbError = null;
  }, [loads]);

  const base = loads
    ? { url: text(saved.base_url || ''), ...musicbrainzRate(settings) }
    : { url: '', mode: 'fixed', rate: '1' };
  const shown = draft ?? { url: base.url, mode: base.mode, rate: base.rate };

  function change(next: { url: string; mode: string; rate: string }) {
    setDraft(next);
    const keys = musicbrainzKeys(next.url, next.mode, next.rate);
    if ('error' in keys) {
      mbError = keys.error;
      return;
    }
    mbError = null;
    store.patch('musicbrainz', keys);
  }

  return (
    <ServiceCard
      service="musicbrainz"
      colour="#ba478f"
      title="MusicBrainz"
      id="musicbrainz-server-settings"
    >
      <div className="form-group">
        <label htmlFor="musicbrainz-base-url">MusicBrainz server URL</label>
        <input
          type="url"
          id="musicbrainz-base-url"
          placeholder="https://musicbrainz.org"
          aria-describedby="musicbrainz-server-help"
          value={shown.url}
          onChange={(event) => change({ ...shown, url: event.target.value })}
        />
        <small className="settings-hint" id="musicbrainz-server-help">
          Optional: enter your self-hosted server address. Leave blank to use public MusicBrainz.
          Applies to search, matching and enrichment as soon as settings are saved.
        </small>
      </div>
      <div className="form-group">
        <label htmlFor="musicbrainz-rate-mode">MusicBrainz request rate</label>
        <select
          id="musicbrainz-rate-mode"
          aria-describedby="musicbrainz-rate-help"
          value={shown.mode}
          onChange={(event) => change({ ...shown, mode: event.target.value })}
        >
          <option value="fixed">Fixed limit</option>
          <option value="adaptive">No limit (adaptive)</option>
        </select>
        <label htmlFor="musicbrainz-request-rate">Maximum requests/second</label>
        <input
          type="number"
          id="musicbrainz-request-rate"
          min="0.001"
          step="any"
          aria-describedby="musicbrainz-rate-help"
          disabled={shown.mode === 'adaptive'}
          value={shown.rate}
          onChange={(event) => change({ ...shown, rate: event.target.value })}
        />
        <small className="settings-hint" id="musicbrainz-rate-help">
          For a fixed limit, enter requests/second (for example, 10). With No limit (adaptive),
          SoulSync will try to find the maximum number of requests/second your server can handle.
          Only use this when you host the MusicBrainz server yourself and it has no other traffic.
          Public MusicBrainz always keeps its rate limit. Environment overrides take precedence over
          these fields. Your server needs search indexes; use its final URL.
        </small>
      </div>

      <div className="form-actions">
        <TestButton onClick={() => void testService(store, 'musicbrainz')} />
      </div>
    </ServiceCard>
  );
}

// ── Hydrabase ──

type Said_ = { text: string; color?: string };
const HB_IDLE = 'rgba(255,255,255,0.4)';

export function HydrabaseCard({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const hb = sec(settings, 'hydrabase');
  const url = text(hb.url || '');
  const apiKey = text(hb.api_key || '');
  const connected = useHydrabase();
  const [said, setSaid] = useState<Said_>({ text: '' });

  // each load asks whether it is connected, and only ever says so
  useEffect(() => {
    if (!loads) return;
    fetch('/api/hydrabase/status')
      .then((r) => r.json() as Promise<{ connected?: boolean }>)
      .then((s) => {
        if (!s.connected) return;
        hydrabaseLink.set(true);
        setSaid({ text: 'Connected', color: '#4caf50' });
      })
      .catch(() => {});
  }, [loads]);

  async function toggle() {
    const u = url.trim();
    const key = apiKey.trim();
    if (!u || !key) {
      setSaid((s) => ({ ...s, text: 'URL and API Key required' }));
      return;
    }
    // save first
    await shell().saveSettings?.(true);
    try {
      const statusRes = await fetch('/api/hydrabase/status');
      const statusData = (await statusRes.json()) as { connected?: boolean };
      if (statusData.connected) {
        await fetch('/api/hydrabase/disconnect', { method: 'POST' });
        hydrabaseLink.set(false);
        setSaid({ text: 'Disconnected', color: HB_IDLE });
        // the picker leaves it for iTunes if it was on it
        if (savedSource(store.getState().settings) === 'hydrabase')
          store.patch('metadata', sourceKeys('itunes'));
        toast('Hydrabase disconnected', 'info');
      } else {
        const res = await fetch('/api/hydrabase/connect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: u, api_key: key }),
        });
        const data = (await res.json()) as { success?: boolean; error?: string };
        if (data.success) {
          hydrabaseLink.set(true);
          setSaid({ text: 'Connected', color: '#4caf50' });
          toast('Hydrabase connected', 'success');
        } else {
          setSaid((s) => ({ ...s, text: data.error || 'Connection failed' }));
          toast('Hydrabase connection failed', 'error');
        }
      }
    } catch {
      setSaid((s) => ({ ...s, text: 'Error' }));
      toast('Hydrabase connection error', 'error');
    }
  }

  return (
    <ServiceCard
      service="hydrabase"
      colour="#00b4d8"
      title="Hydrabase"
      extra={{ 'data-stg': 'connections' }}
    >
      <input type="hidden" id="hydrabase-enabled" value="false" />
      <Field label="WebSocket URL">
        <input
          type="text"
          id="hydrabase-url"
          placeholder="ws://localhost:4545"
          value={url}
          onChange={(event) => store.set('hydrabase', 'url', event.target.value)}
        />
      </Field>
      <Field label="API Key">
        <SecretInput
          id="hydrabase-api-key"
          placeholder="Hydrabase API Key"
          value={apiKey}
          onChange={(value) => store.set('hydrabase', 'api_key', value)}
        />
      </Field>
      <div className="form-group" style={{ marginBottom: '8px' }}>
        <label
          className="checkbox-label"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            cursor: 'pointer',
            padding: 0,
          }}
        >
          <input
            type="checkbox"
            id="hydrabase-auto-connect"
            checked={!!hb.auto_connect}
            onChange={(event) => store.set('hydrabase', 'auto_connect', event.target.checked)}
          />{' '}
          <span>Auto-connect on startup</span>
        </label>
      </div>
      <div className="callback-info">
        <div className="callback-help">
          P2P metadata network. When enabled and connected, replaces Spotify/iTunes as the primary
          metadata source for searches.
        </div>
      </div>
      <div className="form-actions">
        <button className="test-button" id="hydrabase-connect-btn" onClick={() => void toggle()}>
          {connected ? 'Disconnect' : 'Connect'}
        </button>{' '}
        <span
          id="hydrabase-settings-status"
          style={{ fontSize: '0.82em', color: said.color ?? HB_IDLE, marginLeft: '8px' }}
        >
          {said.text}
        </span>
      </div>
    </ServiceCard>
  );
}

// ── Experimental (System) ──

/** settings.js syncJiosaavnEnrichmentBubble: the dashboard's JioSaavn orb,
 *  the rate monitor and the enrichment rail follow the switch */
function jiosaavnBubble(enabled: boolean) {
  window.dispatchEvent(new CustomEvent('ss:jiosaavn-experimental', { detail: { enabled } }));
  shell().refreshRateMonitorExperimentalVisibility?.();
  if (enabled) shell().renderEnrichmentRail?.();
}

type Asking = { title: string; message: string; resolve: (ok: boolean) => void } | null;

function EnableDialog({ asking, close }: { asking: Asking; close: (ok: boolean) => void }) {
  const [ack, setAck] = useState(false);
  useEffect(() => setAck(false), [asking]);
  return createPortal(
    <div
      className={asking ? 'modal-overlay' : 'modal-overlay hidden'}
      id="experimental-enable-modal-overlay"
    >
      <div className="confirm-modal experimental-enable-modal">
        <div className="confirm-modal-header">
          <h2 id="experimental-enable-modal-title">
            {asking?.title || 'Enable experimental feature'}
          </h2>
          <button className="confirm-modal-close" type="button" onClick={() => close(false)}>
            ✕
          </button>
        </div>
        <div className="confirm-modal-content">
          <p id="experimental-enable-modal-message">{asking?.message || ''}</p>
          <label className="experimental-enable-ack">
            <input
              type="checkbox"
              id="experimental-enable-ack-checkbox"
              checked={ack}
              onChange={(event) => setAck(event.target.checked)}
            />{' '}
            <span>I understand the implications of enabling this experimental feature.</span>
          </label>
        </div>
        <div className="confirm-modal-actions">
          <button
            className="modal-button modal-button--secondary"
            type="button"
            onClick={() => close(false)}
          >
            Cancel
          </button>{' '}
          <button
            className="modal-button modal-button--primary"
            type="button"
            id="experimental-enable-confirm-btn"
            disabled={!ack}
            onClick={() => close(ack)}
          >
            Enable
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

const JIOSAAVN_ASK = {
  title: 'Enable JioSaavn',
  message:
    'JioSaavn uses a third-party public API proxy. Coverage and availability may change without notice. Once enabled, JioSaavn will appear as a metadata source on the Connections tab and in search.',
};
const BANDCAMP_ASK = {
  title: 'Enable Bandcamp',
  message:
    "Bandcamp has no official public search/metadata API — this uses Bandcamp's own public search and release-page endpoints. Coverage and availability may change without notice. Once enabled, Bandcamp will appear as a search source in Discover and as a metadata enrichment source for downloaded tracks.",
};

function ExperimentalRow({
  id,
  label,
  checked,
  onChange,
  help,
}: {
  id: string;
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  help: string;
}) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <div className="form-group">
      <div className="setting-row" style={{ flex: 1, minWidth: 0 }}>
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            cursor: 'pointer',
            fontWeight: 'normal',
            margin: 0,
          }}
        >
          <input
            type="checkbox"
            id={id}
            checked={checked}
            onChange={(event) => onChange(event.target.checked)}
          />{' '}
          <span>{label}</span>
        </label>
        {bits.icon}
      </div>
      <div className="setting-help-text setting-help-body" hidden={!state.open}>
        {help}
      </div>
      {bits.line}
    </div>
  );
}

export function ExperimentalSection({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const exp = sec(settings, 'experimental');
  const jiosaavn = jiosaavnOn(settings);
  const [asking, setAsking] = useState<Asking>(null);

  // each load tells the dashboard, the rate monitor and the rail
  useEffect(() => {
    if (loads) jiosaavnBubble(jiosaavnOn(store.getState().settings));
  }, [loads, store]);

  function ask(question: { title: string; message: string }) {
    return new Promise<boolean>((resolve) => {
      setAsking((previous) => {
        // a second ask answers the first one no
        previous?.resolve(false);
        return { ...question, resolve };
      });
    });
  }
  function close(ok: boolean) {
    asking?.resolve(ok);
    setAsking(null);
  }

  async function toggleJiosaavn(on: boolean) {
    if (!on) {
      // the picker cannot keep a source that is gone
      const now = store.getState().settings;
      if (savedSource(now) === 'jiosaavn') store.patch('metadata', sourceKeys('deezer'));
      store.set('experimental', 'jiosaavn_enabled', false);
      jiosaavnBubble(false);
      return;
    }
    if (!(await ask(JIOSAAVN_ASK))) return;
    store.set('experimental', 'jiosaavn_enabled', true);
    jiosaavnBubble(true);
  }

  async function toggleBandcamp(on: boolean) {
    if (on && !(await ask(BANDCAMP_ASK))) return;
    store.set('experimental', 'bandcamp_enabled', on);
  }

  return (
    <>
      <Section tab="advanced" title="Experimental" hint="Opt-in preview features" side="music">
        <div className="settings-group" data-stg="advanced" data-music-only="">
          <p className="settings-experimental-intro">
            Features in this section are optional, may change without notice, and are not held to
            the same support standard as core metadata sources.
          </p>
          <ExperimentalRow
            id="experimental-jiosaavn-enabled"
            label="Enable JioSaavn metadata source"
            checked={jiosaavn}
            onChange={(on) => void toggleJiosaavn(on)}
            help="Exposes JioSaavn as a primary metadata source on the Connections tab and in search. Free, no account — strong for Indian and Bollywood catalogs; limited to 1 request/sec via the public proxy API. Off by default."
          />
          <ExperimentalRow
            id="experimental-bandcamp-enabled"
            label="Enable Bandcamp search & enrichment"
            checked={exp.bandcamp_enabled === true}
            onChange={(on) => void toggleBandcamp(on)}
            help="Adds Bandcamp as a Discover search source and a track/album metadata enrichment source (tags, label, credits). Free, no account — uses Bandcamp's own public search and release-page endpoints (unofficial, no guaranteed uptime or rate limits). Off by default."
          />
        </div>
      </Section>
      <EnableDialog asking={asking} close={close} />
    </>
  );
}
