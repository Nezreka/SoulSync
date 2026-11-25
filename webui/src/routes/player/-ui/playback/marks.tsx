/**
 * marks while it plays: "mark this moment" (the bookmark button, or b)
 * keeps where you are with a note if you like, yours alone unless you share
 * it. every mark you may see sits on the seek bar as a pin.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import type { PlayItem } from '../../-engine/types';
import type { SeekMark } from './seek-bar';

import { clock } from '../../-engine/timeline';
import { noteEdits, notesQuery } from '../../-player.api';
import { MarkIcon } from './icons';
import s from './playback.module.css';
import { getEngine } from './use-player';

/** the kinds a mark can sit on */
const MARKABLE = new Set(['movie', 'episode', 'track', 'audiobook', 'youtube']);

export function useMarks(item: PlayItem | null): SeekMark[] {
  const ok = !!item && MARKABLE.has(item.type);
  const notes = useQuery({ ...notesQuery(item?.type ?? '', item?.id ?? ''), enabled: ok });
  if (!ok) return [];
  return (notes.data ?? [])
    .filter((n) => n.at !== null)
    .map((n) => ({
      id: n.id,
      at: n.at!,
      label: n.body,
      who: n.mine ? (n.shared ? 'You, shared' : 'You') : n.profile.name,
      mine: n.mine,
    }));
}

export function MarkButton({ item, className }: { item: PlayItem; className?: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState<number | null>(null);
  const [body, setBody] = useState('');
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const start = () => {
    setOpen(getEngine().getState().position);
    setBody('');
    setDone(null);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'b' || (e.target as HTMLElement | null)?.closest('input, textarea, select'))
        return;
      if (!MARKABLE.has(item.type)) return;
      e.preventDefault();
      start();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [item.type]);

  useEffect(() => {
    if (open === null) return;
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(null);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  if (!MARKABLE.has(item.type)) return null;

  const save = async () => {
    if (open === null) return;
    setBusy(true);
    try {
      await noteEdits.add(item.type, item.id, { at: open, body: body.trim() || undefined, shared });
      await queryClient.invalidateQueries({ queryKey: ['player', 'notes', item.type, item.id] });
      setOpen(null);
      setDone(`Marked ${clock(open)}`);
      window.setTimeout(() => setDone(null), 2200);
    } catch {
      setDone("Couldn't keep that mark");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={s.menuWrap} ref={box}>
      <button
        type="button"
        className={className ?? s.ctl}
        aria-label="Mark this moment"
        title="Mark this moment (b)"
        aria-expanded={open !== null}
        onClick={() => (open === null ? start() : setOpen(null))}
        data-nav
      >
        <MarkIcon />
      </button>
      {done ? (
        <span className={s.markDone} role="status">
          {done}
        </span>
      ) : null}
      {open !== null ? (
        <form
          className={s.markForm}
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.stopPropagation();
              setOpen(null);
            }
          }}
        >
          <span className={s.menuTitle}>Mark {clock(open)}</span>
          <input
            className={s.markInput}
            placeholder="A note (if you like)"
            value={body}
            maxLength={1000}
            autoFocus
            onChange={(e) => setBody(e.target.value)}
            aria-label="A note for this mark"
          />
          <label className={s.markShare}>
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
            Let others see it
          </label>
          <button type="submit" className={s.markSave} disabled={busy}>
            {busy ? 'Keeping…' : 'Keep this mark'}
          </button>
        </form>
      ) : null}
    </div>
  );
}
