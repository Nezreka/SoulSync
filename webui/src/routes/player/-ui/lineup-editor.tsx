/**
 * your lineup: every channel in the guide, in its groups, to arrange as you
 * like. move one up or down, rename it, hide it (it stays here, off the
 * guide), change or remove the channels you made. every change saves at
 * once, for this profile on every screen. reset puts the guide's order back.
 */

import { useEffect, useRef, useState } from 'react';

import type { Channel, LineupEntry, SavedChannel } from '../-lib/channels';

import { CHANNEL_GROUPS } from '../-lib/channels';
import { guideEdits } from '../-player.api';
import f from './guide-form.module.css';
import { PencilIcon, TrashIcon } from './icons';
import { Sheet } from './sheet';
import sh from './sheet.module.css';

type Lined = Channel & { hidden?: boolean; ownName?: string };

export function LineupEditor({
  channels,
  made,
  canEditServer,
  onChanged,
  onEdit,
  onRemove,
  onClose,
}: {
  /** every channel in this profile's order, hidden ones included */
  channels: Lined[];
  made: SavedChannel[];
  canEditServer: boolean;
  onChanged: () => void;
  onEdit: (channel: SavedChannel) => void;
  onRemove: (channel: SavedChannel) => void;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<LineupEntry[]>(() =>
    channels.map((c) => ({
      channel_id: c.id,
      hidden: !!c.hidden,
      name: c.name !== c.ownName ? c.name : null,
    })),
  );
  const [saved, setSaved] = useState<'saving' | 'saved' | 'failed' | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const byId = new Map(channels.map((c) => [c.id, c]));
  const madeById = new Map(made.map((m) => [`made-${m.id}`, m]));

  // the change waiting out the debounce: closing the editor sends it, it
  // used to be dropped with the timer
  const pending = useRef<LineupEntry[] | null>(null);
  const send = (next: LineupEntry[], live: boolean) => {
    pending.current = null;
    guideEdits
      .saveLineup(next)
      .then(() => {
        if (live) setSaved('saved');
        onChanged();
      })
      .catch(() => live && setSaved('failed'));
  };
  const commit = (next: LineupEntry[]) => {
    setRows(next);
    setSaved('saving');
    pending.current = next;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => send(next, true), 500);
  };
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(
    () => () => {
      window.clearTimeout(timer.current);
      if (pending.current) sendRef.current(pending.current, false);
    },
    [],
  );

  const update = (id: string, change: Partial<LineupEntry>) =>
    commit(rows.map((r) => (r.channel_id === id ? { ...r, ...change } : r)));

  /** move within its group: the guide shows groups, so that's the order that shows */
  const move = (id: string, delta: -1 | 1) => {
    const kind = byId.get(id)?.kind;
    const same = rows.filter((r) => byId.get(r.channel_id)?.kind === kind);
    const at = same.findIndex((r) => r.channel_id === id);
    const other = same[at + delta];
    if (!other) return;
    const next = [...rows];
    const a = next.findIndex((r) => r.channel_id === id);
    const b = next.findIndex((r) => r.channel_id === other.channel_id);
    [next[a], next[b]] = [next[b], next[a]];
    commit(next);
  };

  const shownCount = rows.filter((r) => !r.hidden).length;
  return (
    <Sheet
      wide
      title="Your lineup"
      subtitle={`${shownCount} of ${rows.length} channels in your guide. Changes save as you go, on every screen.`}
      onClose={onClose}
      footer={
        <>
          <span className={f.count} role="status">
            {saved === 'saving'
              ? 'Saving…'
              : saved === 'saved'
                ? 'Saved'
                : saved === 'failed'
                  ? "Couldn't save. Try again."
                  : ''}
          </span>
          <button type="button" className={sh.btn} onClick={() => commit([])} data-nav>
            Reset to the guide’s order
          </button>
          <button type="button" className={sh.btnPrimary} onClick={onClose} data-nav>
            Done
          </button>
        </>
      }
    >
      {CHANNEL_GROUPS.map((group) => {
        const inGroup = rows.filter((r) => byId.get(r.channel_id)?.kind === group.kind);
        if (!inGroup.length) return null;
        return (
          <section key={group.kind} className={f.group} aria-label={group.label}>
            <div className={f.groupHead}>
              <span className={f.groupLabel}>{group.label}</span>
              <button
                type="button"
                className={f.tool}
                onClick={() => {
                  // all hidden: show them all; otherwise hide them all
                  const hide = inGroup.some((r) => !r.hidden);
                  const ids = new Set(inGroup.map((r) => r.channel_id));
                  commit(rows.map((r) => (ids.has(r.channel_id) ? { ...r, hidden: hide } : r)));
                }}
                data-nav
              >
                {inGroup.some((r) => !r.hidden) ? 'Hide all' : 'Show all'}
              </button>
            </div>
            {inGroup.map((r, i) => {
              const c = byId.get(r.channel_id)!;
              const mine = madeById.get(r.channel_id);
              const editable = mine && (mine.scope === 'mine' || canEditServer);
              return (
                <div
                  key={r.channel_id}
                  className={f.line}
                  data-hidden={r.hidden ? 'true' : undefined}
                >
                  <span className={f.lineNum}>{r.hidden ? '–' : (c.number ?? '')}</span>
                  <input
                    className={f.lineName}
                    defaultValue={r.name ?? c.ownName ?? c.name}
                    maxLength={40}
                    aria-label={`Name for ${c.ownName ?? c.name}`}
                    title="Rename it in your guide"
                    onBlur={(e) => {
                      const value = e.target.value.trim();
                      const own = c.ownName ?? c.name;
                      const name = value && value !== own ? value : null;
                      if (name !== r.name) update(r.channel_id, { name });
                      if (!value) e.target.value = own;
                    }}
                  />
                  <span className={f.lineTools}>
                    {mine ? (
                      <span className={f.tag}>
                        {mine.scope === 'server' ? 'Everyone' : 'Yours'}
                      </span>
                    ) : null}
                    <button
                      type="button"
                      className={f.tool}
                      aria-label={`Move ${c.name} up`}
                      disabled={i === 0}
                      onClick={() => move(r.channel_id, -1)}
                      data-nav
                    >
                      ▲
                    </button>
                    <button
                      type="button"
                      className={f.tool}
                      aria-label={`Move ${c.name} down`}
                      disabled={i === inGroup.length - 1}
                      onClick={() => move(r.channel_id, 1)}
                      data-nav
                    >
                      ▼
                    </button>
                    <button
                      type="button"
                      className={f.tool}
                      aria-pressed={r.hidden}
                      title={r.hidden ? 'Hidden from your guide' : 'In your guide'}
                      onClick={() => update(r.channel_id, { hidden: !r.hidden })}
                      data-nav
                    >
                      {r.hidden ? 'Show' : 'Hide'}
                    </button>
                    {editable ? (
                      <>
                        <button
                          type="button"
                          className={f.tool}
                          aria-label={`Change ${c.name}`}
                          title="Change what's on it"
                          onClick={() => onEdit(mine)}
                          data-nav
                        >
                          <PencilIcon />
                        </button>
                        <button
                          type="button"
                          className={f.tool}
                          aria-label={`Remove ${c.name}`}
                          title={mine.scope === 'server' ? 'Remove it for everyone' : 'Remove it'}
                          onClick={() => onRemove(mine)}
                          data-nav
                        >
                          <TrashIcon />
                        </button>
                      </>
                    ) : null}
                  </span>
                </div>
              );
            })}
          </section>
        );
      })}
    </Sheet>
  );
}
