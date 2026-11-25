/**
 * A worker's status on a phone (#2180). On a desktop a worker's status is
 * its hover tooltip and a click pauses or resumes it. A phone has no hover:
 * a tap went straight to the toggle (or did nothing, for the display-only
 * ones), and the status could never be read. On a phone a tap now opens
 * this sheet instead: the worker's status, what it is on and how far, and
 * the tap's old action as a button.
 *
 * Both dashboards use it. The sheet reads the worker's own tooltip, so it
 * shows what the desktop tooltip would, whoever writes it (React on the
 * music side, video-enrichment.js on the video side), and it follows it
 * live while open.
 */

import type { RefObject } from 'react';

import { useEffect, useReducer, useState } from 'react';

import { DialogFrame, DialogHeader } from '@/components/dialog';

import styles from './worker-sheet.module.css';

/** where the tooltip is the only status there is */
export const PHONE_QUERY = '(hover: none), (max-width: 768px)';
const WORKER_BUTTON = '.enrich-worker-btn, .video-enrich-button';
const WORKER = '.enrich-worker, .video-enrich-container';

export interface WorkerSheetAction {
  label: string;
  /** the action leaves the page (Library Repair opens Tools) */
  closes?: boolean;
}

export interface WorkerSnapshot {
  name: string;
  status: string;
  current: string;
  progress: string;
  logo: { src: string; alt: string } | { glyph: string } | null;
}

const text = (root: Element, selector: string) =>
  (root.querySelector(selector)?.textContent ?? '').replace(/\s+/g, ' ').trim();

/** what the worker's tooltip says, read off the page */
export function readWorker(button: HTMLElement): WorkerSnapshot {
  const root = button.closest(WORKER) ?? button.parentElement ?? button;
  const img = button.querySelector('img');
  const glyph = [...button.querySelectorAll('span')].find(
    (span) => !/spinner|badge/.test(span.className) && span.textContent?.trim(),
  );
  return {
    name:
      text(root, '.enrich-worker-tooltip-header, .tooltip-header') ||
      button.getAttribute('title') ||
      'Worker',
    status: text(root, '.tooltip-status'),
    current: text(root, '.tooltip-current'),
    progress: text(root, '.tooltip-progress'),
    logo: img
      ? { src: img.getAttribute('src') ?? '', alt: img.getAttribute('alt') ?? '' }
      : glyph
        ? { glyph: glyph.textContent!.trim() }
        : null,
  };
}

// the sheet's action is the tap's old one: it clicks the worker for real,
// past the listener that turned the tap into the sheet
let passing = false;
function clickThrough(button: HTMLElement) {
  passing = true;
  try {
    button.click();
  } finally {
    passing = false;
  }
}

/**
 * On a phone, a tap on a worker in the stage opens its sheet instead of
 * running the worker's click. Capture phase on the stage, so neither React's
 * onClick nor the video side's delegated listener ever sees that tap.
 */
export function useWorkerSheet(stage: RefObject<HTMLElement | null>) {
  const [button, setButton] = useState<HTMLElement | null>(null);
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const onClick = (event: MouseEvent) => {
      if (passing) return;
      if (!window.matchMedia?.(PHONE_QUERY).matches) return;
      const target = event.target instanceof Element ? event.target : null;
      const hit = target?.closest<HTMLElement>(WORKER_BUTTON);
      if (!hit || !el.contains(hit)) return;
      event.preventDefault();
      event.stopPropagation();
      setButton(hit);
    };
    el.addEventListener('click', onClick, true);
    return () => el.removeEventListener('click', onClick, true);
  }, [stage]);
  return { button, close: () => setButton(null) };
}

/** re-render whenever the worker's markup changes (state class, tooltip text) */
function useFollow(button: HTMLElement | null) {
  const [, bump] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    if (!button) return;
    const root = button.closest(WORKER) ?? button;
    const observer = new MutationObserver(() => bump());
    observer.observe(root, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['class', 'style'],
    });
    return () => observer.disconnect();
  }, [button]);
}

export function WorkerSheet({
  button,
  describe,
  onClose,
  onManage,
}: {
  button: HTMLElement | null;
  /** the tap's old action for this worker as it is now, or null for none */
  describe: (button: HTMLElement) => WorkerSheetAction | null;
  onClose: () => void;
  onManage: () => void;
}) {
  useFollow(button);
  const worker = button ? readWorker(button) : null;
  const action = button ? describe(button) : null;
  return (
    <DialogFrame
      open={button !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      aria-label={worker ? `${worker.name} status` : 'Worker status'}
      viewportClassName={styles.viewport}
      className={styles.sheet}
    >
      {worker ? (
        <>
          <div className={styles.head}>
            {worker.logo && 'src' in worker.logo ? (
              <img className={styles.logo} src={worker.logo.src} alt="" aria-hidden="true" />
            ) : worker.logo ? (
              <span className={styles.logo} aria-hidden="true">
                {worker.logo.glyph}
              </span>
            ) : null}
            <DialogHeader
              title={worker.name}
              closeLabel="Close"
              compact
              className={styles.header}
            />
          </div>
          <div className={styles.rows} data-testid="worker-sheet-rows">
            {worker.status ? <p className={styles.status}>{worker.status}</p> : null}
            {worker.current ? <p className={styles.line}>{worker.current}</p> : null}
            {worker.progress ? <p className={styles.line}>{worker.progress}</p> : null}
          </div>
          <div className={styles.actions}>
            {action ? (
              <button
                type="button"
                className={styles.primary}
                onClick={() => {
                  if (button) clickThrough(button);
                  if (action.closes) onClose();
                }}
              >
                {action.label}
              </button>
            ) : null}
            <button
              type="button"
              className={styles.secondary}
              onClick={() => {
                onClose();
                onManage();
              }}
            >
              Manage workers
            </button>
          </div>
        </>
      ) : null}
    </DialogFrame>
  );
}
