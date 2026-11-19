/**
 * Connections: the music side's hidden API Configuration group and the music
 * service cards not in connections-metadata.tsx (Concerts, Last.fm, Genius,
 * Pollinations, fanart.tv, AcoustID, ListenBrainz), on the settings parts.
 *
 * Nothing shows the group itself (style.css hides it): it is where the cards
 * live between visits to the service sheet. Each card portals into a wrapper
 * kept here (connections-services.tsx). Its Expand All and the row of six
 * Test buttons nobody could see went (settings-bic DESIGN 3.3).
 */

import { type ReactNode, useCallback, useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsStore } from '../settings-store';

import {
  ActionRow,
  Btn,
  DangerRow,
  More,
  Note,
  Row,
  SecretField,
  SheetField,
  SheetGroup,
  StatusWords,
  Switch,
  TextField,
} from '../ui';
import s from '../ui/settings.module.css';
import {
  HydrabaseCard,
  MetadataServiceCards,
  MusicbrainzCard,
  ServiceCard,
  testService,
  useText,
} from './connections-metadata';
import { ServiceHome } from './connections-services';
import { useLoadedDraft } from './library-card';
import { secretNote, text, useSettings } from './source-panels';

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
    <ServiceCard
      service="concerts"
      colour="#00b4b3"
      title="Concerts"
      test={() => testService(store, 'concerts')}
    >
      <Note info>
        Adds a <strong>Live</strong> section to artist pages. Both keys are optional and
        independent: set up either, both, or neither.
      </Note>
      <SheetGroup title="Upcoming dates" />
      <SheetField
        label="Ticketmaster API key"
        htmlFor="concerts-ticketmaster-api-key"
        help="Upcoming dates, venues and ticket links. A free self-service key from developer.ticketmaster.com (5,000 calls a day)."
      >
        <TextField
          full
          id="concerts-ticketmaster-api-key"
          placeholder="Your Discovery API key"
          value={tm.value}
          onChange={(event) => tm.onChange(event.target.value)}
        />
      </SheetField>
      <Row
        label="Concerts near you"
        htmlFor="concerts-country"
        field
        help="Two letters, like US or GB. Keeps the concerts in your Discover inbox to one country. Leave it empty for everywhere. Artist pages always show every date."
      >
        <TextField
          id="concerts-country"
          className={s.fieldCode}
          maxLength={2}
          placeholder="US"
          autoComplete="off"
          value={country.value}
          onChange={(event) => country.onChange(event.target.value)}
        />
      </Row>
      <SheetGroup title="Setlists" />
      <SheetField
        label="Setlist.fm API key"
        htmlFor="concerts-setlistfm-api-key"
        help="What the artist played at recent shows, song by song, and a setlist can be played straight from your library. A free key from setlist.fm/settings/api (non-commercial use)."
      >
        <TextField
          full
          id="concerts-setlistfm-api-key"
          placeholder="Your setlist.fm API key"
          value={sl.value}
          onChange={(event) => sl.onChange(event.target.value)}
        />
      </SheetField>
    </ServiceCard>
  );
}

// ── Last.fm ──

/** From 1 December 2026 a Last.fm API key needs a Pro subscription. The
 *  server remembers a refused key (/api/lastfm/configured says so); the card
 *  then says it plainly instead of a bare "invalid key". */
export const LASTFM_PRO_NOTE =
  'Last.fm API keys need a Pro subscription from 1 December 2026. Without one, SoulSync fills in from free sources (ListenBrainz, Deezer).';

export interface LastfmKeyStatus {
  key_rejected: boolean;
  message: string;
}

export function useLastfmKeyStatus(): [LastfmKeyStatus, () => void] {
  const [status, setStatus] = useState<LastfmKeyStatus>({ key_rejected: false, message: '' });
  const refresh = useCallback(() => {
    fetch('/api/lastfm/configured')
      .then((resp) => (resp.ok ? resp.json() : null))
      .then((data: Partial<LastfmKeyStatus> | null) => {
        if (!data) return;
        const next = { key_rejected: !!data.key_rejected, message: data.message || '' };
        // unchanged: keep the same object, so nothing re-renders
        setStatus((prev) =>
          prev.key_rejected === next.key_rejected && prev.message === next.message ? prev : next,
        );
      })
      .catch(() => {});
  }, []);
  useEffect(refresh, [refresh]);
  return [status, refresh];
}

function LastfmCard({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const key = useText(store, 'lastfm', 'api_key');
  const secret = useText(store, 'lastfm', 'api_secret');
  const user = useTrimmed(store, 'lastfm', 'username');
  const scrobble = useChecked(store, 'lastfm', 'scrobble_enabled');
  const authorized = !!((settings.lastfm || {}) as Section_).session_key;
  const [keyStatus, refreshKeyStatus] = useLastfmKeyStatus();

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
    <ServiceCard
      service="lastfm"
      colour="#d51007"
      title="Last.fm"
      test={() => testService(store, 'lastfm').then(refreshKeyStatus)}
    >
      {keyStatus.key_rejected ? (
        <div id="lastfm-key-rejected" role="status">
          <Note>{keyStatus.message}</Note>
        </div>
      ) : null}
      <SheetGroup title="Your Last.fm API account" />
      <SheetField
        label="API key"
        htmlFor="lastfm-api-key"
        help={secretNote(key.value, 'Used for metadata enrichment.')}
        more={
          <>
            Get the key and secret from{' '}
            <a href="https://www.last.fm/api/account/create" target="_blank" rel="noreferrer">
              Last.fm API Account
            </a>
            . <span id="lastfm-pro-note">{LASTFM_PRO_NOTE}</span>
          </>
        }
      >
        <SecretField id="lastfm-api-key" placeholder="Last.fm API Key" {...key} />
      </SheetField>
      <SheetField
        label="API secret"
        htmlFor="lastfm-api-secret"
        help={secretNote(secret.value, 'Needed for scrobbling.')}
      >
        <SecretField
          id="lastfm-api-secret"
          placeholder="Last.fm API Secret (required for scrobbling)"
          {...secret}
        />
      </SheetField>
      <SheetField
        label="Username"
        htmlFor="lastfm-username"
        help="The Last.fm account whose listening history is imported. It can be corrected here or on the Stats page."
      >
        <TextField
          full
          id="lastfm-username"
          placeholder="Your Last.fm username"
          value={user.value}
          onChange={(event) => user.onChange(event.target.value)}
        />
      </SheetField>
      <SheetGroup title="Scrobbling" />
      <Row
        label="Scrobble plays to Last.fm"
        htmlFor="lastfm-scrobble-enabled"
        help="ListenBrainz is the recommended scrobbler: free, no subscription."
      >
        <Switch id="lastfm-scrobble-enabled" {...scrobble} />
      </Row>
      <ActionRow
        id="lastfm-scrobble-actions"
        label="Allow scrobbling"
        help="Saves first, then opens Last.fm to let SoulSync scrobble for you."
      >
        <StatusWords id="lastfm-scrobble-status" tone={authorized ? 'ok' : 'idle'}>
          {loads ? (authorized ? 'Authorized' : 'Not authorized') : ''}
        </StatusWords>
        <Btn small onClick={() => void authorize()}>
          Authorize scrobbling
        </Btn>
      </ActionRow>
    </ServiceCard>
  );
}

// ── Genius, Pollinations, fanart.tv ──

function GeniusCard({ store }: { store: SettingsStore }) {
  const token = useText(store, 'genius', 'access_token');
  return (
    <ServiceCard
      service="genius"
      colour="#ffff64"
      title="Genius"
      test={() => testService(store, 'genius')}
    >
      <SheetField
        label="Client access token"
        htmlFor="genius-access-token"
        help={secretNote(token.value, 'Generate a Client Access Token. No sign-in flow needed.')}
        more={
          <>
            Get it from{' '}
            <a href="https://genius.com/api-clients" target="_blank" rel="noreferrer">
              Genius API Clients
            </a>{' '}
            with Generate Access Token.
          </>
        }
      >
        <SecretField id="genius-access-token" placeholder="Genius Client Access Token" {...token} />
      </SheetField>
    </ServiceCard>
  );
}

/** an optional key the page saved trimmed */
function OptionalKey({
  store,
  section,
  id,
  placeholder,
  help,
  more,
}: {
  store: SettingsStore;
  section: string;
  id: string;
  placeholder: string;
  help: string;
  more: ReactNode;
}) {
  const key = useTrimmed(store, section, 'api_key');
  return (
    <SheetField
      label="API key (optional)"
      htmlFor={id}
      help={secretNote(key.value, help)}
      more={more}
    >
      <SecretField id={id} placeholder={placeholder} autoComplete="off" {...key} />
    </SheetField>
  );
}

function PollinationsCard({ store }: { store: SettingsStore }) {
  return (
    <ServiceCard
      service="pollinations"
      colour="#7ad3a7"
      title="Pollinations"
      test={() => testService(store, 'pollinations')}
    >
      <Note info>
        Makes cover art for playlists, from the ⋯ menu on any playlist. It works without a key: one
        image every 45 seconds, with a small Pollinations mark in the corner.
      </Note>
      <OptionalKey
        store={store}
        section="pollinations"
        id="pollinations-api-key"
        placeholder="Pollinations API key"
        help="A key removes the mark and the wait."
        more={
          <>
            A key from{' '}
            <a href="https://enter.pollinations.ai" target="_blank" rel="noopener noreferrer">
              enter.pollinations.ai
            </a>{' '}
            removes the mark and the wait.
          </>
        }
      />
    </ServiceCard>
  );
}

function FanartCard({ store }: { store: SettingsStore }) {
  return (
    <ServiceCard
      service="artist-fanart"
      colour="rgb(var(--accent-rgb))"
      title="fanart.tv"
      test={() => testService(store, 'fanart')}
    >
      <Note info>
        HD artist photos and the wide background Plex and Jellyfin show behind an artist page, used
        by <b>Update media server metadata</b> on the Tools page. Without a key SoulSync still tries
        TheAudioDB and Deezer.
      </Note>
      {/* the music card's own id: the video side's fanart.tv card owns fanart-api-key */}
      <OptionalKey
        store={store}
        section="fanart"
        id="artist-fanart-api-key"
        placeholder="fanart.tv personal API key"
        help="The video side's fanart.tv key is used when this is empty."
        more={
          <>
            A free key from{' '}
            <a href="https://fanart.tv/get-an-api-key/" target="_blank" rel="noopener noreferrer">
              fanart.tv
            </a>
            . The video side's fanart.tv key is used when this is empty.
          </>
        }
      />
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
      test={() => testService(store, 'acoustid')}
    >
      <SheetField
        label="Application API key"
        htmlFor="acoustid-api-key"
        help={secretNote(key.value, 'An application key, not your user account key.')}
        more={
          <>
            Register a free application at{' '}
            <a href="https://acoustid.org/new-application" target="_blank" rel="noreferrer">
              acoustid.org/new-application
            </a>{' '}
            and use its key, not your user account key. The fpcalc fingerprint tool is downloaded
            when needed.
          </>
        }
      >
        <SecretField id="acoustid-api-key" placeholder="AcoustID Application API Key" {...key} />
      </SheetField>
      <SheetGroup title="Options" />
      <Row
        label="Verify downloads"
        htmlFor="acoustid-enabled"
        help="Checks a downloaded file is the expected track by its fingerprint. A file that fails goes to the quarantine folder."
        more="Turning it off only stops the check on new downloads: the AcoustID Scanner in Tools still scans when you run it yourself."
      >
        <Switch id="acoustid-enabled" {...enabled} />
      </Row>
      <DangerRow
        label="Clear quarantine"
        help="Deletes every file in the quarantine folder."
        action="Clear Quarantine"
        onAction={() => void clearQuarantine()}
      />
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
      test={() => testService(store, 'listenbrainz')}
    >
      <SheetField
        label="User token"
        htmlFor="listenbrainz-token"
        help={secretNote(token.value, 'From your ListenBrainz settings.')}
        more={
          <>
            Get it from{' '}
            <a href="https://listenbrainz.org/profile/" target="_blank" rel="noreferrer">
              ListenBrainz Settings
            </a>
            .
          </>
        }
      >
        <SecretField id="listenbrainz-token" placeholder="ListenBrainz User Token" {...token} />
      </SheetField>
      <SheetField
        label="Username (optional)"
        htmlFor="listenbrainz-username"
        help="Found from the token when left empty."
      >
        <TextField
          full
          id="listenbrainz-username"
          placeholder="Auto-detected from token if blank"
          value={user.value}
          onChange={(event) => user.onChange(event.target.value)}
        />
      </SheetField>
      <Row
        label="Scrobble plays to ListenBrainz"
        htmlFor="listenbrainz-scrobble-enabled"
        help={
          <span id="listenbrainz-recommended">
            Recommended for scrobbling: free and open, no subscription. Last.fm API keys need Pro
            from 1 December 2026.
          </span>
        }
      >
        <Switch id="listenbrainz-scrobble-enabled" {...scrobble} />
      </Row>
      <More label="More options" count={1} names="your own server or Maloja">
        <SheetField
          label="API base URL"
          htmlFor="listenbrainz-base-url"
          help="Self-hosted? Your server's address, for example http://localhost:8093. Leave it empty for the official one."
          more="Maloja: use http://your-server:42010/apis/listenbrainz and your Maloja API key. Start the history sync from Stats."
        >
          <TextField
            full
            mono
            id="listenbrainz-base-url"
            placeholder="Leave empty for official (api.listenbrainz.org)"
            value={base.value}
            onChange={(event) => base.onChange(event.target.value)}
          />
        </SheetField>
      </More>
    </ServiceCard>
  );
}

// ── the group ──

/** the hidden home of the music cards: a tile opens one in the service sheet */
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
      <ServiceHome.Provider value={home}>
        <Cards store={store} />
      </ServiceHome.Provider>
    </div>
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
