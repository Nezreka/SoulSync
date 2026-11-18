/**
 * The help sheet (plan 4.2, mockup scrap/help/mockup.html). Header: back,
 * the view's title, close, and the search field. Body: the view on top of
 * the stack. Footer: version, platform, up to date.
 *
 * Desktop: a non-modal dialog, the page stays usable beside it. Phone: a
 * full height modal sheet with a scrim, focus kept inside. Either way focus
 * goes to the search field on open, Esc closes, and focus returns to the ?.
 */

import type { KeyboardEvent as ReactKeyboardEvent } from 'react';

import { useCallback, useEffect, useId, useRef, useState } from 'react';

import s from './help.module.css';
import { Icon } from './icons';
import { openReleaseNotes } from './links';
import { VIEWS } from './outlet';
import { canGoBack, currentView, useHelpStore } from './store';
import { Kbd } from './ui';
import { VIEW_META } from './views';

export const BUTTON_ID = 'helper-float-btn';
export const PHONE_QUERY = '(max-width: 768px)';
const EXIT_MS = 220;
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

export function useIsPhone(): boolean {
  const query = () =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(PHONE_QUERY)
      : null;
  const [phone, setPhone] = useState(() => !!query()?.matches);
  useEffect(() => {
    const mq = query();
    if (!mq) return;
    const on = () => setPhone(mq.matches);
    on();
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  return phone;
}

function isMac(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '');
}

export function HelpSheet() {
  const open = useHelpStore((st) => st.open);
  const stack = useHelpStore((st) => st.stack);
  const query = useHelpStore((st) => st.query);
  const isAdmin = useHelpStore((st) => st.isAdmin);
  const page = useHelpStore((st) => st.page);
  const session = useHelpStore((st) => st.session);
  const focusSearch = useHelpStore((st) => st.focusSearch);
  const status = useHelpStore((st) => st.status);
  const close = useHelpStore((st) => st.close);
  const back = useHelpStore((st) => st.back);
  const setQuery = useHelpStore((st) => st.setQuery);

  const phone = useIsPhone();
  const [mounted, setMounted] = useState(open);
  const [shown, setShown] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const titleId = useId();

  const entry = currentView({ stack, query });
  const meta = VIEW_META[entry.view];
  const View = VIEWS[entry.view];
  const depth = canGoBack({ stack, query });

  // in, then out after the slide
  useEffect(() => {
    if (open) {
      setMounted(true);
      const frame = requestAnimationFrame(() => requestAnimationFrame(() => setShown(true)));
      return () => cancelAnimationFrame(frame);
    }
    setShown(false);
    const timer = setTimeout(() => setMounted(false), EXIT_MS);
    return () => clearTimeout(timer);
  }, [open]);

  // focus to the search field on open, back to the ? on close
  useEffect(() => {
    if (!open || !mounted) return;
    const before = document.activeElement as HTMLElement | null;
    // on a phone the field would pull the keyboard up over the sheet: the title takes focus
    if (phone) heading.current?.focus({ preventScroll: true });
    else input.current?.focus({ preventScroll: true });
    return () => {
      const button = document.getElementById(BUTTON_ID);
      const inside = before && panel.current?.contains(before);
      const back = before && before !== document.body && !inside ? before : button;
      // only take focus back if it was still ours; a click elsewhere on the page keeps it
      const active = document.activeElement;
      if (!active || active === document.body || panel.current?.contains(active)) {
        back?.focus?.({ preventScroll: true });
      }
    };
    // a new session is a new open; phone is read once per open
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, mounted, session]);

  // Ctrl K while open, or the palette's hand-off: the caret goes to the field
  useEffect(() => {
    if (focusSearch && mounted) input.current?.focus({ preventScroll: true });
  }, [focusSearch, mounted]);

  // a new view starts at the top and takes focus at its title, so a screen
  // reader hears where it landed (not on the very first render)
  const firstView = useRef(true);
  const viewKey = `${entry.view}:${JSON.stringify(entry.params ?? {})}`;
  useEffect(() => {
    if (body.current) body.current.scrollTop = 0;
    if (firstView.current) {
      firstView.current = false;
      return;
    }
    if (entry.view !== 'search' && document.activeElement !== input.current) {
      heading.current?.focus({ preventScroll: true });
    }
  }, [viewKey, entry.view]);
  useEffect(() => {
    if (!open) firstView.current = true;
  }, [open]);

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        event.nativeEvent.stopImmediatePropagation?.();
        if (event.target === input.current && query) {
          setQuery('');
          return;
        }
        close();
        return;
      }
      // a back gesture from the keyboard: Alt+Left, like a browser
      if (event.key === 'ArrowLeft' && event.altKey && depth) {
        event.preventDefault();
        back();
        return;
      }
      if (event.key !== 'Tab' || !phone || !panel.current) return;
      // modal on a phone: Tab stays inside
      const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => {
          // what sits in a closed answer can't take focus
          const fold = el.closest('details');
          return !fold || fold.open || el.tagName === 'SUMMARY';
        },
      );
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (
        event.shiftKey &&
        (document.activeElement === first || !panel.current.contains(document.activeElement))
      ) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [back, close, depth, phone, query, setQuery],
  );

  if (!mounted) return null;

  const placeholder = isAdmin ? 'Search help, settings and actions' : 'Search help and pages';
  const announce = [
    meta.title,
    isAdmin && status && entry.view === 'home'
      ? status.errors
        ? `${status.errors} ${status.errors === 1 ? 'thing needs' : 'things need'} you`
        : 'Everything is running'
      : '',
  ]
    .filter(Boolean)
    .join('. ');

  return (
    <div className={s.root} data-help-root data-shown={shown || undefined}>
      {phone ? <div className={s.scrim} onClick={close} aria-hidden="true" /> : null}
      <div
        ref={panel}
        id="soulsync-help-sheet"
        className={s.sheet}
        data-wide={meta.wide || undefined}
        data-view={entry.view}
        role="dialog"
        aria-modal={phone ? 'true' : 'false'}
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
      >
        <header className={s.head}>
          <div className={s.top}>
            {depth ? (
              <button type="button" className={s.iconBtn} aria-label="Back" onClick={back}>
                <Icon name="back" size={16} />
              </button>
            ) : null}
            <h2 id={titleId} ref={heading} className={s.title} tabIndex={-1}>
              <span className={s.titleText}>{meta.title}</span>
            </h2>
            <button type="button" className={s.iconBtn} aria-label="Close help" onClick={close}>
              <Icon name="x" size={16} />
            </button>
          </div>
          <label className={s.field}>
            <Icon name="search" size={16} />
            <input
              ref={input}
              className={s.input}
              type="search"
              value={query}
              placeholder={placeholder}
              aria-label={placeholder}
              autoComplete="off"
              spellCheck={false}
              enterKeyHint="search"
              onChange={(e) => setQuery(e.target.value)}
            />
            <span className={s.kbds} aria-hidden="true">
              <Kbd>{isMac() ? '⌘' : 'Ctrl'}</Kbd>
              <Kbd>K</Kbd>
            </span>
          </label>
        </header>

        <div ref={body} className={s.body}>
          <div key={viewKey} className={s.view}>
            <View entry={entry} query={query} isAdmin={isAdmin} page={page} />
          </div>
        </div>

        <Footer />
        <div className={s.srOnly} role="status" aria-live="polite">
          {shown ? announce : ''}
        </div>
      </div>
    </div>
  );
}

function Footer() {
  const update = useHelpStore((st) => st.update);
  const isAdmin = useHelpStore((st) => st.isAdmin);
  const version = update?.current_version;
  const name = version ? `SoulSync ${version}` : 'SoulSync';
  if (!isAdmin) {
    return (
      <footer className={s.foot}>
        <span className={s.dot} data-tone="ok" aria-hidden="true" />
        {name}
      </footer>
    );
  }
  const latest = update?.latest_version;
  return (
    <footer className={s.foot}>
      <span
        className={s.dot}
        data-tone={update?.update_available ? 'warn' : 'ok'}
        aria-hidden="true"
      />
      {update?.is_docker ? `${name} · Docker` : name}
      {update ? (
        update.update_available ? (
          <button type="button" className={s.footEnd} onClick={openReleaseNotes}>
            {latest ? `${latest} is out` : 'An update is out'}
            <Icon name="chev" size={12} />
          </button>
        ) : (
          <span className={s.footEnd}>Up to date</span>
        )
      ) : null}
    </footer>
  );
}
