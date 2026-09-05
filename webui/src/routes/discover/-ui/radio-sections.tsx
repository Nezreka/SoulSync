import { useEffect, useRef, useState } from 'react';

import type { LastfmTrackResult } from '../-discover.lastfm-radio';
import type { LbTabId } from '../-discover.listenbrainz';
import type { DiscoverMix } from '../-discover.mixes';

import { resultSubtitle } from '../-discover.lastfm-radio';
import { lbSubtitle, LB_LOAD_FAILED, LB_TABS } from '../-discover.listenbrainz';
import { DiscoverSection } from './discover-section';
import { DiscoverMixCard } from './mix-shelf';

/**
 * The Last.fm Radio and ListenBrainz sections.
 *
 * Transcribed from index.html 5125-5182, discover.js 3250-3300 (the search
 * dropdown) and 3458-3675 (the ListenBrainz tabs, connect state and groups).
 *
 * HISTORY. The first version of both was invented: made-up row classes with no
 * artwork on the Last.fm results, a made-up `.listenbrainz-tab` tab strip with
 * disabled placeholder tabs, and a one-line connect prompt. The vanilla reuses
 * the DECADE tab styling (`.decade-tabs-inner` > `.decade-tab[data-tab]`),
 * renders ONLY tabs that have data, and its connect state is a full
 * `.lb-empty-state` card with an icon, copy, a settings button and a help link.
 */

// ── Last.fm Radio ────────────────────────────────────────────────────────────

export interface LastfmRadioSectionProps {
  query: string;
  results: LastfmTrackResult[];
  dropdownOpen: boolean;
  /** The dropdown shows a mini spinner while the request is in flight (3254). */
  searching?: boolean;
  mixes: DiscoverMix[];
  loaded: boolean;
  generating?: boolean;
  onQueryChange: (query: string) => void;
  onPick: (track: LastfmTrackResult) => void;
  onClear: () => void;
  onDismiss?: () => void;
  onOpenMix: (key: string) => void;
  onPlayMix?: (key: string) => void;
  /** which mix key is currently resolving against the library, if any. */
  playingKey?: string | null;
}

export function LastfmRadioSection({
  query,
  results,
  dropdownOpen,
  searching,
  mixes,
  loaded,
  generating,
  onQueryChange,
  onPick,
  onClear,
  onDismiss,
  onOpenMix,
  onPlayMix,
  playingKey,
}: LastfmRadioSectionProps) {
  // the vanilla closed the dropdown on any outside click (3387-3394); the
  // port only had Escape, leaving an open panel floating over the page
  const wrapRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!dropdownOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node))
        (onDismiss ?? onClear)();
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [dropdownOpen, onClear, onDismiss]);

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
          <div className="lastfm-radio-input-wrap" ref={wrapRef}>
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
                {searching ? (
                  <div className="lastfm-radio-searching">
                    <div
                      className="server-search-spinner"
                      style={{ width: 14, height: 14, margin: '0 auto' }}
                    />
                  </div>
                ) : (
                  // index in the key: last.fm returns the same name+artist
                  // for different releases, and duplicate keys shuffle rows
                  results.map((track, index) => (
                    <LastfmResultRow
                      key={`${index}:${track.name ?? ''}:${track.artist ?? ''}`}
                      track={track}
                      onPick={onPick}
                    />
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {generating ? (
        // the vanilla's spinner block (style.css has carried its classes all
        // along); without it a pick looked like nothing happening for up to
        // ten seconds of last.fm round trip
        <div className="lastfm-radio-generating">
          <div className="server-search-spinner" style={{ width: 16, height: 16 }} />
          <span>Building your radio…</span>
        </div>
      ) : null}
      <div id="lastfm-radio-playlists" className="discover-mixes-grid">
        {mixes.map((mix) => (
          <DiscoverMixCard
            key={mix.key}
            mix={mix}
            onOpen={onOpenMix}
            onPlay={onPlayMix}
            playing={playingKey === mix.key}
          />
        ))}
      </div>
    </DiscoverSection>
  );
}

/**
 * One search hit (3264-3279): artwork, then a meta column of track name and
 * "artist · N listeners". The vanilla computes a separate "Nk listeners" span
 * and never interpolates it — the module records it as dead, and it stays dead
 * here.
 */
function LastfmResultRow({
  track,
  onPick,
}: {
  track: LastfmTrackResult;
  onPick: (track: LastfmTrackResult) => void;
}) {
  const [broken, setBroken] = useState(false);
  return (
    <button type="button" className="lastfm-radio-result" onClick={() => onPick(track)}>
      <div className="lastfm-radio-result-art">
        {track.image_url && !broken ? (
          <img src={track.image_url} alt="" loading="lazy" onError={() => setBroken(true)} />
        ) : (
          <div className="lastfm-radio-art-empty" />
        )}
      </div>
      <div className="lastfm-radio-result-meta">
        <span className="lastfm-radio-result-track">{track.name}</span>
        <span className="lastfm-radio-result-artist">{resultSubtitle(track)}</span>
      </div>
    </button>
  );
}

// ── ListenBrainz ─────────────────────────────────────────────────────────────

export interface ListenBrainzSectionProps {
  username: string | null;
  activeTab: LbTabId;
  /** Which tabs returned playlists — a tab with none is NOT rendered (3462). */
  hasData: Record<string, boolean>;
  mixes: DiscoverMix[];
  loading?: boolean;
  /** The whole tab load failed, which is not the same as "connect". */
  error?: boolean;
  loaded: boolean;
  /** Sub-tab groups WITH their playlist counts — the label reads "Name (N)". */
  groups?: { name: string; count: number }[];
  activeGroup?: string | null;
  onSelectTab: (tab: LbTabId) => void;
  onSelectGroup: (group: string) => void;
  onRefresh: () => void;
  /** The connect card's button opens the personal settings (3486). */
  onConnect: () => void;
  onOpenMix: (key: string) => void;
  onPlayMix?: (key: string) => void;
  /** which mix key is currently resolving against the library, if any. */
  playingKey?: string | null;
}

export function ListenBrainzSection({
  username,
  activeTab,
  hasData,
  mixes,
  loading,
  error,
  loaded,
  groups,
  activeGroup,
  onSelectTab,
  onSelectGroup,
  onRefresh,
  onConnect,
  onOpenMix,
  onPlayMix,
  playingKey,
}: ListenBrainzSectionProps) {
  const liveTabs = LB_TABS.filter((tab) => hasData[tab.id]);
  const anyData = liveTabs.length > 0;

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
        ) : error ? (
          <div className="discover-empty">
            <p>{LB_LOAD_FAILED}</p>
          </div>
        ) : !anyData ? (
          /*
            The connect card (3479-3489). Not a one-liner: it says WHY, links the
            settings, and points at where the token lives. "No playlists" and
            "not connected" are different problems with different fixes.
          */
          <div className="lb-empty-state">
            <div className="lb-empty-icon">🧠</div>
            <h3>Connect ListenBrainz</h3>
            <p>
              Link your ListenBrainz account to see personalized playlists, recommendations, and
              collaborative playlists.
            </p>
            <button
              type="button"
              className="action-button primary lb-connect-btn"
              onClick={onConnect}
            >
              Connect ListenBrainz
            </button>
            <p className="lb-empty-help">
              Get your token from{' '}
              <a href="https://listenbrainz.org/profile/" target="_blank" rel="noreferrer">
                listenbrainz.org/profile
              </a>
            </p>
          </div>
        ) : (
          /* The DECADE tab styling, reused on purpose (3461) — and only tabs
             WITH data exist. Hiding rather than disabling is the vanilla's
             behaviour; a dead tab is simply not offered. */
          <div className="decade-tabs-inner">
            {liveTabs.map((tab) => (
              <button
                type="button"
                key={tab.id}
                className={tab.id === activeTab ? 'decade-tab active' : 'decade-tab'}
                data-tab={tab.id}
                onClick={() => onSelectTab(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="listenbrainz-tab-content" id="listenbrainz-tab-content">
        {anyData && !loading && !error && (
          <>
            {groups && groups.length > 0 && (
              <div className="decade-tabs-inner" id="lb-subtabs-bar" style={{ marginBottom: 16 }}>
                {groups.map((group) => (
                  <button
                    type="button"
                    key={group.name}
                    className={
                      group.name === activeGroup
                        ? 'decade-tab lb-subtab active'
                        : 'decade-tab lb-subtab'
                    }
                    data-group={group.name}
                    onClick={() => onSelectGroup(group.name)}
                  >
                    {/* The count rides in the label (3710) — "Weekly Jams (4)". */}
                    {group.name} ({group.count})
                  </button>
                ))}
              </div>
            )}
            {/* A plain .discover-grid of mix cards (3634) — no bespoke grid. */}
            <div className="discover-grid">
              {mixes.map((mix) => (
                <DiscoverMixCard
                  key={mix.key}
                  mix={mix}
                  onOpen={onOpenMix}
                  onPlay={onPlayMix}
                  playing={playingKey === mix.key}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </DiscoverSection>
  );
}
