import type { LastfmTrackResult } from '../-discover.lastfm-radio';
import type { LbTabId } from '../-discover.listenbrainz';
import type { DiscoverMix } from '../-discover.mixes';

import {
  lastfmSelectionLabel,
  resultShowsListeners,
  resultSubtitle,
} from '../-discover.lastfm-radio';
import {
  lbSubtitle,
  LB_CONNECT_TITLE,
  LB_EMPTY_CATEGORY,
  LB_TABS,
} from '../-discover.listenbrainz';
import { DiscoverSection } from './discover-section';
import { DiscoverMixCard } from './mix-shelf';

/**
 * The Last.fm Radio and ListenBrainz sections.
 *
 * Transcribed from index.html 5125-5182.
 *
 * Both render mix cards, so neither owns a card of its own. What they DO own is
 * their controls: a search box that generates a radio from one track, and a
 * tab set that has to distinguish "this category is empty" from "you have not
 * connected an account" — those are different problems with different fixes,
 * and one message for both sends the user to the wrong place.
 */

// ── Last.fm Radio ────────────────────────────────────────────────────────────

export interface LastfmRadioSectionProps {
  query: string;
  results: LastfmTrackResult[];
  /** Whether the dropdown is showing at all. */
  dropdownOpen: boolean;
  mixes: DiscoverMix[];
  loaded: boolean;
  generating?: boolean;
  onQueryChange: (query: string) => void;
  onPick: (track: LastfmTrackResult) => void;
  onClear: () => void;
  onOpenMix: (key: string) => void;
}

export function LastfmRadioSection({
  query,
  results,
  dropdownOpen,
  mixes,
  loaded,
  generating,
  onQueryChange,
  onPick,
  onClear,
  onOpenMix,
}: LastfmRadioSectionProps) {
  return (
    <DiscoverSection
      id="lastfm-radio"
      // The layout KEY is `lastfm-radio`; the vanilla's element is
      // `#lastfm-radio-section`, and style.css targets that.
      domId="lastfm-radio-section"
      title="📻 Last.fm Radio"
      subtitle="Search a track to generate a similar-tracks playlist"
      // This section is its own search UI, so it stays even with no radios yet.
      count={1}
      loaded={loaded}
    >
      <div className="lastfm-radio-search" id="lastfm-radio-search-section">
        <div className="lastfm-radio-search-row">
          <div className="lastfm-radio-input-wrap">
            <input
              type="text"
              id="lastfm-radio-input"
              placeholder="Search a track to generate a radio..."
              autoComplete="off"
              disabled={generating}
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') onClear();
              }}
            />
            {dropdownOpen && (
              <div id="lastfm-radio-dropdown" className="lastfm-radio-dropdown">
                {results.map((track) => (
                  <button
                    type="button"
                    key={lastfmSelectionLabel(track.name ?? '', track.artist ?? '')}
                    className="lastfm-radio-result"
                    onClick={() => onPick(track)}
                  >
                    <span className="lastfm-result-name">{track.name}</span>
                    <span className="lastfm-result-sub">{resultSubtitle(track)}</span>
                    {/* Listener counts are only shown where there IS one — a
                        "0 listeners" line reads as a judgement of the track. */}
                    {resultShowsListeners(track) && (
                      <span className="lastfm-result-listeners">{track.listeners}</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      <div id="lastfm-radio-playlists" className="discover-mixes-grid">
        {mixes.map((mix) => (
          <DiscoverMixCard key={mix.key} mix={mix} onOpen={onOpenMix} />
        ))}
      </div>
    </DiscoverSection>
  );
}

// ── ListenBrainz ─────────────────────────────────────────────────────────────

export interface ListenBrainzSectionProps {
  username: string | null;
  activeTab: LbTabId;
  /** Which tabs actually returned playlists. */
  hasData: Record<string, boolean>;
  mixes: DiscoverMix[];
  loading?: boolean;
  loaded: boolean;
  /** Sub-tab group names, when the active tab has enough to warrant them. */
  groups?: string[];
  activeGroup?: string | null;
  onSelectTab: (tab: LbTabId) => void;
  onSelectGroup: (group: string) => void;
  onRefresh: () => void;
  onOpenMix: (key: string) => void;
}

export function ListenBrainzSection({
  username,
  activeTab,
  hasData,
  mixes,
  loading,
  loaded,
  groups,
  activeGroup,
  onSelectTab,
  onSelectGroup,
  onRefresh,
  onOpenMix,
}: ListenBrainzSectionProps) {
  const anyData = Object.values(hasData).some(Boolean);

  return (
    <DiscoverSection
      id="listenbrainz"
      title="🧠 ListenBrainz Playlists"
      subtitle={lbSubtitle(username)}
      count={1}
      loaded={loaded}
      actions={
        <button
          type="button"
          className="action-button primary"
          id="listenbrainz-refresh-btn"
          title="Refresh playlists from ListenBrainz"
          onClick={onRefresh}
        >
          <span className="button-icon">🔄</span>
          <span className="button-text">Refresh</span>
        </button>
      }
    >
      <div className="listenbrainz-tabs" id="listenbrainz-tabs">
        {loading ? (
          <div className="discover-loading">
            <div className="loading-spinner" />
            <p>Loading playlists...</p>
          </div>
        ) : (
          LB_TABS.map((tab) => (
            <button
              type="button"
              key={tab.id}
              className={tab.id === activeTab ? 'listenbrainz-tab active' : 'listenbrainz-tab'}
              data-lb-tab={tab.id}
              // A tab with nothing behind it is shown but not selectable —
              // hiding it entirely would make the set jump around per refresh.
              disabled={!hasData[tab.id]}
              onClick={() => onSelectTab(tab.id)}
            >
              {tab.label}
            </button>
          ))
        )}
      </div>

      <div className="listenbrainz-tab-content" id="listenbrainz-tab-content">
        {/*
          Two different empty states. No data ANYWHERE means the account is not
          connected; no data in THIS tab means the category is empty. One
          message for both sends the user to the wrong fix.
        */}
        {!loading && !anyData ? (
          <div className="listenbrainz-connect">{LB_CONNECT_TITLE}</div>
        ) : (
          <>
            {groups && groups.length > 0 && (
              <div className="listenbrainz-sub-tabs">
                {groups.map((group) => (
                  <button
                    type="button"
                    key={group}
                    className={
                      group === activeGroup
                        ? 'listenbrainz-sub-tab-btn active'
                        : 'listenbrainz-sub-tab-btn'
                    }
                    onClick={() => onSelectGroup(group)}
                  >
                    {group}
                  </button>
                ))}
              </div>
            )}
            {mixes.length === 0 && !loading ? (
              <div className="listenbrainz-empty">{LB_EMPTY_CATEGORY}</div>
            ) : (
              <div className="discover-mixes-grid" id="listenbrainz-grid">
                {mixes.map((mix) => (
                  <DiscoverMixCard key={mix.key} mix={mix} onOpen={onOpenMix} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </DiscoverSection>
  );
}
