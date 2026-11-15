/**
 * The System tab (SETTINGS_PORT.md, step 9), part by part. Same markup,
 * classes and ids. Security & Access and the sections after Logging are
 * still vanilla until their own steps (9b, 9c).
 *
 * 9a: Saving (the autosave switch), Database Settings, Database Maintenance
 * and Logging.
 */

import { type ReactNode, useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';

import { AUTOSAVE_DELAY_MS, type SettingsStore } from '../settings-store';
import { helpBits, Section, useHelp } from './parts';
import { text } from './source-panels';

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

/** the System tab, part by part */
export function SystemTab(props: SettingsTabProps) {
  if (props.part === 'saving') return <SavingSection store={props.store} />;
  if (props.part === 'database') return <DatabaseSections {...props} />;
  return null;
}
