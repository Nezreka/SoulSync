/**
 * The pieces every download card is built from, music and video alike: the
 * lead (art, track number, progress ring), the step list, the facts grid and
 * the ⋯ menu. One grammar for both sides, so a row reads the same wherever it
 * shows up.
 */

import { type ReactNode, useState } from 'react';

import type { JourneyStep } from '../-dl.format';
import type { ActionMenuItem } from '../../artist-detail/-ui/action-menu';

import { ActionMenu } from '../../artist-detail/-ui/action-menu';

const toast = (message: string, type: string) => window.showToast?.(message, type);

// ── icons ─────────────────────────────────────────────────────────────────

function Svg({ children, size = 15, sw = 2 }: { children: ReactNode; size?: number; sw?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export const DlIcon = {
  play: (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M7 4.5v15a1 1 0 0 0 1.5.86l12-7.5a1 1 0 0 0 0-1.72l-12-7.5A1 1 0 0 0 7 4.5z" />
    </svg>
  ),
  x: (
    <Svg sw={2.2}>
      <path d="M18 6 6 18M6 6l12 12" />
    </Svg>
  ),
  more: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <circle cx="5" cy="12" r="1.8" />
      <circle cx="12" cy="12" r="1.8" />
      <circle cx="19" cy="12" r="1.8" />
    </svg>
  ),
  chevron: (
    <Svg>
      <polyline points="6 9 12 15 18 9" />
    </Svg>
  ),
  fold: (
    <Svg size={11} sw={2.4}>
      <polyline points="9 18 15 12 9 6" />
    </Svg>
  ),
  next: (
    <Svg sw={2.2}>
      <path d="M5 12h14" />
      <path d="m13 6 6 6-6 6" />
      <path d="M5 5v14" />
    </Svg>
  ),
  plus: (
    <Svg sw={2.4}>
      <path d="M12 5v14M5 12h14" />
    </Svg>
  ),
  search: (
    <Svg>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </Svg>
  ),
  retry: (
    <Svg>
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </Svg>
  ),
  copy: (
    <Svg>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </Svg>
  ),
  open: (
    <Svg>
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </Svg>
  ),
  block: (
    <Svg>
      <circle cx="12" cy="12" r="9" />
      <line x1="5.6" y1="5.6" x2="18.4" y2="18.4" />
    </Svg>
  ),
  import: (
    <Svg>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </Svg>
  ),
  filter: (
    <Svg>
      <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
    </Svg>
  ),
  list: (
    <Svg>
      <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
    </Svg>
  ),
  check: (
    <Svg size={10} sw={3}>
      <polyline points="20 6 9 17 4 12" />
    </Svg>
  ),
  bang: (
    <Svg size={10} sw={3}>
      <path d="M12 7v6M12 17h.01" />
    </Svg>
  ),
  cross: (
    <Svg size={10} sw={3}>
      <path d="M18 6 6 18M6 6l12 12" />
    </Svg>
  ),
};

/** The AcoustID shield on a finished track. */
export function DlShield({ kind, title }: { kind: 'ok' | 'warn' | 'human'; title: string }) {
  return (
    <span className={`dlc-shield dlc-shield-${kind}`} title={title} aria-label={title} role="img">
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
        {kind === 'warn' ? <path d="M12 9v4M12 16h.01" /> : <path d="m9 12 2 2 4-4" />}
      </svg>
    </span>
  );
}

// ── the lead ──────────────────────────────────────────────────────────────

/** A progress ring, 0–100. */
export function DlRing({ pct }: { pct: number }) {
  const c = 2 * Math.PI * 16;
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <svg className="dlc-ring" viewBox="0 0 36 36" aria-hidden="true">
      <circle className="dlc-ring-track" cx="18" cy="18" r="16" />
      <circle
        className="dlc-ring-fill"
        cx="18"
        cy="18"
        r="16"
        strokeDasharray={c}
        strokeDashoffset={c * (1 - clamped / 100)}
      />
    </svg>
  );
}

export interface DlLeadProps {
  art?: string | null;
  /** 2:3 poster instead of a square cover. */
  poster?: boolean;
  /** tracklist mode: the number takes the art's place. */
  number?: number | string | null;
  /** 0–100 while downloading; a ring draws round the lead. */
  progress?: number | null;
  /** searching / processing: a spinner instead. */
  busy?: boolean;
  failed?: boolean;
  /** shown on hover over the art once the file exists. */
  onPlay?: () => void;
  /** the artist's photo: drawn round when there's no cover to show. */
  avatar?: string | null;
  /** what to draw when there is no art. */
  fallback?: ReactNode;
}

/**
 * The left edge of every row. Status lives here, on the art: a ring while it
 * moves, a spinner while it looks, a play button once it's done.
 */
export function DlLead({
  art,
  poster,
  number,
  progress,
  busy,
  failed,
  onPlay,
  avatar,
  fallback,
}: DlLeadProps) {
  // remember WHICH url broke: the server can fill a cover in on a later poll
  const [broken, setBroken] = useState<string | null>(null);
  const showArt = Boolean(art) && broken !== art && number == null;
  const showAvatar = !showArt && number == null && Boolean(avatar) && broken !== avatar;
  // a ring round a 2:3 poster reads as a mistake. posters keep their art and
  // let the meta column + bottom bar carry the progress
  const ringing = !poster && progress != null && progress > 0;
  const spinning = !poster && busy && !ringing;
  return (
    <div className={`dlc-lead${poster ? ' dlc-lead-poster' : ''}`}>
      {showArt ? (
        <img
          className={`dlc-art${ringing || spinning ? ' dlc-art-dim' : ''}`}
          src={art as string}
          alt=""
          loading="lazy"
          // art urls go stale. drop the torn icon, keep the row readable
          onError={() => setBroken(art as string)}
        />
      ) : showAvatar ? (
        <img
          className={`dlc-art dlc-art-avatar${ringing || spinning ? ' dlc-art-dim' : ''}`}
          src={avatar as string}
          alt=""
          loading="lazy"
          onError={() => setBroken(avatar as string)}
        />
      ) : number != null ? (
        <span className={`dlc-num${failed ? ' dlc-num-bad' : ''}`}>{number}</span>
      ) : (
        <span className="dlc-art dlc-art-empty">{fallback}</span>
      )}
      {ringing ? <DlRing pct={progress as number} /> : null}
      {spinning ? <span className="dlc-spin" aria-hidden="true" /> : null}
      {onPlay ? (
        <button
          type="button"
          className="dlc-play"
          title="Play"
          aria-label="Play"
          onClick={(e) => {
            e.stopPropagation();
            onPlay();
          }}
        >
          {DlIcon.play}
        </button>
      ) : null}
    </div>
  );
}

/** A small round artist photo. Hides itself if the url is dead. */
export function DlAvatar({ src, size = 'sm' }: { src?: string | null; size?: 'sm' | 'md' }) {
  const [dead, setDead] = useState<string | null>(null);
  if (!src || dead === src) return null;
  return (
    <img
      className={`dlc-avatar dlc-avatar-${size}`}
      src={src}
      alt=""
      loading="lazy"
      onError={() => setDead(src)}
    />
  );
}

// ── switches, badges ─────────────────────────────────────────────────────

export interface DlSegment {
  value: string;
  label: React.ReactNode;
  /** null hides the number (not known yet); 0 shows nothing either. */
  count?: number | null;
  /** a health dot before the label: ok / bad / off. */
  dot?: 'ok' | 'bad' | 'off';
  title?: string;
}

/** A segmented switch with counts: the sub-views under Review and Clients. */
export function DlSegmented({
  segments,
  value,
  onChange,
  label,
  id,
}: {
  segments: DlSegment[];
  value: string;
  onChange: (value: string) => void;
  label: string;
  id?: string;
}) {
  return (
    <div className="dlc-seg" role="tablist" aria-label={label} id={id}>
      {segments.map((seg) => (
        <button
          key={seg.value}
          type="button"
          role="tab"
          aria-selected={value === seg.value}
          className={`dlc-seg-btn${value === seg.value ? ' dlc-seg-on' : ''}`}
          data-seg={seg.value}
          title={seg.title}
          onClick={() => onChange(seg.value)}
        >
          {seg.dot ? <span className={`dlc-dot dlc-dot-${seg.dot}`} aria-hidden="true" /> : null}
          {seg.label}
          {seg.count ? <b>{seg.count}</b> : null}
        </button>
      ))}
    </div>
  );
}

/** "FLAC" from "06 Saudade.flac". a quarantined file's own name ends .quarantined, so give the original too. */
export function fileExt(...names: Array<string | null | undefined>): string {
  for (const name of names) {
    const m = /\.([a-z0-9]{2,5})$/i.exec(String(name || ''));
    if (m && m[1].toLowerCase() !== 'quarantined') return m[1].toUpperCase();
  }
  return '';
}

/** A file-shaped lead with its type on it, for rows that have no art. */
export function DlFileBadge({ ext }: { ext: string }) {
  return (
    <span className="dlc-file" aria-hidden="true">
      {ext || 'FILE'}
    </span>
  );
}

/** A square glyph lead (block, warning, client kinds). */
export function DlGlyph({ children, tone }: { children: ReactNode; tone?: 'bad' | 'ok' }) {
  return <span className={`dlc-glyph${tone ? ` dlc-glyph-${tone}` : ''}`}>{children}</span>;
}

/** Selected rows' actions, floating at the bottom of the list. */
export function DlSelectionBar({
  count,
  children,
  onClear,
}: {
  count: number;
  children: ReactNode;
  onClear: () => void;
}) {
  if (count <= 0) return null;
  return (
    <div className="dlc-selbar" role="toolbar" aria-label="Selection">
      <span className="dlc-selbar-n">{count} selected</span>
      {children}
      <DlButton onClick={onClear}>Clear</DlButton>
    </div>
  );
}

// ── the sheet ─────────────────────────────────────────────────────────────

/** found, downloaded, verified, imported: one line each, with times. */
export function DlJourney({ steps }: { steps: JourneyStep[] }) {
  return (
    <ol className="dlc-journey">
      {steps.map((step) => (
        <li key={step.key} className={`dlc-step dlc-step-${step.state}`}>
          <span className="dlc-node" aria-hidden="true">
            {step.state === 'done'
              ? DlIcon.check
              : step.state === 'fail'
                ? DlIcon.cross
                : step.state === 'warn'
                  ? DlIcon.bang
                  : null}
          </span>
          <div className="dlc-step-body">
            <div className="dlc-step-title">{step.title}</div>
            {step.detail ? <div className="dlc-step-detail">{step.detail}</div> : null}
          </div>
          <span className="dlc-step-time">{step.time || ''}</span>
        </li>
      ))}
    </ol>
  );
}

export type DlFact = [label: string, value: ReactNode, opts?: { mono?: boolean; copy?: string }];

function shownFacts(facts: DlFact[]): DlFact[] {
  return facts.filter(([, value]) => value !== '' && value !== null && value !== undefined);
}

function hasFacts(facts: DlFact[]): boolean {
  return shownFacts(facts).length > 0;
}

/** label / value pairs; a path gets mono type and a copy button. */
export function DlFacts({ facts }: { facts: DlFact[] }) {
  const shown = shownFacts(facts);
  if (!shown.length) return null;
  return (
    <dl className="dlc-facts">
      {shown.map(([label, value, opts]) => (
        <div className="dlc-fact" key={label}>
          <dt>{label}</dt>
          <dd className={opts?.mono ? 'dlc-mono' : undefined}>
            {value}
            {opts?.copy ? (
              <button
                type="button"
                className="dlc-copy"
                title={`Copy ${label.toLowerCase()}`}
                onClick={(e) => {
                  e.stopPropagation();
                  copyText(opts.copy as string, `${label} copied`);
                }}
              >
                copy
              </button>
            ) : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Two columns: the steps on the left, the facts on the right, actions under. */
export function DlSheet({
  stepsTitle,
  steps,
  factsTitle,
  facts,
  aside,
  actions,
  hero,
}: {
  stepsTitle: string;
  steps: JourneyStep[];
  factsTitle: string;
  facts: DlFact[];
  /** extra content under the facts (expected vs got, notes). */
  aside?: ReactNode;
  actions?: ReactNode;
  /** video only: the art + story band above the columns. */
  hero?: ReactNode;
}) {
  return (
    <div className="dlc-sheet" onClick={(e) => e.stopPropagation()}>
      {hero}
      <div className="dlc-sheet-cols">
        <section>
          <h4 className="dlc-sheet-h">{stepsTitle}</h4>
          <DlJourney steps={steps} />
        </section>
        {/* an empty Details heading reads as broken. leave the column out */}
        {hasFacts(facts) || aside ? (
          <section>
            <h4 className="dlc-sheet-h">{factsTitle}</h4>
            <DlFacts facts={facts} />
            {aside}
          </section>
        ) : null}
      </div>
      {actions ? <div className="dlc-sheet-acts">{actions}</div> : null}
    </div>
  );
}

// ── controls ──────────────────────────────────────────────────────────────

/** A labelled button. `primary` is the one thing this row wants you to do. */
export function DlButton({
  children,
  primary,
  danger,
  icon,
  className,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  primary?: boolean;
  danger?: boolean;
  icon?: ReactNode;
}) {
  return (
    <button
      type="button"
      {...rest}
      className={`dlc-btn${primary ? ' dlc-btn-primary' : ''}${danger ? ' dlc-btn-danger' : ''}${className ? ` ${className}` : ''}`}
      onClick={(e) => {
        e.stopPropagation();
        rest.onClick?.(e);
      }}
    >
      {icon}
      {children}
    </button>
  );
}

/** The ⋯ menu. Renders nothing when there is nothing in it. */
export function DlMore({
  items,
  label = 'More actions',
}: {
  items: ActionMenuItem[];
  label?: string;
}) {
  if (!items.length) return null;
  return (
    <ActionMenu
      className="dlc-menu"
      items={items}
      trigger={(props) => (
        <button
          type="button"
          className="dlc-icon-btn dlc-more"
          title={label}
          aria-label={label}
          {...props}
          ref={props.ref as (el: HTMLButtonElement | null) => void}
        >
          {DlIcon.more}
        </button>
      )}
    />
  );
}

/**
 * Copy text with a toast. navigator.clipboard is missing on plain-http lan
 * installs, so fall back to the old execCommand path before giving up.
 */
export function copyText(text: string, done = 'Copied'): void {
  const legacy = () => {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      toast(ok ? done : 'Copy not supported here', ok ? 'success' : 'info');
    } catch {
      toast('Copy not supported here', 'info');
    }
  };
  if (navigator.clipboard) {
    void navigator.clipboard.writeText(text).then(() => toast(done, 'success'), legacy);
  } else {
    legacy();
  }
}
