/**
 * What every page file (P5) builds its answers from: a poll that only runs
 * while the sheet shows Home, the rows that name what's stuck, the buttons,
 * plain-words formatting, and the admin's Health findings with their fixes.
 *
 * Answers are mounted by home.tsx only while the sheet is open on Home, so
 * a poll here costs nothing when help is closed.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';

import { getHealth, request, type HealthFinding, type HealthFix } from '../api';
import hs from '../help.module.css';
import { Icon, type IconName } from '../icons';
import { openArticleInSheet, openExternal } from '../links';
import { useHelpStore } from '../store';
import s from './answers.module.css';

// ── data ────────────────────────────────────────────────────────────────

export interface Live<T> {
  data: T | undefined;
  /** the first read settled, ok or not */
  loaded: boolean;
  reload: () => void;
}

/**
 * Read once on mount, then every `everyMs` while the tab is visible. A
 * failed read keeps what was there; `load` returning undefined is "nothing".
 * Pass everyMs 0 for a single read.
 */
export function useLive<T>(load: (() => Promise<T>) | null, everyMs: number): Live<T> {
  const [state, setState] = useState<{ data: T | undefined; loaded: boolean }>({
    data: undefined,
    loaded: false,
  });
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  const enabled = load !== null;

  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    const run = async () => {
      const fn = loadRef.current;
      if (!fn) return;
      try {
        const data = await fn();
        if (alive) setState({ data, loaded: true });
      } catch {
        if (alive) setState((prev) => ({ ...prev, loaded: true }));
      }
    };
    void run();
    const timer =
      everyMs > 0
        ? setInterval(() => {
            if (!document.hidden) void run();
          }, everyMs)
        : null;
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [enabled, everyMs, tick]);

  return { ...state, reload: () => setTick((n) => n + 1) };
}

/** a plain GET through the help client; undefined on any failure */
export async function getJson<T>(path: string): Promise<T | undefined> {
  const res = await request<T>(path);
  return res.ok ? res.data : undefined;
}

export function currentProfileId(): number {
  const ctx = typeof window === 'undefined' ? null : window.getCurrentProfileContext?.();
  return ctx?.profileId || 1;
}

/** the admin's Health findings, worst first. a member never asks (403 anyway) */
export function useHealthFindings(isAdmin: boolean, everyMs = 60_000): Live<HealthFinding[]> {
  return useLive(
    isAdmin
      ? async () => {
          const res = await getHealth(false);
          if (!res.ok) throw new Error(res.error);
          return Array.isArray(res.data.findings) ? res.data.findings : [];
        }
      : null,
    everyMs,
  );
}

export function isProblem(f: HealthFinding): boolean {
  return f.severity === 'error' || f.severity === 'warning';
}

// ── words ───────────────────────────────────────────────────────────────

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** "18 min", "2 hours", "3 days"; seconds under a minute say "a moment" */
export function spoken(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return 'under a minute';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.round(s / 3600);
  if (h < 48) return plural(h, 'hour');
  return plural(Math.round(s / 86400), 'day');
}

/** "Schism", "Schism and Parabola", "Schism, Parabola and 2 more" */
export function names(list: string[], max = 2): string {
  const shown = list.filter(Boolean);
  if (shown.length <= 1) return shown[0] ?? '';
  if (shown.length <= max) return `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}`;
  return `${shown.slice(0, max).join(', ')} and ${shown.length - max} more`;
}

/** sqlite stamps are bare utc, "2026-11-17 21:42:07"; null when unreadable */
export function stampMs(value: string | number | null | undefined): number | null {
  if (value == null || value === '') return null;
  if (typeof value === 'number') return value > 1e12 ? value : value * 1000;
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value.replace(' ', 'T')}Z`;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

/** "18 min ago", "2 hours ago"; '' when unknown */
export function ago(value: string | number | null | undefined, now = Date.now()): string {
  const ms = stampMs(value);
  if (ms == null) return '';
  const secs = (now - ms) / 1000;
  if (secs < 60) return 'just now';
  return `${spoken(secs)} ago`;
}

/** first letter up, a trailing full stop */
export function sentence(text: string): string {
  const t = text.trim().replace(/\s+/g, ' ');
  if (!t) return '';
  const up = t.charAt(0).toUpperCase() + t.slice(1);
  return /[.!?]$/.test(up) ? up : `${up}.`;
}

// ── pieces ──────────────────────────────────────────────────────────────

export function Why({ children }: { children: ReactNode }) {
  return <div className={s.why}>{children}</div>;
}

export function WhyRow({
  title,
  sub,
  art,
  fallback,
  wrap,
}: {
  title: ReactNode;
  sub?: ReactNode;
  /** a cover url; without one the row shows `fallback`'s first letter */
  art?: string | null;
  fallback?: string;
  /** let the second line wrap (a reason, an error) */
  wrap?: boolean;
}) {
  const letter = (fallback || '').trim().charAt(0).toUpperCase();
  const lead = art || letter;
  return (
    <div className={s.whyRow} data-plain={lead ? undefined : ''} data-wrap={wrap ? '' : undefined}>
      {lead ? (
        <span className={s.art} aria-hidden="true">
          {art ? <img src={art} alt="" loading="lazy" /> : letter}
        </span>
      ) : null}
      <span className={s.whyText}>
        <b className={s.whyTitle}>{title}</b>
        {sub ? <small className={s.whySub}>{sub}</small> : null}
      </span>
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return <p className={s.note}>{children}</p>;
}

export function Path({ children }: { children: ReactNode }) {
  return <code className={s.path}>{children}</code>;
}

/** 0..100 */
export function Bar({ pct, label }: { pct: number; label: string }) {
  const value = Math.max(0, Math.min(100, Math.round(pct)));
  return (
    <div
      className={s.bar}
      role="progressbar"
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <i style={{ width: `${value}%` }} />
    </div>
  );
}

export function Acts({ children }: { children: ReactNode }) {
  return <div className={hs.acts}>{children}</div>;
}

/**
 * One action. An async onClick shows busy and can't double fire; a failure
 * is the handler's to toast (the page's own wording).
 */
export function Act({
  kind = 'secondary',
  icon,
  children,
  onClick,
}: {
  kind?: 'primary' | 'secondary' | 'ghost';
  icon?: IconName;
  children: ReactNode;
  onClick: () => void | Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(
    () => () => {
      alive.current = false;
    },
    [],
  );
  return (
    <button
      type="button"
      className={`${hs.btn} ${s.act}`}
      data-kind={kind}
      aria-busy={busy || undefined}
      disabled={busy}
      onClick={() => {
        const out = onClick();
        if (out && typeof (out as Promise<unknown>).then === 'function') {
          setBusy(true);
          void (out as Promise<unknown>)
            .catch(() => undefined)
            .finally(() => {
              if (alive.current) setBusy(false);
            });
        }
      }}
    >
      {icon ? <Icon name={icon} size={14} /> : null}
      {children}
    </button>
  );
}

// ── actions that reach the page ─────────────────────────────────────────

export const PHONE = '(max-width: 768px)';

export function toast(message: string, type: 'success' | 'error' | 'info' = 'info'): void {
  window.showToast?.(message, type);
}

/** on a phone the sheet covers the page: get out of the way first */
export function revealPage(): void {
  if (typeof window.matchMedia === 'function' && window.matchMedia(PHONE).matches) {
    useHelpStore.getState().close();
  }
}

/** click one of the page's own controls; false when it isn't there */
export function pressPage(selector: string): boolean {
  const el = document.querySelector<HTMLElement>(selector);
  if (!el) return false;
  revealPage();
  el.click();
  el.scrollIntoView?.({ block: 'nearest' });
  return true;
}

/** an in-app href, query string included ('/wishlist?failing=true') */
export function goHref(href: string): void {
  revealPage();
  const router = window.SoulSyncWebRouter;
  if (router?.navigateToHref) void router.navigateToHref(href);
  else window.location.assign(href);
}

/** a docs article, read inside the sheet */
export const readArticle = openArticleInSheet;

/** a Settings tab on this side or the video side ('downloads', media 'video') */
export function openSettings(tab: string, media?: 'video'): void {
  const safe = tab.replace(/[^\w-]/g, '');
  const onSettings = /\/settings\/?$/.test(window.location.pathname);
  const sameMedia =
    (new URLSearchParams(window.location.search).get('media') === 'video') === (media === 'video');
  const show = () => window.switchSettingsTab?.(safe);
  revealPage();
  if (onSettings && sameMedia) {
    show();
    return;
  }
  const router = window.SoulSyncWebRouter;
  if (router?.navigateToHref) {
    void router.navigateToHref(`/settings${media === 'video' ? '?media=video' : ''}`);
  } else {
    void window.navigateToPage?.('settings');
  }
  let tries = 0;
  const attempt = () => {
    tries += 1;
    if (document.querySelector(`#settings-page [data-tab="${safe}"]`)) show();
    else if (tries < 20) setTimeout(attempt, 100);
  };
  setTimeout(attempt, 50);
}

// ── health findings as answers (admin) ──────────────────────────────────

/** what a finding's fix button does; the server already wrote the label */
export async function runFix(fix: HealthFix, onDone?: () => void): Promise<void> {
  switch (fix.kind) {
    case 'settings':
      openSettings(fix.tab, fix.media);
      return;
    case 'page':
      goHref(fix.media === 'video' ? `${fix.path}?media=video` : fix.path);
      return;
    case 'external':
      openExternal(fix.href);
      return;
    case 'checklist':
      if (fix.href) goHref(fix.href);
      return;
    case 'logs':
      useHelpStore.getState().push({ view: 'logs' });
      return;
    case 'recheck':
      await getHealth(true);
      onDone?.();
      return;
    case 'post': {
      const res = await request<{ success?: boolean; error?: string; message?: string }>(fix.url, {
        method: 'POST',
        body: JSON.stringify(fix.body ?? {}),
      });
      const ok = res.ok && res.data?.success !== false;
      if (ok) toast(res.ok && res.data?.message ? res.data.message : 'Done', 'success');
      else toast((res.ok ? res.data?.error : res.error) || "That didn't work", 'error');
      if (fix.recheck !== false) await getHealth(true);
      onDone?.();
      return;
    }
  }
}

/**
 * A Health finding, said where it bites. `skip` drops fixes that would only
 * take you where you already are (the Settings tab you're on).
 */
export function FindingBody({
  finding,
  skip,
  onDone,
}: {
  finding: HealthFinding;
  skip?: (fix: HealthFix) => boolean;
  onDone?: () => void;
}) {
  const fixes = (finding.fix ?? []).filter((f) => !skip?.(f));
  const hasRecheck = fixes.some((f) => f.kind === 'recheck' || f.kind === 'post');
  return (
    <>
      {finding.detail ? <p>{sentence(finding.detail)}</p> : null}
      <Acts>
        {fixes.map((fix, i) => (
          <Act
            key={`${fix.kind}-${fix.label}`}
            kind={i === 0 ? 'primary' : 'secondary'}
            icon={fix.kind === 'recheck' || fix.kind === 'post' ? 'refresh' : undefined}
            onClick={() => runFix(fix, onDone)}
          >
            {fix.label}
          </Act>
        ))}
        {hasRecheck ? null : (
          <Act
            kind={fixes.length ? 'secondary' : 'primary'}
            icon="refresh"
            onClick={() => runFix({ label: 'Check again', kind: 'recheck' }, onDone)}
          >
            Check again
          </Act>
        )}
        {finding.docs ? (
          <Act kind="ghost" onClick={() => readArticle(finding.docs as string)}>
            Learn more
          </Act>
        ) : null}
      </Acts>
    </>
  );
}

/** findings in → answers out, errors first; one answer each */
export function findingAnswers(
  findings: HealthFinding[] | undefined,
  keep: (f: HealthFinding) => boolean,
  opts: { skip?: (fix: HealthFix) => boolean; onDone?: () => void } = {},
) {
  return (findings ?? [])
    .filter((f) => isProblem(f) && keep(f))
    .sort((a, b) => Number(b.severity === 'error') - Number(a.severity === 'error'))
    .map((f, i) => ({
      id: `health:${f.id}`,
      title: f.title,
      tone: 'warn' as const,
      live: true,
      open: i === 0,
      body: <FindingBody finding={f} skip={opts.skip} onDone={opts.onDone} />,
    }));
}
