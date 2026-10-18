export function trackDuration(ms: unknown, empty = '—'): string {
  const value = Number(ms);
  if (!Number.isFinite(value) || value <= 0) return empty;
  const seconds = Math.floor(value / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
