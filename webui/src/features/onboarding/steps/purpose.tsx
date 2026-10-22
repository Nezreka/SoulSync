/** Step: what SoulSync is for. Every later step reads this. */

import type { Purpose } from '../types';

import { useOnboarding } from '../store';
import { CheckIcon, s, StepFrame } from '../ui';

const TILES: { id: Purpose; title: string; desc: string; icon: string }[] = [
  {
    id: 'music',
    title: 'Music',
    desc: 'Search, download, tag and file music. Mirror playlists and follow artists.',
    icon: 'M9 18V5l12-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zm12-2a3 3 0 1 1-6 0 3 3 0 0 1 6 0z',
  },
  {
    id: 'video',
    title: 'Movies and TV',
    desc: 'Follow shows and films, grab releases through Prowlarr, tidy your Plex or Jellyfin.',
    icon: 'M3 5h18v12H3zM8 21h8M12 17v4',
  },
  {
    id: 'youtube',
    title: 'YouTube channels',
    desc: 'Follow channels and playlists as TV shows. No API key needed.',
    icon: 'M3 7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4v10a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4zM10 9l5 3-5 3z',
  },
  {
    id: 'podcasts',
    title: 'Podcasts',
    desc: 'Subscribe by search or RSS. New episodes download on their own.',
    icon: 'M12 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM12 14v7M7.8 15.5a6 6 0 1 1 8.4 0M5 18.5a10 10 0 1 1 14 0',
  },
  {
    id: 'audiobooks',
    title: 'Audiobooks',
    desc: 'Browse the Audible catalogue, follow authors, fetch new books.',
    icon: 'M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2zM4 21a2 2 0 0 1 2-2h12v2',
  },
];

export function PurposeStep() {
  const purposes = useOnboarding((st) => st.draft.purposes);
  const update = useOnboarding((st) => st.update);

  const toggle = (id: Purpose) =>
    update((d) => {
      d.purposes = d.purposes.includes(id)
        ? d.purposes.filter((p) => p !== id)
        : TILES.map((t) => t.id).filter((p) => p === id || d.purposes.includes(p));
    });

  return (
    <StepFrame
      step="purpose"
      wide
      title="What will you use SoulSync for?"
      lead="Pick everything that applies. Setup only asks about what you choose, and you can switch the rest on whenever you like."
    >
      <div className={s.grid3}>
        {TILES.map((t) => {
          const on = purposes.includes(t.id);
          return (
            <button
              key={t.id}
              type="button"
              className={s.tile}
              aria-pressed={on}
              onClick={() => toggle(t.id)}
            >
              <span className={s.optionHead} style={{ alignItems: 'flex-start' }}>
                <span className={s.tileIcon}>
                  <svg
                    width="22"
                    height="22"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d={t.icon} />
                  </svg>
                </span>
                <span className={s.tick}>
                  <CheckIcon />
                </span>
              </span>
              <span style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <span className={s.tileTitle}>{t.title}</span>
                <span className={s.optionDesc}>{t.desc}</span>
              </span>
            </button>
          );
        })}
      </div>
      <p className={s.note}>Later, each profile can be given music, video or both.</p>
    </StepFrame>
  );
}
