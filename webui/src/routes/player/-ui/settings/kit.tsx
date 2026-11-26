/**
 * the pieces every settings section is made of: a switch, a row, a card of
 * rows, a set of choices, and the section's head with where it is saved.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { createContext, useContext, type ReactNode } from 'react';

import { playerSettingsQuery, savePlayerSettings, type PlayerSettings } from '../../-player.api';
import { notifyError } from '../notice';
import s from '../social.module.css';
import p from './settings.module.css';

/** tells the page header to flash "Saved" */
export const SavedContext = createContext<() => void>(() => {});
export const useSaved = () => useContext(SavedContext);

export function Switch({
  on,
  label,
  busy,
  onChange,
}: {
  on: boolean;
  label: string;
  busy?: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={s.switch}
      disabled={busy}
      onClick={() => onChange(!on)}
      data-nav
    />
  );
}

export function Row({
  label,
  help,
  stacked,
  children,
}: {
  label: string;
  help?: ReactNode;
  stacked?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={stacked ? `${s.row} ${p.rowStacked}` : s.row}>
      <span className={p.rowText}>
        <span className={s.rowLabel}>{label}</span>
        {help ? <span className={s.rowHelp}>{help}</span> : null}
      </span>
      {children}
    </div>
  );
}

/** rows on one raised surface */
export function Card({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <div className={p.card} role={label ? 'group' : undefined} aria-label={label}>
      {children}
    </div>
  );
}

/** one of a few: a segmented row of radios */
export function Choices<T extends string | number>({
  label,
  options,
  value,
  disabled,
  onPick,
}: {
  label: string;
  options: readonly { value: T; label: string }[];
  value: T;
  disabled?: boolean;
  onPick: (value: T) => void;
}) {
  return (
    <div className={s.segGroup} role="radiogroup" aria-label={label}>
      {options.map((opt) => {
        const on = opt.value === value;
        return (
          <button
            key={String(opt.value)}
            type="button"
            role="radio"
            aria-checked={on}
            className={s.segBtn}
            data-on={on ? 'true' : undefined}
            disabled={disabled}
            onClick={() => onPick(opt.value)}
            data-nav
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

/** a profile setting's save: the same call and cache update as always */
export function useSavePlayerSettings() {
  const queryClient = useQueryClient();
  const onSaved = useSaved();
  return useMutation({
    mutationFn: (changes: Partial<PlayerSettings>) => savePlayerSettings(changes),
    onSuccess: (next) => {
      queryClient.setQueryData(playerSettingsQuery().queryKey, (old) =>
        old ? { ...old, settings: next } : old,
      );
      void queryClient.invalidateQueries({ queryKey: ['player', 'watching-now'] });
      if (next.commercials_enabled) {
        void queryClient.invalidateQueries({ queryKey: ['player', 'commercials-status'] });
      }
      onSaved();
    },
    onError: notifyError("Couldn't save that setting"),
  });
}

export function SaveError({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <div className={p.saveError} role="alert">
      Couldn't save that change. Try again in a moment.
    </div>
  );
}
