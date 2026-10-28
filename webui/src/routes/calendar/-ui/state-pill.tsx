import clsx from 'clsx';

import type { AcqState } from '../-calendar.types';

import { stateLabel } from '../-calendar.model';
import styles from './calendar.module.css';

/** A coloured dot and a word: one state, one colour, everywhere on the page. */
export function StatePill({
  state,
  label,
  large,
}: {
  state: AcqState;
  label?: string;
  large?: boolean;
}) {
  return (
    <span className={clsx(styles.pill, large && styles.pillLarge)} data-state={state}>
      {label ?? stateLabel(state)}
    </span>
  );
}
