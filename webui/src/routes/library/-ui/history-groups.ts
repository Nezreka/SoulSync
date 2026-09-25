import type { LibraryV2HistoryEntry } from '../-library-v2.api';

export function groupHistoryEvents(entries: LibraryV2HistoryEntry[]) {
  const groups: Array<{ key: string; entries: LibraryV2HistoryEntry[] }> = [];
  for (const [index, event] of entries.entries()) {
    // Group adjacent results, never merge across another operation or an
    // unknown timestamp. Every original event remains available when expanded.
    const key = JSON.stringify([
      event.date?.slice(0, 16) ?? index,
      event.category,
      event.event_type,
      event.title,
      event.source,
      event.job_id,
      event.status_basis,
    ]);
    const previous = groups.at(-1);
    if (previous?.key === key) previous.entries.push(event);
    else groups.push({ key, entries: [event] });
  }
  return groups;
}

export function historyOutcomeSummary(entries: LibraryV2HistoryEntry[]): string {
  const counts = new Map<string, number>();
  for (const entry of entries) {
    const label = entry.status?.replaceAll('_', ' ') || 'result not recorded';
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts].map(([label, count]) => `${count} ${label}`).join(' · ');
}

export function historySubject(entry: LibraryV2HistoryEntry): string {
  return (
    entry.track_title ||
    entry.album_title ||
    (entry.track_id
      ? `Track #${entry.track_id}`
      : entry.album_id
        ? `Release #${entry.album_id}`
        : '—')
  );
}
