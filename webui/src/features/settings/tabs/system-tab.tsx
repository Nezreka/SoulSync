/**
 * System > General, API, Maintenance and Developer (settings-bic phase 6),
 * on the parts: each pill a hero sentence and its cards. Security is
 * system-security.tsx, Logs logs-tab.tsx, Server Activity
 * server-activity-settings.tsx; the page that puts them on pills is
 * system/system-page.tsx.
 *
 * Same ids, keys, endpoints and save model as the sections they replace:
 * the store for settings keys, the log level and the maintenance actions
 * through their own endpoints, at once. What changed: the folded sections
 * are cards, the browser's confirm before a compact is SoulSync's own dialog,
 * Clear quarantine moved here from the AcoustID sheet, and the actions that
 * lock the database or delete files sit in a danger zone.
 */

import { type ReactNode, useEffect, useState, useSyncExternalStore } from 'react';

import { wireSetupPreviewControls } from '@/features/onboarding/host';

import type { SettingsStore } from '../settings-store';

import { AUTOSAVE_DELAY_MS } from '../settings-store';
import {
  Btn,
  Card,
  CopyValue,
  FieldLine,
  FieldNote,
  Item,
  itemClass,
  Items,
  Note,
  NumberField,
  Pre,
  Row,
  Say,
  Segmented,
  Select,
  StatusWords,
  Switch,
  Tag,
  TextField,
  type Tone,
  Value,
} from '../ui';
import s from '../ui/settings.module.css';
import { ExperimentalCard } from './connections-metadata';
import { text } from './source-panels';
import { LOG_LEVEL_WORDS } from './system/words';

type Section_ = Record<string, unknown>;

type Shell = Window & {
  _settingsAutosaveOff?: boolean;
  showLoadingOverlay?: (message: string) => void;
  hideLoadingOverlay?: () => void;
};
const shell = () => window as Shell;
const toast = (message: string, type: string) => window.showToast?.(message, type);
const why = (error: unknown) => (error instanceof Error ? error.message : String(error));

export function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}
export function useLoads(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().loads);
}

type Said = { text: string; tone: Tone } | null;
/** what an action said, under it */
function SaidBox({ id, said }: { id?: string; said: Said }) {
  return (
    <div className={s.cardPad} hidden={!said}>
      <Say id={id} tone={said?.tone === 'busy' ? undefined : said?.tone}>
        {said?.text}
      </Say>
    </div>
  );
}

// ── General: saving, the database workers, logging ──

/** the level the server runs at: the saved one, then what the server says */
export function useLogLevel(store: SettingsStore) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const logging = (settings.logging || {}) as Section_;
  const [level, setLevel] = useState('INFO');
  useEffect(() => {
    if (!loads) return;
    setLevel(text(logging.level) || 'INFO');
    let live = true;
    void (async () => {
      try {
        const data = (await (await fetch('/api/settings/log-level')).json()) as {
          success?: boolean;
          level?: string;
        };
        if (live && data.success && data.level) setLevel(data.level);
      } catch (error) {
        console.error('Error loading log level:', error);
      }
    })();
    return () => {
      live = false;
    };
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  /** changeLogLevel: applies at once, not through the store */
  async function change(next: string) {
    setLevel(next);
    try {
      const response = await fetch('/api/settings/log-level', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ level: next }),
      });
      const data = (await response.json()) as { success?: boolean; error?: string };
      if (data.success) toast(`Log level changed to ${next}`, 'success');
      else toast(`Failed to change log level: ${data.error}`, 'error');
    } catch (error) {
      console.error('Error changing log level:', error);
      toast('Failed to change log level', 'error');
    }
  }
  return {
    level,
    path: (loads && text(logging.path)) || 'logs/app.log',
    change,
  };
}

export function GeneralView({
  store,
  log,
}: {
  store: SettingsStore;
  /** the level, shared with the Logs pill (useLogLevel) */
  log: ReturnType<typeof useLogLevel>;
}) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const autosave = loads ? (settings.ui_appearance || {}).settings_autosave !== false : true;
  const workers = (loads && text((settings.database || {}).max_workers)) || '5';
  const words = LOG_LEVEL_WORDS[log.level];
  return (
    <>
      <div className={s.cards}>
        <Card title="Saving">
          <Row
            label="Save settings as I change them"
            htmlFor="settings-autosave-enabled"
            help="Off: your changes wait for the save button at the top of the page, and leaving Settings drops them."
            more="Test and Connect buttons still save first, and video-only settings always save on change."
          >
            <Switch
              id="settings-autosave-enabled"
              checked={autosave}
              onChange={(event) => {
                const checked = event.target.checked;
                // the flag flips at once, so the very next change follows it
                shell()._settingsAutosaveOff = !checked;
                store.set('ui_appearance', 'settings_autosave', checked);
                // the switch itself always saves, or turning autosave off would
                // need a click on the save button to stick
                if (!checked) setTimeout(() => void store.flush(), AUTOSAVE_DELAY_MS);
              }}
            />
          </Row>
        </Card>

        <Card title="Database updates">
          <Row
            field
            label="Concurrent workers"
            htmlFor="max-workers"
            help="Parallel threads for database updates. More is faster and puts more load on the server."
          >
            <Select
              id="max-workers"
              value={workers}
              options={['3', '4', '5', '6', '7', '8', '9', '10'].map((n) => ({
                value: n,
                label: n,
              }))}
              onChange={(event) =>
                store.set('database', 'max_workers', parseInt(event.target.value, 10))
              }
            />
          </Row>
        </Card>

        <Card title="Logging">
          <Row
            field
            label="Log level"
            help={`Writes ${words?.shows ?? 'what it is set to'}. Applies at once, no restart.`}
          >
            <Segmented
              id="log-level-select"
              label="Log level"
              value={log.level}
              options={Object.entries(LOG_LEVEL_WORDS).map(([value, w]) => ({
                value,
                label: w.label,
              }))}
              onChange={(next) => void log.change(next)}
            />
          </Row>
          <Row label="Log file" help="Where the app log is written. The Logs pill shows it live.">
            <Value id="log-path-display">{log.path}</Value>
          </Row>
        </Card>
      </div>
    </>
  );
}

// ── API: keys, the request endpoint, MCP, Lidarr, Seerr ──

type ApiKey = {
  id: string;
  label?: string;
  scope?: string;
  key_prefix?: string;
  created_at?: string;
  last_used_at?: string;
};

/** loadApiKeys: null until loaded, 'none' when the list could not be read */
function useApiKeys(store: SettingsStore) {
  const loads = useLoads(store);
  const [keys, setKeys] = useState<ApiKey[] | 'none' | null>(null);
  async function load() {
    try {
      const response = await fetch('/api/v1/api-keys-internal');
      if (!response.ok) return setKeys('none');
      const data = (await response.json()) as { data?: { keys?: ApiKey[] } };
      setKeys(data.data?.keys || []);
    } catch {
      setKeys('none');
    }
  }
  // the page loaded the list once the settings were in (init.js)
  useEffect(() => {
    if (loads) void load();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return { keys, load };
}

const SCOPE_HELP: Record<string, string> = {
  read: 'Can fetch library and status data, for dashboards. It cannot change anything or read settings, keys or profiles.',
  admin: 'Can change settings and do everything else. The Lidarr and Seerr APIs need one.',
};

/** renderApiKeys, generateApiKey, copyApiKey, revokeApiKey */
function ApiKeysCards({ api }: { api: ReturnType<typeof useApiKeys> }) {
  const { keys, load } = api;
  const [label, setLabel] = useState('');
  const [scope, setScope] = useState('read');
  const [generated, setGenerated] = useState<string | null>(null);

  async function generate() {
    try {
      const response = await fetch('/api/v1/api-keys-internal/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          label: label.trim() || 'Default',
          scope: scope === 'admin' ? 'admin' : 'read',
        }),
      });
      const data = (await response.json()) as {
        success?: boolean;
        data?: { key?: string };
        error?: { message?: string };
      };
      if (data.success && data.data?.key) {
        setGenerated(data.data.key);
        setLabel('');
        toast('API key generated. Copy it now.', 'success');
        void load();
      } else {
        toast(data.error?.message || 'Failed to generate API key', 'error');
      }
    } catch (error) {
      console.error('Error generating API key:', error);
      toast('Failed to generate API key', 'error');
    }
  }

  async function revoke(key: ApiKey) {
    const name = key.label || 'this key';
    const ok = await window.showConfirmDialog?.({
      title: `Revoke ${name}?`,
      message: 'Any app using this key stops working at once. This cannot be undone.',
      confirmText: 'Revoke',
      cancelText: 'Keep it',
      destructive: true,
    });
    if (!ok) return;
    try {
      const response = await fetch(`/api/v1/api-keys-internal/revoke/${key.id}`, {
        method: 'DELETE',
      });
      const data = (await response.json()) as { success?: boolean; error?: { message?: string } };
      if (data.success) {
        toast('API key revoked', 'success');
        void load();
      } else {
        toast(data.error?.message || 'Failed to revoke key', 'error');
      }
    } catch (error) {
      console.error('Error revoking API key:', error);
      toast('Failed to revoke key', 'error');
    }
  }

  const day = (iso: string) => new Date(iso).toLocaleDateString();
  return (
    <>
      <Card
        title="API keys"
        hint={
          <>
            For the <code>/api/v1/</code> endpoints. A key is shown once, when it is made.
          </>
        }
      >
        <div className={s.cardPad} id="api-keys-list">
          {keys === null ? (
            <p className={s.listEmpty}>Loading…</p>
          ) : keys === 'none' ? (
            <p className={s.listEmpty}>No API keys configured.</p>
          ) : keys.length === 0 ? (
            <p className={s.listEmpty}>No API keys yet. Make one below.</p>
          ) : (
            <Items aria-label="API keys">
              {keys.map((k) => (
                <Item key={k.id} data-api-key={k.id}>
                  <span className={s.keyText}>
                    <span className={s.keyName}>
                      {k.label || 'Unnamed'}{' '}
                      <Tag accent={k.scope !== 'read'}>
                        {k.scope === 'read' ? 'Read-only' : 'Admin'}
                      </Tag>
                    </span>
                    <span className={itemClass.dim}>
                      <code>{k.key_prefix || 'sk_'}…</code> · made{' '}
                      {k.created_at ? day(k.created_at) : 'on an unknown date'}
                      {k.last_used_at ? ` · last used ${day(k.last_used_at)}` : ' · never used'}
                    </span>
                  </span>
                  <span className={itemClass.cells}>
                    <Btn
                      kind="danger"
                      small
                      className="revoke-api-key-btn"
                      data-key-id={k.id}
                      data-key-label={k.label || 'this key'}
                      aria-label={`Revoke ${k.label || 'Unnamed'}`}
                      onClick={(event) => {
                        event.preventDefault();
                        void revoke(k);
                      }}
                    >
                      Revoke
                    </Btn>
                  </span>
                </Item>
              ))}
            </Items>
          )}
        </div>
      </Card>

      <Card
        title="New API key"
        hint="Name it after what will use it, so you know which one to revoke."
        actions={
          <Btn small kind="primary" icon="plus" onClick={() => void generate()}>
            Generate key
          </Btn>
        }
      >
        <Row field label="Label" htmlFor="api-key-label">
          <TextField
            wide
            id="api-key-label"
            placeholder="Like Discord bot or Home Assistant"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </Row>
        <Row field label="Access" help={SCOPE_HELP[scope]}>
          <Segmented
            id="api-key-scope"
            label="Access"
            value={scope}
            options={[
              { value: 'read', label: 'Read-only' },
              { value: 'admin', label: 'Admin' },
            ]}
            onChange={setScope}
          />
        </Row>
        <div className={s.cardPad} id="api-key-generated" hidden={!generated}>
          <Note>
            <strong>Copy this key now.</strong> It is not shown again.
          </Note>
          <CopyValue id="api-key-value" value={generated ?? ''} />
        </div>
      </Card>
    </>
  );
}

type Profile = { id: number | string; name?: string; can_download?: boolean };

/** the profile list of a compat api (loadLidarrCompatProfiles): Lidarr's and Seerr's */
function CompatProfile({
  store,
  section,
  id,
}: {
  store: SettingsStore;
  section: string;
  id: string;
}) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const saved = text((settings[section] || {}).profile_id ?? 1) || '1';
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  useEffect(() => {
    if (!loads) return;
    void (async () => {
      try {
        const data = (await (await fetch('/api/profiles')).json()) as { profiles?: Profile[] };
        if (data.profiles?.length) setProfiles(data.profiles);
      } catch (error) {
        console.debug(section + ' profile list failed', error);
      }
    })();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  // a saved profile that is gone shows the first one, as the old list did
  const value =
    profiles && !profiles.some((p) => String(p.id) === String(parseInt(saved, 10) || 1))
      ? '1'
      : String(parseInt(saved, 10) || 1);
  return (
    <Select
      id={id}
      value={profiles ? value : '1'}
      onChange={(event) => store.set(section, 'profile_id', parseInt(event.target.value, 10) || 1)}
    >
      {profiles ? (
        profiles.map((p) => (
          <option key={String(p.id)} value={String(p.id)}>
            {(p.name || 'Profile ' + p.id) + (p.can_download === false ? ' (needs approval)' : '')}
          </option>
        ))
      ) : (
        <option value="1">Loading…</option>
      )}
    </Select>
  );
}

const CURL = `curl -X POST http://<host>:8008/api/v1/request \\
  -H "Authorization: Bearer <your-api-key>" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "Artist - Track Name"}'`;

export function ApiView({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const api = useApiKeys(store);
  const enabled = (section: string) => (loads ? (settings[section] || {}).enabled === true : false);
  // an install from before #2166 has no key and keeps today's fallback profile
  const refuseUnknown = Boolean(loads) && (settings['seerr_compat'] || {}).refuse_unknown === true;
  const toggle = (section: string) => ({
    checked: enabled(section),
    onChange: (event: { target: { checked: boolean } }) =>
      store.set(section, 'enabled', event.target.checked),
  });

  return (
    <>
      <div className={s.cards}>
        <ApiKeysCards api={api} />

        <Card
          title="Request endpoint"
          hint="Start a music download from another tool (a Discord bot, Home Assistant, curl) with any key above."
        >
          <div className={s.cardPad}>
            <Pre>{CURL}</Pre>
            <FieldNote>
              It returns a <code>request_id</code> to poll. Add <code>notify_url</code> to get the
              result posted to a webhook when it is done. It also starts any{' '}
              <strong>Webhook Received</strong> automations.
            </FieldNote>
          </div>
        </Card>

        <Card title="MCP for AI clients">
          <Row
            label="Answer MCP requests at /mcp"
            htmlFor="mcp-enabled"
            help="Lets Claude and other AI apps search, check your library and download through SoulSync."
            more={
              <>
                Add <code>http://&lt;host&gt;:8008/mcp</code> as an MCP server and send an API key
                from above as a Bearer token. A read-only key can look but not change anything.
                Settings, keys and profiles are never reachable.
              </>
            }
          >
            <Switch id="mcp-enabled" {...toggle('mcp')} />
          </Row>
        </Card>

        <Card title="Lidarr compatible API">
          <Row
            label="Answer as Lidarr at /lidarr"
            htmlFor="lidarr-compat-enabled"
            help="Lets request apps that only talk to Lidarr (Singarr, MusicSeerr 1.x) use SoulSync instead."
            more={
              <>
                In the app, set the Lidarr URL to <code>http://&lt;host&gt;:8008/lidarr</code> and
                paste an <strong>Admin</strong> API key from above. Albums it asks for go on the
                wishlist of the profile below.
              </>
            }
          >
            <Switch id="lidarr-compat-enabled" {...toggle('lidarr_compat')} />
          </Row>
          <Row
            sub
            field
            off={!enabled('lidarr_compat')}
            label="Requests go to profile"
            htmlFor="lidarr-compat-profile"
            help="Pick a profile without download rights and every request waits for approval on the Requests page first."
          >
            <CompatProfile store={store} section="lidarr_compat" id="lidarr-compat-profile" />
          </Row>
        </Card>

        <Card title="Seerr compatible API">
          <Row
            label="Answer as Seerr at /seerr"
            htmlFor="seerr-compat-enabled"
            help="Lets apps built for Seerr (Jellyseerr, Overseerr), like Litefin's Jellyfin plugin, find and request movies and shows through SoulSync's video side."
            more={
              <>
                In the app, set the Seerr URL to <code>http://&lt;host&gt;:8008/seerr</code> and
                paste an <strong>Admin</strong> API key from above. Each Jellyfin user requests as
                the profile linked to their Jellyfin account, or the profile with the same name, and
                gets what that profile allows: movies and shows separately, Download right away or
                Ask first, its rating limit, request limit and quality.
              </>
            }
          >
            <Switch id="seerr-compat-enabled" {...toggle('seerr_compat')} />
          </Row>
          <Row
            sub
            off={!enabled('seerr_compat')}
            label="Refuse Jellyfin users with no profile"
            htmlFor="seerr-compat-refuse-unknown"
            help={
              refuseUnknown
                ? "A Jellyfin user that isn't linked to a profile can't browse or request until you link their account or give a profile the same name."
                : 'Off: anyone without a profile browses and requests as the profile below, with its permissions.'
            }
          >
            <Switch
              id="seerr-compat-refuse-unknown"
              checked={refuseUnknown}
              onChange={(event) =>
                store.set('seerr_compat', 'refuse_unknown', event.target.checked)
              }
            />
          </Row>
          {refuseUnknown ? null : (
            <Row
              sub
              field
              off={!enabled('seerr_compat')}
              label="Jellyfin users with no profile act as"
              htmlFor="seerr-compat-profile"
            >
              <CompatProfile store={store} section="seerr_compat" id="seerr-compat-profile" />
            </Row>
          )}
        </Card>
      </div>
    </>
  );
}

// ── Maintenance: the database, the artwork cache, quarantine ──

type DbInfo = {
  success?: boolean;
  total_size_display?: string;
  free_pages?: number;
  free_size_display?: string;
  auto_vacuum_label?: string;
  auto_vacuum?: number;
  wal_size_display?: string;
  vacuum_has_room?: boolean;
};
type TableRow = { name: string; bytes?: number; rows?: number };
type TablesInfo = {
  success?: boolean;
  estimates?: TableRow[];
  measure?: {
    state?: 'idle' | 'running' | 'done' | 'error';
    error?: string | null;
    result?: { method?: string; tables?: TableRow[] } | null;
  };
};
const mb = (bytes: number) =>
  bytes >= 1024 ** 3
    ? `${(bytes / 1024 ** 3).toFixed(1)} GB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

/** loadDbMaintenanceInfo */
function useDbInfo(shown: boolean, opened: number) {
  const [info, setInfo] = useState<DbInfo | null>(null);
  async function load() {
    try {
      const data = (await (await fetch('/api/database/maintenance/info')).json()) as DbInfo;
      if (data.success) setInfo(data);
    } catch (error) {
      console.error('Error loading DB maintenance info:', error);
    }
  }
  // each arrival on the pill, as each arrival on the tab did
  useEffect(() => {
    if (shown) void load();
  }, [shown, opened]); // eslint-disable-line react-hooks/exhaustive-deps
  return { info, load };
}

type CacheStatus = {
  success?: boolean;
  entries?: number;
  pending?: number;
  bytes?: number;
  max_bytes?: number;
  thumbnails?: boolean;
};

/** _imgCacheBytes */
export function cacheBytes(n: number | undefined) {
  if (!n) return '0 MB';
  const megs = n / (1024 * 1024);
  return megs >= 1024 ? (megs / 1024).toFixed(2) + ' GB' : megs.toFixed(1) + ' MB';
}

/** loadImageCacheStatus */
function useCache(shown: boolean, opened: number) {
  const [cache, setCache] = useState<CacheStatus | null>(null);
  const [maxMb, setMaxMb] = useState('');
  const [thumbs, setThumbs] = useState(false);
  async function load() {
    try {
      const data = (await (await fetch('/api/image-cache/status')).json()) as CacheStatus;
      if (!data.success) return;
      setCache(data);
      // the saved values, without overwriting a number being typed
      if (document.activeElement?.id !== 'imgcache-max-mb')
        setMaxMb(String(Math.round((data.max_bytes || 0) / (1024 * 1024))));
      setThumbs(data.thumbnails === true);
    } catch (error) {
      console.error('image cache status failed', error);
    }
  }
  useEffect(() => {
    if (shown) void load();
  }, [shown, opened]); // eslint-disable-line react-hooks/exhaustive-deps
  return { cache, load, maxMb, setMaxMb, thumbs, setThumbs };
}

/** settings.js clearQuarantine (in the AcoustID sheet until phase 6) */
export async function clearQuarantine() {
  const w = shell();
  if (
    !(await window.showConfirmDialog?.({
      title: 'Clear quarantine?',
      message:
        'Every file in the quarantine folder is deleted: the downloads that failed their checks. This cannot be undone.',
      confirmText: 'Delete them',
      cancelText: 'Keep them',
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

function DatabaseCard({ db }: { db: ReturnType<typeof useDbInfo> }) {
  const { info, load } = db;
  const [busy, setBusy] = useState(false);
  const [tables, setTables] = useState<TablesInfo | null>(null);
  const [said, setSaid] = useState<Said>(null);

  async function loadTables(measure: boolean) {
    try {
      const data = (await (
        await fetch(`/api/database/maintenance/tables${measure ? '?measure=1' : ''}`)
      ).json()) as TablesInfo;
      if (data.success) setTables(data);
    } catch (error) {
      console.error('Error loading table sizes:', error);
    }
  }
  // a measure runs on the server's own thread; ask again until it lands
  const measuring = tables?.measure?.state === 'running';
  useEffect(() => {
    if (!measuring) return;
    const timer = window.setTimeout(() => void loadTables(false), 3000);
    return () => window.clearTimeout(timer);
  }, [measuring, tables]);

  async function optimize() {
    setBusy(true);
    setSaid({ tone: 'busy', text: 'Tidying up the database…' });
    try {
      const data = (await (
        await fetch('/api/database/maintenance/optimize', { method: 'POST' })
      ).json()) as {
        success?: boolean;
        error?: string;
        elapsed_seconds?: number;
        pruned?: Record<string, number>;
        wal_after?: number;
      };
      if (data.success) {
        const rows = Object.values(data.pruned || {}).reduce((a, b) => a + b, 0);
        toast('Database tidied up', 'success');
        setSaid({
          tone: 'ok',
          text: `Done in ${data.elapsed_seconds}s. ${rows.toLocaleString()} old cache and log rows removed, search indexes merged, write-ahead log now ${mb(data.wal_after || 0)}.`,
        });
        void load();
      } else {
        const error = data.error || 'Unknown error';
        toast('Tidy up failed: ' + error, 'error');
        setSaid({ tone: 'bad', text: 'Failed: ' + error });
      }
    } catch (error) {
      toast('Tidy up failed: ' + why(error), 'error');
      setSaid({ tone: 'bad', text: 'Failed: ' + why(error) });
    } finally {
      setBusy(false);
    }
  }

  const shownTables = tables
    ? (tables.measure?.result?.tables?.length
        ? tables.measure.result.tables
        : (tables.estimates ?? [])
      ).slice(0, 15)
    : [];
  const measured = !!tables?.measure?.result?.tables?.length;
  const reclaim = (info?.free_pages || 0) > 0;

  return (
    <Card title="Database" hint="SoulSync's own file: your library, history and settings.">
      <div id="db-maintenance-info">
        <Row label="Size">
          <Value id="db-size-display">{info ? info.total_size_display : 'Loading…'}</Value>
        </Row>
        <Row label="Free space inside it" help="What a compact would give back to the disk.">
          <Value id="db-freepages-display">
            {!info
              ? 'Loading…'
              : reclaim
                ? `${info.free_size_display} (${(info.free_pages || 0).toLocaleString()} pages)`
                : 'None, fully compacted'}
          </Value>
        </Row>
        <Row
          label="Write-ahead log"
          help="Recent writes waiting to be folded into the file. Tidy up folds them in."
        >
          <Value id="db-wal-display">
            {info ? (info.wal_size_display ?? 'Unknown') : 'Loading…'}
          </Value>
        </Row>
        <Row label="Auto-vacuum">
          <Value id="db-autovacuum-display">{info ? info.auto_vacuum_label : 'Loading…'}</Value>
        </Row>
      </div>
      <Row
        label="Tidy up"
        help="Safe any time: drops old rows from caches and logs that keep their own retention (never listening history or library data), merges the search indexes and folds the write-ahead log back in."
        more="It also runs on its own with Cache Maintenance."
      >
        <Btn id="db-optimize-btn" icon="refresh" busy={busy} onClick={() => void optimize()}>
          {busy ? 'Tidying up…' : 'Tidy up'}
        </Btn>
      </Row>
      <Row
        label="Biggest tables"
        help="Estimated from row counts at once, then measured on the server, which reads the whole file."
      >
        <Btn id="db-measure-btn" busy={measuring} onClick={() => void loadTables(true)}>
          {measuring ? 'Measuring…' : 'Show biggest tables'}
        </Btn>
      </Row>
      {tables ? (
        <div className={s.cardPad} id="db-tables">
          {tables.measure?.state === 'error' ? (
            <Say tone="bad">Measuring failed: {tables.measure.error}</Say>
          ) : null}
          <Items aria-label="Biggest tables">
            {shownTables.map((t) => (
              <Item key={t.name}>
                <span className={`${itemClass.main} ${itemClass.mono}`}>{t.name}</span>
                <span className={itemClass.dim}>
                  {t.bytes != null ? mb(t.bytes) : `about ${(t.rows || 0).toLocaleString()} rows`}
                </span>
              </Item>
            ))}
          </Items>
          <FieldNote>
            {measuring
              ? 'Measuring every table, this reads the whole file…'
              : measured
                ? 'Measured sizes, biggest first.'
                : 'Estimates from row counts, biggest first.'}
          </FieldNote>
        </div>
      ) : null}
      <SaidBox id="db-optimize-status" said={said} />
    </Card>
  );
}

function ArtworkCacheCard({
  store,
  cache: c,
}: {
  store: SettingsStore;
  cache: ReturnType<typeof useCache>;
}) {
  const { cache, load } = c;
  const [busy, setBusy] = useState<'prune' | 'clear' | null>(null);
  const [said, setSaid] = useState<Said>(null);

  async function act(kind: 'prune' | 'clear') {
    if (kind === 'clear') {
      const ok = await window.showConfirmDialog?.({
        title: 'Clear the artwork cache?',
        message:
          'Every cached cover and poster is removed. Nothing is lost for good: images download again as pages need them, so the only cost is a slower first load.',
        confirmText: 'Clear cache',
        cancelText: 'Cancel',
      });
      if (!ok) return;
    }
    setBusy(kind);
    try {
      const response = await fetch(`/api/image-cache/${kind}`, { method: 'POST' });
      const data = (await response.json()) as {
        success?: boolean;
        error?: string;
        expired?: number;
        evicted?: number;
        removed?: number;
      };
      if (data.success) {
        if (kind === 'prune') {
          const freed = (data.expired || 0) + (data.evicted || 0);
          setSaid({
            tone: 'ok',
            text: freed
              ? `Removed ${freed} cached image${freed === 1 ? '' : 's'}.`
              : 'Nothing to reclaim: the cache is already within its limits.',
          });
        } else {
          setSaid({
            tone: 'ok',
            text: `Cleared ${data.removed || 0} cached image${data.removed === 1 ? '' : 's'}.`,
          });
        }
        void load();
      } else {
        setSaid({
          tone: 'bad',
          text:
            data.error ||
            (kind === 'prune' ? 'Could not reclaim space.' : 'Could not clear the cache.'),
        });
      }
    } catch (error) {
      setSaid({
        tone: 'bad',
        text:
          (kind === 'prune' ? 'Could not reclaim space: ' : 'Could not clear the cache: ') +
          why(error),
      });
    } finally {
      setBusy(null);
    }
  }

  const pending = cache?.pending || 0;
  const entries = !cache
    ? 'Loading…'
    : pending
      ? `${(cache.entries || 0).toLocaleString()} (${pending.toLocaleString()} waiting)`
      : (cache.entries || 0).toLocaleString();
  const size = !cache
    ? 'Loading…'
    : cache.max_bytes
      ? `${cacheBytes(cache.bytes)} of ${cacheBytes(cache.max_bytes)}`
      : `${cacheBytes(cache.bytes)} (no limit)`;

  return (
    <Card
      title="Artwork cache"
      hint="Local copies of covers and posters. Nothing in it is irreplaceable: an image downloads again when a page needs it."
    >
      <Row label="Cached images">
        <Value id="imgcache-entries">{entries}</Value>
      </Row>
      <Row label="Disk used">
        <Value id="imgcache-size">{size}</Value>
      </Row>
      <Row
        field
        label="Size limit"
        htmlFor="imgcache-max-mb"
        help="The art used longest ago goes once the cache passes this size. 0 means no limit."
      >
        <NumberField
          id="imgcache-max-mb"
          min="0"
          step="128"
          unit="MB"
          value={c.maxMb}
          onChange={(event) => {
            c.setMaxMb(event.target.value);
            const n = parseInt(event.target.value, 10);
            store.set('image_cache', 'max_cache_mb', Number.isFinite(n) ? n : 0);
          }}
        />
      </Row>
      <Row
        label="Serve resized thumbnails"
        htmlFor="imgcache-thumbnails"
        help="Resizes art on the server so a grid of tiles doesn't pull full-size originals."
        more="Off by default. The big win on large libraries, at the cost of some CPU the first time each image is seen. Everything else about the cache is unchanged."
      >
        <Switch
          id="imgcache-thumbnails"
          checked={c.thumbs}
          onChange={(event) => {
            c.setThumbs(event.target.checked);
            store.set('image_cache', 'thumbnails', event.target.checked);
          }}
        />
      </Row>
      <Row
        label="Reclaim space now"
        help="Removes expired art and anything over the size limit, as the regular cleanup does."
      >
        <Btn id="imgcache-prune-btn" busy={busy === 'prune'} onClick={() => void act('prune')}>
          {busy === 'prune' ? 'Reclaiming…' : 'Reclaim space'}
        </Btn>
      </Row>
      <Row
        label="Clear the cache"
        help="Every cached image goes. Pages are slower the first time they open again."
      >
        <Btn id="imgcache-clear-btn" busy={busy === 'clear'} onClick={() => void act('clear')}>
          {busy === 'clear' ? 'Clearing…' : 'Clear cache'}
        </Btn>
      </Row>
      <SaidBox id="imgcache-status" said={said} />
    </Card>
  );
}

/** the actions that lock the database or delete files: runDatabaseVacuum,
 *  enableIncrementalVacuum, clearQuarantine. Each asks first */
function DangerZone({ db }: { db: ReturnType<typeof useDbInfo> }) {
  const { info, load } = db;
  const [busy, setBusy] = useState<'vacuum' | 'incremental' | null>(null);
  const [said, setSaid] = useState<Said>(null);
  const noRoom = info?.vacuum_has_room === false;

  async function run(kind: 'vacuum' | 'incremental') {
    const vacuum = kind === 'vacuum';
    const ok = await window.showConfirmDialog?.(
      vacuum
        ? {
            title: 'Compact the database?',
            message:
              'It rewrites the whole file. Back up first. It needs free disk space about the size of the database, and the database is locked while it runs, which on a very large library can be a long time.' +
              (noRoom
                ? ' There is not enough free disk space next to the database right now, so it will likely fail.'
                : ''),
            confirmText: 'Compact',
            cancelText: 'Not now',
            destructive: true,
          }
        : {
            title: 'Turn on incremental vacuum?',
            message:
              'It needs one full compact to switch on, which locks the database and can take over a minute on a large one. After that, freed space goes back to the disk in small batches on its own.',
            confirmText: 'Turn it on',
            cancelText: 'Not now',
            destructive: true,
          },
    );
    if (!ok) return;
    setBusy(kind);
    setSaid({
      tone: 'busy',
      text: vacuum
        ? 'Compacting the database: this may take a while…'
        : 'Turning on incremental vacuum: this may take a while…',
    });
    try {
      const response = await fetch(
        vacuum
          ? '/api/database/maintenance/vacuum'
          : '/api/database/maintenance/enable-incremental-vacuum',
        { method: 'POST' },
      );
      const data = (await response.json()) as {
        success?: boolean;
        error?: string;
        elapsed_seconds?: number;
        saved_display?: string;
        already_enabled?: boolean;
      };
      if (data.success) {
        const done = vacuum
          ? `Database compacted in ${data.elapsed_seconds}s, saved ${data.saved_display}`
          : data.already_enabled
            ? 'Already on'
            : `Turned on in ${data.elapsed_seconds}s, saved ${data.saved_display}`;
        toast(done, 'success');
        setSaid({
          tone: 'ok',
          text: vacuum ? `Done in ${data.elapsed_seconds}s. Saved ${data.saved_display}.` : done,
        });
        void load();
      } else {
        const error = data.error || 'Unknown error';
        toast((vacuum ? 'Vacuum failed: ' : 'Failed: ') + error, 'error');
        setSaid({ tone: 'bad', text: 'Failed: ' + error });
      }
    } catch (error) {
      toast((vacuum ? 'Vacuum failed: ' : 'Failed: ') + why(error), 'error');
      setSaid({ tone: 'bad', text: 'Failed: ' + why(error) });
    } finally {
      setBusy(null);
    }
  }

  const incremental = info?.auto_vacuum === 2 && busy !== 'incremental';
  return (
    <Card
      danger
      title="Danger zone"
      hint="These lock the database while they run, or delete files. Each asks first."
    >
      <Row
        label="Compact the database"
        help="Rewrites the whole file to give its free space back to the disk. Back up first: it needs free disk space about the size of the database and locks it while it runs."
      >
        <Btn
          kind="danger"
          id="db-vacuum-btn"
          busy={busy === 'vacuum'}
          onClick={() => void run('vacuum')}
        >
          {busy === 'vacuum' ? 'Compacting…' : 'Compact database'}
        </Btn>
      </Row>
      {noRoom ? (
        <div className={s.cardPad} id="db-vacuum-room">
          <Note>
            There is not enough free disk space next to the database to compact it right now.
          </Note>
        </div>
      ) : null}
      <Row
        label="Incremental vacuum"
        help={
          incremental
            ? 'On: freed space goes back to the disk in small batches on its own.'
            : 'From then on, freed space goes back to the disk in small batches.'
        }
        more="Switching it on needs one full compact, which locks the database while it runs."
      >
        {incremental ? <StatusWords tone="ok">On</StatusWords> : null}
        <Btn
          kind="danger"
          id="db-incvacuum-btn"
          hidden={incremental}
          busy={busy === 'incremental'}
          onClick={() => void run('incremental')}
        >
          {busy === 'incremental' ? 'Turning on…' : 'Turn on'}
        </Btn>
      </Row>
      <Row
        label="Clear quarantine"
        help="Deletes every file in the quarantine folder: the downloads that failed AcoustID or another check. This cannot be undone."
      >
        <Btn kind="danger" id="quarantine-clear-btn" onClick={() => void clearQuarantine()}>
          Clear quarantine
        </Btn>
      </Row>
      <SaidBox id="db-vacuum-status" said={said} />
    </Card>
  );
}

export function MaintenanceView({
  store,
  shown,
  opened,
}: {
  store: SettingsStore;
  /** the pill is on screen: what it shows reloads on each arrival */
  shown: boolean;
  opened: number;
}) {
  const db = useDbInfo(shown, opened);
  const cache = useCache(shown, opened);
  return (
    <>
      <div className={s.cards}>
        <DatabaseCard db={db} />
        <ArtworkCacheCard store={store} cache={cache} />
        <DangerZone db={db} />
      </div>
    </>
  );
}

// ── Developer: dev mode, the preview tools, Experimental ──

/** dev mode follows ss:dev-mode, which every switch of it sends (the load
 *  check, Activate, Hydrabase's disconnect) */
function useDevMode() {
  const [active, setActive] = useState(false);
  useEffect(() => {
    const follow = (event: Event) =>
      setActive((event as CustomEvent<{ enabled?: boolean }>).detail?.enabled === true);
    window.addEventListener('ss:dev-mode', follow);
    return () => window.removeEventListener('ss:dev-mode', follow);
  }, []);
  return active;
}

/** activateDevMode */
function DevModeCard({ active }: { active: boolean }) {
  const [password, setPassword] = useState('');
  async function activate() {
    try {
      const response = await fetch('/api/dev-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const data = (await response.json()) as { success?: boolean };
      if (data.success) {
        // the dashboard's Hydrabase orb, and this status, follow the event
        window.dispatchEvent(new CustomEvent('ss:dev-mode', { detail: { enabled: true } }));
        const nav = document.getElementById('hydrabase-nav');
        if (nav) nav.style.display = '';
        setPassword('');
        toast('Dev mode activated', 'success');
      } else {
        toast('Invalid password', 'error');
      }
    } catch {
      toast('Failed to activate dev mode', 'error');
    }
  }
  return (
    <Card
      title="Developer mode"
      hint="For SoulSync's maintainers: the advanced log viewer and the Hydrabase tools."
      actions={
        <StatusWords id="dev-mode-status" tone={active ? 'ok' : 'idle'}>
          {active ? 'Active' : 'Inactive'}
        </StatusWords>
      }
    >
      <Row field label="Password" htmlFor="dev-mode-password">
        <FieldLine wrap>
          <TextField
            type="password"
            id="dev-mode-password"
            placeholder="Developer password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <Btn small kind="primary" onClick={() => void activate()}>
            Activate
          </Btn>
        </FieldLine>
      </Row>
    </Card>
  );
}

/** preview tools, nothing saved. The weather preview and the setup preview
 *  are wired by their own modules, by id; they run once the controls are on
 *  the page (both are safe to run again) */
function PreviewToolsCard() {
  useEffect(() => {
    // the shell's own copy: it is a separate bundle, and only its weather
    // state knows the sidebar weather is on
    window.initWeatherPreviewSettings?.();
    wireSetupPreviewControls();
  }, []);
  return (
    <Card
      title="Preview tools"
      hint="See a screen as it would look another day, or on first run. Nothing here is saved."
    >
      <Row
        stack
        label="Weather preview"
        htmlFor="sw-preview-preset"
        help="Shows any sky or holiday in the sidebar so you can check how it looks. Only this browser tab sees it."
        more="The weather line reads Preview while it's on. The date pretends it's another day, which sets the season and the holiday."
      >
        <FieldLine wrap>
          <Select id="sw-preview-preset" defaultValue="">
            <option value="">Live weather</option>
          </Select>
          <Select id="sw-preview-holiday" aria-label="Holiday" defaultValue="">
            <option value="">Holiday by date</option>
            <option value="none">No holiday</option>
          </Select>
          <TextField type="date" id="sw-preview-date" aria-label="Pretend date" />
          <Btn small id="sw-preview-reset">
            Back to live
          </Btn>
        </FieldLine>
        <FieldNote>
          <span id="sw-preview-status">Live weather</span>
        </FieldNote>
      </Row>
      <Row
        stack
        label="Setup preview"
        htmlFor="ob-preview-step"
        help="Runs the real first-run setup over the app, and throws everything away when you close it."
        more="Settings, the profile, follows and sign-ins stay in the preview. Connection checks still talk to the real services, read only. A Preview tag stays in the corner the whole time."
      >
        <FieldLine wrap>
          <Select id="ob-preview-step" aria-label="Start at" defaultValue="welcome">
            <option value="welcome">Start at Welcome</option>
            <option value="account">Your account</option>
            <option value="purpose">What it's for</option>
            <option value="server">Media server</option>
            <option value="folders">Folders</option>
            <option value="sources">Download sources</option>
            <option value="metadata">Metadata and accounts</option>
            <option value="video">Movies and TV</option>
            <option value="library">Make it yours</option>
            <option value="ready">Ready</option>
          </Select>
          <Select id="ob-preview-env" aria-label="Pretend environment" defaultValue="real">
            <option value="real">Detect for real</option>
            <option value="docker">Pretend Docker</option>
            <option value="bare">Pretend bare metal</option>
            <option value="empty">Pretend nothing found</option>
          </Select>
          <Btn small id="ob-preview-open">
            Open preview
          </Btn>
        </FieldLine>
      </Row>
    </Card>
  );
}

export function DeveloperView({ store }: { store: SettingsStore }): ReactNode {
  const active = useDevMode();
  return (
    <>
      <div className={s.cards}>
        <DevModeCard active={active} />
        <PreviewToolsCard />
        <ExperimentalCard store={store} />
      </div>
    </>
  );
}
