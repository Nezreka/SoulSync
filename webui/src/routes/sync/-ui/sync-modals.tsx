/**
 * The mirrored playlist's identify / sync windows.
 *
 * This used to mount one host per source vertical, ten of them, back when
 * each source tab identified and synced its own playlists. Every tab imports
 * into a mirror now, so the mirrored vertical is the only one with anything
 * to show.
 *
 * `discoveryStartBody` is deliberately NOT passed: SourceModals derives it
 * from the config, so a wiring omission here can't break the start.
 */

import type { SyncModals as SyncModalsState } from '../-sync.use-modals';
import type { SourceVertical } from '../-sync.use-vertical';

import { SYNC_SOURCES } from '../-sync.sources';
import { SourceModals } from './source-modals';

export interface SyncModalsProps {
  vertical: SourceVertical;
  modals: SyncModalsState;
  /** useStandalone() — one page-level signal. */
  standalone: boolean;
  /** The mirrored row's source, for label capitalisation. */
  mirroredSource?: string;
}

export function SyncModals({ vertical, modals, standalone, mirroredSource }: SyncModalsProps) {
  return (
    <SourceModals
      config={SYNC_SOURCES.mirrored}
      vertical={vertical}
      openId={modals.openIdFor('mirrored')}
      onClose={modals.close}
      standalone={standalone}
      mirroredSource={mirroredSource}
    />
  );
}
