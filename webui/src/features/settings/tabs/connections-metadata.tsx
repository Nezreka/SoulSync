/**
 * Connections, step 10a (SETTINGS_PORT.md): the primary metadata source
 * picker, the Library discography source under it, every Metadata service
 * card (Spotify, iTunes, Deezer, Discogs, Tidal, Qobuz, MusicBrainz,
 * Hydrabase) and System's Experimental switches, which the picker reads.
 *
 * The service cards are built on the settings parts (settings-bic phase 3):
 * connection fields first, a callback address as a quiet line to copy, sign-in
 * as the sheet's main action, rare things behind More. They sit in the hidden
 * API Configuration group until a tile opens one in the service sheet
 * (connections-services.tsx ServiceFrame).
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
import {
  ActionRow,
  Btn,
  Card,
  CopyButton,
  FieldNote,
  More,
  Note,
  NumberField,
  Row,
  SecretField,
  SectionLabel,
  Select,
  SheetField,
  SheetGroup,
  StatusWords,
  Switch,
  TextField,
  type Tone,
} from '../ui';
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
import { type CardActions, ServiceFrame } from './connections-services';
import { REDACTED_SECRET_SENTINEL, useDraft } from './parts';
import { secretNote, text, useSettings } from './source-panels';

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

/** a music service's card (connections-services.tsx ServiceFrame) */
export function ServiceCard({
  service,
  colour,
  title,
  id,
  extra,
  test,
  primary,
  children,
}: {
  service: string;
  colour: string;
  title: string;
  id?: string;
  extra?: Record<string, string>;
  /** the sheet's Test: the same request the card's own button sent */
  test?: () => unknown;
  /** the sheet's main action, in place of Done (Authenticate) */
  primary?: CardActions['primary'];
  children: ReactNode;
}) {
  return (
    <ServiceFrame
      service={service}
      order={CARD_ORDER.indexOf(service)}
      colour={colour}
      title={title}
      id={id}
      extra={extra}
      test={test}
      primary={primary}
    >
      {children}
    </ServiceFrame>
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

/** a text field for one key, as settings.js loaded it (`|| ''`) */
export function useText(store: SettingsStore, section: string, key: string) {
  const settings = useSettings(store);
  return {
    value: text(sec(settings, section)[key] || ''),
    onChange: (value: string) => store.set(section, key, value),
  };
}

/** a redirect uri: the saved one, else the default, once loaded; its copy
 *  button copies what is typed (or the default when it is emptied) */
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
      <TextField
        full
        mono
        id={`${id}-redirect-uri`}
        placeholder={fallback}
        value={draft.value}
        onChange={(event) => draft.onChange(event.target.value)}
        trailing={<CopyButton value={draft.value || fallback} />}
      />
    ),
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

/** a link out, in the help line */
function Out({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

function SpotifyCard({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const status = useStatus();
  const id = useText(store, 'spotify', 'client_id');
  const secret = useText(store, 'spotify', 'client_secret');
  const redirect = useRedirect({ store, section: 'spotify', id: 'spotify' });
  const meta = sec(settings, 'metadata');
  const enrich = loads ? meta.spotify_free_enrichment !== false : false;
  const signedIn = status?.spotify?.authenticated === true;

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

  return (
    <ServiceCard
      service="spotify"
      colour="#1DB954"
      title="Spotify (Metadata, playlists and your followed artists)"
      test={() => testService(store, 'spotify')}
      primary={{
        label: signedIn ? 'Sign in again' : 'Authenticate',
        id: 'spotify-auth-btn',
        onClick: () => authenticate('Spotify', '/auth/spotify', 'gs-connecting'),
      }}
    >
      <SheetGroup title="Your Spotify app" />
      <SheetField label="Client ID" htmlFor="spotify-client-id">
        <TextField
          full
          id="spotify-client-id"
          placeholder="Spotify Client ID"
          value={id.value}
          onChange={(event) => id.onChange(event.target.value)}
        />
      </SheetField>
      <SheetField
        label="Client secret"
        htmlFor="spotify-client-secret"
        help={secretNote(secret.value)}
      >
        <SecretField
          id="spotify-client-secret"
          placeholder="Spotify Client Secret"
          value={secret.value}
          onChange={secret.onChange}
        />
      </SheetField>
      <SheetField
        label="Redirect URI"
        htmlFor="spotify-redirect-uri"
        help="Add this address to your Spotify app's Redirect URIs in the Spotify Developer Dashboard. Leave it as it is unless SoulSync runs on another machine."
      >
        {redirect.input}
      </SheetField>
      <More label="On a NAS or remote server?" names="Spotify needs HTTPS there">
        <FieldNote>
          Spotify needs HTTPS for any address that is not this machine. On a NAS or remote server,
          put an HTTPS proxy address in Redirect URI above instead (Help and Docs, Getting started).
        </FieldNote>
      </More>
      <ActionRow
        label="Signed in"
        help="Disconnecting stops Spotify-specific actions until you sign in again."
        hidden={!signedIn}
      >
        <Btn small id="spotify-disconnect-btn" hidden={!signedIn} onClick={() => void disconnect()}>
          Disconnect
        </Btn>
      </ActionRow>
      <SheetGroup title="Options" />
      <Row
        label="Use Spotify (no auth) for background enrichment"
        htmlFor="metadata-spotify-free-enrichment"
        help="Keeps bulk enrichment off your connected account."
        more={ENRICH_HELP}
      >
        <Switch
          id="metadata-spotify-free-enrichment"
          checked={enrich}
          onChange={(event) =>
            store.set('metadata', 'spotify_free_enrichment', event.target.checked)
          }
        />
      </Row>
    </ServiceCard>
  );
}

const ENRICH_HELP = (
  <>
    Runs the background metadata enrichment on the no-auth Spotify source instead of this connected
    account. That keeps bulk enrichment off your official API quota (and away from rate-limit bans),
    so your account is kept for searches and playlist sync. <strong>On by default.</strong> Turn it
    off to enrich through your connected account instead. Works with no account connected too. The
    no-auth source cannot supply artist genres.
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
      test={() => testService(store, 'itunes')}
    >
      <Note info>No key needed. iTunes answers searches as it is.</Note>
      <Row
        label="Storefront country"
        htmlFor="itunes-country"
        field
        help="The Apple Music storefront searched first. Albums only sold in another region are looked for in the other storefronts too."
      >
        <Select
          id="itunes-country"
          value={country}
          onChange={(event) => store.set('itunes', 'country', event.target.value || 'US')}
          options={ITUNES_COUNTRIES.map(([value, label]) => ({ value, label }))}
        />
      </Row>
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
      test={test}
      primary={{
        label: 'Authenticate',
        id: 'deezer-auth-btn',
        onClick: () => authenticate('Deezer', '/auth/deezer'),
      }}
    >
      <Note info>
        Searches work with no key. Sign in with your own Deezer app for favorites and playlists, or
        use an ARL token below for playlists and downloads.
      </Note>
      <SheetGroup title="Your Deezer app" />
      <SheetField label="App ID" htmlFor="deezer-app-id">
        <TextField
          full
          id="deezer-app-id"
          placeholder="Deezer App ID"
          value={appId.value}
          onChange={(event) => appId.onChange(event.target.value)}
        />
      </SheetField>
      <SheetField label="App secret" htmlFor="deezer-app-secret" help={secretNote(appSecret.value)}>
        <SecretField
          id="deezer-app-secret"
          placeholder="Deezer App Secret"
          value={appSecret.value}
          onChange={appSecret.onChange}
        />
      </SheetField>
      <SheetField
        label="Redirect URI"
        htmlFor="deezer-redirect-uri"
        help="Add this address to your Deezer app at developers.deezer.com."
      >
        {redirect.input}
      </SheetField>
      <SheetGroup title="Or an ARL token" />
      <SheetField
        label="ARL token"
        htmlFor="deezer-connection-arl"
        help={secretNote(arl, 'Playlist access and downloads, with no Deezer app needed.')}
        more={
          <>
            Sign in at <Out href="https://www.deezer.com">deezer.com</Out>, open DevTools (F12),
            then Application, Cookies, and copy the <code>arl</code> value. It is the same token as
            the Deezer download source.
          </>
        }
      >
        <SecretField
          id="deezer-connection-arl"
          placeholder="Paste your ARL cookie token here"
          value={arl}
          onChange={(value) => store.set('deezer_download', 'arl', value)}
        />
      </SheetField>
    </ServiceCard>
  );
}

function DiscogsCard({ store }: { store: SettingsStore }) {
  const token = useText(store, 'discogs', 'token');
  return (
    <ServiceCard
      service="discogs"
      colour="#e0d4b8"
      title="Discogs"
      test={() => testService(store, 'discogs')}
    >
      <SheetField
        label="Personal access token"
        htmlFor="discogs-token"
        help={secretNote(
          token.value,
          'Free, no app needed: Generate new token in Discogs Developer Settings.',
        )}
        more={
          <>
            Get it from{' '}
            <Out href="https://www.discogs.com/settings/developers">Discogs Developer Settings</Out>{' '}
            with Generate new token. No app registration needed. It gives 60 requests a minute and
            cover art in search results.
          </>
        }
      >
        <SecretField
          id="discogs-token"
          placeholder="Discogs Personal Access Token"
          value={token.value}
          onChange={token.onChange}
        />
      </SheetField>
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
    const w = shell();
    if (
      !(await w.showConfirmDialog?.({
        title: 'Disconnect Tidal',
        message:
          "Disconnect Tidal? The saved token is cleared and you'll need to authenticate again.",
      }))
    )
      return;
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
      test={() => testService(store, 'tidal')}
      primary={{
        label: 'Authenticate',
        id: 'tidal-auth-btn',
        onClick: () => authenticate('Tidal', '/auth/tidal'),
      }}
    >
      <SheetGroup title="Your Tidal app" />
      <SheetField label="Client ID" htmlFor="tidal-client-id">
        <TextField
          full
          id="tidal-client-id"
          placeholder="Tidal Client ID"
          value={id.value}
          onChange={(event) => id.onChange(event.target.value)}
        />
      </SheetField>
      <SheetField
        label="Client secret"
        htmlFor="tidal-client-secret"
        help={secretNote(secret.value)}
      >
        <SecretField
          id="tidal-client-secret"
          placeholder="Tidal Client Secret"
          value={secret.value}
          onChange={secret.onChange}
        />
      </SheetField>
      <SheetField
        label="Redirect URI"
        htmlFor="tidal-redirect-uri"
        help="Add this address to your Tidal app's configuration."
      >
        {redirect.input}
      </SheetField>
      <ActionRow
        label="Start over"
        help="Clears the saved token, for when signing in again does not pick up new permissions. Authenticate afterwards."
      >
        <Btn small id="tidal-disconnect-btn" onClick={() => void disconnect()}>
          Disconnect
        </Btn>
      </ActionRow>
    </ServiceCard>
  );
}

// ── Qobuz ──

type QobuzAuth = QobuzAuthState;

type Said = { text: string; tone?: Tone };

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
        said = { text: data.error || 'Login failed', tone: 'bad' };
        toast(data.error || 'Qobuz login failed', 'error');
      }
    } catch (error) {
      console.error('Qobuz login error:', error);
      said = { text: 'Connection error', tone: 'bad' };
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
        said = { text: data.error || 'Token login failed', tone: 'bad' };
        toast(data.error || 'Qobuz token login failed', 'error');
      }
    } catch (error) {
      console.error('Qobuz token login error:', error);
      said = { text: 'Connection error', tone: 'bad' };
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
      test={() => testService(store, 'qobuz')}
    >
      <Note info>
        Metadata enrichment (ISRC, labels, copyright). Also used for downloads when Qobuz is one of
        your download sources.
      </Note>
      <ActionRow
        id="qobuz-connection-logged-in"
        label="Signed in"
        help={
          <span id="qobuz-connection-user-info">
            {loggedIn
              ? `Connected: ${user.display_name || 'Qobuz User'} (${user.subscription || 'Active'})`
              : ''}
          </span>
        }
        hidden={!loggedIn}
      >
        <Btn small onClick={() => void logout()}>
          Disconnect
        </Btn>
      </ActionRow>
      <div id="qobuz-connection-form" hidden={!!auth && loggedIn}>
        <SheetGroup title="Sign in" />
        <SheetField label="Email" htmlFor="qobuz-connection-email">
          <TextField
            full
            type="email"
            id="qobuz-connection-email"
            placeholder="Qobuz email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </SheetField>
        <SheetField label="Password" htmlFor="qobuz-connection-password">
          <TextField
            full
            type="password"
            id="qobuz-connection-password"
            placeholder="Qobuz password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </SheetField>
        <ActionRow label="Sign in with these" help="Your password is sent to Qobuz once, not kept.">
          <StatusWords id="qobuz-connection-status" tone={login.said.tone} live>
            {login.said.text}
          </StatusWords>
          <Btn
            small
            kind="primary"
            id="qobuz-connection-login-btn"
            busy={login.busy}
            onClick={() => void connect()}
          >
            {login.busy ? 'Connecting...' : 'Connect Qobuz'}
          </Btn>
        </ActionRow>
      </div>
      <More label="Sign in with a token instead" names="when the email sign-in asks for a CAPTCHA">
        <SheetField
          label="Auth token"
          htmlFor="qobuz-connection-token"
          help="If the email sign-in fails on a CAPTCHA, paste your token here."
          more={
            <>
              Sign in at <Out href="https://play.qobuz.com">play.qobuz.com</Out>, open DevTools
              (F12), the Network tab, find a request to <code>www.qobuz.com/api.json</code>, and
              copy the <code>X-User-Auth-Token</code> request header.
            </>
          }
        >
          <TextField
            full
            type="password"
            id="qobuz-connection-token"
            placeholder="Paste your Qobuz auth token here"
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
        </SheetField>
        <ActionRow label="Sign in with the token">
          <StatusWords id="qobuz-token-status" tone={byToken.said.tone} live>
            {byToken.said.text}
          </StatusWords>
          <Btn
            small
            id="qobuz-token-login-btn"
            busy={byToken.busy}
            onClick={() => void connectToken()}
          >
            {byToken.busy ? 'Connecting...' : 'Connect with Token'}
          </Btn>
        </ActionRow>
      </More>
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
  const [error, setError] = useState<string | null>(null);

  // each load shows what is saved (settings.js loadMusicBrainzServerSettings)
  useEffect(() => {
    setDraft(null);
    setError(null);
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
      setError(keys.error);
      return;
    }
    mbError = null;
    setError(null);
    store.patch('musicbrainz', keys);
  }

  return (
    <ServiceCard
      service="musicbrainz"
      colour="#ba478f"
      title="MusicBrainz"
      id="musicbrainz-server-settings"
      test={() => testService(store, 'musicbrainz')}
    >
      <Note info>No key needed. Public MusicBrainz works as it is.</Note>
      <SheetField
        label="MusicBrainz server URL"
        htmlFor="musicbrainz-base-url"
        help="Only for your own MusicBrainz server. Leave it empty for public MusicBrainz."
        more={
          <span id="musicbrainz-server-help">
            Optional: your self-hosted server's address. Leave it empty to use public MusicBrainz.
            It applies to search, matching and enrichment as soon as settings are saved. Your server
            needs its search indexes; use its final URL.
          </span>
        }
      >
        <TextField
          full
          mono
          type="url"
          id="musicbrainz-base-url"
          placeholder="https://musicbrainz.org"
          aria-describedby="musicbrainz-server-help"
          invalid={!!error && /URL/.test(error)}
          value={shown.url}
          onChange={(event) => change({ ...shown, url: event.target.value })}
        />
      </SheetField>
      <Row
        label="Request rate"
        htmlFor="musicbrainz-rate-mode"
        field
        error={error}
        help="How fast SoulSync may ask. Public MusicBrainz always keeps its limit."
        more={
          <span id="musicbrainz-rate-help">
            For a fixed limit, enter requests a second (for example, 10). With No limit (adaptive),
            SoulSync finds the most requests a second your server can take. Only use it when you
            host the MusicBrainz server yourself and nothing else uses it. Public MusicBrainz always
            keeps its rate limit. Environment overrides take precedence over these fields.
          </span>
        }
      >
        <Select
          id="musicbrainz-rate-mode"
          aria-describedby="musicbrainz-rate-help"
          value={shown.mode}
          onChange={(event) => change({ ...shown, mode: event.target.value })}
          options={[
            { value: 'fixed', label: 'Fixed limit' },
            { value: 'adaptive', label: 'No limit (adaptive)' },
          ]}
        />
      </Row>
      <Row
        label="Maximum requests"
        htmlFor="musicbrainz-request-rate"
        sub
        field
        off={shown.mode === 'adaptive'}
        help={shown.mode === 'adaptive' ? 'Not used with No limit.' : undefined}
      >
        <NumberField
          id="musicbrainz-request-rate"
          min="0.001"
          step="any"
          unit="a second"
          aria-describedby="musicbrainz-rate-help"
          disabled={shown.mode === 'adaptive'}
          value={shown.rate}
          onChange={(event) => change({ ...shown, rate: event.target.value })}
        />
      </Row>
    </ServiceCard>
  );
}

// ── Hydrabase ──

export function HydrabaseCard({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const hb = sec(settings, 'hydrabase');
  const url = text(hb.url || '');
  const apiKey = text(hb.api_key || '');
  const connected = useHydrabase();
  const [said, setSaid] = useState<Said>({ text: '' });

  // each load asks whether it is connected, and only ever says so
  useEffect(() => {
    if (!loads) return;
    fetch('/api/hydrabase/status')
      .then((r) => r.json() as Promise<{ connected?: boolean }>)
      .then((s) => {
        if (!s.connected) return;
        hydrabaseLink.set(true);
        setSaid({ text: 'Connected', tone: 'ok' });
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
        setSaid({ text: 'Disconnected', tone: 'idle' });
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
          setSaid({ text: 'Connected', tone: 'ok' });
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
      <Note info>
        A peer-to-peer metadata network. Connected, it takes over from Spotify and iTunes as the
        primary source for searches.
      </Note>
      <SheetGroup title="Connection" />
      <SheetField label="WebSocket URL" htmlFor="hydrabase-url">
        <TextField
          full
          mono
          id="hydrabase-url"
          placeholder="ws://localhost:4545"
          value={url}
          onChange={(event) => store.set('hydrabase', 'url', event.target.value)}
        />
      </SheetField>
      <SheetField label="API key" htmlFor="hydrabase-api-key" help={secretNote(apiKey)}>
        <SecretField
          id="hydrabase-api-key"
          placeholder="Hydrabase API Key"
          value={apiKey}
          onChange={(value) => store.set('hydrabase', 'api_key', value)}
        />
      </SheetField>
      <ActionRow label="Link" help="Saves first, then opens or closes the link.">
        <StatusWords id="hydrabase-settings-status" tone={said.tone} live>
          {said.text}
        </StatusWords>
        <Btn small id="hydrabase-connect-btn" onClick={() => void toggle()}>
          {connected ? 'Disconnect' : 'Connect'}
        </Btn>
      </ActionRow>
      <SheetGroup title="Options" />
      <Row label="Connect when SoulSync starts" htmlFor="hydrabase-auto-connect">
        <Switch
          id="hydrabase-auto-connect"
          checked={!!hb.auto_connect}
          onChange={(event) => store.set('hydrabase', 'auto_connect', event.target.checked)}
        />
      </Row>
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

export function ExperimentalCard({ store }: { store: SettingsStore }) {
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
      <Card
        title="Experimental"
        hint="Optional preview features. They may change without notice and are not held to the same support standard as the core metadata sources."
        data-music-only=""
        id="stg-experimental"
      >
        <Row
          label="JioSaavn metadata source"
          htmlFor="experimental-jiosaavn-enabled"
          help="Offers JioSaavn as a primary metadata source on Connections and in search. Strong for Indian and Bollywood catalogs."
          more="Free, no account. It goes through a public proxy API, limited to one request a second. Off by default. Turning it off moves the metadata source back to Deezer if JioSaavn was picked."
        >
          <Switch
            id="experimental-jiosaavn-enabled"
            checked={jiosaavn}
            onChange={(event) => void toggleJiosaavn(event.target.checked)}
          />
        </Row>
        <Row
          label="Bandcamp search and enrichment"
          htmlFor="experimental-bandcamp-enabled"
          help="Adds Bandcamp as a search source in Discover and fills in tags, label and credits for downloaded tracks."
          more="Free, no account. It uses Bandcamp's own public search and release pages, which are unofficial: no promised uptime or rate limits. Off by default."
        >
          <Switch
            id="experimental-bandcamp-enabled"
            checked={exp.bandcamp_enabled === true}
            onChange={(event) => void toggleBandcamp(event.target.checked)}
          />
        </Row>
      </Card>
      <EnableDialog asking={asking} close={close} />
    </>
  );
}
