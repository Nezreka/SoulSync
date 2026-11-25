/**
 * admin: libraries in the player. which libraries get a page of their own
 * (an entry in the sidebar, their own rows), and who each one is hidden
 * from. the libraries themselves (their names and folders) are set up in
 * SoulSync's video settings; this is only how the player shows them.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { adminLibrariesQuery, updateAdminLibrary, type AdminLibrary } from '../-player.api';
import { MovieIcon, TvIcon } from './icons';
import { notifyError } from './notice';
import s from './social.module.css';

export function LibrariesAdmin() {
  const queryClient = useQueryClient();
  const data = useQuery(adminLibrariesQuery());
  const save = useMutation({
    mutationFn: ({ id, ...changes }: { id: number; own_page?: boolean; hidden_from?: number[] }) =>
      updateAdminLibrary(id, changes),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['player', 'admin', 'libraries'] });
      void queryClient.invalidateQueries({ queryKey: ['player', 'libraries'] });
      void queryClient.invalidateQueries({ queryKey: ['player', 'video-catalog'] });
    },
    onError: notifyError("Couldn't save that library"),
  });

  const libraries = data.data?.libraries ?? [];
  // an admin sees every library, so there is nobody to hide one from but the rest
  const people = (data.data?.profiles ?? []).filter((p) => !p.is_admin);
  // one movies library and one tv library is the player as it always was: nothing to set
  if (data.isLoading || data.isError || libraries.length <= 2) return null;

  const toggleHidden = (lib: AdminLibrary, profileId: number) => {
    const hidden = new Set(lib.hidden_from);
    if (hidden.has(profileId)) hidden.delete(profileId);
    else hidden.add(profileId);
    save.mutate({ id: lib.id, hidden_from: [...hidden] });
  };

  return (
    <section className={`${s.group} ${s.groupWide}`} aria-label="Libraries">
      <div className={s.groupHead}>
        <div className={s.groupHeadTop}>
          <span className={s.groupIcon} aria-hidden="true">
            <MovieIcon />
          </span>
          <div className={s.groupHeadText}>
            <div className={s.groupTitleRow}>
              <h2 className={s.groupTitle}>Libraries</h2>
              <span className={s.scopePill}>Admin</span>
            </div>
            <p className={s.groupNote}>
              Give a library its own page in the player, or hide it from someone. A library without
              its own page stays part of Movies or TV. Admins always see every library.
            </p>
          </div>
        </div>
      </div>
      <div className={s.groupBody}>
        {libraries.map((lib) => (
          <div key={lib.id} className={s.row} style={{ alignItems: 'flex-start' }}>
            <div style={{ display: 'flex', gap: 12, minWidth: 0, flex: 1 }}>
              <span className={s.groupIcon} aria-hidden="true">
                {lib.kind === 'movie' ? <MovieIcon /> : <TvIcon />}
              </span>
              <div style={{ minWidth: 0 }}>
                <span className={s.rowLabel}>{lib.name}</span>
                <div style={{ fontSize: 12, opacity: 0.7 }}>
                  {lib.titles.toLocaleString()}{' '}
                  {lib.kind === 'movie'
                    ? lib.titles === 1
                      ? 'film'
                      : 'films'
                    : lib.titles === 1
                      ? 'show'
                      : 'shows'}
                  {lib.main ? ` · this is ${lib.kind === 'movie' ? 'Movies' : 'TV'} itself` : ''}
                </div>
                {people.length ? (
                  <div
                    role="group"
                    aria-label={`Who can see ${lib.name}`}
                    style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}
                  >
                    {people.map((p) => {
                      const hidden = lib.hidden_from.includes(p.id);
                      return (
                        <button
                          key={p.id}
                          type="button"
                          className={s.scopePill}
                          aria-pressed={!hidden}
                          disabled={save.isPending}
                          onClick={() => toggleHidden(lib, p.id)}
                          title={hidden ? `Hidden from ${p.name}` : `${p.name} can see it`}
                          style={{
                            cursor: 'pointer',
                            opacity: hidden ? 0.45 : 1,
                            textDecoration: hidden ? 'line-through' : undefined,
                          }}
                          data-nav
                        >
                          {p.name}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            </div>
            {lib.main ? null : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
                <span style={{ fontSize: 12, opacity: 0.7 }}>
                  {lib.own_page ? 'Own page' : `Part of ${lib.kind === 'movie' ? 'Movies' : 'TV'}`}
                </span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={lib.own_page}
                  aria-label={`Give ${lib.name} its own page`}
                  className={s.switch}
                  disabled={save.isPending}
                  onClick={() => save.mutate({ id: lib.id, own_page: !lib.own_page })}
                  data-nav
                />
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
