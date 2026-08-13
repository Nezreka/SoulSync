/**
 * Which source modal is open.
 *
 * ONE AT A TIME, deliberately. The vanilla keeps a separate open-flag per tab,
 * but only one tab is ever active, so two can never be reached — the per-tab
 * state is an artefact of nine regions being written separately, not a
 * capability anyone uses. Holding nine independent ids here would preserve the
 * artefact AND add a way to stack two overlays that the vanilla cannot
 * produce: open Tidal's modal, switch tabs, open Qobuz's, and both are
 * mounted. A single slot cannot express that.
 *
 * The four `onOpen` shapes the tabs use — `(sourceId)`, `(sourceId, playlist)`,
 * `(card)` and the server tab's `(playlist, mirrored)` — are NOT unified here.
 * Only the first two open a source modal; the LB/Last.fm card shape is turned
 * into a source id by its own tab, and the server tab's is a view switch, not
 * a modal at all. Flattening them into one handler is how a modal ends up
 * opening on the wrong argument.
 */

import { useCallback, useMemo, useState } from 'react';

import type { SyncSourceId } from './-sync.sources';

export interface SyncModalState {
  source: SyncSourceId;
  /** The source's OWN id — numeric, url_hash, mbid or `mirrored_<n>`. */
  sourceId: string;
}

export interface SyncModals {
  open: SyncModalState | null;
  openModal: (source: SyncSourceId, sourceId: string) => void;
  close: () => void;
  /** What `SourceModals` wants: this source's open id, or null. */
  openIdFor: (source: SyncSourceId) => string | null;
}

export function useSyncModals(): SyncModals {
  const [open, setOpen] = useState<SyncModalState | null>(null);

  const openModal = useCallback((source: SyncSourceId, sourceId: string) => {
    setOpen({ source, sourceId });
  }, []);

  const close = useCallback(() => {
    setOpen(null);
  }, []);

  const openIdFor = useCallback(
    (source: SyncSourceId) => (open?.source === source ? open.sourceId : null),
    [open],
  );

  return useMemo(
    () => ({ open, openModal, close, openIdFor }),
    [open, openModal, close, openIdFor],
  );
}
