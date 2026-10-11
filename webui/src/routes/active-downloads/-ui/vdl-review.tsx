/**
 * The video downloads Review pane: the browsable recycle bin (restore /
 * purge) + the release blocklist inline.
 *
 * A React port of the review half of
 * webui/static/video/video-downloads-tabs.js, reusing the same .adl-* markup
 * vocabulary so the existing CSS applies unchanged.
 */

import { useCallback, useState } from 'react';

import type { VdlBlockEntry, VdlRecycleBin, VdlReviewSub } from '../-vdl.types';

import {
  clearVideoBlocklist,
  emptyVideoRecycle,
  fetchVideoBlocklist,
  fetchVideoRecycle,
  purgeVideoRecycleEntry,
  restoreAllVideoRecycle,
  restoreVideoRecycleEntry,
  unblockVideoRelease,
} from '../-vdl.api';
import {
  vdlBinDisplayName,
  vdlBlockSxe,
  vdlBlockWhat,
  vdlFmtAge,
  vdlFmtBytes,
} from '../-vdl.helpers';

const toast = (message: string, type: string) => window.showToast?.(message, type);

/**
 * The review sub-tab (recycle / blocklist) outlives unmounts — module scope,
 * like the old tabs.js `_sub`, so switching Music|Video and back keeps it.
 */
const vdlReviewSubStore = { sub: 'recycle' as VdlReviewSub };

async function confirmed(options: {
  title: string;
  message: string;
  confirmText: string;
}): Promise<boolean> {
  if (typeof window.showConfirmDialog === 'function') {
    return Boolean(await window.showConfirmDialog({ ...options, destructive: true }));
  }
  return window.confirm(options.message);
}

export interface VdlReviewController {
  bin: VdlRecycleBin | null;
  binCount: number | null;
  binError: string | null;
  blocklist: VdlBlockEntry[];
  /** The cheap count for the header's Review badge — no full pane load. */
  loadBadge: () => Promise<void>;
  /** The full pane data, loaded when the Review view opens. */
  loadReview: () => Promise<void>;
}

export function useVdlReview(): VdlReviewController {
  const [bin, setBin] = useState<VdlRecycleBin | null>(null);
  const [binError, setBinError] = useState<string | null>(null);
  const [blocklist, setBlocklist] = useState<VdlBlockEntry[]>([]);

  const loadBadge = useCallback(async () => {
    const data = await fetchVideoRecycle();
    if (data) {
      setBin(data);
      setBinError(null);
    }
  }, []);

  const loadReview = useCallback(async () => {
    const [recycle, blk] = await Promise.all([fetchVideoRecycle(), fetchVideoBlocklist()]);
    if (recycle) {
      setBin(recycle);
      setBinError(null);
    } else {
      setBinError('could not load the recycle bin');
    }
    setBlocklist(blk);
  }, []);

  return {
    bin,
    binCount: bin ? bin.items.length : null,
    binError,
    blocklist,
    loadBadge,
    loadReview,
  };
}

function RecycleRows({
  bin,
  onRestore,
  onPurge,
}: {
  bin: VdlRecycleBin;
  onRestore: (trashDir: string | null | undefined, name: string) => void;
  onPurge: (trashDir: string | null | undefined, name: string) => void;
}) {
  const items = bin.items || [];

  return (
    <>
      <div className="adl-review-explainer">
        files removed by upgrades, retention and dismissed imports land here instead of dying
        {bin.recycle_enabled === false
          ? ' — recycling is currently OFF in organization settings, deletes are permanent'
          : ''}
        . restore puts a file back exactly where it was; entries older than the tracker restore into
        _restored for a rescan. auto-purge after {bin.keep_days ?? 7} days.
      </div>
      {items.length === 0 ? (
        <div className="adl-empty">the bin is empty</div>
      ) : (
        <div className="adl-list">
          {items.map((e, i) => (
            <div className="adl-row adl-row-completed" key={`${e.trash_dir}:${e.name}:${i}`}>
              <div className="adl-row-info">
                <div className="adl-row-title">{vdlBinDisplayName(e.name)}</div>
                <div className="adl-row-meta">
                  {e.original_path || 'original location unknown — restores into _restored'}
                </div>
                {e.reason ? <div className="adl-row-batch">{e.reason}</div> : null}
              </div>
              <span className="adl-quality-chip">{vdlFmtBytes(e.size) || '—'}</span>
              <span className="verif-time">{vdlFmtAge(e.age_seconds)}</span>
              <button
                type="button"
                className="verif-act verif-act-ok verif-act-labeled"
                title="Put this file back"
                onClick={() => onRestore(e.trash_dir, e.name)}
              >
                ↩<span className="verif-act-label">Restore</span>
              </button>
              <button
                type="button"
                className="verif-act verif-act-del verif-act-labeled"
                title="Delete permanently"
                onClick={() => onPurge(e.trash_dir, e.name)}
              >
                🗑<span className="verif-act-label">Delete</span>
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function BlocklistRows({
  blocklist,
  onUnblock,
}: {
  blocklist: VdlBlockEntry[];
  onUnblock: (id: number | string) => void;
}) {
  return (
    <>
      <div className="adl-review-explainer">
        exact releases (and blocked uploaders) the search will never pick again. remove one to make
        it eligible.
      </div>
      {blocklist.length === 0 ? (
        <div className="adl-empty">nothing blocked</div>
      ) : (
        <div className="adl-list">
          {blocklist.map((b) => {
            const what = vdlBlockWhat(b);
            const sxe = vdlBlockSxe(b);
            return (
              <div className="adl-row adl-row-failed" key={b.id}>
                <div className="adl-row-info">
                  <div className="adl-row-title">
                    {what}
                    {sxe ? ` ${sxe}` : ''}
                    {!b.filename ? (
                      <span
                        className="adl-quality-chip"
                        title="Every file from this uploader is skipped"
                      >
                        whole uploader
                      </span>
                    ) : null}
                  </div>
                  <div className="adl-row-meta">
                    {[b.title, b.username].filter(Boolean).join(' · ')}
                  </div>
                  {b.reason ? <div className="adl-row-error">{b.reason}</div> : null}
                </div>
                <button
                  type="button"
                  className="verif-act verif-act-labeled"
                  title="Allow this release again"
                  onClick={() => onUnblock(b.id)}
                >
                  ✕<span className="verif-act-label">Unblock</span>
                </button>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

export function VdlReviewPane({
  review,
  onChanged,
}: {
  review: VdlReviewController;
  onChanged: () => void;
}) {
  const [sub, setSubState] = useState<VdlReviewSub>(vdlReviewSubStore.sub);
  const setSub = useCallback((s: VdlReviewSub) => {
    vdlReviewSubStore.sub = s;
    setSubState(s);
  }, []);
  const binN = review.bin?.items.length ?? 0;

  const onRestore = async (trashDir: string | null | undefined, name: string) => {
    const r = await restoreVideoRecycleEntry(trashDir, name);
    const ok = Boolean(r && (r.success ?? r.ok));
    const dest = typeof r?.restored_to === 'string' ? r.restored_to : 'library';
    toast(
      ok ? `Restored to ${dest}` : String(r?.error || 'Restore failed'),
      ok ? 'success' : 'error',
    );
    onChanged();
  };

  const onPurge = async (trashDir: string | null | undefined, name: string) => {
    const go = await confirmed({
      title: 'Delete permanently',
      message: `Delete "${name}" from the bin for good?`,
      confirmText: 'Delete',
    });
    if (!go) return;
    const r = await purgeVideoRecycleEntry(trashDir, name);
    const ok = Boolean(r && (r.success ?? r.ok));
    toast(ok ? 'Deleted' : String(r?.error || 'Delete failed'), ok ? 'info' : 'error');
    onChanged();
  };

  const onRestoreAll = async () => {
    const r = await restoreAllVideoRecycle();
    const n = Number(r?.restored ?? 0);
    const failed = Number(r?.failed ?? 0);
    toast(
      r ? `Restored ${n} file(s)${failed ? `, ${failed} failed` : ''}` : 'Restore failed',
      'success',
    );
    onChanged();
  };

  const onEmpty = async () => {
    const go = await confirmed({
      title: 'Empty bin',
      message: 'Permanently delete everything in the recycle bin?',
      confirmText: 'Empty bin',
    });
    if (!go) return;
    const r = await emptyVideoRecycle();
    toast(r ? `Deleted ${Number(r.purged ?? 0)} file(s)` : 'Failed', 'info');
    onChanged();
  };

  const onUnblock = async (id: number | string) => {
    await unblockVideoRelease(id);
    onChanged();
  };

  const onClearBlocklist = async () => {
    const go = await confirmed({
      title: 'Clear blocklist',
      message: 'Every blocked release becomes pickable again. Clear it?',
      confirmText: 'Clear',
    });
    if (!go) return;
    await clearVideoBlocklist();
    onChanged();
  };

  return (
    <div data-vdl-pane="review">
      <div className="adl-batch-filter-banner">
        <button
          type="button"
          className={`adl-pill${sub === 'recycle' ? ' active' : ''}`}
          onClick={() => setSub('recycle')}
        >
          🗑 Recycle bin ({binN})
        </button>
        <button
          type="button"
          className={`adl-pill${sub === 'blocklist' ? ' active' : ''}`}
          onClick={() => setSub('blocklist')}
        >
          🚫 Blocklist ({review.blocklist.length})
        </button>
        <span className="verif-banner-spacer" />
        {sub === 'recycle' && binN > 0 ? (
          <>
            <button
              type="button"
              className="adl-filter-banner-clear"
              onClick={() => void onRestoreAll()}
            >
              ↩ Restore all
            </button>
            <button
              type="button"
              className="adl-filter-banner-clear verif-bulk-danger"
              onClick={() => void onEmpty()}
            >
              🗑 Empty bin
            </button>
          </>
        ) : null}
        {sub === 'blocklist' && review.blocklist.length > 0 ? (
          <button
            type="button"
            className="adl-filter-banner-clear verif-bulk-danger"
            onClick={() => void onClearBlocklist()}
          >
            Clear all
          </button>
        ) : null}
      </div>

      {review.binError && sub === 'recycle' ? (
        <div className="adl-client-empty adl-client-empty-error">
          couldn&apos;t load the recycle bin — {review.binError}
        </div>
      ) : null}

      {sub === 'recycle' ? (
        review.bin ? (
          <RecycleRows
            bin={review.bin}
            onRestore={(t, n) => void onRestore(t, n)}
            onPurge={(t, n) => void onPurge(t, n)}
          />
        ) : (
          <div className="adl-section-header">Loading…</div>
        )
      ) : (
        <BlocklistRows blocklist={review.blocklist} onUnblock={(id) => void onUnblock(id)} />
      )}
    </div>
  );
}
