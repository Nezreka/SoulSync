/**
 * What was Library > Video Preferences (settings-bic phase 4, DESIGN 3.3):
 * Autoplay trailers and the where-to-watch region are what pages show, so
 * Appearance draws them (VideoPagesCard, video-prefs.ts); Monitor Specials is
 * an acquisition rule, so Downloads > Behaviour, Video draws it
 * (MonitorSpecialsRow, a key of the video downloads config, posted alone so
 * nothing else there is written back). Each saves when changed, as before.
 */

import { Card, Row, Select, Switch } from '../ui';
import s from '../ui/settings.module.css';
import { DOWNLOADS_URL, useVideoDownloads, videoDownloads } from './video-downloads';
import { useVideoPrefs, videoPrefs } from './video-prefs';

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

/** Appearance, video side: what the movie and show pages show */
export function VideoPagesCard() {
  const { autoplay, region } = useVideoPrefs();
  return (
    <div data-ui="" data-video-only="" className={s.stack}>
      <Card title="Video pages" hint="What the movie and show pages show. Saves at once.">
        <Row
          label="Autoplay trailers in the billboard"
          htmlFor="video-billboard-autoplay"
          help="Plays a muted trailer behind the hero after a moment (uses more bandwidth)."
        >
          <Switch
            id="video-billboard-autoplay"
            data-video-pref="billboard_autoplay"
            checked={autoplay}
            onChange={(event) => void videoPrefs.change({ autoplay: event.target.checked })}
          />
        </Row>
        <Row
          field
          label="Where-to-watch region"
          htmlFor="video-watch-region"
          help="The country whose streaming services a title lists."
        >
          <Select
            id="video-watch-region"
            data-video-pref="watch_region"
            value={region}
            onChange={(event) => void videoPrefs.change({ region: event.target.value })}
            options={REGIONS.map(([value, label]) => ({ value, label }))}
          />
        </Row>
      </Card>
    </div>
  );
}

/** Downloads > Behaviour, Video: specials (season 0) hunted or not
 *  (monitor_specials in the video db; VideoDatabase._apply_specials_policy) */
export function MonitorSpecialsRow() {
  const { config } = useVideoDownloads();
  return (
    <Row
      label="Monitor Specials (season 0)"
      htmlFor="video-monitor-specials"
      help="Off: every show's Specials season is unmonitored, so its episodes are never searched or added to the wishlist."
      more="A show that is nothing but specials keeps them, and a Specials season you switch on by hand on a show's page stays on. Turning this back on monitors the ones it switched off."
    >
      <Switch
        id="video-monitor-specials"
        checked={config ? config.monitor_specials !== false : true}
        onChange={(event) => void saveSpecials(event.target.checked)}
      />
    </Row>
  );
}
