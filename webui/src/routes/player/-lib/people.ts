/** person pages: a filmography read for people, not for a database */

import type { PersonCredit } from '../-player.types';

/** one card per title: a person can be in a show as several roles */
export function dedupeCredits(credits: PersonCredit[]): PersonCredit[] {
  const seen = new Map<string, PersonCredit>();
  for (const c of credits) {
    const key = `${c.kind}:${c.tmdb_id}`;
    const prev = seen.get(key);
    if (!prev) seen.set(key, { ...c });
    else if (c.role && !(prev.role ?? '').includes(c.role))
      prev.role = [prev.role, c.role].filter(Boolean).join(', ');
  }
  return [...seen.values()];
}
