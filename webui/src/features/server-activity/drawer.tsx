/**
 * The Server Activity drawer: one drawer, two widths. Now playing is 480px
 * over whatever page you were on; History and Stats widen the same drawer to
 * about 1120px, because tables and charts need room. On a phone it is a full
 * height sheet at every tab.
 */

import type { KeyboardEvent } from 'react';

import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { getSettings } from './api';
import d from './drawer.module.css';
import { History } from './history';
import { CloseIcon, NarrowIcon, WidenIcon } from './icons';
import { useLiveActivity, useNow } from './live';
import { NowPlaying } from './now-playing';
import { RANGES, Stats } from './stats';
import { Segmented } from './ui';

export type Tab = 'now' | 'history' | 'stats';
export const TABS: Tab[] = ['now', 'history', 'stats'];

const EXIT_MS = 420;
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface DrawerProps {
  open: boolean;
  tab: Tab;
  onTab: (tab: Tab) => void;
  onClose: () => void;
}

/** the presets come from settings; read once per open */
function usePresets(open: boolean): string[] {
  const [presets, setPresets] = useState<string[]>([]);
  useEffect(() => {
    if (!open) return;
    const ctrl = new AbortController();
    getSettings(ctrl.signal)
      .then((result) => {
        if (result.ok && result.data.settings) setPresets(result.data.settings.stop_presets ?? []);
      })
      .catch(() => {
        // closed first
      });
    return () => ctrl.abort();
  }, [open]);
  return presets;
}

export function Drawer({ open, tab, onTab, onClose }: DrawerProps) {
  const [mounted, setMounted] = useState(open);
  const [shown, setShown] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [statsDays, setStatsDays] = useState(30);
  const panel = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const panelId = useId();
  const tabId = (t: Tab) => `${panelId}-tab-${t}`;

  const live = useLiveActivity(open, tab === 'now');
  const now = useNow(open && tab === 'now');
  const presets = usePresets(open);

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

  // focus in on open, back to the button on close
  useEffect(() => {
    if (!open || !mounted) return;
    const before = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() => {
      // the dialog itself takes focus (no ring on open); Tab walks in from there
      panel.current?.focus({ preventScroll: true });
    });
    return () => {
      cancelAnimationFrame(frame);
      const back =
        before && before !== document.body ? before : document.getElementById('activity-float-btn');
      back?.focus?.({ preventScroll: true });
    };
  }, [open, mounted]);

  // a new tab starts at the top
  useEffect(() => {
    if (body.current) body.current.scrollTop = 0;
  }, [tab]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab' || !panel.current) return;
      const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => !el.closest('[inert]') && el.getAttribute('aria-hidden') !== 'true',
      );
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [onClose],
  );

  if (!mounted) return null;

  const data = live.data;
  const servers = data?.servers ?? [];
  const streams = data?.ok ? data.summary.streams : 0;
  const wide = tab !== 'now' && !narrow;

  return (
    <div className={d.root} data-server-activity data-shown={shown || undefined}>
      <div className={d.scrim} onClick={onClose} aria-hidden="true" />
      <div
        ref={panel}
        className={d.drawer}
        data-wide={wide || undefined}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <header className={d.head}>
          <h2 id={titleId} className={d.title}>
            <span className={d.liveDot} data-live={streams > 0 || undefined} aria-hidden="true" />
            Server Activity
          </h2>
          {servers.length ? (
            <ul className={d.servers} aria-label="Servers">
              {servers.map((srv) => {
                const word = srv.server === 'plex' ? 'Plex' : 'Jellyfin';
                const named =
                  srv.name && srv.name.toLowerCase() !== srv.server
                    ? `${word} · ${srv.name}`
                    : word;
                return (
                  <li key={srv.server} data-ok={srv.reachable || undefined}>
                    <i aria-hidden="true" />
                    {named}
                    {srv.reachable ? null : <span className={d.srOnly}> (not answering)</span>}
                  </li>
                );
              })}
            </ul>
          ) : null}
          <button type="button" className={d.close} aria-label="Close" onClick={onClose}>
            <CloseIcon size={18} />
          </button>
        </header>

        <div className={d.bar}>
          <Segmented<Tab>
            role="tablist"
            label="Server Activity views"
            value={tab}
            onChange={onTab}
            idFor={tabId}
            controls={panelId}
            options={[
              { value: 'now', label: 'Now playing', count: data?.ok ? streams : null },
              { value: 'history', label: 'History' },
              { value: 'stats', label: 'Stats' },
            ]}
          />
          <span className={d.barEnd}>
            {tab === 'stats' ? (
              <Segmented<number>
                label="Range"
                value={statsDays}
                onChange={setStatsDays}
                options={RANGES}
              />
            ) : null}
            {tab !== 'now' ? (
              <button
                type="button"
                className={d.widthToggle}
                onClick={() => setNarrow((x) => !x)}
                aria-pressed={narrow}
                title={narrow ? 'Make the drawer wide again' : 'Make the drawer narrow'}
              >
                {narrow ? <WidenIcon size={14} /> : null}
                {narrow ? 'Widen' : 'Narrow'}
                {narrow ? null : <NarrowIcon size={14} />}
              </button>
            ) : null}
          </span>
        </div>

        <div
          ref={body}
          className={d.body}
          role="tabpanel"
          id={panelId}
          aria-labelledby={tabId(tab)}
          tabIndex={-1}
        >
          {tab === 'now' ? (
            <NowPlaying
              data={data}
              error={live.error}
              loading={live.loading}
              receivedAt={live.receivedAt}
              now={now}
              presets={presets}
              onRetry={() => void live.refresh()}
              onClose={onClose}
              onSeeHistory={() => onTab('history')}
            />
          ) : tab === 'history' ? (
            <History onNavigate={onClose} />
          ) : (
            <Stats days={statsDays} onNavigate={onClose} />
          )}
        </div>
      </div>
    </div>
  );
}
