/**
 * #2332: "Holiday music: automatic / on / off" on an artist, album or track.
 * christmas songs carry everyday genres, so SoulSync works out what is
 * holiday music (its genres, titles like Jingle Bells) and the mixes keep it
 * to the season. this is the hand flag over that guess: kept by name on the
 * server, so a rescan or a retag never undoes it. it saves on change.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  fetchLibraryV2Holiday,
  setLibraryV2Holiday,
  type LibraryV2HolidayEntity,
  type LibraryV2HolidayState,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';

type Choice = 'auto' | 'on' | 'off';

const WHO: Record<string, string> = {
  track: 'this track',
  album: 'its album',
  artist: 'its artist',
};

export function holidayChoice(state: LibraryV2HolidayState | undefined): Choice {
  if (!state || state.override === null) return 'auto';
  return state.override ? 'on' : 'off';
}

/** the line under the picker: what it is now, and why */
export function holidaySays(
  entity: LibraryV2HolidayEntity,
  state: LibraryV2HolidayState | undefined,
): string {
  if (!state) return '';
  const what = state.holiday ? 'Holiday music' : 'Not holiday music';
  if (state.override !== null) {
    return state.holiday
      ? 'Holiday music, set by hand. Mixes play it only in season.'
      : 'Not holiday music, set by hand. Mixes play it all year.';
  }
  if (state.decided_by !== 'auto' && state.decided_by !== entity) {
    return `${what}, from what you set on ${WHO[state.decided_by] ?? 'another row'}.`;
  }
  return state.detected
    ? 'Holiday music, from its genres or title. Mixes play it only in season.'
    : 'Not holiday music, going by its genres and title.';
}

export function HolidayFlag({
  entity,
  id,
  disabled = false,
}: {
  entity: LibraryV2HolidayEntity;
  id: number;
  disabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const key = ['library-v2-holiday', entity, id] as const;
  const query = useQuery({ queryKey: key, queryFn: () => fetchLibraryV2Holiday(entity, id) });
  const save = useMutation({
    mutationFn: (choice: Choice) =>
      setLibraryV2Holiday(entity, id, choice === 'auto' ? null : choice === 'on'),
    onSuccess: (state) => {
      queryClient.setQueryData(key, state);
      // an artist or album flag changes what its tracks say
      void queryClient.invalidateQueries({ queryKey: ['library-v2-holiday'] });
    },
  });
  const fieldId = `lib2-${entity}-holiday`;
  const state = save.data ?? query.data;
  return (
    <div className={`${styles.editRow} ${styles.editFull}`}>
      <label htmlFor={fieldId}>Holiday music</label>
      <select
        id={fieldId}
        className={styles.select}
        value={holidayChoice(state)}
        disabled={disabled || query.isLoading || save.isPending}
        onChange={(event) => save.mutate(event.target.value as Choice)}
      >
        <option value="auto">Automatic</option>
        <option value="on">On: holiday music</option>
        <option value="off">Off: not holiday music</option>
      </select>
      <small className={styles.muted}>
        {save.isError
          ? (save.error as Error).message
          : query.isError
            ? (query.error as Error).message
            : holidaySays(entity, state)}
      </small>
    </div>
  );
}
