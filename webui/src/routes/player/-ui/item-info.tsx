/**
 * the info sheet: what an item is and what file it is (movie, episode,
 * track, audiobook, podcast episode, home video, youtube). the server words
 * every value (scrap/ITEM_INFO_CONTRACT.md); this only draws the sections.
 *
 * one sheet for the whole player: openItemInfo(ref) from anywhere (the
 * right-click menu), ItemInfoHost mounted once in the shell. InfoButton is
 * the "i" next to an item.
 */

import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { itemInfoQuery, type InfoItemType, type InfoRow } from '../-player.api';
import { CheckIcon, CopyIcon, InfoIcon } from './icons';
import s from './item-info.module.css';
import { Sheet } from './sheet';
import sh from './sheet.module.css';

export interface ItemInfoRef {
  type: InfoItemType;
  id: string;
  title?: string;
}

let open: ItemInfoRef | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

export function openItemInfo(ref: ItemInfoRef) {
  if (!ref.id) return;
  open = ref;
  emit();
}

export function closeItemInfo() {
  open = null;
  emit();
}

export function ItemInfoHost() {
  const ref = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => open,
    () => null,
  );
  if (!ref) return null;
  return <ItemInfoSheet key={`${ref.type}:${ref.id}`} item={ref} onClose={closeItemInfo} />;
}

/** the "i": round on its own, or a labelled button among a page's actions
 * (pass the page's button class and labelled) */
export function InfoButton({
  type,
  id,
  title,
  className,
  labelled,
}: {
  type: InfoItemType;
  id: string | number;
  title: string;
  className?: string;
  labelled?: boolean;
}) {
  return (
    <button
      type="button"
      className={className ?? s.button}
      title="Info"
      aria-label={`Info about ${title}`}
      onClick={(e) => {
        e.stopPropagation();
        openItemInfo({ type, id: String(id), title });
      }}
      onKeyDown={(e) => e.stopPropagation()}
      data-labelled={labelled ? 'true' : undefined}
      data-nav
    >
      <span className={s.glyph} data-labelled={labelled ? 'true' : undefined}>
        <InfoIcon />
      </span>
      {labelled ? 'Info' : null}
    </button>
  );
}

export function ItemInfoSheet({ item, onClose }: { item: ItemInfoRef; onClose: () => void }) {
  const info = useQuery(itemInfoQuery(item.type, item.id));
  const data = info.data;
  return (
    <Sheet
      title={data?.title ?? item.title ?? 'Info'}
      subtitle={data?.subtitle || undefined}
      onClose={onClose}
      wide
    >
      {info.isLoading ? (
        <p className={s.note} role="status">
          Reading the details…
        </p>
      ) : info.isError ? (
        <div className={s.note} role="alert">
          <p className={s.problem}>
            {info.error instanceof Error ? info.error.message : "Couldn't load this item's info."}
          </p>
          <button type="button" className={sh.btn} onClick={() => void info.refetch()} data-nav>
            Try again
          </button>
        </div>
      ) : data && data.sections.length ? (
        <div className={s.sections}>
          {data.sections.map((section, n) => (
            <section
              key={`${section.id}-${n}`}
              className={s.section}
              aria-label={section.title}
              data-first-of-group={
                section.group && data.sections[n - 1]?.group !== section.group ? 'true' : undefined
              }
            >
              <h3 className={s.heading}>{section.title}</h3>
              <dl className={s.rows}>
                {section.rows.map((row, i) => (
                  <InfoLine key={`${row.label}-${i}`} row={row} />
                ))}
              </dl>
            </section>
          ))}
        </div>
      ) : (
        <p className={s.note}>There's nothing more to show about this yet.</p>
      )}
    </Sheet>
  );
}

function InfoLine({ row }: { row: InfoRow }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = async () => {
    const ok = await copyText(row.value);
    if (!ok) return;
    setCopied(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1400);
  };
  return (
    <div className={s.row}>
      <dt className={s.label}>{row.label}</dt>
      <dd className={s.value} data-mono={row.mono ? 'true' : undefined}>
        <span className={s.text}>{row.value}</span>
        {row.copy ? (
          <button
            type="button"
            className={s.copy}
            aria-label={copied ? `${row.label} copied` : `Copy ${row.label}`}
            title={copied ? 'Copied' : 'Copy'}
            onClick={() => void copy()}
            data-copied={copied ? 'true' : undefined}
            data-nav
          >
            {copied ? <CheckIcon /> : <CopyIcon />}
          </button>
        ) : null}
      </dd>
    </div>
  );
}

/** the clipboard where the browser offers it, else the old way */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // a page that isn't https: fall through
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  } catch {
    return false;
  }
}
