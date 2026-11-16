/**
 * Connections, step 10b (SETTINGS_PORT.md): the music side's hidden API
 * Configuration group, whole. Its heading with Expand All, every music
 * service card (the Metadata ones are connections-metadata.tsx; Concerts,
 * Last.fm, Genius, Pollinations, fanart.tv, AcoustID and ListenBrainz are
 * here) and the row of test buttons under them.
 *
 * Nothing shows the group itself (style.css hides it): it is where the cards
 * live between visits to the Services modal. Each card portals into a wrapper
 * kept here, before the test row (connections-services.tsx).
 */

import { type ReactNode, useState, useSyncExternalStore } from 'react';

import type { SettingsStore } from '../settings-store';

import {
  Field,
  HydrabaseCard,
  MetadataServiceCards,
  MusicbrainzCard,
  ServiceCard,
  TestButton,
  testService,
  useText,
} from './connections-metadata';
import { ServiceHome } from './connections-services';
import { useLoadedDraft } from './library-card';
import { SecretInput } from './parts';
import { text, useSettings } from './source-panels';

type Section_ = Record<string, unknown>;
type Shell = Window & {
  showToast?: (message: string, type?: string) => void;
  showLoadingOverlay?: (message?: string) => void;
  hideLoadingOverlay?: () => void;
  saveSettings?: (quiet?: boolean) => Promise<void>;
};
const shell = () => window as Shell;
const toast = (message: string, type: string) => shell().showToast?.(message, type);

function useLoads(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().loads);
}

/** a field the page saved trimmed (settings.js _cfgStr with trim): what is
 *  typed shows as typed, until the next load */
function useTrimmed(
  store: SettingsStore,
  section: string,
  key: string,
  shape: (value: string) => string = (v) => v,
) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const saved = text(((settings[section] || {}) as Section_)[key] || '');
  const draft = useLoadedDraft(store, saved, (raw) => store.set(section, key, shape(raw.trim())));
  return { value: loads ? draft.value : '', onChange: draft.onChange };
}

function useChecked(store: SettingsStore, section: string, key: string) {
  const settings = useSettings(store);
  return {
    checked: ((settings[section] || {}) as Section_)[key] === true,
    onChange: (event: { target: { checked: boolean } }) =>
      store.set(section, key, event.target.checked),
  };
}

// ── Concerts ──

function ConcertsCard({ store }: { store: SettingsStore }) {
  const tm = useTrimmed(store, 'concerts', 'ticketmaster_api_key');
  const country = useTrimmed(store, 'concerts', 'country', (v) => v.toUpperCase());
  const sl = useTrimmed(store, 'concerts', 'setlistfm_api_key');
  return (
    <ServiceCard service="concerts" colour="#00b4b3" title="Concerts">
      <div className="setting-help-text">
        Adds a <strong>Live</strong> section to artist pages. Both are optional and independent —
        set up either, both, or neither.
      </div>
      <div className="form-group">
        <label>Ticketmaster API Key:</label>
        <input
          type="text"
          id="concerts-ticketmaster-api-key"
          placeholder="Your Discovery API key"
          value={tm.value}
          onChange={(event) => tm.onChange(event.target.value)}
        />
        <div className="setting-help-text">
          Upcoming dates, venues and ticket links. Free self-service key from
          developer.ticketmaster.com (5,000 calls a day).
        </div>
      </div>
      <div className="form-group">
        <label>Concerts near you (country):</label>
        <input
          type="text"
          id="concerts-country"
          maxLength={2}
          placeholder="US"
          autoComplete="off"
          style={{ textTransform: 'uppercase', maxWidth: '90px' }}
          value={country.value}
          onChange={(event) => country.onChange(event.target.value)}
        />
        <div className="setting-help-text">
          Two letters, like US or GB. Keeps the concerts in your Discover inbox to one country.
          Leave it empty for everywhere. Artist pages always show every date.
        </div>
      </div>
      <div className="form-group">
        <label>Setlist.fm API Key:</label>
        <input
          type="text"
          id="concerts-setlistfm-api-key"
          placeholder="Your setlist.fm API key"
          value={sl.value}
          onChange={(event) => sl.onChange(event.target.value)}
        />
        <div className="setting-help-text">
          What the artist actually played at recent shows, song by song — and a setlist can be
          played straight from your library. Free key from setlist.fm/settings/api (non-commercial
          use).
        </div>
      </div>
      <div className="form-actions">
        <TestButton onClick={() => void testService(store, 'concerts')} />
      </div>
    </ServiceCard>
  );
}

// ── Last.fm ──

function LastfmCard({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const key = useText(store, 'lastfm', 'api_key');
  const secret = useText(store, 'lastfm', 'api_secret');
  const user = useTrimmed(store, 'lastfm', 'username');
  const scrobble = useChecked(store, 'lastfm', 'scrobble_enabled');
  const authorized = !!((settings.lastfm || {}) as Section_).session_key;

  // save first, so the API secret is stored
  async function authorize() {
    try {
      await shell().saveSettings?.();
      const resp = await fetch('/api/lastfm/auth-url');
      const data = (await resp.json()) as { success?: boolean; url?: string; error?: string };
      if (data.success && data.url) {
        window.open(data.url, '_blank', 'width=600,height=500');
        toast('Authorize SoulSync in the Last.fm window that opened', 'info');
      } else {
        toast(data.error || 'Could not generate auth URL', 'error');
      }
    } catch {
      toast('Failed to start Last.fm authorization', 'error');
    }
  }

  return (
    <ServiceCard service="lastfm" colour="#d51007" title="Last.fm" titleClass="lastfm-title">
      <Field label="API Key:">
        <SecretInput id="lastfm-api-key" placeholder="Last.fm API Key" {...key} />
      </Field>
      <Field label="API Secret:">
        <SecretInput
          id="lastfm-api-secret"
          placeholder="Last.fm API Secret (required for scrobbling)"
          {...secret}
        />
      </Field>
      <div className="form-group">
        <label htmlFor="lastfm-username">Username:</label>
        <input
          type="text"
          id="lastfm-username"
          placeholder="Your Last.fm username"
          value={user.value}
          onChange={(event) => user.onChange(event.target.value)}
        />
        <small>
          The Last.fm account whose listening history to import. You can correct this username here
          or on the Stats page.
        </small>
      </div>
      <div className="callback-info">
        <div className="callback-help">
          Get your API key and secret from{' '}
          <a
            href="https://www.last.fm/api/account/create"
            target="_blank"
            style={{ color: '#d51007' }}
          >
            Last.fm API Account
          </a>
        </div>
        <div className="callback-help">
          API key: used for metadata enrichment. API secret: required for scrobbling.
        </div>
      </div>
      <div className="form-group" style={{ marginTop: '12px' }}>
        <label className="checkbox-label">
          <input type="checkbox" id="lastfm-scrobble-enabled" {...scrobble} /> Scrobble plays to
          Last.fm
        </label>
      </div>
      <div className="form-actions" id="lastfm-scrobble-actions">
        <button className="test-button" onClick={() => void authorize()}>
          Authorize Scrobbling
        </button>{' '}
        <span className="setting-help-text" id="lastfm-scrobble-status">
          {loads ? (authorized ? 'Authorized' : 'Not authorized') : ''}
        </span>
      </div>
      <div className="form-actions">
        <TestButton onClick={() => void testService(store, 'lastfm')} />
      </div>
    </ServiceCard>
  );
}

// ── Genius, Pollinations, fanart.tv ──

function GeniusCard({ store }: { store: SettingsStore }) {
  const token = useText(store, 'genius', 'access_token');
  return (
    <ServiceCard service="genius" colour="#ffff64" title="Genius" titleClass="genius-title">
      <Field label="Client Access Token:">
        <SecretInput id="genius-access-token" placeholder="Genius Client Access Token" {...token} />
      </Field>
      <div className="callback-info">
        <div className="callback-help">
          Get your token from{' '}
          <a href="https://genius.com/api-clients" target="_blank" style={{ color: '#ffff64' }}>
            Genius API Clients
          </a>
        </div>
        <div className="callback-help">
          Generate a "Client Access Token" — no OAuth flow needed.
        </div>
      </div>
      <div className="form-actions">
        <TestButton onClick={() => void testService(store, 'genius')} />
      </div>
    </ServiceCard>
  );
}

/** an optional key the page saved trimmed */
function OptionalKey({
  store,
  section,
  id,
  placeholder,
}: {
  store: SettingsStore;
  section: string;
  id: string;
  placeholder: string;
}) {
  const key = useTrimmed(store, section, 'api_key');
  return (
    <Field label="API Key (optional):">
      <SecretInput id={id} placeholder={placeholder} autoComplete="off" {...key} />
    </Field>
  );
}

function PollinationsCard({ store }: { store: SettingsStore }) {
  return (
    <ServiceCard service="pollinations" colour="#7ad3a7" title="Pollinations">
      <OptionalKey
        store={store}
        section="pollinations"
        id="pollinations-api-key"
        placeholder="Pollinations API key"
      />
      <div className="callback-info">
        <div className="callback-help">
          Makes cover art for playlists, from the ⋯ menu on any playlist. Works without a key: one
          image every 45 seconds, with a small Pollinations mark in the corner.
        </div>
        <div className="callback-help">
          A key from{' '}
          <a
            href="https://enter.pollinations.ai"
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: '#7ad3a7' }}
          >
            enter.pollinations.ai
          </a>{' '}
          removes the mark and the wait.
        </div>
      </div>
      <div className="form-actions">
        <TestButton onClick={() => void testService(store, 'pollinations')} />
      </div>
    </ServiceCard>
  );
}

function FanartCard({ store }: { store: SettingsStore }) {
  return (
    <ServiceCard service="artist-fanart" colour="rgb(var(--accent-rgb))" title="fanart.tv">
      {/* the music card's own id: the video side's fanart.tv card owns fanart-api-key */}
      <OptionalKey
        store={store}
        section="fanart"
        id="artist-fanart-api-key"
        placeholder="fanart.tv personal API key"
      />
      <div className="callback-info">
        <div className="callback-help">
          HD artist photos and the wide background Plex and Jellyfin show behind an artist page,
          used by <b>Update media server metadata</b> on the Tools page. Without a key SoulSync
          still tries TheAudioDB and Deezer.
        </div>
        <div className="callback-help">
          A free key from{' '}
          <a
            href="https://fanart.tv/get-an-api-key/"
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: 'rgb(var(--accent-rgb))' }}
          >
            fanart.tv
          </a>
          . The video side's fanart.tv key is used when this is empty.
        </div>
      </div>
      <div className="form-actions">
        <TestButton onClick={() => void testService(store, 'fanart')} />
      </div>
    </ServiceCard>
  );
}

// ── AcoustID ──

/** settings.js clearQuarantine */
async function clearQuarantine() {
  const w = shell() as Shell & {
    showConfirmDialog?: (o: {
      title: string;
      message: string;
      confirmText?: string;
      destructive?: boolean;
    }) => Promise<boolean>;
  };
  if (
    !(await w.showConfirmDialog?.({
      title: 'Clear Quarantine',
      message: 'Delete all files in the quarantine folder? This cannot be undone.',
      confirmText: 'Delete',
      destructive: true,
    }))
  )
    return;
  try {
    w.showLoadingOverlay?.('Clearing quarantine folder...');
    const response = await fetch('/api/quarantine/clear', { method: 'POST' });
    const result = (await response.json()) as {
      success?: boolean;
      message?: string;
      error?: string;
    };
    if (result.success) toast(result.message || 'Quarantine cleared', 'success');
    else toast(`Failed to clear quarantine: ${result.error}`, 'error');
  } catch (error) {
    console.error('Error clearing quarantine:', error);
    toast('Failed to clear quarantine', 'error');
  } finally {
    w.hideLoadingOverlay?.();
  }
}

function AcoustidCard({ store }: { store: SettingsStore }) {
  const enabled = useChecked(store, 'acoustid', 'enabled');
  const key = useText(store, 'acoustid', 'api_key');
  return (
    <ServiceCard
      service="acoustid"
      colour="#ba55d3"
      title="AcoustID Verification"
      titleClass="acoustid-title"
    >
      <div className="form-group" style={{ marginBottom: '12px' }}>
        <label
          className="checkbox-label"
          style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}
        >
          <input type="checkbox" id="acoustid-enabled" {...enabled} />{' '}
          <span>Enable Download Verification</span>
        </label>
        <div style={{ color: '#888', fontSize: '0.8em', marginTop: '4px', marginLeft: '24px' }}>
          Verifies downloaded audio matches expected track using fingerprints. Turning it off only
          stops the check on new downloads: the AcoustID Scanner in Tools still scans when you run
          it yourself.
        </div>
      </div>
      <Field label="Application API Key:">
        <SecretInput id="acoustid-api-key" placeholder="AcoustID Application API Key" {...key} />
      </Field>
      <div className="callback-info">
        <div className="callback-help">
          Requires an <strong>Application API key</strong> (not your user account key) — register a
          free application at{' '}
          <a
            href="https://acoustid.org/new-application"
            target="_blank"
            style={{ color: '#ba55d3' }}
          >
            acoustid.org/new-application
          </a>
        </div>
        <div
          className="callback-help"
          style={{ opacity: 0.7, fontSize: '0.85em', marginTop: '4px' }}
        >
          The fpcalc fingerprint tool is automatically downloaded if needed. Failed verifications
          move files to Quarantine folder.
        </div>
      </div>
      <div
        className="form-actions"
        style={{
          marginTop: '10px',
          display: 'flex',
          gap: '8px',
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        <button className="test-button" onClick={() => void testService(store, 'acoustid')}>
          Test Connection
        </button>{' '}
        <button
          className="test-button"
          onClick={() => void clearQuarantine()}
          style={{
            background: 'rgba(255,82,82,0.15)',
            borderColor: 'rgba(255,82,82,0.3)',
            color: '#ff5252',
          }}
        >
          Clear Quarantine
        </button>
      </div>
    </ServiceCard>
  );
}

// ── ListenBrainz ──

function ListenbrainzCard({ store }: { store: SettingsStore }) {
  const base = useText(store, 'listenbrainz', 'base_url');
  const token = useText(store, 'listenbrainz', 'token');
  const user = useTrimmed(store, 'listenbrainz', 'username');
  const scrobble = useChecked(store, 'listenbrainz', 'scrobble_enabled');
  return (
    <ServiceCard
      service="listenbrainz"
      colour="#eb743b"
      title="ListenBrainz"
      titleClass="listenbrainz-title"
    >
      <Field label="API Base URL:">
        <input
          type="text"
          id="listenbrainz-base-url"
          placeholder="Leave empty for official (api.listenbrainz.org)"
          value={base.value}
          onChange={(event) => base.onChange(event.target.value)}
        />
      </Field>
      <Field label="User Token:">
        <SecretInput id="listenbrainz-token" placeholder="ListenBrainz User Token" {...token} />
      </Field>
      <Field label="Username (optional):">
        <input
          type="text"
          id="listenbrainz-username"
          placeholder="Auto-detected from token if blank"
          value={user.value}
          onChange={(event) => user.onChange(event.target.value)}
        />
      </Field>
      <div className="callback-info">
        <div className="callback-help">
          Get your token from{' '}
          <a href="https://listenbrainz.org/profile/" target="_blank" style={{ color: '#eb743b' }}>
            ListenBrainz Settings
          </a>
        </div>
        <div className="callback-help">
          Self-hosted? Enter your server URL (e.g. http://localhost:8093)
        </div>
        <div className="callback-help">
          Maloja: use http://your-server:42010/apis/listenbrainz and your Maloja API key. Start
          history sync from Stats.
        </div>
      </div>
      <div className="form-group" style={{ marginTop: '12px' }}>
        <label className="checkbox-label">
          <input type="checkbox" id="listenbrainz-scrobble-enabled" {...scrobble} /> Scrobble plays
          to ListenBrainz
        </label>
      </div>
      <div className="form-actions">
        <TestButton onClick={() => void testService(store, 'listenbrainz')} />
      </div>
    </ServiceCard>
  );
}

// ── the group ──

const TEST_ROW: Array<[string, string]> = [
  ['spotify', 'Spotify'],
  ['tidal', 'Tidal'],
  ['listenbrainz', 'ListenBrainz'],
  ['acoustid', 'AcoustID'],
  ['lastfm', 'Last.fm'],
  ['genius', 'Genius'],
];

/** settings.js toggleAllServiceAccordions: every card on the page, open or
 *  shut together */
function toggleAll(button: HTMLButtonElement) {
  const cards = [...document.querySelectorAll('#settings-page .stg-service')];
  const allOpen = cards.length > 0 && cards.every((c) => c.classList.contains('expanded'));
  for (const c of cards) c.classList.toggle('expanded', !allOpen);
  button.textContent = allOpen ? 'Expand All' : 'Collapse All';
}

export function MusicApiGroup({ store }: { store: SettingsStore }) {
  const [home, setHome] = useState<HTMLElement | null>(null);
  return (
    <div
      className="settings-group"
      data-stg="connections"
      data-music-only=""
      data-svc-legacy=""
      ref={setHome}
    >
      <h3>
        API Configuration{' '}
        <button
          className="stg-accordion-toggle"
          onClick={(event) => toggleAll(event.currentTarget)}
        >
          Expand All
        </button>
      </h3>
      <ServiceHome.Provider value={home}>
        <Cards store={store} />
      </ServiceHome.Provider>
      <div className="api-test-buttons">
        {TEST_ROW.map(([service, name], i) => (
          <Spaced key={service} first={i === 0}>
            <button className="test-button" onClick={() => void testService(store, service)}>
              Test {name}
            </button>
          </Spaced>
        ))}
      </div>
    </div>
  );
}

function Spaced({ first, children }: { first: boolean; children: ReactNode }) {
  return (
    <>
      {first ? null : ' '}
      {children}
    </>
  );
}

/** the cards, in the group's order (the wrappers keep it, see CARD_ORDER) */
function Cards({ store }: { store: SettingsStore }) {
  return (
    <>
      <MetadataServiceCards store={store} />
      <ConcertsCard store={store} />
      <LastfmCard store={store} />
      <GeniusCard store={store} />
      <PollinationsCard store={store} />
      <FanartCard store={store} />
      <MusicbrainzCard store={store} />
      <AcoustidCard store={store} />
      <ListenbrainzCard store={store} />
      <HydrabaseCard store={store} />
    </>
  );
}
