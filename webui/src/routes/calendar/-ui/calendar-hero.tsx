import type { CSSProperties } from 'react';

import clsx from 'clsx';

import type { CalendarEpisode } from '../-calendar.types';

import { backdropOf } from '../-calendar.art';
import { featured, showHue, stateShort, whenLabel } from '../-calendar.model';
import styles from './calendar.module.css';
import { StatePill } from './state-pill';

/**
 * Next up: the soonest one to three episodes as a billboard. Two or three cut
 * into diagonal panels that blend into each other; the soonest leads, and the
 * one under the pointer opens out while the others fold to slivers.
 */
export function CalendarHero({
  episodes,
  today,
  now,
  currentWeek,
  autoOn,
  onOpen,
}: {
  episodes: CalendarEpisode[];
  today: string;
  now: number;
  currentWeek: boolean;
  autoOn: boolean;
  onOpen: (ep: CalendarEpisode) => void;
}) {
  const list = featured(episodes, now, currentWeek);
  if (!list.length) return null;
  const panel = (ep: CalendarEpisode, i: number, multi: boolean) => {
    const bg = backdropOf(ep, 1280);
    return (
      <div
        key={String(ep.id)}
        className={multi ? clsx(styles.bbPanel, i === 0 && styles.bbPanelLead) : styles.bb}
        style={{ '--vcal-h': showHue(ep.show_title) } as CSSProperties}
        role="button"
        tabIndex={0}
        aria-label={`${ep.show_title}, ${whenLabel(ep, today, now)}`}
        onClick={() => onOpen(ep)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onOpen(ep);
          }
        }}
      >
        {bg ? (
          <div className={styles.bbBg} style={{ backgroundImage: `url("${bg}")` }} />
        ) : (
          <div className={clsx(styles.bbBg, styles.bbBgEmpty)} />
        )}
        <div className={styles.bbScrim} />
        <div className={styles.bbContent}>
          <div className={styles.bbEyebrow}>
            <span className={styles.bbDot} />
            {currentWeek ? 'NEXT UP' : 'FEATURED'} · {whenLabel(ep, today, now).toUpperCase()}
            {ep.network ? ` · ${ep.network.toUpperCase()}` : ''}
          </div>
          <h2 className={styles.bbTitle}>{ep.show_title}</h2>
          <div className={styles.bbSub}>
            <span className={styles.bbSe}>
              S{ep.season_number} · E{ep.episode_number}
            </span>
            {ep.title ? ` · ${ep.title}` : ''}
          </div>
          <div className={styles.bbActions}>
            <span className={styles.bbBtn}>View details</span>
            <StatePill
              state={ep.acq}
              label={
                ep.acq === 'downloading'
                  ? `Downloading ${ep.progress ?? 0}%`
                  : stateShort(ep, { autoOn })
              }
              large
            />
          </div>
        </div>
      </div>
    );
  };
  if (list.length === 1)
    return <section className={styles.hero}>{panel(list[0], 0, false)}</section>;
  return (
    <section className={styles.hero}>
      <div className={styles.bbMulti} data-count={list.length}>
        {list.map((ep, i) => panel(ep, i, true))}
      </div>
    </section>
  );
}
