/**
 * The System tab (SETTINGS_PORT.md, step 9), part by part. Same markup,
 * classes and ids. Security & Access and the sections after Logging are
 * still vanilla until their own steps (9b, 9c).
 *
 * 9a: Saving (the autosave switch), Database Settings, Database Maintenance
 * and Logging.
 */

import { type ReactNode, useEffect, useState, useSyncExternalStore } from 'react';

import { wireSetupPreviewControls } from '@/features/onboarding/host';

import type { SettingsTabProps } from '../host';

import { AUTOSAVE_DELAY_MS, type SettingsStore } from '../settings-store';
import { ExperimentalSection } from './connections-metadata';
import { helpBits, Section, useHelp } from './parts';
import { text } from './source-panels';
import { SecuritySection } from './system-security';

const TAB = 'advanced';
type Section_ = Record<string, unknown>;

type Shell = Window & {
  _settingsAutosaveOff?: boolean;
  showToast?: (message: string, type?: string) => void;
};
const shell = () => window as Shell;
const toast = (message: string, type: string) => shell().showToast?.(message, type);

function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}
function useLoads(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().loads);
}

// ── Saving ──

function SavingSection({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const on = loads ? (settings.ui_appearance || {}).settings_autosave !== false : true;
  return (
    <Section tab={TAB} title="Saving" hint="Autosave">
      <div className="settings-group" data-stg={TAB}>
        <div className="form-group">
          <label className="checkbox-label">
            <input
              type="checkbox"
              id="settings-autosave-enabled"
              checked={on}
              onChange={(event) => {
                const checked = event.target.checked;
                // the flag flips at once, so the very next change follows it
                shell()._settingsAutosaveOff = !checked;
                store.set('ui_appearance', 'settings_autosave', checked);
                // the switch itself always saves, or turning autosave off would
                // need a click on the save button to stick
                if (!checked) setTimeout(() => void store.flush(), AUTOSAVE_DELAY_MS);
              }}
            />{' '}
            Save settings as I change them
          </label>
          <small className="settings-hint">
            On by default. Turn it off and your changes wait until you click the save button at the
            top of the page. Leaving Settings without saving drops them. Test and Connect buttons
            still save first, and video-only settings always save on change.
          </small>
        </div>
      </div>
    </Section>
  );
}

// ── Database Settings, Database Maintenance, Logging ──

/** a field whose help body sits after the form-group (Database Settings) */
function HelpAfter({ children, help }: { children: ReactNode; help: string }) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <>
      <div className="form-group">
        {children}
        {bits.icon}
        {bits.line}
      </div>
      <div className="help-text setting-help-body" hidden={!state.open}>
        {help}
      </div>
    </>
  );
}

/** a field with a help-text div inside the form-group (Logging) */
function HelpInside({ children, help }: { children: ReactNode; help: string }) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <div className="form-group">
      {children}
      {bits.icon}
      <div className="setting-help-text setting-help-body" hidden={!state.open}>
        {help}
      </div>
      {bits.line}
    </div>
  );
}

type DbInfo = {
  success?: boolean;
  total_size_display?: string;
  free_pages?: number;
  free_size_display?: string;
  auto_vacuum_label?: string;
  auto_vacuum?: number;
};
type Status = { text: string; color: string } | null;

const STATUS_STYLE = {
  marginTop: '8px',
  padding: '8px 12px',
  borderRadius: '8px',
  fontSize: '0.85em',
};

/** loadDbMaintenanceInfo, runDatabaseVacuum, enableIncrementalVacuum */
function DatabaseMaintenance({ opened, active }: { opened: number; active: boolean }) {
  const [info, setInfo] = useState<DbInfo | null>(null);
  const [busy, setBusy] = useState<'vacuum' | 'incremental' | null>(null);
  const [status, setStatus] = useState<Status>(null);

  async function load() {
    try {
      const data = (await (await fetch('/api/database/maintenance/info')).json()) as DbInfo;
      if (data.success) setInfo(data);
    } catch (error) {
      console.error('Error loading DB maintenance info:', error);
    }
  }
  // each arrival on the tab, as switchSettingsTab did
  useEffect(() => {
    if (active) void load();
  }, [opened, active]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(kind: 'vacuum' | 'incremental') {
    const vacuum = kind === 'vacuum';
    const ask = vacuum
      ? 'This will compact the database by rewriting it. The database will be locked during this operation. For large databases this may take over a minute. Continue?'
      : 'This will enable incremental vacuum mode. It requires a one-time full VACUUM to activate, which locks the database and may take over a minute on large databases. Continue?';
    if (!window.confirm(ask)) return;
    setBusy(kind);
    setStatus({
      color: 'rgba(255,255,255,0.6)',
      text: vacuum
        ? 'Running VACUUM — this may take a while...'
        : 'Enabling incremental vacuum — this may take a while...',
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
          ? `Database compacted in ${data.elapsed_seconds}s — saved ${data.saved_display}`
          : data.already_enabled
            ? 'Already enabled'
            : `Enabled in ${data.elapsed_seconds}s — saved ${data.saved_display}`;
        toast(done, 'success');
        setStatus({
          color: '#4caf50',
          text: vacuum ? `Done in ${data.elapsed_seconds}s. Saved ${data.saved_display}.` : done,
        });
        void load();
      } else {
        const why = data.error || 'Unknown error';
        toast((vacuum ? 'Vacuum failed: ' : 'Failed: ') + why, 'error');
        setStatus({ color: '#ef5350', text: 'Failed: ' + why });
      }
    } catch (error) {
      const why = error instanceof Error ? error.message : String(error);
      toast((vacuum ? 'Vacuum failed: ' : 'Failed: ') + why, 'error');
      setStatus({ color: '#ef5350', text: 'Failed: ' + why });
    } finally {
      setBusy(null);
    }
  }

  const incremental = info?.auto_vacuum === 2 && busy !== 'incremental';
  return (
    <div className="settings-group" data-stg={TAB}>
      <div className="form-group" id="db-maintenance-info">
        <label>Database Size:</label>
        <span id="db-size-display" className="readonly-field">
          {info ? info.total_size_display : 'Loading...'}
        </span>
      </div>
      <div className="form-group">
        <label>Free Pages:</label>
        <span id="db-freepages-display" className="readonly-field">
          {!info
            ? 'Loading...'
            : (info.free_pages || 0) > 0
              ? `${(info.free_pages || 0).toLocaleString()} (${info.free_size_display} reclaimable)`
              : 'None — database is fully compacted'}
        </span>
      </div>
      <div className="form-group">
        <label>Auto-Vacuum Mode:</label>
        <span id="db-autovacuum-display" className="readonly-field">
          {info ? info.auto_vacuum_label : 'Loading...'}
        </span>
      </div>

      <div className="form-actions" style={{ flexWrap: 'wrap' }}>
        <button
          className="test-button"
          id="db-vacuum-btn"
          disabled={busy === 'vacuum'}
          onClick={() => void run('vacuum')}
        >
          {busy === 'vacuum' ? 'Compacting...' : 'Compact Database (VACUUM)'}
        </button>
        <button
          className="test-button"
          id="db-incvacuum-btn"
          disabled={busy === 'incremental' || incremental}
          style={incremental ? { opacity: '0.5' } : undefined}
          onClick={() => void run('incremental')}
        >
          {busy === 'incremental'
            ? 'Enabling...'
            : incremental
              ? 'Incremental Vacuum Enabled'
              : 'Enable Incremental Vacuum'}
        </button>
      </div>
      <div className="help-text">
        <strong>Compact Database</strong> rewrites the entire DB to reclaim unused space. Locks the
        database briefly — may take a minute on large databases.
        <br />
        <strong>Incremental Vacuum</strong> enables automatic page reclamation. Requires a one-time
        full compact to activate. After that, freed pages are reclaimed in small batches
        automatically.
      </div>
      <div
        id="db-vacuum-status"
        style={{
          ...STATUS_STYLE,
          display: status ? 'block' : 'none',
          ...(status ? { background: 'rgba(255,255,255,0.04)', color: status.color } : {}),
        }}
      >
        {status?.text}
      </div>
    </div>
  );
}

/** changeLogLevel, and the level the server is running at */
function Logging({ store }: { store: SettingsStore }) {
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

  return (
    <div className="settings-group" data-stg={TAB}>
      <HelpInside help="Controls the level of detail in application logs. DEBUG shows all details, INFO shows general operations, WARNING and ERROR show only issues.">
        <label>Log Level:</label>
        <select
          id="log-level-select"
          className="form-select"
          value={level}
          onChange={(event) => void change(event.target.value)}
        >
          <option value="DEBUG">DEBUG (Detailed)</option>
          <option value="INFO">INFO (Normal)</option>
          <option value="WARNING">WARNING (Minimal)</option>
          <option value="ERROR">ERROR (Critical Only)</option>
        </select>
      </HelpInside>

      <div className="form-group">
        <label>Log Path:</label>
        <div className="readonly-field" id="log-path-display">
          {(loads && text(logging.path)) || 'logs/app.log'}
        </div>
      </div>
    </div>
  );
}

function DatabaseSections({ store, opened, active }: SettingsTabProps) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const workers = (loads && text((settings.database || {}).max_workers)) || '5';
  return (
    <>
      <Section tab={TAB} title="Database Settings" hint="Concurrency">
        <div className="settings-group" data-stg={TAB}>
          <HelpAfter help="Number of parallel threads for database updates. Higher values = faster updates but more server load.">
            <label>Concurrent Workers:</label>
            <select
              id="max-workers"
              value={workers}
              onChange={(event) =>
                store.set('database', 'max_workers', parseInt(event.target.value, 10))
              }
            >
              {['3', '4', '5', '6', '7', '8', '9', '10'].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </HelpAfter>
        </div>
      </Section>

      <Section tab={TAB} title="Database Maintenance" hint="Size, vacuum & cleanup">
        <DatabaseMaintenance opened={opened} active={active} />
      </Section>

      <Section tab={TAB} title="Logging" hint="Log level & path">
        <Logging store={store} />
      </Section>
    </>
  );
}

// ── 9b: SoulSync API, Developer Mode, Artwork Cache ──

type ApiKey = {
  id: string;
  label?: string;
  scope?: string;
  key_prefix?: string;
  created_at?: string;
  last_used_at?: string;
};

const KEY_ROW = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: '8px 10px',
  marginBottom: '4px',
  background: 'rgba(255,255,255,0.03)',
  border: '1px solid rgba(255,255,255,0.06)',
  borderRadius: '6px',
};
const MUTED = { color: '#666', fontSize: '13px' };
const HELP = { color: '#888', fontSize: '12px' };

type ConfirmShell = Window & {
  showConfirmDialog?: (options: Record<string, unknown>) => Promise<boolean>;
};

/** loadApiKeys, renderApiKeys, generateApiKey, copyApiKey, revokeApiKey */
function ApiKeys({ store }: { store: SettingsStore }) {
  const loads = useLoads(store);
  const [keys, setKeys] = useState<ApiKey[] | 'none' | null>(null);
  const [label, setLabel] = useState('');
  const [scope, setScope] = useState('read');
  const [generated, setGenerated] = useState<string | null>(null);

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
        toast('API key generated! Copy it now.', 'success');
        void load();
      } else {
        toast(data.error?.message || 'Failed to generate API key', 'error');
      }
    } catch (error) {
      console.error('Error generating API key:', error);
      toast('Failed to generate API key', 'error');
    }
  }

  function copy(code: HTMLElement | null) {
    if (!code) return;
    navigator.clipboard
      .writeText(code.textContent || '')
      .then(() => toast('API key copied to clipboard', 'success'))
      .catch(() => {
        // older browsers
        const range = document.createRange();
        range.selectNode(code);
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(range);
        document.execCommand('copy');
        toast('API key copied', 'success');
      });
  }

  async function revoke(key: ApiKey) {
    const name = key.label || 'this key';
    const ok = await (window as ConfirmShell).showConfirmDialog?.({
      title: 'Revoke API Key',
      message: `Revoke API key "${name}"? Any apps using this key will stop working.`,
      confirmText: 'Revoke',
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
    <div className="api-service-frame">
      <h4 className="service-title" style={{ color: '#64ffda' }}>
        REST API Keys
      </h4>
      <div className="setting-help-text" style={{ marginBottom: '12px', ...HELP }}>
        Generate API keys to access SoulSync remotely via the <code>/api/v1/</code> endpoints. Keys
        are shown once at creation — store them securely.
      </div>

      <div id="api-keys-list" style={{ marginBottom: '12px' }}>
        {keys === null ? (
          <div style={{ ...MUTED, padding: '8px 0' }}>Loading...</div>
        ) : keys === 'none' ? (
          <div style={MUTED}>No API keys configured.</div>
        ) : keys.length === 0 ? (
          <div style={{ ...MUTED, padding: '4px 0' }}>No API keys yet. Generate one below.</div>
        ) : (
          keys.map((k) => (
            <div key={k.id} style={KEY_ROW}>
              <div style={{ flex: '1', minWidth: '0' }}>
                <div style={{ fontSize: '13px', color: '#e0e0e0', fontWeight: '500' }}>
                  {k.label || 'Unnamed'}{' '}
                  <span
                    style={{
                      marginLeft: '6px',
                      padding: '1px 6px',
                      borderRadius: '4px',
                      fontSize: '10px',
                      fontWeight: '600',
                      ...(k.scope === 'read'
                        ? { background: 'rgba(100,255,218,0.12)', color: '#64ffda' }
                        : { background: 'rgba(255,171,64,0.12)', color: '#ffab40' }),
                    }}
                  >
                    {k.scope === 'read' ? 'Read-only' : 'Admin'}
                  </span>
                </div>
                <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>
                  <code>{k.key_prefix || 'sk_...'}...</code> &middot; Created{' '}
                  {k.created_at ? day(k.created_at) : 'unknown'}
                  {k.last_used_at ? ` · Last used ${day(k.last_used_at)}` : ''}
                </div>
              </div>
              <button
                className="revoke-api-key-btn"
                data-key-id={k.id}
                data-key-label={k.label || 'this key'}
                style={{
                  padding: '4px 10px',
                  background: 'rgba(255,82,82,0.1)',
                  border: '1px solid rgba(255,82,82,0.2)',
                  color: '#ff5252',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontSize: '11px',
                  whiteSpace: 'nowrap',
                }}
                onClick={(event) => {
                  event.preventDefault();
                  void revoke(k);
                }}
              >
                Revoke
              </button>
            </div>
          ))
        )}
      </div>

      <div className="form-group" style={{ marginBottom: '8px' }}>
        <label>New Key Label:</label>
        <input
          type="text"
          id="api-key-label"
          placeholder="e.g. Discord Bot, Home Assistant"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
        />
      </div>
      <div className="form-group" style={{ marginBottom: '8px' }}>
        <label>Access:</label>
        <select id="api-key-scope" value={scope} onChange={(event) => setScope(event.target.value)}>
          <option value="read">Read-only (stats and library, for dashboards)</option>
          <option value="admin">Admin (can change settings and everything else)</option>
        </select>
        <div className="setting-help-text" style={{ marginTop: '4px', ...HELP }}>
          A read-only key can fetch library and status data. It cannot change anything or read
          settings, keys or profiles.
        </div>
      </div>
      <div className="form-actions">
        <button className="auth-button" onClick={() => void generate()}>
          🔑 Generate API Key
        </button>
      </div>

      <div
        id="api-key-generated"
        style={{
          display: generated ? 'block' : 'none',
          marginTop: '12px',
          padding: '12px',
          background: 'rgba(100, 255, 218, 0.08)',
          border: '1px solid rgba(100, 255, 218, 0.2)',
          borderRadius: '8px',
        }}
      >
        <div style={{ fontSize: '11px', color: '#64ffda', marginBottom: '6px', fontWeight: '600' }}>
          Copy this key now — it won't be shown again!
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <code
            id="api-key-value"
            style={{
              flex: '1',
              fontSize: '12px',
              wordBreak: 'break-all',
              color: '#fff',
              background: 'rgba(0,0,0,0.3)',
              padding: '8px',
              borderRadius: '4px',
            }}
          >
            {generated}
          </code>
          <button
            onClick={() => copy(document.getElementById('api-key-value'))}
            style={{
              padding: '6px 12px',
              background: 'rgba(100,255,218,0.15)',
              border: '1px solid rgba(100,255,218,0.3)',
              color: '#64ffda',
              borderRadius: '4px',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            Copy
          </button>
        </div>
      </div>
    </div>
  );
}

type Profile = { id: number | string; name?: string; can_download?: boolean };

/** the Lidarr profile list (loadLidarrCompatProfiles) */
function LidarrProfile({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const saved = text((settings.lidarr_compat || {}).profile_id ?? 1) || '1';
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  useEffect(() => {
    if (!loads) return;
    void (async () => {
      try {
        const data = (await (await fetch('/api/profiles')).json()) as { profiles?: Profile[] };
        if (data.profiles?.length) setProfiles(data.profiles);
      } catch (error) {
        console.debug('lidarr profile list failed', error);
      }
    })();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  // a saved profile that is gone shows the first one, as the old list did
  const value =
    profiles && !profiles.some((p) => String(p.id) === String(parseInt(saved, 10) || 1))
      ? '1'
      : String(parseInt(saved, 10) || 1);
  return (
    <select
      id="lidarr-compat-profile"
      value={profiles ? value : '1'}
      onChange={(event) =>
        store.set('lidarr_compat', 'profile_id', parseInt(event.target.value, 10) || 1)
      }
    >
      {profiles ? (
        profiles.map((p) => (
          <option key={String(p.id)} value={String(p.id)}>
            {(p.name || 'Profile ' + p.id) + (p.can_download === false ? ' (needs approval)' : '')}
          </option>
        ))
      ) : (
        <option value="1">Loading...</option>
      )}
    </select>
  );
}

function ApiSection({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const check = (id: string, section: string, label: string) => (
    <div className="form-group">
      <label className="checkbox-label">
        <input
          type="checkbox"
          id={id}
          checked={loads ? (settings[section] || {}).enabled === true : false}
          onChange={(event) => store.set(section, 'enabled', event.target.checked)}
        />{' '}
        {label}
      </label>
    </div>
  );
  return (
    <Section tab={TAB} title="SoulSync API" hint="REST keys & inbound endpoint">
      <div className="settings-group" data-stg={TAB}>
        <ApiKeys store={store} />

        <div className="api-service-frame" style={{ marginTop: '16px' }}>
          <h4 className="service-title" style={{ color: '#64ffda' }}>
            Inbound Request Endpoint
          </h4>
          <div className="setting-help-text" style={{ marginBottom: '10px', ...HELP }}>
            Trigger music downloads from external tools (Discord bots, Home Assistant, curl, etc.)
            using any API key above.
          </div>
          <div
            style={{
              background: 'rgba(0,0,0,0.3)',
              borderRadius: '6px',
              padding: '10px',
              fontSize: '12px',
              color: '#ccc',
              overflowX: 'auto',
            }}
          >
            <code style={{ whiteSpace: 'pre', display: 'block' }}>
              {`curl -X POST http://<host>:8008/api/v1/request \\
  -H "Authorization: Bearer <your-api-key>" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "Artist - Track Name"}'`}
            </code>
          </div>
          <div
            className="setting-help-text"
            style={{ marginTop: '8px', color: '#666', fontSize: '11px' }}
          >
            Returns a <code>request_id</code> for status polling. Add <code>"notify_url"</code> to
            POST results to a webhook on completion. Also triggers any{' '}
            <strong>Webhook Received</strong> automations in the Automation Hub.
          </div>
        </div>

        <div className="api-service-frame" style={{ marginTop: '16px' }}>
          <h4 className="service-title" style={{ color: '#64ffda' }}>
            MCP for AI Clients
          </h4>
          <div className="setting-help-text" style={{ marginBottom: '10px', ...HELP }}>
            Lets Claude and other AI apps search, check your library and download through SoulSync.
            Add <code>http://&lt;host&gt;:8008/mcp</code> as an MCP server and send an API key from
            above as a Bearer token. A read-only key can look but not change anything. Settings,
            keys and profiles are never reachable.
          </div>
          {check('mcp-enabled', 'mcp', 'Answer MCP requests at /mcp')}
        </div>

        <div className="api-service-frame" style={{ marginTop: '16px' }}>
          <h4 className="service-title" style={{ color: '#64ffda' }}>
            Lidarr Compatible API
          </h4>
          <div className="setting-help-text" style={{ marginBottom: '10px', ...HELP }}>
            Lets request apps that only talk to Lidarr (Singarr, MusicSeerr 1.x) use SoulSync
            instead. In the app, set the Lidarr URL to <code>http://&lt;host&gt;:8008/lidarr</code>{' '}
            and paste an <strong>Admin</strong> API key from above. Albums it asks for go on the
            wishlist of the profile below.
          </div>
          {check('lidarr-compat-enabled', 'lidarr_compat', 'Answer as Lidarr at /lidarr')}
          <div className="form-group">
            <label>Requests go to profile:</label>
            <LidarrProfile store={store} />
            <div className="setting-help-text" style={{ marginTop: '4px', ...HELP }}>
              Pick a profile without download rights and every request waits for approval on the
              Requests page first.
            </div>
          </div>
        </div>
      </div>
    </Section>
  );
}

/** activateDevMode; the status follows ss:dev-mode, which every switch of
 *  dev mode sends (the load check, this button, Hydrabase's disconnect) */
function DevModeSection() {
  const [active, setActive] = useState(false);
  const [password, setPassword] = useState('');
  useEffect(() => {
    const follow = (event: Event) =>
      setActive((event as CustomEvent<{ enabled?: boolean }>).detail?.enabled === true);
    window.addEventListener('ss:dev-mode', follow);
    return () => window.removeEventListener('ss:dev-mode', follow);
  }, []);

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
    <Section tab={TAB} title="Developer Mode" hint="Advanced log viewer">
      <div className="settings-group" data-stg={TAB}>
        <div className="form-group">
          <label>Password:</label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              type="password"
              id="dev-mode-password"
              placeholder="Enter dev password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <button className="test-button" onClick={() => void activate()}>
              Activate
            </button>
          </div>
          <div
            id="dev-mode-status"
            style={{ marginTop: '8px', color: active ? 'rgb(var(--accent-light-rgb))' : '#888' }}
          >
            {active ? 'Active' : 'Inactive'}
          </div>
        </div>
      </div>
    </Section>
  );
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
  const mb = (n || 0) / (1024 * 1024);
  return mb >= 1024 ? (mb / 1024).toFixed(2) + ' GB' : mb.toFixed(1) + ' MB';
}

/** loadImageCacheStatus, runImageCachePrune, runImageCacheClear */
function ArtworkCacheSection({ store, opened, active }: SettingsTabProps) {
  const [cache, setCache] = useState<CacheStatus | null>(null);
  const [maxMb, setMaxMb] = useState('');
  const [thumbs, setThumbs] = useState(false);
  const [busy, setBusy] = useState<'prune' | 'clear' | null>(null);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);

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
    if (active) void load();
  }, [opened, active]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(kind: 'prune' | 'clear') {
    if (kind === 'clear') {
      const ok = await (window as ConfirmShell).showConfirmDialog?.({
        title: 'Clear the artwork cache?',
        message:
          'Every cached cover and poster is removed. Nothing is lost permanently — ' +
          'images are downloaded again as pages need them, so the only cost is a ' +
          'slower first load.',
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
          setStatus({
            error: false,
            text: freed
              ? `Removed ${freed} cached image${freed === 1 ? '' : 's'}.`
              : 'Nothing to reclaim — the cache is already within its limits.',
          });
        } else {
          setStatus({
            error: false,
            text: `Cleared ${data.removed || 0} cached image${data.removed === 1 ? '' : 's'}.`,
          });
        }
        void load();
      } else {
        setStatus({
          error: true,
          text:
            data.error ||
            (kind === 'prune' ? 'Could not reclaim space.' : 'Could not clear the cache.'),
        });
      }
    } catch (error) {
      const why = error instanceof Error ? error.message : String(error);
      setStatus({
        error: true,
        text:
          (kind === 'prune' ? 'Could not reclaim space: ' : 'Could not clear the cache: ') + why,
      });
    } finally {
      setBusy(null);
    }
  }

  const pending = cache?.pending || 0;
  const entries = !cache
    ? 'Loading...'
    : pending
      ? `${(cache.entries || 0).toLocaleString()} (${pending.toLocaleString()} pending)`
      : (cache.entries || 0).toLocaleString();
  const size = !cache
    ? 'Loading...'
    : cache.max_bytes
      ? `${cacheBytes(cache.bytes)} of ${cacheBytes(cache.max_bytes)}`
      : `${cacheBytes(cache.bytes)} (no limit)`;

  return (
    <Section tab={TAB} title="Artwork Cache" hint="Local copies of cover & poster art">
      <div className="settings-group" data-stg={TAB}>
        <div className="form-group">
          <label>Cached images:</label>
          <span id="imgcache-entries" className="readonly-field">
            {entries}
          </span>
        </div>
        <div className="form-group">
          <label>Disk used:</label>
          <span id="imgcache-size" className="readonly-field">
            {size}
          </span>
        </div>

        <div className="form-group">
          <label htmlFor="imgcache-max-mb">Size limit (MB):</label>
          <input
            type="number"
            id="imgcache-max-mb"
            min="0"
            step="128"
            style={{ maxWidth: '160px' }}
            value={maxMb}
            onChange={(event) => {
              setMaxMb(event.target.value);
              const n = parseInt(event.target.value, 10);
              store.set('image_cache', 'max_cache_mb', Number.isFinite(n) ? n : 0);
            }}
          />
        </div>
        <div className="help-text">
          Oldest-unused art is removed once the cache passes this size. <strong>0</strong> means no
          limit.
        </div>

        <div className="form-group">
          <label
            htmlFor="imgcache-thumbnails"
            style={{ display: 'flex', alignItems: 'center', gap: '8px' }}
          >
            <input
              type="checkbox"
              id="imgcache-thumbnails"
              checked={thumbs}
              onChange={(event) => {
                setThumbs(event.target.checked);
                store.set('image_cache', 'thumbnails', event.target.checked);
              }}
            />{' '}
            Serve resized thumbnails
          </label>
        </div>
        <div className="help-text">
          Off by default. When on, SoulSync resizes art server-side so a grid of tiles doesn't pull
          full-size originals — the big win on large libraries, at the cost of some CPU the first
          time each image is seen. Everything else about the cache is unchanged.
        </div>

        <div className="form-actions" style={{ flexWrap: 'wrap' }}>
          <button
            className="test-button"
            id="imgcache-prune-btn"
            disabled={busy === 'prune'}
            onClick={() => void act('prune')}
          >
            {busy === 'prune' ? 'Reclaiming...' : 'Reclaim space now'}
          </button>
          <button
            className="test-button"
            id="imgcache-clear-btn"
            disabled={busy === 'clear'}
            onClick={() => void act('clear')}
          >
            {busy === 'clear' ? 'Clearing...' : 'Clear cache'}
          </button>
        </div>
        <div className="help-text">
          Nothing here is irreplaceable — every image is re-downloaded on demand, so clearing only
          costs a slower first load.
        </div>
        <div
          id="imgcache-status"
          style={{
            display: status ? 'block' : 'none',
            marginTop: '8px',
            padding: '8px 12px',
            borderRadius: '8px',
            fontSize: '0.85em',
            ...(status
              ? {
                  background: status.error ? 'rgba(255,80,80,.12)' : 'rgba(80,255,150,.10)',
                  color: status.error ? '#ff9a9a' : '#8ee7b0',
                }
              : {}),
          }}
        >
          {status?.text}
        </div>
      </div>
    </Section>
  );
}

function ToolSections(props: SettingsTabProps) {
  return (
    <>
      <ApiSection store={props.store} />
      <DevModeSection />
      <ArtworkCacheSection {...props} />
    </>
  );
}

/** Developer: preview tools, nothing saved. The weather preview and the setup
 *  preview are wired by their own modules, by id; they run once the controls
 *  are on the page (both are safe to run again) */
function DeveloperSection() {
  useEffect(() => {
    // the shell's own copy: it is a separate bundle, and only its weather
    // state knows the sidebar weather is on
    window.initWeatherPreviewSettings?.();
    wireSetupPreviewControls();
  }, []);
  return (
    <>
      <Section tab={TAB} title="Developer" hint="Preview tools, nothing saved">
        <div className="settings-group" data-stg={TAB}>
          <div className="form-group">
            <label htmlFor="sw-preview-preset">Weather preview:</label>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <select id="sw-preview-preset" defaultValue="">
                <option value="">Live weather</option>
              </select>
              <select id="sw-preview-holiday" aria-label="Holiday" defaultValue="">
                <option value="">Holiday by date</option>
                <option value="none">No holiday</option>
              </select>
              <input type="date" id="sw-preview-date" aria-label="Pretend date" />
              <button className="test-button" id="sw-preview-reset" type="button">
                Back to live
              </button>
            </div>
            <div className="help-text">
              Shows any sky or holiday in the sidebar so you can check how it looks. Only this
              browser tab sees it and nothing is saved; the weather line reads "Preview" while it's
              on. The date pretends it's another day, which sets the season and the holiday.
            </div>
          </div>
          <div className="form-group">
            <label>Status:</label>
            <span id="sw-preview-status" className="readonly-field">
              Live weather
            </span>
          </div>
          <div className="form-group">
            <label htmlFor="ob-preview-step">Setup preview:</label>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <select id="ob-preview-step" aria-label="Start at" defaultValue="welcome">
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
              </select>
              <select id="ob-preview-env" aria-label="Pretend environment" defaultValue="real">
                <option value="real">Detect for real</option>
                <option value="docker">Pretend Docker</option>
                <option value="bare">Pretend bare metal</option>
                <option value="empty">Pretend nothing found</option>
              </select>
              <button className="test-button" id="ob-preview-open" type="button">
                Open preview
              </button>
            </div>
            <div className="help-text">
              Runs the real first-run setup over the app. Nothing is saved: settings, the profile,
              follows and sign-ins stay in the preview and are thrown away when you close it.
              Connection checks still talk to the real services, read only. A Preview tag stays in
              the corner the whole time.
            </div>
          </div>
        </div>
      </Section>

      <div
        className="settings-experimental-break"
        data-stg={TAB}
        role="separator"
        aria-label="Experimental features"
      >
        <span className="settings-experimental-break__line" aria-hidden="true"></span>
        <span className="settings-experimental-break__label">
          Experimental — proceed with caution
        </span>
        <span className="settings-experimental-break__line" aria-hidden="true"></span>
      </div>
    </>
  );
}

/** the System tab, part by part */
export function SystemTab(props: SettingsTabProps) {
  if (props.part === 'saving') return <SavingSection store={props.store} />;
  if (props.part === 'security') return <SecuritySection {...props} />;
  if (props.part === 'database') return <DatabaseSections {...props} />;
  if (props.part === 'tools') return <ToolSections {...props} />;
  if (props.part === 'developer') return <DeveloperSection />;
  // Experimental is the Connections tab's (the metadata source picker reads it)
  if (props.part === 'experimental') return <ExperimentalSection store={props.store} />;
  return null;
}
