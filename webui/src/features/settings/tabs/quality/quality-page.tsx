/**
 * The Quality tab (settings-bic DESIGN.md 3.1, mockup 04): Music, Audiobooks,
 * Movies and TV and YouTube, one pill each. The video side shows its own two
 * first and opens on Movies and TV. Each pill shows only what it is for: the
 * music cards never sit on the video pill or the other way round.
 *
 * Every pill stays mounted while another is shown (as on Downloads), so a
 * profile being edited is still there when you come back. The video profiles
 * load when the video settings page is shown; on the music side, opening
 * Movies and TV or YouTube loads them, as the Services switch does.
 */

import { useEffect, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../../host';

import { useNav } from '../../nav';
import { ViewPanel } from '../downloads/downloads-page';
import { YoutubeFileOptions } from '../library-video-organization';
import { MusicQualityTab } from '../quality-tab';
import { VideoQualitySections, YoutubeQuality } from '../video-quality';
import { markVideoShown, useVideoShown } from '../video-shown';
import { AudiobookQualityView } from './audiobooks-view';

const TAB = 'quality';

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

export function QualityPage(props: SettingsTabProps) {
  const state = useNav();
  const side = useSide();
  const here = state.tab === TAB;
  const view = here ? state.view : side === 'video' ? 'video' : 'music';
  const shown = useVideoShown();
  const videoPill = here && (view === 'video' || view === 'youtube');

  // the music side never showed the video settings page: load the video
  // profiles the first time one of its pills opens
  useEffect(() => {
    if (videoPill && side !== 'video' && shown === 0) markVideoShown();
  }, [videoPill, side, shown]);

  return (
    <div data-stg-new={TAB} hidden={!here}>
      <ViewPanel tab={TAB} view="music" open={view === 'music'}>
        <MusicQualityTab {...props} />
      </ViewPanel>
      <ViewPanel tab={TAB} view="audiobooks" open={view === 'audiobooks'}>
        <AudiobookQualityView store={props.store} />
      </ViewPanel>
      <ViewPanel tab={TAB} view="video" open={view === 'video'}>
        <VideoQualitySections />
      </ViewPanel>
      <ViewPanel tab={TAB} view="youtube" open={view === 'youtube'}>
        <YoutubeQuality shown={shown} />
        {/* what a YouTube file contains (DESIGN 3.3: from Library) */}
        <YoutubeFileOptions />
      </ViewPanel>
    </div>
  );
}
