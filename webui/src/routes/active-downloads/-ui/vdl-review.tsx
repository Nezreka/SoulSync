/**
 * The video downloads Review pane: the browsable recycle bin (restore /
 * purge) + the release blocklist inline, on the same rows as the music
 * Review view (dl-parts).
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
import { BinStrip } from './adl-review';
import { DlButton, DlFileBadge, DlGlyph, DlMore, DlSegmented, fileExt } from './dl-parts';

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

/** "MKV" from a bin entry's name. */
function binExt(name: string): string {
  return fileExt(vdlBinDisplayName(name), name);
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
  const total = items.reduce((n, e) => n + (Number(e.size) || 0), 0);
  return (
    <>
      <BinStrip
        size={vdlFmtBytes(total) || '0 B'}
        count={items.length}
        line={`Files removed by upgrades, retention and dismissed imports land here first. Restore puts one back where it was; anything older than the tracker restores into _restored. Kept for ${bin.keep_days ?? 7} days.`}
        warn={
          bin.recycle_enabled === false
            ? 'Recycling is off in organization settings, so deletes are permanent right now.'
            : undefined
        }
      />
      {items.length === 0 ? (
        <div className="dlc-empty-line">The bin is empty.</div>
      ) : (
        <div className="dlc-list">
          {items.map((e, i) => (
            <div className="dlc-row dlc-row-completed" key={`${e.trash_dir}:${e.name}:${i}`}>
              <DlFileBadge ext={binExt(e.name)} />
              <div className="dlc-main">
                <div className="dlc-title">{vdlBinDisplayName(e.name)}</div>
                <div className="dlc-sub dlc-mono">
                  {e.original_path || 'original location unknown, restores into _restored'}
                </div>
              </div>
              <div className="dlc-meta">
                {e.reason ? <span className="dlc-tag">{e.reason}</span> : null}
                <span>{vdlFmtBytes(e.size) || '—'}</span>
                <span className="dlc-when">{vdlFmtAge(e.age_seconds)}</span>
              </div>
              <div className="dlc-acts">
                <DlButton
                  primary
                  title="Put this file back"
                  onClick={() => onRestore(e.trash_dir, e.name)}
                >
                  Restore
                </DlButton>
                <DlMore
                  items={[
                    {
                      key: 'purge',
                      label: 'Delete forever',
                      danger: true,
                      onSelect: () => onPurge(e.trash_dir, e.name),
                    },
                  ]}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

const BlockIcon = (
  <svg
    width="18"
    height="18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    aria-hidden="true"
  >
    <circle cx="12" cy="12" r="9" />
    <line x1="5.6" y1="5.6" x2="18.4" y2="18.4" />
  </svg>
);
const UploaderBlockIcon = (
  <svg
    width="18"
    height="18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    aria-hidden="true"
  >
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21c1-4 4.5-6 8-6s7 2 8 6" />
    <line x1="3" y1="3" x2="21" y2="21" />
  </svg>
);

function BlocklistRows({
  blocklist,
  onUnblock,
}: {
  blocklist: VdlBlockEntry[];
  onUnblock: (id: number | string) => void;
}) {
  return (
    <>
      <p className="dlc-explain">
        <b>Releases and uploaders the search will never pick again.</b> Unblock one to make it
        eligible.
      </p>
      {blocklist.length === 0 ? (
        <div className="dlc-empty-line">Nothing blocked.</div>
      ) : (
        <div className="dlc-list">
          {blocklist.map((b) => {
            const what = vdlBlockWhat(b);
            const sxe = vdlBlockSxe(b);
            const uploader = !b.filename;
            return (
              <div className="dlc-row dlc-row-failed" key={b.id}>
                <DlGlyph tone="bad">{uploader ? UploaderBlockIcon : BlockIcon}</DlGlyph>
                <div className="dlc-main">
                  <div className={`dlc-title${uploader ? '' : ' dlc-mono dlc-title-mono'}`}>
                    {what}
                    {sxe ? ` ${sxe}` : ''}
                  </div>
                  <div className="dlc-sub">
                    {[
                      b.title,
                      uploader ? 'every file from this uploader is skipped' : b.username,
                      b.reason,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </div>
                </div>
                <div className="dlc-meta">
                  <span
                    className={`dlc-tag${uploader ? ' dlc-tag-bad' : ''}`}
                    title={
                      uploader ? 'Every file from this uploader is skipped' : 'This exact release'
                    }
                  >
                    {uploader ? 'Whole uploader' : 'Release'}
                  </span>
                </div>
                <div className="dlc-acts">
                  <DlButton title="Allow this release again" onClick={() => onUnblock(b.id)}>
                    Unblock
                  </DlButton>
                </div>
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

  const binMenu =
    sub === 'recycle' && binN > 0
      ? [
          { key: 'restore', label: `Restore all (${binN})`, onSelect: () => void onRestoreAll() },
          { key: 'empty', label: 'Empty bin', danger: true, onSelect: () => void onEmpty() },
        ]
      : sub === 'blocklist' && review.blocklist.length > 0
        ? [
            {
              key: 'clear',
              label: 'Clear blocklist',
              danger: true,
              onSelect: () => void onClearBlocklist(),
            },
          ]
        : [];

  return (
    <div data-vdl-pane="review" className="dlc-stack">
      <div className="dlc-subbar">
        <DlSegmented
          label="Review"
          value={sub}
          onChange={(v) => setSub(v as VdlReviewSub)}
          segments={[
            { value: 'recycle', label: 'Recycle bin', count: binN },
            { value: 'blocklist', label: 'Blocklist', count: review.blocklist.length },
          ]}
        />
        <div className="dlc-subbar-side">
          <span className="dlh-more">
            <DlMore items={binMenu} label="Review actions" />
          </span>
        </div>
      </div>

      {review.binError && sub === 'recycle' ? (
        <div className="dlc-empty-line dlc-empty-error">
          Couldn&apos;t load the recycle bin: {review.binError}
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
          <div className="dlc-loading">Loading…</div>
        )
      ) : (
        <BlocklistRows blocklist={review.blocklist} onUnblock={(id) => void onUnblock(id)} />
      )}
    </div>
  );
}
