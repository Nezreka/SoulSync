/**
 * the sidebar, your way: move the places within each section, and leave out
 * the ones you don't use. saved with the profile, so it is the same on every
 * screen (and in the app). a hidden place still opens from a link or search.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { arrangeNav, HOME_ID, moveNav } from '../-lib/nav-order';
import { playerLibrariesQuery, playerSettingsQuery, savePlayerSettings } from '../-player.api';
import { styles as k } from './kit';
import { notifyError } from './notice';
import { NAV, NAV_GROUPS, useSections } from './sections';
import { navPlaces } from './shell';
import s from './social.module.css';

export function SidebarSettings() {
  const queryClient = useQueryClient();
  const sections = useSections();
  const settings = useQuery(playerSettingsQuery()).data?.settings;
  const ownPages = (
    useQuery({ ...playerLibrariesQuery(), enabled: sections.video }).data ?? []
  ).filter((lib) => lib.own_page);
  const save = useMutation({
    mutationFn: savePlayerSettings,
    onSuccess: (next) =>
      queryClient.setQueryData(playerSettingsQuery().queryKey, (old) =>
        old ? { ...old, settings: next } : old,
      ),
    onError: notifyError("Couldn't save the sidebar"),
  });

  const every = navPlaces(
    NAV.filter((entry) => entry.visible(sections)),
    ownPages,
  );
  const hidden = new Set(settings?.sidebar_hidden ?? []);
  // every place in its saved order, the hidden ones too (they are listed here to bring back)
  const ordered = arrangeNav(every, settings?.sidebar_order, []);
  const order = ordered.map((p) => p.id);
  const arranged = (settings?.sidebar_order?.length ?? 0) > 0 || hidden.size > 0;

  return (
    <>
      {NAV_GROUPS.map((group) => {
        const places = ordered.filter((p) => p.entry.group === group.id);
        if (places.length < 2) return null;
        const inGroup = places.map((p) => p.id);
        // a move swaps two places of the same section; the others keep their spots
        const move = (id: string, step: -1 | 1) => {
          const next = moveNav(inGroup, id, step);
          let i = 0;
          save.mutate({
            sidebar_order: order.map((x) => (inGroup.includes(x) ? next[i++] : x)),
          });
        };
        return (
          <div key={group.id} role="group" aria-label={group.label ?? 'Main'}>
            {group.label ? <div className={s.rowLabel}>{group.label}</div> : null}
            {places.map((p, i) => {
              const off = hidden.has(p.id);
              return (
                <div key={p.id} className={k.editorRow} data-on={off ? undefined : 'true'}>
                  <input
                    type="checkbox"
                    checked={!off}
                    disabled={p.id === HOME_ID || save.isPending}
                    onChange={(e) => {
                      const next = new Set(hidden);
                      if (e.target.checked) next.delete(p.id);
                      else next.add(p.id);
                      save.mutate({ sidebar_hidden: [...next] });
                    }}
                    aria-label={`Show ${p.label} in the sidebar`}
                    data-nav
                  />
                  <span className={k.editorRowText}>
                    <span className={k.editorRowName}>{p.label}</span>
                  </span>
                  <span className={k.editorMove}>
                    <button
                      type="button"
                      onClick={() => move(p.id, -1)}
                      disabled={i === 0 || save.isPending}
                      aria-label={`Move ${p.label} up`}
                      data-nav
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      onClick={() => move(p.id, 1)}
                      disabled={i === places.length - 1 || save.isPending}
                      aria-label={`Move ${p.label} down`}
                      data-nav
                    >
                      ↓
                    </button>
                  </span>
                </div>
              );
            })}
          </div>
        );
      })}
      {arranged ? (
        <button
          type="button"
          className={k.moreLink}
          disabled={save.isPending}
          onClick={() => save.mutate({ sidebar_order: [], sidebar_hidden: [] })}
          data-nav
        >
          Back to the usual order
        </button>
      ) : null}
    </>
  );
}
