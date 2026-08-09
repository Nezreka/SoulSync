/**
 * Mounts the source modals for all nine verticals from ONE table.
 *
 * Nine hand-written `<SourceModals config={SYNC_SOURCES.x} …>` blocks is the
 * shape kettui's fourth observation warns about — nine places to drift, and a
 * tenth source added to the table silently getting no modal at all. The
 * registry's `SYNC_VERTICAL_IDS` drives it instead, so a source cannot be in
 * the table and missing here.
 *
 * NOTHING RENDERED SourceModals BEFORE THIS. The tabs only ever signalled
 * `onOpen`; the component existed and was tested but had no production mount
 * site. This is it.
 *
 * `discoveryStartBody` is deliberately NOT passed. SourceModals already
 * derives the ListenBrainz `{playlist}` body from `config.discovery.startBody`
 * precisely so a wiring omission here cannot break the start invisibly — its
 * own comment says so. Passing it again would put the same decision in two
 * places, and the one here would be the copy that drifts.
 */

import type { SyncModals as SyncModalsState } from '../-sync.use-modals';
import type { SyncVerticals } from '../-sync.verticals';

import { SYNC_SOURCES } from '../-sync.sources';
import { SYNC_VERTICAL_IDS } from '../-sync.verticals';
import { SourceModals } from './source-modals';

export interface SyncModalsProps {
  verticals: SyncVerticals;
  modals: SyncModalsState;
  /** useStandalone() — one page-level signal, the same for every source. */
  standalone: boolean;
  /**
   * The mirrored row's source, for label capitalisation. Mirrored only: every
   * other vertical knows its own name from its config.
   */
  mirroredSource?: string;
}

export function SyncModals({ verticals, modals, standalone, mirroredSource }: SyncModalsProps) {
  return (
    <>
      {SYNC_VERTICAL_IDS.map((id) => (
        <SourceModals
          key={id}
          config={SYNC_SOURCES[id]}
          vertical={verticals[id]}
          openId={modals.openIdFor(id)}
          onClose={modals.close}
          standalone={standalone}
          mirroredSource={id === 'mirrored' ? mirroredSource : undefined}
        />
      ))}
    </>
  );
}
