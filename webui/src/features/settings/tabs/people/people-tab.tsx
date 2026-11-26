/**
 * Settings > People (PLAN 8.1): pills for People, Global policy,
 * Templates, Invites and Activity. Admins only: for anyone else, and when
 * the API says no, nothing is drawn. Each person's own sheet is piece E's
 * (people/editor), opened from a row.
 *
 * window.openPeopleSettings(pill?) opens Settings on this tab, for "Manage
 * profiles" and anything else that should land here.
 */

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';

import type { SettingsTabProps } from '../../host';

import { usePeopleData, useViewer } from './data';
import { ActivityScreen, InvitesScreen } from './invites-activity';
import { onProfileLayersClosed } from './open-editor';
import { PeopleHome } from './people-home';
import css from './people.module.css';
import { PolicyScreen } from './policy-screen';
import { TemplatesScreen } from './templates-screen';

export const PEOPLE_TAB = 'people';

export type Pill = 'people' | 'policy' | 'templates' | 'invites' | 'activity';
const PILLS: Array<[Pill, string]> = [
  ['people', 'People'],
  ['policy', 'Global policy'],
  ['templates', 'Templates'],
  ['invites', 'Invites'],
  ['activity', 'Activity'],
];

// a pill asked for before the tab mounted (openPeopleSettings)
let pendingPill: Pill | null = null;
const pillListeners = new Set<(p: Pill) => void>();

export function openPeopleSettings(pill: Pill = 'people') {
  pendingPill = pill;
  for (const l of pillListeners) l(pill);
  const w = window as Window & {
    navigateToPage?: (page: string) => unknown;
    switchSettingsTab?: (tab: string) => void;
  };
  if (!document.getElementById('settings-page')) void w.navigateToPage?.('settings');
  // the shell keeps the tab for the page when it isn't on screen yet
  w.switchSettingsTab?.(PEOPLE_TAB);
}

export function PeopleTab({ active, opened }: SettingsTabProps) {
  const me = useViewer();
  const { state, reload } = usePeopleData(me.admin, opened);
  const [pill, setPill] = useState<Pill>(() => pendingPill ?? 'people');
  const [invites, setInvites] = useState<number | null>(null);
  const [visited, setVisited] = useState<Set<Pill>>(() => new Set([pill]));
  useEffect(() => {
    setVisited((v) => (v.has(pill) ? v : new Set([...v, pill])));
  }, [pill]);
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  useEffect(() => {
    const l = (p: Pill) => setPill(p);
    pillListeners.add(l);
    if (pendingPill) {
      setPill(pendingPill);
      pendingPill = null;
    }
    return () => {
      pillListeners.delete(l);
    };
  }, []);
  // the old editor and invite maker are modals: show what they saved
  useEffect(
    () => (active && me.admin ? onProfileLayersClosed(() => void reload()) : undefined),
    [active, me.admin, reload],
  );

  if (!me.admin) return null;
  if (state.status === 'error' && state.forbidden) return null;

  const data = state.status === 'ready' ? state.data : null;
  const counts: Partial<Record<Pill, number | null>> = data
    ? {
        people: data.people.length,
        templates: data.templates.length,
        invites: invites ?? data.openInvites,
      }
    : {};

  const key = (e: KeyboardEvent, i: number) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!step && e.key !== 'Home' && e.key !== 'End') return;
    e.preventDefault();
    const n =
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? PILLS.length - 1
          : (i + step + PILLS.length) % PILLS.length;
    setPill(PILLS[n][0]);
    refs.current[PILLS[n][0]]?.focus();
  };

  return (
    <section className={css.root} data-people="" hidden={!active} aria-label="People and access">
      <div className={css.pills} role="tablist" aria-label="People and access">
        {PILLS.map(([id, label], i) => (
          <button
            key={id}
            ref={(el) => {
              refs.current[id] = el;
            }}
            type="button"
            role="tab"
            id={`ppl-pill-${id}`}
            aria-selected={pill === id}
            aria-controls={`ppl-screen-${id}`}
            tabIndex={pill === id ? 0 : -1}
            className={css.pill}
            onClick={() => setPill(id)}
            onKeyDown={(e) => key(e, i)}
          >
            {label}
            {counts[id] ? <span className={css.count}>{counts[id]}</span> : null}
          </button>
        ))}
      </div>
      {state.status === 'error' ? (
        <div className={`${css.look} ${css.screen}`} role="alert">
          <span className={css.lookBody}>{state.message}</span>
          <button
            type="button"
            className={`${css.btn} ${css.secondary}`}
            onClick={() => void reload()}
          >
            Try again
          </button>
        </div>
      ) : !data ? (
        <p className={css.empty}>Loading people...</p>
      ) : (
        PILLS.filter(([id]) => visited.has(id)).map(([id]) => (
          // a screen stays mounted once opened, so an unsaved policy or
          // template survives a look at another pill
          <div
            key={id}
            id={`ppl-screen-${id}`}
            role="tabpanel"
            aria-labelledby={`ppl-pill-${id}`}
            className={css.screen}
            hidden={pill !== id}
          >
            {id === 'people' ? (
              <PeopleHome
                data={data}
                meId={me.id}
                // a save reloads through ss:person-permissions-saved
                hooks={{ onOpenPolicy: () => setPill('policy') }}
                onChanged={() => void reload()}
              />
            ) : id === 'policy' ? (
              <PolicyScreen data={data} onSaved={() => void reload()} />
            ) : id === 'templates' ? (
              <TemplatesScreen data={data} onSaved={() => void reload()} />
            ) : id === 'invites' ? (
              <InvitesScreen people={data.people.map((r) => r.person)} onCount={setInvites} />
            ) : (
              <ActivityScreen catalogue={data.catalogue} />
            )}
          </div>
        ))
      )}
    </section>
  );
}
