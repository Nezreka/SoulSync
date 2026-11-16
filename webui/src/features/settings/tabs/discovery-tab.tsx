/**
 * The Discovery tab of Settings, ported from index.html and settings.js
 * (SETTINGS_PORT.md, step 4). Same markup, classes and ids as the vanilla
 * page. Two slots hold it:
 *  - part "main": the "Discovery & playlists" group, Playlists, Listening
 *    Stats and Discovery
 *  - part "pool": Discovery Pool Settings (music only)
 *
 * Import Lists, the video-only section of this tab, is still vanilla
 * (video-settings.js) until the video pass.
 */

import { useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';

import {
  HEMISPHERES,
  LOOKBACK_PERIODS,
  SYNC_MODES,
  discoverySummaries,
  resolveDiscovery,
} from './discovery';
import { Section, helpBits, useDraft, useHelp } from './parts';
import { ImportListsSection } from './video-row-lists';

const TAB = 'discovery';

const HELP = {
  m3uExport:
    "Saves an M3U playlist file alongside your music. Albums are skipped — they're already grouped by your media server.",
  libraryM3u:
    'Keeps a single soulsync_library.m3u of your entire library, rewritten after every scan (deep, incremental, or full refresh) so it stays in sync with the database.',
  libraryM3uPath:
    'Where the whole-library M3U is written. Leave empty to use your Music Library folder.',
  syncMode:
    '"Replace" deletes and recreates the server playlist every sync, which wipes its custom image/description. "Reconcile" updates the same playlist in place (adds new tracks, removes ones no longer in the source) so your custom image and description survive. "Append" only ever adds.',
  wishlistMissing:
    "When on, syncing a playlist adds tracks missing from your library to the wishlist (which queues them for download). Turn it off and syncs skip the wishlist step — missing tracks are never added to the wishlist. (A playlist's own organize/folder-download setting is separate and still applies.) The Sync / Sync + download buttons always choose per click, regardless of this setting.",
  createBackup:
    "Replace deletes the playlist on your media server and rebuilds it. This copies the old server playlist first, so anything you added to it on the server isn't lost. SoulSync already keeps its own copy of the source playlist, so turn this off if you don't edit playlists on the server. Reconcile and Append edit in place, so they normally skip it.",
  stats:
    'Polls your active media server (Plex, Jellyfin, or Navidrome) for play history and displays stats on the Stats page.',
  lookback:
    'Controls how far back to scan when adding new artists or refreshing discovery pool. Once scanned, artists are kept updated with new releases only.',
  hemisphere:
    'Adjusts seasonal playlists on the Discover page to match your region. Southern hemisphere flips seasons (e.g. December = Summer, June = Winter). Holidays like Christmas and Halloween stay calendar-fixed.',
};

/** a tick box with its help. the markup has the help body either after the
 *  form-group or, `inside`, as its last element before the help line */
function Check({
  id,
  label,
  help,
  checked,
  onChange,
  inside = false,
}: {
  id: string;
  label: string;
  help: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  inside?: boolean;
}) {
  const state = useHelp();
  const bits = helpBits(help, state);
  const body = (
    <div className="help-text setting-help-body" hidden={!state.open}>
      {help}
    </div>
  );
  return (
    <>
      <div className="form-group">
        <label className="checkbox-label">
          <input
            type="checkbox"
            id={id}
            checked={checked}
            onChange={(event) => onChange(event.target.checked)}
          />
          {label}
        </label>
        {bits.icon}
        {inside ? body : null}
        {bits.line}
      </div>
      {inside ? null : body}
    </>
  );
}

function Main({ store }: SettingsTabProps) {
  const state = useSyncExternalStore(store.subscribe, store.getState);
  const values = resolveDiscovery(state.settings);
  const summary = discoverySummaries(values);
  const set = (section: string, key: string) => (value: unknown) => store.set(section, key, value);

  const pathHelp = useHelp();
  const pathBits = helpBits(HELP.libraryM3uPath, pathHelp);
  const modeHelp = useHelp();
  const modeBits = helpBits(HELP.syncMode, modeHelp);
  const statsHelp = useHelp();
  const statsBits = helpBits(HELP.stats, statsHelp);
  const libraryHelp = useHelp();
  const libraryBits = helpBits(HELP.libraryM3u, libraryHelp);

  const interval = useDraft(String(values.statsInterval), (raw) =>
    store.set('listening_stats', 'poll_interval', parseInt(raw, 10) || 30),
  );
  // a fresh load shows what was saved, not a half-typed number
  const loads = state.loads;
  useEffect(() => interval.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="stg-cardgroup" data-stg={TAB}>
        <span className="stg-cardgroup-label">Discovery &amp; playlists</span>
        <span className="stg-cardgroup-sub">Playlists, history and how recommendations behave</span>
      </div>

      <Section
        tab={TAB}
        title="Playlists"
        hint="M3U export & server playlist sync"
        summaryKey="library-playlists"
        summary={state.loaded ? summary.playlists : ''}
      >
        <div className="settings-group" data-stg={TAB}>
          <Check
            id="m3u-export-enabled"
            label="Auto-save M3U file when downloading playlists"
            help={HELP.m3uExport}
            checked={values.m3uExport}
            onChange={set('m3u_export', 'enabled')}
          />

          <div className="form-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                id="library-m3u-enabled"
                checked={values.libraryM3u}
                onChange={(event) =>
                  store.set('m3u_export', 'library_enabled', event.target.checked)
                }
              />
              Auto-generate a whole-library M3U (updates on every scan)
            </label>
            {libraryBits.icon}
            {libraryBits.line}
          </div>
          <div className="help-text setting-help-body" hidden={!libraryHelp.open}>
            Keeps a single <code>soulsync_library.m3u</code> of your entire library, rewritten after
            every scan (deep, incremental, or full refresh) so it stays in sync with the database.
          </div>
          <div className="form-group">
            <label>Whole-library M3U output folder:</label>
            <input
              type="text"
              id="library-m3u-path"
              className="form-input"
              placeholder="leave empty for the Music Library folder"
              value={values.libraryM3uPath}
              onChange={(event) => store.set('m3u_export', 'library_path', event.target.value)}
            />
            {pathBits.icon}
            <div className="help-text setting-help-body" hidden={!pathHelp.open}>
              {HELP.libraryM3uPath}
            </div>
            {pathBits.line}
          </div>

          <div className="form-group">
            <label>Sync mode:</label>
            <select
              id="playlist-sync-mode"
              className="form-select"
              value={values.syncMode}
              onChange={(event) => store.set('playlist_sync', 'mode', event.target.value)}
            >
              {SYNC_MODES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {modeBits.icon}
            <div className="help-text setting-help-body" hidden={!modeHelp.open}>
              {HELP.syncMode}
            </div>
            {modeBits.line}
          </div>

          <Check
            id="playlist-sync-wishlist-missing"
            label="Add missing tracks to the wishlist during sync"
            help={HELP.wishlistMissing}
            checked={values.wishlistMissing}
            onChange={set('playlist_sync', 'wishlist_missing_tracks')}
            inside
          />
          <Check
            id="create-backup"
            label={'Keep a "<name> Backup" copy on the server before a Replace sync'}
            help={HELP.createBackup}
            checked={values.createBackup}
            onChange={set('playlist_sync', 'create_backup')}
            inside
          />
        </div>
      </Section>

      <Section
        tab={TAB}
        title="Listening Stats"
        hint="Play history from your media server"
        summaryKey="library-stats"
        summary={state.loaded ? summary.stats : ''}
      >
        <div className="settings-group" data-stg={TAB}>
          <div className="form-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                id="listening-stats-enabled"
                checked={values.statsEnabled}
                onChange={(event) => store.set('listening_stats', 'enabled', event.target.checked)}
              />
              Enable listening stats collection from media server
            </label>
            {statsBits.icon}
            {statsBits.line}
          </div>
          <div className="help-text setting-help-body" hidden={!statsHelp.open}>
            {HELP.stats}
          </div>
          <div className="form-group">
            <label>Poll Interval (minutes):</label>
            <input
              type="number"
              id="listening-stats-interval"
              min="5"
              max="1440"
              value={interval.value}
              onChange={(event) => interval.onChange(event.target.value)}
            />
          </div>
        </div>
      </Section>

      <Section
        tab={TAB}
        title="Discovery"
        hint="How adventurous your recommendations are"
        summaryKey="library-discovery"
        summary={state.loaded ? summary.discovery : ''}
      >
        <div className="settings-group" data-stg={TAB}>
          <div className="form-group">
            <label>Adventurousness</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '8px' }}>
              <span style={{ fontSize: '12px', color: '#999' }}>Safe</span>
              <input
                type="range"
                id="discover-adventurousness"
                min="0"
                max="1"
                step="0.05"
                value={values.adventurousness}
                style={{ flex: 1 }}
                onChange={(event) =>
                  store.set('discover', 'adventurousness', parseFloat(event.target.value))
                }
              />
              <span style={{ fontSize: '12px', color: '#999' }}>Adventurous</span>
              <span
                id="discover-adventurousness-val"
                style={{
                  minWidth: '36px',
                  textAlign: 'right',
                  fontWeight: 700,
                  color: 'var(--accent)',
                }}
              >
                {values.adventurousness.toFixed(2)}
              </span>
            </div>
            <div className="help-text setting-help-body" style={{ marginTop: '10px' }}>
              Higher pushes globally-popular artists down so more obscure, non-obvious picks
              surface; lower keeps recommendations safe and close to what you already know. Affects{' '}
              <strong>Based On Your Listening</strong>.
            </div>
          </div>
        </div>
      </Section>
    </>
  );
}

async function postJson(url: string, body: unknown) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await response.json()) as { success?: boolean };
}

/** settings.js saved these with every page save, outside /api/settings. a
 *  failed lookback was a warning beside "saved"; a failed hemisphere was
 *  only logged */
export async function saveLookback(period: unknown): Promise<boolean> {
  try {
    const result = await postJson('/api/discovery/lookback-period', { period });
    if (result.success === true) return true;
  } catch (error) {
    console.error('Error saving discovery lookback period:', error);
  }
  window.showToast?.('Settings saved, but discovery lookback period failed to save', 'warning');
  return true;
}

export async function saveHemisphere(hemisphere: unknown): Promise<boolean> {
  try {
    await postJson('/api/discovery/hemisphere', { hemisphere });
  } catch (error) {
    console.error('Error saving hemisphere setting:', error);
  }
  return true;
}

function Pool({ store }: SettingsTabProps) {
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  const [lookback, setLookback] = useState('30');
  const [hemisphere, setHemisphere] = useState('northern');
  const lookbackHelp = useHelp();
  const lookbackBits = helpBits(HELP.lookback, lookbackHelp);
  const hemiHelp = useHelp();
  const hemiBits = helpBits(HELP.hemisphere, hemiHelp);

  // each time the page loads its settings, as loadSettingsData did
  useEffect(() => {
    if (loads === 0) return;
    // a pick not saved yet (autosave off) is what the save button sends: it
    // stays on screen, as the old page's dropdown kept it until the next load
    const queued = (key: string) => {
      const value = store.queuedExtra(key);
      return typeof value === 'string' ? value : null;
    };
    void (async () => {
      const keptLookback = queued('discovery.lookback_period');
      const keptHemisphere = queued('discovery.hemisphere');
      if (keptLookback !== null) setLookback(keptLookback);
      if (keptHemisphere !== null) setHemisphere(keptHemisphere);
      if (keptLookback === null)
        try {
          const data = (await (await fetch('/api/discovery/lookback-period')).json()) as {
            period?: string;
          };
          if (data.period && store.queuedExtra('discovery.lookback_period') === undefined)
            setLookback(String(data.period));
        } catch (error) {
          console.error('Error loading discovery lookback period:', error);
        }
      if (keptHemisphere === null)
        try {
          const data = (await (await fetch('/api/discovery/hemisphere')).json()) as {
            hemisphere?: string;
          };
          if (data.hemisphere && store.queuedExtra('discovery.hemisphere') === undefined)
            setHemisphere(data.hemisphere);
        } catch (error) {
          console.error('Error loading hemisphere setting:', error);
        }
    })();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Section tab={TAB} title="Discovery Pool Settings" hint="Lookback & hemisphere" side="music">
      <div className="settings-group" data-stg={TAB} data-music-only="">
        <div className="form-group">
          <label>Lookback Period:</label>
          <select
            id="discovery-lookback-period"
            className="form-select"
            value={lookback}
            onChange={(event) => {
              setLookback(event.target.value);
              store.setExtra('discovery.lookback_period', event.target.value, saveLookback);
            }}
          >
            {LOOKBACK_PERIODS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {lookbackBits.icon}
          <div className="setting-help-text setting-help-body" hidden={!lookbackHelp.open}>
            {HELP.lookback}
          </div>
          {lookbackBits.line}
        </div>

        <div className="form-group">
          <label>Hemisphere:</label>
          <select
            id="discovery-hemisphere"
            className="form-select"
            value={hemisphere}
            onChange={(event) => {
              setHemisphere(event.target.value);
              store.setExtra('discovery.hemisphere', event.target.value, saveHemisphere);
            }}
          >
            {HEMISPHERES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {hemiBits.icon}
          <div className="setting-help-text setting-help-body" hidden={!hemiHelp.open}>
            {HELP.hemisphere}
          </div>
          {hemiBits.line}
        </div>
      </div>
    </Section>
  );
}

export function DiscoveryTab(props: SettingsTabProps) {
  if (props.part === 'import-lists') return <ImportListsSection />;
  return props.part === 'pool' ? <Pool {...props} /> : <Main {...props} />;
}
