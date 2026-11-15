/**
 * Pieces every ported Settings tab shares (SETTINGS_PORT.md). They render the
 * page's markup as it is after settings.js has finished with it, so the
 * page's css, its search and its keyboard handlers find what they always did.
 *
 *  - Section: a collapsible tile. The header's hint shows a live summary of
 *    the section once the settings have loaded, else its fixed description
 *    (settings.js refreshLibrarySummaries did this by rewriting the hint).
 *  - The help line: settings.js initSettingHelpLines puts the first sentence
 *    of a setting's help under it, and moves the "i" to the end of that line
 *    when there is more to read, or hides it when there is not. The "i" opens
 *    the rest (toggleSettingHelp).
 */

import { type KeyboardEvent, type MouseEvent, type ReactNode, useCallback, useState } from 'react';

/** the page's collapsible tile, exactly as its inline handler did it. the
 *  settings search and the tab switch change these classes on the nodes
 *  themselves, so this stays out of React state */
export function toggleSection(event: MouseEvent<HTMLElement>) {
  const header = event.currentTarget;
  header.classList.toggle('collapsed');
  const body = header.nextElementSibling as HTMLElement | null;
  if (!body) return;
  body.classList.toggle('collapsed');
  body.style.display = body.classList.contains('collapsed') ? 'none' : '';
}

type Side = 'music' | 'video' | undefined;

function sideAttrs(side: Side) {
  if (side === 'music') return { 'data-music-only': '' };
  if (side === 'video') return { 'data-video-only': '' };
  return {};
}

export function Section({
  tab,
  title,
  hint,
  summaryKey,
  summary,
  side,
  bodyStyle,
  children,
}: {
  tab: string;
  title: string;
  /** the fixed description, shown until (and whenever) there is no summary */
  hint: string;
  /** data-stg-summary: the hint shows `summary` once it is not empty */
  summaryKey?: string;
  summary?: string;
  side?: Side;
  bodyStyle?: Record<string, string>;
  children: ReactNode;
}) {
  const live = !!summaryKey && !!summary;
  return (
    <>
      <div
        className="settings-section-header collapsed"
        data-stg={tab}
        {...sideAttrs(side)}
        onClick={toggleSection}
        role="button"
        tabIndex={0}
        aria-expanded="false"
      >
        <span className="settings-section-arrow">&#9660;</span>
        <h3>{title}</h3>
        {summaryKey ? (
          <span
            className={live ? 'settings-section-hint is-live' : 'settings-section-hint'}
            data-stg-summary={summaryKey}
            data-stg-fallback={hint}
          >
            {live ? summary : hint}
          </span>
        ) : (
          <span className="settings-section-hint">{hint}</span>
        )}
      </div>
      <div
        className="settings-section-body collapsed"
        data-stg={tab}
        {...sideAttrs(side)}
        style={{ display: 'none', ...bodyStyle }}
      >
        {children}
      </div>
    </>
  );
}

/** settings.js settingHelpFirstLine: the first sentence, at most ~140 chars */
export function helpFirstLine(text: string): { line: string; rest: boolean } {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return { line: '', rest: false };
  const match = clean.match(/^(.+?[.!?])(\s|$)/);
  let line = match ? match[1] : clean;
  if (line.length > 140) {
    const cut = line.slice(0, 140);
    line = cut.slice(0, Math.max(cut.lastIndexOf(' '), 80)).replace(/[,;:\s]+$/, '') + '…';
  }
  return { line, rest: line.length < clean.length };
}

export function useHelp() {
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((o) => !o), []);
  return { open, toggle };
}

function HelpIcon({
  open,
  toggle,
  hidden,
}: {
  open: boolean;
  toggle: () => void;
  hidden?: boolean;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLSpanElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggle();
    }
  };
  return (
    <span
      className={open ? 'info-icon open' : 'info-icon'}
      role="button"
      tabIndex={0}
      title="What's this?"
      hidden={hidden}
      onClick={toggle}
      onKeyDown={onKeyDown}
    >
      i
    </span>
  );
}

/**
 * The "i" and the help line for one setting. `text` is the help as plain
 * text (what the body's textContent reads); the body itself is rendered by
 * the caller, `hidden` while closed, where the vanilla markup had it.
 *
 * `icon` goes where the "i" sat in the markup: present (hidden) only when the
 * whole help fits on the line. `line` is the last child of the form-group.
 */
export function helpBits(text: string, help: { open: boolean; toggle: () => void }) {
  const { line, rest } = helpFirstLine(text);
  if (!line) return { icon: <HelpIcon {...help} />, line: null };
  return {
    icon: rest ? null : <HelpIcon {...help} hidden />,
    line: (
      <small className="stg-help-line">
        <span className="stg-help-text">{line}</span>
        {rest ? <HelpIcon {...help} /> : null}
      </small>
    ),
  };
}

/** a number field that keeps what is typed and saves what it parses to */
export function useDraft(value: string, save: (raw: string) => void) {
  const [draft, setDraft] = useState<string | null>(null);
  return {
    value: draft ?? value,
    onChange: (raw: string) => {
      setDraft(raw);
      save(raw);
    },
    reset: () => setDraft(null),
  };
}
