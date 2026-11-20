/**
 * The Discovery tab (settings-bic DESIGN.md 3.1, 3.3): Recommendations,
 * Playlists and Import Lists (the video side's own), one pill each, each
 * read back in a hero sentence. Playlists holds every playlist and M3U
 * setting, the three M3U path options from Library included.
 *
 * Every pill stays mounted while another is shown, as on the other rebuilt
 * tabs. The import lists load when the video settings page is shown; on the
 * music side, opening Import Lists loads them, as Quality's video pills do.
 */

import { useEffect, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../../host';

import { useNav } from '../../nav';
import s from '../../ui/settings.module.css';
import {
  Playlists,
  PlaylistsHero,
  Recommendations,
  RecommendationsHero,
  usePool,
} from '../discovery-tab';
import { ViewPanel } from '../downloads/downloads-page';
import { LibHero } from '../library-card';
import { ImportListsCard, useImportListLines } from '../video-row-lists';
import { markVideoShown, useVideoShown } from '../video-shown';
import { importListsSentence } from './words';

const TAB = 'discovery';

function useSide(): 'music' | 'video' {
  return useSyncExternalStore(
    (listener) => {
      const observer = new MutationObserver(listener);
      observer.observe(document.body, { attributes: true, attributeFilter: ['data-side'] });
      return () => observer.disconnect();
    },
    () => (document.body.getAttribute('data-side') === 'video' ? 'video' : 'music'),
    () => 'music',
  );
}

function ImportListsHero() {
  const lines = useImportListLines();
  const on = lines ? lines.filter((l) => l.enabled).length : 0;
  return (
    <LibHero
      parts={importListsSentence(lines)}
      stats={
        lines
          ? [
              { value: lines.length, label: lines.length === 1 ? 'List' : 'Lists' },
              { value: on, label: 'Switched on' },
            ]
          : undefined
      }
    />
  );
}

export function DiscoveryPage({ store }: SettingsTabProps) {
  const state = useNav();
  const side = useSide();
  const here = state.tab === TAB;
  const view = here ? state.view : 'recommendations';
  const pool = usePool(store);
  const shown = useVideoShown();
  const lists = here && view === 'import-lists';

  // the music side never showed the video settings page: load the lists the
  // first time their pill opens
  useEffect(() => {
    if (lists && side !== 'video' && shown === 0) markVideoShown();
  }, [lists, side, shown]);

  return (
    <div data-stg-new={TAB} hidden={!here}>
      <ViewPanel tab={TAB} view="recommendations" open={view === 'recommendations'}>
        <RecommendationsHero store={store} pool={pool} side={side} />
        <div className={s.cards}>
          <Recommendations store={store} pool={pool} />
        </div>
      </ViewPanel>
      <ViewPanel tab={TAB} view="playlists" open={view === 'playlists'}>
        <PlaylistsHero store={store} />
        <div className={s.cards}>
          <Playlists store={store} />
        </div>
      </ViewPanel>
      <ViewPanel tab={TAB} view="import-lists" open={view === 'import-lists'}>
        <ImportListsHero />
        <div className={s.cards}>
          <ImportListsCard />
        </div>
      </ViewPanel>
    </div>
  );
}

/** the Discovery tab: one page with its pills (settings-bic phase 5) */
export function DiscoveryTab(props: SettingsTabProps) {
  if (props.part === 'page') return <DiscoveryPage {...props} />;
  return null;
}
