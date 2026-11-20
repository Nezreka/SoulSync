/**
 * Pieces every ported Settings tab shares (SETTINGS_PORT.md). They render the
 * page's markup as it is after settings.js has finished with it, so the
 * page's css, its search and its keyboard handlers find what they always did.
 *
 *  - The help line: settings.js initSettingHelpLines puts the first sentence
 *    of a setting's help under it, and moves the "i" to the end of that line
 *    when there is more to read, or hides it when there is not. The "i" opens
 *    the rest (toggleSettingHelp).
 */

import type React from 'react';

import {
  Children,
  type KeyboardEvent,
  type ReactNode,
  isValidElement,
  useCallback,
  useState,
} from 'react';

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

export function HelpIcon({
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

/** the text a node renders, as the DOM's textContent would read it. help
 *  bodies hold only text and inline tags (strong, em, code, br) */
export function textOf(node: ReactNode): string {
  let out = '';
  Children.forEach(node, (child) => {
    if (typeof child === 'string' || typeof child === 'number') out += String(child);
    // a <br> sat on its own line in the html, so the text read a space there
    else if (isValidElement(child) && child.type === 'br') out += ' ';
    else if (isValidElement<{ children?: ReactNode }>(child)) out += textOf(child.props.children);
  });
  return out;
}

/**
 * The "i" and the help line for one setting. `text` is the help (plain text,
 * or the body's own markup, read with textOf); the body itself is rendered by
 * the caller, `hidden` while closed, where the vanilla markup had it.
 *
 * `icon` goes where the "i" sat in the markup: present (hidden) only when the
 * whole help fits on the line. `line` is the last child of the form-group.
 */
export function helpBits(text: ReactNode, help: { open: boolean; toggle: () => void }) {
  const { line, rest } = helpFirstLine(typeof text === 'string' ? text : textOf(text));
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

/** ConfigManager.REDACTED_SENTINEL: a saved secret arrives as this, never the
 *  secret itself, and saving it back means "keep what is saved" */
export const REDACTED_SECRET_SENTINEL = '__redacted_unchanged__';

/**
 * A password field for a saved secret, as settings.js _wireRedactedSecrets
 * made them: focusing a masked field empties it so a new value is typed, not
 * added to the mask; leaving it empty and untouched puts the mask back (and
 * nothing is saved); real typing is a real change, an emptied field included.
 */
export function SecretInput({
  value,
  onChange,
  ...rest
}: {
  value: string;
  onChange: (value: string) => void;
} & Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  'value' | 'onChange' | 'type' | 'onFocus' | 'onBlur'
>) {
  // shown in place of the mask while a masked field has focus
  const [cleared, setCleared] = useState(false);
  return (
    <input
      {...rest}
      type="password"
      value={cleared ? '' : value}
      onFocus={() => {
        if (value === REDACTED_SECRET_SENTINEL) setCleared(true);
      }}
      onBlur={() => setCleared(false)}
      onChange={(event) => {
        setCleared(false);
        onChange(event.target.value);
      }}
    />
  );
}
