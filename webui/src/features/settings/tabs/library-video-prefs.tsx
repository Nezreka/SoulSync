/**
 * Library > Video Preferences (SETTINGS_PORT.md, video pass V2c): Detail
 * Pages (autoplay in the billboard, the where-to-watch region; video-prefs.ts)
 * and Monitoring (Monitor Specials, a key of the video downloads config, posted
 * alone so nothing else there is written back). Was index.html and
 * video-settings.js; each saves when changed, as before.
 */

import { type ReactNode, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';

import { Section } from './parts';
import { DOWNLOADS_URL, useVideoDownloads, videoDownloads } from './video-downloads';
import { useVideoPrefs, videoPrefs } from './video-prefs';

const TAB = 'library';

export const REGIONS: Array<[string, string]> = [
  ['US', 'United States'],
  ['GB', 'United Kingdom'],
  ['CA', 'Canada'],
  ['AU', 'Australia'],
  ['IE', 'Ireland'],
  ['NZ', 'New Zealand'],
  ['DE', 'Germany'],
  ['FR', 'France'],
  ['ES', 'Spain'],
  ['IT', 'Italy'],
  ['NL', 'Netherlands'],
  ['SE', 'Sweden'],
  ['NO', 'Norway'],
  ['DK', 'Denmark'],
  ['BR', 'Brazil'],
  ['MX', 'Mexico'],
  ['IN', 'India'],
  ['JP', 'Japan'],
  ['KR', 'South Korea'],
];

const toast = (message: string, type: string) => window.showToast?.(message, type);

/** saveMonitorSpecials: the one key, alone */
async function saveSpecials(on: boolean) {
  videoDownloads.edit({ monitor_specials: on });
  try {
    await fetch(DOWNLOADS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ monitor_specials: on }),
    });
    toast(on ? 'Specials monitored' : 'Specials unmonitored', 'success');
  } catch {
    toast('Could not save Monitor Specials', 'error');
  }
}

function Service({ name, title, children }: { name: string; title: string; children: ReactNode }) {
  return (
    <div className="api-service-frame stg-service" data-video-service={name}>
      <div
        className="stg-service-header"
        onClick={(event) => event.currentTarget.parentElement?.classList.toggle('expanded')}
      >
        <span className="stg-service-dot" style={{ color: '#38bdf8' }}></span>
        <h4 className="service-title">{title}</h4>
        <span className="stg-service-chevron">›</span>
      </div>
      <div className="stg-service-body">{children}</div>
    </div>
  );
}

export function VideoPrefs({ store }: SettingsTabProps) {
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  const { autoplay, region } = useVideoPrefs();
  const { config } = useVideoDownloads();
  // the region the select shows (one not in the list shows the first)
  const label = (REGIONS.find(([code]) => code === region) ?? REGIONS[0])[1];

  return (
    <>
      <div className="stg-cardgroup" data-stg={TAB}>
        <span className="stg-cardgroup-label">Video</span>
        <span className="stg-cardgroup-sub">Where to watch and what detail pages show</span>
      </div>
      <Section
        tab={TAB}
        title="Video Preferences"
        hint="Trailers & where-to-watch region"
        summaryKey="library-video-prefs"
        summary={loads ? `where to watch: ${label}` : ''}
      >
        <div className="settings-group" data-stg={TAB}>
          <Service name="prefs" title="Detail Pages">
            <label className="vid-pref-row">
              <input
                type="checkbox"
                id="video-billboard-autoplay"
                data-video-pref="billboard_autoplay"
                checked={autoplay}
                onChange={(event) => void videoPrefs.change({ autoplay: event.target.checked })}
              />{' '}
              <span>Autoplay trailers in the billboard</span>
            </label>
            <div className="callback-info">
              <div className="callback-help">
                Plays a muted trailer behind the hero after a moment (uses more bandwidth).
              </div>
            </div>
            <label className="vid-pref-row vid-pref-row--select">
              <span>Where-to-watch region</span>{' '}
              <select
                id="video-watch-region"
                data-video-pref="watch_region"
                value={region}
                onChange={(event) => void videoPrefs.change({ region: event.target.value })}
              >
                {REGIONS.map(([code, name]) => (
                  <option key={code} value={code}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          </Service>

          {/* Specials (season 0): hunt them or not (monitor_specials in the
              video db; VideoDatabase._apply_specials_policy) */}
          <Service name="monitoring" title="Monitoring">
            <label className="vid-pref-row">
              <input
                type="checkbox"
                id="video-monitor-specials"
                checked={config ? config.monitor_specials !== false : true}
                onChange={(event) => void saveSpecials(event.target.checked)}
              />{' '}
              <span>Monitor Specials (season 0)</span>
            </label>
            <div className="callback-info">
              <div className="callback-help">
                Off: every show&apos;s Specials season is unmonitored, so its episodes are never
                searched or added to the wishlist. A show that is nothing but specials keeps them,
                and a Specials season you switch on by hand on a show&apos;s page stays on. Turning
                this back on monitors the ones it switched off.
              </div>
            </div>
          </Service>
        </div>
      </Section>
    </>
  );
}
