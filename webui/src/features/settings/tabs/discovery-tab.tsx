/**
 * The Discovery tab's cards (settings-bic DESIGN.md 3.3), on the parts:
 *  - Recommendations: how adventurous, the play history they read, and the
 *    discovery pool's lookback and hemisphere (music side, their own
 *    endpoints)
 *  - Playlists: server playlist sync, the M3U files, and the three M3U path
 *    options that were on Library
 *  - Import Lists (video side): video-row-lists.tsx
 *
 * Same keys, ids and saves as the page they replace: everything here goes
 * through the store (2 s), lookback and hemisphere through store.setExtra
 * with their own endpoints. tabs/discovery/discovery-page.tsx lays them out.
 */

import { useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsStore } from '../settings-store';

import { Card, NumberField, Row, Segmented, Select, Switch, TextField } from '../ui';
import s from '../ui/settings.module.css';
import {
  HEMISPHERES,
  HOLIDAY_MODES,
  LOOKBACK_PERIODS,
  MONTHS,
  SYNC_MODES,
  formatDay,
  parseDay,
  resolveDiscovery,
  sayDay,
} from './discovery';
import { useChanged } from './downloads/behaviour-view';
import { M3uExportOptions } from './library-folders';
import { useDraft } from './parts';

export function useDiscovery(store: SettingsStore) {
  const state = useSyncExternalStore(store.subscribe, store.getState);
  return { state, values: resolveDiscovery(state.settings) };
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

/**
 * The discovery pool's two settings: read from their own endpoints with
 * every settings load (not before), a pick not saved yet kept on screen.
 * null until loaded, so the hero does not read a guess back.
 */
export function usePool(store: SettingsStore) {
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  const [lookback, setLookback] = useState<string | null>(null);
  const [hemisphere, setHemisphere] = useState<string | null>(null);

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
          if (store.queuedExtra('discovery.lookback_period') === undefined)
            setLookback(data.period ? String(data.period) : '30');
        } catch (error) {
          console.error('Error loading discovery lookback period:', error);
          setLookback((v) => v ?? '30');
        }
      if (keptHemisphere === null)
        try {
          const data = (await (await fetch('/api/discovery/hemisphere')).json()) as {
            hemisphere?: string;
          };
          if (store.queuedExtra('discovery.hemisphere') === undefined)
            setHemisphere(data.hemisphere || 'northern');
        } catch (error) {
          console.error('Error loading hemisphere setting:', error);
          setHemisphere((v) => v ?? 'northern');
        }
    })();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    lookback,
    hemisphere,
    pickLookback(value: string) {
      setLookback(value);
      store.setExtra('discovery.lookback_period', value, saveLookback);
    },
    pickHemisphere(value: string) {
      setHemisphere(value);
      store.setExtra('discovery.hemisphere', value, saveHemisphere);
    },
  };
}

export type Pool = ReturnType<typeof usePool>;

export function Recommendations({ store, pool }: { store: SettingsStore; pool: Pool }) {
  const { state, values } = useDiscovery(store);
  const changed = (section: string, key: string) => useChanged(store, section, key); // eslint-disable-line react-hooks/rules-of-hooks

  const interval = useDraft(String(values.statsInterval), (raw) =>
    store.set('listening_stats', 'poll_interval', parseInt(raw, 10) || 30),
  );
  // a fresh load shows what was saved, not a half-typed number
  const loads = state.loads;
  useEffect(() => interval.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <Card title="How adventurous" hint="How far recommendations stray from what you know">
        <Row
          field
          label="Adventurousness"
          htmlFor="discover-adventurousness"
          changed={changed('discover', 'adventurousness')}
          help="Higher pushes globally popular artists down, so more obscure picks surface. Lower keeps recommendations close to what you already know."
          more={
            <>
              It shapes <strong>Based On Your Listening</strong> on the Discover page. This is the
              default for every profile: anyone who turns the dial on their Discover page keeps
              their own setting.
            </>
          }
        >
          <span className={s.rangeLine}>
            <span className={s.rangeEnd}>Safe</span>
            <input
              type="range"
              id="discover-adventurousness"
              className={s.range}
              min="0"
              max="1"
              step="0.05"
              value={values.adventurousness}
              onChange={(event) =>
                store.set('discover', 'adventurousness', parseFloat(event.target.value))
              }
            />
            <span className={s.rangeEnd}>Adventurous</span>
            <b className={s.rangeValue} id="discover-adventurousness-val">
              {values.adventurousness.toFixed(2)}
            </b>
          </span>
        </Row>
      </Card>

      <Card title="Listening stats" hint="Play history from your media server">
        <Row
          label="Collect listening stats from your media server"
          htmlFor="listening-stats-enabled"
          changed={changed('listening_stats', 'enabled')}
          help="Reads play history from your active media server (Plex, Jellyfin or Navidrome) and shows it on the Stats page."
        >
          <Switch
            id="listening-stats-enabled"
            checked={values.statsEnabled}
            onChange={(event) => store.set('listening_stats', 'enabled', event.target.checked)}
          />
        </Row>
        <Row
          sub
          field
          off={!values.statsEnabled}
          label="Check for new plays every"
          htmlFor="listening-stats-interval"
          changed={changed('listening_stats', 'poll_interval')}
          help={
            values.statsEnabled
              ? 'Between 5 minutes and a day.'
              : 'Used once listening stats are switched on.'
          }
        >
          <NumberField
            id="listening-stats-interval"
            min="5"
            max="1440"
            unit="minutes"
            value={interval.value}
            onChange={(event) => interval.onChange(event.target.value)}
          />
        </Row>
      </Card>

      {/* the discovery pool is the music side's own */}
      <div data-music-only="" className={s.stack}>
        <Card title="Discovery pool" hint="Where new releases for Discover come from">
          <Row
            field
            label="Lookback period"
            htmlFor="discovery-lookback-period"
            help="How far back to scan when an artist is added or the discovery pool refreshes. Once scanned, an artist is only checked for new releases."
          >
            <Select
              id="discovery-lookback-period"
              value={pool.lookback ?? '30'}
              options={LOOKBACK_PERIODS.map((o) => ({ ...o }))}
              onChange={(event) => pool.pickLookback(event.target.value)}
            />
          </Row>
          <Row
            field
            label="Hemisphere"
            help="Seasonal playlists on the Discover page follow your seasons."
            more="The southern hemisphere flips them: December is summer and June is winter. Holidays like Christmas and Halloween stay on their calendar dates."
          >
            <Segmented
              id="discovery-hemisphere"
              label="Hemisphere"
              value={pool.hemisphere ?? 'northern'}
              options={HEMISPHERES.map((o) => ({ ...o }))}
              onChange={(value) => pool.pickHemisphere(value)}
            />
          </Row>
        </Card>
        <HolidayMusic store={store} />
      </div>
    </>
  );
}

const MONTH_OPTIONS = MONTHS.map((name, i) => ({ value: String(i + 1), label: name }));
const DAY_OPTIONS = Array.from({ length: 31 }, (_, i) => ({
  value: String(i + 1),
  label: String(i + 1),
}));

/** one end of the season: a month and a day, saved as 'MM-DD' */
function SeasonDay({
  id,
  label,
  value,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  const [month, day] = parseDay(value, value);
  return (
    <span className={s.rangeLine}>
      <Select
        id={`${id}-month`}
        aria-label={`${label} month`}
        value={String(month)}
        options={MONTH_OPTIONS}
        disabled={disabled}
        onChange={(event) => onChange(formatDay(Number(event.target.value), day))}
      />
      <Select
        id={`${id}-day`}
        aria-label={`${label} day`}
        value={String(day)}
        options={DAY_OPTIONS}
        disabled={disabled}
        onChange={(event) => onChange(formatDay(month, Number(event.target.value)))}
      />
    </span>
  );
}

/**
 * #2332: christmas songs carry everyday genres, so they turned up in every
 * mix all year. when they may play, the season's dates, and an optional
 * Holiday genre in the files for other apps.
 */
export function HolidayMusic({ store }: { store: SettingsStore }) {
  const { values } = useDiscovery(store);
  const changed = (key: string) => useChanged(store, 'holiday_music', key); // eslint-disable-line react-hooks/rules-of-hooks
  const mode = HOLIDAY_MODES.find((m) => m.value === values.holidayMode);
  const season = values.holidayMode === 'season';

  return (
    <Card title="Holiday music" hint="Christmas songs only when it's the season">
      <Row
        field
        label="Holiday music in mixes"
        changed={changed('mode')}
        help={mode?.say ?? ''}
        more={
          <>
            SoulSync spots holiday music by its genres (Christmas, Holiday, Xmas), by titles like
            Jingle Bells, Silent Night, Navidad or Hanukkah, and by what you set yourself: an
            artist, album or track page has a <strong>Holiday music</strong> switch that a rescan or
            a retag never undoes. This covers daily mixes, genre mixes, discovery playlists, radio,
            shuffle and the player's music rows.
          </>
        }
      >
        <Segmented
          id="holiday-music-mode"
          label="Holiday music in mixes"
          value={values.holidayMode}
          options={HOLIDAY_MODES.map((m) => ({ value: m.value, label: m.label }))}
          onChange={(value) => store.set('holiday_music', 'mode', value)}
        />
      </Row>
      <Row
        sub
        field
        off={!season}
        label="Season starts"
        changed={changed('season_start')}
        help={
          season
            ? `Holiday songs come back on ${sayDay(values.holidayStart)}.`
            : 'Used when holiday music is set to In season.'
        }
      >
        <SeasonDay
          id="holiday-season-start"
          label="Season starts"
          value={values.holidayStart}
          disabled={!season}
          onChange={(value) => store.set('holiday_music', 'season_start', value)}
        />
      </Row>
      <Row
        sub
        field
        off={!season}
        label="Season ends"
        changed={changed('season_end')}
        help={
          season
            ? `The last day is ${sayDay(values.holidayEnd)}. A season can run over New Year.`
            : 'Used when holiday music is set to In season.'
        }
      >
        <SeasonDay
          id="holiday-season-end"
          label="Season ends"
          value={values.holidayEnd}
          disabled={!season}
          onChange={(value) => store.set('holiday_music', 'season_end', value)}
        />
      </Row>
      <Row
        label="Write a Holiday genre to your files"
        htmlFor="holiday-write-tag"
        changed={changed('write_tag')}
        help="Adds Holiday to the genre tag of holiday music, so Navidrome, AudioMuse and other apps can filter it too."
        more="Off by default. New downloads get it, and Write Tags or a retag adds it to what you have and keeps it there."
      >
        <Switch
          id="holiday-write-tag"
          checked={values.holidayTag}
          onChange={(event) => store.set('holiday_music', 'write_tag', event.target.checked)}
        />
      </Row>
    </Card>
  );
}

export function Playlists({ store }: { store: SettingsStore }) {
  const { values } = useDiscovery(store);
  const changed = (section: string, key: string) => useChanged(store, section, key); // eslint-disable-line react-hooks/rules-of-hooks
  const mode = SYNC_MODES.find((m) => m.value === values.syncMode);
  const replace = values.syncMode === 'replace';

  return (
    <>
      <Card title="Server playlist sync" hint="What a sync does to the playlist on your server">
        <Row
          field
          label="Sync mode"
          changed={changed('playlist_sync', 'mode')}
          help={mode?.say ?? ''}
          more="Replace wipes a custom image and description you set on the server. Reconcile adds new tracks and removes ones no longer in the source, so they survive. Append only ever adds."
        >
          <Segmented
            id="playlist-sync-mode"
            label="Sync mode"
            value={values.syncMode}
            options={SYNC_MODES.map((m) => ({ value: m.value, label: m.label }))}
            onChange={(value) => store.set('playlist_sync', 'mode', value)}
          />
        </Row>
        <Row
          sub
          off={!replace}
          label={'Keep a "<name> Backup" copy on the server first'}
          htmlFor="create-backup"
          changed={changed('playlist_sync', 'create_backup')}
          help={
            replace
              ? 'Copies the old server playlist before Replace rebuilds it, so anything you added on the server is not lost.'
              : 'Only Replace uses this: Reconcile and Append edit in place.'
          }
          more="SoulSync already keeps its own copy of the source playlist, so leave this off if you don't edit playlists on the server."
        >
          <Switch
            id="create-backup"
            checked={values.createBackup}
            onChange={(event) => store.set('playlist_sync', 'create_backup', event.target.checked)}
          />
        </Row>
        <Row
          label="Add missing tracks to the wishlist during sync"
          htmlFor="playlist-sync-wishlist-missing"
          changed={changed('playlist_sync', 'wishlist_missing_tracks')}
          help="Tracks a synced playlist has that your library doesn't are added to the wishlist, which queues them for download."
          more="Off: syncs skip the wishlist step and missing tracks are never added. A playlist's own organize or folder download setting still applies, and the Sync and Sync + download buttons always choose per click."
        >
          <Switch
            id="playlist-sync-wishlist-missing"
            checked={values.wishlistMissing}
            onChange={(event) =>
              store.set('playlist_sync', 'wishlist_missing_tracks', event.target.checked)
            }
          />
        </Row>
      </Card>

      <Card title="M3U files" hint="Playlist files saved beside your music">
        <Row
          label="Auto-save M3U file when downloading playlists"
          htmlFor="m3u-export-enabled"
          changed={changed('m3u_export', 'enabled')}
          help="Saves an M3U playlist file alongside your music. Albums are skipped: your media server already groups them."
        >
          <Switch
            id="m3u-export-enabled"
            checked={values.m3uExport}
            onChange={(event) => store.set('m3u_export', 'enabled', event.target.checked)}
          />
        </Row>
        <Row
          label="Keep a whole-library M3U"
          htmlFor="library-m3u-enabled"
          changed={changed('m3u_export', 'library_enabled')}
          help="One soulsync_library.m3u of your whole library, written again after every scan."
          more="Deep, incremental and full refresh scans all rewrite it, so it stays in step with the database."
        >
          <Switch
            id="library-m3u-enabled"
            checked={values.libraryM3u}
            onChange={(event) => store.set('m3u_export', 'library_enabled', event.target.checked)}
          />
        </Row>
        <Row
          sub
          wide
          off={!values.libraryM3u}
          label="Whole-library M3U folder"
          htmlFor="library-m3u-path"
          changed={changed('m3u_export', 'library_path')}
          help={
            values.libraryM3u
              ? 'Where the whole-library M3U is written. Empty uses the music library folder.'
              : 'Used once the whole-library M3U is switched on.'
          }
        >
          <TextField
            full
            mono
            id="library-m3u-path"
            icon="folder"
            placeholder="empty for the music library folder"
            value={values.libraryM3uPath}
            onChange={(event) => store.set('m3u_export', 'library_path', event.target.value)}
          />
        </Row>
      </Card>

      {/* the M3U path options, from Library > Folders (DESIGN 3.3) */}
      <M3uExportOptions store={store} />
    </>
  );
}
