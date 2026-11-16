/**
 * What the Library tab's two media cards share (Folders, step 8a; Organization,
 * step 8b): the collapsible section, the Music / Audiobooks / Podcasts / Video
 * tabs, and the vanilla panels parked in #library-parked-panels and placed
 * back between React's own while the card is mounted.
 */

import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';

import type { SettingsStore } from '../settings-store';

import { helpBits, useDraft, useHelp } from './parts';
import { text } from './source-panels';

export const LIBRARY_TAB = 'library';

export type Media = 'music' | 'audiobooks' | 'podcasts' | 'video';

const MEDIA: Array<[Media, string]> = [
  ['music', 'Music'],
  ['audiobooks', 'Audiobooks'],
  ['podcasts', 'Podcasts'],
  ['video', 'Video'],
];

/** a Library section: header and body, the body a media card */
export function LibrarySection({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: ReactNode;
}) {
  return (
    <>
      <div
        className="settings-section-header collapsed"
        data-stg={LIBRARY_TAB}
        onClick={(event) => {
          const header = event.currentTarget;
          header.classList.toggle('collapsed');
          const body = header.nextElementSibling as HTMLElement | null;
          if (!body) return;
          body.classList.toggle('collapsed');
          body.style.display = body.classList.contains('collapsed') ? 'none' : '';
        }}
        role="button"
        tabIndex={0}
        aria-expanded="false"
      >
        <span className="settings-section-arrow">&#9660;</span>
        <h3>{title}</h3>
        <span className="settings-section-hint">{hint}</span>
      </div>
      <div
        className="settings-section-body collapsed stg-media-card"
        data-stg={LIBRARY_TAB}
        style={{ display: 'none' }}
      >
        {children}
      </div>
    </>
  );
}

/**
 * The card's tabs (`<prefix>-<media>-tab`, controlling `<prefix>-<media>-panel`),
 * click and arrow keys as handleLibraryMediaTabKey did. The settings search
 * still switches a card by calling switchLibraryMediaTab on a tab, which clicks
 * a React one (settings.js).
 */
export function useMediaTabs(prefix: string, label: string) {
  const [media, setMedia] = useState<Media>('music');
  const refs = useRef<Partial<Record<Media, HTMLButtonElement | null>>>({});
  const onKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const current = MEDIA.findIndex(([k]) => k === media);
    const index =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? MEDIA.length - 1
          : (current + (event.key === 'ArrowRight' ? 1 : -1) + MEDIA.length) % MEDIA.length;
    event.preventDefault();
    setMedia(MEDIA[index][0]);
    refs.current[MEDIA[index][0]]?.focus();
  };
  const tabs = (
    <div className="stg-media-switcher" role="tablist" aria-label={label}>
      {MEDIA.map(([key, text]) => (
        <button
          key={key}
          ref={(el) => {
            refs.current[key] = el;
          }}
          type="button"
          id={`${prefix}-${key}-tab`}
          role="tab"
          aria-selected={media === key ? 'true' : 'false'}
          aria-controls={`${prefix}-${key}-panel`}
          tabIndex={media === key ? 0 : -1}
          onClick={() => setMedia(key)}
          onKeyDown={onKey}
        >
          {text}
        </button>
      ))}
    </div>
  );
  return { media, tabs };
}

/** a path field behind an Unlock button, as togglePathLock did it */
export function LockedPath({
  id,
  value,
  onChange,
  placeholder,
  check,
  loads,
}: {
  id: string;
  /** what the field shows when the settings load (a default when unset) */
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  check?: Record<string, string>;
  /** the settings loads: each shows the stored value again */
  loads?: number;
}) {
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // what was typed stays on screen, an emptied field too: loadSettingsData
  // filled the default once, and the field showed what was typed after it
  const draft = useDraft(value, onChange);
  useEffect(() => draft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="path-input-group">
      <input
        ref={input}
        type="text"
        id={id}
        {...check}
        placeholder={placeholder}
        readOnly={!open}
        value={draft.value}
        onChange={(event) => draft.onChange(event.target.value)}
      />
      <button
        className={open ? 'browse-button' : 'browse-button locked'}
        onClick={() => {
          setOpen(!open);
          if (!open) setTimeout(() => input.current?.focus(), 0);
        }}
      >
        {open ? 'Lock' : 'Unlock'}
      </button>
    </div>
  );
}

/** a setting with its "i" and its help (a hidden small) inside the form-group */
export function HintGroup({
  children,
  help,
  preview,
  hidden,
  after,
}: {
  children: ReactNode;
  help: ReactNode;
  /** the path preview slot, right after the field (_pathPreviewSlot) */
  preview?: ReactNode;
  hidden?: boolean;
  /** what sits after the help body, before the help line (a list) */
  after?: ReactNode;
}) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <div className="form-group" hidden={hidden}>
      {children}
      {preview}
      {bits.icon}
      <small className="settings-hint setting-help-body" hidden={!state.open}>
        {help}
      </small>
      {after}
      {bits.line}
    </div>
  );
}

/** a field that shows what is typed and saves what it reads as; a fresh
 *  load puts the saved value back */
export function useLoadedDraft(store: SettingsStore, value: string, save: (raw: string) => void) {
  const draft = useDraft(value, save);
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  useEffect(() => draft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return draft;
}

/** parseInt with a fallback for what does not read as one (_cfgInt) */
export function int(raw: string, fallback: number) {
  const n = parseInt(raw, 10);
  return Number.isFinite(n) ? n : fallback;
}

/** the Docker check loadSettingsData made, for the default folders */
export function isDocker(settings: Record<string, Record<string, unknown> | undefined>) {
  const env = settings._environment || {};
  const soulseek = settings.soulseek || {};
  return (
    !!env.docker ||
    text(soulseek.transfer_path).startsWith('/app/') ||
    text(soulseek.download_path).startsWith('/app/')
  );
}
