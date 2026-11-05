/**
 * React's view of what this profile chose to see (#1909).
 *
 * The state lives in the shell bundle (src/shell/features.ts) as
 * window.SoulSyncFeatures, because the sidebar and the vanilla pages need it
 * too. Importing that module here would give React its own copy, so this
 * reads through window and listens for the change event.
 */

import { useSyncExternalStore } from 'react';

export const FEATURES_CHANGED_EVENT = 'ss:features-changed';

export type FeatureId =
  | 'discover'
  | 'playlists'
  | 'explorer'
  | 'import'
  | 'stats'
  | 'music_videos'
  | 'sample_studio'
  | 'podcasts'
  | 'audiobooks'
  | 'video'
  | 'video_discover'
  | 'video_calendar'
  | 'chat'
  | 'automations'
  | 'tools';

function subscribe(onChange: () => void): () => void {
  window.addEventListener(FEATURES_CHANGED_EVENT, onChange);
  return () => window.removeEventListener(FEATURES_CHANGED_EVENT, onChange);
}

/** no shell (tests, a page opened on its own) means everything is on */
export function isFeatureOn(id: FeatureId): boolean {
  return window.SoulSyncFeatures?.isOn(id) ?? true;
}

export function useFeatureOn(id: FeatureId): boolean {
  return useSyncExternalStore(
    subscribe,
    () => isFeatureOn(id),
    () => true,
  );
}

export function openFeaturePicker(): void {
  window.openFeaturePicker?.();
}
