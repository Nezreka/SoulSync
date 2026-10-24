import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import type { WatchlistGlobalConfig } from '../-watchlist.types';

import {
  resetWatchlistToDefaults,
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
  'include_other_artists',
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
  include_other_artists: {
    icon: '👥',
    title: 'Include Other Artists',
    description: 'Tracks by other artists found on tributes, soundtracks and compilations',
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
  include_other_artists: false,
  exclude_terms: '',
  global_auto_download: true,
  // #1450: defaults match the server ("all" = today's behaviour, opt-in).
  edition_preference: 'all',
  prefer_explicit_edition: true,
};

/**
 * #1450: the three edition-preference choices as compact toggle pills,
 * reusing the .watchlist-filter-btn pill style the discography modal's
 * Future-releases rows use (NOT another tall checkbox stack).
 */
const EDITION_PILL_OPTIONS = [
  ['all', 'All editions'],
  ['one_standard', 'One per album · Standard'],
  ['one_complete', 'One per album · Most complete'],
] as const;

/**
 * Whether the save is allowed to proceed.
 *
 * These are the defaults every following artist uses (#1822), so they always
 * need a release type. The server enforces the same rule; checking here keeps
 * the message specific.
 */
export function globalConfigIsSavable(config: WatchlistGlobalConfig): boolean {
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

  const reset = useMutation({
    mutationFn: () => resetWatchlistToDefaults(),
    onSuccess: async (count) => {
      window.showToast?.(
        `${count} artist${count === 1 ? '' : 's'} back on the defaults`,
        'success',
      );
      setConfig((previous) => ({ ...previous, custom_artist_count: 0 }));
      await queryClient.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY });
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  const onReset = async () => {
    const count = config.custom_artist_count ?? 0;
    const confirmed = await window.showConfirmDialog?.({
      title: 'Reset to defaults',
      message: `${count} artist${count === 1 ? ' has its' : 's have their'} own rules. Put ${
        count === 1 ? 'it' : 'them'
      } back on the defaults?`,
      confirmText: 'Reset',
      destructive: true,
    });
    if (confirmed === false) return;
    reset.mutate();
  };

  const save = useMutation({
    mutationFn: (next: WatchlistGlobalConfig) => saveWatchlistGlobalConfig(next),
    onSuccess: async (saved) => {
      window.showToast?.('Watchlist defaults saved', 'success');
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
    // the override is retired (#1822): defaults are what followers use
    save.mutate({
      ...config,
      global_override_enabled: false,
      exclude_terms: config.exclude_terms.trim(),
    });
  };

  const customCount = config.custom_artist_count ?? 0;
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
        aria-label="Watchlist defaults"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="wl-global-modal-head">
          <div>
            <h2 className="wl-global-modal-title">Watchlist defaults</h2>
            <p className="wl-global-modal-sub">
              Every artist uses these unless you give it its own rules.
            </p>
          </div>
          <button className="wl-global-modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="watchlist-artist-config-content">
          <div className="watchlist-artist-config-body">
            {customCount > 0 ? (
              <div className="config-section wl-defaults-custom">
                <span>
                  {customCount} artist{customCount === 1 ? ' has its' : 's have their'} own rules
                  and {customCount === 1 ? "doesn't" : "don't"} use these.
                </span>
                <button
                  type="button"
                  className="btn btn--secondary"
                  disabled={reset.isPending}
                  onClick={() => void onReset()}
                >
                  {reset.isPending ? 'Resetting…' : 'Reset all to defaults'}
                </button>
              </div>
            ) : null}

            <div>
              <div className="config-section">
                <h3 className="config-section-title">Release Types</h3>
                <p className="config-section-subtitle">Which release types to grab</p>
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

            {/* Outside the dimmed override block like Auto-Download: this is a
                global preference, not a per-artist override. Compact pill
                rows in the .watchlist-filter-btn toggle style, matching the
                discography modal's Future-releases rows. */}
            <div className="config-section">
              <h3 className="config-section-title">Edition Preference</h3>
              <p className="config-section-subtitle">
                When an album exists in several editions, scan for and pre-check one edition per
                album instead of every edition
              </p>
              <div className="discog-future-row">
                <span className="discog-future-label">Editions</span>
                <div
                  className="discog-future-pills"
                  role="radiogroup"
                  aria-label="Edition preference"
                >
                  {EDITION_PILL_OPTIONS.map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={config.edition_preference === value}
                      className={`watchlist-filter-btn${
                        config.edition_preference === value ? ' active' : ''
                      }`}
                      onClick={() =>
                        setConfig((previous) => ({ ...previous, edition_preference: value }))
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="discog-future-row" style={{ marginTop: 8 }}>
                <span className="discog-future-label">Tie-break</span>
                <div className="discog-future-pills">
                  <button
                    type="button"
                    aria-pressed={config.prefer_explicit_edition}
                    className={`watchlist-filter-btn${
                      config.prefer_explicit_edition ? ' active' : ''
                    }`}
                    onClick={() =>
                      setConfig((previous) => ({
                        ...previous,
                        prefer_explicit_edition: !previous.prefer_explicit_edition,
                      }))
                    }
                  >
                    Prefer explicit edition
                  </button>
                </div>
              </div>
            </div>

            <div className="config-section">
              <h3 className="config-section-title">Custom Exclusion Terms</h3>{' '}
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
                  Applied to every watchlist scan. Case-insensitive substring matching.
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
              {save.isPending ? 'Saving...' : 'Save defaults'}
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
