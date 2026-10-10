import { useState } from 'react';

import { BodyPortal } from './portal';

/**
 * The "Add to Watchlist" modal on artist detail pages. The button used to be
 * a one-click add; now it opens this pop-in (like the video side's watchlist
 * button) so the artist's per-artist settings can be set at add time instead
 * of only afterwards on the watchlist page.
 *
 * The include_* fields are plain on/off booleans, shown here as the same
 * toggle pills the watchlist page uses (not a tall checkbox stack). Only
 * auto-download is tri-state (on / off / follow the global), matching that
 * modal.
 *
 * The Companion extension's 👁 badges are deliberately untouched: they stay
 * one-click by design.
 */

export interface WatchlistAddSettings {
  include_albums: boolean;
  include_eps: boolean;
  include_singles: boolean;
  include_live: boolean;
  include_remixes: boolean;
  include_acoustic: boolean;
  include_compilations: boolean;
  include_instrumentals: boolean;
  /** Include tracks by artists other than the watched one (tributes, soundtracks). Default off. */
  include_other_artists: boolean;
  auto_download_pref: 'on' | 'off' | null;
}

export const WATCHLIST_ADD_DEFAULTS: WatchlistAddSettings = {
  include_albums: true,
  include_eps: true,
  include_singles: true,
  include_live: false,
  include_remixes: false,
  include_acoustic: false,
  include_compilations: false,
  include_instrumentals: false,
  include_other_artists: false,
  auto_download_pref: null,
};

/** The boolean keys of the watchlist settings (everything except the
 * tri-state auto_download_pref). */
type WatchBoolKey = {
  [K in keyof WatchlistAddSettings]: WatchlistAddSettings[K] extends boolean ? K : never;
}[keyof WatchlistAddSettings];

const RELEASE_TYPES: ReadonlyArray<readonly [WatchBoolKey, string]> = [
  ['include_albums', 'Albums'],
  ['include_eps', 'EPs'],
  ['include_singles', 'Singles'],
];

const CONTENT_FILTERS: ReadonlyArray<readonly [WatchBoolKey, string]> = [
  ['include_live', 'Live'],
  ['include_remixes', 'Remixes'],
  ['include_acoustic', 'Acoustic'],
  ['include_compilations', 'Compilations'],
  ['include_instrumentals', 'Instrumentals'],
  ['include_other_artists', 'Other artists'],
];

/** One compact pill-toggle row for the watchlist settings. */
function WatchPillRow({
  label,
  options,
  settings,
  onToggle,
}: {
  label: string;
  options: ReadonlyArray<readonly [WatchBoolKey, string]>;
  settings: WatchlistAddSettings;
  onToggle: (key: WatchBoolKey) => void;
}): React.ReactNode {
  return (
    <div className="watchadd-row">
      <span className="watchadd-label">{label}</span>
      <div className="watchadd-pills">
        {options.map(([key, pillLabel]) => (
          <button
            type="button"
            key={key}
            className={`watchlist-filter-btn${settings[key] ? ' active' : ''}`}
            aria-pressed={settings[key]}
            onClick={() => onToggle(key)}
          >
            {pillLabel}
          </button>
        ))}
      </div>
    </div>
  );
}

export async function addArtistToWatchlist(
  artistId: unknown,
  artistName: string,
  settings: WatchlistAddSettings,
): Promise<string> {
  const addResponse = await fetch('/api/watchlist/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ artist_id: artistId, artist_name: artistName }),
  });
  const addData = await addResponse.json();
  if (!addData.success) throw new Error(addData.error || 'Failed to add to watchlist');

  const configResponse = await fetch(
    `/api/watchlist/artist/${encodeURIComponent(String(artistId))}/config`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        include_albums: settings.include_albums,
        include_eps: settings.include_eps,
        include_singles: settings.include_singles,
        include_live: settings.include_live,
        include_remixes: settings.include_remixes,
        include_acoustic: settings.include_acoustic,
        include_compilations: settings.include_compilations,
        include_instrumentals: settings.include_instrumentals,
        include_other_artists: settings.include_other_artists,
        auto_download_pref: settings.auto_download_pref,
      }),
    },
  );
  const configData = await configResponse.json();
  if (!configData.success) {
    throw new Error(configData.error || 'Failed to save watchlist settings');
  }
  if (typeof window.updateWatchlistCount === 'function') window.updateWatchlistCount();
  return String(addData.message ?? '');
}

export function WatchlistAddModal({
  artist,
  onClose,
  onWatched,
  onDownloadExisting,
}: {
  /** imageUrl is the artist's photo (release art as fallback); '' shows the note tile. */
  artist: { id: unknown; name: string; imageUrl?: string };
  onClose: () => void;
  onWatched: () => void;
  onDownloadExisting: () => void;
}) {
  const [settings, setSettings] = useState<WatchlistAddSettings>(WATCHLIST_ADD_DEFAULTS);
  const [downloadExisting, setDownloadExisting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (key: keyof WatchlistAddSettings) =>
    setSettings((prev) => ({ ...prev, [key]: !prev[key] }));

  const watch = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const message = await addArtistToWatchlist(artist.id, artist.name, settings);
      window.showToast?.(message || `Watching ${artist.name}`, 'success');
      onWatched();
      onClose();
      if (downloadExisting) onDownloadExisting();
    } catch (err) {
      setError((err as Error).message);
    }
    setBusy(false);
  };

  return (
    <BodyPortal>
      <div className="watchadd-modal-overlay visible" id="watchadd-modal-overlay">
        <div className="watchadd-modal" role="dialog" aria-label={`Watch ${artist.name}`}>
          <div className="watchadd-hero">
            {artist.imageUrl ? (
              <div
                className="watchadd-hero-bg"
                style={{ backgroundImage: `url('${artist.imageUrl}')` }}
                aria-hidden="true"
              />
            ) : null}
            <div className="watchadd-hero-content">
              {artist.imageUrl ? (
                <img className="watchadd-hero-photo" src={artist.imageUrl} alt="" />
              ) : (
                <div
                  className="watchadd-hero-photo watchadd-hero-photo-fallback"
                  aria-hidden="true"
                >
                  🎵
                </div>
              )}
              <div className="watchadd-hero-text">
                <div className="watchadd-title">Watch {artist.name}</div>
                <div className="watchadd-sub">Get notified of new releases</div>
              </div>
            </div>
            <button className="watchadd-close" type="button" onClick={onClose} aria-label="Close">
              ×
            </button>
          </div>

          <div className="watchadd-rows">
            <WatchPillRow
              label="Watch for"
              options={RELEASE_TYPES}
              settings={settings}
              onToggle={toggle}
            />
            <WatchPillRow
              label="Include"
              options={CONTENT_FILTERS}
              settings={settings}
              onToggle={toggle}
            />
            <div className="watchadd-row">
              <span className="watchadd-label">New releases</span>
              <select
                className="watchadd-select"
                aria-label="New releases from this artist"
                value={settings.auto_download_pref ?? ''}
                onChange={(event) =>
                  setSettings((prev) => ({
                    ...prev,
                    auto_download_pref:
                      event.target.value === '' ? null : (event.target.value as 'on' | 'off'),
                  }))
                }
              >
                <option value="">Follow global setting</option>
                <option value="on">Download automatically</option>
                <option value="off">Follow only — I&apos;ll pick</option>
              </select>
            </div>
            <label className="watchadd-opt watchadd-download">
              <input
                type="checkbox"
                checked={downloadExisting}
                onChange={() => setDownloadExisting((v) => !v)}
              />
              <span>Also download existing releases</span>
            </label>
          </div>

          {error ? <div className="watchadd-error">{error}</div> : null}

          <div className="watchadd-footer">
            <button className="discog-cancel-btn" type="button" onClick={onClose}>
              Cancel
            </button>
            <button
              className="discog-submit-btn"
              type="button"
              disabled={busy}
              onClick={() => void watch()}
            >
              <span className="discog-submit-icon">👁</span>
              <span>{busy ? 'Watching...' : 'Watch'}</span>
            </button>
          </div>
        </div>
      </div>
    </BodyPortal>
  );
}
