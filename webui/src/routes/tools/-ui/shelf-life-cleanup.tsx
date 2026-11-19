/**
 * Shelf Life cleanup: pick the titles a space cleanup deletes.
 *
 * Opens on every open Shelf Life finding (or the ones picked in the list),
 * lowest score first. Type how much space you want back and it trims the list
 * to the fewest titles that reach it, lowest score first; it never reaches
 * above the threshold to make the number, it says how short it falls. Tick
 * titles off freely; the total follows. "Delete" hands the chosen ids back,
 * and the caller gates the batch on one typed phrase ('delete 14 titles').
 */
import clsx from 'clsx';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { DialogFrame } from '@/components/dialog';

import {
  fetchShelfLifePlan,
  formatLibrarySize,
  type ShelfLifePlan,
  type ShelfLifePlanItem,
} from '../-video-tools.api';
import styles from './finding-prompts.module.css';

export interface ShelfLifeCleanupProps {
  /** the findings picked in the list; null = every open Shelf Life finding */
  ids: number[] | null;
  /** the ids to delete, or null when cancelled */
  resolve: (ids: number[] | null) => void;
}

export function ShelfLifeCleanupDialog({ ids, resolve }: ShelfLifeCleanupProps) {
  const [plan, setPlan] = useState<ShelfLifePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState('');
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const targetRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(
    async (targetGb: number | null) => {
      setError(null);
      try {
        const next = await fetchShelfLifePlan({ targetGb, ids: ids ?? undefined });
        if (next.error) {
          setError(next.error);
          return;
        }
        setPlan(next);
        setChosen(new Set(next.items.map((item) => item.id)));
      } catch {
        setError("Couldn't load the Shelf Life list.");
      }
    },
    [ids],
  );

  useEffect(() => {
    void load(null);
  }, [load]);

  const picked: ShelfLifePlanItem[] = useMemo(
    () => (plan?.items || []).filter((item) => chosen.has(item.id)),
    [plan, chosen],
  );
  const total = picked.reduce((sum, item) => sum + (item.size_bytes || 0), 0);

  const toggle = (id: number) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const applyTarget = () => {
    const gb = Number(target);
    if (!target.trim()) void load(null);
    else if (Number.isFinite(gb) && gb > 0) void load(gb);
    else setError('Type how many GB you want back, e.g. 500.');
  };

  const freed = plan?.recycling
    ? `goes to the recycle bin and comes back as free space after ${plan.keep_days} day${
        plan.keep_days === 1 ? '' : 's'
      } (or when you empty the bin)`
    : 'is deleted for good (the recycle bin is off)';

  return (
    <DialogFrame
      open
      onOpenChange={(next) => {
        if (!next) resolve(null);
      }}
      viewportClassName={clsx('confirm-modal-overlay', styles.witnessOverlay)}
      className={styles.witnessBox}
      initialFocus={targetRef}
      aria-label="Free up space with Shelf Life"
    >
      <h3 style={{ margin: '0 0 8px', fontSize: '1.2em' }}>Free up space</h3>
      <p style={{ margin: '0 0 12px', fontSize: '0.9em', opacity: 0.85 }}>
        Lowest Shelf Life first. Each title is deleted the way Delete Title does it, and hidden from
        Discover.
      </p>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', margin: '0 0 12px' }}>
        <input
          id="shelf-life-target"
          ref={targetRef}
          type="number"
          min={1}
          inputMode="decimal"
          placeholder="GB to free"
          value={target}
          onChange={(event) => setTarget(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') applyTarget();
          }}
          style={{
            flex: 1,
            padding: '8px',
            border: '1px solid var(--white-a30)',
            borderRadius: '6px',
            background: 'var(--bg-primary)',
            color: 'var(--text-primary)',
          }}
        />
        <button
          id="shelf-life-plan"
          type="button"
          className="repair-inbox-btn"
          onClick={applyTarget}
        >
          {target.trim() ? 'Plan' : 'Show all'}
        </button>
      </div>
      {error ? (
        <p role="alert" style={{ color: 'var(--danger)', margin: '0 0 8px' }}>
          {error}
        </p>
      ) : null}
      {plan ? (
        <>
          {plan.items.length ? (
            <ul
              id="shelf-life-items"
              style={{
                listStyle: 'none',
                margin: 0,
                padding: 0,
                maxHeight: '40vh',
                overflowY: 'auto',
              }}
            >
              {plan.items.map((item) => (
                <li key={item.id} style={{ padding: '4px 0' }}>
                  <label
                    style={{ display: 'flex', gap: '8px', alignItems: 'center', cursor: 'pointer' }}
                  >
                    <input
                      type="checkbox"
                      checked={chosen.has(item.id)}
                      onChange={() => toggle(item.id)}
                    />
                    <span style={{ flex: 1 }}>
                      {item.title}
                      {item.year ? ` (${item.year})` : ''}
                    </span>
                    <span style={{ opacity: 0.7 }}>{item.score ?? '?'}</span>
                    <span style={{ opacity: 0.7, minWidth: '6em', textAlign: 'right' }}>
                      {formatLibrarySize(item.size_bytes)}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          ) : (
            <p>
              No open Shelf Life findings. Run the job first; it only offers titles below the line.
            </p>
          )}
          <p id="shelf-life-total" style={{ margin: '12px 0 4px', fontWeight: 600 }}>
            {picked.length.toLocaleString()} title{picked.length === 1 ? '' : 's'},{' '}
            {formatLibrarySize(total)}
          </p>
          {plan.target_bytes && plan.short_bytes > 0 ? (
            <p id="shelf-life-short" style={{ margin: '0 0 4px', fontSize: '0.9em' }}>
              {formatLibrarySize(plan.short_bytes)} short of {formatLibrarySize(plan.target_bytes)}:
              that is everything below the line. Raise the threshold in the job's settings to reach
              further.
            </p>
          ) : null}
          <p style={{ margin: '0 0 16px', fontSize: '0.85em', opacity: 0.8 }}>The space {freed}.</p>
        </>
      ) : null}
      <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
        <button
          id="shelf-life-cancel"
          type="button"
          className="repair-inbox-btn ghost"
          onClick={() => resolve(null)}
        >
          Cancel
        </button>
        <button
          id="shelf-life-delete"
          type="button"
          className="repair-inbox-btn destructive"
          disabled={!picked.length}
          onClick={() => resolve(picked.map((item) => item.id))}
        >
          Delete {picked.length.toLocaleString()} title{picked.length === 1 ? '' : 's'}…
        </button>
      </div>
    </DialogFrame>
  );
}
