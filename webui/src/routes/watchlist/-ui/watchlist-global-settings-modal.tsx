import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import type { WatchlistGlobalConfig } from '../-watchlist.types';

import {
  saveWatchlistGlobalConfig,
  WATCHLIST_QUERY_KEY,
  watchlistGlobalConfigQueryOptions,
} from '../-watchlist.api';

/**
 * The eight "include" flags, in the order the vanilla modal listed them.
 *
 * Release types come first because they are the three the save validation
 * cares about; the rest are content filters.
 */
const RELEASE_TYPE_KEYS = ['include_albums', 'include_eps', 'include_singles'] as const;

const CONTENT_FILTER_KEYS = [
  'include_live',
  'include_remixes',
  'include_acoustic',
  'include_compilations',
  'include_instrumentals',
] as const;

const ALL_INCLUDE_KEYS = [...RELEASE_TYPE_KEYS, ...CONTENT_FILTER_KEYS] as const;

type IncludeKey = (typeof ALL_INCLUDE_KEYS)[number];

const OPTION_COPY: Record<IncludeKey, { icon: string; title: string; description: string }> = {
  include_albums: { icon: '💿', title: 'Albums', description: 'Full-length studio albums' },
  include_eps: { icon: '🎵', title: 'EPs', description: 'Extended plays (4-6 tracks)' },
  include_singles: {
    icon: '🎶',
    title: 'Singles',
    description: 'Single tracks and 2-3 track releases',
  },
  include_live: {
    icon: '🎤',
    title: 'Include Live Versions',
    description: 'Live performances and concerts',
  },
  include_remixes: {
    icon: '🎧',
    title: 'Include Remixes',
    description: 'Remix versions and edits',
  },
  include_acoustic: {
    icon: '🎸',
    title: 'Include Acoustic Versions',
    description: 'Acoustic and stripped versions',
  },
  include_compilations: {
    icon: '📀',
    title: 'Include Compilations',
    description: 'Greatest hits and collections',
  },
  include_instrumentals: {
    icon: '🎹',
    title: 'Include Instrumentals',
    description: 'Instrumental, karaoke, and backing track versions',
  },
};

export const EMPTY_GLOBAL_CONFIG: WatchlistGlobalConfig = {
  global_override_enabled: false,
  include_albums: true,
  include_eps: true,
  include_singles: true,
  include_live: false,
  include_remixes: false,
  include_acoustic: false,
  include_compilations: false,
  include_instrumentals: false,
  exclude_terms: '',
  global_auto_download: true,
};

/**
 * Whether the save is allowed to proceed.
 *
 * The gate only applies when the override is ON: with it off these values are
 * inert, so an all-unchecked config is legitimate and must still save. The
 * server enforces the same rule, but checking here keeps the message specific.
 */
export function globalConfigIsSavable(config: WatchlistGlobalConfig): boolean {
  if (!config.global_override_enabled) return true;
  return RELEASE_TYPE_KEYS.some((key) => config[key]);
}

/** "Include Everything" is ticked exactly when all eight flags are on. */
export function includeEverythingChecked(config: WatchlistGlobalConfig): boolean {
  return ALL_INCLUDE_KEYS.every((key) => config[key]);
}

export function setAllIncludes(
  config: WatchlistGlobalConfig,
  value: boolean,
): WatchlistGlobalConfig {
  const next = { ...config };
  for (const key of ALL_INCLUDE_KEYS) next[key] = value;
  return next;
}

interface Props {
  profileId: number;
  initialConfig: WatchlistGlobalConfig | null;
  onClose: () => void;
}

export function WatchlistGlobalSettingsModal({ profileId, initialConfig, onClose }: Props) {
  const queryClient = useQueryClient();
  const [config, setConfig] = useState<WatchlistGlobalConfig>(
    () => initialConfig ?? EMPTY_GLOBAL_CONFIG,
  );

  // The modal opens from cached config; if the fetch resolves later, adopt it
  // rather than showing stale defaults the user might then save over.
  useEffect(() => {
    if (initialConfig) setConfig(initialConfig);
  }, [initialConfig]);

  const save = useMutation({
    mutationFn: (next: WatchlistGlobalConfig) => saveWatchlistGlobalConfig(next),
    onSuccess: async (saved) => {
      window.showToast?.('Global watchlist settings saved', 'success');
      queryClient.setQueryData(watchlistGlobalConfigQueryOptions(profileId).queryKey, saved);
      // The grid's pills reflect per-artist settings, which the override
      // supersedes, so the whole page is refreshed exactly as before.
      await queryClient.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY });
      onClose();
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  const onSave = () => {
    if (!globalConfigIsSavable(config)) {
      window.showToast?.('Please select at least one release type', 'error');
      return;
    }
    save.mutate({ ...config, exclude_terms: config.exclude_terms.trim() });
  };

  const overrideEnabled = config.global_override_enabled;
  const setFlag = (key: IncludeKey, value: boolean) =>
    setConfig((previous) => ({ ...previous, [key]: value }));

  return (
    // See the artist config modal: these ids drive the style.css z-index rule
    // and helper.js's contextual help, and are free now the vanilla markup is gone.
    <div id="watchlist-global-config-modal-overlay" className="modal-overlay" onClick={onClose}>
      <div
        id="watchlist-global-config-modal"
        className="watchlist-artist-config-modal wl-global-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Global Watchlist Settings"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="wl-global-modal-head">
          <div>
            <h2 className="wl-global-modal-title">Global Watchlist Settings</h2>
            <p className="wl-global-modal-sub">
              Override per-artist settings for all watchlist scans.
            </p>
          </div>
          <button className="wl-global-modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="watchlist-artist-config-content">
          <div className="watchlist-artist-config-body">
            <div className="config-section">
              <label
                className={`config-option global-override-toggle${overrideEnabled ? ' enabled' : ''}`}
              >
                <input
                  type="checkbox"
                  checked={overrideEnabled}
                  onChange={(event) =>
                    setConfig((previous) => ({
                      ...previous,
                      global_override_enabled: event.target.checked,
                    }))
                  }
                />
                <div className="config-option-content">
                  <div className="config-option-icon">🌐</div>
                  <div className="config-option-text">
                    <span className="config-option-title">Enable Global Override</span>
                    <span className="config-option-description">
                      When enabled, the settings below apply to ALL artists, ignoring individual
                      artist configs
                    </span>
                  </div>
                </div>
              </label>
            </div>

            {/* Dimmed and click-through-disabled while the override is off,
                exactly as toggleGlobalOverrideOptions did. The values stay
                editable in state so nothing is lost by toggling. */}
            <div
              style={{
                opacity: overrideEnabled ? 1 : 0.4,
                pointerEvents: overrideEnabled ? 'auto' : 'none',
                transition: 'opacity 0.3s',
              }}
            >
              <div className="config-section">
                <h3 className="config-section-title">Release Types</h3>
                <p className="config-section-subtitle">
                  Select which release types to download for all artists
                </p>
                <div className="config-options">
                  {RELEASE_TYPE_KEYS.map((key) => (
                    <ConfigOption
                      key={key}
                      checked={config[key]}
                      onChange={(value) => setFlag(key, value)}
                      {...OPTION_COPY[key]}
                    />
                  ))}
                </div>
              </div>

              <div className="config-section">
                <h3 className="config-section-title">Content Filters</h3>
                <p className="config-section-subtitle">
                  Check to INCLUDE, leave unchecked to EXCLUDE (default: all excluded)
                </p>
                <div className="config-options">
                  {CONTENT_FILTER_KEYS.map((key) => (
                    <ConfigOption
                      key={key}
                      checked={config[key]}
                      onChange={(value) => setFlag(key, value)}
                      {...OPTION_COPY[key]}
                    />
                  ))}
                </div>
              </div>

              <div className="config-section">
                <label className="config-option include-everything-option">
                  <input
                    type="checkbox"
                    checked={includeEverythingChecked(config)}
                    onChange={(event) =>
                      setConfig((previous) => setAllIncludes(previous, event.target.checked))
                    }
                  />
                  <div className="config-option-content">
                    <div className="config-option-icon">✅</div>
                    <div className="config-option-text">
                      <span className="config-option-title">Include Everything</span>
                      <span className="config-option-description">
                        Enable all release types AND content filters (live, remixes, acoustic,
                        compilations, instrumentals)
                      </span>
                    </div>
                  </div>
                </label>
              </div>
            </div>

            {/* Outside the dimmed block on purpose, and it is NOT a checkbox in
                the list above: this is a DEFAULT, not an override. It decides
                what artists who never chose do, and any artist that did choose
                still wins. Folding it into the override switch would force
                auto-download on for everyone already using that switch for
                release formats. */}
            <div className="config-section">
              <h3 className="config-section-title">Auto-Download</h3>
              <p className="config-section-subtitle">
                The default for every artist you have not set individually. Artists you have
                explicitly switched on or off keep their own setting either way.
              </p>
              <label
                className={`config-option global-override-toggle${
                  config.global_auto_download ? ' enabled' : ''
                }`}
              >
                <input
                  type="checkbox"
                  checked={config.global_auto_download}
                  onChange={(event) =>
                    setConfig((previous) => ({
                      ...previous,
                      global_auto_download: event.target.checked,
                    }))
                  }
                />
                <div className="config-option-content">
                  <div className="config-option-icon">⬇️</div>
                  <div className="config-option-text">
                    <span className="config-option-title">
                      Auto-download new releases by default
                    </span>
                    <span className="config-option-description">
                      Off = follow only: scans still find new releases, but nothing is downloaded
                      unless you turn it on for that artist
                    </span>
                  </div>
                </div>
              </label>
            </div>

            <div className="config-section">
              <h3 className="config-section-title">Custom Exclusion Terms</h3>
              <p className="config-section-subtitle">
                Comma-separated terms — tracks or albums matching any term will be skipped during
                scans
              </p>
              <div className="config-exclude-terms-container">
                <input
                  type="text"
                  className="config-exclude-terms-input"
                  placeholder="e.g. commentary, interlude, skit, demo, a cappella"
                  aria-label="Custom exclusion terms"
                  value={config.exclude_terms}
                  onChange={(event) =>
                    setConfig((previous) => ({ ...previous, exclude_terms: event.target.value }))
                  }
                />
                <p className="config-exclude-terms-hint">
                  Applied globally to all watchlist scans regardless of override setting.
                  Case-insensitive substring matching.
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className="watchlist-artist-config-footer">
          <div className="config-modal-actions">
            <button className="btn btn--secondary" type="button" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn btn--primary"
              type="button"
              disabled={save.isPending}
              onClick={onSave}
            >
              {save.isPending ? 'Saving...' : 'Save Global Settings'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ConfigOption({
  checked,
  onChange,
  icon,
  title,
  description,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  icon: string;
  title: string;
  description: string;
}) {
  return (
    <label className="config-option">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <div className="config-option-content">
        <div className="config-option-icon">{icon}</div>
        <div className="config-option-text">
          <span className="config-option-title">{title}</span>
          <span className="config-option-description">{description}</span>
        </div>
      </div>
    </label>
  );
}
