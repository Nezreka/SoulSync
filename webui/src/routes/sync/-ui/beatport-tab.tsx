/**
 * The Beatport tab — index.html 2395-3085, and the load/teardown pair
 * `ensureBeatportContentLoaded` (sync-spotify.js 23) and
 * `cleanupBeatportContent` (core.js 535).
 *
 * ONE PANE, not a sub-shell. The markup carries a `.beatport-tabs` strip with
 * three buttons (Browse / Browse Charts / My Playlists) and its own comment
 * says why it is `style="display: none"` — "Browse is the only view". Only
 * `#beatport-rebuild-content` has the `active` class, so the other two panes
 * (`#beatport-browse-content`, `#beatport-playlists-content`) are unreachable
 * markup. They are NOT ported: an inner tab strip nobody can click is an
 * artefact, and porting the panes behind it would be porting dead UI.
 *
 * THE LAZY LOAD AND THE TEARDOWN BOTH DISSOLVE. `ensureBeatportContentLoaded`
 * exists because the panes live in the DOM from page load, so "first time this
 * tab is opened" needed a flag and an AbortController; `cleanupBeatportContent`
 * then aborted the flight and tore down five sliders. Here each section is a
 * component that fetches on mount and aborts on unmount, and `SyncShell` only
 * mounts a panel once its tab is opened — so the flag, the abort plumbing and
 * the five cleanup calls have no counterpart. What survives is the ORDER, which
 * is this file's job.
 *
 * The download-bubble region stays an ADOPTED DIV. `registerBeatportDownload`
 * writes into `beatportDownloadBubbles`, a top-level `let` in core.js that no
 * module can reach, and globals.d.ts already records the consequence: the React
 * tab "must keep the section div for it to render into". So the div is rendered
 * empty and hidden, exactly as the markup has it, and the vanilla paints it.
 */

import { useCallback, useMemo, useRef, useState } from 'react';

import type { BeatportGenre } from '../-beatport.api';
import type { BeatportDownloadEnv } from '../-beatport.downloads';
import type { BeatportListEnv } from '../-beatport.lists';
import type { SourceVertical } from '../-sync.use-vertical';

import { defaultBeatportDownloadEnv } from '../-beatport.downloads';
import { openBeatportList } from '../-beatport.lists';
import {
  BeatportDJChartsSection,
  BeatportFeaturedChartsSection,
  BeatportHeroSection,
  BeatportHypePicksSection,
  BeatportNewReleasesSection,
} from './beatport-sections';
import { BeatportTop10Lists, BeatportTop10Releases } from './beatport-top10';
import { GenreBrowserModal } from './genre-browser-modal';
import { GenrePage } from './genre-page';

export interface BeatportTabProps {
  /** the beatport vertical: every chart-type list opens as its discovery */
  vertical?: SourceVertical;
  /** opens the discovery modal for a list ref */
  onOpen?: (sourceId: string) => void;
}

export function BeatportTab({ vertical, onOpen }: BeatportTabProps = {}) {
  /**
   * the vertical and the opener change every render; the env must not (every
   * section would get a new object each time), so the list opener reads them
   * through a ref.
   */
  const listEnvRef = useRef<BeatportListEnv | null>(null);
  listEnvRef.current =
    vertical && onOpen
      ? {
          showToast: (message, type) => window.showToast?.(message, type),
          showLoadingOverlay: (message) => window.showLoadingOverlay?.(message),
          hideLoadingOverlay: () => window.hideLoadingOverlay?.(),
          phaseOf: (id) => vertical.states[id]?.phase,
          seed: (id, playlist) => vertical.seed(id, playlist),
          open: onOpen,
          startDiscovery: (id, chart) => vertical.startDiscovery(id, chart),
        }
      : null;

  /**
   * ONE env for the whole tab. `defaultBeatportDownloadEnv()` builds a bag of
   * injected vanilla functions (toast, overlay, openDownloadModal) for the
   * release cards; `openList` sends every chart-type list to discovery.
   */
  const env = useMemo<BeatportDownloadEnv>(
    () => ({
      ...defaultBeatportDownloadEnv(),
      openList: (request) => {
        const listEnv = listEnvRef.current;
        if (!listEnv) {
          window.showToast?.('Beatport lists are not available here', 'error');
          return;
        }
        void openBeatportList(request, listEnv);
      },
    }),
    [],
  );

  const [browsingGenres, setBrowsingGenres] = useState(false);
  const [genre, setGenre] = useState<BeatportGenre | null>(null);

  // 2659-2680 / 2745-2760: Back returns to the grid, the close button and the
  // backdrop close the whole thing. Closing from inside a genre must therefore
  // clear the genre too, or reopening would land on the last one.
  const closeBrowser = useCallback(() => {
    setBrowsingGenres(false);
    setGenre(null);
  }, []);

  return (
    /**
     * `beatport-tab-content active` — BOTH classes, and the pair is the point.
     * `.beatport-tab-content` alone is `display: none` (style.css 16993); only
     * `.active` turns it into the `flex-direction: column` scroll container the
     * pane actually is (17000). The port emitted the id and neither class, so
     * the pane laid out as a plain block instead.
     *
     * The `active` is STATIC here, unlike in the vanilla where it moved between
     * three panes. Two of those panes are unreachable (see the header note) and
     * this component only ever renders the one, so a class that never changes
     * is the honest encoding — not state pretending to be state.
     */
    <div id="beatport-rebuild-content" className="beatport-tab-content active">
      <BeatportHeroSection env={env} />

      {/* Three buttons, and none of them switches an inner view: Browse by
          Genre opens the modal, and both Top 100s open as playlists to
          identify, then sync or download. */}
      <div className="beatport-nav-buttons-section">
        <div className="beatport-nav-buttons-container">
          <button
            type="button"
            className="beatport-nav-button"
            id="browse-by-genre-btn"
            onClick={() => setBrowsingGenres(true)}
          >
            <span className="beatport-nav-icon genre-icon" />
            <span className="beatport-nav-text">Browse by Genre</span>
          </button>
          <button
            type="button"
            className="beatport-nav-button"
            id="beatport-top100-btn"
            onClick={() => env.openList?.({ ref: 'top-100', label: 'Beatport Top 100' })}
          >
            <span className="beatport-nav-icon top100-icon" />
            <span className="beatport-nav-text">Beatport Top 100</span>
          </button>
          <button
            type="button"
            className="beatport-nav-button"
            id="hype-top100-btn"
            onClick={() => env.openList?.({ ref: 'hype-100', label: 'Hype Top 100' })}
          >
            <span className="beatport-nav-icon hype-icon" />
            <span className="beatport-nav-text">Hype Top 100</span>
          </button>
        </div>
      </div>

      {/* Adopted: the vanilla's bubble registry paints into this. Hidden by
          default exactly as the markup has it — showBeatportDownloadsSection
          reveals it when a download registers. */}
      <div
        id="beatport-downloads-section"
        className="artist-downloads-section"
        style={{ display: 'none' }}
      />

      {/* The order below is the markup's, and it is the whole point of this
          file: 2867 top-10 lists, 2915 top-10 releases, 2936 new releases,
          2973 hype picks, 3011 featured charts, 3048 DJ charts. */}
      <BeatportTop10Lists env={env} />
      <BeatportTop10Releases env={env} />
      <BeatportNewReleasesSection env={env} />
      <BeatportHypePicksSection env={env} />
      <BeatportFeaturedChartsSection env={env} />
      <BeatportDJChartsSection env={env} />

      <GenreBrowserModal
        open={browsingGenres}
        onClose={closeBrowser}
        onSelectGenre={setGenre}
        genreView={
          genre ? <GenrePage genre={genre} onBack={() => setGenre(null)} env={env} /> : undefined
        }
      />
    </div>
  );
}
